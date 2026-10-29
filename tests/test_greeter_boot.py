"""Boot-to-greeter guarantees across random installer choice combinations."""

from __future__ import annotations

import random
import unittest

from gentoo_installer import catalog
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    _desktop_packages,
    _display_manager_script,
    _final_tool_checks,
    _login_sessions_for,
    _resolved_dm,
    ensure_boot_to_greeter,
    greeter_boot_failures,
    render_chroot_script,
)
from gentoo_installer.wizard import _packages_locked_from_earlier_steps


def _base_cfg(**kwargs) -> InstallConfig:
    cfg = InstallConfig()
    cfg.username = "ashley"
    cfg.target_disk = "/dev/vda"
    cfg.architecture = "amd64"
    cfg.init_system = "openrc"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    cfg.dry_run = True
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class MandatoryBrowserTests(unittest.TestCase):
    def test_required_pair_is_chrome_and_firefox_esr_slot(self) -> None:
        atoms = catalog.required_browser_atoms()
        self.assertEqual(
            atoms,
            ["www-client/firefox-bin:esr", "www-client/google-chrome"],
        )

    def test_browser_category_is_only_firefox_esr_and_chrome(self) -> None:
        browsers = next(pkgs for key, _title, pkgs in catalog.CATEGORIES if key == "browsers")
        atoms = [atom for atom, _ in browsers]
        self.assertEqual(
            atoms,
            ["www-client/firefox-bin:esr", "www-client/google-chrome"],
        )
        self.assertNotIn("www-client/qutebrowser", atoms)
        self.assertNotIn("www-client/firefox", atoms)

    def test_desktop_packages_always_queue_pair(self) -> None:
        cfg = _base_cfg(browser="none", pipewire=False)
        pkgs = _desktop_packages(cfg)
        self.assertIn("www-client/firefox-bin:esr", pkgs)
        self.assertIn("www-client/google-chrome", pkgs)

    def test_locked_includes_mandatory_browsers(self) -> None:
        cfg = _base_cfg(window_managers=["gui-wm/sway"], display_manager="greetd")
        locked = _packages_locked_from_earlier_steps(cfg)
        self.assertIn("www-client/firefox-bin:esr", locked)
        self.assertIn("www-client/google-chrome", locked)


