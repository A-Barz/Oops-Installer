"""Early fail-closed Portage oneshot + idempotent eselect repository enable."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    GIT_ATOM,
    _emerge_git_block,
    _overlay_script,
    _portage_oneshot_script,
    render_chroot_script,
)
from gentoo_installer.rev import INSTALLER_REV


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "webrsync"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class PortageOneshotTests(unittest.TestCase):
    def test_oneshot_helper_fail_closed_no_noreplace(self) -> None:
        script = _portage_oneshot_script(reason="unit")
        self.assertIn("emerge --ask=n --oneshot --verbose sys-apps/portage", script)
        self.assertIn("required Portage oneshot failed", script)
        self.assertIn("--usepkg=n --getbinpkg=n sys-apps/portage", script)
        self.assertIn("exit 1", script)
        self.assertNotIn("|| true", script)
        self.assertNotIn("$NR", script)
        self.assertNotIn("--noreplace", script)

    def test_early_oneshot_before_git_emerge(self) -> None:
        script = _emerge_git_block(_cfg(sync_method="git"), reason="unit-test")
        oneshot = script.find("emerge Portage oneshot (before first emerge")
        git_emerge = script.find(f"emerge_one {GIT_ATOM}")
        self.assertGreaterEqual(oneshot, 0)
        self.assertGreaterEqual(git_emerge, 0)
        self.assertLess(oneshot, git_emerge)
        self.assertIn("required Portage oneshot failed", script)

    def test_chroot_post_profile_oneshot_fail_closed(self) -> None:
        script = render_chroot_script(
            _cfg(sync_method="git"),
            {"root": "/dev/vda2", "esp": "/dev/vda1"},
        )
        profile = script.find(">> eselect profile")
        handbook = script.find(
            "emerge Portage oneshot (after sync/profile (Handbook final tree))"
        )
        overlay = script.find("eselect repository (overlays; after profile)")
        world = script.find(">> emerge @world")
        self.assertGreaterEqual(profile, 0)
        self.assertGreaterEqual(handbook, 0)
        self.assertLess(profile, handbook)
        self.assertLess(handbook, world)
        # Soft post-overlay oneshot removed; critical oneshot is fail-closed.
        self.assertNotIn("sys-apps/portage || true", script)
        self.assertIn("required Portage oneshot failed", script)
        # Early oneshot (git prep) still precedes first emerge_one git.
        early = script.find("before first emerge after tree/profile ready")
        git_emerge = script.find(f"emerge_one {GIT_ATOM}")
        self.assertGreaterEqual(early, 0)
        self.assertGreaterEqual(git_emerge, 0)
        self.assertLess(early, git_emerge)
        if overlay >= 0:
            self.assertLess(handbook, overlay)

    def test_installer_rev_bumped(self) -> None:
        self.assertEqual(INSTALLER_REV, 82)


class OverlayIdempotentEnableTests(unittest.TestCase):
    def test_overlay_enable_skips_already_enabled(self) -> None:
        script = _overlay_script(
            _cfg(overlays=["suckless-extra"], window_managers=["gui-wm/niri"])
        )
        self.assertIn("already enabled; skip enable", script)
        self.assertIn("already enabled/present; continue", script)
        self.assertIn("/var/db/repos/suckless-extra", script)
        self.assertIn("eselect repository enable", script)
        # Fatal path remains for a true enable failure with no repo on disk.
        self.assertIn("failed to enable overlay suckless-extra", script)
        enable = script.find("eselect repository enable")
        sync = script.find("emaint sync --repo")
        self.assertGreaterEqual(enable, 0)
        self.assertLess(enable, sync)

    def test_guru_enable_also_idempotent(self) -> None:
        script = _overlay_script(_cfg(window_managers=["gui-wm/niri"]))
        self.assertIn("already enabled; skip enable", script)
        self.assertIn("/var/db/repos/guru", script)


if __name__ == "__main__":
    unittest.main()
