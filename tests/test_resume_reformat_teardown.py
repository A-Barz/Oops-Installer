"""Resume → format again must swapoff/umount before mkfs (btrfs subvols)."""

from __future__ import annotations

import unittest
from unittest import mock

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _disk_teardown_script,
    _mkfs,
    _teardown_disk_for_reformat,
    _unmount_target,
    phase_disks,
)
from gentoo_installer.runner import Runner


def _btrfs_cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.mountpoint = "/mnt/gentoo"
    cfg.target_disk = "/dev/vda"
    cfg.wipe_disk = True
    cfg.wipe_mode = "disk"
    cfg.layout = "btrfs-subvols"
    cfg.filesystem = "btrfs"
    cfg.firmware = "uefi"
    cfg.partition_table = "gpt"
    cfg.swap_mode = "file"
    cfg.resume = False
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class DiskTeardownOrderTests(unittest.TestCase):
    def test_script_swapoffs_before_umount(self) -> None:
        script = _disk_teardown_script(_btrfs_cfg())
        swap_at = script.find("swapoff -a")
        umount_tree = script.find("unmount chroot binds and target tree")
        umount_disk = script.find("release mounts on target disk partitions")
        fail_closed = script.find("disk teardown failed: target still busy")
        self.assertGreaterEqual(swap_at, 0)
        self.assertGreaterEqual(umount_tree, 0)
        self.assertGreaterEqual(umount_disk, 0)
        self.assertGreaterEqual(fail_closed, 0)
        self.assertLess(swap_at, umount_tree, "swapoff must run before unmounting the target tree")
        self.assertLess(umount_tree, umount_disk)
        self.assertLess(umount_disk, fail_closed)

    def test_script_mentions_resume_reformat_and_fail_closed(self) -> None:
        script = _disk_teardown_script(_btrfs_cfg())
        self.assertIn("resume reformat", script.lower())
        self.assertIn("exit 1", script)
        self.assertIn("Cannot wipe or format while devices are in use", script)
        self.assertIn("/mnt/gentoo", script)
        self.assertIn("/dev/vda", script)

    def test_lvm_and_md_deactivate_when_layout_needs_it(self) -> None:
        lvm = _disk_teardown_script(_btrfs_cfg(layout="lvm-luks", filesystem="ext4"))
        self.assertIn("vgchange -an", lvm)
        md = _disk_teardown_script(_btrfs_cfg(layout="mdraid1", filesystem="ext4", extra_raid_disk="/dev/vdb"))
        self.assertIn("mdadm --stop", md)
        self.assertIn("/dev/vdb", md)

    def test_teardown_runs_script_with_check_true(self) -> None:
        cfg = _btrfs_cfg()
        runner = mock.MagicMock(spec=Runner)
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _teardown_disk_for_reformat(cfg, runner)
        runner.shell.assert_called()
        args, kwargs = runner.shell.call_args
        self.assertIn("swapoff -a", args[0])
        self.assertTrue(kwargs.get("check", True))

    def test_unmount_target_uses_fail_closed_teardown(self) -> None:
        cfg = _btrfs_cfg()
        runner = mock.MagicMock(spec=Runner)
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _unmount_target(cfg, runner)
        script = runner.shell.call_args[0][0]
        self.assertIn("swapoff -a", script)
        self.assertIn("exit 1", script)


class ResumeReformatPathTests(unittest.TestCase):
    def test_mkfs_tears_down_before_mkfs_btrfs(self) -> None:
        """Regression: btrfs subvol format after resume left swap/mounts busy."""
        cfg = _btrfs_cfg()
        runner = mock.MagicMock(spec=Runner)
        devices = {
            "esp": "/dev/vda1",
            "boot": "",
            "swap": "",
            "root": "/dev/vda2",
            "luks_source": "/dev/vda2",
        }
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _mkfs(cfg, runner, devices)
        scripts = [c.args[0] for c in runner.shell.call_args_list if c.args]
        self.assertTrue(scripts, "mkfs should run shell commands")
        self.assertIn("swapoff -a", scripts[0])
        self.assertIn("teardown", scripts[0])
        joined = "\n".join(scripts)
        teardown_at = joined.find("swapoff -a")
        mkfs_at = joined.find("mkfs.btrfs")
        self.assertGreaterEqual(mkfs_at, 0)
        self.assertLess(teardown_at, mkfs_at, "teardown/swapoff must precede mkfs.btrfs")

    def test_phase_disks_teardown_before_wipe_when_not_resume(self) -> None:
        cfg = _btrfs_cfg(resume=False)
        runner = mock.MagicMock(spec=Runner)
        calls: list[str] = []

        def capture_shell(script: str, check: bool = True):  # noqa: ARG001
            calls.append(script)
            return mock.MagicMock(returncode=0)

        runner.shell.side_effect = capture_shell
        with (
            mock.patch("gentoo_installer.execute._leave_tree"),
            mock.patch("gentoo_installer.execute._sgdisk_gpt"),
            mock.patch("gentoo_installer.execute._partprobe_disk"),
            mock.patch("gentoo_installer.execute._release_disk_mounts"),
            mock.patch("gentoo_installer.execute._map_devices", return_value={
                "esp": "/dev/vda1",
                "boot": "",
                "swap": "",
                "root": "/dev/vda2",
                "luks_source": "/dev/vda2",
            }),
            mock.patch("gentoo_installer.execute._mkfs") as mkfs,
            mock.patch("gentoo_installer.execute._mount_tree"),
            mock.patch("gentoo_installer.execute.ui"),
        ):
            phase_disks(cfg, runner)
        wipe_scripts = [s for s in calls if "wipefs" in s or "swapoff -a" in s]
        self.assertTrue(any("swapoff -a" in s for s in wipe_scripts))
        first_teardown = next(i for i, s in enumerate(calls) if "swapoff -a" in s)
        first_wipe = next(i for i, s in enumerate(calls) if "wipefs" in s)
        self.assertLess(first_teardown, first_wipe)
        mkfs.assert_called_once()

    def test_phase_disks_resume_skips_wipe_and_mkfs(self) -> None:
        cfg = _btrfs_cfg(resume=True)
        runner = mock.MagicMock(spec=Runner)
        with (
            mock.patch("gentoo_installer.execute._ensure_target_mounted") as ensure,
            mock.patch("gentoo_installer.execute._mkfs") as mkfs,
            mock.patch("gentoo_installer.execute.ui"),
        ):
            phase_disks(cfg, runner)
        ensure.assert_called_once()
        mkfs.assert_not_called()
        runner.shell.assert_not_called()


if __name__ == "__main__":
    unittest.main()
