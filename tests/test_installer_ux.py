"""Installer UX: WM dotfiles (Hyprland installer-only) and locked packages."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.wizard import _packages_locked_from_earlier_steps


class DotfileCatalogueTests(unittest.TestCase):
    def test_hyprland_offers_installer_only(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/hyprland")]
        self.assertEqual(ids, ["installer"])
        self.assertNotIn("end-4", ids)
        self.assertNotIn("ml4w", ids)
        self.assertNotIn("hyde", ids)
        # Removed from the catalogue entirely — not expert-gated.
        all_ids = [choice.id for choice in catalog.WM_DOTFILES.get("gui-wm/hyprland", ())]
        self.assertEqual(all_ids, ["installer"])

    def test_niri_offers_installer_and_upstream_only(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/niri")]
        self.assertEqual(ids, ["installer", "upstream"])

    def test_unknown_compositor_has_no_dotfiles(self) -> None:
        self.assertEqual(catalog.dotfile_choices("gui-wm/sway"), [])

    def test_stale_end4_profile_falls_back_to_installer(self) -> None:
        choice = catalog.selected_dotfile("gui-wm/hyprland", ["gui-wm/hyprland=end-4"])
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "installer")
        self.assertEqual(choice.repo, "")

    def test_stale_ml4w_profile_falls_back_to_installer(self) -> None:
        choice = catalog.selected_dotfile("gui-wm/hyprland", ["gui-wm/hyprland=ml4w"])
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
        cfg.window_managers = ["gui-wm/niri"]
        cfg.shell = "none"
        cfg.pipewire = True
        cfg.wm_dotfiles = ["gui-wm/niri=upstream"]
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("gui-wm/niri", locked)
        self.assertIn("media-video/pipewire", locked)
        self.assertIn("gui-apps/waybar", locked)
        self.assertNotIn("www-client/google-chrome", locked)
        cfg_no_dot = InstallConfig()
        cfg_no_dot.window_managers = ["gui-wm/sway"]
        cfg_no_dot.shell = "none"
        cfg_no_dot.pipewire = False
        locked_plain = _packages_locked_from_earlier_steps(cfg_no_dot)
        self.assertNotIn("www-client/firefox-bin:esr", locked_plain)
        self.assertNotIn("www-client/google-chrome", locked_plain)

    def test_niri_upstream_waybar_stays_visible_selected(self) -> None:
        """Locked dots packages show selected — not hidden (OI-86 / c5ab)."""
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/niri"]
        cfg.shell = "none"
        cfg.pipewire = True
        cfg.wm_dotfiles = ["gui-wm/niri=upstream"]
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("gui-apps/waybar", locked)
        hide_always = catalog.firewall_catalogue_hidden(bool(cfg.nftables))
        show_locked = locked - hide_always
        wayland = next(pkgs for key, _t, pkgs in catalog.CATEGORIES if key == "wayland-common")
        visible = catalog.without_already_selected(wayland, hide_always)
        visible_atoms = {atom for atom, _ in visible}
        self.assertIn("gui-apps/waybar", visible_atoms)
        self.assertIn("gui-apps/waybar", show_locked)
        hidden_old = catalog.without_already_selected(wayland, locked)
        self.assertNotIn("gui-apps/waybar", {atom for atom, _ in hidden_old})

    def test_hyprland_installer_dots_pull_no_extra_packages(self) -> None:
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/hyprland"]
        cfg.shell = "none"
        cfg.wm_dotfiles = ["gui-wm/hyprland=installer"]
        self.assertEqual(
            catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, cfg.wm_dotfiles),
            [],
        )
        stale = catalog.dotfile_package_atoms(
            cfg.shell, cfg.window_managers, ["gui-wm/hyprland=end-4"]
        )
        self.assertEqual(stale, [])

    def test_locked_includes_greetd_when_chosen(self) -> None:
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/sway"]
        cfg.shell = "none"
        cfg.display_manager = "greetd"
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("gui-libs/greetd", locked)
        self.assertIn("gui-apps/gtkgreet", locked)
        self.assertIn("gui-wm/labwc", locked)
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
        same = catalog.without_already_selected(wayland, set())
        self.assertTrue(any(atom == "gui-apps/foot" for atom, _ in same))


if __name__ == "__main__":
    unittest.main()
