"""Interactive handbook-ordered wizard.

Each private function `_about`, `_media`, … maps to one AMD64 Handbook
installation chapter (plus chapter 12 for packages/overlays/WMs).

Nothing in this module writes to disks. It only fills InstallConfig.
execute.py runs after `_review()` confirms the target disk path.
"""

from __future__ import annotations

from . import catalog, detect, ui
from .config import InstallConfig

HANDBOOK = "https://wiki.gentoo.org/wiki/Handbook:AMD64"


def fill_missing_secrets(cfg: InstallConfig) -> None:
    """After --config, ask for passwords that were not stored in the YAML/JSON file."""
    ui.chapter("cfg", "Loaded profile — secrets and confirmation", HANDBOOK)
    ui.info("Non-secret choices were imported. Enter credentials that were not saved.")
    if not cfg.root_password:
        cfg.root_password = ui.ask_password("Root password")
    if cfg.username and not cfg.user_password:
        cfg.user_password = ui.ask_password(f"Password for {cfg.username}")
    if cfg.luks and not cfg.luks_passphrase:
        cfg.luks_passphrase = ui.ask_password("LUKS passphrase")
    if cfg.network_mode == "wifi" and not cfg.wifi_psk:
        cfg.wifi_psk = ui.ask_password("Wi-Fi passphrase", confirm=False)
    if not cfg.grub_password and ui.ask_yes_no("Set a GRUB password (was not in the saved file)?", False):
        cfg.grub_password = ui.ask_password("GRUB password")
    ui.danger(f"This saved profile will operate on {cfg.target_disk}")
    if not ui.ask_yes_no("Continue with these imported settings?", False):
        raise SystemExit("Aborted after loading config.")
    _confirm_disk(cfg)


def run_wizard(cfg: InstallConfig) -> InstallConfig:
    """Drive every chapter in handbook order, then the pre-wipe review."""
    ui.info("This installer follows the Gentoo AMD64 Handbook chapter by chapter.")
    ui.info(HANDBOOK)
    ui.warn("It can wipe the selected disk. Back up first. Prefer testing in a VM.")
    ui.warn(
        "Limits: package catalogue is not the full Portage tree; TPM enrollment is after first boot; "
        "Limine kernel upgrades need a limine.conf refresh; a swap *file* is not LUKS-wrapped."
    )
    print()

    _about(cfg)
    _media(cfg)
    _network(cfg)
    _disks(cfg)
    _stage(cfg)
    _base(cfg)
    _kernel(cfg)
    _system(cfg)
    _tools(cfg)
    _bootloader(cfg)
    _finalize(cfg)
    _packages(cfg)
    _review(cfg)
    return cfg


def _about(cfg: InstallConfig) -> None:
    """Handbook: About — dry-run, resume, mountpoint."""
    ui.chapter("1", "About the installation", f"{HANDBOOK}/Installation/About")
    ui.info("A live Ubuntu XFCE session is used as the host instead of the Gentoo minimal ISO.")
    ui.info("After you answer every chapter, a summary is shown before any disk is touched.")
    cfg.dry_run = ui.ask_yes_no("Dry-run only (print commands, do not change disks)?", False)
    cfg.resume = ui.ask_yes_no("Resume a previous interrupted install?", False)
    cfg.mountpoint = ui.prompt("Target mountpoint", cfg.mountpoint)


def _media(cfg: InstallConfig) -> None:
    """Handbook: Media — we treat Ubuntu XFCE live as the installation medium."""
    ui.chapter("2", "Choosing the right installation medium (Ubuntu live host)", f"{HANDBOOK}/Installation/Media")
    if detect.is_linux():
        ui.ok(f"Linux host detected ({detect.nproc()} CPUs, ~{detect.mem_gib()} GiB RAM)")
        ui.ok("UEFI firmware" if detect.is_uefi() else "BIOS/legacy firmware detected")
        if detect.is_root():
            ui.ok("Running as root")
        else:
            ui.warn("Not running as root — the execute phase will require sudo/root.")
    else:
        ui.warn("This machine is not Linux. You can still complete the wizard; execution needs Ubuntu live.")

    cfg.architecture = ui.menu(
        "CPU architecture (stage3 / profile)",
        [
            ("amd64", "64-bit x86_64 — typical PCs and most VMs"),
            ("arm64", "AArch64 — experimental stage3 mapping"),
        ],
        cfg.architecture if cfg.architecture in {"amd64", "arm64"} else "amd64",
    )
    cfg.firmware = "uefi" if detect.is_uefi() else "bios"
    cfg.firmware = ui.menu(
        "Firmware / boot mode",
        [
            ("uefi", "UEFI + GPT (recommended; handbook default on modern PCs)"),
            ("bios", "Legacy BIOS + MBR or GPT+BIOS boot partition"),
        ],
        cfg.firmware,
    )
    cfg.skip_host_packages = not ui.ask_yes_no(
        "Install host tools on Ubuntu (gdisk, cryptsetup, btrfs-progs, wget, gnupg, …)?",
        True,
    )


