"""Default and catalog atoms must be keyworded / unmasked before emerge."""

from __future__ import annotations

import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _atoms_to_unmask,
    _keyword_text,
    _package_license_lines,
    _unmask_text,
    ensure_boot_to_greeter,
    render_chroot_script,
)


def _typical_niri(**kwargs) -> InstallConfig:
    """Default-ish desktop path: OpenRC, binhost, niri, greetd, handbook tools."""
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.init_system = "openrc"
    cfg.profile = "default/linux/amd64/23.0/desktop"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = ["gui-wm/niri"]
    cfg.shell = "none"
    cfg.display_manager = "greetd"
    cfg.pipewire = True
    cfg.binhost = True
    cfg.testing_keywords = False
    cfg.firmware = "uefi"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.username = "ashley"
    cfg.network_stack = "networkmanager"
    cfg.wireless = True
    cfg.chrony = True
    cfg.sshd = True
    cfg.zram = True
    cfg.nftables = True
    cfg.linux_firmware = True
    cfg.gentoo_mirrors = ""  # triggers mirrorselect
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    ensure_boot_to_greeter(cfg)
    return cfg


class DefaultSelectionKeywordTests(unittest.TestCase):
    """Typical wizard defaults land in accept_keywords + package.unmask."""

    DEFAULT_ATOMS = [
        "gui-wm/niri",
        "gui-libs/greetd",
        "gui-apps/gtkgreet",
        "gui-wm/labwc",
        "acct-user/greetd",
        "acct-group/greetd",
        "gui-libs/display-manager-init",
        "sys-kernel/gentoo-kernel-bin",
        "net-misc/networkmanager",
        "sys-apps/dbus",
        "sys-auth/elogind",
        "sys-block/zram-init",
        "sys-boot/grub",
        "sys-boot/efibootmgr",
        "sys-kernel/linux-firmware",
        "net-firewall/nftables",
        "app-portage/cpuid2cpuflags",
        "app-portage/mirrorselect",
        "app-eselect/eselect-repository",
        "dev-vcs/git",
        "sys-apps/portage",
    ]

    def test_default_atoms_are_planned_keyworded_and_unmasked(self) -> None:
        cfg = _typical_niri()
        planned = set(_atoms_to_unmask(cfg))
        keywords = _keyword_text(cfg)
        unmask = _unmask_text(cfg)
        missing_plan: list[str] = []
        missing_kw: list[str] = []
        missing_un: list[str] = []
        for atom in self.DEFAULT_ATOMS:
            if atom not in planned:
                missing_plan.append(atom)
            if f"{atom} ~amd64" not in keywords:
                missing_kw.append(atom)
            if atom not in unmask.splitlines():
                missing_un.append(atom)
        self.assertEqual(missing_plan, [], msg=f"not in plan: {missing_plan}")
        self.assertEqual(missing_kw, [], msg=f"not keyworded: {missing_kw}")
        self.assertEqual(missing_un, [], msg=f"not unmasked: {missing_un}")

    def test_chrome_license_only_when_selected(self) -> None:
        bare = _typical_niri(browser="none")
        self.assertFalse(
            any(line.startswith("www-client/google-chrome") for line in _package_license_lines(bare))
        )
        cfg = _typical_niri(browser="chrome", accept_license="@FREE @BINARY-REDISTRIBUTABLE *")
        lines = _package_license_lines(cfg)
        self.assertTrue(any(line.startswith("www-client/google-chrome") for line in lines))
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("package.license/apps", script)
        self.assertIn("www-client/google-chrome google-chrome", script)
        self.assertIn("*", cfg.accept_license)

    def test_soft_default_firefox_is_keyworded_when_selected(self) -> None:
        cfg = _typical_niri(browser="firefox-esr")
        planned = set(_atoms_to_unmask(cfg))
        self.assertIn("www-client/firefox-bin:esr", planned)
        self.assertNotIn("www-client/google-chrome", planned)
        keywords = _keyword_text(cfg)
        self.assertIn("www-client/firefox-bin:esr ~amd64", keywords)

    def test_selected_proprietary_editors_get_license_lines(self) -> None:
        cfg = _typical_niri(
            selected_packages=[
                "app-editors/sublime-text",
                "app-editors/vscode",
                "app-editors/logseq-desktop-bin",
            ]
        )
        lines = "\n".join(_package_license_lines(cfg))
        self.assertIn("app-editors/sublime-text *", lines)
        self.assertIn("app-editors/vscode *", lines)
        self.assertIn("app-editors/logseq-desktop-bin *", lines)


