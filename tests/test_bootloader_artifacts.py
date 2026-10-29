"""M13 / OI-44: bootloader final checks require grub.cfg and ESP EFI artifacts."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _bootloader_boot_ok_tests,
    _final_tool_checks,
    _grub_boot_ok_test,
)


def _cfg(**kwargs) -> InstallConfig:
    cfg = InstallConfig()
    cfg.username = "ashley"
    cfg.target_disk = "/dev/vda"
    cfg.architecture = "amd64"
    cfg.firmware = "uefi"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.dry_run = True
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class BootloaderArtifactCheckTests(unittest.TestCase):
    def test_uefi_grub_requires_grub_cfg_and_esp_efi(self) -> None:
        text = _final_tool_checks(_cfg(), ["grub"])
        self.assertIn("[ -s /boot/grub/grub.cfg ]", text)
        self.assertIn("missing /boot/grub/grub.cfg", text)
        self.assertIn("/efi/EFI/BOOT/BOOTX64.EFI", text)
        self.assertIn("/efi/EFI/Gentoo/grubx64.efi", text)
        self.assertIn("missing ESP EFI payload", text)
        self.assertIn("no selected bootloader left bootable artifacts", text)
        # Binary-only success is no longer enough.
        boot_ok = _grub_boot_ok_test(_cfg())
        self.assertIn("[ -s /boot/grub/grub.cfg ]", boot_ok)
        self.assertIn("/efi/EFI/BOOT/BOOTX64.EFI", boot_ok)

    def test_bios_grub_requires_grub_cfg_not_esp(self) -> None:
        cfg = _cfg(firmware="bios")
        text = _final_tool_checks(cfg, ["grub"])
        self.assertIn("missing /boot/grub/grub.cfg", text)
        self.assertNotIn("missing ESP EFI payload", text)
        boot_ok = _grub_boot_ok_test(cfg)
        self.assertIn("[ -s /boot/grub/grub.cfg ]", boot_ok)
        self.assertNotIn("/efi/EFI/BOOT/", boot_ok)

    def test_arm64_uefi_uses_bootaa64(self) -> None:
        cfg = _cfg(architecture="arm64")
        text = _final_tool_checks(cfg, ["grub"])
        self.assertIn("/efi/EFI/BOOT/BOOTAA64.EFI", text)
        self.assertIn("/efi/EFI/Gentoo/grubaa64.efi", text)
        self.assertNotIn("BOOTX64.EFI", text)

    def test_luks_still_requires_cryptodisk(self) -> None:
        text = _final_tool_checks(_cfg(luks=True, luks_passphrase="secret"), ["grub"])
        self.assertIn("LUKS+GRUB requires GRUB_ENABLE_CRYPTODISK=y", text)
        self.assertIn("missing /boot/grub/grub.cfg", text)

    def test_systemd_boot_requires_loader_tree(self) -> None:
        tests = _bootloader_boot_ok_tests(
            _cfg(bootloader="systemd-boot", bootloaders=["systemd-boot"]),
            ["systemd-boot"],
        )
        self.assertEqual(len(tests), 1)
        self.assertIn("bootctl", tests[0])
        self.assertIn("/efi/loader", tests[0])

    def test_limine_requires_esp_artifact(self) -> None:
        tests = _bootloader_boot_ok_tests(
            _cfg(bootloader="limine", bootloaders=["limine"]),
            ["limine"],
        )
        self.assertEqual(len(tests), 1)
        self.assertIn("/efi/limine.conf", tests[0])
        self.assertIn("/efi/EFI/BOOT/BOOTX64.EFI", tests[0])


if __name__ == "__main__":
    unittest.main()