def _network(cfg: InstallConfig) -> None:
    """Handbook: Networking — live-session DHCP/Wi-Fi so stage3 can download."""
    ui.chapter("3", "Configuring the network", f"{HANDBOOK}/Installation/Networking")
    online = detect.has_network()
    if online:
        ui.ok("Internet access looks working.")
    else:
        ui.warn("Could not reach distfiles.gentoo.org. Configure networking before execute.")

    cfg.network_mode = ui.menu(
        "Live-environment network",
        [
            ("dhcp", "Already online / DHCP — continue"),
            ("wifi", "Connect Wi-Fi from this installer (wpa_supplicant)"),
            ("static", "Set a static IPv4 address on the live host"),
            ("skip", "Do not touch live networking"),
        ],
        "dhcp" if online else "wifi",
    )
    if cfg.network_mode == "wifi":
        cfg.wifi_iface = _choose_iface("Wi-Fi interface", prefer_wireless=True, default=cfg.wifi_iface)
        cfg.wifi_ssid = ui.prompt("SSID", cfg.wifi_ssid)
        cfg.wifi_psk = ui.ask_password("Wi-Fi passphrase", confirm=False)
    elif cfg.network_mode == "static":
        cfg.static_iface = _choose_iface(
            "Live NIC for the static address",
            prefer_wireless=False,
            default=cfg.static_iface,
        )
        cfg.static_ip = ui.prompt("CIDR address (e.g. 192.168.1.50/24)", cfg.static_ip)
        cfg.static_gateway = ui.prompt("Gateway", cfg.static_gateway)
        cfg.static_dns = ui.prompt("DNS", cfg.static_dns)
    cfg.ntp = ui.ask_yes_no("Synchronise clock (ntpdate/chrony) before downloading stage3?", True)


