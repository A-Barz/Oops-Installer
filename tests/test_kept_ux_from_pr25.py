"""Keepers from PR #25: LUKS Yes default, UFW LocalSend, mise invite, banner polish.

Omarchy live sync / snapshot / inspired-by preset are intentionally absent.
"""

from __future__ import annotations

import inspect
import io
import unittest
from unittest import mock

from gentoo_installer import catalog, ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _mise_agents_invite_block,
    _ufw_block,
    render_chroot_script,
    required_atom_plan,
)
from gentoo_installer.wizard import (
    _apply_firewall_exclusion,
    _packages_locked_from_earlier_steps,
)


class LuksWizardDefaultTests(unittest.TestCase):
    def test_disks_source_defaults_luks_yes_on_wipe_paths(self) -> None:
        from gentoo_installer import wizard

        src = inspect.getsource(wizard._disks)
        self.assertIn("clear opt-out", src)
        self.assertIn("LUKS2 encrypt the root", src)
        self.assertIn("LUKS opted out", src)
        self.assertIn("Default is Yes for wipe/full-disk", src)
        # ask_yes_no(..., True) on wipe paths — not driven by a live snapshot.
        self.assertNotIn("omarchy", src.lower())
        self.assertNotIn("_omarchy_snapshot", src)


class UfwBlockTests(unittest.TestCase):
    def test_ufw_block_deny_allow_no_localsend_by_default(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        cfg.ufw = True
        cfg.ufw_localsend = False
        cfg.sshd = True
        block = _ufw_block(cfg)
        self.assertIn("default deny incoming", block)
        self.assertIn("default allow outgoing", block)
        self.assertIn("22/tcp", block)
        self.assertNotIn("53317", block)

    def test_ufw_localsend_opt_in(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        cfg.ufw = True
        cfg.ufw_localsend = True
        block = _ufw_block(cfg)
        self.assertIn("53317", block)

    def test_ufw_skipped_when_nftables(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = True
        cfg.ufw = True
        self.assertEqual(_ufw_block(cfg), "")

    def test_required_atom_plan_includes_ufw(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        cfg.ufw = True
        plan = required_atom_plan(cfg)
        self.assertIn("net-firewall/ufw", plan)
        self.assertNotIn("net-firewall/nftables", plan)

    def test_apply_firewall_queues_ufw_atom(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        cfg.ufw = True
        cfg.selected_packages = []
        cfg.extra_packages = []
        _apply_firewall_exclusion(cfg)
        self.assertTrue(cfg.ufw)
        self.assertIn(catalog.UFW_ATOM, cfg.selected_packages)
        self.assertIn(catalog.UFW_ATOM, cfg.extra_packages)

    def test_locked_hides_nftables_when_ufw(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        cfg.ufw = True
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn(catalog.NFTABLES_ATOM, locked)


class MiseInviteTests(unittest.TestCase):
    def test_invite_writes_note_without_passwordless_sudo(self) -> None:
        cfg = InstallConfig()
        cfg.mise_agents = True
        cfg.mise_agent_tools = ["claude", "codex"]
        cfg.username = "ashley"
        block = _mise_agents_invite_block(cfg)
        self.assertIn("mise-agents-invite.txt", block)
        self.assertIn("claude", block)
        self.assertIn("codex", block)
        self.assertNotIn("NOPASSWD", block)
        self.assertIn("no passwordless sudo", block.lower())
        self.assertIn("no Quickshell agents panel", block)
        self.assertNotIn("Omarchy", block)
        self.assertNotIn("omarchy", block.lower())

    def test_invite_skipped_without_flag(self) -> None:
        cfg = InstallConfig()
        cfg.mise_agents = False
        cfg.username = "ashley"
        self.assertEqual(_mise_agents_invite_block(cfg), "")

    def test_chroot_embeds_ufw_and_invite(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        cfg.ufw = True
        cfg.ufw_localsend = True
        cfg.mise_agents = True
        cfg.mise_agent_tools = ["opencode"]
        cfg.username = "gentoo"
        cfg.window_managers = ["gui-wm/hyprland"]
        script = render_chroot_script(
            cfg, {"root": "/dev/vda2", "esp": "/dev/vda1", "luks_source": ""}
        )
        self.assertIn("default deny incoming", script)
        self.assertIn("53317", script)
        self.assertIn("mise-agents-invite.txt", script)
        self.assertIn("opencode", script)


class BannerPolishTests(unittest.TestCase):
    def test_banner_includes_mark_and_openrc_safe_line(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            ui.banner()
        text = buf.getvalue()
        self.assertIn("Wayland-first", text)
        self.assertIn("OpenRC-safe", text)
        self.assertIn("OO", "\n".join(ui._oops_mark_ascii()))
        for line in ui._oops_installer_ascii():
            self.assertIn(line.rstrip(), text)


class KeptConfigFieldsTests(unittest.TestCase):
    def test_config_has_keeper_fields_not_omarchy_ux(self) -> None:
        cfg = InstallConfig()
        self.assertFalse(hasattr(cfg, "omarchy_inspired"))
        self.assertFalse(hasattr(cfg, "omarchy_sync_source"))
        self.assertFalse(hasattr(cfg, "omarchy_sync_ref"))
        self.assertFalse(cfg.mise_agents)
        self.assertEqual(cfg.mise_agent_tools, [])
        self.assertFalse(cfg.ufw)
        self.assertFalse(cfg.ufw_localsend)


class OmarchyUxGoneTests(unittest.TestCase):
    def test_omarchy_sync_module_removed(self) -> None:
        with self.assertRaises(ImportError):
            from gentoo_installer import omarchy_sync  # noqa: F401

    def test_wizard_has_no_omarchy_preset_or_sync(self) -> None:
        from gentoo_installer import wizard

        self.assertFalse(hasattr(wizard, "refresh_omarchy_cues"))
        self.assertFalse(hasattr(wizard, "_ask_omarchy_inspired_preset"))
        self.assertFalse(hasattr(wizard, "_omarchy_snapshot"))


if __name__ == "__main__":
    unittest.main()
