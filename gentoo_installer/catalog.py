"""Categorised Gentoo packages, overlays, window managers, and Plymouth themes.

This is *not* a dump of the entire ::gentoo tree (tens of thousands of
ebuilds). It is a handbook-sized catalogue plus the ability to type any
atom. Overlay packages (niri, Hyprland, …) record their eselect name in
Pkg.overlay so execute.py can `eselect repository enable` before emerge.

Sources:
  https://wiki.gentoo.org/wiki/Window_manager
  https://wiki.gentoo.org/wiki/List_of_software_for_Wayland
  https://overlays.gentoo.org/
  https://wiki.gentoo.org/wiki/Plymouth
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pkg:
    """One emerge atom as shown in the wizard.

    overlay:
      "gentoo" — main tree (::gentoo), always present after stage3.
      anything else — name passed to `eselect repository enable`.
    """

    atom: str
    desc: str
    overlay: str = "gentoo"


def _p(atom: str, desc: str, overlay: str = "gentoo") -> tuple[str, str]:
    """UI row: (atom, 'description  ::overlay') for paged_checklist."""
    tag = "" if overlay == "gentoo" else f"  ::{overlay}"
    return (atom, desc + tag)


# Roughly the top Wayland compositors people actually use, plus previously
# offered GURU options that are still viable (river, SwayFX, MangoWM, miracle-wm).
# GURU compositors enable that overlay only when selected. Hyprland stays on
# hyproverlay (not GURU).

WAYLAND_COMPOSITORS: list[Pkg] = [
    Pkg("gui-wm/hyprland", "Hyprland — dynamic tiling Wayland compositor", "hyproverlay"),
    Pkg("gui-wm/sway", "Sway — i3-compatible wlroots compositor"),
    Pkg("gui-wm/swayfx", "SwayFX — Sway with eye-candy (rounded corners, blur)", "guru"),
    Pkg("gui-wm/niri", "Niri — scrollable tiling Wayland compositor", "guru"),
    Pkg("gui-wm/river", "River — dynamic tiling Wayland compositor (Zig)", "guru"),
    Pkg("gui-wm/wayfire", "Wayfire — stacking Wayland compositor"),
    Pkg("gui-wm/labwc", "Labwc — Openbox-style stacking compositor"),
    Pkg("gui-wm/dwl", "dwl — suckless dwm for Wayland"),
    Pkg("gui-wm/mangowm", "MangoWM — wlroots compositor with SceneFX eye-candy", "guru"),
    Pkg("gui-wm/miracle-wm", "miracle-wm — Mir-based tiling Wayland compositor", "guru"),
    Pkg("x11-wm/qtile", "Qtile — Python WM on Wayland"),
    Pkg("gui-wm/gamescope", "Gamescope — Steam/micro-compositor"),
    Pkg("dev-libs/weston", "Weston — Wayland reference compositor"),
]

# Suckless dwm and i3. The long X11/DE window-manager list is not offered.
X11_WINDOW_MANAGERS: list[Pkg] = [
    Pkg("x11-wm/dwm", "dwm — suckless dynamic window manager"),
    Pkg("x11-wm/i3", "i3 — popular X11 tiling window manager"),
]

WINDOW_MANAGERS: list[Pkg] = WAYLAND_COMPOSITORS + X11_WINDOW_MANAGERS

# (title, Exec=, "wayland"|"x11") when the package name is not the command.
_SESSION_OVERRIDE: dict[str, tuple[str, str, str] | None] = {
    "gui-wm/hyprland": ("Hyprland", "Hyprland", "wayland"),
    "gui-wm/niri": ("Niri", "niri-session", "wayland"),
    "gui-wm/swayfx": ("SwayFX", "sway", "wayland"),
    "gui-wm/river": ("River", "river", "wayland"),
    "gui-wm/mangowm": ("MangoWM", "mango", "wayland"),
    "gui-wm/miracle-wm": ("miracle-wm", "miracle-wm", "wayland"),
    "x11-wm/qtile": ("Qtile (Wayland)", "qtile start -b wayland", "wayland"),
    "dev-libs/weston": ("Weston", "weston", "wayland"),
    "gui-wm/gamescope": ("Gamescope", "gamescope -- foot", "wayland"),
    "x11-wm/dwm": ("dwm", "start-dwm", "x11"),
    "x11-wm/i3": ("i3", "i3", "x11"),
}

# Shells the wizard can select. Both ebuilds live in GURU; the overlay is
# enabled only after this choice, and GURU is not a package-list option.
SHELL_CHOICES: list[tuple[str, str]] = [
    ("none", "No shell — compositor plus shared utilities"),
    ("noctalia", "Noctalia shell"),
    ("dms", "DMS (DankMaterialShell)"),
]

SHELL_PACKAGES: dict[str, list[Pkg]] = {
    "noctalia": [Pkg("gui-apps/noctalia-shell", "Noctalia shell", "guru")],
    "dms": [Pkg("gui-apps/dankmaterialshell", "DMS (DankMaterialShell)", "guru")],
}

# greetd frontend used only when Noctalia is the shell and a login manager is enabled.
NOCTALIA_GREETER = Pkg("gui-apps/noctalia-greeter", "Noctalia greeter for greetd", "guru")

SHELL_EXEC: dict[str, str] = {
    "noctalia": "noctalia-shell",
    "dms": "dms",
}

# Previously offered from GURU but not restored as menu options. A saved profile
# or typed atom must not emerge them. Wizard-offered GURU WMs/shells are the
# exception (see offered_guru_atoms). vscode/vscodium/helix now live in ::gentoo.
REMOVED_GURU_ATOMS = frozenset(
    {
        "gui-wm/cage",
        "gui-apps/fuzzel",
        "gui-apps/swaylock-effects",
        "gui-apps/xwayland-satellite",
        "gui-apps/nwg-look",
        "x11-misc/ly",
        "gui-apps/tuigreet",
        "www-client/brave-bin",
        "app-misc/fastfetch",
        "app-office/onlyoffice-bin",
        "net-im/discord",
        "games-util/mangohud",
        "games-emulation/retroarch",
        "app-emulation/distrobox",
    }
)

# Offered packages that are still ~arch on a stable system. Keywording them
# keeps one masked atom from failing its emerge. The installer also keywords
# every atom it plans to emerge (see execute._keyword_text); this set remains
# as an explicit allow-list for tests and documentation.
TESTING_ATOMS = frozenset(
    {
        "gui-wm/niri",
        "gui-wm/swayfx",
        "gui-wm/river",
        "gui-wm/mangowm",
        "gui-wm/miracle-wm",
        "gui-wm/gamescope",
        "x11-themes/kvantum",
        "x11-themes/tela-icon-theme",
        "x11-themes/numix-icon-theme-circle",
        "media-fonts/iosevka",
        "media-sound/easyeffects",
        "media-sound/cava",
        "media-sound/playerctl",
        "gnome-extra/polkit-gnome",
        "kde-apps/dolphin",
        "gnome-base/nautilus",
        "sys-kernel/cachyos-kernel",
        "sys-kernel/cachyos-kernel-bin",
        "sys-kernel/cachyos-sources",
        "media-libs/fdk-aac",
    }
)

# Kernel packages the wizard offers. Value stored in InstallConfig.kernel is the
# package name only (no category). Overlay kernels need eselect repository enable.
# There is no Ubuntu LTS kernel ebuild in ::gentoo or a known repos.gentoo.org
# overlay that this installer can emerge (Ubuntu ships .deb kernels).
KERNEL_OPTIONS: list[Pkg] = [
    Pkg("sys-kernel/gentoo-kernel-bin", "Binary dist-kernel — fastest, matches binhost"),
    Pkg("sys-kernel/gentoo-kernel", "Source dist-kernel — compiled on this machine"),
    Pkg(
        "sys-kernel/gentoo-sources",
        "gentoo-sources — manual config; tracks upstream LTS (dist-kernel helpers still used if possible)",
    ),
    Pkg(
        "sys-kernel/cachyos-kernel-bin",
        "CachyOS binary dist-kernel (::CachyOS-kernels overlay)",
        "CachyOS-kernels",
    ),
    Pkg(
        "sys-kernel/cachyos-kernel",
        "CachyOS source dist-kernel (::CachyOS-kernels overlay)",
        "CachyOS-kernels",
    ),
]


def kernel_menu_choices() -> list[tuple[str, str]]:
    """Wizard rows: (short package name, description)."""
    return [(pkg.atom.rsplit("/", 1)[-1], pkg.desc) for pkg in KERNEL_OPTIONS]


def kernel_atom(name: str) -> str:
    """Full emerge atom for a short kernel package name."""
    bare = (name or "gentoo-kernel-bin").split("::", 1)[0]
    if "/" in bare:
        return bare
    return f"sys-kernel/{bare}"


def kernel_overlay(name: str) -> str:
    """Overlay required for this kernel choice, or gentoo."""
    atom = kernel_atom(name)
    for pkg in KERNEL_OPTIONS:
        if pkg.atom == atom:
            return pkg.overlay
    return "gentoo"

# Compositors that speak the wlroots protocols grim, slurp, and portal-wlr need.
# Niri, Weston, and Gamescope do not. Niri uses the GNOME portal instead.
_WLR_HELPER_COMPOSITORS = frozenset(
    {
        "gui-wm/hyprland",
        "gui-wm/sway",
        "gui-wm/swayfx",
        "gui-wm/river",
        "gui-wm/wayfire",
        "gui-wm/labwc",
        "gui-wm/dwl",
        "gui-wm/mangowm",
        "x11-wm/qtile",
    }
)

# Installed for every selected Wayland compositor. wlroots-only rows are
# dropped for a Niri-only session. They are not a separate checklist.
WAYLAND_BASE: list[Pkg] = [
    Pkg("sys-apps/xdg-desktop-portal", "XDG desktop portal"),
    Pkg("gui-libs/xdg-desktop-portal-wlr", "wlroots XDG portal"),
    Pkg("sys-apps/xdg-desktop-portal-gtk", "GTK portal backend"),
    Pkg("gui-apps/wl-clipboard", "wl-copy / wl-paste"),
    Pkg("gui-apps/grim", "Grim screenshots"),
    Pkg("gui-apps/slurp", "Slurp region select"),
    Pkg("x11-base/xwayland", "Xwayland"),
    Pkg("dev-qt/qtwayland", "Qt Wayland platform plugin"),
    Pkg("media-fonts/noto", "Noto fonts"),
    Pkg("media-fonts/noto-emoji", "Noto Emoji"),
    Pkg("sys-auth/polkit", "polkit"),
]

# Extra packages a compositor needs that the shared set does not cover.
WM_REQUIREMENTS: dict[str, list[Pkg]] = {
    "gui-wm/hyprland": [
        Pkg("gui-apps/xdg-desktop-portal-hyprland", "Hyprland XDG portal", "hyproverlay"),
        Pkg("gui-apps/hyprpaper", "Hyprpaper wallpaper", "hyproverlay"),
        Pkg("gui-apps/hypridle", "Hypridle", "hyproverlay"),
        Pkg("gui-apps/hyprlock", "Hyprlock", "hyproverlay"),
    ],
    "gui-wm/sway": [
        Pkg("gui-apps/swaybg", "SwayBG wallpaper"),
        Pkg("gui-apps/swayidle", "Swayidle"),
        Pkg("gui-apps/swaylock", "Swaylock"),
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "gui-wm/swayfx": [
        Pkg("gui-apps/swaybg", "SwayBG wallpaper"),
        Pkg("gui-apps/swayidle", "Swayidle"),
        Pkg("gui-apps/swaylock", "Swaylock"),
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "gui-wm/river": [
        Pkg("gui-apps/foot", "foot terminal"),
        Pkg("gui-apps/swaybg", "Wallpaper"),
        Pkg("gui-apps/swaylock", "Swaylock"),
    ],
    "gui-wm/wayfire": [
        Pkg("gui-apps/wf-shell", "wf-shell — Wayfire panel and background"),
    ],
    "gui-wm/labwc": [
        Pkg("gui-apps/swaybg", "Wallpaper"),
        Pkg("gui-apps/swaylock", "Swaylock"),
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "gui-wm/dwl": [
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "gui-wm/mangowm": [
        Pkg("gui-apps/foot", "foot terminal"),
        Pkg("gui-apps/swaybg", "Wallpaper"),
    ],
    "gui-wm/miracle-wm": [
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "gui-wm/niri": [
        Pkg("sys-apps/xdg-desktop-portal-gnome", "GNOME portal — file chooser and screencast on Niri"),
        Pkg("gnome-base/gnome-keyring", "GNOME Keyring — secrets for that portal"),
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "x11-wm/qtile": [
        Pkg("x11-terms/alacritty", "Alacritty"),
    ],
    "dev-libs/weston": [
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "gui-wm/gamescope": [
        Pkg("gui-apps/foot", "foot terminal"),
    ],
    "x11-wm/dwm": [
        Pkg("x11-base/xorg-server", "X.Org server"),
        Pkg("x11-apps/xinit", "xinit / startx"),
        Pkg("x11-misc/dmenu", "dmenu"),
        Pkg("x11-terms/st", "st terminal"),
        Pkg("x11-misc/slstatus", "slstatus"),
        Pkg("x11-misc/slock", "slock"),
    ],
    "x11-wm/i3": [
        Pkg("x11-base/xorg-server", "X.Org server"),
        Pkg("x11-apps/xinit", "xinit / startx"),
        Pkg("x11-misc/dmenu", "dmenu"),
        Pkg("x11-misc/i3status", "i3status"),
        Pkg("x11-terms/st", "st terminal"),
    ],
}

# Left out of a Niri-only install. grim and slurp need wlr-screencopy, which
# Niri does not implement, and portal-wlr does not serve Niri clients.
_NIRI_SKIP_BASE = frozenset(
    {
        "gui-libs/xdg-desktop-portal-wlr",
        "gui-apps/grim",
        "gui-apps/slurp",
        "x11-base/xwayland",
    }
)

# Apps that run on Niri (layer-shell, idle, lock, the niri waybar module).
# Shown as their own list and preselected when Niri is chosen without a shell.
NIRI_APPS: list[Pkg] = [
    Pkg("gui-apps/waybar", "Waybar — bar, with the niri module"),
    Pkg("gui-apps/wofi", "Wofi — application launcher"),
    Pkg("gui-apps/mako", "Mako — notifications"),
    Pkg("gui-apps/swaybg", "swaybg — wallpaper on Niri"),
    Pkg("gui-apps/swayidle", "swayidle — idle on Niri"),
    Pkg("gui-apps/swaylock", "swaylock — screen lock on Niri"),
    Pkg("x11-terms/alacritty", "Alacritty — terminal"),
    Pkg("media-gfx/imv", "imv — image viewer"),
    Pkg("media-video/mpv", "mpv"),
]

# Optional apps that run on every compositor this installer offers.
WAYLAND_COMMON_UTILS: list[Pkg] = [
    Pkg("gui-apps/foot", "foot — Wayland terminal"),
    Pkg("x11-terms/alacritty", "Alacritty — GPU terminal"),
    Pkg("x11-terms/kitty", "Kitty"),
    Pkg("x11-terms/ghostty", "Ghostty"),
    Pkg("media-gfx/imv", "imv — image viewer"),
    Pkg("media-video/mpv", "mpv"),
    Pkg("gui-apps/waybar", "Waybar — status bar"),
    Pkg("gui-apps/wofi", "Wofi — launcher"),
    Pkg("gui-apps/mako", "Mako — notifications"),
    Pkg("gui-apps/kanshi", "Kanshi — output profiles"),
]


def _pkg_title(atom: str) -> str:
    for pkg in WINDOW_MANAGERS:
        if pkg.atom == atom:
            return pkg.desc.split("—", 1)[0].strip()
    return atom.rsplit("/", 1)[-1]


def wayland_selected(atoms: list[str]) -> bool:
    picked = set(atoms)
    return any(p.atom in picked for p in WAYLAND_COMPOSITORS)


def shell_atoms(shell: str) -> list[str]:
    return [pkg.atom for pkg in SHELL_PACKAGES.get(shell, [])]


def shell_exec(shell: str) -> str:
    return SHELL_EXEC.get(shell, "")


def offered_guru_atoms(shell: str) -> set[str]:
    """GURU atoms the wizard may emerge: shells, greeter, and offered GURU WMs."""
    allowed = set(shell_atoms(shell))
    for pkg in WINDOW_MANAGERS:
        if pkg.overlay == "guru":
            allowed.add(pkg.atom)
    if shell == "noctalia":
        allowed.add(NOCTALIA_GREETER.atom)
    return allowed


# Proprietary / patent-encumbered multimedia stack. Only emerged after the user
# explicitly accepts the licenses in the wizard (cfg.nonfree_codecs).
NONFREE_CODEC_PACKAGES: list[Pkg] = [
    Pkg("media-libs/gst-plugins-ugly", "GStreamer ugly plugins — MPEG, DVD, patented codecs"),
    Pkg("media-libs/gst-plugins-bad", "GStreamer bad plugins — extra formats"),
    Pkg("media-plugins/gst-plugins-libav", "GStreamer FFmpeg plugin — broad codec coverage"),
    Pkg("media-libs/fdk-aac", "Fraunhofer FDK AAC (proprietary license)"),
    Pkg("media-libs/faac", "FAAC AAC encoder"),
    Pkg("media-sound/lame", "LAME MP3 encoder"),
    Pkg("media-video/ffmpeg", "FFmpeg — decode/encode many formats"),
]

# Licenses appended to ACCEPT_LICENSE when non-free codecs are accepted.
NONFREE_CODEC_LICENSES = (
    "FraunhoferFDK",
    "MPEG-4",
)


def nonfree_codec_atoms() -> list[str]:
    return [pkg.atom for pkg in NONFREE_CODEC_PACKAGES]


def niri_app_rows() -> list[tuple[str, str]]:
    """Checklist rows for software that works on Niri."""
    return [_p(pkg.atom, pkg.desc, pkg.overlay) for pkg in NIRI_APPS]


def niri_preselected(shell: str) -> list[str]:
    """Atoms ticked ahead of the package lists.

    A shell already provides a bar, launcher, and notifications, so those stay
    unticked. The portal, keyring, and foot are requirements and are not here.
    """
    if shell != "none":
        return ["gui-apps/swaylock"]
    return [
        "gui-apps/waybar",
        "gui-apps/wofi",
        "gui-apps/mako",
        "gui-apps/swaybg",
        "gui-apps/swayidle",
        "gui-apps/swaylock",
    ]


def packages_for_selection(wm_atoms: list[str], shell: str, pipewire: bool) -> list[Pkg]:
    """Necessities for the chosen compositors, plus the shell if one was chosen.

    Shared Wayland utilities are included once. GURU atoms are limited to the
    selected shell and to Niri when that compositor is selected.
    """
    allowed_guru = offered_guru_atoms(shell)
    pkgs: list[Pkg] = []
    selected = [atom for atom in wm_atoms if atom]
    if wayland_selected(selected) or shell in SHELL_PACKAGES:
        base = list(WAYLAND_BASE)
        if "gui-wm/niri" in selected and not any(atom in _WLR_HELPER_COMPOSITORS for atom in selected):
            base = [pkg for pkg in base if pkg.atom not in _NIRI_SKIP_BASE]
        pkgs.extend(base)
        if pipewire:
            pkgs.append(Pkg("media-video/pipewire", "PipeWire"))
            pkgs.append(Pkg("media-video/wireplumber", "WirePlumber"))
    for atom in selected:
        pkgs.extend(WM_REQUIREMENTS.get(atom, []))
    pkgs.extend(SHELL_PACKAGES.get(shell, []))
    seen: set[str] = set()
    out: list[Pkg] = []
    for pkg in pkgs:
        if pkg.atom in seen:
            continue
        if pkg.overlay == "guru" and pkg.atom not in allowed_guru:
            continue
        seen.add(pkg.atom)
        out.append(pkg)
    return out


def login_sessions(desktop: str, atoms: list[str], shell: str = "") -> list[tuple[str, str, str]]:
    """Sessions the greeter should offer: (title, exec, wayland|x11)."""
    del desktop  # Desktop environments are not installed.
    sessions: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    dwl_cmd = "start-dwl" if shell_exec(shell) else "dwl"

    def add(title: str, command: str, kind: str) -> None:
        key = (command, kind)
        if not command or key in seen:
            return
        seen.add(key)
        sessions.append((title, command, kind))

    for atom in atoms:
        if atom == "gui-wm/dwl":
            add("dwl", dwl_cmd, "wayland")
            continue
        if atom == "x11-wm/qtile" and shell_exec(shell):
            add("Qtile (Wayland)", "start-qtile", "wayland")
            continue
        if atom in _SESSION_OVERRIDE:
            override = _SESSION_OVERRIDE[atom]
            if override:
                add(*override)
            continue
        known = {p.atom for p in WINDOW_MANAGERS}
        if atom not in known:
            continue
        kind = "wayland" if any(p.atom == atom for p in WAYLAND_COMPOSITORS) else "x11"
        command = atom.rsplit("/", 1)[-1]
        add(_pkg_title(atom), command, kind)
    return sessions


def resolved_display_manager(display_manager: str, desktop: str, atoms: list[str]) -> str:
    """Login manager after GRUB. Wayland sessions use greetd. X11 uses startx.

    An explicit greetd/sddm/lightdm/gdm choice still wins. "none" stays on the TTY.
    """
    del desktop
    choice = (display_manager or "auto").strip()
    picked = set(atoms)
    if choice in {"sddm", "gdm", "lightdm", "greetd", "slim"}:
        return choice
    if "gui-libs/greetd" in picked or "gui-apps/gtkgreet" in picked:
        return "greetd"
    if "x11-misc/slim" in picked:
        return "slim"
    if "x11-misc/sddm" in picked:
        return "sddm"
    if "gnome-base/gdm" in picked:
        return "gdm"
    if "x11-misc/lightdm" in picked:
        return "lightdm"
    if choice == "none":
        return "none"
    if wayland_selected(list(picked)) or any(
        atom in picked for atom in ("gui-apps/noctalia-shell", "gui-apps/dankmaterialshell")
    ):
        return "greetd"
    return "none"


def wm_choices() -> list[tuple[str, str]]:
    """Tagged rows for the window-manager checklist (Wayland vs X11)."""
    rows = []
    for p in WINDOW_MANAGERS:
        kind = "Wayland" if p in WAYLAND_COMPOSITORS else "X11"
        rows.append(_p(p.atom, f"[{kind}] {p.desc}", p.overlay))
    return rows


# Themes shipped inside sys-boot/plymouth (plymouth-set-default-theme --list).
# spinner is Plymouth's own packaged theme. It is not this installer's default.
PLYMOUTH_BUILTIN_THEMES: list[tuple[str, str]] = [
    ("spinner", "Spinner — ring animation"),
    ("bgrt", "BGRT — firmware OEM logo"),
    ("fade-in", "Fade-in logo"),
    ("glow", "Glow"),
    ("solar", "Solar flares"),
    ("spinfinity", "Spinfinity"),
    ("tribar", "Text-mode tribar"),
    ("text", "Text boot messages"),
    ("details", "Verbose details"),
    ("script", "Script engine demo"),
]

PLYMOUTH_THEME_PACKAGES: dict[str, Pkg] = {
    "breeze": Pkg("kde-plasma/breeze-plymouth", "Breeze (Plasma) Plymouth theme"),
    "dracula": Pkg("sys-boot/plymouth-dracula-theme", "Dracula Plymouth theme", "wayland-desktop"),
    "openrc-plugin": Pkg("sys-boot/plymouth-openrc-plugin", "OpenRC framebuffer status plugin"),
}


def plymouth_theme_choices() -> list[tuple[str, str]]:
    """Builtin theme names plus extra theme packages (breeze/dracula)."""
    rows = list(PLYMOUTH_BUILTIN_THEMES)
    rows.append(("breeze", "Breeze — KDE Plasma theme package"))
    rows.append(("dracula", "Dracula — overlay theme (::wayland-desktop)"))
    return rows


def plymouth_packages_for(theme_ids: list[str], want_openrc_plugin: bool = False) -> list[Pkg]:
    """Map selected theme IDs to extra ebuilds (builtin themes need no package)."""
    pkgs = []
    for tid in theme_ids:
        if tid in PLYMOUTH_THEME_PACKAGES:
            pkgs.append(PLYMOUTH_THEME_PACKAGES[tid])
    if want_openrc_plugin:
        pkgs.append(PLYMOUTH_THEME_PACKAGES["openrc-plugin"])
    return pkgs


# Handbook default is GRUB. Others are optional extras the user may install together.
BOOTLOADERS = [
    ("grub", "GRUB — handbook default, BIOS + UEFI"),
    ("systemd-boot", "systemd-boot — UEFI simple loader"),
    ("refind", "rEFInd — graphical UEFI boot manager"),
    ("efistub", "EFI stub / UKI — kernel as EFI binary"),
    ("syslinux", "Syslinux — BIOS"),
    ("limine", "Limine — UEFI copies BOOT*.EFI; BIOS uses bios-install"),
    ("lilo", "LILO — legacy BIOS (discouraged)"),
]

# First page of the overlay checklist — the ones WM/Steam/science users actually need.
FEATURED_OVERLAYS: list[tuple[str, str]] = [
    ("wayland-desktop", "Wayland compositors and tools not yet in ::gentoo"),
    ("hyproverlay", "Hyprland and Hypr* utilities"),
    ("CachyOS-kernels", "CachyOS dist-kernel and sources (cachyos-kernel / -bin)"),
    ("steam-overlay", "Steam client and Steam-based games"),
    ("gentoo-zh", "Packages useful for Chinese-speaking users"),
    ("science", "Experimental scientific packages"),
    ("kde", "KDE team testing overlay"),
    ("qt", "Qt team testing overlay"),
    ("haskell", "Official Haskell overlay"),
    ("emacs", "Experimental Emacs ebuilds"),
    ("java", "Java overlay"),
    ("ruby", "Experimental Ruby ebuilds"),
    ("pentoo", "Security / pentest tools"),
    ("audio-overlay", "Pro-audio packages"),
    ("mv", "mv overlay — extra / live ebuilds"),
    ("gentoo", "Skip — ::gentoo is always present"),
]


# Handbook ch. 6 USE flags. Value is the flag written to make.conf; the
# description is what the wizard shows so you know why you would enable it.
USE_FLAGS: list[tuple[str, str]] = [
    ("X", "X11 — classic graphical stack (most desktops still need this)"),
    ("wayland", "Wayland — modern display protocol (Sway, Hyprland, GNOME/KDE Wayland)"),
    ("gtk", "GTK — GNOME/Xfce/MATE toolkits"),
    ("qt5", "Qt5 — older KDE/Qt applications"),
    ("qt6", "Qt6 — current KDE Plasma / Qt applications"),
    ("opengl", "OpenGL — 3D acceleration"),
    ("vulkan", "Vulkan — modern GPU API"),
    ("alsa", "ALSA — Linux sound drivers and mixing"),
    ("pulseaudio", "PulseAudio — compatibility layer (often with PipeWire)"),
    ("pipewire", "PipeWire — current audio/video server"),
    ("jack", "JACK — low-latency pro audio"),
    ("bluetooth", "Bluetooth — adapters, audio, input"),
    ("networkmanager", "NetworkManager — desktop networking (Wi-Fi, VPN, Ethernet)"),
    ("wifi", "wifi — extra wireless support in some packages"),
    ("dbus", "D-Bus — desktop/session message bus"),
    ("udev", "udev — device events (almost always wanted)"),
    ("elogind", "elogind — logind for OpenRC desktops (seat, power, idle)"),
    ("systemd", "systemd USE — only for systemd stages/profiles"),
    ("cups", "CUPS — printing"),
    ("pam", "PAM — pluggable authentication"),
    ("ssl", "ssl — TLS/HTTPS in clients and servers"),
    ("python", "python — Python bindings where optional"),
    ("nls", "nls — native-language menus; translations still limited by LINGUAS/L10N"),
    ("dist-kernel", "dist-kernel — distribution kernel helpers (installkernel, modules)"),
]

COMMON_LOCALES: list[tuple[str, str]] = [
    ("en_US.UTF-8", "English (United States) UTF-8"),
    ("en_GB.UTF-8", "English (United Kingdom) UTF-8"),
    ("de_DE.UTF-8", "German (Germany) UTF-8"),
    ("fr_FR.UTF-8", "French (France) UTF-8"),
    ("es_ES.UTF-8", "Spanish (Spain) UTF-8"),
    ("es_MX.UTF-8", "Spanish (Mexico) UTF-8"),
    ("it_IT.UTF-8", "Italian (Italy) UTF-8"),
    ("pt_BR.UTF-8", "Portuguese (Brazil) UTF-8"),
    ("pt_PT.UTF-8", "Portuguese (Portugal) UTF-8"),
    ("nl_NL.UTF-8", "Dutch (Netherlands) UTF-8"),
    ("pl_PL.UTF-8", "Polish (Poland) UTF-8"),
    ("ru_RU.UTF-8", "Russian (Russia) UTF-8"),
    ("uk_UA.UTF-8", "Ukrainian (Ukraine) UTF-8"),
    ("cs_CZ.UTF-8", "Czech (Czechia) UTF-8"),
    ("sv_SE.UTF-8", "Swedish (Sweden) UTF-8"),
    ("nb_NO.UTF-8", "Norwegian Bokmål UTF-8"),
    ("fi_FI.UTF-8", "Finnish (Finland) UTF-8"),
    ("da_DK.UTF-8", "Danish (Denmark) UTF-8"),
    ("ja_JP.UTF-8", "Japanese (Japan) UTF-8"),
    ("ko_KR.UTF-8", "Korean (Korea) UTF-8"),
    ("zh_CN.UTF-8", "Chinese simplified (China) UTF-8"),
    ("zh_TW.UTF-8", "Chinese traditional (Taiwan) UTF-8"),
    ("ar_EG.UTF-8", "Arabic (Egypt) UTF-8"),
    ("he_IL.UTF-8", "Hebrew (Israel) UTF-8"),
    ("hi_IN.UTF-8", "Hindi (India) UTF-8"),
    ("tr_TR.UTF-8", "Turkish (Turkey) UTF-8"),
    ("C.UTF-8", "C.UTF-8 — POSIX Unicode, no translations"),
    ("custom", "Type a different glibc locale name"),
]


def extra_overlays() -> list[tuple[str, str]]:
    """More names from overlays.gentoo.org (shown after the featured list)."""
    names = [
        "asahi", "bitcoin", "brother-overlay", "calculate", "crossdev", "dlang",
        "electron", "elementary", "enlightenment-live", "gentoo-zh", "libressl",
        "librewolf", "linux-surface", "loongson", "mate-de-gentoo", "melpa",
        "mysql", "palemoon", "parona-overlay", "php-overlay", "proaudio-gentoo",
        "pypi", "R_Overlay", "riscv", "ros-overlay", "sage-on-gentoo",
        "src_prepare-overlay", "suckless-extra", "tex-overlay", "toolchain",
        "torbrowser", "ukui", "vapoursynth", "wine", "without-systemd", "x11",
        "xlibre", "zugaina", "pf4public", "tatsh-overlay", "thegreatmcpain",
        "sad", "sorrow", "yellow", "oddlama", "pentoo", "steam-overlay",
        "wayland-desktop", "hyproverlay", "science", "kde", "qt",
    ]
    featured = {n for n, _ in FEATURED_OVERLAYS}
    return [
        (n, f"Community overlay from overlays.gentoo.org — `eselect repository enable {n}`")
        for n in sorted(set(names))
        if n not in featured
    ]


def overlay_choices() -> list[tuple[str, str]]:
    rows = [ov for ov in FEATURED_OVERLAYS if ov[0] not in {"gentoo", "guru"}]
    rows.extend(ov for ov in extra_overlays() if ov[0] != "guru")
    return rows


# Wizard chapter 12 categories. Each tuple is (id, title, checklist rows).
# Keep window-managers first; wizard.py treats that category separately.

def _pkgs(*items: Pkg) -> list[tuple[str, str]]:
    """Checklist rows. Packages from the GURU overlay are not options."""
    return [_p(p.atom, p.desc, p.overlay) for p in items if p.overlay != "guru"]


CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "window-managers",
        "Window managers (Hyprland, Sway, dwl, dwm, i3)",
        wm_choices(),
    ),
    (
        "wayland-common",
        "Utilities that work with every Wayland compositor",
        _pkgs(*WAYLAND_COMMON_UTILS),
    ),
    (
        "suckless",
        "Suckless tools",
        _pkgs(
            Pkg("x11-wm/dwm", "dwm — also offered with the window managers"),
            Pkg("x11-misc/dmenu", "dmenu"),
            Pkg("x11-terms/st", "st — simple terminal"),
            Pkg("x11-misc/slock", "slock"),
            Pkg("x11-misc/slstatus", "slstatus"),
            Pkg("www-client/surf", "surf — suckless webkit browser"),
            Pkg("x11-misc/tabbed", "tabbed"),
        ),
    ),
    (
        "app-stores",
        "App stores (Flatpak, Bazaar, AppImage)",
        _pkgs(
            Pkg("sys-apps/flatpak", "Flatpak — sandboxed apps; Flathub is added after emerge"),
            Pkg(
                "flatpak-app/io.github.kolunmi.Bazaar",
                "Bazaar — Flatpak/Flathub app store (installed from Flathub, not Portage)",
            ),
            Pkg("sys-fs/fuse:0", "FUSE 2 — libfuse.so.2, required to run most AppImages"),
            Pkg("sys-fs/fuse", "FUSE 3 — newer FUSE userspace (AppImages still need fuse:0)"),
        ),
    ),
    (
        "display-managers",
        "Display / login managers",
        _pkgs(
            Pkg("gui-libs/greetd", "greetd — login daemon used for Wayland sessions"),
            Pkg("gui-apps/gtkgreet", "gtkgreet — GTK greeter for greetd"),
            Pkg("x11-misc/lightdm", "LightDM"),
            Pkg("x11-misc/sddm", "SDDM"),
            Pkg("x11-misc/slim", "SLiM — legacy simple login manager"),
        ),
    ),
    (
        "browsers",
        "Web browsers",
        _pkgs(
            Pkg("www-client/firefox-bin", "Firefox — official binary (no compile)"),
            Pkg("www-client/firefox", "Firefox — compile from source (hours)"),
            Pkg("www-client/google-chrome", "Google Chrome — official binary"),
            Pkg("www-client/librewolf", "LibreWolf — privacy-forked Firefox", "librewolf"),
            Pkg("www-client/qutebrowser", "qutebrowser — keyboard-driven Qt browser"),
            Pkg("www-client/falkon", "Falkon — KDE Qt WebEngine browser"),
            Pkg("www-client/epiphany", "GNOME Web — GTK WebKit browser"),
            Pkg("www-client/w3m", "w3m — text browser with images in a terminal"),
            Pkg("www-client/lynx", "Lynx — classic text-only browser"),
            Pkg("www-client/links", "Links — text/graphical lightweight browser"),
        ),
    ),
    (
        "editors",
        "Text editors",
        _pkgs(
            Pkg("app-editors/neovim", "Neovim"),
            Pkg("app-editors/vim", "Vim"),
            Pkg("app-editors/gvim", "gVim — Vim with a GUI"),
            Pkg("app-editors/emacs", "GNU Emacs"),
            Pkg("app-editors/nano", "nano"),
            Pkg("app-editors/helix", "Helix — modal editor"),
            Pkg("app-editors/kakoune", "Kakoune"),
            Pkg("app-editors/micro", "micro — friendly terminal editor"),
            Pkg("app-editors/sublime-text", "Sublime Text — proprietary"),
            Pkg("app-editors/kate", "Kate"),
            Pkg("app-editors/gedit", "gedit"),
            Pkg("app-editors/gnome-text-editor", "GNOME Text Editor"),
            Pkg("app-editors/featherpad", "FeatherPad"),
            Pkg("app-editors/mousepad", "Mousepad"),
            Pkg("app-editors/kile", "Kile — LaTeX"),
            Pkg("app-editors/ghostwriter", "ghostwriter — Markdown"),
            # Atom is discontinued; Pulsar is the community fork (Flathub, not Portage).
            Pkg(
                "flatpak-app/dev.pulsar_edit.Pulsar",
                "Pulsar — Atom successor (Flatpak; Atom itself is discontinued)",
            ),
            # Obsidian is not in ::gentoo; install from Flathub after Flatpak is queued.
            Pkg(
                "flatpak-app/md.obsidian.Obsidian",
                "Obsidian — Markdown knowledge base (Flatpak)",
            ),
            Pkg("app-editors/logseq-desktop-bin", "Logseq — open-source knowledge base (binary)"),
        ),
    ),
    (
        "ides",
        "IDEs (2026)",
        _pkgs(
            Pkg("app-editors/vscode", "Visual Studio Code — Microsoft binary"),
            Pkg("app-editors/vscodium", "VSCodium — open-source VS Code builds"),
            Pkg("app-editors/zed", "Zed — high-performance GUI editor"),
            Pkg("dev-util/geany", "Geany — lightweight IDE"),
            # JetBrains IDEs are not in ::gentoo; offer official Flathub apps.
            Pkg(
                "flatpak-app/com.jetbrains.IntelliJ-IDEA-Community",
                "IntelliJ IDEA Community (Flatpak)",
            ),
            Pkg(
                "flatpak-app/com.jetbrains.PyCharm-Community",
                "PyCharm Community (Flatpak)",
            ),
            Pkg(
                "flatpak-app/com.jetbrains.WebStorm",
                "WebStorm (Flatpak — proprietary JetBrains IDE)",
            ),
        ),
    ),
    (
        "terminals",
        "Terminal emulators",
        _pkgs(
            Pkg("x11-terms/alacritty", "Alacritty"),
            Pkg("x11-terms/kitty", "Kitty"),
            Pkg("x11-terms/ghostty", "Ghostty"),
            Pkg("gui-apps/foot", "foot"),
            Pkg("x11-terms/rxvt-unicode", "urxvt"),
            Pkg("x11-terms/xterm", "xterm"),
            Pkg("x11-terms/xfce4-terminal", "Xfce Terminal"),
            Pkg("kde-apps/konsole", "Konsole"),
            Pkg("x11-terms/gnome-terminal", "GNOME Terminal"),
            Pkg("x11-terms/mlterm", "mlterm"),
        ),
    ),
    (
        "shells-cli",
        "Shells, CLI, and Portage tools",
        _pkgs(
            Pkg("app-shells/zsh", "Zsh"),
            Pkg("app-shells/fish", "fish"),
            Pkg("app-shells/bash-completion", "bash-completion"),
            Pkg("app-shells/starship", "Starship prompt"),
            Pkg("app-misc/tmux", "tmux"),
            Pkg("app-misc/screen", "GNU Screen"),
            Pkg("sys-apps/eza", "eza (ls)"),
            Pkg("sys-apps/bat", "bat"),
            Pkg("sys-apps/ripgrep", "ripgrep"),
            Pkg("sys-apps/fd", "fd"),
            Pkg("app-misc/neofetch", "neofetch"),
            Pkg("app-portage/gentoolkit", "gentoolkit (equery, eclean)"),
            Pkg("app-portage/eix", "eix"),
            Pkg("app-portage/flaggie", "flaggie"),
            Pkg("app-eselect/eselect-repository", "eselect-repository"),
            Pkg("app-portage/cpuid2cpuflags", "cpuid2cpuflags"),
            Pkg("app-text/wgetpaste", "wgetpaste"),
        ),
    ),
    (
        "audio-video",
        "Audio & video",
        _pkgs(
            Pkg("media-video/pipewire", "PipeWire"),
            Pkg("media-video/wireplumber", "WirePlumber"),
            Pkg("media-sound/pulseaudio", "PulseAudio"),
            Pkg("media-sound/alsa-utils", "alsa-utils"),
            Pkg("media-sound/pavucontrol", "pavucontrol"),
            Pkg("media-video/mpv", "mpv"),
            Pkg("media-video/vlc", "VLC"),
            Pkg("media-video/ffmpeg", "FFmpeg"),
            Pkg("media-sound/audacious", "Audacious"),
            Pkg("media-sound/strawberry", "Strawberry"),
            Pkg("media-gfx/gimp", "GIMP"),
            Pkg("media-gfx/inkscape", "Inkscape"),
            Pkg("media-gfx/imv", "imv image viewer"),
            Pkg("media-gfx/feh", "feh"),
        ),
    ),
    (
        "office-docs",
        "Office & documents",
        _pkgs(
            Pkg("app-office/libreoffice-bin", "LibreOffice binary"),
            Pkg("app-office/libreoffice", "LibreOffice from source"),
            Pkg("app-text/zathura", "Zathura"),
            Pkg("app-text/evince", "Evince"),
            Pkg("kde-apps/okular", "Okular"),
            Pkg("app-text/pandoc-bin", "Pandoc binary"),
            Pkg("app-text/texlive", "TeX Live"),
        ),
    ),
    (
        "networking",
        "Networking & remote access",
        _pkgs(
            Pkg("net-misc/networkmanager", "NetworkManager"),
            Pkg("net-misc/dhcpcd", "dhcpcd"),
            Pkg("net-vpn/wireguard-tools", "WireGuard tools"),
            Pkg("net-vpn/openvpn", "OpenVPN"),
            Pkg("net-misc/openssh", "OpenSSH"),
            Pkg("net-misc/chrony", "chrony"),
            Pkg("net-misc/wget", "wget"),
            Pkg("net-misc/curl", "curl"),
            Pkg("net-misc/rsync", "rsync"),
            Pkg("net-ftp/filezilla", "FileZilla"),
            Pkg("net-im/telegram-desktop-bin", "Telegram Desktop bin"),
            Pkg("net-im/signal-desktop-bin", "Signal"),
            Pkg("net-analyzer/wireshark", "Wireshark"),
            Pkg("net-firewall/ufw", "ufw"),
            Pkg("net-firewall/nftables", "nftables"),
        ),
    ),
    (
        "containers-virt",
        "Containers & virtualisation",
        _pkgs(
            Pkg("app-containers/docker", "Docker"),
            Pkg("app-containers/docker-cli", "Docker CLI"),
            Pkg("app-containers/containerd", "containerd"),
            Pkg("app-containers/podman", "Podman"),
            Pkg("app-emulation/qemu", "QEMU"),
            Pkg("app-emulation/libvirt", "libvirt"),
            Pkg("app-emulation/virt-manager", "virt-manager"),
            Pkg("app-emulation/virtualbox", "VirtualBox"),
            Pkg("sys-kernel/linux-headers", "linux-headers"),
        ),
    ),
    (
        "dev",
        "Development toolchains",
        _pkgs(
            Pkg("dev-vcs/git", "Git"),
            Pkg("dev-lang/python", "Python"),
            Pkg("dev-lang/rust-bin", "Rust (binary)"),
            Pkg("dev-lang/go", "Go"),
            Pkg("dev-lang/php", "PHP"),
            Pkg("dev-db/sqlite", "SQLite"),
            Pkg("dev-db/postgresql", "PostgreSQL"),
            Pkg("dev-db/mariadb", "MariaDB"),
            Pkg("sys-devel/gdb", "GDB"),
            Pkg("dev-util/strace", "strace"),
            Pkg("dev-util/cmake", "CMake"),
            Pkg("dev-util/meson", "Meson"),
            Pkg("dev-util/pkgconf", "pkgconf"),
        ),
    ),
    (
        "icon-themes",
        "Icon themes",
        _pkgs(
            Pkg("x11-themes/papirus-icon-theme", "Papirus icons"),
            Pkg("x11-themes/adwaita-icon-theme", "Adwaita icons"),
            Pkg("x11-themes/tela-icon-theme", "Tela icons"),
            Pkg("x11-themes/numix-icon-theme-circle", "Numix Circle icons"),
            Pkg("kde-frameworks/breeze-icons", "Breeze icons"),
        ),
    ),
    (
        "gtk-themes",
        "GTK themes",
        _pkgs(
            Pkg("x11-themes/arc-theme", "Arc"),
            Pkg("x11-themes/greybird", "Greybird"),
            Pkg("x11-themes/numix-gtk-theme", "Numix"),
            Pkg("x11-themes/clearlooks-phenix", "Clearlooks Phenix"),
            Pkg("x11-themes/adapta-gtk-theme", "Adapta"),
        ),
    ),
    (
        "qt-themes",
        "Qt themes",
        _pkgs(
            Pkg("x11-themes/kvantum", "Kvantum — SVG theme engine"),
            Pkg("x11-themes/adwaita-qt", "Adwaita Qt"),
            Pkg("x11-themes/qtcurve", "QtCurve"),
            Pkg("kde-plasma/breeze", "Breeze Qt style"),
        ),
    ),
    (
        "fonts",
        "Fonts",
        _pkgs(
            Pkg("media-fonts/fonts-meta", "fonts-meta"),
            Pkg("media-fonts/noto", "Noto"),
            Pkg("media-fonts/noto-emoji", "Noto Emoji"),
            Pkg("media-fonts/noto-cjk", "Noto CJK"),
            Pkg("media-fonts/hack", "Hack"),
            Pkg("media-fonts/jetbrains-mono", "JetBrains Mono"),
            Pkg("media-fonts/fira-code", "Fira Code"),
            Pkg("media-fonts/fira-mono", "Fira Mono"),
            Pkg("media-fonts/fira-sans", "Fira Sans"),
            Pkg("media-fonts/iosevka", "Iosevka"),
            Pkg("media-fonts/cascadia-code", "Cascadia Code"),
            Pkg("media-fonts/fantasque-sans-mono", "Fantasque Sans Mono"),
            Pkg("media-fonts/liberation-fonts", "Liberation"),
            Pkg("media-fonts/dejavu", "DejaVu"),
            Pkg("media-fonts/roboto", "Roboto"),
            Pkg("media-fonts/cantarell", "Cantarell"),
            Pkg("media-fonts/droid", "Droid"),
            Pkg("media-fonts/croscorefonts", "Croscore"),
            Pkg("media-fonts/crosextrafonts-carlito", "Carlito"),
            Pkg("media-fonts/crosextrafonts-caladea", "Caladea"),
            Pkg("media-fonts/fontawesome", "Font Awesome"),
            Pkg("media-fonts/terminus-font", "Terminus"),
            Pkg("media-fonts/unifont", "Unifont"),
        ),
    ),
    (
        "games",
        "Games & Steam",
        _pkgs(
            Pkg("games-util/steam-launcher", "Steam", "steam-overlay"),
            Pkg("games-util/gamemode", "GameMode"),
            Pkg("games-puzzle/gnome-tetravex", "GNOME Tetravex"),
            Pkg("kde-apps/kpat", "KPatience"),
            Pkg("kde-apps/kmines", "KMines"),
            Pkg("kde-apps/kbounce", "KBounce"),
        ),
    ),
    (
        "system",
        "System administration",
        _pkgs(
            Pkg("sys-process/htop", "htop"),
            Pkg("sys-process/btop", "btop"),
            Pkg("sys-apps/smartmontools", "smartmontools"),
            Pkg("sys-apps/usbutils", "usbutils"),
            Pkg("sys-apps/pciutils", "pciutils"),
            Pkg("sys-fs/ncdu", "ncdu"),
            Pkg("app-admin/sudo", "sudo"),
            Pkg("app-admin/doas", "doas"),
            Pkg("app-admin/sysklogd", "sysklogd"),
            Pkg("app-admin/rsyslog", "rsyslog"),
            Pkg("sys-process/cronie", "cronie"),
            Pkg("app-backup/restic", "restic"),
            Pkg("app-backup/borgbackup", "Borg"),
            Pkg("sys-apps/flatpak", "Flatpak (also listed under App stores)"),
        ),
    ),
]


@dataclass(frozen=True)
class DotfileChoice:
    """One compositor config the installer can place in the user's home.

    `repo` empty means the installer's own config. A GitHub choice copies
    `source` only. The project's install script is never run.
    `kind` is "dir" (archive subdir) or "file" (one raw file).
    """

    id: str
    label: str
    repo: str = ""
    branch: str = "main"
    source: str = ""
    dest: str = ""
    kind: str = "dir"
    # Gentoo atoms the config launches. Empty for the installer default.
    # Programs that are not in the Gentoo tree are omitted on purpose.
    packages: tuple[str, ...] = ()


# Exactly two choices per compositor: installer default plus the top-rated
# GitHub config. Star counts checked on GitHub, 2026-10-25. Paths are the
# compositor config only (not waybar, the shell, or the upstream install script).
WM_DOTFILES: dict[str, tuple[DotfileChoice, ...]] = {
    "gui-wm/hyprland": (
        DotfileChoice("installer", "Installer default — keymap and a short Hyprland config"),
        DotfileChoice(
            "end-4",
            "end-4/dots-hyprland — most-starred Hyprland dots",
            "end-4/dots-hyprland",
            "main",
            "dots/.config/hypr",
            ".config/hypr",
            packages=(
                "gnome-base/gnome-keyring",
                "media-sound/easyeffects",
                "media-sound/playerctl",
                "media-sound/cava",
                "gui-apps/foot",
                "x11-terms/kitty",
                "x11-terms/alacritty",
                "kde-apps/dolphin",
                "xfce-base/thunar",
                "www-client/firefox-bin",
                "app-shells/fish",
                "media-fonts/jetbrains-mono",
                "sys-process/btop",
                "app-misc/jq",
            ),
        ),
    ),
    "gui-wm/niri": (
        DotfileChoice("installer", "Installer default — keymap, binds, and the packages you selected"),
        DotfileChoice(
            "upstream",
            "niri-wm/niri — upstream default config",
            "niri-wm/niri",
            "main",
            "resources/default-config.kdl",
            ".config/niri/config.kdl",
            "file",
            (
                "gui-apps/waybar",
                "x11-terms/alacritty",
                "gui-apps/swaylock",
            ),
        ),
    ),
}

# Hard cap so a longer WM_DOTFILES entry still only offers two options.
_MAX_DOTFILE_CHOICES = 2


def dotfile_choices(atom: str) -> list[DotfileChoice]:
    """Top two config choices for this compositor, or none."""
    return list(WM_DOTFILES.get(atom, ()))[:_MAX_DOTFILE_CHOICES]


def without_already_selected(
    rows: list[tuple[str, str]], already: set[str]
) -> list[tuple[str, str]]:
    """Drop checklist rows whose atoms were chosen in an earlier step."""
    if not already:
        return list(rows)
    return [(atom, desc) for atom, desc in rows if atom not in already]


# Noctalia and DMS already provide the bar, launcher, and their own config.
DOTFILE_SHELLS_SKIP = frozenset({"noctalia", "dms"})


def dotfiles_skipped(shell: str) -> bool:
    """True when a shell makes the GitHub compositor configs the wrong session."""
    return shell in DOTFILE_SHELLS_SKIP


def selected_dotfile(atom: str, wm_dotfiles: list[str]) -> DotfileChoice | None:
    """The saved choice for this compositor, or the installer default."""
    choices = dotfile_choices(atom)
    if not choices:
        return None
    wanted = "installer"
    for item in wm_dotfiles or []:
        key, _, value = str(item).partition("=")
        if key == atom and value:
            wanted = value
            break
    for choice in choices:
        if choice.id == wanted:
            return choice
    return choices[0]


def dotfile_package_atoms(shell: str, window_managers: list[str], wm_dotfiles: list[str]) -> list[str]:
    """Gentoo packages the chosen GitHub configs launch. Empty when a shell skips them."""
    if dotfiles_skipped(shell):
        return []
    out: list[str] = []
    for atom in window_managers:
        choice = selected_dotfile(atom, wm_dotfiles)
        if choice is None or not choice.repo:
            continue
        for pkg in choice.packages:
            if pkg not in out:
                out.append(pkg)
    return out


def _known_packages() -> list[Pkg]:
    pkgs = list(WINDOW_MANAGERS) + list(WAYLAND_BASE) + list(WAYLAND_COMMON_UTILS)
    pkgs.extend(KERNEL_OPTIONS)
    pkgs.extend(PLYMOUTH_THEME_PACKAGES.values())
    for group in list(WM_REQUIREMENTS.values()) + list(SHELL_PACKAGES.values()):
        pkgs.extend(group)
    pkgs.append(NOCTALIA_GREETER)
    return pkgs


def atom_overlay(atom: str) -> str:
    """Which eselect overlay an atom needs, or 'gentoo' for the main tree."""
    bare = atom.split("::", 1)[0]
    if bare in REMOVED_GURU_ATOMS:
        return "guru"
    if atom.startswith("flatpak-app/"):
        return "gentoo"
    if bare.startswith("sys-kernel/"):
        ov = kernel_overlay(bare)
        if ov != "gentoo":
            return ov
    for pkg in _known_packages():
        if pkg.atom == atom or pkg.atom == bare:
            return pkg.overlay
    for _, _, pkgs in CATEGORIES:
        for value, desc in pkgs:
            if value == atom and "::" in desc:
                return desc.split("::")[-1].strip()
    return "gentoo"


def overlays_required_by(atoms: list[str], extra_overlays: list[str]) -> list[str]:
    """Union of user-picked overlays and overlays implied by selected atoms."""
    need = set(extra_overlays)
    for atom in atoms:
        ov = atom_overlay(atom)
        if ov and ov != "gentoo":
            need.add(ov)
        if "::" in atom:
            need.add(atom.split("::", 1)[1])
    need.discard("gentoo")
    return sorted(need)