def _disks(cfg: InstallConfig) -> None:
    """Handbook: Disks — layout, filesystem, LUKS, swap. Most destructive choices."""
    ui.chapter("4", "Preparing the disks", f"{HANDBOOK}/Installation/Disks")
    disks = detect.list_disks()
    live = detect.live_boot_disk()
    choices = []
    for d in disks:
        tag = f"{d['size']}  {d['model']}  {d['tran']}"
        if d["removable"]:
            tag += "  [removable]"
        if live and d["path"].startswith(live):
            tag += "  [possible live USB]"
        choices.append((d["path"], tag))
    if not choices:
        ui.warn("No disks detected (expected on Windows). You can type a device path.")
        cfg.target_disk = ui.prompt("Target block device", cfg.target_disk or "/dev/sda")
    else:
        cfg.target_disk = ui.menu("Target disk (will be partitioned if wipe is enabled)", choices, choices[0][0])

    cfg.wipe_disk = ui.ask_yes_no("Wipe this disk and create a new partition table?", True)
    if cfg.firmware == "uefi":
        cfg.partition_table = "gpt"
        ui.info("UEFI selected — GPT will be used (handbook).")
    else:
        cfg.partition_table = ui.menu(
            "Partition table (BIOS)",
            [("mbr", "MBR/DOS — handbook BIOS path"), ("gpt", "GPT with BIOS boot partition")],
            "mbr",
        )

    cfg.layout = ui.menu(
        "Disk layout (merged from oddlama, GForge, l0rdg3x, handbook)",
        [
            ("handbook", "Handbook default: ESP/boot + swap + root"),
            ("btrfs-subvols", "GPT + Btrfs subvolumes @ @home @log @cache @tmp @swap"),
            ("lvm", "LVM: ESP + PV with root and optional swap LVs"),
            ("lvm-luks", "LVM-on-LUKS (ESP unencrypted)"),
            ("mdraid1", "Software RAID1 of two disks, then handbook layout"),
            ("zfs", "ZFS root pool (needs zfsutils-linux on Ubuntu)"),
            ("existing", "Use partitions you already created"),
        ],
        "handbook",
    )
    if cfg.layout == "mdraid1":
        others = [c for c in choices if c[0] != cfg.target_disk]
        if len(others) >= 1:
            cfg.extra_raid_disk = ui.menu("Second RAID1 disk", others, others[0][0])
        else:
            cfg.extra_raid_disk = ui.prompt("Second RAID1 disk", "/dev/sdb")
    if cfg.layout == "existing":
        cfg.wipe_disk = False
        cfg.existing_esp = ui.prompt("ESP / boot partition", cfg.existing_esp or "/dev/sda1")
        cfg.existing_root = ui.prompt("Root partition (or LUKS container)", cfg.existing_root or "/dev/sda3")
        cfg.existing_swap = ui.prompt("Swap partition (empty to skip)", cfg.existing_swap)

    if cfg.layout in {"btrfs-subvols"}:
        cfg.filesystem = "btrfs"
    elif cfg.layout == "zfs":
        cfg.filesystem = "zfs"
    else:
        cfg.filesystem = ui.menu(
            "Root filesystem",
            [
                ("ext4", "Handbook default, widely compatible"),
                ("xfs", "Excellent for large files"),
                ("btrfs", "Snapshots / compression"),
                ("f2fs", "Flash-oriented"),
                ("jfs", "JFS"),
            ],
            "ext4" if cfg.layout != "btrfs-subvols" else "btrfs",
        )

    cfg.swap_mode = ui.menu(
        "Swap",
        [
            ("partition", "Dedicated swap partition (handbook)"),
            ("file", "Swap file on the root filesystem"),
            ("zram", "ZRAM only"),
            ("both", "Swap partition/file + ZRAM"),
            ("none", "No swap"),
        ],
        "partition",
    )
    if cfg.swap_mode in {"partition", "file", "both"}:
        suggested = max(2, min(8, detect.mem_gib()))
        cfg.swap_size_gib = int(ui.prompt("Swap size in GiB", str(suggested)))
    cfg.zram = cfg.swap_mode in {"zram", "both"} or ui.ask_yes_no("Also configure ZRAM?", cfg.swap_mode != "none")

    cfg.luks = cfg.layout == "lvm-luks" or ui.ask_yes_no("LUKS2 encrypt the root (and optionally swap)?", cfg.layout == "lvm-luks")
    if cfg.luks:
        cfg.luks_passphrase = ui.ask_password("LUKS passphrase")
        if cfg.swap_mode in {"partition", "both"}:
            if cfg.layout == "lvm-luks":
                ui.info("LVM-on-LUKS already encrypts the volume group, so the swap LV is not wrapped a second time.")
                cfg.encrypted_swap = True
            else:
                ui.info("Encrypted swap is a separate LUKS2 volume opened as /dev/mapper/swap (same passphrase as root).")
                cfg.encrypted_swap = ui.ask_yes_no("Encrypt the swap partition/LV as well?", True)
        else:
            cfg.encrypted_swap = False
            if cfg.swap_mode == "file":
                ui.warn("A swap *file* is not LUKS-wrapped. Use a swap partition or LVM if you need encrypted swap.")
        cfg.tpm2_unlock = ui.ask_yes_no(
            "Prepare TPM2 auto-unlock scripts (PCR 7+14, after first boot)?",
            detect.has_tpm2(),
        )
    if cfg.firmware == "uefi":
        cfg.esp_size_mib = int(ui.prompt("EFI System Partition size in MiB", str(cfg.esp_size_mib)))


