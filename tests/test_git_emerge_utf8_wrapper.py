"""Chroot wrapper and git emerge must not run under bare LANG=C."""

from __future__ import annotations

import unittest
from unittest import mock

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    GIT_ATOM,
    _chroot_env_argv,
    _emerge_git_block,
    _generate_locales,
    _installer_utf8_locale,
    _make_conf_text,
    phase_chroot,
    render_chroot_script,
)


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "git"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.locale = "en_US.UTF-8"
    cfg.dry_run = True
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class ChrootEnvLocaleTests(unittest.TestCase):
    def test_default_helper_still_uses_c(self) -> None:
        argv = _chroot_env_argv("true")
        self.assertIn("LANG=C", argv)
        self.assertIn("LC_ALL=C", argv)

    def test_utf8_locale_override(self) -> None:
        argv = _chroot_env_argv("chroot", "/mnt/gentoo", "bash", locale="en_US.UTF-8")
        self.assertIn("LANG=en_US.UTF-8", argv)
        self.assertIn("LC_ALL=en_US.UTF-8", argv)
        self.assertNotIn("LANG=C", argv)

    def test_phase_chroot_launches_install_under_utf8(self) -> None:
        cfg = _cfg()
        runner = mock.Mock()
        captured: list[list[str]] = []

        def capture_live(argv, _on_line, check=True):  # noqa: ANN001
            captured.append(list(argv))
            return mock.Mock(returncode=0)

        runner.run_live.side_effect = capture_live
        # dry_run short-circuits before run_live — force non-dry path stubs.
        cfg.dry_run = False
        with mock.patch("gentoo_installer.execute._apply_live_firmware"):
            with mock.patch("gentoo_installer.execute._repair_chroot_leftovers"):
                with mock.patch("gentoo_installer.execute._write_make_conf"):
                    with mock.patch("gentoo_installer.execute._write_system_files"):
                        with mock.patch("gentoo_installer.execute._generate_locales"):
                            with mock.patch("gentoo_installer.execute._map_devices", return_value={"root": "/dev/vda2", "esp": "/dev/vda1"}):
                                with mock.patch("pathlib.Path.write_text"):
                                    with mock.patch("pathlib.Path.chmod"):
                                        with mock.patch("pathlib.Path.mkdir"):
                                            with mock.patch("pathlib.Path.exists", return_value=False):
                                                with mock.patch.object(cfg, "save"):
                                                    phase_chroot(cfg, runner, "/mnt/gentoo")
        self.assertTrue(captured)
        argv = captured[0]
        self.assertIn("LANG=en_US.UTF-8", argv)
        self.assertIn("LC_ALL=en_US.UTF-8", argv)
        self.assertNotIn("LANG=C", argv)


class GenerateLocalesUtf8Tests(unittest.TestCase):
    def test_eselect_locale_runs_under_utf8_not_c(self) -> None:
        cfg = _cfg()
        runner = mock.Mock()
        runner.run.return_value = mock.Mock(returncode=0)
        _generate_locales(cfg, runner, "/mnt/gentoo")
        self.assertEqual(runner.run.call_count, 2)
        locale_gen_argv = runner.run.call_args_list[0][0][0]
        eselect_argv = runner.run.call_args_list[1][0][0]
        # locale-gen may stay on C; setting locale must not.
        self.assertIn("LANG=C", locale_gen_argv)
        self.assertIn("locale-gen", " ".join(locale_gen_argv))
        self.assertIn("LANG=en_US.UTF-8", eselect_argv)
        self.assertIn("LC_ALL=en_US.UTF-8", eselect_argv)
        self.assertNotIn("LANG=C", eselect_argv)
        joined = " ".join(eselect_argv)
        self.assertIn("export LANG=", joined)
        self.assertIn("eselect locale set", joined)
        self.assertIn("refuse locale set under bare LANG", joined)

    def test_musl_skips_glibc_locale_gen(self) -> None:
        cfg = _cfg(stage_variant="musl")
        runner = mock.Mock()
        _generate_locales(cfg, runner, "/mnt/gentoo")
        runner.run.assert_not_called()


class EmergeGitBlockTests(unittest.TestCase):
    def test_block_keywords_utf8_emerge_one_and_error_surface(self) -> None:
        script = _emerge_git_block(_cfg(), reason="unit-test")
        self.assertIn("package.accept_keywords/git", script)
        self.assertIn("package.unmask/git", script)
        self.assertIn(f"{GIT_ATOM} ~amd64", script)
        self.assertIn("export LANG=en_US.UTF-8", script)
        self.assertIn("refuse emerge", script)
        self.assertIn("gentoo tree/profile precondition", script)
        self.assertIn("/var/db/repos/gentoo/profiles missing before emerge", script)
        self.assertIn(f"no ebuilds for {GIT_ATOM}", script)
        self.assertIn("make.profile not usable before emerge", script)
        self.assertIn("emerge_one", script)
        self.assertIn(f"emerge_one {GIT_ATOM}", script)
        self.assertIn("--autounmask-write --autounmask-continue", script)
        self.assertIn("keyword mask in emerge -pv", script)
        self.assertIn("git_emerge_status", script)
        self.assertIn("emerge -pv", script)
        self.assertIn("emerge --info (head)", script)
        self.assertIn("build.log", script)
        # Failure dump lines always carry >> so the host log filter keeps them.
        self.assertIn('echo ">> $_line" >&2', script)
        self.assertIn("sed 's/^/>> /' /etc/portage/package.accept_keywords/git", script)
        kw = script.find("package.accept_keywords/git")
        emerge = script.find(f"emerge_one {GIT_ATOM}")
        self.assertLess(kw, emerge)
        # UTF-8 export before emerge.
        self.assertLess(script.find("export LANG=en_US.UTF-8"), emerge)
        # Bare verbose emerge of git is gone; path goes through emerge_one.
        self.assertNotIn(f"emerge --ask=n --verbose --noreplace {GIT_ATOM}", script)

    def test_musl_uses_c_utf8(self) -> None:
        cfg = _cfg(stage_variant="musl", locale="C")
        self.assertEqual(_installer_utf8_locale(cfg), "C.UTF-8")
        script = _emerge_git_block(cfg, reason="musl")
        self.assertIn("export LANG=C.UTF-8", script)


class BinpkgRespectUseReadinessTests(unittest.TestCase):
    def test_make_conf_never_embeds_binpkg_respect_use(self) -> None:
        for respect in (False, True):
            text = _make_conf_text(_cfg(binhost=True, binpkg_respect_use=respect))
            self.assertNotIn("binpkg-respect-use", text)

    def test_chroot_sanitizes_before_git_emerge(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        sanitize = script.find("\nsanitize_binpkg_respect_use\n")
        emerge = script.find(f"emerge_one {GIT_ATOM}")
        self.assertGreaterEqual(sanitize, 0)
        self.assertGreaterEqual(emerge, 0)
        self.assertLess(sanitize, emerge)
        self.assertIn("$RESPECT_USE", script)


if __name__ == "__main__":
    unittest.main()
