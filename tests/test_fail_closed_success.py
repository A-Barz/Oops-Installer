"""H5/H6: fail-closed install success for required services and selected WMs."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _critical_service_final_checks,
    _emerge_each_block,
    _final_tool_checks,
    _required_tool_repairs,
    _required_window_manager_script,
    _resume_prelude,
    render_chroot_script,
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
    cfg.chrony = True
    cfg.nftables = True
    cfg.syslog = "sysklogd"
    cfg.cron = "cronie"
    cfg.window_managers = ["gui-wm/niri"]
    cfg.display_manager = "greetd"
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class CriticalServiceFailClosedTests(unittest.TestCase):
    def test_final_checks_require_nm_chrony_nftables(self) -> None:
        text = _final_tool_checks(_cfg(), ["grub"])
        self.assertIn("required service missing: NetworkManager", text)
        self.assertIn("required service missing: chronyd", text)
        self.assertIn("required service missing: nftables", text)
        self.assertIn("required service missing: sysklogd", text)
        self.assertIn("required service missing: cronie", text)
        self.assertIn("missing=1", text)
        # Warn-only path must not be used for these critical services.
        self.assertNotIn("still missing: NetworkManager", text)
        self.assertNotIn("still missing: chronyd", text)

    def test_final_checks_nm_missing_sets_missing_even_if_greeter_ok(self) -> None:
        checks = _critical_service_final_checks(_cfg())
        labels = [label for _test, label in checks]
        self.assertIn("NetworkManager", labels)
        self.assertIn("chronyd", labels)

    def test_chrony_disabled_skips_chronyd_requirement(self) -> None:
        text = _final_tool_checks(_cfg(chrony=False), ["grub"])
        self.assertNotIn("required service missing: chronyd", text)

    def test_dhcpcd_stack_is_required(self) -> None:
        text = _final_tool_checks(_cfg(network_stack="dhcpcd"), ["grub"])
        self.assertIn("required service missing: dhcpcd", text)
        self.assertNotIn("required service missing: NetworkManager", text)

    def test_repairs_mark_critical_services_required(self) -> None:
        text = _required_tool_repairs(_cfg(), ["grub"])
        self.assertIn(
            "required package missing after emerge: net-misc/networkmanager",
            text,
        )
        self.assertIn("required package missing after emerge: net-misc/chrony", text)
        self.assertIn(
            "required package missing after emerge: net-firewall/nftables",
            text,
        )
        # NM recovery still runs before the hard check.
        self.assertLess(
            text.find("NetworkManager init script still missing"),
            text.find("required package missing after emerge: net-misc/networkmanager"),
        )


class WorldFailClosedTests(unittest.TestCase):
    def test_world_failure_exits_not_continues(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn('echo ">> emerge @world failed', script)
        self.assertNotIn("continuing with the selected packages", script)
        world = script.find(">> emerge @world")
        fail = script.find("emerge @world failed", world)
        self.assertGreater(fail, world)
        # Non-zero @world dumps Portage detail, retries --usepkg=n once, then
        # fails closed if the source retry still fails.
        self.assertIn("dump_world_emerge_failure", script)
        retry_fail = script.find("@world source rebuild failed", fail)
        self.assertGreater(retry_fail, fail)
        chunk = script[retry_fail : retry_fail + 280]
        self.assertIn("exit 1", chunk)
        self.assertLess(
            script.find("dump_world_emerge_failure \"$world_status\"", fail),
            retry_fail,
        )


class RequiredWindowManagerTests(unittest.TestCase):
    def test_selected_niri_is_hard_required_before_greeter(self) -> None:
        cfg = _cfg()
        wm = _required_window_manager_script(cfg)
        self.assertIn("required window managers / compositors", wm)
        self.assertIn(
            "required package missing after emerge: gui-wm/niri",
            wm,
        )
        self.assertIn("command -v niri-session", wm)
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        wm_pos = script.find(">> required window managers / compositors")
        dm_pos = script.find(">> login manager: greetd")
        self.assertGreaterEqual(wm_pos, 0)
        self.assertGreaterEqual(dm_pos, 0)
        self.assertLess(wm_pos, dm_pos)

    def test_niri_not_in_soft_late_block(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        late = script.find(">> desktop and extra packages")
        self.assertGreaterEqual(late, 0)
        # Soft block ends before required WM section.
        wm = script.find(">> required window managers / compositors", late)
        soft = script[late:wm]
        self.assertNotIn("emerge_one gui-wm/niri", soft)
        self.assertNotIn("emerge_one 'gui-wm/niri'", soft)

    def test_final_checks_require_selected_wm_binary(self) -> None:
        text = _final_tool_checks(_cfg(), ["grub"])
        self.assertIn("no selected window manager binary is installed", text)
        self.assertIn("niri-session", text)
        self.assertIn("missing=1", text)

    def test_optional_catalogue_still_soft_fails(self) -> None:
        block = _emerge_each_block(
            "desktop and extra packages",
            ["gui-apps/waybar", "sys-apps/flatpak"],
        )
        self.assertIn("emerge_one gui-apps/waybar || true", block)
        self.assertIn("emerge_one sys-apps/flatpak || true", block)


class ResumeNoreplaceCompanionTests(unittest.TestCase):
    def test_resume_notes_emerge_checked_skips_noreplace(self) -> None:
        text = _resume_prelude(_cfg(resume=True))
        self.assertIn("NR=\"--noreplace\"", text)
        self.assertIn("emerge_checked (no --noreplace)", text)

    def test_required_wm_path_uses_emerge_checked_not_emerge_one(self) -> None:
        wm = _required_window_manager_script(_cfg())
        self.assertIn("emerge_checked gui-wm/niri", wm)
        self.assertNotIn("emerge_one", wm)


if __name__ == "__main__":
    unittest.main()
