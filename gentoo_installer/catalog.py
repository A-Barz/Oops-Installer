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


# Wayland compositors live in gui-wm (and a few DE packages). Many still
# require GURU / hyproverlay / wayland-desktop rather than ::gentoo.

WAYLAND_COMPOSITORS: list[Pkg] = [
    Pkg("gui-wm/hyprland", "Hyprland — dynamic tiling Wayland compositor", "hyproverlay"),
    Pkg("gui-wm/niri", "Niri — scrollable-tiling Wayland compositor", "guru"),
    Pkg("gui-wm/sway", "Sway — i3-compatible wlroots compositor"),
    Pkg("gui-wm/swayfx", "SwayFX — Sway with extra eye candy", "guru"),
    Pkg("gui-wm/wayfire", "Wayfire — Compiz-like stacking compositor"),
    Pkg("gui-wm/labwc", "Labwc — Openbox-inspired stacking compositor"),
    Pkg("gui-wm/dwl", "dwl — dwm clone for Wayland (wlroots)"),
    Pkg("gui-wm/river", "River — dynamic compositor with separate WM", "guru"),
    Pkg("gui-wm/cage", "Cage — kiosk compositor (single fullscreen app)", "guru"),
    Pkg("gui-wm/cagebreak", "Cagebreak — Ratpoison-like tiling compositor", "wayland-desktop"),
    Pkg("gui-wm/kiwmi", "Kiwmi — Lua-configurable compositor", "wayland-desktop"),
    Pkg("gui-wm/mangowm", "MangoWM — lightweight wlroots compositor", "guru"),
    Pkg("gui-wm/miracle-wm", "Miracle — Mir-based tiling compositor", "guru"),
    Pkg("gui-wm/newm", "newm — laptop/touchpad-oriented compositor", "wayland-desktop"),
    Pkg("gui-wm/waybox", "Waybox — Openbox clone for Wayland", "wayland-desktop"),
    Pkg("gui-liri/liri-shell", "Liri Shell — Qt Wayland shell", "wayland-desktop"),
    Pkg("dev-libs/weston", "Weston — reference Wayland compositor"),
    Pkg("kde-plasma/kwin", "KWin — KDE Plasma compositor (X11+Wayland)"),
    Pkg("x11-wm/mutter", "Mutter — GNOME compositor (X11+Wayland)"),
    Pkg("x11-wm/enlightenment", "Enlightenment — stacking compositor / DE"),
    Pkg("x11-wm/qtile", "Qtile — Python WM (X11 and Wayland)"),
]

# Classic X11 WMs from the Gentoo Window manager wiki (x11-wm/*).
X11_WINDOW_MANAGERS: list[Pkg] = [
    Pkg("x11-wm/i3", "i3 — tiling window manager"),
    Pkg("x11-wm/awesome", "Awesome — dynamic WM, Lua config"),
    Pkg("x11-wm/bspwm", "bspwm — binary-space tiling WM"),
    Pkg("x11-wm/dwm", "dwm — suckless dynamic WM"),
    Pkg("x11-wm/herbstluftwm", "herbstluftwm — manual tiling WM"),
    Pkg("x11-wm/openbox", "Openbox — stacking WM"),
    Pkg("x11-wm/fluxbox", "Fluxbox — stacking WM (Blackbox fork)"),
    Pkg("x11-wm/icewm", "IceWM — lightweight stacking WM"),
    Pkg("x11-wm/jwm", "JWM — extremely lightweight WM"),
    Pkg("x11-wm/pekwm", "PekWM — dynamic stacking WM"),
    Pkg("x11-wm/ratpoison", "ratpoison — screen-like tiling WM"),
    Pkg("x11-wm/spectrwm", "spectrwm — small dynamic tiling WM"),
    Pkg("x11-wm/xmonad", "xmonad — Haskell tiling WM"),
    Pkg("x11-wm/stumpwm", "StumpWM — Common Lisp tiling WM"),
    Pkg("x11-wm/notion", "Notion — tiling tabbed WM"),
    Pkg("x11-wm/fvwm3", "FVWM3 — stacking WM"),
    Pkg("x11-wm/windowmaker", "Window Maker — GNUstep WM"),
    Pkg("x11-wm/blackbox", "Blackbox — stacking WM"),
    Pkg("x11-wm/ctwm", "CTWM — lightweight stacking WM"),
    Pkg("x11-wm/cwm", "cwm — OpenBSD stacking WM"),
    Pkg("x11-wm/evilwm", "evilwm — tiny stacking WM"),
    Pkg("x11-wm/twm", "twm — classic X WM"),
    Pkg("x11-wm/lwm", "lwm — lightweight stacking WM"),
    Pkg("x11-wm/goomwwm", "goomwwm — get out of my way WM"),
    Pkg("x11-wm/echinus", "echinus — dwm fork, tiling+floating"),
    Pkg("x11-wm/musca", "Musca — dynamic WM"),
    Pkg("x11-wm/page", "page — mouse-friendly tiling WM"),
    Pkg("x11-wm/amiwm", "amiwm — Amiga Workbench-like WM"),
    Pkg("x11-wm/aewm", "aewm — minimal dynamic WM"),
    Pkg("x11-wm/aewm++", "aewm++ — modernised aewm"),
    Pkg("x11-wm/larswm", "larswm — 9wm-based tiling WM"),
    Pkg("x11-wm/matwm2", "matwm2 — simple EWMH WM"),
    Pkg("x11-wm/oroborus", "oroborus — small fast WM"),
    Pkg("x11-wm/sithwm", "Sith WM — minimalist WM"),
    Pkg("x11-wm/windowlab", "WindowLab — small novel WM"),
    Pkg("x11-wm/wm2", "wm2 — minimalist WM"),
    Pkg("x11-wm/marco", "Marco — MATE window manager"),
    Pkg("x11-wm/muffin", "Muffin — Cinnamon window manager"),
    Pkg("xfce-base/xfwm4", "Xfwm — Xfce window manager"),
]

