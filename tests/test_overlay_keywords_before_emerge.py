"""Overlay helpers and */*::overlay must be keyworded/unmasked before emerge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    OVERLAY_HELPER_ATOMS,
    _overlay_helper_keyword_text,
    _overlay_helper_unmask_text,
    _overlay_repo_keyword_text,
    _overlay_repo_unmask_text,
    _overlay_script,
    _required_overlays,
    _write_make_conf,
    _write_overlay_portage_files,
    render_chroot_script,
)


def _niri_cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.testing_keywords = False
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = ["gui-wm/niri"]
    cfg.shell = "none"
    cfg.display_manager = "greetd"
    cfg.init_system = "openrc"
    cfg.overlays = []
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class OverlayKeywordOrderTests(unittest.TestCase):
    def test_required_overlays_include_guru_for_niri(self) -> None:
        self.assertIn("guru", _required_overlays(_niri_cfg()))

    def test_required_overlays_include_cachy(self) -> None:
        cfg = _niri_cfg(kernel="cachyos-kernel-bin", window_managers=["gui-wm/sway"])
        ovs = _required_overlays(cfg)
        self.assertIn("CachyOS-kernels", ovs)

    def test_helper_keyword_and_unmask_cover_eselect_and_git(self) -> None:
        cfg = _niri_cfg()
        kw = _overlay_helper_keyword_text(cfg)
        um = _overlay_helper_unmask_text()
        for atom in OVERLAY_HELPER_ATOMS:
            self.assertIn(atom, kw)
            self.assertIn(f"{atom} ~amd64", kw)
            self.assertIn(atom, um)

    def test_overlay_repo_lines_for_every_required_overlay(self) -> None:
        cfg = _niri_cfg(kernel="cachyos-kernel")
        ovs = _required_overlays(cfg)
        self.assertTrue(ovs)
        kw = _overlay_repo_keyword_text(cfg, ovs)
        um = _overlay_repo_unmask_text(cfg, ovs)
        for ov in ovs:
            self.assertIn(f"*/*::{ov} ~amd64", kw)
            self.assertIn(f"*/*::{ov}", um)

    def test_overlay_script_keywords_helpers_before_their_emerge(self) -> None:
        script = _overlay_script(_niri_cfg())
        self.assertTrue(script)
        helper_kw = script.find("package.accept_keywords/overlay-helpers")
        helper_um = script.find("package.unmask/overlay-helpers")
        emerge = script.find("emerge_one app-eselect/eselect-repository")
        self.assertGreaterEqual(helper_kw, 0)
        self.assertGreaterEqual(helper_um, 0)
        self.assertGreaterEqual(emerge, 0)
        self.assertLess(helper_kw, emerge, "accept_keywords for helpers must precede emerge")
        self.assertLess(helper_um, emerge, "package.unmask for helpers must precede emerge")
        # Explicit atoms in the keyword file body before emerge.
        for atom in OVERLAY_HELPER_ATOMS:
            pos = script.find(f"{atom} ~amd64")
            self.assertGreaterEqual(pos, 0)
            self.assertLess(pos, emerge)

    def test_overlay_script_keywords_repos_after_sync_before_script_ends(self) -> None:
        cfg = _niri_cfg(kernel="cachyos-kernel-bin")
        script = _overlay_script(cfg)
        sync_guru = script.find("emaint sync --repo guru")
        sync_cachy = script.find("emaint sync --repo CachyOS-kernels")
        kw_file = script.find("package.accept_keywords/overlays")
        um_file = script.find("package.unmask/overlays")
        self.assertGreaterEqual(sync_guru, 0)
        self.assertGreaterEqual(sync_cachy, 0)
        self.assertGreaterEqual(kw_file, 0)
        self.assertGreaterEqual(um_file, 0)
        last_sync = max(sync_guru, sync_cachy)
        self.assertLess(last_sync, kw_file)
        self.assertLess(last_sync, um_file)
        self.assertIn("*/*::guru ~amd64", script)
        self.assertIn("*/*::CachyOS-kernels ~amd64", script)
        self.assertIn("*/*::guru", script.split("package.unmask/overlays", 1)[1])

    def test_global_testing_keywords_still_writes_helper_files_on_host(self) -> None:
        """*/** testing empties installer keywords; overlay-helpers must still be written."""
        cfg = _niri_cfg(testing_keywords=True, kernel="cachyos-kernel")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "etc" / "portage").mkdir(parents=True)
            _write_overlay_portage_files(cfg, str(root))
            helpers_kw = (root / "etc/portage/package.accept_keywords/overlay-helpers").read_text()
            helpers_um = (root / "etc/portage/package.unmask/overlay-helpers").read_text()
            overlays_kw = (root / "etc/portage/package.accept_keywords/overlays").read_text()
            overlays_um = (root / "etc/portage/package.unmask/overlays").read_text()
        for atom in OVERLAY_HELPER_ATOMS:
            self.assertIn(f"{atom} ~amd64", helpers_kw)
            self.assertIn(atom, helpers_um)
        self.assertIn("*/*::guru ~amd64", overlays_kw)
        self.assertIn("*/*::CachyOS-kernels ~amd64", overlays_kw)
        self.assertIn("*/*::guru", overlays_um)
        self.assertIn("*/*::CachyOS-kernels", overlays_um)

    def test_write_make_conf_emits_overlay_portage_files(self) -> None:
        cfg = _niri_cfg()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "etc" / "portage").mkdir(parents=True)
            _write_make_conf(cfg, str(root))
            self.assertTrue((root / "etc/portage/package.accept_keywords/overlay-helpers").is_file())
            self.assertTrue((root / "etc/portage/package.unmask/overlay-helpers").is_file())
            self.assertTrue((root / "etc/portage/package.accept_keywords/overlays").is_file())
            self.assertTrue((root / "etc/portage/package.unmask/overlays").is_file())

    def test_chroot_script_embeds_helper_prep_before_emerge(self) -> None:
        script = render_chroot_script(
            _niri_cfg(),
            {"root": "/dev/vda2", "esp": "/dev/vda1"},
        )
        helper_kw = script.find("package.accept_keywords/overlay-helpers")
        emerge = script.find("emerge_one app-eselect/eselect-repository")
        self.assertGreaterEqual(helper_kw, 0)
        self.assertGreaterEqual(emerge, 0)
        self.assertLess(helper_kw, emerge)

    def test_no_overlays_yields_empty_script(self) -> None:
        cfg = InstallConfig()
        cfg.kernel = "gentoo-kernel-bin"
        cfg.window_managers = ["gui-wm/sway"]
        cfg.shell = "none"
        cfg.overlays = []
        self.assertEqual(_overlay_script(cfg), "")
        self.assertEqual(_required_overlays(cfg), [])


class GamesCategoryRemovedTests(unittest.TestCase):
    def test_catalog_has_no_games_category(self) -> None:
        from gentoo_installer import catalog

        keys = [key for key, _title, _pkgs in catalog.CATEGORIES]
        self.assertNotIn("games", keys)
        for _key, _title, pkgs in catalog.CATEGORIES:
            atoms = " ".join(atom for atom, _ in pkgs)
            self.assertNotIn("steam-launcher", atoms)
            self.assertNotIn("gamemode", atoms)

    def test_steam_not_featured_overlay(self) -> None:
        from gentoo_installer import catalog

        featured = {name for name, _ in catalog.FEATURED_OVERLAYS}
        self.assertNotIn("steam-overlay", featured)


if __name__ == "__main__":
    unittest.main()