def _stage(cfg: InstallConfig) -> None:
    """Handbook: Stage file — stage1/2/3, OpenRC/systemd, desktop vs hardened/musl."""
    ui.chapter("5", "Installing the Gentoo installation files", f"{HANDBOOK}/Installation/Stage")
    cfg.stage_level = ui.menu(
        "Stage tarball (handbook default is stage3)",
        [
            (
                "stage3",
                "stage3 — full base system (recommended). Ready for Portage after extract.",
            ),
            (
                "stage2",
                "stage2 — toolchain plus a thin base. This installer then runs emerge -e @system.",
            ),
            (
                "stage1",
                "stage1 — seed toolchain only. Very long bootstrap; not for a first install.",
            ),
        ],
        "stage3",
    )
    if cfg.stage_level != "stage3":
        ui.warn(f"{cfg.stage_level} is experimental on official autobuilds and can take many extra hours.")
        ui.warn("Desktop/LLVM tarballs usually exist only as stage3. Minimal flavour will be used.")
        cfg.install_type = "server"
    cfg.init_system = ui.menu(
        "Init system",
        [
            ("openrc", "OpenRC — Gentoo native"),
            ("systemd", "systemd — handbook alternative"),
        ],
        "openrc",
    )
    if cfg.stage_level == "stage3":
        cfg.install_type = ui.menu(
            "Install type",
            [
                ("desktop", "Desktop stage3 (includes LLVM/Rust-bin; faster graphical installs)"),
                ("server", "Minimal/server stage3"),
            ],
            "desktop",
        )
    cfg.stage_variant = ui.menu(
        "Stage libc / toolchain variant",
        [
            ("standard", "glibc + GCC (recommended)"),
            ("hardened", "Hardened toolchain stage when available"),
            ("musl", "musl libc (no desktop profiles)"),
        ],
        "standard",
    )
    if cfg.stage_variant == "musl":
        ui.warn("musl stages typically have no KDE/GNOME desktop profiles.")
        cfg.install_type = "server"
    cfg.verify_stage = ui.ask_yes_no("Verify the stage tarball with a detached GPG signature?", True)
    kw = "~amd64" if cfg.architecture == "amd64" else "~arm64"
    cfg.testing_keywords = ui.ask_yes_no(
        f"Accept testing keywords (*/* {kw}) for the whole system?",
        False,
    )


def _base(cfg: InstallConfig) -> None:
    """Handbook: Base system — profile, sync, binhost, USE, make.conf knobs."""
    ui.chapter("6", "Installing the Gentoo base system", f"{HANDBOOK}/Installation/Base")
    profile_choices = _profiles(cfg)
    cfg.profile = ui.menu("Portage profile (23.0 tree)", profile_choices, profile_choices[0][0])
    cfg.sync_method = ui.menu(
        "Repository sync method",
        [
            ("webrsync", "emerge-webrsync — handbook-friendly snapshots"),
            ("rsync", "emerge --sync rsync"),
            ("git", "Git sync of gentoo.git (faster later updates)"),
        ],
        "webrsync",
    )
    cfg.binhost = ui.ask_yes_no("Enable Gentoo binary packages (getbinpkg / binhost)?", cfg.stage_variant == "standard")
    if cfg.binhost:
        cfg.binhost_abi = ui.menu(
            "Binhost ABI",
            [("x86-64", "Generic x86-64"), ("x86-64-v3", "x86-64-v3 (Haswell and newer)")],
            "x86-64",
        )
        cfg.binpkg_respect_use = ui.ask_yes_no(
            "Require binpkgs to match USE exactly? (No = use binaries even if USE differs; "
            "avoids compiling glibc's ~500 locales from source)",
            False,
        )
    gpu = detect.detect_gpu()
    ui.info(f"Detected VIDEO_CARDS suggestion: {gpu}")
    cfg.video_cards = ui.prompt("VIDEO_CARDS (or 'auto')", "auto")
    ui.info("USE flags control optional features inside packages (audio, GUI toolkits, networking).")
    extra = ui.checklist(
        "Global USE flags (handbook ch. 6) — Space toggles, Enter continues",
        catalog.USE_FLAGS,
        selected=_default_use(cfg),
    )
    typed = ui.prompt("Extra USE tokens to add (space-separated, empty for none)", "")
    if typed.strip():
        extra = list(dict.fromkeys(extra + typed.split()))
    cfg.extra_use = extra
    cfg.lto = ui.ask_yes_no("Enable LTO in CFLAGS (experimental, slower compiles)?", False)
    cfg.hardened_flags = cfg.stage_variant == "hardened" or ui.ask_yes_no("Add hardened CFLAGS / USE (pie ssp)?", False)
    cfg.gentoo_mirrors = ui.prompt("GENTOO_MIRRORS URL (empty = distfiles.gentoo.org)", "")
    cfg.makeopts_jobs = int(ui.prompt("MAKEOPTS jobs (0 = CPU count)", "0"))


