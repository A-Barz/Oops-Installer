"""OI-73: tip-tree shell / Hyprland portal atoms must match GURU + hyproverlay."""

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
        self.assertGreaterEqual(INSTALLER_REV, 83)

    def test_noctalia_uses_tip_guru_atom_and_exec(self) -> None:
        self.assertEqual(catalog.shell_atoms("noctalia"), ["gui-apps/noctalia"])
        self.assertEqual(catalog.shell_exec("noctalia"), "noctalia")
        self.assertIn(("noctalia", "Noctalia shell"), catalog.SHELL_CHOICES)
        planned = set(_atoms_to_unmask(_cfg(shell="noctalia", window_managers=["gui-wm/labwc"])))
        self.assertIn("gui-apps/noctalia", planned)
        self.assertIn("gui-apps/noctalia-greeter", planned)
        self.assertNotIn("gui-apps/noctalia-shell", planned)

    def test_dms_hidden_and_old_shell_atoms_blocked(self) -> None:
        choice_ids = {key for key, _ in catalog.SHELL_CHOICES}
        self.assertNotIn("dms", choice_ids)
        self.assertNotIn("dms", catalog.SHELL_PACKAGES)
        self.assertEqual(catalog.shell_atoms("dms"), [])
        for dead in ("gui-apps/noctalia-shell", "gui-apps/dankmaterialshell"):
            self.assertIn(dead, catalog.REMOVED_GURU_ATOMS)
            self.assertNotIn(dead, catalog.offered_guru_atoms("noctalia"))
            self.assertNotIn(dead, catalog.offerable_portage_atoms())

    def test_hyprland_portal_is_gui_libs(self) -> None:
        reqs = [pkg.atom for pkg in catalog.WM_REQUIREMENTS["gui-wm/hyprland"]]
        self.assertIn("gui-libs/xdg-desktop-portal-hyprland", reqs)
        self.assertNotIn("gui-apps/xdg-desktop-portal-hyprland", reqs)
        planned = set(_atoms_to_unmask(_cfg(window_managers=["gui-wm/hyprland"])))
        self.assertIn("gui-libs/xdg-desktop-portal-hyprland", planned)
        self.assertNotIn("gui-apps/xdg-desktop-portal-hyprland", planned)

    def test_legacy_dms_shell_does_not_queue_dead_atom(self) -> None:
        # Saved profiles may still say shell=dms; must not hard-fail emerge.
        cfg = _cfg(shell="dms", window_managers=["gui-wm/sway"])
        planned = set(_atoms_to_unmask(cfg))
        self.assertNotIn("gui-apps/dankmaterialshell", planned)
        # Unknown shell alone no longer forces greeter; Wayland WMs still do.
        self.assertFalse(catalog.needs_login_greeter([], "dms"))
        self.assertTrue(catalog.needs_login_greeter(cfg.window_managers, cfg.shell))

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

    def test_extra_compositors_plymouth_flatpak_still_offered(self) -> None:
        # Preference: do not strip Extra Compositors / DE metas / Plymouth / Flatpak.
        wm_atoms = {pkg.atom for pkg in catalog.WAYLAND_COMPOSITORS}
        self.assertIn("gui-wm/wayfire", wm_atoms)
        self.assertIn("gui-wm/gamescope", wm_atoms)
        self.assertTrue(catalog.PLYMOUTH_THEME_PACKAGES)
        # Flatpak apps remain as catalogue rows (pseudo-atoms).
        flatpak_rows = []
        for _key, _title, rows in catalog.CATEGORIES:
            flatpak_rows.extend(atom for atom, _ in rows if atom.startswith("flatpak-app/"))
        self.assertTrue(flatpak_rows)
        self.assertIn("sys-apps/flatpak", catalog.offerable_portage_atoms())
        self.assertIn("net-misc/chrony", catalog.offerable_portage_atoms())


if __name__ == "__main__":
    unittest.main()
