"""Btrfs @swap subvolume is not swap — wizard must still ask, and clarify."""

from __future__ import annotations

import inspect
import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import render_chroot_script
from gentoo_installer import wizard


class BtrfsSubvolsSwapUxTests(unittest.TestCase):
    def test_wizard_explains_at_swap_is_not_automatic_swap(self) -> None:
        src = inspect.getsource(wizard._disks)
        self.assertIn("nodatacow subvolume", src)
        self.assertIn("not swap yet", src)
        self.assertIn("independent of Btrfs subvolumes", src)
        # Default choice for this layout is a file on @swap, not a partition.
        self.assertIn('"file"', src)
        self.assertIn("/swap/swapfile", src)

    def test_btrfs_subvols_swapfile_lands_on_at_swap_mount(self) -> None:
        cfg = InstallConfig()
        cfg.username = "ashley"
        cfg.target_disk = "/dev/vda"
        cfg.architecture = "amd64"
        cfg.init_system = "openrc"
        cfg.kernel = "gentoo-kernel-bin"
        cfg.bootloader = "grub"
        cfg.bootloaders = ["grub"]
        cfg.dry_run = True
        cfg.layout = "btrfs-subvols"
        cfg.filesystem = "btrfs"
        cfg.swap_mode = "file"
        cfg.swap_size_gib = 4
        cfg.zram = False
        cfg.window_managers = ["gui-wm/sway"]
        cfg.display_manager = "greetd"
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("/swap/swapfile", script)
        self.assertIn("of=/swap/swapfile", script)
        # Classic root swapfile path must not be used for this layout.
        self.assertNotIn("of=/swapfile ", script)
        self.assertIn("subvol=@swap", script)

    def test_non_btrfs_layout_keeps_root_swapfile(self) -> None:
        cfg = InstallConfig()
        cfg.username = "ashley"
        cfg.target_disk = "/dev/vda"
        cfg.architecture = "amd64"
        cfg.init_system = "openrc"
        cfg.kernel = "gentoo-kernel-bin"
        cfg.bootloader = "grub"
        cfg.bootloaders = ["grub"]
        cfg.dry_run = True
        cfg.layout = "handbook"
        cfg.filesystem = "ext4"
        cfg.swap_mode = "file"
        cfg.swap_size_gib = 4
        cfg.window_managers = []
        cfg.display_manager = "none"
        script = render_chroot_script(cfg, {"root": "/dev/vda3", "esp": "/dev/vda1"})
        self.assertIn("of=/swapfile", script)
        self.assertNotIn("/swap/swapfile", script)


if __name__ == "__main__":
    unittest.main()
