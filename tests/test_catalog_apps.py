"""Wayland WM catalogue, codecs license gate, editors/IDEs."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import _atoms_to_unmask
from gentoo_installer.wizard import _ask_nonfree_codecs, _packages_locked_from_earlier_steps


class WaylandWmTests(unittest.TestCase):
    def test_top_wayland_list_includes_restored_guru_options(self) -> None:
        atoms = [pkg.atom for pkg in catalog.WAYLAND_COMPOSITORS]
        for want in (
            "gui-wm/hyprland",
            "gui-wm/sway",
            "gui-wm/swayfx",
            "gui-wm/niri",
            "gui-wm/river",
            "gui-wm/wayfire",
            "gui-wm/labwc",
            "gui-wm/dwl",
            "gui-wm/mangowm",
            "gui-wm/miracle-wm",
            "x11-wm/qtile",
        ):
            self.assertIn(want, atoms)
        self.assertGreaterEqual(len(catalog.WAYLAND_COMPOSITORS), 10)
        self.assertNotIn("gui-wm/swayfx", catalog.REMOVED_GURU_ATOMS)
        self.assertNotIn("gui-wm/river", catalog.REMOVED_GURU_ATOMS)

    def test_offered_guru_includes_selected_compositors(self) -> None:
        allowed = catalog.offered_guru_atoms("none")
        self.assertIn("gui-wm/niri", allowed)
        self.assertIn("gui-wm/river", allowed)
        self.assertIn("gui-wm/swayfx", allowed)
        self.assertIn("gui-wm/mangowm", allowed)
        self.assertIn("gui-wm/miracle-wm", allowed)
        self.assertNotIn("gui-wm/cage", allowed)


class CodecLicenseTests(unittest.TestCase):
    def test_codecs_default_off_and_not_queued(self) -> None:
        cfg = InstallConfig()
        self.assertFalse(cfg.nonfree_codecs)
        self.assertNotIn("media-libs/fdk-aac", _atoms_to_unmask(cfg))

    def test_accepted_codecs_are_queued_and_locked(self) -> None:
        cfg = InstallConfig()
        cfg.nonfree_codecs = True
        cfg.accept_license = "@FREE @BINARY-REDISTRIBUTABLE FraunhoferFDK MPEG-4 *"
        atoms = catalog.nonfree_codec_atoms()
        self.assertIn("media-libs/gst-plugins-ugly", atoms)
        self.assertIn("media-libs/fdk-aac", atoms)
        locked = _packages_locked_from_earlier_steps(cfg)
        for atom in atoms:
            self.assertIn(atom, locked)
        self.assertIn("media-libs/fdk-aac", _atoms_to_unmask(cfg))

    def test_decline_path_leaves_flag_false(self) -> None:
        # Drive the prompt with canned answers: offer=no.
        answers = iter(["n"])

        def fake_yes_no(prompt: str, default: bool = True) -> bool:  # noqa: ARG001
            return next(answers) == "y"

        import gentoo_installer.wizard as wiz
        import gentoo_installer.ui as ui_mod

        original = ui_mod.ask_yes_no
        ui_mod.ask_yes_no = fake_yes_no  # type: ignore[assignment]
        try:
            cfg = InstallConfig()
            _ask_nonfree_codecs(cfg)
            self.assertFalse(cfg.nonfree_codecs)
        finally:
            ui_mod.ask_yes_no = original  # type: ignore[assignment]


class EditorsIdesTests(unittest.TestCase):
    def _category_atoms(self, key: str) -> set[str]:
        for cat_key, _title, rows in catalog.CATEGORIES:
            if cat_key == key:
                return {atom for atom, _desc in rows}
        self.fail(f"missing category {key}")
        return set()

    def test_editors_include_sublime_obsidian_pulsar_note(self) -> None:
        atoms = self._category_atoms("editors")
        self.assertIn("app-editors/sublime-text", atoms)
        self.assertIn("flatpak-app/md.obsidian.Obsidian", atoms)
        self.assertIn("flatpak-app/dev.pulsar_edit.Pulsar", atoms)
        self.assertIn("app-editors/helix", atoms)
        self.assertIn("app-editors/neovim", atoms)

    def test_ides_include_vscode_zed_jetbrains_flatpaks(self) -> None:
        atoms = self._category_atoms("ides")
        self.assertIn("app-editors/vscode", atoms)
        self.assertIn("app-editors/vscodium", atoms)
        self.assertIn("app-editors/zed", atoms)
        self.assertIn("flatpak-app/com.jetbrains.IntelliJ-IDEA-Community", atoms)
        self.assertIn("flatpak-app/com.jetbrains.PyCharm-Community", atoms)
        # vscode is no longer treated as a removed GURU atom.
        self.assertNotIn("app-editors/vscode", catalog.REMOVED_GURU_ATOMS)


if __name__ == "__main__":
    unittest.main()
