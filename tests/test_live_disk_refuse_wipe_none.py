"""P5: H7 live-disk refuse, H8 honest wipe-mode none, M14 wipe/layout gate."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from gentoo_installer import detect, wizard
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    assert_wipe_layout_consistent,
    phase_disks,
    refuse_live_installer_targets,
)
from gentoo_installer.runner import Runner


def _existing_cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.mountpoint = "/mnt/gentoo"
    cfg.target_disk = "/dev/sda"
    cfg.wipe_disk = False
    cfg.wipe_mode = "none"
    cfg.layout = "existing"
    cfg.filesystem = "ext4"
    cfg.firmware = "uefi"
    cfg.existing_esp = "/dev/sda1"
    cfg.existing_root = "/dev/sda3"
    cfg.existing_swap = "/dev/sda2"
    cfg.resume = False
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class LiveDiskRefuseTests(unittest.TestCase):
    def test_wizard_omits_live_disk_from_menu_source(self) -> None:
        src = inspect.getsource(wizard._disks)
        self.assertIn("Omitting", src)
        self.assertIn("possible live USB", src)
        self.assertIn("cannot be wiped or formatted", src)
        self.assertIn("_refuse_live_installer_path", src)

    def test_wizard_refuse_helper_exits_on_live(self) -> None:
        with mock.patch.object(detect, "is_live_installer_disk", return_value=True):
            with self.assertRaises(SystemExit) as raised:
                wizard._refuse_live_installer_path("/dev/sdb", action="select")
        self.assertIn("live installer medium", str(raised.exception))

    def test_wizard_refuse_helper_allows_non_live(self) -> None:
        with mock.patch.object(detect, "is_live_installer_disk", return_value=False):
            wizard._refuse_live_installer_path("/dev/sda", action="select")

    def test_phase_disks_refuses_live_target_before_wipe(self) -> None:
        cfg = _existing_cfg(target_disk="/dev/sdb")
        runner = Runner(dry_run=True)
        shells: list[str] = []

        def capture(script: str, check: bool = True) -> None:
            shells.append(script)

        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=True):
            with mock.patch.object(runner, "shell", side_effect=capture):
                with self.assertRaises(SystemExit) as raised:
                    phase_disks(cfg, runner)
        self.assertIn("live installer medium", str(raised.exception))
        self.assertEqual(shells, [])

    def test_phase_disks_refuses_live_existing_root(self) -> None:
        cfg = _existing_cfg(existing_root="/dev/sdb3")

        def is_live(path: str) -> bool:
            return path.startswith("/dev/sdb")

        with mock.patch("gentoo_installer.detect.is_live_installer_disk", side_effect=is_live):
            with self.assertRaises(SystemExit) as raised:
                refuse_live_installer_targets(cfg)
        self.assertIn("/dev/sdb3", str(raised.exception))

    def test_phase_disks_allows_non_live_target(self) -> None:
        cfg = _existing_cfg()
        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            refuse_live_installer_targets(cfg)


class HonestWipeNoneTests(unittest.TestCase):
    def test_wizard_menu_no_longer_claims_keep_data(self) -> None:
        src = inspect.getsource(wizard._disks)
        self.assertNotIn("keep data", src.lower())
        self.assertIn("Keep partition table; reformat selected ESP/root/swap for Gentoo", src)
        self.assertIn("_confirm_reformat_existing", src)
        self.assertIn("mkfs/mkswap", src)

    def test_config_comment_matches_reformat_behavior(self) -> None:
        from gentoo_installer import config as config_mod

        src = inspect.getsource(config_mod.InstallConfig)
        self.assertIn("keep partition table", src)
        self.assertIn("mkfs/mkswap", src)
        self.assertNotIn("none = leave data", src)

    def test_confirm_reformat_names_esp_root_swap(self) -> None:
        cfg = _existing_cfg()
        asks: list[str] = []

        def fake_ask(question: str, default: bool = True) -> bool:
            asks.append(question)
            return True

        warns: list[str] = []
        with mock.patch.object(wizard.ui, "ask_yes_no", side_effect=fake_ask):
            with mock.patch.object(wizard.ui, "danger"):
                with mock.patch.object(wizard.ui, "warn", side_effect=lambda m: warns.append(m)):
                    with mock.patch.object(wizard.ui, "info"):
                        wizard._confirm_reformat_existing(cfg)
        self.assertTrue(asks)
        self.assertIn("ESP/root/swap", asks[0])
        joined = "\n".join(warns)
        self.assertIn("/dev/sda1", joined)
        self.assertIn("/dev/sda3", joined)
        self.assertIn("/dev/sda2", joined)

    def test_confirm_reformat_abort(self) -> None:
        cfg = _existing_cfg()
        with mock.patch.object(wizard.ui, "ask_yes_no", return_value=False):
            with mock.patch.object(wizard.ui, "danger"):
                with mock.patch.object(wizard.ui, "warn"):
                    with mock.patch.object(wizard.ui, "info"):
                        with self.assertRaises(SystemExit) as raised:
                            wizard._confirm_reformat_existing(cfg)
        self.assertIn("Aborted", str(raised.exception))

    def test_wipe_mode_none_still_formats_in_phase_disks(self) -> None:
        """Document intentional mkfs for wipe_mode none (H8-A, not mount-existing)."""
        cfg = _existing_cfg()
        runner = Runner(dry_run=True)
        mkfs_calls: list[object] = []

        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            with mock.patch(
                "gentoo_installer.execute._mkfs",
                side_effect=lambda *a, **k: mkfs_calls.append(a),
            ):
                with mock.patch("gentoo_installer.execute._mount_tree"):
                    with mock.patch("gentoo_installer.execute._map_devices") as mapped:
                        mapped.return_value = {
                            "esp": cfg.existing_esp,
                            "boot": cfg.existing_esp,
                            "swap": cfg.existing_swap,
                            "root": cfg.existing_root,
                            "luks_source": cfg.existing_root,
                        }
                        phase_disks(cfg, runner)
        self.assertEqual(len(mkfs_calls), 1)


class WipeLayoutConsistentTests(unittest.TestCase):
    def test_m14_refuses_wipe_disk_false_with_handbook_layout(self) -> None:
        cfg = _existing_cfg(wipe_disk=False, layout="handbook", wipe_mode="disk")
        with self.assertRaises(SystemExit) as raised:
            assert_wipe_layout_consistent(cfg)
        self.assertIn("wipe_disk is false", str(raised.exception))
        self.assertIn("handbook", str(raised.exception))

    def test_m14_allows_existing_without_wipe_disk(self) -> None:
        assert_wipe_layout_consistent(_existing_cfg())

    def test_m14_allows_wipe_disk_with_handbook(self) -> None:
        cfg = _existing_cfg(wipe_disk=True, layout="handbook", wipe_mode="disk")
        assert_wipe_layout_consistent(cfg)

    def test_phase_disks_m14_before_wipe_script(self) -> None:
        cfg = _existing_cfg(wipe_disk=False, layout="handbook", wipe_mode="disk")
        runner = Runner(dry_run=True)
        shells: list[str] = []

        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            with mock.patch.object(runner, "shell", side_effect=lambda s, check=True: shells.append(s)):
                with self.assertRaises(SystemExit) as raised:
                    phase_disks(cfg, runner)
        self.assertIn("wipe_disk is false", str(raised.exception))
        self.assertEqual(shells, [])


if __name__ == "__main__":
    unittest.main()
