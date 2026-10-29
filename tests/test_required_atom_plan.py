"""M4 / OI-43: one required_atom_plan feeds keywords, emerge, and H5 checks."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _atoms_to_unmask,
    _critical_service_tools,
    _service_stack_atoms,
    render_chroot_script,
    required_atom_plan,
)


def _cfg(**kwargs) -> InstallConfig:
    cfg = InstallConfig()
    cfg.username = "ashley"
    cfg.target_disk = "/dev/vda"
    cfg.architecture = "amd64"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.dry_run = True
    cfg.network_stack = "networkmanager"
    cfg.wireless = True
    cfg.chrony = True
    cfg.nftables = True
    cfg.sshd = True
    cfg.zram = True
    cfg.syslog = "sysklogd"
    cfg.cron = "cronie"
    cfg.window_managers = ["gui-wm/niri"]
    cfg.display_manager = "greetd"
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class RequiredAtomPlanTests(unittest.TestCase):
    def test_openrc_nm_plan_includes_elogind_and_nftables(self) -> None:
        plan = required_atom_plan(_cfg())
        self.assertEqual(
            plan,
            [
                "net-misc/networkmanager",
                "sys-apps/dbus",
                "sys-auth/elogind",
                "net-wireless/iw",
                "net-wireless/wpa_supplicant",
                "net-misc/openssh",
                "net-misc/chrony",
                "app-admin/sysklogd",
                "sys-process/cronie",
                "sys-block/zram-init",
                "net-firewall/nftables",
            ],
        )

    def test_service_stack_alias_matches_plan(self) -> None:
        cfg = _cfg()
        self.assertEqual(_service_stack_atoms(cfg), required_atom_plan(cfg))

    def test_keywords_emerge_and_critical_share_plan(self) -> None:
        cfg = _cfg()
        plan = required_atom_plan(cfg)
        planned = set(_atoms_to_unmask(cfg))
        for atom in plan:
            self.assertIn(atom, planned, msg=f"{atom} missing from keywords/unmask plan")

        script = render_chroot_script(
            cfg,
            {"root": "/dev/vda3", "esp": "/dev/vda1", "boot": "/dev/vda1", "swap": ""},
        )
        # Former net_pkgs drift: elogind + nftables must appear in the chroot script.
        self.assertIn("sys-auth/elogind", script)
        self.assertIn("net-firewall/nftables", script)

        critical = [atom for atom, _probe in _critical_service_tools(cfg)]
        self.assertEqual(
            critical,
            [
                "net-misc/networkmanager",
                "net-misc/chrony",
                "app-admin/sysklogd",
                "sys-process/cronie",
                "net-firewall/nftables",
            ],
        )
        for atom in critical:
            self.assertIn(atom, plan)

    def test_dhcpcd_plan_skips_nm_stack(self) -> None:
        cfg = _cfg(network_stack="dhcpcd")
        plan = required_atom_plan(cfg)
        self.assertIn("net-misc/dhcpcd", plan)
        self.assertNotIn("net-misc/networkmanager", plan)
        self.assertNotIn("sys-auth/elogind", plan)
        critical = [atom for atom, _ in _critical_service_tools(cfg)]
        self.assertIn("net-misc/dhcpcd", critical)
        self.assertNotIn("net-misc/networkmanager", critical)

    def test_systemd_networkd_plan(self) -> None:
        cfg = _cfg(init_system="systemd", network_stack="systemd-networkd", zram=True)
        plan = required_atom_plan(cfg)
        self.assertIn("sys-apps/systemd", plan)
        self.assertIn("sys-apps/zram-generator", plan)
        self.assertNotIn("sys-block/zram-init", plan)
        self.assertNotIn("app-admin/sysklogd", plan)  # OpenRC-only syslog atom
        critical = [atom for atom, _ in _critical_service_tools(cfg)]
        self.assertIn("sys-apps/systemd", critical)

    def test_disabled_optionals_drop_from_plan(self) -> None:
        cfg = _cfg(chrony=False, nftables=False, wireless=False, sshd=False, zram=False, syslog="none", cron="none")
        plan = required_atom_plan(cfg)
        self.assertEqual(
            plan,
            [
                "net-misc/networkmanager",
                "sys-apps/dbus",
                "sys-auth/elogind",
            ],
        )
        critical = [atom for atom, _ in _critical_service_tools(cfg)]
        self.assertEqual(critical, ["net-misc/networkmanager"])


if __name__ == "__main__":
    unittest.main()
