"""Installer UX: top-two WM dotfiles and hiding already-selected packages."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.wizard import _packages_locked_from_earlier_steps


class DotfileTopTwoTests(unittest.TestCase):
    def test_hyprland_offers_installer_and_end4_only(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/hyprland")]
        self.assertEqual(ids, ["installer", "end-4"])

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
        # end-4 dotfiles may pull Firefox; Chrome is never auto-locked as mandatory.
        self.assertNotIn("www-client/google-chrome", locked)
        cfg_no_dot = InstallConfig()
        cfg_no_dot.window_managers = ["gui-wm/sway"]
        cfg_no_dot.shell = "none"
        cfg_no_dot.pipewire = False
        locked_plain = _packages_locked_from_earlier_steps(cfg_no_dot)
        self.assertNotIn("www-client/firefox-bin:esr", locked_plain)
        self.assertNotIn("www-client/google-chrome", locked_plain)

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
