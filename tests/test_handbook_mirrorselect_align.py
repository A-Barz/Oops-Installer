"""OI-82: Handbook wiki mirrorselect alignment (rev 87)."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    GENTOO_MIRROR_BASES,
    _binrepos_conf_text,
    _default_gentoo_mirrors,
    _make_conf_text,
    _mirrorselect_block,
    render_chroot_script,
)
from gentoo_installer.rev import INSTALLER_REV


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "webrsync"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.locale = "en_US.UTF-8"
    cfg.dry_run = True
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.gentoo_mirrors = ""
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class HandbookMirrorselectAlignTests(unittest.TestCase):
    def test_rev_bumped(self) -> None:
        self.assertGreaterEqual(INSTALLER_REV, 87)

    def test_default_mirrors_join_bases_distfiles_first(self) -> None:
        value = _default_gentoo_mirrors()
        self.assertTrue(value.startswith("https://distfiles.gentoo.org"))
        for base in GENTOO_MIRROR_BASES:
            self.assertIn(base, value)
        self.assertEqual(value, " ".join(GENTOO_MIRROR_BASES))

    def test_make_conf_always_has_gentoo_mirrors_when_empty(self) -> None:
        text = _make_conf_text(_cfg(gentoo_mirrors=""))
        self.assertIn('GENTOO_MIRRORS="', text)
        self.assertIn("https://distfiles.gentoo.org", text)
        self.assertIn("https://gentoo.osuosl.org", text)

    def test_make_conf_keeps_user_mirrors(self) -> None:
        text = _make_conf_text(_cfg(gentoo_mirrors="https://example.mirror/gentoo"))
        self.assertIn('GENTOO_MIRRORS="https://example.mirror/gentoo"', text)
        self.assertNotIn("gentoo.osuosl.org", text)

    def test_mirrorselect_block_uses_deep_flag_and_quiet_fallback(self) -> None:
        block = _mirrorselect_block(_cfg(gentoo_mirrors=""))
        self.assertIn("mirrorselect -s3 -b10 -D -o", block)
        self.assertIn("2>/dev/null", block)
        self.assertIn(">> mirrorselect failed; using curated GENTOO_MIRRORS", block)
        self.assertNotIn("Could not find any mirrors", block)
        # Replace-on-success, never append a second GENTOO_MIRRORS via >>
        self.assertIn('sed -i "s#^GENTOO_MIRRORS=', block)

    def test_mirrorselect_block_skipped_when_user_set_mirrors(self) -> None:
        block = _mirrorselect_block(_cfg(gentoo_mirrors="https://distfiles.gentoo.org"))
        self.assertEqual(block, "")

    def test_chroot_runs_mirrorselect_before_overlays_and_world(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        deep = script.index("mirrorselect -s3 -b10 -D -o")
        world = script.index("echo \">> emerge @world\"")
        self.assertLess(deep, world)
        # Earlier than the oneshot reason string (after sync/profile) or overlays marker.
        self.assertIn("deep probe; curated fallback ready", script)
        oneshot = script.index("after sync/profile (Handbook final tree)")
        self.assertLess(deep, oneshot)

    def test_binrepos_still_handbook_paths(self) -> None:
        body = _binrepos_conf_text(_cfg(binhost=True, binhost_abi="x86-64"))
        self.assertIn("/binpackages/23.0/x86-64/", body)
        self.assertIn("verify-signature = true", body)
        self.assertIn("distfiles.gentoo.org", body)


if __name__ == "__main__":
    unittest.main()
