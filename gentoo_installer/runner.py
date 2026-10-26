"""Command runner with logging, dry-run, and resumable phases.

Runner:
  All host-side shell work (apt, sgdisk, wget, mount, chroot) goes through
  run() or shell() so dry-run can print without executing, and so a log
  file exists when an emerge fails hours later.

State:
  JSON list of completed phase names. execute() skips those when
  InstallConfig.resume is True — same idea as nathanshearer/gentooinstall.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import ui


def _safe_cwd(preferred: str | None) -> str:
    """Directory for a child process.

    After `umount /mnt/gentoo`, a shell whose cwd was that tree cannot start.
    The following handbook command is `mkdir --parents /mnt/gentoo`, so the
    failure is reported there. An explicit cwd is kept; a vanished one becomes /.
    """
    if preferred:
        return preferred
    try:
        return os.getcwd()
    except OSError:
        os.chdir("/")
        return "/"


class Runner:
    def __init__(self, dry_run: bool = False, log_path: str = "/var/tmp/gentoo-live-installer.log") -> None:
        self.dry_run = dry_run
        self.log_path = Path(log_path)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Live USB /var/tmp might be read-only in odd setups; fall back to CWD.
            self.log_path = Path("gentoo-live-installer.log")

    def log(self, line: str) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line.rstrip() + "\n")
        except OSError:
            pass

    def run(
        self,
        argv: Sequence[str],
        check: bool = True,
        capture: bool = False,
        env: dict | None = None,
        cwd: str | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        pretty = " ".join(argv)
        ui.step(pretty)
        self.log("$ " + pretty)
        if self.dry_run:
            return subprocess.CompletedProcess(argv, 0, "", "")
        merged = self._portage_env(env)
        proc = subprocess.run(
            list(argv),
            check=False,
            text=True,
            capture_output=capture,
            env=merged,
            cwd=_safe_cwd(cwd),
            input=input_text,
        )
        if proc.stdout:
            self.log(proc.stdout)
        if proc.stderr:
            self.log(proc.stderr)
        if check and proc.returncode != 0:
            ui.error(f"Command failed ({proc.returncode}): {pretty}")
            if proc.stderr:
                print(proc.stderr)
            sys.exit(proc.returncode)
        return proc

    def shell(self, script: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a bash -c snippet. Prefer this for pipelines (cryptsetup | …).

        Uses non-login bash so /etc/profile cannot export a stale FEATURES into
        host commands (chroot itself is started with env -i).
        """
        ui.step(script if len(script) < 200 else script[:200] + "…")
        self.log("$ " + script)
        if self.dry_run:
            return subprocess.CompletedProcess(["bash", "-c", script], 0, "", "")
        proc = subprocess.run(
            ["bash", "-c", script],
            check=False,
            text=True,
            env=self._portage_env(),
            cwd=_safe_cwd(None),
        )
        if check and proc.returncode != 0:
            ui.error(f"Shell command failed ({proc.returncode})")
            sys.exit(proc.returncode)
        return proc

    def run_live(self, argv: Sequence[str], on_line, check: bool = True) -> subprocess.CompletedProcess:
        """Run a command on a pseudo-terminal and pass each output line to on_line.

        Emerge only line-buffers when stdout is a TTY. A pipe would hold the
        "Jobs: N of M" lines until the buffer filled, so the percent would
        sit still and then jump. If a pty is not available, fall back to run().
        """
        pretty = " ".join(argv)
        ui.step(pretty)
        self.log("$ " + pretty)
        if self.dry_run:
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        try:
            return self._run_pty(list(argv), on_line, check)
        except ImportError:
            ui.warn("No pseudo-terminal; the install percent will not follow emerge.")
            return self.run(argv, check=check)

    def _portage_env(self, extra: dict | None = None) -> dict:
        """Child environment without a stale Portage FEATURES from the live USB."""
        env = os.environ.copy()
        if extra:
            env.update(extra)
        env.pop("FEATURES", None)
        env.pop("PORTAGE_FEATURES", None)
        return env

    def _run_pty(self, argv: list[str], on_line, check: bool) -> subprocess.CompletedProcess:
        import pty
        import select

        master, slave = pty.openpty()
        self._copy_winsize(slave)
        env = self._portage_env()
        env.pop("USE", None)
        try:
            proc = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                cwd=_safe_cwd(None),
                close_fds=True,
            )
        except Exception:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        buf = b""
        try:
            while True:
                try:
                    ready, _, _ = select.select([master], [], [], 0.4)
                except InterruptedError:
                    continue
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        break
                    self._write_live(chunk)
                    buf = self._drain_live(buf + chunk, on_line)
                    continue
                if proc.poll() is not None:
                    break
        finally:
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                self._write_live(chunk)
                buf = self._drain_live(buf + chunk, on_line)
            os.close(master)
            rc = proc.wait()
        if buf.strip():
            self._emit_live(buf, on_line)
        if check and rc != 0:
            ui.error(f"Command failed ({rc}): {' '.join(argv)}")
            sys.exit(rc)
        return subprocess.CompletedProcess(argv, rc, "", "")

    def _copy_winsize(self, slave: int) -> None:
        try:
            import fcntl
            import struct
            import termios

            packed = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
            fcntl.ioctl(slave, termios.TIOCSWINSZ, packed)
        except OSError:
            return

    def _write_live(self, chunk: bytes) -> None:
        try:
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        except Exception:
            text = chunk.decode("utf-8", "replace")
            sys.stdout.write(text)
            sys.stdout.flush()

    def _drain_live(self, buf: bytes, on_line) -> bytes:
        while True:
            split_at = -1
            for sep in (b"\n", b"\r"):
                pos = buf.find(sep)
                if pos != -1 and (split_at == -1 or pos < split_at):
                    split_at = pos
            if split_at == -1:
                return buf
            self._emit_live(buf[:split_at], on_line)
            buf = buf[split_at + 1 :]

    def _emit_live(self, raw: bytes, on_line) -> None:
        text = raw.decode("utf-8", "replace").strip("\x00")
        if not text.strip():
            return
        on_line(text)


class State:
    """Persists completed execute() phase names for --resume."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.done: list[str] = []
        if self.path.exists():
            try:
                self.done = json.loads(self.path.read_text(encoding="utf-8")).get("done", [])
            except json.JSONDecodeError:
                self.done = []

    def skip(self, phase: str, resume: bool) -> bool:
        if resume and phase in self.done:
            ui.info(f"Skipping completed phase: {phase}")
            return True
        return False

    def mark(self, phase: str) -> None:
        if phase not in self.done:
            self.done.append(phase)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"done": self.done}, indent=2), encoding="utf-8")
        except OSError:
            pass