class OfferableAtomKeywordTests(unittest.TestCase):
    """Every catalog Portage atom is in the plan when that atom is selected."""

    def test_each_offerable_atom_keyworded_when_selected(self) -> None:
        failures: list[str] = []
        for atom in catalog.offerable_portage_atoms():
            cfg = InstallConfig()
            cfg.architecture = "amd64"
            cfg.testing_keywords = False
            cfg.kernel = "gentoo-kernel-bin"
            cfg.username = "ashley"
            cfg.binhost = True
            cfg.gentoo_mirrors = "https://distfiles.gentoo.org/"  # skip mirrorselect noise
            bare = atom.split("::", 1)[0]
            if bare.startswith("sys-kernel/") and bare in {
                catalog.kernel_atom(name) for name, _ in catalog.kernel_menu_choices()
            } or bare in {
                "sys-kernel/gentoo-kernel-bin",
                "sys-kernel/gentoo-kernel",
                "sys-kernel/gentoo-sources",
                "sys-kernel/cachyos-kernel",
                "sys-kernel/cachyos-kernel-bin",
            }:
                # Kernel menu ids map through kernel_atom; pick matching menu name.
                for name, _desc in catalog.kernel_menu_choices():
                    if catalog.kernel_atom(name) == bare:
                        cfg.kernel = name
                        break
                else:
                    cfg.kernel = bare.rsplit("/", 1)[-1]
            elif bare.startswith("gui-wm/") or bare.startswith("x11-wm/") or bare == "dev-libs/weston":
                cfg.window_managers = [bare]
                cfg.display_manager = "greetd"
            else:
                cfg.window_managers = ["gui-wm/sway"]
                cfg.display_manager = "greetd"
                cfg.selected_packages = [bare]
                cfg.extra_packages = [bare]
            if bare in catalog.nonfree_codec_atoms():
                cfg.nonfree_codecs = True
            ensure_boot_to_greeter(cfg)
            planned = set(_atoms_to_unmask(cfg))
            keywords = _keyword_text(cfg)
            if bare not in planned:
                failures.append(f"{bare}: missing from _atoms_to_unmask")
                continue
            if catalog.kernel_uses_stable_lts(cfg.kernel) and bare == "sys-kernel/gentoo-sources":
                if f"{bare} -~amd64" not in keywords:
                    failures.append(f"{bare}: LTS pin missing from keywords")
            elif f"{bare} ~amd64" not in keywords:
                failures.append(f"{bare}: missing ~amd64 keyword line")
            if bare not in _unmask_text(cfg).splitlines():
                failures.append(f"{bare}: missing from package.unmask")
        self.assertEqual(failures, [], msg="\n".join(failures[:40]))

    def test_systemd_networkmanager_keywords_dbus(self) -> None:
        cfg = _typical_niri(init_system="systemd", profile="default/linux/amd64/23.0/desktop/systemd")
        planned = set(_atoms_to_unmask(cfg))
        self.assertIn("sys-apps/dbus", planned)
        self.assertIn("sys-apps/dbus ~amd64", _keyword_text(cfg))

    def test_systemd_networkd_keywords_systemd(self) -> None:
        cfg = _typical_niri(
            init_system="systemd",
            profile="default/linux/amd64/23.0/desktop/systemd",
            network_stack="systemd-networkd",
        )
        planned = set(_atoms_to_unmask(cfg))
        self.assertIn("sys-apps/systemd", planned)
        self.assertIn("sys-apps/systemd ~amd64", _keyword_text(cfg))

    def test_hyprland_dotfiles_keyword_curl(self) -> None:
        cfg = _typical_niri(
            window_managers=["gui-wm/hyprland"],
            wm_dotfiles=["gui-wm/hyprland=end-4"],
            gentoo_mirrors="https://distfiles.gentoo.org/",
        )
        planned = set(_atoms_to_unmask(cfg))
        self.assertIn("net-misc/curl", planned)
        self.assertIn("gui-libs/xdg-desktop-portal-hyprland", planned)
        self.assertNotIn("gui-apps/xdg-desktop-portal-hyprland", planned)


if __name__ == "__main__":
    unittest.main()
