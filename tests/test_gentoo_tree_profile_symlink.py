"""After stage unpack: tree sync must create /var/db/repos/gentoo; profile must symlink make.profile."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _gentoo_tree_present_script,
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


class GentooTreePresentTests(unittest.TestCase):
    def test_tree_verify_fail_closed_on_missing_repos(self) -> None:
        script = _gentoo_tree_present_script()
        self.assertIn("/var/db/repos/gentoo", script)
        self.assertIn("/var/db/repos/gentoo/profiles", script)
        self.assertIn("exit 1", script)
        self.assertIn("missing after Portage tree sync", script)

    def test_chroot_verifies_tree_after_sync_before_profile(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        sync = script.find(">> sync Portage tree")
        verify = script.find("verify Portage tree at /var/db/repos/gentoo")
        profile = script.find(">> eselect profile")
        self.assertGreaterEqual(sync, 0)
        self.assertGreaterEqual(verify, 0)
        self.assertLess(sync, verify)
        self.assertLess(verify, profile)


class MakeProfileSymlinkTests(unittest.TestCase):
    def test_profile_requires_symlink_not_plain_path(self) -> None:
        script = _profile_set_script(_cfg())
        self.assertIn("[ -L /etc/portage/make.profile ]", script)
        self.assertNotIn("[ -e /etc/portage/make.profile ] || [ -L /etc/portage/make.profile ]", script)
        self.assertIn("must be a symlink", script)
        self.assertIn("readlink -f /etc/portage/make.profile", script)
        self.assertIn("/var/db/repos/gentoo/profiles", script)
        self.assertIn("exit 1", script)

    def test_clears_non_symlink_make_profile_before_eselect(self) -> None:
        script = _profile_set_script(_cfg())
        clear = script.find("removing non-symlink /etc/portage/make.profile")
        rm = script.find("rm -rf /etc/portage/make.profile")
        eselect = script.find("eselect profile set")
        self.assertGreaterEqual(clear, 0)
        self.assertGreaterEqual(rm, 0)
        self.assertLess(clear, eselect)
        self.assertLess(rm, eselect)
        self.assertIn("[ -e /etc/portage/make.profile ] && [ ! -L /etc/portage/make.profile ]", script)

    def test_chroot_fail_closed_mentions_both_repos_and_symlink(self) -> None:
        script = render_chroot_script(_cfg(sync_method="git"), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("/var/db/repos/gentoo missing after Portage tree sync", script)
        self.assertIn("[ -L /etc/portage/make.profile ]", script)
        self.assertIn("make.profile must be a symlink after eselect profile set", script)
        self.assertIn("removing non-symlink /etc/portage/make.profile", script)

    def test_profile_is_linked_before_portage_loads(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        profile = script.find(">> eselect profile")
        link = script.find("link_make_profile", profile)
        eselect = script.find("eselect profile set", profile)
        call = script.find("\nportage_prepare\n", profile)
        emerge = script.find(">> emerge Portage")
        self.assertGreaterEqual(link, 0)
        self.assertLess(link, eselect)
        self.assertLess(eselect, call)
        self.assertLess(call, emerge)
        self.assertNotIn("\nportage_prepare\n", script[:profile])
        self.assertIn('local dest="/var/db/repos/gentoo/profiles/${profile}"', script)

    def test_profile_ok_requires_eselect_success(self) -> None:
        script = _profile_set_script(_cfg())
        eselect = script.find('if eselect profile set "$profile_path"')
        ok = script.find("profile_ok=1", eselect)
        self.assertGreaterEqual(eselect, 0)
        self.assertGreaterEqual(ok, 0)
        window = script[eselect:ok]
        self.assertIn("profile_has_marker /etc/portage/make.profile", window)
        self.assertNotIn('eselect profile set --force "$profile_path" || true', script)
        self.assertNotIn("eselect profile show", window)


if __name__ == "__main__":
    unittest.main()