def _profiles(cfg: InstallConfig) -> list[tuple[str, str]]:
    """Build eselect profile paths for the 23.0 tree from init/variant/arch."""
    arch = "amd64" if cfg.architecture == "amd64" else "arm64"
    if cfg.stage_variant == "musl":
        return [(f"default/linux/{arch}/23.0/musl", "musl base")]
    if cfg.stage_variant == "hardened":
        suffix = "/systemd" if cfg.init_system == "systemd" else ""
        return [(f"default/linux/{arch}/23.0/hardened{suffix}", "hardened")]
    systemd = cfg.init_system == "systemd"
    rows = [
        (f"default/linux/{arch}/23.0", "Base"),
        (f"default/linux/{arch}/23.0/desktop", "Desktop"),
        (f"default/linux/{arch}/23.0/desktop/gnome", "GNOME"),
        (f"default/linux/{arch}/23.0/desktop/plasma", "KDE Plasma"),
    ]
    if systemd:
        rows = [
            (f"default/linux/{arch}/23.0/systemd", "Base systemd"),
            (f"default/linux/{arch}/23.0/desktop/systemd", "Desktop systemd"),
            (f"default/linux/{arch}/23.0/desktop/gnome/systemd", "GNOME systemd"),
            (f"default/linux/{arch}/23.0/desktop/plasma/systemd", "Plasma systemd"),
        ]
    return rows


def _default_use(cfg: InstallConfig) -> list[str]:
    """Pre-tick USE flags that match the chosen init system and desktop type."""
    flags = ["dist-kernel"]
    if cfg.install_type == "desktop":
        flags.append("X")
    if cfg.init_system == "openrc":
        flags.append("elogind")
    else:
        flags.append("systemd")
    if cfg.network_stack == "networkmanager" or cfg.install_type == "desktop":
        flags.append("networkmanager")
    return flags


def _kernel(cfg: InstallConfig) -> None:
    """Handbook: Kernel — dist-kernel vs source, firmware, dracut, UKI."""
    ui.chapter("7", "Configuring the Linux kernel", f"{HANDBOOK}/Installation/Kernel")
    cfg.kernel = ui.menu(
        "Kernel (handbook: distribution kernel recommended)",
        [
            ("gentoo-kernel-bin", "Binary dist-kernel — fastest, matches binhost"),
            ("gentoo-kernel", "Source dist-kernel — compiled on this machine"),
            ("gentoo-sources", "gentoo-sources (manual config; dist-kernel helpers still used if possible)"),
        ],
        "gentoo-kernel-bin",
    )
    cfg.linux_firmware = ui.ask_yes_no("Install linux-firmware?", True)
    cfg.sof_firmware = ui.ask_yes_no("Install SOF firmware (Intel sound)?", False)
    cfg.intel_microcode = detect.detect_intel_cpu() or ui.ask_yes_no("Install intel-microcode?", detect.detect_intel_cpu())
    cfg.initramfs = ui.menu(
        "Initramfs",
        [("dracut", "dracut (handbook installkernel path)"), ("none", "No initramfs (not for LUKS/LVM)")],
        "dracut",
    )
    cfg.uki = ui.ask_yes_no("Build a Unified Kernel Image (UKI) if the bootloader supports it?", False)


def _system(cfg: InstallConfig) -> None:
    """Handbook: System — hostname, locale, timezone, keymap, users."""
    ui.chapter("8", "Configuring the system", f"{HANDBOOK}/Installation/System")
    cfg.hostname = ui.prompt("Hostname", cfg.hostname)
    cfg.timezone = ui.prompt("Timezone (e.g. America/New_York)", cfg.timezone)
    loc = ui.menu(
        "System locale (written to locale.gen and /etc/env.d/02locale)",
        catalog.COMMON_LOCALES,
        cfg.locale if cfg.locale in {v for v, _ in catalog.COMMON_LOCALES} else "en_US.UTF-8",
    )
    if loc == "custom":
        loc = ui.prompt("glibc locale name (e.g. en_CA.UTF-8)", cfg.locale or "en_US.UTF-8")
    cfg.locale = loc.strip() or "en_US.UTF-8"
    ui.info("Only your locale plus C.UTF-8 are generated. glibc will not compile all 500+ locales.")
    if ui.ask_yes_no("Generate additional locales besides the one above?", False):
        extras = [
            (v, d)
            for v, d in catalog.COMMON_LOCALES
            if v not in {cfg.locale, "custom", "C.UTF-8"}
        ]
        cfg.extra_locales = ui.checklist("Additional locales", extras, selected=cfg.extra_locales)
    else:
        cfg.extra_locales = []
    cfg.keymap = ui.prompt("Console keymap (e.g. us, uk, de, fr)", cfg.keymap)
    cfg.root_password = ui.ask_password("Root password")
    if ui.ask_yes_no("Create a non-root user?", True):
        cfg.username = ui.prompt("Username", cfg.username)
        cfg.user_password = ui.ask_password(f"Password for {cfg.username}")
        cfg.user_groups = ui.prompt("Supplementary groups", cfg.user_groups)
    else:
        cfg.username = ""


