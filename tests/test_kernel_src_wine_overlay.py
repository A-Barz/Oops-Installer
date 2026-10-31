"""Kernel before ZFS/modules + drop stale wine overlay (OI-64)."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _ensure_kernel_sources_script,
    _overlay_script,
    _package_use_text,
    _post_kernel_module_repairs,
    _pre_kernel_tools,
    _required_overlays,
    _required_tool_repairs,
    render_chroot_script,
)
from gentoo_installer.rev import INSTALLER_REV


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.filesystem = "ext4"
    cfg.network_stack = "networkmanager"
    cfg.chrony = True
    cfg.plymouth = True
    cfg.flatpak = True
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.selected_packages = []
    cfg.extra_packages = []
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def _script(**kwargs: object) -> str:
    return render_chroot_script(
        _cfg(**kwargs),
        {"root": "/dev/vda3", "esp": "/dev/vda1", "boot": "/dev/vda1", "swap": ""},
    )


class KernelBeforeModulesTests(unittest.TestCase):
    def test_zfs_not_in_pre_kernel_repairs(self) -> None:
        cfg = _cfg(filesystem="zfs", layout="zfs")
        pre = [atom for atom, _test in _pre_kernel_tools(cfg, ["grub"])]
        self.assertNotIn("sys-fs/zfs", pre)
        repairs = _required_tool_repairs(cfg, ["grub"])
        self.assertNotIn("sys-fs/zfs", repairs)
        post = _post_kernel_module_repairs(cfg, ["grub"])
        self.assertIn("sys-fs/zfs", post)
        self.assertIn("out-of-tree modules after /usr/src/linux", post)

    def test_chroot_kernel_before_zfs(self) -> None:
        script = _script(filesystem="zfs", layout="zfs")
        kernel = script.find(">> emerge kernel")
        zfs_post = script.find("out-of-tree modules after /usr/src/linux")
        zfs_atom = script.find("sys-fs/zfs", zfs_post)
        self.assertGreaterEqual(kernel, 0)
        self.assertGreaterEqual(zfs_post, 0)
        self.assertGreater(zfs_post, kernel)
        self.assertGreater(zfs_atom, zfs_post)
        # Pre-kernel repair block must not emerge zfs.
        pre = script.find("install tools before dracut and OpenRC services")
        self.assertGreaterEqual(pre, 0)
        self.assertLess(pre, kernel)
        self.assertNotIn("sys-fs/zfs", script[pre:kernel])

    def test_zfs_fail_closed_without_usr_src_linux(self) -> None:
        script = _script(filesystem="zfs", layout="zfs")
        self.assertIn("Unable to find kernel sources at /usr/src/linux", script)
        self.assertIn("needed before ZFS/modules", script)
        gate = script.find("ensure /usr/src/linux")
        zfs = script.find("out-of-tree modules after /usr/src/linux")
        self.assertGreaterEqual(gate, 0)
        self.assertLess(gate, zfs)

    def test_gentoo_sources_symlink_use_and_eselect(self) -> None:
        cfg = _cfg(kernel="gentoo-sources")
        use = _package_use_text(cfg)
        self.assertIn("sys-kernel/gentoo-sources symlink", use)
        lts = _package_use_text(_cfg(kernel="gentoo-sources-lts"))
        self.assertIn("sys-kernel/gentoo-sources symlink", lts)
        gate = _ensure_kernel_sources_script(cfg, ["grub"])
        self.assertIn("eselect kernel set", gate)
        script = _script(kernel="gentoo-sources-lts", filesystem="zfs", layout="zfs")
        self.assertIn("sys-kernel/gentoo-sources symlink", script)
        self.assertIn("eselect kernel set", script)

    def test_bin_kernel_without_modules_keeps_soft_kernel_fail(self) -> None:
        script = _script(filesystem="ext4")
        self.assertIn('echo ">> kernel emerge failed"', script)
        self.assertNotIn("needed before ZFS/modules", script)
        self.assertNotIn("Unable to find kernel sources at /usr/src/linux", script)

    def test_chrony_plymouth_flatpak_still_present(self) -> None:
        """Preferences: keep chrony and Extra Compositors/DE/Plymouth/Flatpak."""
        script = _script()
        self.assertIn("net-misc/chrony", script)
        self.assertIn("sys-boot/plymouth", script)
        self.assertIn("sys-apps/flatpak", script)
        keys = {key for key, _title, _rows in catalog.CATEGORIES}
        self.assertIn("window-managers", keys)
        self.assertIn("app-stores", keys)
        self.assertTrue(callable(catalog.plymouth_packages_for))


class StaleWineOverlayTests(unittest.TestCase):
    def test_wine_not_in_overlay_choices(self) -> None:
        names = {name for name, _desc in catalog.overlay_choices()}
        self.assertNotIn("wine", names)
        self.assertNotIn("sad", names)
        self.assertNotIn("src_prepare-overlay", names)
        for name in catalog.STALE_OVERLAY_NAMES:
            self.assertNotIn(name, names)

    def test_required_overlays_drops_stale_wine(self) -> None:
        cfg = _cfg(overlays=["wine", "suckless-extra"])
        ovs = _required_overlays(cfg)
        self.assertNotIn("wine", ovs)
        self.assertIn("suckless-extra", ovs)

    def test_overlay_script_never_enables_wine(self) -> None:
        script = _overlay_script(_cfg(overlays=["wine", "guru"]))
        self.assertNotIn("eselect repository enable wine", script)
        self.assertNotIn("enable 'wine'", script)

    def test_overlay_script_gates_missing_remote_names(self) -> None:
        script = _overlay_script(_cfg(overlays=["suckless-extra"]))
        self.assertIn("not listed in repositories.xml", script)
        self.assertIn("stale or renamed name", script)
        # Still enables when the name is present in the remote list.
        self.assertIn("eselect repository enable suckless-extra", script)

    def test_installer_rev_80(self) -> None:
        self.assertEqual(INSTALLER_REV, 83)


if __name__ == "__main__":
    unittest.main()
