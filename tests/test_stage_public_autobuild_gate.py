"""OI-86: refuse stage1/2 before wget; public autobuilds are stage3-only."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _refuse_non_public_stage_level,
    _resolve_stage,
    phase_stage3,
)
from gentoo_installer.rev import INSTALLER_REV
from gentoo_installer import wizard


class StagePublicAutobuildGateTests(unittest.TestCase):
    def test_rev_bumped(self) -> None:
        self.assertGreaterEqual(INSTALLER_REV, 88)

    def test_wizard_copy_states_public_stage3_only(self) -> None:
        src = inspect.getsource(wizard._stage)
        self.assertIn("Public Gentoo autobuilds publish stage3 only", src)
        self.assertIn("not on public autobuilds", src)
        self.assertIn("Continue with stage3 instead", src)
        self.assertIn("latest-stage1/2", src)

    def test_refuse_stage2_before_wget(self) -> None:
        cfg = InstallConfig()
        cfg.stage_level = "stage2"
        with self.assertRaises(SystemExit) as raised:
            _refuse_non_public_stage_level(cfg)
        msg = str(raised.exception)
        self.assertIn("stage_level=stage2", msg)
        self.assertIn("stage3", msg)
        self.assertIn("404", msg)

    def test_refuse_stage1_before_wget(self) -> None:
        cfg = InstallConfig()
        cfg.stage_level = "stage1"
        with self.assertRaises(SystemExit) as raised:
            _refuse_non_public_stage_level(cfg)
        self.assertIn("stage_level=stage1", str(raised.exception))

    def test_stage3_is_allowed(self) -> None:
        cfg = InstallConfig()
        cfg.stage_level = "stage3"
        _refuse_non_public_stage_level(cfg)  # does not raise

    def test_resolve_stage_refuses_without_wget(self) -> None:
        cfg = InstallConfig()
        cfg.stage_level = "stage2"
        cfg.architecture = "amd64"
        runner = mock.Mock()
        with self.assertRaises(SystemExit) as raised:
            _resolve_stage(cfg, runner, "/mnt/gentoo", "stage2-amd64-openrc")
        self.assertIn("stage_level=stage2", str(raised.exception))
        runner.run.assert_not_called()

    def test_phase_stage3_refuses_stage2_without_wget(self) -> None:
        cfg = InstallConfig()
        cfg.stage_level = "stage2"
        cfg.resume = False
        runner = mock.Mock()
        with mock.patch("gentoo_installer.execute._prepare_mountpoint") as prepare:
            with self.assertRaises(SystemExit) as raised:
                phase_stage3(cfg, runner, "/mnt/gentoo")
        self.assertIn("stage_level=stage2", str(raised.exception))
        prepare.assert_not_called()
        runner.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
