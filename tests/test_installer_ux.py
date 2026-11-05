"""Installer UX: WM dotfiles (Hyprland safe default) and locked packages."""

from __future__ import annotations

import unittest
from unittest import mock

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.wizard import _hyprland_dotfile_menu_choices, _packages_locked_from_earlier_steps


class DotfileTopTwoTests(unittest.TestCase):
    def test_hyprland_safe_menu_is_installer_only(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/hyprland")]
        self.assertEqual(ids, ["installer"])
        self.assertNotIn("end-4", ids)
        self.assertNotIn("ml4w", ids)
        self.assertNotIn("hyde", ids)

    def test_hyprland_expert_menu_keeps_third_party(self) -> None:
        ids = [
            choice.id
            for choice in catalog.dotfile_choices("gui-wm/hyprland", include_expert=True)
        ]
        self.assertEqual(ids, ["installer", "end-4", "ml4w"])
        experts = [
            choice.id
            for choice in catalog.dotfile_choices("gui-wm/hyprland", include_expert=True)
            if choice.expert
        ]
        self.assertEqual(experts, ["end-4", "ml4w"])

    def test_niri_offers_installer_and_upstream_only(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/niri")]
        self.assertEqual(ids, ["installer", "upstream"])

    def test_unknown_compositor_has_no_dotfiles(self) -> None:
        self.assertEqual(catalog.dotfile_choices("gui-wm/sway"), [])

    def test_stale_saved_dotfile_id_falls_back(self) -> None:
        choice = catalog.selected_dotfile("gui-wm/hyprland", ["gui-wm/hyprland=hyde"])
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "installer")

    def test_selected_dotfile_still_resolves_expert_id(self) -> None:
        choice = catalog.selected_dotfile("gui-wm/hyprland", ["gui-wm/hyprland=end-4"])
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "end-4")
        self.assertTrue(choice.expert)


class HyprlandExpertGateTests(unittest.TestCase):
    def test_default_path_stays_installer_without_expert_yes(self) -> None:
        with mock.patch("gentoo_installer.wizard.ui.info"), mock.patch(
            "gentoo_installer.wizard.ui.ask_yes_no", return_value=False
        ) as ask:
            choices, default = _hyprland_dotfile_menu_choices("gui-wm/hyprland", "installer")
        self.assertEqual([c.id for c in choices], ["installer"])
        self.assertEqual(default, "installer")
        self.assertEqual(ask.call_count, 1)

    def test_expert_path_requires_two_confirms(self) -> None:
        with mock.patch("gentoo_installer.wizard.ui.info"), mock.patch(
            "gentoo_installer.wizard.ui.danger"
        ), mock.patch(
            "gentoo_installer.wizard.ui.ask_yes_no", side_effect=[True, True]
        ):
            choices, default = _hyprland_dotfile_menu_choices("gui-wm/hyprland", "installer")
        self.assertEqual([c.id for c in choices], ["installer", "end-4", "ml4w"])
        self.assertEqual(default, "installer")

    def test_saved_expert_declined_falls_back_to_installer(self) -> None:
        with mock.patch("gentoo_installer.wizard.ui.warn"), mock.patch(
            "gentoo_installer.wizard.ui.ask_yes_no", return_value=False
        ):
            choices, default = _hyprland_dotfile_menu_choices("gui-wm/hyprland", "end-4")
        self.assertEqual([c.id for c in choices], ["installer"])
        self.assertEqual(default, "installer")


