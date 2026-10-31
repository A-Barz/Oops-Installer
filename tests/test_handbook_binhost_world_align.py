"""OI-74: Handbook/wiki binhost + @world alignment (rev 84)."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _binrepos_conf_text,
    _features_line,
    _make_conf_text,
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
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class HandbookBinhostWorldAlignTests(unittest.TestCase):
    def test_rev_bumped(self) -> None:
        self.assertGreaterEqual(INSTALLER_REV, 84)

    def test_features_include_getbinpkg_and_signature_when_binhost(self) -> None:
        text = _make_conf_text(_cfg(binhost=True))
        self.assertIn("getbinpkg", text)
        self.assertIn("binpkg-request-signature", text)
        self.assertIn("--getbinpkg", text)
        self.assertIn("--usepkg", text)
        self.assertNotIn("binpkg-respect-use", text)
        line = _features_line(_cfg(binhost=True))
        self.assertIn("getbinpkg", line)
        self.assertIn("binpkg-request-signature", line)

    def test_features_omit_binhost_tokens_when_source_only(self) -> None:
        line = _features_line(_cfg(binhost=False))
        self.assertNotIn("getbinpkg", line)
        self.assertNotIn("binpkg-request-signature", line)
        text = _make_conf_text(_cfg(binhost=False))
        self.assertNotIn("--getbinpkg", text)

    def test_binrepos_x86_64_has_verify_and_location(self) -> None:
        body = _binrepos_conf_text(_cfg(binhost=True, binhost_abi="x86-64"))
        self.assertIn("verify-signature = true", body)
        self.assertIn("location = /var/cache/binhost/gentoo", body)
        self.assertIn("/binpackages/23.0/x86-64/", body)
        self.assertNotIn("x86-64-v3", body)

    def test_binrepos_v3_writes_both_uris(self) -> None:
        body = _binrepos_conf_text(_cfg(binhost=True, binhost_abi="x86-64-v3"))
        self.assertIn("/binpackages/23.0/x86-64/", body)
        self.assertIn("/binpackages/23.0/x86-64-v3/", body)
        self.assertIn("[binhost-x86-64-v3]", body)
        self.assertIn("location = /var/cache/binhost/gentoo-x86-64-v3", body)
        self.assertEqual(body.count("verify-signature = true"), 2)

    def test_chroot_runs_getuto_and_embeds_binrepos(self) -> None:
        script = render_chroot_script(
            _cfg(binhost=True, binhost_abi="x86-64-v3"),
            {"root": "/dev/vda2", "esp": "/dev/vda1"},
        )
        self.assertIn("getuto", script)
        self.assertIn("verify-signature = true", script)
        self.assertIn("x86-64-v3", script)
        self.assertIn("dropping v3 binhost (keep x86-64)", script)

    def test_world_newuse_with_binhost(self) -> None:
        script = render_chroot_script(_cfg(binhost=True), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn(
            "emerge --verbose --update --deep --newuse --keep-going "
            "$RESPECT_USE $NR @world",
            script,
        )
        self.assertIn(
            "emerge --verbose --update --deep --newuse --keep-going "
            "--usepkg=n --getbinpkg=n @world",
            script,
        )

    def test_world_changed_use_without_binhost(self) -> None:
        script = render_chroot_script(
            _cfg(binhost=False),
            {"root": "/dev/vda2", "esp": "/dev/vda1"},
        )
        self.assertIn(
            "emerge --verbose --update --deep --changed-use --keep-going "
            "$RESPECT_USE $NR @world",
            script,
        )
        self.assertIn(
            "emerge --verbose --update --deep --changed-use --keep-going "
            "--usepkg=n --getbinpkg=n @world",
            script,
        )
        self.assertNotIn(
            "emerge --verbose --update --deep --newuse --keep-going "
            "$RESPECT_USE $NR @world",
            script,
        )


if __name__ == "__main__":
    unittest.main()
