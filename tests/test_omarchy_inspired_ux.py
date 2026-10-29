"""Omarchy-inspired UX: live sync fallback, LUKS default, UFW, mise invite, banner."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gentoo_installer import catalog, omarchy_sync, ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _mise_agents_invite_block,
    _ufw_block,
    render_chroot_script,
    required_atom_plan,
)
from gentoo_installer.wizard import _apply_firewall_exclusion, _packages_locked_from_earlier_steps


class OmarchySyncTests(unittest.TestCase):
    def test_bundled_snapshot_loads_offline(self) -> None:
        snap = omarchy_sync.load_omarchy_snapshot(offline=True)
        self.assertIn(snap.source, {"bundled", "cache", "builtin"})
        self.assertTrue(snap.encrypt_default_on)
        self.assertTrue(snap.ufw_deny_incoming)
        self.assertTrue(snap.ufw_allow_outgoing)
        tools = snap.agent_tools_for_invite()
        self.assertTrue(tools)
        self.assertLessEqual(len(tools), 4)
        for name in tools:
            self.assertIn(name, omarchy_sync.SAFE_AGENT_TOOLS)

    def test_map_arch_packages_maps_and_skips(self) -> None:
        mapped, skipped = omarchy_sync.map_arch_packages(
            ["hyprland", "git", "mise", "quickshell", "ufw", "unknown-omarchy-pkg"]
        )
        self.assertIn("gui-wm/hyprland", mapped)
        self.assertIn("dev-vcs/git", mapped)
        self.assertIn("net-firewall/ufw", mapped)
        self.assertTrue(any("mise" in s for s in skipped))
        self.assertTrue(any("quickshell" in s for s in skipped))
        self.assertTrue(any("unknown-omarchy-pkg" in s for s in skipped))

    def test_live_failure_falls_back_without_raising(self) -> None:
        with mock.patch.object(
            omarchy_sync,
            "_fetch_live",
            side_effect=TimeoutError("boom"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                cache = Path(tmp) / "cache.json"
                with mock.patch.object(omarchy_sync, "cache_path", return_value=cache):
                    snap = omarchy_sync.load_omarchy_snapshot(timeout=0.1)
        self.assertIn(snap.source, {"bundled", "cache", "builtin"})
        self.assertIn("live", snap.error)

    def test_bundle_file_present(self) -> None:
        path = omarchy_sync.bundle_path()
        self.assertTrue(path.is_file(), msg=str(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("repo"), "omacom/omarchy")
        self.assertTrue(data.get("mise_tools"))


class LuksWizardDefaultTests(unittest.TestCase):
    def test_disks_source_defaults_luks_yes_on_wipe_paths(self) -> None:
        import inspect

        from gentoo_installer import wizard

        src = inspect.getsource(wizard._disks)
        self.assertIn("clear opt-out", src)
        self.assertIn("LUKS2 encrypt the root", src)
        # ask_yes_no(..., luks_default) with encrypt_default_on → Yes on wipe paths.
        self.assertIn("luks_default", src)
        self.assertIn("LUKS opted out", src)


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


class OmarchyPresetFieldsTests(unittest.TestCase):
    def test_config_has_omarchy_fields(self) -> None:
        cfg = InstallConfig()
        self.assertFalse(cfg.omarchy_inspired)
        self.assertFalse(cfg.mise_agents)
        self.assertEqual(cfg.mise_agent_tools, [])
        self.assertFalse(cfg.ufw)
        self.assertFalse(cfg.ufw_localsend)


if __name__ == "__main__":
    unittest.main()
