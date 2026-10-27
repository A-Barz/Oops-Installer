"""Rough install duration from the choices already on an InstallConfig.

The number is a planning figure for a typical home link and a desktop-stage
binhost. Source builds and a few heavy packages stretch it. It is not a promise.
The install percent uses the same minute split.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import InstallConfig

# Minutes for one package when it is not in the heavy table.
_LIGHT_BIN = 3
_LIGHT_SOURCE = 12

_HEAVY_MINUTES = {
    "www-client/firefox": (25, 180),
    "www-client/firefox-bin": (12, 12),
    "www-client/google-chrome": (10, 10),
    "www-client/librewolf": (20, 180),
    "www-client/qutebrowser": (15, 40),
    "www-client/falkon": (12, 40),
    "www-client/epiphany": (12, 40),
    "app-office/libreoffice": (20, 160),
    "app-office/libreoffice-bin": (15, 15),
    "app-text/texlive": (25, 40),
    "dev-lang/rust": (15, 90),
    "dev-lang/rust-bin": (8, 8),
    "dev-lang/go": (8, 20),
    "dev-qt/qtwayland": (8, 30),
    "kde-plasma/breeze": (12, 30),
    "kde-frameworks/breeze-icons": (6, 12),
    "gui-wm/hyprland": (20, 50),
    "gui-wm/wayfire": (12, 30),
    "gui-wm/gamescope": (15, 35),
    "dev-libs/weston": (8, 15),
    "gui-wm/sway": (8, 20),
    "gui-wm/labwc": (8, 18),
    "gui-wm/dwl": (8, 18),
    "x11-wm/qtile": (8, 15),
    "x11-base/xorg-server": (10, 25),
    "app-emulation/qemu": (15, 40),
    "app-emulation/virtualbox": (15, 45),
    "app-containers/docker": (8, 20),
    "www-client/surf": (10, 40),
    "media-video/ffmpeg": (8, 25),
    "media-gfx/gimp": (10, 30),
    "media-gfx/inkscape": (10, 25),
    "sys-kernel/linux-headers": (5, 8),
    "x11-themes/kvantum": (6, 12),
}


def format_duration(minutes: int) -> str:
    """Speakable length: '2 hours 15 minutes'."""
    minutes = max(1, int(minutes))
    hours, mins = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
    if mins:
        parts.append(f"{mins} minute" if mins == 1 else f"{mins} minutes")
    return " ".join(parts) or "1 minute"


def format_remaining(minutes: int) -> str:
    minutes = max(0, int(minutes))
    if minutes <= 0:
        return "still going"
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m left"
    return f"{mins}m left"


def atom_key(atom: str) -> str:
    """Category/package, keeping a slot only when the heavy table lists it.

    sys-fs/fuse:0 stays distinct from sys-fs/fuse when the table has an
    opinion. Otherwise a slot is the same package.
    """
    slotted = atom.split("::", 1)[0].strip()
    if slotted in _HEAVY_MINUTES:
        return slotted
    return slotted.split(":", 1)[0]


def _minutes_for_atom(cfg: InstallConfig, key: str) -> int:
    binary = bool(cfg.binhost)
    pair = _HEAVY_MINUTES.get(key)
    if pair:
        return pair[0] if binary else pair[1]
    if key.startswith("www-client/") and not key.endswith("-bin"):
        return 30 if binary else 90
    return _LIGHT_BIN if binary else _LIGHT_SOURCE


def _world_minutes(cfg: InstallConfig) -> int:
    """Minutes for emerge @world.

    A fresh stage3 is already close to its profile, so @world is smaller
    than a source browser. Plasma and GNOME build the desktop inside @world.
    """
    binary = bool(cfg.binhost)
    profile = (cfg.profile or "").lower()
    if any(name in profile for name in ("plasma", "gnome")):
        return 80 if binary else 200
    if "desktop" in profile or cfg.install_type == "desktop":
        return 20 if binary else 45
    return 12 if binary else 25


def _kernel_raw_minutes(cfg: InstallConfig) -> int:
    binary = bool(cfg.binhost)
    kernel = (cfg.kernel or "gentoo-kernel-bin").split("::", 1)[0]
    if "/" in kernel:
        kernel = kernel.rsplit("/", 1)[-1]
    if kernel in {"gentoo-kernel-bin", "cachyos-kernel-bin"}:
        return 10 if binary else 25
    if kernel in {"gentoo-kernel", "cachyos-kernel"}:
        return 55 if binary else 90
    # gentoo-sources and gentoo-sources-lts (menu id → same emerge) are source builds.
    if kernel in {"gentoo-sources", "gentoo-sources-lts"}:
        return 80
    return 80


def _early_raw_minutes(cfg: InstallConfig) -> int:
    """Partitioning, stage3, sync, and the other work before emerge @world."""
    minutes = 35
    if cfg.linux_firmware:
        minutes += 8
    if not (cfg.gentoo_mirrors or "").strip():
        minutes += 3
    if cfg.sof_firmware:
        minutes += 5
    if cfg.luks:
        minutes += 5
    if cfg.filesystem == "zfs" or cfg.layout == "zfs":
        minutes += 20
    return minutes


def _scale_base(cfg: InstallConfig, raw: int) -> int:
    """LTO and a source install stretch the pre-package work as one block.

    Scaling the sum, rather than each line, keeps the old minute total.
    int() on each line would drift by a minute.
    """
    scaled = raw
    if cfg.lto:
        scaled = int(scaled * 1.35)
    if not cfg.binhost:
        scaled = int(scaled * 1.6)
    return scaled


@dataclass(frozen=True)
class ProgressPlan:
    """Minute slices shared by the spoken estimate and the install percent.

    `atoms` includes the kernel. `alongside` is shell, compositor, and
    Plymouth time that has no Portage counter of its own; the bar credits
    it in proportion to the atoms, so the chroot's last line has no leftover
    slice to jump.
    """

    early: int
    world: int
    atoms: dict[str, int]
    alongside: int

    @property
    def after(self) -> int:
        return sum(self.atoms.values()) + self.alongside

    @property
    def total(self) -> int:
        return max(25, self.early + self.world + self.after)


def progress_plan(cfg: InstallConfig) -> ProgressPlan:
    """Split one install into early, @world, and later-package minutes.

    Later packages use the same per-atom minutes as the spoken estimate.
    The percent credits each of those packages from that emerge's own
    finished/total counter.
    """
    early_raw = _early_raw_minutes(cfg)
    kernel_raw = _kernel_raw_minutes(cfg)
    plymouth_raw = 12 if cfg.plymouth else 0
    raw = early_raw + kernel_raw + plymouth_raw
    scaled = _scale_base(cfg, raw)
    if raw:
        kernel_minutes = int(scaled * kernel_raw / raw)
        plymouth_minutes = int(scaled * plymouth_raw / raw)
        early = scaled - kernel_minutes - plymouth_minutes
    else:
        kernel_minutes = plymouth_minutes = early = 0

    binary = bool(cfg.binhost)
    alongside = plymouth_minutes
    shell = getattr(cfg, "shell", "none")
    if shell not in {"", "none"}:
        alongside += 15 if binary else 35
    if cfg.window_managers or shell not in {"", "none"}:
        alongside += 15 if binary else 40

    atoms: dict[str, int] = {}
    kernel_name = cfg.kernel or "gentoo-kernel-bin"
    if kernel_minutes > 0:
        atoms[f"sys-kernel/{kernel_name}"] = kernel_minutes
    seen = set(atoms)
    for atom in (
        *(cfg.extra_packages or []),
        *(cfg.window_managers or []),
        *(cfg.selected_packages or []),
    ):
        key = atom_key(atom)
        if not key or key in seen:
            continue
        seen.add(key)
        atoms[key] = _minutes_for_atom(cfg, key)

    plan = ProgressPlan(
        early=early,
        world=_world_minutes(cfg),
        atoms=atoms,
        alongside=alongside,
    )
    short = 25 - (plan.early + plan.world + plan.after)
    if short > 0:
        return ProgressPlan(plan.early + short, plan.world, dict(plan.atoms), plan.alongside)
    return plan


def estimate_minutes(cfg: InstallConfig) -> int:
    """Minutes from kernel, binhost, @world, and the packages queued for emerge."""
    return progress_plan(cfg).total
