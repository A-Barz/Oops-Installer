"""H2: LUKS + GRUB enables cryptodisk and fails closed without it."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _final_tool_checks,
    _grub_uefi_install,
    _install_bootloaders,
    _limine_install,
    render_chroot_script,
)


def _cfg(**kwargs: object) -> InstallConfig:
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


class GrubCryptodiskTests(unittest.TestCase):
    def test_luks_grub_uefi_writes_cryptodisk(self) -> None:
        cfg = _cfg(luks=True, luks_passphrase="secret")
        text = _install_bootloaders(
            cfg,
            {"root": "/dev/mapper/root", "esp": "/dev/vda1", "luks_source": "/dev/vda3"},
            ["grub"],
        )
        self.assertIn("GRUB_ENABLE_CRYPTODISK=y", text)
        self.assertIn("export GRUB_ENABLE_CRYPTODISK=y", text)
        # Cryptodisk config must appear before grub-install embeds modules.
        self.assertLess(
            text.index("GRUB_ENABLE_CRYPTODISK=y"),
            text.index("grub-install --target=x86_64-efi"),
        )
        self.assertIn("exit 1", text)
        self.assertIn("grub-mkconfig -o /boot/grub/grub.cfg || { echo '>> grub-mkconfig failed' >&2; exit 1; }", text)

    def test_non_luks_grub_does_not_force_cryptodisk(self) -> None:
        cfg = _cfg(luks=False)
        text = _install_bootloaders(
            cfg,
            {"root": "/dev/vda3", "esp": "/dev/vda1"},
            ["grub"],
        )
        self.assertNotIn("GRUB_ENABLE_CRYPTODISK", text)
        # Soft-fail path for non-LUKS remains (echo-only removable fallback).
        self.assertIn("echo '>> grub-install failed' >&2", text)
        self.assertNotIn(
            "grub-mkconfig -o /boot/grub/grub.cfg || { echo '>> grub-mkconfig failed' >&2; exit 1; }",
            text,
        )

    def test_chroot_script_luks_grub_includes_cryptodisk(self) -> None:
        cfg = _cfg(luks=True, luks_passphrase="secret", initramfs="dracut")
        script = render_chroot_script(
            cfg,
            {"root": "/dev/mapper/root", "esp": "/dev/vda1", "luks_source": "/dev/vda3"},
        )
        self.assertIn("GRUB_ENABLE_CRYPTODISK=y", script)
        self.assertIn("LUKS+GRUB requires GRUB_ENABLE_CRYPTODISK=y", script)
        self.assertIn("missing /boot/grub/grub.cfg", script)

    def test_final_checks_luks_grub_missing_cryptodisk_fatal(self) -> None:
        cfg = _cfg(luks=True, luks_passphrase="secret")
        text = _final_tool_checks(cfg, ["grub"])
        self.assertIn("LUKS+GRUB requires GRUB_ENABLE_CRYPTODISK=y", text)
        self.assertIn("missing=1", text)
        self.assertIn("missing /boot/grub/grub.cfg", text)

    def test_final_checks_non_luks_skips_cryptodisk(self) -> None:
        cfg = _cfg(luks=False)
        text = _final_tool_checks(cfg, ["grub"])
        self.assertNotIn("GRUB_ENABLE_CRYPTODISK", text)
        self.assertNotIn("LUKS+GRUB", text)

    def test_limine_luks_still_copies_kernels_to_esp(self) -> None:
        cfg = _cfg(bootloader="limine", bootloaders=["limine"], luks=True, luks_passphrase="secret")
        text = _limine_install(
            cfg,
            {"root": "/dev/mapper/root", "esp": "/dev/vda1", "luks_source": "/dev/vda2"},
        )
        self.assertIn("cp -f \"$KERNEL\" /efi/vmlinuz", text)
        self.assertIn("cp -f \"$INITRD\" /efi/initramfs", text)
        self.assertIn("rd.luks.crypttab=1", text)
        self.assertNotIn("GRUB_ENABLE_CRYPTODISK", text)

    def test_grub_uefi_fail_closed_flag(self) -> None:
        soft = _grub_uefi_install("x86_64-efi", fail_closed=False)
        hard = _grub_uefi_install("x86_64-efi", fail_closed=True)
        self.assertIn("|| echo '>> grub-install failed' >&2", soft)
        self.assertNotIn("exit 1", soft)
        self.assertIn("{ echo '>> grub-install failed' >&2; exit 1; }", hard)
        self.assertIn(
            "grub-mkconfig -o /boot/grub/grub.cfg || { echo '>> grub-mkconfig failed' >&2; exit 1; }",
            hard,
        )


if __name__ == "__main__":
    unittest.main()