WINDOW_MANAGERS: list[Pkg] = WAYLAND_COMPOSITORS + X11_WINDOW_MANAGERS


def wm_choices() -> list[tuple[str, str]]:
    """Tagged rows for the window-manager checklist (Wayland vs X11)."""
    rows = []
    for p in WINDOW_MANAGERS:
        kind = "Wayland" if p in WAYLAND_COMPOSITORS else "X11"
        rows.append(_p(p.atom, f"[{kind}] {p.desc}", p.overlay))
    return rows


# Themes shipped inside sys-boot/plymouth (plymouth-set-default-theme --list).
PLYMOUTH_BUILTIN_THEMES: list[tuple[str, str]] = [
    ("spinner", "Spinner (default, clean)"),
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
    ("guru", "Official user-maintained overlay (niri, river, many gui-apps)"),
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
        "wayland-desktop", "guru", "hyproverlay", "science", "kde", "qt",
    ]
    featured = {n for n, _ in FEATURED_OVERLAYS}
    return [
        (n, f"Community overlay from overlays.gentoo.org — `eselect repository enable {n}`")
        for n in sorted(set(names))
        if n not in featured
    ]


def overlay_choices() -> list[tuple[str, str]]:
    return [ov for ov in FEATURED_OVERLAYS if ov[0] != "gentoo"] + extra_overlays()


# Wizard chapter 12 categories. Each tuple is (id, title, checklist rows).
# Keep window-managers first; wizard.py treats that category separately.

def _pkgs(*items: Pkg) -> list[tuple[str, str]]:
    return [_p(p.atom, p.desc, p.overlay) for p in items]


CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "window-managers",
        "Window managers & Wayland compositors (niri, Hyprland, Sway, i3, …)",
        wm_choices(),
    ),
    (
        "wayland-utils",
        "Wayland utilities (bars, lock, clipboard, portals)",
        _pkgs(
            Pkg("gui-apps/waybar", "Waybar status bar"),
            Pkg("gui-apps/wofi", "Wofi launcher (rofi clone)"),
            Pkg("gui-apps/wmenu", "wmenu — dmenu look for wlroots"),
            Pkg("gui-apps/foot", "foot — fast Wayland terminal"),
            Pkg("gui-apps/fuzzel", "Fuzzel launcher", "guru"),
            Pkg("gui-apps/mako", "Mako notifications"),
            Pkg("gui-apps/grim", "Grim screenshots"),
            Pkg("gui-apps/slurp", "Slurp region select"),
            Pkg("gui-apps/swappy", "Swappy screenshot editor"),
            Pkg("gui-apps/wf-recorder", "wf-recorder screen recorder"),
            Pkg("gui-apps/wl-clipboard", "wl-copy / wl-paste"),
            Pkg("gui-apps/swaybg", "SwayBG wallpaper"),
            Pkg("gui-apps/swaylock", "Swaylock"),
            Pkg("gui-apps/swayidle", "Swayidle"),
            Pkg("gui-apps/swaylock-effects", "Swaylock-effects", "guru"),
            Pkg("gui-apps/hyprpaper", "Hyprpaper wallpaper", "hyproverlay"),
            Pkg("gui-apps/hypridle", "Hypridle", "hyproverlay"),
            Pkg("gui-apps/hyprlock", "Hyprlock", "hyproverlay"),
            Pkg("gui-apps/xdg-desktop-portal-hyprland", "Hyprland XDG portal", "hyproverlay"),
            Pkg("gui-libs/xdg-desktop-portal-wlr", "wlroots XDG portal"),
            Pkg("sys-apps/xdg-desktop-portal", "XDG desktop portal"),
            Pkg("gui-apps/xwayland-satellite", "Xwayland-satellite (needed by Niri)", "guru"),
            Pkg("gui-apps/kanshi", "Kanshi output profiles"),
            Pkg("gui-apps/waypipe", "Waypipe — Wayland over SSH"),
            Pkg("gui-apps/wayvnc", "WayVNC server"),
            Pkg("dev-libs/wayland", "Wayland protocol libraries"),
            Pkg("dev-qt/qtwayland", "Qt Wayland platform plugin"),
            Pkg("x11-base/xwayland", "Xwayland"),
            Pkg("gui-apps/nwg-look", "GTK settings for wlroots", "guru"),
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
        "desktop-environments",
        "Desktop environments",
        _pkgs(
            Pkg("xfce-base/xfce4-meta", "Xfce — lightweight GTK desktop (panels, file manager, settings)"),
            Pkg("kde-plasma/plasma-meta", "KDE Plasma — full Qt desktop"),
            Pkg("kde-apps/kde-apps-meta", "KDE Gear — Dolphin, Konsole, Gwenview, and other KDE apps"),
            Pkg("gnome-base/gnome", "GNOME — full GTK desktop with mutter"),
            Pkg("mate-base/mate", "MATE — traditional GTK desktop (GNOME 2 style)"),
            Pkg("gnome-extra/cinnamon", "Cinnamon — GNOME-fork desktop used by Linux Mint"),
            Pkg("lxde-base/lxde-meta", "LXDE — very light GTK2 desktop"),
            Pkg("lxqt-base/lxqt-meta", "LXQt — light Qt desktop"),
            Pkg("x11-wm/enlightenment", "Enlightenment — compositor plus desktop shell"),
            Pkg("pantheon-base/pantheon", "Pantheon — elementary OS desktop (if the ebuild exists)"),
        ),
    ),
    (
        "display-managers",
        "Display / login managers",
        _pkgs(
            Pkg("x11-misc/lightdm", "LightDM — greeter used by Xfce/MATE/Cinnamon"),
            Pkg("x11-misc/sddm", "SDDM — Plasma-friendly graphical login"),
            Pkg("gnome-base/gdm", "GDM — GNOME display manager"),
            Pkg("x11-misc/ly", "ly — TUI login manager (no X greeter)", "guru"),
            Pkg("gui-libs/greetd", "greetd — minimal login daemon (often with tuigreet)"),
            Pkg("gui-apps/gtkgreet", "gtkgreet — GTK greeter for greetd"),
            Pkg("gui-apps/tuigreet", "tuigreet — terminal greeter for greetd", "guru"),
            Pkg("x11-misc/slim", "SLiM — legacy simple login manager"),
        ),
    ),
    (
        "browsers",
        "Web browsers",
        _pkgs(
            Pkg("www-client/firefox-bin", "Firefox — official binary (no compile)"),
            Pkg("www-client/firefox", "Firefox — compile from source (hours)"),
            Pkg("www-client/chromium", "Chromium — open-source Chrome (long compile)"),
            Pkg("www-client/google-chrome", "Google Chrome — official binary"),
            Pkg("www-client/librewolf", "LibreWolf — privacy-forked Firefox", "librewolf"),
            Pkg("www-client/brave-bin", "Brave — Chromium-based browser binary", "guru"),
            Pkg("www-client/ungoogled-chromium", "Ungoogled Chromium — Chrome without Google services", "guru"),
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
            Pkg("app-editors/helix", "Helix", "guru"),
            Pkg("app-editors/vscode", "VS Code", "guru"),
            Pkg("app-editors/vscodium", "VSCodium", "guru"),
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
            Pkg("app-misc/fastfetch", "fastfetch", "guru"),
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
            Pkg("app-office/onlyoffice-bin", "ONLYOFFICE", "guru"),
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
            Pkg("net-im/discord", "Discord", "guru"),
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
        "fonts-themes",
        "Fonts & themes",
        _pkgs(
            Pkg("media-fonts/fonts-meta", "fonts-meta"),
            Pkg("media-fonts/noto", "Noto"),
            Pkg("media-fonts/noto-emoji", "Noto Emoji"),
            Pkg("media-fonts/hack", "Hack"),
            Pkg("media-fonts/jetbrains-mono", "JetBrains Mono"),
            Pkg("media-fonts/fira-code", "Fira Code"),
            Pkg("x11-themes/papirus-icon-theme", "Papirus icons"),
            Pkg("x11-themes/arc-theme", "Arc GTK theme"),
            Pkg("x11-themes/adwaita-icon-theme", "Adwaita icons"),
        ),
    ),
    (
        "games",
        "Games & Steam",
        _pkgs(
            Pkg("games-util/steam-launcher", "Steam", "steam-overlay"),
            Pkg("games-util/gamemode", "GameMode"),
            Pkg("games-util/mangohud", "MangoHud", "guru"),
            Pkg("games-emulation/retroarch", "RetroArch", "guru"),
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
            Pkg("app-emulation/distrobox", "Distrobox", "guru"),
        ),
    ),
]


def atom_overlay(atom: str) -> str:
    """Which eselect overlay an atom needs, or 'gentoo' for the main tree."""
    if atom.startswith("flatpak-app/"):
        return "gentoo"
    for p in WINDOW_MANAGERS + list(PLYMOUTH_THEME_PACKAGES.values()):
        if p.atom == atom:
            return p.overlay
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
