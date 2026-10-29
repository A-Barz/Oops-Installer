"""M9 / OI-48: catalogue ufw vs default nftables — one firewall stack."""

from __future__ import annotations

import inspect
import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.wizard import _apply_firewall_exclusion, _packages_locked_from_earlier_steps


class FirewallCatalogueTests(unittest.TestCase):
    def test_descriptions_mention_exclusion(self) -> None:
        networking = next(pkgs for key, _title, pkgs in catalog.CATEGORIES if key == "networking")
        by_atom = dict(networking)
        self.assertIn(catalog.UFW_ATOM, by_atom)
        self.assertIn(catalog.NFTABLES_ATOM, by_atom)
        self.assertIn("exclusive", by_atom[catalog.UFW_ATOM].lower())
        self.assertIn("exclusive", by_atom[catalog.NFTABLES_ATOM].lower())

    def test_hidden_when_nftables_enabled(self) -> None:
        hidden = catalog.firewall_catalogue_hidden(True)
        self.assertEqual(hidden, {catalog.UFW_ATOM, catalog.NFTABLES_ATOM})
        self.assertEqual(catalog.firewall_catalogue_hidden(False), set())

    def test_locked_includes_ufw_when_nftables(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = True
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn(catalog.UFW_ATOM, locked)
        self.assertIn(catalog.NFTABLES_ATOM, locked)

    def test_locked_allows_ufw_when_nftables_off(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = False
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertNotIn(catalog.UFW_ATOM, locked)


class FirewallEnforceTests(unittest.TestCase):
    def test_strips_ufw_when_nftables_on(self) -> None:
        nft, selected, extra, warnings = catalog.enforce_firewall_mutual_exclusion(
            True,
            ["net-firewall/ufw", "app-misc/jq"],
            ["net-firewall/ufw", "net-firewall/nftables"],
        )
        self.assertTrue(nft)
        self.assertEqual(selected, ["app-misc/jq"])
        self.assertEqual(extra, [])
        self.assertTrue(any("ufw" in w for w in warnings))

    def test_ufw_wins_over_catalogue_nftables_when_flag_off(self) -> None:
        nft, selected, extra, warnings = catalog.enforce_firewall_mutual_exclusion(
            False,
            ["net-firewall/ufw", "net-firewall/nftables"],
            [],
        )
        self.assertFalse(nft)
        self.assertEqual(selected, ["net-firewall/ufw"])
        self.assertEqual(extra, [])
        self.assertTrue(any("nftables" in w for w in warnings))

    def test_apply_updates_config(self) -> None:
        cfg = InstallConfig()
        cfg.nftables = True
        cfg.selected_packages = ["net-firewall/ufw", "media-sound/lollypop"]
        cfg.extra_packages = ["net-firewall/ufw", "gui-wm/niri"]
        _apply_firewall_exclusion(cfg)
        self.assertTrue(cfg.nftables)
        self.assertNotIn(catalog.UFW_ATOM, cfg.selected_packages)
        self.assertNotIn(catalog.UFW_ATOM, cfg.extra_packages)
        self.assertIn("media-sound/lollypop", cfg.selected_packages)
        self.assertIn("gui-wm/niri", cfg.extra_packages)

    def test_wizard_documents_exclusion(self) -> None:
        from gentoo_installer import wizard

        src = inspect.getsource(wizard._tools)
        self.assertIn("One firewall stack", src)
        self.assertIn("net-firewall/ufw", src)


if __name__ == "__main__":
    unittest.main()
