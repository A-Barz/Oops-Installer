"""OI-87: freeze-path checklist hints must not wrap (Enter spam on Down)."""

from __future__ import annotations

import unittest
from unittest import mock

from gentoo_installer import ui


class ChecklistHintWrapTests(unittest.TestCase):
    def test_empty_section_hint_is_single_clipped_row(self) -> None:
        with mock.patch("gentoo_installer.ui.width", return_value=80):
            rows = ui._checklist_hint_rows(select_all=True, locked=False)
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0]), 80)
        self.assertIn("Enter — continue", rows[0])
        self.assertNotIn("already queued", rows[0])

    def test_freeze_path_puts_queued_note_on_own_row(self) -> None:
        """Hyprland dots/utils: locked defaults must not lengthen the Enter hint."""
        with mock.patch("gentoo_installer.ui.width", return_value=80):
            rows = ui._checklist_hint_rows(select_all=True, locked=True)
        self.assertEqual(len(rows), 2)
        self.assertIn("Enter — continue", rows[0])
        self.assertNotIn("already queued", rows[0])
        self.assertIn("already queued", rows[1])
        for row in rows:
            self.assertLessEqual(len(row), 80)

    def test_narrow_terminal_still_one_row_each(self) -> None:
        with mock.patch("gentoo_installer.ui.width", return_value=60):
            rows = ui._checklist_hint_rows(select_all=True, locked=True)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertLessEqual(len(row), 60)


if __name__ == "__main__":
    unittest.main()
