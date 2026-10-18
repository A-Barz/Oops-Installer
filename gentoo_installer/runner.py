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
        merged = os.environ.copy()
        if env:
            merged.update(env)
        # Portage reads FEATURES from the process environment (even inside chroot).
        merged.pop("FEATURES", None)
        proc = subprocess.run(
            list(argv),
            check=False,
            text=True,
            capture_output=capture,
            env=merged,
            cwd=cwd,
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
        """Run a bash -lc snippet. Prefer this for pipelines (cryptsetup | …)."""
        ui.step(script if len(script) < 200 else script[:200] + "…")
        self.log("$ " + script)
        if self.dry_run:
            return subprocess.CompletedProcess(["bash", "-c", script], 0, "", "")
        env = os.environ.copy()
        env.pop("FEATURES", None)
        proc = subprocess.run(["bash", "-lc", script], check=False, text=True, env=env)
        if check and proc.returncode != 0:
            ui.error(f"Shell command failed ({proc.returncode})")
            sys.exit(proc.returncode)
        return proc


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
