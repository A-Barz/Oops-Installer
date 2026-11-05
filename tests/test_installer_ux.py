"""Installer UX: Hyprland Quickshell rice dots and locked packages."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _atoms_to_unmask,
    _dotfile_for,
    _final_tool_checks,
    _package_use_text,
    _required_overlays,
    _required_window_manager_script,
    render_chroot_script,
)
from gentoo_installer.wizard import _packages_locked_from_earlier_steps


def _hypr_cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.username = "ashley"
    cfg.architecture = "amd64"
    cfg.window_managers = ["gui-wm/hyprland"]
    cfg.shell = "none"
    cfg.pipewire = True
    cfg.display_manager = "greetd"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.wm_dotfiles = ["gui-wm/hyprland=installer"]
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class DotfileCatalogueTests(unittest.TestCase):
    def test_hyprland_offers_installer_own_end4(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/hyprland")]
        self.assertEqual(ids, ["installer", "own", "end-4"])
        self.assertNotIn("ml4w", ids)
        self.assertNotIn("hyde", ids)
        end4 = next(c for c in catalog.dotfile_choices("gui-wm/hyprland") if c.id == "end-4")
        self.assertIn("gui-apps/quickshell", end4.packages)
        self.assertIn(("dots/.config/quickshell", ".config/quickshell"), end4.also)

    def test_niri_offers_installer_and_upstream_only(self) -> None:
        ids = [choice.id for choice in catalog.dotfile_choices("gui-wm/niri")]
        self.assertEqual(ids, ["installer", "upstream"])

    def test_unknown_compositor_has_no_dotfiles(self) -> None:
        self.assertEqual(catalog.dotfile_choices("gui-wm/sway"), [])

    def test_stale_ml4w_profile_falls_back_to_installer(self) -> None:
        choice = catalog.selected_dotfile("gui-wm/hyprland", ["gui-wm/hyprland=ml4w"])
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "installer")

    def test_end4_profile_selects_full_path(self) -> None:
        choice = catalog.selected_dotfile("gui-wm/hyprland", ["gui-wm/hyprland=end-4"])
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "end-4")
        self.assertTrue(choice.also)
        self.assertEqual(choice.source, "dots/.config/hypr")


class QuickshellHyprlandTests(unittest.TestCase):
    def test_quickshell_is_hyprland_requirement(self) -> None:
        reqs = [pkg.atom for pkg in catalog.WM_REQUIREMENTS["gui-wm/hyprland"]]
        self.assertIn("gui-apps/quickshell", reqs)
        pkg = next(p for p in catalog.WM_REQUIREMENTS["gui-wm/hyprland"] if p.atom == "gui-apps/quickshell")
        self.assertEqual(pkg.overlay, "guru")

    def test_hyprland_always_plans_quickshell_and_guru(self) -> None:
        cfg = _hypr_cfg()
        planned = set(_atoms_to_unmask(cfg))
        self.assertIn("gui-apps/quickshell", planned)
        self.assertIn("guru", _required_overlays(cfg))
        self.assertIn("hyproverlay", _required_overlays(cfg))

    def test_package_use_enables_quickshell_hyprland(self) -> None:
        use = _package_use_text(_hypr_cfg())
        self.assertIn("gui-apps/quickshell hyprland", use)

    def test_qs_is_hard_required_and_fail_closed(self) -> None:
        cfg = _hypr_cfg()
        wm = _required_window_manager_script(cfg)
        self.assertIn("gui-apps/quickshell", wm)
        self.assertIn("command -v qs", wm)
        self.assertIn("required package missing after emerge: gui-apps/quickshell", wm)
        final = _final_tool_checks(cfg, ["grub"])
        self.assertIn("required Quickshell (qs) missing", final)
        self.assertIn("missing=1", final)

    def test_installer_rice_starts_qs(self) -> None:
        script = render_chroot_script(_hypr_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn('hl.exec_cmd("qs")', script)
        self.assertIn("ShellRoot", script)
        self.assertIn(".config/quickshell/shell.qml", script)

    def test_own_import_builds_choice_from_config(self) -> None:
        cfg = _hypr_cfg(
            wm_dotfiles=["gui-wm/hyprland=own"],
            wm_dotfile_import_repo="alice/dots",
            wm_dotfile_import_branch="main",
            wm_dotfile_import_hypr=".config/hypr",
            wm_dotfile_import_quickshell=".config/quickshell",
        )
        choice = _dotfile_for(cfg, "gui-wm/hyprland")
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "own")
        self.assertEqual(choice.repo, "alice/dots")
        self.assertEqual(choice.also, ((".config/quickshell", ".config/quickshell"),))

    def test_own_without_repo_falls_back_to_installer(self) -> None:
        cfg = _hypr_cfg(wm_dotfiles=["gui-wm/hyprland=own"], wm_dotfile_import_repo="")
        choice = _dotfile_for(cfg, "gui-wm/hyprland")
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual(choice.id, "installer")
        self.assertEqual(choice.repo, "")

    def test_end4_chroot_fetches_quickshell_tree(self) -> None:
        cfg = _hypr_cfg(wm_dotfiles=["gui-wm/hyprland=end-4"])
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("dots/.config/quickshell", script)
        self.assertIn("end-4/dots-hyprland", script)
        planned = set(_atoms_to_unmask(cfg))
        self.assertIn("gui-apps/fuzzel", planned)
        self.assertIn("app-misc/cliphist", planned)


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

    def test_hyprland_locks_quickshell(self) -> None:
        locked = _packages_locked_from_earlier_steps(_hypr_cfg())
        self.assertIn("gui-apps/quickshell", locked)
        self.assertIn("gui-wm/hyprland", locked)

    def test_hyprland_installer_dots_pull_no_extra_dotfile_packages(self) -> None:
        cfg = _hypr_cfg()
        self.assertEqual(
            catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, cfg.wm_dotfiles),
            [],
        )

    def test_locked_includes_greetd_when_chosen(self) -> None:
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/sway"]
        cfg.shell = "none"
        cfg.display_manager = "greetd"
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("gui-libs/greetd", locked)
        self.assertIn("gui-apps/gtkgreet", locked)
        self.assertIn("gui-wm/labwc", locked)


if __name__ == "__main__":
    unittest.main()