def _tools(cfg: InstallConfig) -> None:
    """Handbook: Tools — NM/dhcpcd, sshd, syslog, cron, CUPS, chrony."""
    ui.chapter("9", "Installing system tools", f"{HANDBOOK}/Installation/Tools")
    net_choices = [
        ("networkmanager", "NetworkManager — desktops/laptops"),
        ("dhcpcd", "dhcpcd only"),
        ("netifrc", "OpenRC netifrc"),
    ]
    if cfg.init_system == "systemd":
        net_choices.append(("systemd-networkd", "systemd-networkd"))
    cfg.network_stack = ui.menu(
        "Installed-system networking",
        net_choices,
        "networkmanager" if cfg.install_type == "desktop" else "dhcpcd",
    )
    if cfg.network_stack == "netifrc":
        cfg.netifrc_iface = _choose_iface(
            "netifrc interface on the installed system",
            prefer_wireless=None,
            default=cfg.netifrc_iface or cfg.static_iface or cfg.wifi_iface,
        )
    cfg.wireless = ui.ask_yes_no("Install wireless tools (iw, wpa_supplicant)?", True)
    cfg.sshd = ui.ask_yes_no("Install and enable sshd?", True)
    cfg.cups = ui.ask_yes_no("Install CUPS printing?", False)
    cfg.chrony = ui.ask_yes_no("Install chrony for NTP?", True)
    cfg.syslog = ui.menu(
        "Syslog (OpenRC mainly; systemd uses journald)",
        [("sysklogd", "sysklogd"), ("metalog", "metalog"), ("rsyslog", "rsyslog"), ("none", "None")],
        "sysklogd",
    )
    cfg.cron = ui.menu(
        "Cron (OpenRC; systemd has timers)",
        [("cronie", "cronie"), ("fcron", "fcron"), ("none", "None")],
        "cronie",
    )


def _bootloader(cfg: InstallConfig) -> None:
    """Handbook: Bootloader — multiple loaders plus Plymouth theme selection."""
    ui.chapter("10", "Configuring the bootloader", f"{HANDBOOK}/Installation/Bootloader")
    loaders = list(catalog.BOOTLOADERS)
    if cfg.firmware != "uefi":
        loaders = [item for item in loaders if item[0] not in {"systemd-boot", "refind", "efistub"}]
    else:
        loaders = [item for item in loaders if item[0] not in {"syslinux", "lilo"}]
    ui.info("You can install more than one bootloader. Pick a primary afterwards for installkernel.")
    cfg.bootloaders = ui.paged_checklist(
        "Bootloaders to install",
        loaders,
        selected=["grub"],
        page_size=12,
    )
    if not cfg.bootloaders:
        ui.warn("No bootloader selected — defaulting to GRUB.")
        cfg.bootloaders = ["grub"]
    if "limine" in cfg.bootloaders:
        ui.warn(
            "Limine: the installer copies BOOT*.EFI (UEFI) or runs bios-install (BIOS) and writes limine.conf "
            "from the kernel currently in /boot. After kernel upgrades, refresh limine.conf or the vmlinuz symlink."
        )
    cfg.bootloader = ui.menu(
        "Primary bootloader",
        [(name, desc) for name, desc in loaders if name in cfg.bootloaders],
        cfg.bootloaders[0],
    )
    cfg.plymouth = ui.ask_yes_no("Install Plymouth boot splash?", cfg.install_type == "desktop")
    if cfg.plymouth:
        cfg.plymouth_themes = ui.paged_checklist(
            "Plymouth themes to install",
            catalog.plymouth_theme_choices(),
            selected=["spinner"],
            page_size=12,
        )
        if not cfg.plymouth_themes:
            cfg.plymouth_themes = ["spinner"]
        cfg.plymouth_theme = ui.menu(
            "Default Plymouth theme",
            [(t, t) for t in cfg.plymouth_themes],
            cfg.plymouth_themes[0],
        )
        if cfg.init_system == "openrc":
            cfg.plymouth_openrc_plugin = ui.ask_yes_no("Install plymouth-openrc-plugin?", True)
    cfg.secure_boot = cfg.firmware == "uefi" and ui.ask_yes_no("Prepare Secure Boot (shim/mokutil notes and installkernel signing USE)?", False)
    if "grub" in cfg.bootloaders and ui.ask_yes_no("Set a GRUB password?", False):
        cfg.grub_password = ui.ask_password("GRUB password")
    if cfg.stage_variant == "hardened":
        cfg.selinux = ui.ask_yes_no("Enable SELinux (permissive at first boot)?", False)
        if cfg.selinux:
            cfg.selinux_policy = ui.menu(
                "SELinux policy",
                [("targeted", "targeted"), ("strict", "strict"), ("mls", "mls")],
                "targeted",
            )


