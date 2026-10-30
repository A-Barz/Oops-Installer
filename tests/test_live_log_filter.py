"""Host live-log filter must keep Portage calculation / chroot-rev lines."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gentoo_installer.runner import Runner


class LiveLogFilterTests(unittest.TestCase):
    def _logged(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "installer.log"
            runner = Runner(dry_run=True, log_path=str(log))
            runner._log_live_line(text)
            if not log.exists():
                return []
            return [line.rstrip("\n") for line in log.read_text(encoding="utf-8").splitlines()]

    def test_keeps_portage_calculation_and_rev_banner(self) -> None:
        keepers = [
            "=== gentoo-live-installer chroot rev 77 ===",
            "!!! Repository 'gentoo' is missing masters",
            'emerge: there are no ebuilds to satisfy "dev-vcs/git".',
            "!!! All ebuilds that could satisfy dev-vcs/git have been masked.",
            ">>> Emerging (1 of 1) dev-vcs/git-2.45.0::gentoo",
            " * The following package is masked:",
            ">> emerge dev-vcs/git failed (exit 1)",
        ]
        for line in keepers:
            with self.subTest(line=line):
                self.assertEqual(self._logged(line), [line[:500]])

    def test_drops_compiler_chatter(self) -> None:
        chatter = [
            "  CC      foo.o",
            "checking for gcc... gcc",
            "Making all in lib",
        ]
        for line in chatter:
            with self.subTest(line=line):
                self.assertEqual(self._logged(line), [])


if __name__ == "__main__":
    unittest.main()
