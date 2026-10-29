"""H8-B: expert wipe_mode=mount — no mkfs/luksFormat; fstype fail-closed."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from gentoo_installer import detect, wizard
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    assert_mount_existing_fstypes,
    phase_disks,
)
from gentoo_installer.runner import Runner


def _mount_cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.mountpoint = "/mnt/gentoo"
    cfg.target_disk = "/dev/sda"
    cfg.wipe_disk = False
    cfg.wipe_mode = "mount"
    cfg.layout = "existing"
    cfg.filesystem = "ext4"
    cfg.firmware = "uefi"
    cfg.existing_esp = "/dev/sda1"
    cfg.existing_root = "/dev/sda3"
    cfg.existing_swap = "/dev/sda2"
    cfg.swap_mode = "partition"
    cfg.luks = False
    cfg.resume = False
    cfg.dry_run = False
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def _devices(cfg: InstallConfig) -> dict[str, str]:
    return {
        "esp": cfg.existing_esp,
        "boot": cfg.existing_esp,
        "swap": cfg.existing_swap,
        "root": cfg.existing_root,
        "luks_source": cfg.existing_root,
    }


class MountExistingWizardTests(unittest.TestCase):
    def test_menu_offers_expert_mount_path(self) -> None:
        src = inspect.getsource(wizard._disks)
        self.assertIn("Expert: mount existing filesystems", src)
        self.assertIn("_confirm_mount_existing", src)
        self.assertIn('"mount"', src)

    def test_confirm_mount_names_devices(self) -> None:
        cfg = _mount_cfg()
        warns: list[str] = []
        with mock.patch.object(wizard.ui, "ask_yes_no", return_value=True):
            with mock.patch.object(wizard.ui, "warn", side_effect=lambda m: warns.append(m)):
                with mock.patch.object(wizard.ui, "info"):
                    wizard._confirm_mount_existing(cfg)
        joined = "\n".join(warns).lower()
        self.assertIn("/dev/sda1", joined)
        self.assertIn("/dev/sda3", joined)
        self.assertIn("/dev/sda2", joined)
        self.assertIn("no wipefs", joined)

    def test_wipe_devices_empty_for_mount(self) -> None:
        self.assertEqual(wizard._wipe_devices(_mount_cfg()), [])


class MountExistingExecuteTests(unittest.TestCase):
    def test_phase_disks_mount_skips_mkfs_and_luks_format(self) -> None:
        cfg = _mount_cfg()
        runner = Runner(dry_run=True)
        mkfs_calls: list[object] = []
        format_calls: list[object] = []
        open_calls: list[object] = []

        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            with mock.patch(
                "gentoo_installer.execute._mkfs",
                side_effect=lambda *a, **k: mkfs_calls.append(a),
            ):
                with mock.patch(
                    "gentoo_installer.execute._luks_format_open",
                    side_effect=lambda *a, **k: format_calls.append(a),
                ):
                    with mock.patch(
                        "gentoo_installer.execute._luks_open_only",
                        side_effect=lambda *a, **k: open_calls.append(a),
                    ):
                        with mock.patch("gentoo_installer.execute._mount_tree") as mount:
                            with mock.patch(
                                "gentoo_installer.execute._map_devices",
                                return_value=_devices(cfg),
                            ):
                                with mock.patch(
                                    "gentoo_installer.execute.assert_mount_existing_fstypes"
                                ):
                                    phase_disks(cfg, runner)
        self.assertEqual(mkfs_calls, [])
        self.assertEqual(format_calls, [])
        self.assertEqual(open_calls, [])
        mount.assert_called_once()
        self.assertFalse(mount.call_args.kwargs.get("create_subvols", True))

    def test_phase_disks_mount_opens_luks_without_format(self) -> None:
        cfg = _mount_cfg(luks=True, luks_passphrase="secret", dry_run=True)
        runner = Runner(dry_run=True)
        format_calls: list[object] = []
        open_calls: list[object] = []

        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            with mock.patch("gentoo_installer.execute._mkfs") as mkfs:
                with mock.patch(
                    "gentoo_installer.execute._luks_format_open",
                    side_effect=lambda *a, **k: format_calls.append(a),
                ):
                    with mock.patch(
                        "gentoo_installer.execute._luks_open_only",
                        side_effect=lambda *a, **k: open_calls.append(a),
                    ):
                        with mock.patch("gentoo_installer.execute._mount_tree"):
                            with mock.patch(
                                "gentoo_installer.execute._map_devices",
                                return_value=_devices(cfg),
                            ):
                                phase_disks(cfg, runner)
        mkfs.assert_not_called()
        self.assertEqual(format_calls, [])
        self.assertEqual(len(open_calls), 1)
        self.assertEqual(open_calls[0][2], cfg.existing_root)
        self.assertEqual(open_calls[0][3], "root")

    def test_fstype_mismatch_fails_closed(self) -> None:
        cfg = _mount_cfg(filesystem="ext4")
        devices = _devices(cfg)

        def fake_type(node: str) -> str:
            return {"/dev/sda1": "vfat", "/dev/sda3": "xfs", "/dev/sda2": "swap"}.get(node, "")

        with mock.patch.object(detect, "blkid_type", side_effect=fake_type):
            with self.assertRaises(SystemExit) as raised:
                assert_mount_existing_fstypes(cfg, devices)
        self.assertIn("TYPE='xfs'", str(raised.exception))
        self.assertIn("ext4", str(raised.exception))

    def test_fstype_match_allows_mount(self) -> None:
        cfg = _mount_cfg(filesystem="ext4")
        devices = _devices(cfg)

        def fake_type(node: str) -> str:
            return {"/dev/sda1": "vfat", "/dev/sda3": "ext4", "/dev/sda2": "swap"}.get(node, "")

        with mock.patch.object(detect, "blkid_type", side_effect=fake_type):
            assert_mount_existing_fstypes(cfg, devices)

    def test_missing_type_fails_closed(self) -> None:
        cfg = _mount_cfg()
        with mock.patch.object(detect, "blkid_type", return_value=""):
            with self.assertRaises(SystemExit) as raised:
                assert_mount_existing_fstypes(cfg, _devices(cfg))
        self.assertIn("could not read filesystem TYPE", str(raised.exception))

    def test_mount_refuses_wipe_partitions(self) -> None:
        cfg = _mount_cfg(wipe_partitions=["/dev/sda3"])
        runner = Runner(dry_run=True)
        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            with self.assertRaises(SystemExit) as raised:
                phase_disks(cfg, runner)
        self.assertIn("refuses wipe", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
