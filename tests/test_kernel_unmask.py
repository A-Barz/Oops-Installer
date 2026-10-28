"""Kernel menu options and pre-emerge keyword / unmask coverage."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _atoms_to_unmask,
    _keyword_text,
    _overlay_script,
    _unmask_text,
)


class KernelChoiceTests(unittest.TestCase):
    def test_menu_includes_gentoo_and_cachy_not_ubuntu(self) -> None:
        names = [name for name, _desc in catalog.kernel_menu_choices()]
        self.assertEqual(
            names,
            [
                "gentoo-kernel-bin",
                "gentoo-kernel",
                "gentoo-sources-lts",
                "gentoo-sources",
                "cachyos-kernel-bin",
                "cachyos-kernel",
            ],
        )
        joined = " ".join(names).lower()
        self.assertNotIn("ubuntu", joined)

    def test_lts_menu_emerges_real_gentoo_sources_stable(self) -> None:
        """No fake *-lts ebuild — Gentoo LTS is stable sys-kernel/gentoo-sources."""
        self.assertEqual(catalog.kernel_atom("gentoo-sources-lts"), "sys-kernel/gentoo-sources")
        self.assertTrue(catalog.kernel_uses_stable_lts("gentoo-sources-lts"))
        self.assertFalse(catalog.kernel_uses_stable_lts("gentoo-sources"))
        self.assertEqual(catalog.kernel_overlay("gentoo-sources-lts"), "gentoo")
        labels = dict(catalog.kernel_menu_choices())
        self.assertIn("LTS", labels["gentoo-sources-lts"])
        self.assertIn("gentoo-sources", labels["gentoo-sources-lts"])

    def test_lts_keywords_stay_stable(self) -> None:
        cfg = InstallConfig()
        cfg.architecture = "amd64"
        cfg.testing_keywords = False
        cfg.kernel = "gentoo-sources-lts"
        text = _keyword_text(cfg)
        self.assertIn("sys-kernel/gentoo-sources -~amd64", text)
        self.assertNotIn("sys-kernel/gentoo-sources ~amd64", text)

    def test_lts_keywords_pin_when_global_testing(self) -> None:
        cfg = InstallConfig()
        cfg.architecture = "amd64"
        cfg.testing_keywords = True
        cfg.kernel = "gentoo-sources-lts"
        text = _keyword_text(cfg)
        self.assertEqual(text.strip(), "sys-kernel/gentoo-sources -~amd64")

    def test_cachy_maps_to_overlay(self) -> None:
        self.assertEqual(catalog.kernel_overlay("cachyos-kernel-bin"), "CachyOS-kernels")
        self.assertEqual(catalog.kernel_atom("cachyos-kernel"), "sys-kernel/cachyos-kernel")
        self.assertEqual(catalog.kernel_overlay("gentoo-kernel-bin"), "gentoo")
        self.assertEqual(catalog.atom_overlay("sys-kernel/cachyos-kernel-bin"), "CachyOS-kernels")

    def test_overlay_script_enables_cachy_when_selected(self) -> None:
        cfg = InstallConfig()
        cfg.kernel = "cachyos-kernel-bin"
        cfg.overlays = []
        script = _overlay_script(cfg)
        self.assertIn("CachyOS-kernels", script)
        self.assertIn("eselect repository enable", script)
        self.assertIn("package.unmask/overlays", script)
        self.assertIn("package.accept_keywords/overlay-helpers", script)
        emerge = script.find("emerge --ask=n --noreplace app-eselect/eselect-repository")
        self.assertGreaterEqual(emerge, 0)
        self.assertLess(script.find("package.accept_keywords/overlay-helpers"), emerge)


class UnmaskBeforeEmergeTests(unittest.TestCase):
    def test_keywords_cover_kernel_and_queued_packages(self) -> None:
        cfg = InstallConfig()
        cfg.architecture = "amd64"
        cfg.testing_keywords = False
        cfg.kernel = "cachyos-kernel"
        cfg.window_managers = ["gui-wm/niri"]
        cfg.shell = "none"
        cfg.pipewire = True
        cfg.selected_packages = ["www-client/firefox-bin", "gui-apps/waybar"]
        cfg.display_manager = "greetd"
        text = _keyword_text(cfg)
        self.assertIn("sys-kernel/cachyos-kernel ~amd64", text)
        self.assertIn("gui-wm/niri ~amd64", text)
        self.assertIn("www-client/firefox-bin ~amd64", text)
        self.assertIn("gui-libs/greetd ~amd64", text)

    def test_global_testing_keywords_skips_per_package_file(self) -> None:
        cfg = InstallConfig()
        cfg.testing_keywords = True
        cfg.kernel = "gentoo-kernel-bin"
        self.assertEqual(_keyword_text(cfg), "")

    def test_unmask_lists_planned_atoms(self) -> None:
        cfg = InstallConfig()
        cfg.kernel = "gentoo-kernel-bin"
        cfg.window_managers = ["gui-wm/sway"]
        cfg.shell = "none"
        atoms = _atoms_to_unmask(cfg)
        self.assertIn("sys-kernel/gentoo-kernel-bin", atoms)
        self.assertIn("gui-wm/sway", atoms)
        unmask = _unmask_text(cfg)
        self.assertIn("sys-kernel/gentoo-kernel-bin", unmask)
        self.assertIn("gui-apps/swaylock", unmask)

    def test_stage3_does_not_imply_missing_kernel_choice(self) -> None:
        """Kernel is always a separate InstallConfig field; stage_level never hides it."""
        cfg = InstallConfig()
        cfg.stage_level = "stage3"
        cfg.kernel = "gentoo-kernel-bin"
        names = {name for name, _ in catalog.kernel_menu_choices()}
        self.assertIn(cfg.kernel, names)
        self.assertEqual(cfg.stage_level, "stage3")


if __name__ == "__main__":
    unittest.main()
