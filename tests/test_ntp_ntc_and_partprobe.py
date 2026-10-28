"""Host NTP ntpsec hint and partprobe must not hang on the live USB dm stack."""

from __future__ import annotations

import unittest
from unittest import mock

from gentoo_installer import detect
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    HOST_PACKAGES,
    NTP_NTC_HINT,
    PARTPROBE_TIMEOUT_SEC,
    _partprobe_disk,
    sync_live_clock,
)
from gentoo_installer.wizard import _network
from gentoo_installer import ui


class NtpNtpsecHintTests(unittest.TestCase):
    def test_host_packages_include_ntpsec_not_chrony(self) -> None:
        self.assertIn("ntpsec", HOST_PACKAGES)
        self.assertNotIn("chrony", HOST_PACKAGES)

    def test_hint_names_ubuntu_ntpsec_replacing_chrony(self) -> None:
        self.assertIn("ntpsec", NTP_NTC_HINT)
        self.assertIn("replace chrony", NTP_NTC_HINT)
        self.assertIn("apt-get install -y ntpsec", NTP_NTC_HINT)
        self.assertIn("apt-get remove -y chrony", NTP_NTC_HINT)

    def test_missing_tools_warn_ntp_not_found_and_ntpsec_hint(self) -> None:
        cfg = InstallConfig()
        cfg.ntp = True
        cfg.dry_run = False
        runner = mock.Mock()
        warns: list[str] = []
        with mock.patch("gentoo_installer.execute._host_ntp_tools_present", return_value=False):
            with mock.patch.object(ui, "warn", side_effect=lambda m: warns.append(m)):
                ok = sync_live_clock(cfg, runner)
        self.assertFalse(ok)
        runner.shell.assert_not_called()
        self.assertTrue(any("ntp update was not found" in w for w in warns))
        self.assertTrue(any("ntpsec" in w and "chrony" in w for w in warns))

    def test_wizard_network_mentions_ntpsec_replacing_chrony(self) -> None:
        import inspect

        src = inspect.getsource(_network)
        self.assertIn("ntpsec", src)
        self.assertIn("chrony", src)
        self.assertIn("apt-get install -y ntpsec", src)

    def test_config_maps_old_chrony_key_to_ntpsec(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.yaml"
            path.write_text("chrony: false\narchitecture: amd64\n", encoding="utf-8")
            cfg = InstallConfig.load(path)
        self.assertFalse(cfg.ntpsec)


class PartprobeLiveUsbTests(unittest.TestCase):
    def test_timeout_constant_is_bounded(self) -> None:
        self.assertGreaterEqual(PARTPROBE_TIMEOUT_SEC, 5)
        self.assertLessEqual(PARTPROBE_TIMEOUT_SEC, 60)

    def test_partprobe_targets_one_disk_with_timeout(self) -> None:
        runner = mock.Mock()
        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=False):
            _partprobe_disk(runner, "/dev/vda")
        runner.shell.assert_called_once()
        script = runner.shell.call_args[0][0]
        self.assertIn("partprobe /dev/vda", script)
        self.assertIn(f"timeout {PARTPROBE_TIMEOUT_SEC}", script)
        self.assertIn("blockdev --rereadpt", script)
        self.assertIn("device-mapper ioctl", script)
        # Must never probe the whole system (that hangs on live USB dm).
        self.assertNotRegex(script, r"(?m)^\s*partprobe\s*$")
        self.assertNotIn("partprobe ||", script)

    def test_partprobe_skips_live_installer_medium(self) -> None:
        runner = mock.Mock()
        with mock.patch("gentoo_installer.detect.is_live_installer_disk", return_value=True):
            _partprobe_disk(runner, "/dev/sdb")
        runner.shell.assert_not_called()

    def test_is_live_installer_disk_matches_parent(self) -> None:
        with mock.patch.object(detect, "live_boot_disk", return_value="/dev/sdb"):
            self.assertTrue(detect.is_live_installer_disk("/dev/sdb"))
            self.assertTrue(detect.is_live_installer_disk("/dev/sdb1"))
            self.assertFalse(detect.is_live_installer_disk("/dev/sda"))


if __name__ == "__main__":
    unittest.main()
