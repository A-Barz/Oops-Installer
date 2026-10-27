"""The install percent and the spoken estimate share one minute budget.

A transcript of emerge lines is enough. Nothing here partitions a disk.
"""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.estimate import (
    _HEAVY_MINUTES,
    _LIGHT_BIN,
    _LIGHT_SOURCE,
    atom_key,
    estimate_minutes,
    progress_plan,
)
from gentoo_installer.ui import InstallProgress, progress_bar


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

    def _plasma(self) -> InstallConfig:
        cfg = desktop_source()
        cfg.profile = "default/linux/amd64/23.0/desktop/plasma"
        return cfg


class ProgressTests(unittest.TestCase):
    def test_packages_move_the_bar_and_the_closing_line_does_not(self) -> None:
        cfg = desktop_source()
        plan = progress_plan(cfg)
        progress = InstallProgress(plan)
        progress.feed(">> emerge @world")
        progress.feed(">>> Jobs: 40 of 40 complete")
        after_world = progress.snapshot()
        self.assertLess(after_world, 40, msg=f"@world finished at {after_world}%")

        progress.feed(">> fetch kernel and package sources")
        progress.feed(">>> Jobs: 1 of 80 complete")
        self.assertEqual(progress.snapshot(), after_world)

        for atom in plan.atoms:
            progress.feed(f">> emerge {atom}")
            progress.feed(f">>> Installing (1 of 1) {atom}-1.0")
            progress.feed(">>> Jobs: 1 of 1 complete")
        after_packages = progress.snapshot()
        span = int(plan.after / plan.total * 96)
        self.assertGreaterEqual(after_packages - after_world, span - 5)

        progress.feed("Chroot configuration complete.")
        self.assertLessEqual(progress.snapshot() - after_packages, 2)

    def test_multi_job_emerge_moves_before_it_finishes(self) -> None:
        cfg = InstallConfig()
        cfg.binhost = False
        cfg.profile = "default/linux/amd64/23.0"
        cfg.install_type = "minimal"
        cfg.selected_packages = ["www-client/firefox"]
        progress = InstallProgress(progress_plan(cfg))
        progress.feed(">> emerge @world")
        progress.feed(">>> Jobs: 4 of 4 complete")
        baseline = progress.snapshot()
        progress.feed(">> emerge www-client/firefox")
        progress.feed(">>> Jobs: 5 of 10 complete")
        midpoint = progress.snapshot()
        progress.feed(">>> Jobs: 10 of 10 complete")
        finished = progress.snapshot()
        self.assertGreater(midpoint, baseline)
        self.assertGreater(finished, midpoint)
        progress.feed("Chroot configuration complete.")
        self.assertLessEqual(progress.snapshot() - finished, 2)

    def test_build_log_does_not_rewind_world(self) -> None:
        progress = InstallProgress(progress_plan(desktop_source()))
        progress.feed(">> emerge @world")
        progress.feed(">>> Jobs: 20 of 40 complete")
        mid = progress.snapshot()
        progress.feed("libtool: Calculating dependencies for a nested build")
        self.assertEqual(progress.snapshot(), mid)
        progress.feed(">> @world died with Illegal instruction; rebuilding from source")
        self.assertLess(progress.snapshot(), mid)
        self.assertGreaterEqual(progress._shown, mid)

    def test_failure_line_does_not_count_as_that_package(self) -> None:
        cfg = desktop_source()
        progress = InstallProgress(progress_plan(cfg))
        progress.feed(">> emerge @world")
        progress.feed(">>> Jobs: 40 of 40 complete")
        held = progress.snapshot()
        progress.feed(">> emerge failed (1): www-client/firefox; building this package from source")
        progress.feed(">>> Jobs: 1 of 1 complete")
        self.assertEqual(progress.snapshot(), held)

    def test_status_lines_include_bar_percent_and_jobs_countdown(self) -> None:
        progress = InstallProgress(progress_plan(desktop_source()))
        progress.feed(">> emerge @world")
        progress.feed(">>> Jobs: 5 of 40 complete")
        bar, detail = progress.status_lines()
        self.assertIn("%", bar)
        self.assertIn("[", bar)
        self.assertIn("#", progress_bar(42, "still going"))
        self.assertIn("Jobs: 5 of 40", detail)
        self.assertIn("35 left in this emerge", detail)
        self.assertIn("@world", detail)

    def test_per_package_jobs_countdown_updates_status(self) -> None:
        cfg = InstallConfig()
        cfg.binhost = False
        cfg.profile = "default/linux/amd64/23.0"
        cfg.selected_packages = ["www-client/firefox"]
        progress = InstallProgress(progress_plan(cfg))
        progress.feed(">> emerge @world")
        progress.feed(">>> Jobs: 4 of 4 complete")
        progress.feed(">> emerge www-client/firefox")
        progress.feed(">>> Jobs: 3 of 10 complete")
        _bar, detail = progress.status_lines()
        self.assertIn("www-client/firefox", detail)
        self.assertIn("Jobs: 3 of 10", detail)
        self.assertIn("7 left in this emerge", detail)


if __name__ == "__main__":
    unittest.main()
