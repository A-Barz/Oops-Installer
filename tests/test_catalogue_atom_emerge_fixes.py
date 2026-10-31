"""OI-73 / OI-79: Hyprland portal atoms; Portage shells removed; extras kept."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import _atoms_to_unmask, _overlay_script, render_chroot_script
from gentoo_installer.rev import INSTALLER_REV


def _cfg(**kwargs) -> InstallConfig:
    cfg = InstallConfig()
    cfg.username = "ashley"
    cfg.target_disk = "/dev/vda"
    cfg.architecture = "amd64"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.dry_run = True
    cfg.network_stack = "networkmanager"
    cfg.pipewire = True
    cfg.shell = "none"
    cfg.display_manager = "greetd"
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class CatalogueAtomEmergeFixesTests(unittest.TestCase):
    def test_rev_bumped(self) -> None:
        self.assertGreaterEqual(INSTALLER_REV, 86)

    def test_portage_shells_removed(self) -> None:
        choice_ids = {key for key, _ in catalog.SHELL_CHOICES}
        self.assertEqual(choice_ids, {"none"})
        self.assertEqual(catalog.SHELL_PACKAGES, {})
        self.assertEqual(catalog.shell_atoms("noctalia"), [])
        self.assertEqual(catalog.shell_exec("noctalia"), "")
        self.assertNotIn("dms", choice_ids)
        for dead in (
            "gui-apps/noctalia",
            "gui-apps/noctalia-greeter",
            "gui-apps/noctalia-shell",
            "gui-apps/dankmaterialshell",
        ):
            self.assertIn(dead, catalog.REMOVED_GURU_ATOMS)
            self.assertNotIn(dead, catalog.offered_guru_atoms("none"))
            self.assertNotIn(dead, catalog.offered_guru_atoms("noctalia"))
            self.assertNotIn(dead, catalog.offerable_portage_atoms())

    def test_legacy_shell_profile_does_not_emerge_shell_atoms(self) -> None:
        for shell in ("noctalia", "dms"):
            cfg = _cfg(shell=shell, window_managers=["gui-wm/sway"])
            planned = set(_atoms_to_unmask(cfg))
            self.assertNotIn("gui-apps/noctalia", planned)
            self.assertNotIn("gui-apps/noctalia-greeter", planned)
            self.assertNotIn("gui-apps/dankmaterialshell", planned)

    def test_hyprland_portal_is_gui_libs(self) -> None:
        reqs = [pkg.atom for pkg in catalog.WM_REQUIREMENTS["gui-wm/hyprland"]]
        self.assertIn("gui-libs/xdg-desktop-portal-hyprland", reqs)
        self.assertNotIn("gui-apps/xdg-desktop-portal-hyprland", reqs)
        planned = set(_atoms_to_unmask(_cfg(window_managers=["gui-wm/hyprland"])))
        self.assertIn("gui-libs/xdg-desktop-portal-hyprland", planned)
        self.assertNotIn("gui-apps/xdg-desktop-portal-hyprland", planned)

    def test_eselect_repository_uses_emerge_one(self) -> None:
        script = _overlay_script(_cfg(window_managers=["gui-wm/niri"]))
        self.assertIn("emerge_one app-eselect/eselect-repository", script)
        self.assertNotIn(
            "emerge --ask=n --verbose --noreplace app-eselect/eselect-repository",
            script,
        )
        chroot = render_chroot_script(
            _cfg(window_managers=["gui-wm/niri"]),
            {"root": "/dev/vda2", "esp": "/dev/vda1"},
        )
        self.assertIn("emerge_one app-eselect/eselect-repository", chroot)

    def test_extra_compositors_plymouth_flatpak_chrony_still_offered(self) -> None:
        # Preference: do not strip Extra Compositors / DE metas / Plymouth / Flatpak / chrony.
        wm_atoms = {pkg.atom for pkg in catalog.WAYLAND_COMPOSITORS}
        self.assertIn("gui-wm/wayfire", wm_atoms)
        self.assertIn("gui-wm/gamescope", wm_atoms)
        self.assertTrue(catalog.PLYMOUTH_THEME_PACKAGES)
        flatpak_rows = []
        for _key, _title, rows in catalog.CATEGORIES:
            flatpak_rows.extend(atom for atom, _ in rows if atom.startswith("flatpak-app/"))
        self.assertTrue(flatpak_rows)
        self.assertIn("sys-apps/flatpak", catalog.offerable_portage_atoms())
        self.assertIn("net-misc/chrony", catalog.offerable_portage_atoms())


if __name__ == "__main__":
    unittest.main()
