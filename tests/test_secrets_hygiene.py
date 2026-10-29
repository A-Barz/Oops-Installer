"""H3/H4: always shred .install-secrets; never log LUKS passphrases."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _luks_format_open,
    _luks_open_only,
    _shred_install_secrets,
    ask_resume_or_unmount,
    phase_chroot,
)
from gentoo_installer.runner import Runner, redact_secrets


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.locale = "en_US.UTF-8"
    cfg.root_password = "root-secret-xyz"
    cfg.user_password = "user-secret-xyz"
    cfg.luks = True
    cfg.luks_passphrase = "luks-passphrase-super-secret"
    cfg.grub_password = "grub-secret-xyz"
    cfg.dry_run = False
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class RedactSecretsTests(unittest.TestCase):
    def test_redacts_planted_secret(self) -> None:
        secret = "luks-passphrase-super-secret"
        raw = f"printf '%s' '{secret}' | cryptsetup open /dev/vda3 root"
        shown = redact_secrets(raw, [secret])
        self.assertNotIn(secret, shown)
        self.assertIn("***", shown)
        self.assertIn("cryptsetup open", shown)

    def test_runner_shell_redacts_before_log_and_step(self) -> None:
        secret = "planted-shell-secret"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "installer.log"
            runner = Runner(dry_run=True, log_path=str(log), secrets=[secret])
            steps: list[str] = []
            with mock.patch("gentoo_installer.runner.ui.step", side_effect=lambda s: steps.append(s)):
                runner.shell(f"echo {secret} && true")
            self.assertTrue(steps)
            self.assertNotIn(secret, steps[0])
            logged = log.read_text(encoding="utf-8")
            self.assertNotIn(secret, logged)
            self.assertIn("***", logged)


class LuksPassphraseLoggingTests(unittest.TestCase):
    def test_luks_format_open_uses_key_file_stdin_not_passphrase(self) -> None:
        cfg = _cfg()
        runner = mock.MagicMock(spec=Runner)
        logged: list[str] = []

        def capture_run(argv, check=True, input_text=None, **kwargs):  # noqa: ANN001, ARG001
            logged.append(" ".join(str(a) for a in argv))
            self.assertEqual(input_text, cfg.luks_passphrase)
            self.assertIn("--key-file=-", argv)
            self.assertNotIn(cfg.luks_passphrase, " ".join(str(a) for a in argv))
            return mock.MagicMock(returncode=0)

        runner.run.side_effect = capture_run
        _luks_format_open(cfg, runner, "/dev/vda3", "root")
        self.assertEqual(len(logged), 2)
        self.assertIn("cryptsetup luksFormat", logged[0])
        self.assertIn("/dev/vda3", logged[0])
        self.assertIn("cryptsetup open", logged[1])
        self.assertIn("root", logged[1])
        for line in logged:
            self.assertNotIn(cfg.luks_passphrase, line)
        runner.shell.assert_not_called()

    def test_luks_open_only_same_contract(self) -> None:
        cfg = _cfg()
        runner = mock.MagicMock(spec=Runner)
        captured: list[tuple[list[str], str | None]] = []

        def capture_run(argv, check=True, input_text=None, **kwargs):  # noqa: ANN001, ARG001
            captured.append((list(argv), input_text))
            return mock.MagicMock(returncode=0)

        runner.run.side_effect = capture_run
        with mock.patch("gentoo_installer.execute.Path.exists", return_value=False):
            _luks_open_only(cfg, runner, "/dev/vda3", "root")
        self.assertEqual(len(captured), 1)
        argv, stdin = captured[0]
        self.assertEqual(argv[:3], ["cryptsetup", "open", "--key-file=-"])
        self.assertEqual(stdin, cfg.luks_passphrase)
        self.assertNotIn(cfg.luks_passphrase, " ".join(argv))

    def test_runner_ui_step_for_luks_omits_passphrase(self) -> None:
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "installer.log"
            runner = Runner(dry_run=True, log_path=str(log), secrets=[cfg.luks_passphrase])
            steps: list[str] = []
            with mock.patch("gentoo_installer.runner.ui.step", side_effect=lambda s: steps.append(s)):
                _luks_format_open(cfg, runner, "/dev/vda3", "root")
            joined = "\n".join(steps) + "\n" + log.read_text(encoding="utf-8")
            self.assertNotIn(cfg.luks_passphrase, joined)
            self.assertIn("cryptsetup", joined)
            self.assertIn("/dev/vda3", joined)


class InstallSecretsShredTests(unittest.TestCase):
    def test_shred_removes_secrets_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mnt"
            secrets = root / "root" / ".install-secrets"
            secrets.parent.mkdir(parents=True)
            secrets.write_text("ROOT_PW='x'\n", encoding="utf-8")
            secrets.chmod(0o600)
            _shred_install_secrets(str(root))
            self.assertFalse(secrets.exists())

    def test_phase_chroot_removes_secrets_after_systemexit(self) -> None:
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "gentoo"
            (root / "root").mkdir(parents=True)
            runner = mock.Mock()

            def boom(_argv, _on_line, check=True):  # noqa: ANN001, ARG001
                secrets = root / "root" / ".install-secrets"
                self.assertTrue(secrets.is_file(), "secrets must exist during chroot")
                raise SystemExit(42)

            runner.run_live.side_effect = boom
            with (
                mock.patch("gentoo_installer.execute._apply_live_firmware"),
                mock.patch("gentoo_installer.execute._repair_chroot_leftovers"),
                mock.patch("gentoo_installer.execute._write_make_conf"),
                mock.patch("gentoo_installer.execute._write_system_files"),
                mock.patch("gentoo_installer.execute._generate_locales"),
                mock.patch(
                    "gentoo_installer.execute._map_devices",
                    return_value={"root": "/dev/vda2", "esp": "/dev/vda1"},
                ),
                mock.patch.object(cfg, "save"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    phase_chroot(cfg, runner, str(root))
            self.assertEqual(raised.exception.code, 42)
            self.assertFalse((root / "root" / ".install-secrets").exists())

    def test_phase_chroot_success_also_removes_secrets(self) -> None:
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "gentoo"
            (root / "root").mkdir(parents=True)
            runner = mock.Mock()
            runner.run_live.return_value = mock.Mock(returncode=0)
            with (
                mock.patch("gentoo_installer.execute._apply_live_firmware"),
                mock.patch("gentoo_installer.execute._repair_chroot_leftovers"),
                mock.patch("gentoo_installer.execute._write_make_conf"),
                mock.patch("gentoo_installer.execute._write_system_files"),
                mock.patch("gentoo_installer.execute._generate_locales"),
                mock.patch(
                    "gentoo_installer.execute._map_devices",
                    return_value={"root": "/dev/vda2", "esp": "/dev/vda1"},
                ),
                mock.patch.object(cfg, "save"),
            ):
                phase_chroot(cfg, runner, str(root))
            self.assertFalse((root / "root" / ".install-secrets").exists())

    def test_ask_resume_shreds_even_when_keeping_mount(self) -> None:
        cfg = _cfg(mountpoint="/mnt/gentoo", target_disk="/dev/vda")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mnt"
            secrets = root / "root" / ".install-secrets"
            secrets.parent.mkdir(parents=True)
            secrets.write_text("LUKS_PW='keep-me'\n", encoding="utf-8")
            cfg.mountpoint = str(root)
            mounted = InstallConfig()
            mounted.mountpoint = str(root)
            mounted.target_disk = "/dev/vda"
            with (
                mock.patch("gentoo_installer.execute._find_failed_mount", return_value=mounted),
                mock.patch("gentoo_installer.execute._chroot_binds_mounted", return_value=False),
                mock.patch("gentoo_installer.execute.ui.ask_yes_no", return_value=True),
                mock.patch("gentoo_installer.execute.ui.warn"),
                mock.patch("gentoo_installer.execute.ui.ok"),
            ):
                decision = ask_resume_or_unmount(cfg)
            self.assertEqual(decision, "resume")
            self.assertFalse(secrets.exists())


if __name__ == "__main__":
    unittest.main()