class AlreadySelectedPackageTests(unittest.TestCase):
    def test_without_already_selected_filters_rows(self) -> None:
        rows = [
            ("gui-apps/foot", "foot"),
            ("gui-apps/waybar", "Waybar"),
            ("x11-terms/kitty", "Kitty"),
        ]
        visible = catalog.without_already_selected(rows, {"gui-apps/foot", "gui-apps/waybar"})
        self.assertEqual(visible, [("x11-terms/kitty", "Kitty")])

    def test_locked_includes_wm_requirements_and_dotfile_packages(self) -> None:
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/hyprland"]
        cfg.shell = "none"
        cfg.pipewire = True
        cfg.wm_dotfiles = ["gui-wm/hyprland=end-4"]
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("gui-wm/hyprland", locked)
        self.assertIn("gui-apps/hyprpaper", locked)
        self.assertIn("media-video/pipewire", locked)
        self.assertIn("sys-process/btop", locked)
        # end-4 dotfiles may pull Firefox; Chrome is never auto-locked as mandatory.
        self.assertNotIn("www-client/google-chrome", locked)
        cfg_no_dot = InstallConfig()
        cfg_no_dot.window_managers = ["gui-wm/sway"]
        cfg_no_dot.shell = "none"
        cfg_no_dot.pipewire = False
        locked_plain = _packages_locked_from_earlier_steps(cfg_no_dot)
        self.assertNotIn("www-client/firefox-bin:esr", locked_plain)
        self.assertNotIn("www-client/google-chrome", locked_plain)

    def test_end4_btop_stays_visible_selected_in_system_admin(self) -> None:
        """Locked expert-dots packages show selected — not hidden (OI-86 / c5ab)."""
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/hyprland"]
        cfg.shell = "none"
        cfg.pipewire = True
        cfg.wm_dotfiles = ["gui-wm/hyprland=end-4"]
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("sys-process/btop", locked)
        hide_always = catalog.firewall_catalogue_hidden(bool(cfg.nftables))
        show_locked = locked - hide_always
        system = next(pkgs for key, _t, pkgs in catalog.CATEGORIES if key == "system")
        # Do not hide queued dots deps — only mutual-exclusion / claimed rows.
        visible = catalog.without_already_selected(system, hide_always)
        visible_atoms = {atom for atom, _ in visible}
        self.assertIn("sys-process/btop", visible_atoms)
        self.assertIn("sys-process/htop", visible_atoms)
        self.assertIn("sys-process/btop", show_locked)
        # Old hide-locked behavior would drop btop; that must not return.
        hidden_old = catalog.without_already_selected(system, locked)
        self.assertNotIn("sys-process/btop", {atom for atom, _ in hidden_old})

    def test_locked_includes_greetd_when_chosen(self) -> None:
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/sway"]
        cfg.shell = "none"
        cfg.display_manager = "greetd"
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("gui-libs/greetd", locked)
        self.assertIn("gui-apps/gtkgreet", locked)
        self.assertIn("gui-wm/labwc", locked)
        # Display-manager category must not re-offer greeter stack.
        dm_rows = next(pkgs for key, _t, pkgs in catalog.CATEGORIES if key == "display-managers")
        visible = catalog.without_already_selected(dm_rows, locked)
        visible_atoms = {atom for atom, _ in visible}
        self.assertNotIn("gui-libs/greetd", visible_atoms)
        self.assertNotIn("gui-apps/gtkgreet", visible_atoms)

    def test_overlapping_category_rows_can_be_hidden_after_claim(self) -> None:
        wayland = next(pkgs for key, _title, pkgs in catalog.CATEGORIES if key == "wayland-common")
        terminals = next(pkgs for key, _title, pkgs in catalog.CATEGORIES if key == "terminals")
        claimed = {"gui-apps/foot", "x11-terms/alacritty"}
        later = catalog.without_already_selected(terminals, claimed)
        later_atoms = {atom for atom, _ in later}
        self.assertNotIn("gui-apps/foot", later_atoms)
        self.assertNotIn("x11-terms/alacritty", later_atoms)
        self.assertTrue(any(atom == "x11-terms/kitty" for atom, _ in later))
        # Same-category revisit keeps claimed atoms visible when not in hide.
        same = catalog.without_already_selected(wayland, set())
        self.assertTrue(any(atom == "gui-apps/foot" for atom, _ in same))


if __name__ == "__main__":
    unittest.main()