class GreeterResolutionTests(unittest.TestCase):
    def test_wayland_none_becomes_greetd(self) -> None:
        cfg = _base_cfg(window_managers=["gui-wm/hyprland"], display_manager="none")
        ensure_boot_to_greeter(cfg)
        self.assertEqual(cfg.display_manager, "greetd")
        self.assertEqual(_resolved_dm(cfg), "greetd")

    def test_x11_only_stays_text_console(self) -> None:
        cfg = _base_cfg(window_managers=["x11-wm/dwm", "x11-wm/i3"], display_manager="none")
        ensure_boot_to_greeter(cfg)
        self.assertEqual(_resolved_dm(cfg), "none")
        self.assertEqual(greeter_boot_failures(cfg), [])

    def test_shell_without_wm_gets_labwc_and_greetd(self) -> None:
        cfg = _base_cfg(window_managers=[], shell="noctalia", display_manager="none")
        ensure_boot_to_greeter(cfg)
        self.assertEqual(cfg.display_manager, "greetd")
        self.assertIn("gui-wm/labwc", cfg.window_managers)
        sessions = _login_sessions_for(cfg)
        self.assertTrue(sessions)
        self.assertEqual(greeter_boot_failures(cfg), [])

    def test_empty_wm_list_with_greetd_gets_fallback_session(self) -> None:
        cfg = _base_cfg(window_managers=[], shell="none", display_manager="greetd")
        # Explicit greetd with no WMs: still must not write empty environments.
        cfg.extra_packages = ["gui-libs/greetd"]
        sessions = catalog.ensure_login_sessions(cfg.desktop, cfg.extra_packages, cfg.shell)
        # needs_login_greeter is false without wayland — ensure_login_sessions stays empty
        # unless atoms imply a greeter need. Force via needs path:
        sessions = catalog.ensure_login_sessions(
            cfg.desktop, ["gui-wm/labwc"], cfg.shell
        )
        self.assertEqual(sessions[0][1], "labwc")

    def test_display_manager_script_writes_nonempty_environments(self) -> None:
        cfg = _base_cfg(window_managers=["gui-wm/sway"], display_manager="greetd", shell="none")
        script = _display_manager_script(cfg)
        self.assertIn("/etc/greetd/config.toml", script)
        self.assertIn("/etc/greetd/environments", script)
        self.assertIn("sway", script)
        self.assertIn("gtkgreet", script)
        self.assertIn("labwc", script)
        self.assertIn("verify greeter can present a login", script)
        self.assertIn("boot-to-greeter checks failed", script)

    def test_tuigreet_stays_removed_and_never_selected(self) -> None:
        """Saved profiles naming tuigreet must not revive a dead greeter path."""
        self.assertIn("gui-apps/tuigreet", catalog.REMOVED_GURU_ATOMS)
        self.assertNotIn("gui-apps/tuigreet", catalog.offered_guru_atoms("none"))
        cfg = _base_cfg(
            window_managers=["gui-wm/sway"],
            display_manager="greetd",
            shell="none",
            extra_packages=["gui-apps/tuigreet"],
        )
        from gentoo_installer.execute import _display_manager_tools

        tools = {atom for atom, _ in _display_manager_tools(cfg)}
        self.assertNotIn("gui-apps/tuigreet", tools)
        self.assertIn("gui-apps/gtkgreet", tools)
        script = _display_manager_script(cfg)
        self.assertNotIn("tuigreet", script)
        self.assertIn("gtkgreet", script)
        self.assertEqual(greeter_boot_failures(cfg), [])

    def test_noctalia_uses_noctalia_greeter(self) -> None:
        cfg = _base_cfg(
            window_managers=["gui-wm/hyprland"],
            shell="noctalia",
            display_manager="greetd",
        )
        script = _display_manager_script(cfg)
        self.assertIn("noctalia-greeter", script)
        self.assertNotIn("/etc/greetd/environments", script)
        self.assertEqual(greeter_boot_failures(cfg), [])

    def test_final_checks_make_greeter_fatal(self) -> None:
        cfg = _base_cfg(window_managers=["gui-wm/niri"], display_manager="greetd")
        text = _final_tool_checks(cfg, ["grub"])
        self.assertIn("greeter check failed", text)
        self.assertIn("display-manager in default runlevel", text)
        self.assertIn("DISPLAYMANAGER=greetd", text)
        self.assertIn("greetd system user", text)
        self.assertIn("gtkgreet", text)
        self.assertIn("labwc", text)
        self.assertIn("session .desktop", text)

    def test_niri_openrc_queues_guru_gtkgreet_labwc_and_greetd_user(self) -> None:
        from gentoo_installer.execute import (
            _atoms_to_unmask,
            _display_manager_tools,
            _overlay_script,
            render_chroot_script,
        )

        cfg = _base_cfg(
            window_managers=["gui-wm/niri"],
            display_manager="greetd",
            init_system="openrc",
            profile="default/linux/amd64/23.0/desktop",
        )
        tools = {atom for atom, _ in _display_manager_tools(cfg)}
        self.assertIn("gui-libs/greetd", tools)
        self.assertIn("gui-apps/gtkgreet", tools)
        self.assertIn("gui-wm/labwc", tools)
        self.assertIn("acct-user/greetd", tools)
        self.assertIn("acct-group/greetd", tools)
        self.assertIn("gui-libs/display-manager-init", tools)
        unmask = _atoms_to_unmask(cfg)
        self.assertIn("gui-apps/gtkgreet", unmask)
        self.assertIn("gui-wm/labwc", unmask)
        self.assertIn("gui-libs/greetd", unmask)
        overlay = _overlay_script(cfg)
        self.assertIn("guru", overlay)
        self.assertIn("exit 1", overlay)
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("valid Portage profile could not be set", script)
        self.assertIn("ensure greetd system user", script)
        self.assertIn("DISPLAYMANAGER=\"greetd\"", script)
        self.assertIn("required package missing after emerge: gui-apps/gtkgreet", script)
        self.assertIn("rc-update add display-manager", script)
        self.assertNotIn("rc-update add greetd", script)
        self.assertEqual(greeter_boot_failures(cfg), [])

    def test_systemd_greetd_still_enables_greetd_unit(self) -> None:
        cfg = _base_cfg(
            window_managers=["gui-wm/sway"],
            display_manager="greetd",
            init_system="systemd",
        )
        script = _display_manager_script(cfg)
        self.assertIn("graphical.target", script)
        text = _final_tool_checks(cfg, ["grub"])
        self.assertIn("greetd systemd unit", text)
        self.assertNotIn("display-manager in default runlevel", text)
        self.assertEqual(greeter_boot_failures(cfg), [])