def _finalize(cfg: InstallConfig) -> None:
    """Handbook: Finalizing — DE, display manager, browser; packages follow in ch. 12."""
    ui.chapter("11", "Finalizing the installation", f"{HANDBOOK}/Installation/Finalizing")
    if cfg.install_type == "desktop":
        cfg.desktop = ui.menu(
            "Desktop environment",
            [
                ("none", "No DE — tiling WMs and extras are chosen in chapter 12"),
                ("xfce", "Xfce — light GTK desktop (good default on a live USB)"),
                ("plasma", "KDE Plasma — full Qt desktop"),
                ("gnome", "GNOME — full GTK desktop"),
                ("mate", "MATE — traditional GTK desktop"),
                ("cinnamon", "Cinnamon — Mint-style desktop"),
                ("i3", "i3 — X11 tiling window manager (not a full DE)"),
            ],
            "xfce",
        )
        if cfg.desktop != "none":
            cfg.display_manager = ui.menu(
                "Display manager (graphical login)",
                [
                    ("auto", "Auto — LightDM/SDDM/GDM matching the DE"),
                    ("lightdm", "LightDM — common for Xfce/MATE/Cinnamon"),
                    ("sddm", "SDDM — usual choice for Plasma"),
                    ("gdm", "GDM — usual choice for GNOME"),
                    ("none", "None — log in on TTY and run startx"),
                ],
                "auto",
            )
            cfg.pipewire = ui.ask_yes_no("Install PipeWire audio?", True)
            cfg.browser = ui.menu(
                "Web browser",
                [
                    ("firefox-bin", "Firefox official binary — no compile"),
                    ("firefox", "Firefox compiled from source — many hours"),
                    ("chromium", "Chromium from source — very long compile"),
                    ("none", "No browser from this chapter (you can pick one in chapter 12)"),
                ],
                "firefox-bin",
            )
            cfg.kde_games = cfg.desktop == "plasma" and ui.ask_yes_no("Install a small KDE games set?", False)
            cfg.flatpak = ui.ask_yes_no("Install Flatpak?", False)
    ui.info("The next chapter is a categorised emerge catalogue (window managers, overlays, extra packages).")


def _packages(cfg: InstallConfig) -> None:
    """Extra chapter: overlays, window managers (niri/Hyprland/…), categorised emerge."""
    ui.chapter(
        "12",
        "Packages, overlays, and window managers",
        "https://packages.gentoo.org/",
    )
    ui.info("Browse Portage categories, enable overlays (guru, hyproverlay, wayland-desktop, …),")
    ui.info("and pick window managers including niri, Hyprland, Sway, and the X11 set.")
    ui.info("Search with /text  ·  n/p pages  ·  toggle numbers  ·  0 when a list is finished.")

    if ui.ask_yes_no("Enable extra Portage overlays (eselect repository)?", True):
        cfg.overlays = ui.paged_checklist(
            "Overlays from overlays.gentoo.org / eselect repository",
            catalog.overlay_choices(),
            selected=cfg.overlays,
            page_size=14,
        )
        more = ui.prompt("Extra overlay names (space-separated, empty for none)", "")
        if more.strip():
            cfg.overlays = list(dict.fromkeys(cfg.overlays + more.split()))

    cfg.window_managers = ui.paged_checklist(
        "Window managers & Wayland compositors",
        catalog.wm_choices(),
        selected=cfg.window_managers,
        page_size=14,
    )
    if cfg.desktop == "i3" and "x11-wm/i3" not in cfg.window_managers:
        cfg.window_managers.append("x11-wm/i3")

    picked: list[str] = list(cfg.selected_packages)
    if ui.ask_yes_no("Browse more emerge categories (browsers, editors, games, …)?", True):
        cats = [(key, title) for key, title, _ in catalog.CATEGORIES if key != "window-managers"]
        cats.append(("done", "Done with categories"))
        while True:
            choice = ui.menu("Package category", cats, "done")
            if choice == "done":
                break
            packages = next(pkgs for key, _title, pkgs in catalog.CATEGORIES if key == choice)
            title = next(title for key, title, _ in catalog.CATEGORIES if key == choice)
            selected = ui.paged_checklist(title, packages, selected=picked, page_size=14)
            for atom in selected:
                if atom not in picked:
                    picked.append(atom)
            for atom in list(picked):
                if atom in {a for a, _ in packages} and atom not in selected:
                    picked.remove(atom)

    extra = ui.prompt("Additional emerge atoms (space-separated, e.g. app-misc/jq media-gfx/feh)", "")
    if extra.strip():
        for atom in extra.split():
            if atom not in picked:
                picked.append(atom)
    cfg.selected_packages = picked
    # extra_packages is the emerge set: catalogue picks + WMs + typed atoms.
    cfg.extra_packages = list(dict.fromkeys(cfg.extra_packages + picked + cfg.window_managers))
    # niri → guru, hyprland → hyproverlay, etc.
    cfg.overlays = catalog.overlays_required_by(cfg.extra_packages, cfg.overlays)
    if cfg.window_managers or any("wayland" in a or "gui-wm/" in a for a in cfg.extra_packages):
        if "wayland" not in cfg.extra_use:
            cfg.extra_use.append("wayland")
    if cfg.overlays:
        ui.ok("Overlays that will be enabled: " + ", ".join(cfg.overlays))
    ui.ok(f"{len(cfg.extra_packages)} package atoms queued for emerge")


