"""Resume → format again must swapoff/umount before mkfs (btrfs subvols).

Also covers H1: pre-mkfs teardown is mounts-only (no cryptsetup close / vgchange
-an of maps this phase just opened); full teardown stays for wipe/unmount.
"""

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


def _luks_handbook_cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.mountpoint = "/mnt/gentoo"
    cfg.target_disk = "/dev/vda"
    cfg.wipe_disk = True
    cfg.wipe_mode = "disk"
    cfg.layout = "handbook"
    cfg.filesystem = "ext4"
    cfg.firmware = "uefi"
    cfg.partition_table = "gpt"
    cfg.swap_mode = "partition"
    cfg.swap_size_gib = 2
    cfg.luks = True
    cfg.luks_passphrase = "test-luks-pass"
    cfg.encrypted_swap = True
    cfg.resume = False
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


def _lvm_luks_cfg(**kwargs: object) -> InstallConfig:
    cfg = _luks_handbook_cfg(layout="lvm-luks", encrypted_swap=False, **kwargs)
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
        self.assertIn("cryptsetup close root", lvm)
        md = _disk_teardown_script(_btrfs_cfg(layout="mdraid1", filesystem="ext4", extra_raid_disk="/dev/vdb"))
        self.assertIn("mdadm --stop", md)
        self.assertIn("/dev/vdb", md)

    def test_mounts_only_skips_cryptsetup_and_vgchange(self) -> None:
        full = _disk_teardown_script(_lvm_luks_cfg(), mode="full")
        mounts = _disk_teardown_script(_lvm_luks_cfg(), mode="mounts_only")
        self.assertIn("cryptsetup close root", full)
        self.assertIn("cryptsetup close swap", full)
        self.assertIn("vgchange -an", full)
        self.assertNotIn("cryptsetup close root", mounts)
        self.assertNotIn("cryptsetup close swap", mounts)
        self.assertNotIn("vgchange -an", mounts)
        self.assertIn("mounts-only", mounts)
        self.assertIn("swapoff -a", mounts)
        self.assertIn("exit 1", mounts)
        self.assertIn("Cannot wipe or format while devices are in use", mounts)

    def test_mounts_only_skips_md_stop(self) -> None:
        cfg = _btrfs_cfg(layout="mdraid1", filesystem="ext4", extra_raid_disk="/dev/vdb")
        mounts = _disk_teardown_script(cfg, mode="mounts_only")
        self.assertNotIn("mdadm --stop", mounts)
        self.assertIn("swapoff -a", mounts)

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            _disk_teardown_script(_btrfs_cfg(), mode="half")

    def test_teardown_runs_script_with_check_true(self) -> None:
        cfg = _btrfs_cfg()
        runner = mock.MagicMock(spec=Runner)
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _teardown_disk_for_reformat(cfg, runner)
        runner.shell.assert_called()
        args, kwargs = runner.shell.call_args
        self.assertIn("swapoff -a", args[0])
        self.assertIn("cryptsetup close root", args[0])
        self.assertTrue(kwargs.get("check", True))

    def test_unmount_target_uses_fail_closed_teardown(self) -> None:
        cfg = _btrfs_cfg()
        runner = mock.MagicMock(spec=Runner)
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _unmount_target(cfg, runner)
        script = runner.shell.call_args[0][0]
        self.assertIn("swapoff -a", script)
        self.assertIn("cryptsetup close root", script)
        self.assertIn("cryptsetup close swap", script)
        self.assertIn("exit 1", script)
        self.assertIn("Cannot wipe or format while devices are in use", script)


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
        self.assertIn("mounts-only", scripts[0])
        self.assertNotIn("cryptsetup close root", scripts[0])
        self.assertNotIn("cryptsetup close swap", scripts[0])
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
        pre_wipe = calls[first_teardown]
        self.assertIn("cryptsetup close root", pre_wipe)
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


