# Oops-Installer

**Oops-Installer — A Gentoo Wayland First and Suckless Setup Tool**

A terminal installer that runs on **Ubuntu Boot Image** live media and installs **Gentoo Linux** onto a disk. Questions follow the [Gentoo AMD64 Handbook](https://wiki.gentoo.org/wiki/Handbook:AMD64). Nothing is written to the target until you re-type the disk path at the end.

## Notice

This is a personal project. The name **Oops-Installer** is a poke at Gentoo: an installer misses the value of installing Gentoo by hand. That is a statement about open source. Any likeness to an original Oops-Installer for Gentoo is purely coincidental, a Freudian slip at most, and was not intended. You can do whatever you want with it. Fork a project, contribute, or make your own. Code for the sake of it.

The project is open for testing and bug reports. Try it in a virtual machine, or on a disk you can wipe, and file what you find at [GitHub Issues](https://github.com/A-Barz/Oops-Installer/issues). Include the installer revision from the banner, the choices you made, and the command that failed.

## Why Gentoo for Wayland and suckless

Oops-Installer aims at a **Wayland-first, compositor-centric** Gentoo system — not a GNOME or Plasma meta. You pick Hyprland, Sway, Niri, dwl, or similar; optional Noctalia or DMS; suckless tools; and a handbook-sized app catalogue. There is **no desktop-environment picker** and **no games category**.

Gentoo fits that intent:

- **Control** — Portage profiles, USE flags, kernels (including stable LTS `gentoo-sources` or CachyOS), and overlays (`hyproverlay`, GURU when needed) stay explicit. Binary packages are optional so a desktop install need not compile everything.
- **Minimalism** — Stage3 plus the session you chose. Shared Wayland utilities and suckless packages are offered; nothing assumes a full DE stack.
- **Performance** — Dist-kernels and binhosts when you want speed; source builds and LTO when you want to tune. Hardware probed on the live USB seeds sensible USE and `VIDEO_CARDS` defaults you can change.
- **Ownership** — Configs and emerges land on *your* disk. Hyprland/Niri can use the installer config or a top GitHub config (downloaded; upstream install scripts are never run). Dual boot is not set up: the default path wipes the disk you confirm.

Read **[HOWTO.txt](HOWTO.txt)** before a real install.

## Requirements

- Ubuntu Boot Image live USB/ISO (or another Debian-like live system with `apt` and Python 3.10+)
- Wired or wireless internet
- Root privileges
- A target disk you can **wipe** (unless you choose existing partitions)
- Optional but recommended for stage3 GPG: on the **Ubuntu Boot Image host**, install apt package **`ntpsec`** for live clock sync. It must **replace host chrony** (those Ubuntu packages conflict): `sudo apt-get remove -y chrony; sudo apt-get install -y ntpsec`. (Gentoo still uses optional `net-misc/chrony` on the installed system.)

## Usage

```bash
sudo apt-get update
sudo apt-get remove -y chrony
sudo apt-get install -y python3 git ntpsec
git clone <this-repo>
cd Oops-Installer   # or gentoo-live-installer
sudo python3 install.py
```

Useful flags:

```bash
sudo python3 install.py --dry-run
sudo python3 install.py --wizard-only --save-config ./gentoo-install.yaml
sudo python3 install.py --config ./gentoo-install.yaml
sudo python3 install.py --resume
```

Passwords, LUKS, and Wi-Fi keys are omitted from saved YAML/JSON unless you pass `--include-secrets`. See `example-config.yaml`. After disk confirmation the installer shows a full-width nap banner (no progress bar); emerge output streams until finish, then you are asked to power down and remove the USB only after the machine is off.

---

## Wizard flow (code order)

Defaults are `InstallConfig` values unless noted. Conditional steps are marked.

### Before chapter 1

1. **Banner** — Oops-Installer ASCII (Gentoo purple/green), tagline, thanks, navigation keys.
2. **NTFS gate** — If any NTFS volume exists: confirm you know what you are doing (default No).
3. **Interrupted mount** (when applicable) — Resume or unmount and restart.
4. **Loaded profile** (when `--config` / saved YAML has disk or software) — Fill missing secrets; optionally continue without re-walking chapters.
5. **Preamble** — Handbook link; wipe / VM / catalogue / TPM / Limine / swap-file warnings.

### 1 — About the installation

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Dry-run only? | yes/no | No |
| Resume interrupted install? | yes/no (skipped if already decided) | No |
| Target mountpoint | prompt | `/mnt/gentoo` |

### 2 — Installation medium (Ubuntu live host)

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| CPU architecture | `amd64`, `arm64` | `amd64` |
| Firmware | `uefi`, `bios` (UEFI bits detected) | Detected |
| Install host tools on Ubuntu? | gdisk, cryptsetup, btrfs-progs, wget, gnupg, … | Yes |

### 3 — Network (live session)

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Live network | `dhcp`, `wifi`, `static`, `skip` | `dhcp` if online else `wifi` |
| Wi-Fi / static | iface, SSID/PSK or CIDR/gateway/DNS | DNS `1.1.1.1` |
| Synchronise clock before stage3? | uses Ubuntu **`ntpsec`** (must replace **chrony**) | Yes |

### 4 — Disks

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Target disk | Detected disks (tags removable / live USB) or typed path | First disk / `/dev/sda` |
| Wipe mode | `disk`, `partitions`, `none` | `disk` |
| Partition table | UEFI → forced `gpt`; BIOS → `mbr` / `gpt` | `mbr` on BIOS |
| Layout | `handbook`, `btrfs-subvols`, `lvm`, `lvm-luks`, `mdraid1`, `zfs`, `existing` | `handbook` (or `existing` if wipe is partitions/none) |
| Root filesystem | Forced `btrfs` / `zfs` for those layouts; else `ext4`, `xfs`, `btrfs`, `f2fs`, `jfs` | `ext4` |
| Swap | On btrfs-subvols: `file`, `partition`, `zram`, `both`, `none` (note: `@swap` ≠ swap yet). Else: `partition`, `file`, `zram`, `both`, `none` | `file` / `partition` |
| Swap size | GiB | ~RAM-based (2–8) |
| ZRAM | Asked or implied by swap mode | Often yes with partition/file |
| Console keymap | e.g. `us` | `us` |
| LUKS2 | Forced on `lvm-luks`; else yes/no; passphrase; encrypt swap; TPM2 helper scripts | Off unless lvm-luks |
| ESP size | MiB (UEFI) | `1024` |

SSD detection enables weekly `fstrim`. Disks under ~40 GiB get a warning. Dual boot is not supported.

### 5 — Stage file

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Stage level | `stage3`, `stage2`, `stage1` | `stage3` |
| Init system | `openrc`, `systemd` | `openrc` |
| Install type | `desktop`, `server` (stage3 only) | `desktop` |
| Variant | `standard`, `hardened`, `musl` | `standard` |
| Verify stage GPG? | yes/no | Yes |
| Testing keywords (`*/* ~arch`)? | yes/no | No |

### 6 — Base system (Portage)

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Profile | Built from variant/init/arch (e.g. `…/23.0/desktop`) | desktop profile |
| Sync | `webrsync`, `rsync`, `git` | `webrsync` |
| Binary packages? | ABI `x86-64` / `x86-64-v3`; exact USE match? | Yes on standard; ABI from CPU |
| NVIDIA (if detected) | `nouveau`, `nvidia` | `nouveau` |
| `VIDEO_CARDS` / `INPUT_DEVICES` | prompts from live probe | auto / `libinput` (+ wacom) |
| Global USE | checklist (see below) | pre-ticks `dist-kernel`, `wayland`, … |
| Extra USE / LTO / hardened CFLAGS | prompts | LTO No; hardened forced if variant hardened |
| Mirrors / MAKEOPTS | empty = mirrorselect | jobs `0` = CPU count |

**USE checklist flags:** `X`, `wayland`, `gtk`, `qt5`, `qt6`, `opengl`, `vulkan`, `alsa`, `pulseaudio`, `pipewire`, `jack`, `bluetooth`, `networkmanager`, `wifi`, `dbus`, `udev`, `elogind`, `systemd`, `cups`, `pam`, `ssl`, `python`, `nls`, `dist-kernel`.

### 7 — Kernel

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Kernel | see table | `gentoo-kernel-bin` |
| `linux-firmware` | yes/no | Yes |
| SOF firmware | yes/no | No |
| `intel-microcode` | Auto-yes on Intel CPU; else ask | — |
| Initramfs | `dracut`, `none` | `dracut` |
| Unified Kernel Image (UKI)? | yes/no | No |

| Menu id | Emerges | Overlay |
|---------|---------|---------|
| `gentoo-kernel-bin` | `sys-kernel/gentoo-kernel-bin` | gentoo |
| `gentoo-kernel` | `sys-kernel/gentoo-kernel` | gentoo |
| `gentoo-sources-lts` | stable `sys-kernel/gentoo-sources` | gentoo |
| `gentoo-sources` | `sys-kernel/gentoo-sources` | gentoo |
| `cachyos-kernel-bin` | `sys-kernel/cachyos-kernel-bin` | CachyOS-kernels |
| `cachyos-kernel` | `sys-kernel/cachyos-kernel` | CachyOS-kernels |

There is no Ubuntu LTS ebuild and no fake `*-lts` package.

### 8 — System configuration

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Hostname / timezone | prompts | `gentoo` / `UTC` |
| Locale | common list + custom; optional extra locales | `en_US.UTF-8` |
| Keymap | confirm | `us` |
| Root password | | |
| Non-root user? | username, password, groups | Yes → user `gentoo`, groups `wheel,audio,video,usb,users` |

### 9 — System tools

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Networking stack | `networkmanager`, `dhcpcd`, `netifrc`; + `systemd-networkd` if systemd | NM on desktop |
| Wireless tools / sshd / nftables | yes/no | Yes |
| CUPS | yes/no | No |
| chrony | yes/no | Yes |
| Syslog | `sysklogd`, `metalog`, `rsyslog`, `none` | `sysklogd` |
| Cron | `cronie`, `fcron`, `none` | `cronie` |

### 10 — Bootloader

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Bootloaders to install | checklist (firmware filters apply) | `grub` |
| Primary bootloader | menu of selected | first selected |
| Plymouth | themes checklist; OpenRC plugin if OpenRC | Yes on desktop |
| Secure Boot prep | UEFI only | No |
| GRUB password | if GRUB selected | No |
| SELinux permissive | hardened variant only | No |

**Bootloader ids:** `grub`, `systemd-boot`, `refind`, `efistub`, `syslinux`, `limine`, `lilo`.

### 11 — Wayland session

No desktop environment. Forced: browsers = Chrome + Firefox ESR.

| Ask | Options / notes | Default |
|-----|-----------------|---------|
| Compositors / WMs | checklist (below) | empty / saved |
| Wayland shell | `none`, `noctalia`, `dms` | `none` |
| Dotfiles | Hyprland: `installer` \| `end-4`; Niri: `installer` \| `upstream`. Skipped for Noctalia/DMS | `installer` |
| PipeWire | yes/no | Yes if WM or shell |
| Proprietary codecs | license accept for ugly/bad/libav, fdk-aac, faac, lame, ffmpeg | Off |

**If a Wayland compositor or shell is chosen**, login is **greetd** (gtkgreet+labwc, or noctalia-greeter with Noctalia). X11-only dwm/i3 → text console / startx (no greeter). Shell without a WM → `labwc` is added.

**Compositors / WMs offered**

| Atom | Notes |
|------|--------|
| `gui-wm/hyprland` | ::hyproverlay |
| `gui-wm/sway` | |
| `gui-wm/swayfx` | ::guru |
| `gui-wm/niri` | ::guru |
| `gui-wm/river` | ::guru |
| `gui-wm/wayfire` | |
| `gui-wm/labwc` | |
| `gui-wm/dwl` | |
| `gui-wm/mangowm` | ::guru |
| `gui-wm/miracle-wm` | ::guru |
| `x11-wm/qtile` | Wayland |
| `gui-wm/gamescope` | |
| `dev-libs/weston` | |
| `x11-wm/dwm` | X11 |
| `x11-wm/i3` | X11 |

GURU is enabled when a GURU compositor or Noctalia/DMS is selected — not as a free overlay checklist row.

### 12 — Suckless tools and popular apps

1. Optional **extra Portage overlays** (featured + community list; GURU not listed here).
2. **Browsers** — info only: always `www-client/firefox-bin:esr` and `www-client/google-chrome`.
3. If Niri: **Niri apps** checklist (preticks waybar/wofi/mako/swaybg/swayidle/swaylock when shell is `none`).
4. Linear category walk (Space toggle, Enter next, Select all). Categories already decided earlier are skipped: window-managers, browsers, display-managers (and there is no games / DE category).
5. Extra free-typed emerge atoms.

### Review → confirm → nap

Summary table and time estimate → wipe warning → “Proceed?” → **re-type the disk path** → nap banner (“Take a nice relaxing nap” / dream about the source in the clouds). Then execute phases: host packages → network → disks → stage3 → chroot mounts → chroot install → done.

---

## Default and auto-queued packages

### Always installed

- `www-client/firefox-bin:esr` — Firefox ESR (mandatory)
- `www-client/google-chrome` — Google Chrome (mandatory; `ACCEPT_LICENSE` expanded)

### When a Wayland compositor or shell is selected

**Shared Wayland base** (`WAYLAND_BASE`):  
`sys-apps/xdg-desktop-portal`, `gui-libs/xdg-desktop-portal-wlr`*, `sys-apps/xdg-desktop-portal-gtk`, `gui-apps/wl-clipboard`, `gui-apps/grim`*, `gui-apps/slurp`*, `x11-base/xwayland`*, `dev-qt/qtwayland`, `media-fonts/noto`, `media-fonts/noto-emoji`, `sys-auth/polkit`  
(\* skipped for a Niri-only session)

**PipeWire** (if accepted): `media-video/pipewire`, `media-video/wireplumber`

**Per-compositor requirements** (examples): Hyprland → portal-hyprland, hyprpaper, hypridle, hyprlock; Sway/SwayFX → swaybg, swayidle, swaylock, foot; Niri → xdg-desktop-portal-gnome, gnome-keyring, foot; dwm → xorg-server, xinit, dmenu, st, slstatus, slock; i3 → xorg-server, xinit, dmenu, i3status, st.

**Greeter:** `gui-libs/greetd` plus gtkgreet+labwc, or `gui-apps/noctalia-greeter` with Noctalia (plus greetd user/group / display-manager-init as needed).

**Shells:** `gui-apps/noctalia-shell` or `gui-apps/dankmaterialshell` (::guru) when chosen.

`nano` is installed on the new system. Dotfile choices may pull additional atoms the chosen config launches (upstream install scripts are not run).

### Proprietary codecs (only after explicit license accept)

`media-libs/gst-plugins-ugly`, `media-libs/gst-plugins-bad`, `media-plugins/gst-plugins-libav`, `media-libs/fdk-aac`, `media-libs/faac`, `media-sound/lame`, `media-video/ffmpeg`.

---

## Package categories (chapter 12)

Order and atoms match `catalog.CATEGORIES` (plus conditional Niri apps). There is **no games category**.

### Niri apps (only if Niri selected)

`gui-apps/waybar`, `gui-apps/wofi`, `gui-apps/mako`, `gui-apps/swaybg`, `gui-apps/swayidle`, `gui-apps/swaylock`, `x11-terms/alacritty`, `media-gfx/imv`, `media-video/mpv`

### Utilities that work with every Wayland compositor

`gui-apps/foot`, `x11-terms/alacritty`, `x11-terms/kitty`, `x11-terms/ghostty`, `media-gfx/imv`, `media-video/mpv`, `gui-apps/waybar`, `gui-apps/wofi`, `gui-apps/mako`, `gui-apps/kanshi`

### Suckless tools

`x11-wm/dwm`, `x11-misc/dmenu`, `x11-terms/st`, `x11-misc/slock`, `x11-misc/slstatus`, `www-client/surf`, `x11-misc/tabbed`

### App stores (Flatpak, Bazaar, AppImage)

`sys-apps/flatpak`, `flatpak-app/io.github.kolunmi.Bazaar`, `sys-fs/fuse:0`, `sys-fs/fuse`

### Text editors

`app-editors/sublime-text`, `flatpak-app/dev.pulsar_edit.Pulsar`, `app-editors/neovim`, `app-editors/vim`, `app-editors/gvim`, `app-editors/emacs`, `app-editors/nano`, `app-editors/helix`, `app-editors/kakoune`, `app-editors/micro`, `app-editors/kate`, `app-editors/gedit`, `app-editors/gnome-text-editor`, `app-editors/featherpad`, `app-editors/mousepad`, `app-editors/kile`, `app-editors/ghostwriter`, `app-editors/logseq-desktop-bin`

### IDEs (2026)

`app-editors/vscode`, `app-editors/vscodium`, `app-editors/zed`

### Terminal emulators

`x11-terms/alacritty`, `x11-terms/kitty`, `x11-terms/ghostty`, `gui-apps/foot`, `x11-terms/rxvt-unicode`, `x11-terms/xterm`, `x11-terms/xfce4-terminal`, `kde-apps/konsole`, `x11-terms/gnome-terminal`, `x11-terms/mlterm`

### Shells, CLI, and Portage tools

`app-shells/zsh`, `app-shells/fish`, `app-shells/bash-completion`, `app-shells/starship`, `app-misc/tmux`, `app-misc/screen`, `sys-apps/eza`, `sys-apps/bat`, `sys-apps/ripgrep`, `sys-apps/fd`, `app-misc/neofetch`, `app-portage/gentoolkit`, `app-portage/eix`, `app-portage/flaggie`, `app-eselect/eselect-repository`, `app-portage/cpuid2cpuflags`, `app-text/wgetpaste`

### Audio & video

`media-video/pipewire`, `media-video/wireplumber`, `media-sound/pulseaudio`, `media-sound/alsa-utils`, `media-sound/pavucontrol`, `media-video/mpv`, `media-video/vlc`, `media-video/ffmpeg`, `media-sound/audacious`, `media-sound/strawberry`, `media-gfx/gimp`, `media-gfx/inkscape`, `media-gfx/imv`, `media-gfx/feh`

### Office & documents

`app-office/libreoffice-bin`, `app-office/libreoffice`, `app-text/zathura`, `app-text/evince`, `kde-apps/okular`, `app-text/pandoc-bin`, `app-text/texlive`

### Networking & remote access

`net-misc/networkmanager`, `net-misc/dhcpcd`, `net-vpn/wireguard-tools`, `net-vpn/openvpn`, `net-misc/openssh`, `net-misc/chrony`, `net-misc/wget`, `net-misc/curl`, `net-misc/rsync`, `net-ftp/filezilla`, `net-im/telegram-desktop-bin`, `net-im/signal-desktop-bin`, `net-analyzer/wireshark`, `net-firewall/ufw`, `net-firewall/nftables`

### Containers & virtualisation

`app-containers/docker`, `app-containers/docker-cli`, `app-containers/containerd`, `app-containers/podman`, `app-emulation/qemu`, `app-emulation/libvirt`, `app-emulation/virt-manager`, `app-emulation/virtualbox`, `sys-kernel/linux-headers`

### Development toolchains

`dev-vcs/git`, `dev-lang/python`, `dev-lang/rust-bin`, `dev-lang/go`, `dev-lang/php`, `dev-db/sqlite`, `dev-db/postgresql`, `dev-db/mariadb`, `sys-devel/gdb`, `dev-util/strace`, `dev-util/cmake`, `dev-util/meson`, `dev-util/pkgconf`

### Icon themes

`x11-themes/papirus-icon-theme`, `x11-themes/adwaita-icon-theme`, `x11-themes/tela-icon-theme`, `x11-themes/numix-icon-theme-circle`, `kde-frameworks/breeze-icons`

### GTK themes

`x11-themes/arc-theme`, `x11-themes/greybird`, `x11-themes/numix-gtk-theme`, `x11-themes/clearlooks-phenix`, `x11-themes/adapta-gtk-theme`

### Qt themes

`x11-themes/kvantum`, `x11-themes/adwaita-qt`, `x11-themes/qtcurve`, `kde-plasma/breeze`

### Fonts

`media-fonts/fonts-meta`, `media-fonts/noto`, `media-fonts/noto-emoji`, `media-fonts/noto-cjk`, `media-fonts/hack`, `media-fonts/jetbrains-mono`, `media-fonts/fira-code`, `media-fonts/fira-mono`, `media-fonts/fira-sans`, `media-fonts/iosevka`, `media-fonts/cascadia-code`, `media-fonts/fantasque-sans-mono`, `media-fonts/liberation-fonts`, `media-fonts/dejavu`, `media-fonts/roboto`, `media-fonts/cantarell`, `media-fonts/droid`, `media-fonts/croscorefonts`, `media-fonts/crosextrafonts-carlito`, `media-fonts/crosextrafonts-caladea`, `media-fonts/fontawesome`, `media-fonts/terminus-font`, `media-fonts/unifont`

### System administration

`sys-process/htop`, `sys-process/btop`, `sys-apps/smartmontools`, `sys-apps/usbutils`, `sys-apps/pciutils`, `sys-fs/ncdu`, `app-admin/sudo`, `app-admin/doas`, `app-admin/sysklogd`, `app-admin/rsyslog`, `sys-process/cronie`, `app-backup/restic`, `app-backup/borgbackup`, `sys-apps/flatpak`

You can still type any Portage atom. Some formerly offered GURU atoms (Brave, Discord, tuigreet, RetroArch, …) are blocked even if typed — see `REMOVED_GURU_ATOMS` in `catalog.py`.

### Featured overlays (checklist)

`wayland-desktop`, `hyproverlay`, `CachyOS-kernels`, `gentoo-zh`, `science`, `kde`, `qt`, `haskell`, `emacs`, `java`, `ruby`, `pentoo`, `audio-overlay`, `mv` (plus a longer community list). `::gentoo` is always present; **GURU is not a checklist row**.

---

## Safety

This installer has been actively tested on bare metal. The default path **destroys all data on the selected disk**.

## Research

The installer comes from three comparisons: public Gentoo install scripts ([RESEARCH.md](RESEARCH.md)), the Arch wiki read beside the Gentoo Handbook, and hardware the Ubuntu boot image can see on the machine being installed.

### Arch install guide and the Gentoo Handbook

The wizard follows handbook order. Places where Arch is stricter and this installer follows Arch:

- `/etc/fstab` names a plain partition by PARTUUID on GPT, or by filesystem UUID otherwise. LVM, LUKS, and ZFS stay on `/dev/mapper` names. ESP at `/efi` with `umask=0077`.
- GRUB keeps the kernel on `/boot`. systemd-boot and EFI stub install the kernel onto `/efi`.
- Empty mirror answer → `mirrorselect` → `GENTOO_MIRRORS`.
- Console keymap before LUKS passphrase; SSD → weekly `fstrim`; nftables offered (default yes); SSH opened only when sshd is selected.
- Small-disk warning; 32-bit UEFI limited to GRUB `i386-efi`; declining `linux-firmware` notes AMD microcode lives there.
- After install: power down; remove USB only when the machine is off.

Arch can reuse an existing EFI partition for another OS. This installer still wipes the disk you choose and does not set up dual boot.

### USE flags from the Ubuntu boot image

Live `lspci` / `lsusb` / `/proc/cpuinfo` / `ld.so --help` seed defaults only (binhost ABI, `INPUT_DEVICES`, bluetooth/wifi/alsa, NVIDIA nouveau vs proprietary, virtio/vmware/qxl in VMs). `CPU_FLAGS_X86` still comes from `cpuid2cpuflags` in the chroot.

## Limits (also shown in the wizard)

- The package catalogue is handbook-sized plus overlays, not the entire Portage tree.
- **Encrypted swap** is a separate LUKS2 volume when using a swap partition/LV. LVM-on-LUKS does not wrap swap twice. A **swap file** is not LUKS-wrapped.
- **TPM2** only writes a helper script; enroll after first boot.
- **Limine** needs `limine.conf` / `vmlinuz` refreshed on kernel upgrades.
- **netifrc** uses the NIC you pick, not a hardcoded `eth0`.

## Layout

| Path | Role |
| --- | --- |
| `install.py` | Entry point |
| `gentoo_installer/wizard.py` | Handbook-ordered colourful Q&A |
| `gentoo_installer/execute.py` | Host, disk, stage3, chroot phases |
| `gentoo_installer/estimate.py` | Spoken pre-install time estimate |
| `gentoo_installer/ui.py` | Colours, menus, passwords, nap notice, banner |
| `gentoo_installer/catalog.py` | Package categories, WMs, overlays, Plymouth themes |

$\scriptsize\textcolor{purple}{\textsf{Y}}\textcolor{green}{\textsf{o}}\textcolor{purple}{\textsf{u}}\textcolor{green}{\textsf{'}}\textcolor{purple}{\textsf{r}}\textcolor{green}{\textsf{e}}\ \textcolor{purple}{\textsf{W}}\textcolor{green}{\textsf{e}}\textcolor{purple}{\textsf{l}}\textcolor{green}{\textsf{c}}\textcolor{purple}{\textsf{o}}\textcolor{green}{\textsf{m}}\textcolor{purple}{\textsf{e}}$ 😈
