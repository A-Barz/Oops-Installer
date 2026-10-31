"""OI-76 A1: drop without-systemd from catalogue; refuse if still selected."""

from __future__ import annotations

import inspect
import unittest

from gentoo_installer import catalog, wizard
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import _overlay_script, _required_overlays
from gentoo_installer.rev import INSTALLER_REV


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.overlays = []
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.selected_packages = []
    cfg.extra_packages = []
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class WithoutSystemdSkipTests(unittest.TestCase):
    def test_rev_bumped(self) -> None:
        self.assertGreaterEqual(INSTALLER_REV, 85)

    def test_not_in_overlay_choices(self) -> None:
        names = {name for name, _desc in catalog.overlay_choices()}
        self.assertNotIn("without-systemd", names)
        for name in catalog.REFUSED_OVERLAY_NAMES:
            self.assertNotIn(name, names)

    def test_chrony_compositors_plymouth_flatpak_kept(self) -> None:
        keys = {key for key, _title, _rows in catalog.CATEGORIES}
        self.assertIn("window-managers", keys)
        self.assertIn("app-stores", keys)
        self.assertTrue(callable(catalog.plymouth_packages_for))
        rows = " ".join(atom for _k, _t, pkgs in catalog.CATEGORIES for atom, _d in pkgs)
        self.assertIn("sys-apps/flatpak", rows)
        self.assertTrue(hasattr(InstallConfig, "chrony"))
        self.assertTrue(InstallConfig().chrony)

    def test_refuse_raises_on_without_systemd(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            catalog.refuse_unsupported_overlays(["suckless-extra", "without-systemd"])
        msg = str(raised.exception)
        self.assertIn("without-systemd", msg)
        self.assertIn("systemd-utils", msg)

    def test_refuse_allows_other_overlays(self) -> None:
        catalog.refuse_unsupported_overlays(["suckless-extra", "guru"])

    def test_required_overlays_refuses(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            _required_overlays(_cfg(overlays=["without-systemd", "suckless-extra"]))
        self.assertIn("without-systemd", str(raised.exception))

    def test_overlay_script_refuses_before_enable(self) -> None:
        with self.assertRaises(SystemExit):
            _overlay_script(_cfg(overlays=["without-systemd"]))

    def test_openrc_chooser_copy_in_wizard(self) -> None:
        src = inspect.getsource(wizard._stage)
        self.assertIn("without-systemd community overlay", src)
        self.assertIn("sys-apps/systemd-utils", src)
        self.assertIn("udev via systemd-utils, not systemd-as-init", src)
        self.assertIn("common on other distros", src)
        self.assertIn("elogind", src)


if __name__ == "__main__":
    unittest.main()