class H1LuksMkfsTeardownTests(unittest.TestCase):
    """H1: do not close LUKS/LVM between open and mkfs."""

    def _run_phase_disks_capture(self, cfg: InstallConfig, mapped: dict[str, str]) -> list[str]:
        runner = mock.MagicMock(spec=Runner)
        calls: list[str] = []

        def capture_shell(script: str, check: bool = True):  # noqa: ARG001
            calls.append(script)
            return mock.MagicMock(returncode=0)

        def capture_run(argv, check: bool = True, **kwargs):  # noqa: ARG001, ANN001
            calls.append(" ".join(str(a) for a in argv))
            return mock.MagicMock(returncode=0)

        runner.shell.side_effect = capture_shell
        runner.run.side_effect = capture_run
        with (
            mock.patch("gentoo_installer.execute._leave_tree"),
            mock.patch("gentoo_installer.execute._sgdisk_gpt"),
            mock.patch("gentoo_installer.execute._partprobe_disk"),
            mock.patch("gentoo_installer.execute._release_disk_mounts"),
            mock.patch("gentoo_installer.execute._map_devices", return_value=dict(mapped)),
            mock.patch("gentoo_installer.execute._mount_tree"),
            mock.patch("gentoo_installer.execute.ui"),
        ):
            phase_disks(cfg, runner)
        return calls

    def test_luks_phase_disks_no_close_between_open_and_mkfs(self) -> None:
        cfg = _luks_handbook_cfg()
        mapped = {
            "esp": "/dev/vda1",
            "boot": "",
            "swap": "/dev/vda2",
            "root": "/dev/vda3",
            "luks_source": "/dev/vda3",
        }
        calls = self._run_phase_disks_capture(cfg, mapped)
        joined = "\n".join(calls)

        open_root = joined.find("cryptsetup open")
        self.assertGreaterEqual(open_root, 0, "expected LUKS open for root")
        # First mkfs/mkswap after LUKS open — prefer root mkfs, fall back to mkswap.
        mkfs_root = joined.find("mkfs.ext4", open_root)
        mkswap = joined.find("mkswap", open_root)
        format_at = min(x for x in (mkfs_root, mkswap) if x >= 0)
        between = joined[open_root:format_at]
        self.assertNotIn(
            "cryptsetup close root",
            between,
            "H1: must not close root mapper between open and mkfs",
        )
        self.assertNotIn(
            "cryptsetup close swap",
            between,
            "H1: must not close swap mapper between open and mkswap/mkfs",
        )
        # Pre-wipe full teardown (before LUKS) may still close; that is fine.
        pre_open = joined[:open_root]
        self.assertIn("cryptsetup close root", pre_open)

        # Pre-mkfs teardown must be mounts-only.
        mounts_only = [s for s in calls if "mounts-only" in s]
        self.assertTrue(mounts_only, "expected mounts-only teardown before mkfs")
        for script in mounts_only:
            self.assertNotIn("cryptsetup close root", script)
            self.assertNotIn("cryptsetup close swap", script)
            self.assertNotIn("vgchange -an", script)

        # Assert mapper nodes before format.
        self.assertTrue(
            any("test -b '/dev/mapper/root'" in s or 'test -b "/dev/mapper/root"' in s or "test -b /dev/mapper/root" in s for s in calls)
            or any("test -b" in s and "/dev/mapper/root" in s for s in calls),
            f"expected block-device assert for /dev/mapper/root; got: {calls!r}",
        )

    def test_lvm_luks_phase_disks_no_vgchange_between_open_and_mkfs(self) -> None:
        cfg = _lvm_luks_cfg()
        mapped = {
            "esp": "/dev/vda1",
            "boot": "",
            "swap": "",
            "root": "/dev/vda2",
            "luks_source": "/dev/vda2",
        }
        calls = self._run_phase_disks_capture(cfg, mapped)
        joined = "\n".join(calls)

        open_at = joined.find("cryptsetup open")
        self.assertGreaterEqual(open_at, 0)
        vg_at = joined.find("vgcreate", open_at)
        self.assertGreaterEqual(vg_at, 0)
        mkfs_at = joined.find("mkfs.ext4", vg_at)
        self.assertGreaterEqual(mkfs_at, 0)
        between = joined[open_at:mkfs_at]
        self.assertNotIn("cryptsetup close root", between)
        self.assertNotIn("vgchange -an", between)
        self.assertIn("/dev/vg0/root", joined[mkfs_at : mkfs_at + 200] or joined)
        # mkfs should target the LV, not the closed mapper.
        self.assertTrue(
            any("mkfs.ext4" in s and "/dev/vg0/root" in s for s in calls),
            "expected mkfs on /dev/vg0/root",
        )
        mounts_only = [s for s in calls if "mounts-only" in s]
        self.assertTrue(mounts_only)
        for script in mounts_only:
            self.assertNotIn("vgchange -an", script)
            self.assertNotIn("cryptsetup close root", script)

    def test_mkfs_luks_mapper_uses_mounts_only(self) -> None:
        cfg = _luks_handbook_cfg(swap_mode="file", encrypted_swap=False)
        runner = mock.MagicMock(spec=Runner)
        devices = {
            "esp": "/dev/vda1",
            "boot": "",
            "swap": "",
            "root": "/dev/mapper/root",
            "luks_source": "/dev/vda3",
        }
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _mkfs(cfg, runner, devices)
        scripts = [c.args[0] for c in runner.shell.call_args_list if c.args]
        self.assertIn("mounts-only", scripts[0])
        self.assertNotIn("cryptsetup close root", scripts[0])
        self.assertTrue(any("/dev/mapper/root" in s and "test -b" in s for s in scripts))
        self.assertTrue(any("mkfs.ext4" in s and "/dev/mapper/root" in s for s in scripts))

    def test_unmount_and_wipe_still_full_close(self) -> None:
        cfg = _luks_handbook_cfg()
        runner = mock.MagicMock(spec=Runner)
        with mock.patch("gentoo_installer.execute._leave_tree"):
            _unmount_target(cfg, runner)
        script = runner.shell.call_args[0][0]
        self.assertIn("cryptsetup close root", script)
        self.assertIn("cryptsetup close swap", script)
        self.assertIn("exit 1", script)
        self.assertNotIn("mounts-only", script)

        # Wipe path in phase_disks (before LUKS) uses full teardown via _unmount_target.
        wipe_calls: list[str] = []

        def capture_shell(script: str, check: bool = True):  # noqa: ARG001
            wipe_calls.append(script)
            return mock.MagicMock(returncode=0)

        runner2 = mock.MagicMock(spec=Runner)
        runner2.shell.side_effect = capture_shell
        with (
            mock.patch("gentoo_installer.execute._leave_tree"),
            mock.patch("gentoo_installer.execute._sgdisk_gpt"),
            mock.patch("gentoo_installer.execute._partprobe_disk"),
            mock.patch("gentoo_installer.execute._release_disk_mounts"),
            mock.patch(
                "gentoo_installer.execute._map_devices",
                return_value={
                    "esp": "/dev/vda1",
                    "boot": "",
                    "swap": "/dev/vda2",
                    "root": "/dev/vda3",
                    "luks_source": "/dev/vda3",
                },
            ),
            mock.patch("gentoo_installer.execute._mkfs"),
            mock.patch("gentoo_installer.execute._mount_tree"),
            mock.patch("gentoo_installer.execute.ui"),
        ):
            phase_disks(cfg, runner2)
        first_teardown = next(s for s in wipe_calls if "swapoff -a" in s)
        self.assertIn("cryptsetup close root", first_teardown)
        self.assertIn("exit 1", first_teardown)


if __name__ == "__main__":
    unittest.main()
