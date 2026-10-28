"""Regression checks for package UX rewrite, banner host string, and codecs offer."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from gentoo_installer import catalog, ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.wizard import _ask_nonfree_codecs, _packages_locked_from_earlier_steps


class BannerHostTests(unittest.TestCase):
    def test_banner_says_ubuntu_boot_image_not_xfce(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            ui.banner()
        text = buf.getvalue()
        self.assertIn("Ubuntu Boot Image", text)
        self.assertNotIn("XFCE", text)
        self.assertNotIn("Xfce", text)


class CodecsOfferTests(unittest.TestCase):
    def test_codecs_prompt_still_reachable(self) -> None:
        answers = iter(["n"])

        def fake_yes_no(prompt: str, default: bool = True) -> bool:  # noqa: ARG001
            return next(answers) == "y"

        import gentoo_installer.ui as ui_mod

        original = ui_mod.ask_yes_no
        ui_mod.ask_yes_no = fake_yes_no  # type: ignore[assignment]
        try:
            cfg = InstallConfig()
            _ask_nonfree_codecs(cfg)
            self.assertFalse(cfg.nonfree_codecs)
        finally:
            ui_mod.ask_yes_no = original  # type: ignore[assignment]


class PackageCategoryTests(unittest.TestCase):
    def test_browsers_category_only_mandatory_pair(self) -> None:
        rows = next(pkgs for key, _t, pkgs in catalog.CATEGORIES if key == "browsers")
        self.assertEqual(
            [a for a, _ in rows],
            ["www-client/firefox-bin:esr", "www-client/google-chrome"],
        )

    def test_greetd_locked_hides_display_manager_rows(self) -> None:
        cfg = InstallConfig()
        cfg.window_managers = ["gui-wm/hyprland"]
        cfg.shell = "none"
        cfg.display_manager = "greetd"
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertTrue({"gui-libs/greetd", "gui-apps/gtkgreet"} <= locked)


if __name__ == "__main__":
    unittest.main()
