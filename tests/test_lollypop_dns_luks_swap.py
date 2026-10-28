"""Lollypop catalog row, DNS presets, guru LUKS/swap notes."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from gentoo_installer import catalog, ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _dns_nameservers,
    _resolv_conf_text,
    _write_dns_config,
    render_chroot_script,
)
from gentoo_installer.wizard import _ask_dns


class LollypopCatalogTests(unittest.TestCase):
    def test_lollypop_in_audio_video_category(self) -> None:
        rows = next(pkgs for key, _t, pkgs in catalog.CATEGORIES if key == "audio-video")
        atoms = [atom for atom, _ in rows]
        self.assertIn("media-sound/lollypop", atoms)
        self.assertLess(atoms.index("media-sound/lollypop"), atoms.index("media-gfx/gimp"))


class DnsPresetTests(unittest.TestCase):
    def test_presets_google_cloudflare_opendns(self) -> None:
        self.assertEqual(catalog.dns_servers_for("google"), ["8.8.8.8", "8.8.4.4"])
        self.assertEqual(catalog.dns_servers_for("cloudflare"), ["1.1.1.1", "1.0.0.1"])
        self.assertEqual(
            catalog.dns_servers_for("opendns"),
            ["208.67.222.222", "208.67.220.220"],
        )

    def test_custom_splits_ips(self) -> None:
        self.assertEqual(
            catalog.dns_servers_for("custom", "9.9.9.9 149.112.112.112"),
            ["9.9.9.9", "149.112.112.112"],
        )

    def test_ask_dns_sets_provider_and_servers(self) -> None:
        cfg = InstallConfig()
        answers = iter(["google"])

        def fake_menu(title, choices, default=None):  # noqa: ARG001
            return next(answers)

        with mock.patch("gentoo_installer.wizard.ui.menu", side_effect=fake_menu):
            with mock.patch("gentoo_installer.wizard.ui.ok"):
                with mock.patch("gentoo_installer.wizard.ui.guru_note"):
                    with mock.patch("gentoo_installer.wizard.ui.paint", side_effect=lambda t, *a: t):
                        _ask_dns(cfg)
        self.assertEqual(cfg.dns_provider, "google")
        self.assertEqual(cfg.dns_servers, "8.8.8.8 8.8.4.4")
        self.assertEqual(cfg.static_dns, "8.8.8.8")

    def test_write_dns_config_resolv_and_nm(self) -> None:
        cfg = InstallConfig()
        cfg.dns_provider = "opendns"
        cfg.dns_servers = "208.67.222.222 208.67.220.220"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "etc").mkdir()
            _write_dns_config(cfg, root, keep_existing=False)
            resolv = (root / "etc/resolv.conf").read_text()
            self.assertIn("nameserver 208.67.222.222", resolv)
            self.assertIn("nameserver 208.67.220.220", resolv)
            nm = (root / "etc/NetworkManager/conf.d/90-oops-dns-none.conf").read_text()
            self.assertIn("dns=none", nm)

    def test_netifrc_chroot_embeds_dns_servers(self) -> None:
        cfg = InstallConfig()
        cfg.network_stack = "netifrc"
        cfg.dns_provider = "google"
        cfg.dns_servers = "8.8.8.8 8.8.4.4"
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn('dns_servers_', script)
        self.assertIn("8.8.8.8 8.8.4.4", script)

    def test_resolv_text_lists_all_nameservers(self) -> None:
        cfg = InstallConfig()
        cfg.dns_provider = "cloudflare"
        text = _resolv_conf_text(cfg)
        self.assertIn("nameserver 1.1.1.1", text)
        self.assertIn("nameserver 1.0.0.1", text)
        self.assertEqual(_dns_nameservers(cfg), ["1.1.1.1", "1.0.0.1"])


class GuruLuksSwapNoteTests(unittest.TestCase):
    def test_guru_note_prints_purple_label(self) -> None:
        os.environ["FORCE_COLOR"] = "1"
        os.environ.pop("NO_COLOR", None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.guru_note("Encrypting root keeps the disk unreadable if stolen.")
        out = buf.getvalue()
        self.assertIn("guru", out)
        self.assertIn(ui.C.PURPLE, out)
        self.assertIn("unreadable", out)

    def test_disks_source_has_luks_and_swap_guru_reasons(self) -> None:
        import inspect
        from gentoo_installer import wizard as wiz

        src = inspect.getsource(wiz._disks)
        self.assertIn("ui.guru_note", src)
        self.assertIn("Encrypting root", src)
        self.assertIn("Unencrypted swap can leak", src)
        self.assertIn("32 GiB RAM or more", src)
        self.assertIn("lower-spec", src)


if __name__ == "__main__":
    unittest.main()
