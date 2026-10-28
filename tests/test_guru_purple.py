"""GURU overlay name is painted Gentoo purple whenever the installer prints it."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout

from gentoo_installer import ui


class GuruPurpleTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["FORCE_COLOR"] = "1"
        os.environ.pop("NO_COLOR", None)

    def test_highlight_wraps_guru_in_gentoo_purple(self) -> None:
        out = ui.highlight_guru("enable guru then sync")
        self.assertIn(ui.C.PURPLE, out)
        self.assertIn("guru", out)
        self.assertTrue(out.index(ui.C.PURPLE) < out.lower().index("guru"))

    def test_highlight_preserves_gURU_casing(self) -> None:
        out = ui.highlight_guru("GURU overlay compositors")
        self.assertIn(f"{ui.C.BOLD}{ui.C.PURPLE}GURU{ui.C.RESET}", out)

    def test_highlight_matches_colon_colon_guru(self) -> None:
        out = ui.highlight_guru("niri  ::guru")
        self.assertIn(f"{ui.C.BOLD}{ui.C.PURPLE}guru{ui.C.RESET}", out)

    def test_highlight_skips_when_no_color(self) -> None:
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertEqual(ui.highlight_guru("enable guru"), "enable guru")
        finally:
            os.environ.pop("NO_COLOR", None)

    def test_info_prints_purple_guru(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.info("That overlay is guru for niri.")
        out = buf.getvalue()
        self.assertIn(ui.C.PURPLE, out)
        self.assertIn("guru", out)

    def test_does_not_color_inside_longer_words(self) -> None:
        out = ui.highlight_guru("guruguru and unguruish")
        # No bare overlay token — leave the string alone.
        self.assertEqual(out, "guruguru and unguruish")


if __name__ == "__main__":
    unittest.main()
