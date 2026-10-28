"""Pre-install estimate stays; live percent / sticky Jobs UI is gone."""

from __future__ import annotations

import unittest

from gentoo_installer import ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.estimate import (
    _HEAVY_MINUTES,
    _LIGHT_BIN,
    _LIGHT_SOURCE,
    atom_key,
    estimate_minutes,
    format_duration,
    progress_plan,
)


def legacy_minutes(cfg: InstallConfig) -> int:
    """Minute total from before @world had a slice of its own.

    The new estimate may only grow by progress_plan().world.
    """
    binary = bool(cfg.binhost)
    minutes = 35
    kernel = cfg.kernel or "gentoo-kernel-bin"
    if kernel == "gentoo-kernel-bin":
        minutes += 10 if binary else 25
    elif kernel == "gentoo-kernel":
        minutes += 55 if binary else 90
    else:
        minutes += 80
    if cfg.linux_firmware:
        minutes += 8
    if not (cfg.gentoo_mirrors or "").strip():
        minutes += 3
    if cfg.sof_firmware:
        minutes += 5
    if cfg.plymouth:
        minutes += 12
    if cfg.luks:
        minutes += 5
    if cfg.filesystem == "zfs" or cfg.layout == "zfs":
        minutes += 20
    if cfg.lto:
        minutes = int(minutes * 1.35)
    if not binary:
        minutes = int(minutes * 1.6)
    atoms: list[str] = []
    atoms.extend(cfg.extra_packages or [])
    atoms.extend(cfg.window_managers or [])
    atoms.extend(cfg.selected_packages or [])
    shell = getattr(cfg, "shell", "none")
    if shell not in {"", "none"}:
        minutes += 15 if binary else 35
    if cfg.window_managers or shell not in {"", "none"}:
        minutes += 15 if binary else 40
    seen: set[str] = set()
    for atom in atoms:
        slotted = atom.split("::", 1)[0]
        key = slotted if slotted in _HEAVY_MINUTES else slotted.split(":")[0]
        if not key or key in seen:
            continue
        seen.add(key)
        pair = _HEAVY_MINUTES.get(key)
        if pair:
            minutes += pair[0] if binary else pair[1]
        elif key.startswith("www-client/") and not key.endswith("-bin"):
            minutes += 30 if binary else 90
        else:
            minutes += _LIGHT_BIN if binary else _LIGHT_SOURCE
    return max(25, minutes)


def desktop_source() -> InstallConfig:
    """Source desktop whose long work is the selected packages, not @world."""
    cfg = InstallConfig()
    cfg.binhost = False
    cfg.profile = "default/linux/amd64/23.0/desktop"
    cfg.install_type = "desktop"
    cfg.kernel = "gentoo-kernel"
    cfg.window_managers = ["gui-wm/hyprland"]
    cfg.selected_packages = [
        "www-client/firefox",
        "app-office/libreoffice",
        "media-gfx/gimp",
        "media-video/ffmpeg",
        *[f"app-misc/pkg{i}" for i in range(12)],
    ]
    return cfg


class EstimateTests(unittest.TestCase):
    def test_spoken_estimate_adds_world_minutes(self) -> None:
        cfg = desktop_source()
        plan = progress_plan(cfg)
        self.assertEqual(estimate_minutes(cfg), legacy_minutes(cfg) + plan.world)
        self.assertGreater(plan.after, plan.world)
        self.assertEqual(plan.atoms["www-client/firefox"], _HEAVY_MINUTES["www-client/firefox"][1])

        bare = InstallConfig()
        bare.binhost = True
        bare.profile = "default/linux/amd64/23.0"
        bare.install_type = "minimal"
        bare.window_managers = []
        bare.selected_packages = []
        bare_plan = progress_plan(bare)
        self.assertEqual(estimate_minutes(bare), legacy_minutes(bare) + bare_plan.world)
        self.assertGreater(progress_plan(self._plasma()).world, plan.world)

    def test_atom_key_keeps_a_listed_slot_only(self) -> None:
        self.assertEqual(atom_key("sys-fs/fuse:0"), "sys-fs/fuse")
        self.assertEqual(atom_key("www-client/firefox::gentoo"), "www-client/firefox")

    def test_format_duration_is_speakable(self) -> None:
        self.assertIn("hour", format_duration(90))
        self.assertIn("minute", format_duration(12))

    def _plasma(self) -> InstallConfig:
        cfg = desktop_source()
        cfg.profile = "default/linux/amd64/23.0/desktop/plasma"
        return cfg


class NoLiveProgressUiTests(unittest.TestCase):
    def test_install_progress_class_removed(self) -> None:
        self.assertFalse(hasattr(ui, "InstallProgress"))
        self.assertFalse(hasattr(ui, "progress_bar"))
        self.assertFalse(hasattr(ui, "progress_percent"))

    def test_wait_notice_is_long_nap_not_percent(self) -> None:
        notice = ui.INSTALL_WAIT_NOTICE.lower()
        self.assertIn("nap", notice)
        self.assertIn("no progress", notice)
        self.assertNotIn("%", notice)
        self.assertNotIn("jobs", notice)


if __name__ == "__main__":
    unittest.main()