def _review(cfg: InstallConfig) -> None:
    """Last safe abort point. User must re-type the disk path before execute()."""
    ui.chapter("✓", "Review — nothing has been written to disk yet", HANDBOOK)
    rows = [
        ("Firmware", cfg.firmware),
        ("Disk", cfg.target_disk),
        ("Wipe disk", str(cfg.wipe_disk)),
        ("Layout", cfg.layout),
        ("Filesystem", cfg.filesystem),
        ("LUKS", str(cfg.luks) + (" + encrypted swap" if cfg.encrypted_swap else "")),
        ("Swap", f"{cfg.swap_mode} {cfg.swap_size_gib}G"),
        ("Init", cfg.init_system),
        ("Stage", f"{cfg.stage_level}/{cfg.install_type}/{cfg.stage_variant}"),
        ("USE", " ".join((cfg.use_flags or "").split()[:12]) + ("…" if len((cfg.use_flags or "").split()) > 12 else "")),
        ("Profile", cfg.profile),
        ("Kernel", cfg.kernel),
        ("Bootloaders", ", ".join(cfg.bootloaders) + f" (primary {cfg.bootloader})"),
        ("Plymouth", cfg.plymouth_theme if cfg.plymouth else "no"),
        ("Hostname", cfg.hostname),
        ("Timezone", cfg.timezone),
        ("Locale", cfg.locale),
        ("User", cfg.username or "(root only)"),
        ("Desktop", cfg.desktop),
        ("Window managers", ", ".join(cfg.window_managers) or "none"),
        ("Overlays", ", ".join(cfg.overlays) or "none"),
        ("Extra packages", str(len(cfg.extra_packages))),
        ("Binhost", str(cfg.binhost)),
        ("Dry-run", str(cfg.dry_run)),
    ]
    ui.summary_table(rows)
    ui.danger(f"Continuing will operate on {cfg.target_disk}")
    if not ui.ask_yes_no("Proceed with these settings?", False):
        raise SystemExit("Aborted by user before any disk changes.")
    _confirm_disk(cfg)


def _confirm_disk(cfg: InstallConfig) -> None:
    """Require an exact re-type of the disk path (empty Enter used to skip this)."""
    if cfg.dry_run:
        ui.info("Dry-run: skipping disk-path retype.")
        return
    typed = ui.prompt(f"Type the disk path to confirm ({cfg.target_disk})", "")
    if typed != cfg.target_disk:
        raise SystemExit("Disk confirmation did not match. Aborting.")


def _choose_iface(title: str, prefer_wireless: bool | None, default: str = "") -> str:
    """Pick a live NIC from /sys/class/net, or type a name when none are listed (Windows)."""
    nics = detect.list_nics()
    guess = default or detect.default_nic(prefer_wireless)
    if not nics:
        ui.warn("No network interfaces listed (expected on Windows). Type the NIC name.")
        return ui.prompt(title, guess)
    choices = []
    for nic in nics:
        kind = "wifi" if nic["wireless"] else "wired"
        choices.append((nic["name"], f"{kind} · {nic['state']}"))
    if guess not in {c[0] for c in choices}:
        guess = choices[0][0]
    return ui.menu(title, choices, guess)
