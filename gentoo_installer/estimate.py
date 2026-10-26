"""Rough install duration from the choices already on an InstallConfig.

The number is a planning figure for a typical home link and a desktop-stage
binhost. Source builds and a few heavy packages stretch it. It is not a promise.
"""

from __future__ import annotations

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


def _atom_key(atom: str) -> str:
    """Category/package, without a slot, overlay, or version."""
    bare = atom.split("::", 1)[0]
    return bare.split(":", 1)[0] if bare.count("/") == 1 and ":" in bare else bare


def progress_weights(cfg: InstallConfig) -> tuple[int, int, int]:
    """Relative sizes of the early, @world, and later-package slices.

    The install percent uses these only to split the bar. Inside @world and
    each later emerge it follows Portage's finished/total counter, so a short
    time estimate cannot run the number ahead of emerge.
    """
    binary = bool(cfg.binhost)
    profile = (cfg.profile or "").lower()
    if any(name in profile for name in ("plasma", "gnome")):
        world = 320 if binary else 560
    elif "desktop" in profile or cfg.install_type == "desktop":
        world = 200 if binary else 380
    else:
        world = 100 if binary else 220
    if cfg.lto:
        world = int(world * 1.15)

    after = 16
    kernel = cfg.kernel or "gentoo-kernel-bin"
    if kernel == "gentoo-kernel":
        after += 14
    elif kernel == "gentoo-sources":
        after += 10
    else:
        after += 4
    seen: set[str] = set()
    atoms: list[str] = []
    atoms.extend(cfg.extra_packages or [])
    atoms.extend(cfg.window_managers or [])
    atoms.extend(cfg.selected_packages or [])
    for atom in atoms:
        key = _atom_key(atom)
        if not key or key in seen:
            continue
        seen.add(key)
        heavy = key in _HEAVY_MINUTES or (key.startswith("www-client/") and not key.endswith("-bin"))
        if heavy:
            after += 8 if binary else 14
        else:
            after += 2 if binary else 4
    return 8, world, after


def estimate_minutes(cfg: InstallConfig) -> int:
    """Minutes from kernel, binhost, and the packages queued for emerge."""
    binary = bool(cfg.binhost)
    minutes = 35  # partitioning, stage3 fetch, Portage sync, base tools
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
    if getattr(cfg, "shell", "none") not in {"", "none"}:
        minutes += 15 if binary else 35
    if cfg.window_managers or getattr(cfg, "shell", "none") not in {"", "none"}:
        minutes += 15 if binary else 40

    seen: set[str] = set()
    for atom in atoms:
        # sys-fs/fuse:0 should stay distinct from sys-fs/fuse. Only strip a
        # slot when the heavy table has no opinion; keep the category/package.
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