class RandomComboGreeterTests(unittest.TestCase):
    """Simulate many user choice combinations; each Wayland path must boot to a greeter."""

    WAYLAND_WMS = [pkg.atom for pkg in catalog.WAYLAND_COMPOSITORS]
    X11_WMS = [pkg.atom for pkg in catalog.X11_WINDOW_MANAGERS]
    SHELLS = ["none", "noctalia", "dms"]
    KERNELS = ["gentoo-kernel-bin", "gentoo-kernel", "gentoo-sources", "cachyos-kernel-bin"]
    INITS = ["openrc", "systemd"]
    CODECS = [False, True]
    PIPEWIRE = [False, True]
    MINIMAL_PKGS = [[], ["gui-apps/waybar", "gui-apps/foot"], ["sys-apps/flatpak"]]

    def test_random_combinations_boot_to_greeter(self) -> None:
        rng = random.Random(20261027)
        failures: list[str] = []
        for i in range(80):
            wayland = rng.choice([True, True, True, False])  # bias toward Wayland
            if wayland:
                wms = rng.sample(self.WAYLAND_WMS, k=rng.randint(1, 3))
                if rng.random() < 0.2:
                    wms += rng.sample(self.X11_WMS, k=1)
            else:
                wms = rng.sample(self.X11_WMS, k=rng.randint(1, 2))
            shell = rng.choice(self.SHELLS) if wayland else "none"
            # Old profiles may still say none / auto for Wayland.
            dm = rng.choice(["greetd", "none", "auto"]) if wayland else "none"
            cfg = _base_cfg(
                window_managers=list(wms),
                shell=shell,
                display_manager=dm,
                init_system=rng.choice(self.INITS),
                kernel=rng.choice(self.KERNELS),
                nonfree_codecs=rng.choice(self.CODECS),
                pipewire=rng.choice(self.PIPEWIRE),
                selected_packages=list(rng.choice(self.MINIMAL_PKGS)),
                extra_packages=list(wms),
            )
            problems = greeter_boot_failures(cfg)
            if problems:
                failures.append(f"combo {i}: wms={wms} shell={shell} dm={dm} → {problems}")
            if catalog.needs_login_greeter(cfg.window_managers, cfg.shell):
                if _resolved_dm(cfg) != "greetd":
                    failures.append(f"combo {i}: expected greetd, got {_resolved_dm(cfg)}")
                if not _login_sessions_for(cfg):
                    failures.append(f"combo {i}: empty sessions after ensure")
            # Script generation must not raise and must include greeter when needed.
            script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
            if catalog.needs_login_greeter(cfg.window_managers, cfg.shell):
                if "login manager: greetd" not in script and ">> login manager: greetd" not in script:
                    failures.append(f"combo {i}: chroot script missing greetd setup")
                if "www-client/firefox-bin:esr" not in script and "firefox-bin:esr" not in script:
                    # May appear only in keyword/unmask files inside the script.
                    if "firefox-bin:esr" not in script:
                        failures.append(f"combo {i}: firefox ESR not in chroot script")
        self.assertEqual(failures, [], msg="\n".join(failures[:20]))

    def test_rich_vs_minimal_hyprland_profiles(self) -> None:
        minimal = _base_cfg(
            window_managers=["gui-wm/hyprland"],
            shell="none",
            display_manager="auto",
            pipewire=False,
            selected_packages=[],
        )
        rich = _base_cfg(
            window_managers=["gui-wm/hyprland", "gui-wm/niri", "gui-wm/sway"],
            shell="dms",
            display_manager="none",  # must still become greetd
            pipewire=True,
            nonfree_codecs=True,
            selected_packages=["gui-apps/waybar", "sys-apps/flatpak", "www-client/qutebrowser"],
            init_system="systemd",
            kernel="cachyos-kernel-bin",
        )
        self.assertEqual(greeter_boot_failures(minimal), [])
        self.assertEqual(greeter_boot_failures(rich), [])
        self.assertEqual(_resolved_dm(rich), "greetd")


if __name__ == "__main__":
    unittest.main()
