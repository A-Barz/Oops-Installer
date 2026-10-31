"""Handbook order for eselect: sync → news → profile → repository → locale."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _locale_eselect_script,
    _locale_prep_script,
    _news_read_script,
    _overlay_script,
    _profile_set_script,
    render_chroot_script,
)


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "webrsync"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.locale = "en_US.UTF-8"
    cfg.profile = "default/linux/amd64/23.0/desktop"
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class EselectHandbookOrderTests(unittest.TestCase):
    def test_news_script_lists_and_reads(self) -> None:
        script = _news_read_script()
        self.assertIn("eselect news list", script)
        self.assertIn("eselect news read", script)
        self.assertIn("command -v eselect", script)
        self.assertIn("exit 1", script)

    def test_profile_script_fail_closed(self) -> None:
        script = _profile_set_script(_cfg())
        self.assertIn("eselect profile set", script)
        self.assertIn("profile_ok", script)
        self.assertIn("exit 1", script)
        self.assertIn("[ -L /etc/portage/make.profile ]", script)

    def test_locale_prep_does_not_eselect_before_sync(self) -> None:
        # Early prep only locale-gen + LANG export (Portage ambiguous-C fix).
        script = _locale_prep_script(_cfg())
        self.assertIn("locale-gen", script)
        self.assertIn("export LANG=", script)
        self.assertNotIn("eselect locale", script)

    def test_locale_eselect_after_profile_fail_closed_on_bare_c(self) -> None:
        script = _locale_eselect_script(_cfg())
        self.assertIn("eselect locale set", script)
        self.assertIn('C|POSIX|""', script)
        self.assertIn("exit 1", script)

    def test_overlay_mkdir_repos_conf_before_enable(self) -> None:
        script = _overlay_script(_cfg(window_managers=["gui-wm/niri"]))
        mkdir = script.find("mkdir -p /etc/portage/repos.conf")
        enable = script.find("eselect repository enable")
        self.assertGreaterEqual(mkdir, 0)
        self.assertLess(mkdir, enable)
        self.assertIn("eselect repository list -i", script)
        self.assertIn("already enabled; skip enable", script)

    def test_chroot_handbook_eselect_order(self) -> None:
        cfg = _cfg(window_managers=["gui-wm/niri"], shell="fish", sync_method="git")
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        sync = script.find(">> sync Portage tree")
        news = script.find("eselect news (Handbook: after sync, before profile)")
        profile = script.find(">> eselect profile")
        repo = script.find("eselect repository (overlays; after profile)")
        enable = script.find("eselect repository enable")
        locale = script.find("eselect locale (Handbook: after profile)")
        world = script.find(">> emerge @world")
        for name, idx in [
            ("sync", sync),
            ("news", news),
            ("profile", profile),
            ("repo", repo),
            ("enable", enable),
            ("locale", locale),
            ("world", world),
        ]:
            self.assertGreaterEqual(idx, 0, name)
        self.assertLess(sync, news)
        self.assertLess(news, profile)
        self.assertLess(profile, repo)
        self.assertLess(repo, enable)
        self.assertLess(enable, locale)
        self.assertLess(locale, world)

    def test_tree_sync_fail_closed(self) -> None:
        for method in ("webrsync", "rsync", "git"):
            script = render_chroot_script(
                _cfg(sync_method=method),
                {"root": "/dev/vda2", "esp": "/dev/vda1"},
            )
            self.assertIn("Portage tree sync failed", script, method)

    def test_no_eselect_locale_between_header_and_sync(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        sync = script.find(">> sync Portage tree")
        early = script[:sync]
        self.assertNotIn("eselect locale", early)
        self.assertIn("eselect locale (Handbook: after profile)", script)


if __name__ == "__main__":
    unittest.main()
