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


# Compositors offered by the wizard. GURU-only compositors (niri, river, …)
# are not options. Hyprland stays on hyproverlay, which is not GURU.

WAYLAND_COMPOSITORS: list[Pkg] = [
    Pkg("gui-wm/hyprland", "Hyprland — dynamic tiling Wayland compositor", "hyproverlay"),
    Pkg("gui-wm/sway", "Sway — i3-compatible wlroots compositor"),
    Pkg("gui-wm/wayfire", "Wayfire — stacking Wayland compositor"),
    Pkg("gui-wm/labwc", "Labwc — Openbox-style stacking compositor"),
    Pkg("gui-wm/dwl", "dwl — suckless dwm for Wayland"),
    Pkg("x11-wm/qtile", "Qtile — Python WM on Wayland"),
    Pkg("dev-libs/weston", "Weston — Wayland reference compositor"),
    Pkg("gui-wm/gamescope", "Gamescope — micro-compositor"),
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

SHELL_EXEC: dict[str, str] = {
    "noctalia": "noctalia-shell",
    "dms": "dms",
}

# Previously offered from GURU. They are not menu options, and a saved profile
# or a typed atom must not emerge them. The selected shell is the exception.
REMOVED_GURU_ATOMS = frozenset(
    {
        "gui-wm/niri",
        "gui-wm/swayfx",
        "gui-wm/river",
        "gui-wm/cage",
        "gui-wm/mangowm",
        "gui-wm/miracle-wm",
        "gui-apps/fuzzel",
        "gui-apps/swaylock-effects",
        "gui-apps/xwayland-satellite",
        "gui-apps/nwg-look",
        "x11-misc/ly",
        "gui-apps/tuigreet",
        "www-client/brave-bin",
        "app-editors/helix",
        "app-editors/vscode",
        "app-editors/vscodium",
        "app-misc/fastfetch",
        "app-office/onlyoffice-bin",
        "net-im/discord",
        "games-util/mangohud",
        "games-emulation/retroarch",
        "app-emulation/distrobox",
    }
)

# Offered packages that are still ~arch on a stable system. Keywording them
# keeps one masked atom from failing its emerge.
TESTING_ATOMS = frozenset(
    {
        "gui-wm/gamescope",
        "x11-themes/kvantum",
        "x11-themes/tela-icon-theme",
        "x11-themes/numix-icon-theme-circle",
        "media-fonts/iosevka",
    }
)

# Installed for every selected Wayland compositor. These work on Hyprland,
# Sway, Wayfire, labwc, dwl, and Qtile. They are not a separate checklist.
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


def packages_for_selection(wm_atoms: list[str], shell: str, pipewire: bool) -> list[Pkg]:
    """Necessities for the chosen compositors, plus the shell if one was chosen.

    Shared Wayland utilities are included once. GURU atoms are limited to the
    selected shell.
    """
    allowed_guru = set(shell_atoms(shell))
    pkgs: list[Pkg] = []
    selected = [atom for atom in wm_atoms if atom]
    if wayland_selected(selected) or shell in SHELL_PACKAGES:
        pkgs.extend(WAYLAND_BASE)
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
        "Editors & IDEs",
        _pkgs(
            Pkg("app-editors/neovim", "Neovim"),
            Pkg("app-editors/vim", "Vim"),
            Pkg("app-editors/emacs", "GNU Emacs"),
            Pkg("app-editors/nano", "nano"),
            Pkg("dev-util/geany", "Geany"),
            Pkg("app-editors/kate", "Kate"),
            Pkg("app-editors/gedit", "gedit"),
            Pkg("app-editors/kile", "Kile"),
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


def _known_packages() -> list[Pkg]:
    pkgs = list(WINDOW_MANAGERS) + list(WAYLAND_BASE) + list(WAYLAND_COMMON_UTILS)
    pkgs.extend(PLYMOUTH_THEME_PACKAGES.values())
    for group in list(WM_REQUIREMENTS.values()) + list(SHELL_PACKAGES.values()):
        pkgs.extend(group)
    return pkgs


def atom_overlay(atom: str) -> str:
    """Which eselect overlay an atom needs, or 'gentoo' for the main tree."""
    bare = atom.split("::", 1)[0]
    if bare in REMOVED_GURU_ATOMS:
        return "guru"
    if atom.startswith("flatpak-app/"):
        return "gentoo"
    for pkg in _known_packages():
        if pkg.atom == atom:
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
