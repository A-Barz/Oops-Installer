"""Interactive handbook-ordered wizard.

Each private function `_about`, `_media`, … maps to one AMD64 Handbook
installation chapter (plus chapter 12 for packages/overlays/WMs).

Nothing in this module writes to disks. It only fills InstallConfig.
execute.py runs after `_review()` confirms the target disk path.
"""

from __future__ import annotations

from pathlib import Path

from . import catalog, detect, ui
from .config import InstallConfig, part_name
from .estimate import estimate_minutes, format_duration

HANDBOOK = "https://wiki.gentoo.org/wiki/Handbook:AMD64"

# Default mise agent names for the optional first-login invite.
DEFAULT_MISE_AGENT_TOOLS = ("claude", "codex", "opencode", "cursor-agent")

# LocalSend-style ports offered when UFW is chosen (opt-in).
LOCALSEND_UFW_PORTS = (
    {"port": 53317, "proto": "udp"},
    {"port": 53317, "proto": "tcp"},
)


def profile_has_software(cfg: InstallConfig) -> bool:
    """True when a saved profile still has the compositors or apps from an attempt."""
    if cfg.window_managers or cfg.selected_packages or cfg.extra_packages:
        return True
    return bool(cfg.shell and cfg.shell != "none")


def queue_saved_software(cfg: InstallConfig) -> None:
    """Rebuild the emerge set from the software stored in a loaded profile.

    The first attempt records window managers, the shell, and the ticked apps.
    Loading that YAML must install those atoms again. Compositor requirements
    are added back. Apps the user unticked are not put back on the list.
    """
    known_wm = {pkg.atom for pkg in catalog.WINDOW_MANAGERS}
    managers: list[str] = []
    for atom in list(cfg.window_managers) + list(cfg.extra_packages) + list(cfg.selected_packages):
        bare = atom.split("::", 1)[0]
        if bare in known_wm and bare not in managers:
            managers.append(bare)
    if cfg.shell not in {"", "none"} and not catalog.wayland_selected(managers):
        if "gui-wm/labwc" not in managers:
            managers.append("gui-wm/labwc")
    cfg.window_managers = managers
    needed_atoms = {
        pkg.atom for pkg in catalog.packages_for_selection(managers, cfg.shell, cfg.pipewire)
    }
    # selected_packages is the ticked list. extra_packages is that list plus
    # requirements, so it is only the source when the ticks were not saved.
    sources = list(cfg.selected_packages) if cfg.selected_packages else list(cfg.extra_packages)
    picked: list[str] = []
    for atom in sources:
        bare = atom.split("::", 1)[0]
        if bare in known_wm or atom in needed_atoms or bare in needed_atoms:
            continue
        if atom not in picked:
            picked.append(atom)
    allowed = catalog.offered_guru_atoms(cfg.shell)
    kept: list[str] = []
    for atom in picked:
        bare, _, suffix = atom.partition("::")
        overlay = suffix or catalog.atom_overlay(bare)
        if overlay == "guru" and bare not in allowed:
            continue
        kept.append(atom)
    cfg.selected_packages = kept
    needed = [pkg.atom for pkg in catalog.packages_for_selection(managers, cfg.shell, cfg.pipewire)]
    needed += catalog.dotfile_package_atoms(cfg.shell, managers, cfg.wm_dotfiles)
    if getattr(cfg, "nonfree_codecs", False):
        needed += catalog.nonfree_codec_atoms()
    cfg.extra_packages = list(dict.fromkeys(managers + needed + kept))
    cfg.overlays = [name for name in cfg.overlays if name != "guru"]
    cfg.overlays = catalog.overlays_required_by(cfg.extra_packages, cfg.overlays)
    catalog.refuse_unsupported_overlays(cfg.overlays)
    if managers or any("wayland" in atom or "gui-wm/" in atom for atom in cfg.extra_packages):
        if "wayland" not in cfg.extra_use:
            cfg.extra_use.append("wayland")
    if profile_has_software(cfg):
        setattr(cfg, "_software_loaded", True)


def announce_saved_software(cfg: InstallConfig) -> None:
    """Tell the user which saved compositors and apps will be installed."""
    queue_saved_software(cfg)
    session = list(cfg.window_managers)
    if cfg.shell not in {"", "none"}:
        session.append(f"shell {cfg.shell}")
    if not session and not cfg.selected_packages:
        ui.warn("The saved profile has no compositors or extra apps.")
        return
    ui.ok("Software from the saved profile will be installed.")
    if session:
        ui.info("Session: " + ", ".join(session))
    if cfg.selected_packages:
        ui.info("Apps: " + ", ".join(cfg.selected_packages))


def fill_missing_secrets(cfg: InstallConfig) -> bool:
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
    if not getattr(cfg, "_resume_decided", False):
        state = Path(cfg.state_file)
        mounted = detect.is_mounted(cfg.mountpoint)
        if state.exists() or mounted:
            ui.info(
                "An incomplete install was found"
                + (f" (state {cfg.state_file})" if state.exists() else "")
                + (f"; {cfg.mountpoint} is already mounted" if mounted else "")
                + "."
            )
            if ui.ask_yes_no(
                "Resume (re-run package installs, keep files already on the disk, do not format or repartition)?",
                True,
            ):
                cfg.resume = True
                cfg.wipe_disk = False
                ui.ok("Resume enabled — existing mounts will be reused.")
            else:
                cfg.resume = False
                ui.warn("Full restart: the disk will be unmounted, then wiped if this profile says so.")
    _announce_estimate(cfg)
    announce_saved_software(cfg)
    _warn_before_wipe(cfg)
    if not ui.ask_yes_no("Continue with these imported settings?", False):
        ui.info("The wizard will open with the saved software already ticked.")
        return False
    _confirm_disk(cfg)
    return True


def warn_if_ntfs_present() -> None:
    """Stop at startup when a Windows filesystem is visible.

    The scan is the whole machine, before a target disk is chosen.
    Dual boot is not supported, so an NTFS volume has to be acknowledged.
    """
    found = [part for part in detect.list_partitions() if detect.partition_is_ntfs(part)]
    if not found:
        return
    ui.danger("NTFS filesystems were found. Oops-Installer does not support dual boot.")
    for part in found:
        label = f"  {part['label']}" if part.get("label") else ""
        ui.warn(f"{part['path']}  {part['size']}  NTFS{label}  on {part['disk']}")
    ui.warn("A later confirmation can wipe the disk you choose, including these NTFS partitions.")
    ui.warn("Continue only if you know what you are doing.")
    if not ui.ask_yes_no("You know what you are doing. Continue the installer?", False):
        raise SystemExit("Aborted. NTFS partitions are present and dual boot is not supported.")


def run_wizard(cfg: InstallConfig) -> InstallConfig:
    """Drive every chapter in handbook order, then the pre-wipe review."""
    # Opening thanks is printed with the banner (install.py main) before this runs.
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
    ui.info("A live Ubuntu Boot Image session is used as the host instead of the Gentoo minimal ISO.")
    ui.info("After you answer every chapter, a summary is shown before any disk is touched.")
    cfg.dry_run = ui.ask_yes_no("Dry-run only (print commands, do not change disks)?", False)
    if getattr(cfg, "_resume_decided", False):
        if cfg.resume:
            ui.info("Resuming the interrupted install. The mounted target is kept.")
        else:
            ui.info("The previous target was unmounted. This install starts from the beginning.")
    else:
        cfg.resume = ui.ask_yes_no("Resume a previous interrupted install?", False)
    cfg.mountpoint = ui.prompt("Target mountpoint", cfg.mountpoint)


def _media(cfg: InstallConfig) -> None:
    """Handbook: Media — we treat Ubuntu Boot Image live as the installation medium."""
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
    if cfg.firmware == "uefi":
        bits = detect.uefi_platform_size()
        cfg.uefi_bits = bits if bits in {32, 64} else 64
        if cfg.uefi_bits == 32:
            ui.warn(
                "Firmware is 32-bit UEFI (IA32), including mixed mode. "
                "GRUB with the i386-efi target is the loader that can boot this machine."
            )
    else:
        cfg.uefi_bits = 0
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
    _ask_dns(cfg)
    cfg.ntp = ui.ask_yes_no(
        "Synchronise clock before downloading stage3? "
        "(uses Ubuntu ntpsec-ntpdate when installed; needed for stage3 GPG checks)",
        True,
    )
    if cfg.ntp:
        ui.info(
            "If sync tools are missing on the live USB, install Ubuntu package "
            "ntpsec-ntpdate: sudo apt-get install -y ntpsec-ntpdate"
        )


def _ask_dns(cfg: InstallConfig) -> None:
    """Guru-styled DNS picker: Cloudflare / Google / OpenDNS / custom."""
    print()
    print(ui.paint("  ▸ DNS resolvers", ui.C.BOLD, ui.C.PURPLE))
    ui.guru_note(
        "Same nameservers for the live USB and the installed Gentoo — "
        "stage3 download and first boot both use them."
    )
    default = cfg.dns_provider if cfg.dns_provider in {k for k, _ in catalog.DNS_MENU} else "cloudflare"
    cfg.dns_provider = ui.menu(
        "DNS resolvers (live host + installed system)",
        catalog.DNS_MENU,
        default,
    )
    if cfg.dns_provider == "custom":
        raw = ui.prompt(
            "Custom nameservers (space-separated IPs)",
            cfg.dns_servers or cfg.static_dns or "1.1.1.1 1.0.0.1",
        )
        servers = catalog.dns_servers_for("custom", raw)
        if not servers:
            ui.warn("No nameservers typed — falling back to Cloudflare.")
            cfg.dns_provider = "cloudflare"
            servers = catalog.dns_servers_for("cloudflare")
    else:
        servers = catalog.dns_servers_for(cfg.dns_provider)
    cfg.dns_servers = " ".join(servers)
    cfg.static_dns = servers[0]
    ui.ok(f"DNS: {cfg.dns_provider} → {cfg.dns_servers}")


def _refuse_live_installer_path(path: str, *, action: str = "select") -> None:
    """H7: wizard refuse for live Ubuntu Boot Image medium (typed path or menu)."""
    if path and detect.is_live_installer_disk(path):
        raise SystemExit(
            f"Refusing to {action} the live installer medium ({path}). "
            "Choose a different disk."
        )


def _existing_role_devices(cfg: InstallConfig) -> list[tuple[str, str]]:
    """Named ESP/boot/root/swap nodes for existing-layout confirms."""
    roles: list[tuple[str, str]] = []
    if cfg.existing_esp:
        roles.append(("ESP", cfg.existing_esp))
    if cfg.existing_boot and cfg.existing_boot not in {cfg.existing_esp, ""}:
        roles.append(("boot", cfg.existing_boot))
    if cfg.existing_root:
        roles.append(("root", cfg.existing_root))
    if cfg.existing_swap:
        roles.append(("swap", cfg.existing_swap))
    return roles


def _confirm_reformat_existing(cfg: InstallConfig) -> None:
    """H8: wipe_mode none keeps the table but still formats named ESP/root/swap."""
    roles = _existing_role_devices(cfg)
    if not roles:
        raise SystemExit("No ESP/root/swap devices named to reformat.")
    ui.danger("Keep-partition-table mode still formats these devices (mkfs/mkswap):")
    for role, path in roles:
        ui.warn(f"  {role}: {path}")
    ui.info("If you enable LUKS later, luksFormat also runs on the root (and swap if chosen).")
    if not ui.ask_yes_no("Reformat the named ESP/root/swap devices above?", False):
        raise SystemExit("Aborted before reformatting existing partitions.")


def _confirm_mount_existing(cfg: InstallConfig) -> None:
    """H8-B: wipe_mode mount never mkfs/luksFormat — confirm named nodes only."""
    roles = _existing_role_devices(cfg)
    if not roles:
        raise SystemExit("No ESP/root/swap devices named to mount.")
    ui.warn("Expert mount-existing: no wipefs, mkfs, mkswap, or luksFormat.")
    ui.info("These devices will be opened (if LUKS) and mounted as-is:")
    for role, path in roles:
        ui.warn(f"  {role}: {path}")
    ui.info("Install aborts if the on-disk filesystem type does not match your choice.")
    if not ui.ask_yes_no("Mount the named ESP/root/swap devices without formatting?", False):
        raise SystemExit("Aborted before mounting existing partitions.")


def _disks(cfg: InstallConfig) -> None:
    """Handbook: Disks — layout, filesystem, LUKS, swap. Most destructive choices."""
    ui.chapter("4", "Preparing the disks", f"{HANDBOOK}/Installation/Disks")
    disks = detect.list_disks()
    live = detect.live_boot_disk()
    choices = []
    omitted_live: list[str] = []
    for d in disks:
        tag = f"{d['size']}  {d['model']}  {d['tran']}"
        if d["removable"]:
            tag += "  [removable]"
        # H7: omit the live installer medium from the wipe target menu.
        if detect.is_live_installer_disk(d["path"]) or (live and d["path"] == live):
            omitted_live.append(d["path"])
            continue
        choices.append((d["path"], tag))
    if omitted_live:
        for path in omitted_live:
            ui.warn(
                f"Omitting {path} [possible live USB] — the live installer medium "
                "cannot be wiped or formatted."
            )
    if not choices:
        ui.warn("No wipeable disks detected (or only the live USB). You can type a device path.")
        cfg.target_disk = ui.prompt("Target block device", cfg.target_disk or "/dev/sda")
        _refuse_live_installer_path(cfg.target_disk, action="select")
    else:
        cfg.target_disk = ui.menu("Target disk (will be partitioned if wipe is enabled)", choices, choices[0][0])
        _refuse_live_installer_path(cfg.target_disk, action="select")
    _warn_small_disk(cfg)
    _note_ssd_trim(cfg)

    wipe_default = "disk"
    if not cfg.wipe_disk:
        if getattr(cfg, "wipe_partitions", None):
            wipe_default = "partitions"
        elif cfg.wipe_mode == "mount":
            wipe_default = "mount"
        else:
            wipe_default = "none"
    cfg.wipe_mode = ui.menu(
        "What should be wiped before install?",
        [
            ("disk", "Entire disk — new partition table (recommended)"),
            ("partitions", "Selected partitions only (pick one, several, or all) — then install"),
            (
                "none",
                "Keep partition table; reformat selected ESP/root/swap for Gentoo",
            ),
            (
                "mount",
                "Expert: mount existing filesystems (no mkfs / no luksFormat)",
            ),
        ],
        wipe_default,
    )
    cfg.wipe_partitions = []
    if cfg.wipe_mode == "disk":
        cfg.wipe_disk = True
        ui.info(f"The whole disk {cfg.target_disk} will be wiped and repartitioned.")
    elif cfg.wipe_mode == "partitions":
        cfg.wipe_disk = False
        disk = cfg.target_disk.rstrip("/")
        part_rows: list[tuple[str, str]] = []
        for p in detect.list_partitions():
            path = str(p.get("path") or "")
            if not path.startswith("/dev/"):
                continue
            parent = str(p.get("disk") or "") or (detect.parent_block_device(path) or "")
            if parent != disk and not path.startswith(disk + "p") and not (
                path.startswith(disk) and path != disk and not disk[-1:].isdigit()
            ):
                continue
            fs = p.get("fstype") or "unknown"
            size = p.get("size") or "?"
            label = p.get("label") or p.get("parttypename") or ""
            tag = f"{size}  {fs}"
            if label:
                tag += f"  {label}"
            part_rows.append((path, tag))
        if not part_rows:
            ui.warn(
                "No partitions listed on that disk. Type paths to wipe "
                "(space-separated), or choose entire-disk wipe instead."
            )
            typed = ui.prompt("Partition paths to wipe (empty = none)", "")
            cfg.wipe_partitions = typed.split() if typed.strip() else []
        else:
            ui.info(
                "Space toggles each partition. Select all wipes every listed "
                "partition on this disk. Enter continues."
            )
            cfg.wipe_partitions = ui.paged_checklist(
                f"Partitions to wipe on {disk}",
                part_rows,
                selected=[],
                page_size=14,
                select_all=True,
            )
            if not cfg.wipe_partitions:
                ui.warn("No partitions selected — nothing will be wiped before install.")
        if cfg.wipe_partitions:
            for part in cfg.wipe_partitions:
                _refuse_live_installer_path(part, action="wipe")
            ui.ok("Will wipe: " + ", ".join(cfg.wipe_partitions))
    elif cfg.wipe_mode == "mount":
        cfg.wipe_disk = False
        ui.warn(
            "Expert mount-existing: partition table and filesystems stay. "
            "No wipefs, mkfs, mkswap, or luksFormat — fstype must already match."
        )
    else:
        cfg.wipe_disk = False
        ui.info(
            "Partition table stays. Selected ESP, root, and swap will still be "
            "formatted (mkfs/mkswap; luksFormat if encryption is enabled)."
        )

    if cfg.firmware == "uefi":
        cfg.partition_table = "gpt"
        ui.info("UEFI selected — GPT will be used (handbook).")
    else:
        cfg.partition_table = ui.menu(
            "Partition table (BIOS)",
            [("mbr", "MBR/DOS — handbook BIOS path"), ("gpt", "GPT with BIOS boot partition")],
            "mbr",
        )

    existing_wipes = {"partitions", "none", "mount"}
    layout_default = "existing" if cfg.wipe_mode in existing_wipes else "handbook"
    if cfg.wipe_mode == "mount":
        # Expert path is always existing-layout; skip the full layout menu.
        cfg.layout = "existing"
        ui.info("Mount-existing forces layout=existing (named ESP/root/swap).")
    else:
        cfg.layout = ui.menu(
            "Disk layout (merged from oddlama, GForge, l0rdg3x, handbook)",
            [
                ("handbook", "Handbook default: ESP/boot + swap + root"),
                (
                    "btrfs-subvols",
                    "GPT + Btrfs subvolumes (@ @home @log @cache @tmp + @swap mount for a swapfile)",
                ),
                ("lvm", "LVM: ESP + PV with root and optional swap LVs"),
                ("lvm-luks", "LVM-on-LUKS (ESP unencrypted)"),
                ("mdraid1", "Software RAID1 of two disks, then handbook layout"),
                ("zfs", "ZFS root pool (needs zfsutils-linux on Ubuntu)"),
                ("existing", "Use partitions you already created (or wiped above)"),
            ],
            layout_default,
        )
    if cfg.layout == "mdraid1":
        others = [c for c in choices if c[0] != cfg.target_disk]
        if len(others) >= 1:
            cfg.extra_raid_disk = ui.menu("Second RAID1 disk", others, others[0][0])
        else:
            cfg.extra_raid_disk = ui.prompt("Second RAID1 disk", "/dev/sdb")
        _refuse_live_installer_path(cfg.extra_raid_disk, action="select")
    if cfg.layout == "existing" or cfg.wipe_mode in existing_wipes:
        if cfg.layout != "existing" and cfg.wipe_mode != "disk":
            ui.info(
                "Selected-partition / keep-table installs use your existing partition layout."
            )
            cfg.layout = "existing"
        cfg.wipe_disk = False
        cfg.existing_esp = ui.prompt("ESP / boot partition", cfg.existing_esp or "/dev/sda1")
        cfg.existing_root = ui.prompt("Root partition (or LUKS container)", cfg.existing_root or "/dev/sda3")
        cfg.existing_swap = ui.prompt("Swap partition (empty to skip)", cfg.existing_swap)
        refuse_action = "mount" if cfg.wipe_mode == "mount" else "format"
        for path in (cfg.existing_esp, cfg.existing_root, cfg.existing_swap):
            _refuse_live_installer_path(path, action=refuse_action)
        # H8: wipe_mode none still mkfs's these nodes — confirm by name.
        if cfg.wipe_mode == "none":
            _confirm_reformat_existing(cfg)
        elif cfg.wipe_mode == "mount":
            _confirm_mount_existing(cfg)

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

    # btrfs-subvols creates @swap (nodatacow, mounted at /swap) but does not
    # enable swap by itself — the user still picks file / partition / zram / none.
    ram_gib = detect.mem_gib()
    ui.guru_note(
        f"This machine has ~{ram_gib} GiB RAM. Swap is for lower-spec machines — "
        "not suggested at 32 GiB RAM or more (pick none unless you know you need it)."
    )
    swap_default = "none" if ram_gib >= 32 else ("file" if cfg.layout == "btrfs-subvols" else "partition")
    if cfg.layout == "btrfs-subvols":
        ui.info(
            "Btrfs @swap is only a nodatacow subvolume at /swap for a swap *file*. "
            "It is not swap yet. Pick how (or whether) this install uses swap/ZRAM."
        )
        cfg.swap_mode = ui.menu(
            "Swap (independent of Btrfs subvolumes)",
            [
                ("file", "Swap file on @swap (/swap/swapfile) — matches this layout"),
                ("partition", "Dedicated swap partition (extra GPT partition)"),
                ("zram", "ZRAM only (compressed RAM; no disk swap)"),
                ("both", "Swap file on @swap + ZRAM"),
                ("none", "No swap and no ZRAM"),
            ],
            swap_default if swap_default in {"file", "none"} else "file",
        )
    else:
        cfg.swap_mode = ui.menu(
            "Swap",
            [
                ("partition", "Dedicated swap partition (handbook)"),
                ("file", "Swap file on the root filesystem"),
                ("zram", "ZRAM only"),
                ("both", "Swap partition/file + ZRAM"),
                ("none", "No swap"),
            ],
            swap_default if swap_default in {"partition", "none"} else "partition",
        )
    if cfg.swap_mode in {"partition", "file", "both"}:
        suggested = max(2, min(8, detect.mem_gib()))
        cfg.swap_size_gib = int(ui.prompt("Swap size in GiB", str(suggested)))
    if cfg.swap_mode in {"zram", "both"}:
        cfg.zram = True
    elif cfg.swap_mode == "none":
        cfg.zram = False
    else:
        # partition / file — ZRAM is an optional extra, not implied by disk layout.
        cfg.zram = ui.ask_yes_no(
            "Also configure ZRAM (compressed RAM swap, independent of disk swap)?",
            False if cfg.layout == "btrfs-subvols" else cfg.swap_mode != "none",
        )

    ui.info("Set the console keymap before any passphrase. A LUKS passphrase is typed in this layout.")
    cfg.keymap = ui.prompt("Console keymap (e.g. us, uk, de, fr)", cfg.keymap or "us")
    if detect.apply_live_keymap(cfg.keymap):
        ui.ok(f"Live text console keymap set to {cfg.keymap}.")
    elif cfg.keymap and cfg.keymap != "us":
        ui.info("A graphical terminal keeps the desktop layout. loadkeys only changes a text console.")

    if cfg.wipe_mode == "mount":
        # H8-B: open existing containers only — never luksFormat in this path.
        cfg.luks = ui.ask_yes_no(
            "Open an existing LUKS2 container on the named root partition?",
            False,
        )
        if cfg.luks:
            cfg.luks_passphrase = ui.ask_password("LUKS passphrase (open existing)")
            if cfg.existing_swap and cfg.swap_mode in {"partition", "both"}:
                cfg.encrypted_swap = ui.ask_yes_no(
                    "Also open an existing LUKS2 swap volume (same passphrase)?",
                    False,
                )
            else:
                cfg.encrypted_swap = False
            cfg.tpm2_unlock = ui.ask_yes_no(
                "Prepare TPM2 auto-unlock scripts (PCR 7+14, after first boot)?",
                detect.has_tpm2(),
            )
        else:
            cfg.encrypted_swap = False
            cfg.tpm2_unlock = False
    else:
        # Wipe / full-disk paths: LUKS default ON; clear opt-out.
        if cfg.layout != "lvm-luks":
            ui.guru_note(
                "Encrypting root (and home on that volume) keeps the disk unreadable if "
                "the machine is lost or stolen — cold plugs cannot read your files."
            )
            ui.info(
                "Default is Yes for wipe/full-disk installs. Say No only for VMs, "
                "throwaway disks, or when you knowingly opt out of disk encryption."
            )
        cfg.luks = cfg.layout == "lvm-luks" or ui.ask_yes_no(
            "LUKS2 encrypt the root (and home on it; optionally swap)?",
            True,
        )
        if not cfg.luks and cfg.layout != "lvm-luks":
            ui.warn("LUKS opted out — the root filesystem will be readable if the disk leaves your hands.")
        if cfg.luks:
            cfg.luks_passphrase = ui.ask_password("LUKS passphrase")
            ui.info(
                "With GRUB (handbook UEFI), the first unlock is at the GRUB prompt "
                "(cryptodisk); the initramfs may ask again until single-pass unlock is designed."
            )
            if cfg.swap_mode in {"partition", "both"}:
                if cfg.layout == "lvm-luks":
                    ui.info("LVM-on-LUKS already encrypts the volume group, so the swap LV is not wrapped a second time.")
                    cfg.encrypted_swap = True
                else:
                    ui.info("Encrypted swap is a separate LUKS2 volume opened as /dev/mapper/swap (same passphrase as root).")
                    ui.guru_note(
                        "Unencrypted swap can leak passwords and keys from RAM to disk — "
                        "encrypting swap closes that hole when the drive leaves your hands."
                    )
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
        ui.info(
            "The ESP is mounted at /efi (handbook). GRUB keeps kernels on /boot. "
            "systemd-boot and EFI stub install the kernel onto /efi."
        )


def _stage(cfg: InstallConfig) -> None:
    """Handbook: Stage file — stage3 on public autobuilds; OpenRC/systemd; desktop vs hardened/musl."""
    ui.chapter("5", "Installing the Gentoo installation files", f"{HANDBOOK}/Installation/Stage")
    ui.info(
        "The stage tarball is the base system archive. "
        "Public Gentoo autobuilds publish stage3 only — stage1 and stage2 tarballs "
        "are not on distfiles mirrors (latest-stage1/2 would 404 everywhere)."
    )
    ui.info(
        "Stage3 does not include a bootable kernel image — "
        "you choose which kernel package to emerge in chapter 7."
    )
    cfg.stage_level = ui.menu(
        "Stage tarball (handbook default is stage3; public mirrors are stage3-only)",
        [
            (
                "stage3",
                "stage3 — full base system (recommended). Ready for Portage after extract.",
            ),
            (
                "stage2",
                "stage2 — not on public autobuilds (expert; installer refuses download)",
            ),
            (
                "stage1",
                "stage1 — not on public autobuilds (expert; installer refuses download)",
            ),
        ],
        "stage3",
    )
    if cfg.stage_level != "stage3":
        ui.danger(
            f"{cfg.stage_level} is not published on public Gentoo autobuild mirrors."
        )
        ui.warn(
            "This installer will not wget latest-stage1/2 — every mirror would answer 404."
        )
        if not ui.ask_yes_no("Continue with stage3 instead (required for public mirrors)?", True):
            raise SystemExit(
                "Aborted. Public autobuilds are stage3-only; choose stage3."
            )
        cfg.stage_level = "stage3"
        ui.ok("Using stage3.")
    ui.info("Kernel package selection comes later (chapter 7), after the base-system questions.")
    ui.info(
        "OpenRC is Gentoo's default init (PID 1). That is not the same as the "
        "without-systemd community overlay: an OpenRC stage3 still uses "
        "sys-apps/systemd-utils for udev (and related tools), not systemd as PID 1."
    )
    ui.info(
        "OpenRC keeps shell-script services, pairs with elogind for desktops, and "
        "does not require journald/timers. Many other distros default to full "
        "systemd; both OpenRC and systemd stages are supported here."
    )
    cfg.init_system = ui.menu(
        "Init system",
        [
            (
                "openrc",
                "OpenRC — Gentoo default PID 1 (udev via systemd-utils, not systemd-as-init)",
            ),
            (
                "systemd",
                "systemd — full PID 1 (common on other distros; handbook alternative)",
            ),
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
    known_profiles = {key for key, _label in profile_choices}
    preferred = cfg.profile if cfg.profile in known_profiles else profile_choices[0][0]
    cfg.profile = ui.menu("Portage profile (23.0 tree)", profile_choices, preferred)
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
        detected_abi = "x86-64-v3" if cfg.architecture == "amd64" and detect.supports_x86_64_v3() else "x86-64"
        if detected_abi == "x86-64-v3":
            ui.info("This CPU can use x86-64-v3 binary packages (Haswell / Excavator and newer).")
        else:
            ui.info("This CPU stays on generic x86-64 binary packages.")
        highlight = cfg.binhost_abi if cfg.binhost_abi == "x86-64-v3" else detected_abi
        cfg.binhost_abi = ui.menu(
            "Binhost ABI",
            [("x86-64", "Generic x86-64"), ("x86-64-v3", "x86-64-v3 (Haswell and newer)")],
            highlight if highlight in {"x86-64", "x86-64-v3"} else detected_abi,
        )
        cfg.binpkg_respect_use = ui.ask_yes_no(
            "Require binpkgs to match USE exactly? (No = use binaries even if USE differs; "
            "avoids compiling glibc's ~500 locales from source)",
            False,
        )
    nvidia_driver = "nouveau"
    if detect.has_nvidia():
        ui.warn("The proprietary NVIDIA driver needs a matched kernel module and often fails on a first install.")
        nvidia_driver = ui.menu(
            "NVIDIA driver",
            [
                ("nouveau", "Nouveau — open source, in the kernel"),
                ("nvidia", "Proprietary NVIDIA driver"),
            ],
            "nouveau",
        )
    gpu = detect.detect_gpu(nvidia=nvidia_driver)
    gpu_names = detect.gpu_devices()
    if gpu_names:
        ui.info("Graphics hardware: " + "; ".join(gpu_names))
    else:
        ui.info("No display controller was listed by lspci.")
    ui.info(f"VIDEO_CARDS for that hardware: {gpu}")
    ui.info("Empty Enter keeps this value.")
    cfg.video_cards = ui.prompt("VIDEO_CARDS", gpu)
    cfg.input_devices = detect.suggest_input_devices()
    ui.info(f"INPUT_DEVICES for this machine: {cfg.input_devices}")
    cfg.input_devices = ui.prompt("INPUT_DEVICES", cfg.input_devices)
    for flag in ("alsa", "wifi", "bluetooth"):
        ui.info(f"{flag}: {detect.hardware_note(flag)}")
    ui.info("USE flags control optional features inside packages (audio, GUI toolkits, networking).")
    ui.info("The flag name is the value written to make.conf. A detected device is shown beside alsa, wifi, and bluetooth.")
    ui.info("Pre-ticked flags match this machine and the init/desktop choices you already made.")
    extra = ui.checklist(
        "Global USE flags (handbook ch. 6) — Space toggles, Enter continues",
        _use_rows(),
        selected=_default_use(cfg),
    )
    typed = ui.prompt("Extra USE tokens to add (space-separated, empty for none)", "")
    if typed.strip():
        extra = list(dict.fromkeys(extra + typed.split()))
    cfg.extra_use = extra
    cfg.use_flags = " ".join(extra)
    # Always show the applied set — testers could not see which flags were chosen.
    if cfg.extra_use:
        ui.ok("USE flags that will be written to make.conf:")
        ui.info("  " + " ".join(cfg.extra_use))
    else:
        ui.warn("No global USE flags were selected.")
    cfg.lto = ui.ask_yes_no("Enable LTO in CFLAGS (experimental, slower compiles)?", False)
    cfg.hardened_flags = cfg.stage_variant == "hardened" or ui.ask_yes_no("Add hardened CFLAGS / USE (pie ssp)?", False)
    ui.info(
        "If emerge gets HTTP 404 from the chosen mirror, the installer checks another mirror and retries that emerge."
    )
    cfg.gentoo_mirrors = ui.prompt(
        "GENTOO_MIRRORS URL (empty = curated bases + optional deep mirrorselect)",
        cfg.gentoo_mirrors,
    )
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
        (f"default/linux/{arch}/23.0/desktop", "Desktop — libraries for Wayland and X"),
        (f"default/linux/{arch}/23.0", "Base"),
    ]
    if systemd:
        rows = [
            (f"default/linux/{arch}/23.0/desktop/systemd", "Desktop systemd — libraries for Wayland and X"),
            (f"default/linux/{arch}/23.0/systemd", "Base systemd"),
        ]
    return rows


def _video_review(cfg: InstallConfig) -> str:
    """VIDEO_CARDS value plus the display controller lspci reported."""
    value = cfg.video_cards or "auto"
    names = detect.gpu_devices()
    if names:
        return value + " — " + "; ".join(names)
    return value


def _use_rows() -> list[tuple[str, str]]:
    """USE checklist rows. Hardware flags include the device found on this machine."""
    rows: list[tuple[str, str]] = []
    for flag, desc in catalog.USE_FLAGS:
        note = detect.hardware_note(flag)
        if note:
            desc = f"{note} — {desc}"
        rows.append((flag, desc))
    return rows


def _default_use(cfg: InstallConfig) -> list[str]:
    """Pre-tick USE flags that match the chosen init system and desktop type."""
    flags = ["dist-kernel", "wayland"]
    if cfg.install_type == "desktop":
        flags.append("X")
    if cfg.init_system == "openrc":
        flags.append("elogind")
    else:
        flags.append("systemd")
    if cfg.network_stack == "networkmanager" or cfg.install_type == "desktop":
        flags.append("networkmanager")
    if detect.has_bluetooth():
        flags.append("bluetooth")
    if detect.has_wifi():
        flags.append("wifi")
    if detect.has_sound():
        flags.append("alsa")
    return flags


def _kernel(cfg: InstallConfig) -> None:
    """Handbook: Kernel — dist-kernel vs source, firmware, dracut, UKI."""
    ui.chapter("7", "Configuring the Linux kernel", f"{HANDBOOK}/Installation/Kernel")
    ui.info(
        "Stage3 left the root filesystem without a bootable kernel. "
        "Pick the Portage package that will be emerged into /boot (and onto /efi for some loaders)."
    )
    ui.info(
        "CachyOS options enable the CachyOS-kernels overlay. "
        "There is no Ubuntu LTS ebuild and no separate gentoo-*-lts package in Portage. "
        "Gentoo’s LTS path is stable sys-kernel/gentoo-sources (Gentoo stabilizes upstream longterm)."
    )
    known = {name for name, _desc in catalog.kernel_menu_choices()}
    preferred = cfg.kernel if cfg.kernel in known else "gentoo-kernel-bin"
    cfg.kernel = ui.menu(
        "Kernel package (always asked for stage3 — not bundled in the stage tarball)",
        catalog.kernel_menu_choices(),
        preferred,
    )
    if catalog.kernel_uses_stable_lts(cfg.kernel):
        ui.info(
            "Gentoo LTS selected: emerging sys-kernel/gentoo-sources with Gentoo stable keywords "
            f"({catalog.kernel_atom(cfg.kernel)}). Testing (~arch) will not be applied to that package."
        )
    if catalog.kernel_overlay(cfg.kernel) != "gentoo":
        ui.info(
            f"{cfg.kernel} comes from the {catalog.kernel_overlay(cfg.kernel)} overlay, "
            "which will be enabled before emerge."
        )
    cfg.linux_firmware = ui.ask_yes_no("Install linux-firmware?", True)
    if not cfg.linux_firmware:
        if detect.is_virtual_machine():
            ui.info("linux-firmware also carries AMD CPU microcode. Skipping it is reasonable in a virtual machine.")
        elif detect.detect_amd_cpu():
            ui.warn(
                "This is physical AMD hardware. linux-firmware carries AMD microcode. "
                "Skipping it leaves the CPU unpatched."
            )
        else:
            ui.warn("linux-firmware carries AMD CPU microcode. Skip it in a VM. Keep it on real AMD hardware.")
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
    ui.info(
        f"Console keymap is {cfg.keymap}. It was set before any passphrase, "
        "and this is the layout the installed system will use."
    )
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
    ui.info(
        "One firewall stack only: nftables here excludes catalogue net-firewall/ufw. "
        "Say no if you want UFW (deny incoming / allow outgoing) instead."
    )
    cfg.nftables = ui.ask_yes_no(
        "Install nftables and drop unsolicited inbound connections?",
        False if cfg.ufw else True,
    )
    if cfg.nftables and cfg.sshd:
        ui.info("The firewall allows SSH on port 22. Other new inbound connections are dropped.")
        cfg.ufw = False
        cfg.ufw_localsend = False
    elif cfg.nftables:
        ui.info("The firewall drops new inbound connections. SSH is not opened.")
        cfg.ufw = False
        cfg.ufw_localsend = False
    else:
        ui.info("nftables skipped. UFW (when chosen) uses deny-incoming / allow-outgoing.")
        cfg.ufw = ui.ask_yes_no(
            "Install and enable UFW with deny-incoming / allow-outgoing defaults?",
            True if cfg.ufw else False,
        )
        if cfg.ufw:
            if cfg.sshd:
                ui.info("UFW will allow SSH on port 22.")
            port_desc = ", ".join(f"{p['port']}/{p['proto']}" for p in LOCALSEND_UFW_PORTS)
            cfg.ufw_localsend = ui.ask_yes_no(
                f"Also open LocalSend-style ports ({port_desc})? (opt-in)",
                False,
            )
        else:
            cfg.ufw_localsend = False
            ui.info("No installer firewall — you may still type net-firewall/ufw later in the catalogue.")
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
        cfg.cron if cfg.cron in {"cronie", "fcron", "none"} else "cronie",
    )
    if cfg.fstrim and cfg.init_system == "openrc" and cfg.cron == "none":
        ui.info("Weekly fstrim runs from cron. cronie will be enabled for that job.")
        cfg.cron = "cronie"


def _bootloader(cfg: InstallConfig) -> None:
    """Handbook: Bootloader — multiple loaders plus Plymouth theme selection."""
    ui.chapter("10", "Configuring the bootloader", f"{HANDBOOK}/Installation/Bootloader")
    loaders = list(catalog.BOOTLOADERS)
    if cfg.firmware != "uefi":
        loaders = [item for item in loaders if item[0] not in {"systemd-boot", "refind", "efistub"}]
    elif cfg.uefi_bits == 32:
        ui.warn("32-bit UEFI: only GRUB is offered. systemd-boot, rEFInd, and EFI stub need 64-bit firmware.")
        loaders = [item for item in loaders if item[0] == "grub"]
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
    if cfg.firmware == "uefi" and cfg.bootloader in {"systemd-boot", "efistub"}:
        ui.info("The kernel is installed on /efi. systemd-boot and EFI stub load it from the EFI System Partition.")
    elif cfg.firmware == "uefi" and cfg.bootloader == "grub":
        ui.info("GRUB keeps the kernel on /boot, on the root filesystem. The ESP stays mounted at /efi.")
        if cfg.luks:
            ui.info(
                "LUKS + GRUB enables GRUB_ENABLE_CRYPTODISK so GRUB can read encrypted /boot. "
                "You type the disk passphrase at GRUB first; initramfs may prompt again."
            )
    cfg.plymouth = ui.ask_yes_no("Install Plymouth boot splash?", cfg.install_type == "desktop")
    if cfg.plymouth:
        _ask_plymouth_theme(cfg)
        if cfg.init_system == "openrc":
            cfg.plymouth_openrc_plugin = ui.ask_yes_no("Install plymouth-openrc-plugin?", True)
    else:
        cfg.plymouth_theme = ""
        cfg.plymouth_themes = []
        cfg.plymouth_openrc_plugin = False
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


def _ask_plymouth_theme(cfg: InstallConfig) -> None:
    """Record the splash the user checked. Never fill in spinner for them."""
    choices = catalog.plymouth_theme_choices()
    labels = dict(choices)
    known = {theme_id for theme_id, _desc in choices}
    cfg.plymouth_themes = ui.paged_checklist(
        "Plymouth themes to install",
        choices,
        selected=[theme for theme in cfg.plymouth_themes if theme in known],
        page_size=12,
    )
    if not cfg.plymouth_themes:
        ui.warn("Nothing was checked. Spinner is not added unless you check it.")
        cfg.plymouth_themes = ui.paged_checklist(
            "Plymouth themes to install",
            choices,
            selected=[],
            page_size=12,
        )
    if not cfg.plymouth_themes:
        ui.warn("Plymouth will be installed without changing its theme.")
        cfg.plymouth_theme = ""
        return
    if len(cfg.plymouth_themes) == 1:
        cfg.plymouth_theme = cfg.plymouth_themes[0]
        ui.ok(f"Plymouth theme: {labels.get(cfg.plymouth_theme, cfg.plymouth_theme)}")
        return
    others = [theme for theme in cfg.plymouth_themes if theme != "spinner"]
    # A list that still contains spinner must not highlight spinner when
    # another theme was checked. One theme skips this menu entirely.
    if cfg.plymouth_theme in others:
        preferred = cfg.plymouth_theme
    elif others:
        preferred = others[0]
    else:
        preferred = cfg.plymouth_themes[0]
    ui.info("The highlighted theme is the one that boots. The others are only installed.")
    cfg.plymouth_theme = ui.menu(
        "Which Plymouth theme should boot?",
        [(theme, labels.get(theme, theme)) for theme in cfg.plymouth_themes],
        preferred,
    )


def _dotfile_review(cfg: InstallConfig) -> str:
    """Short review label for the compositor configs that will be copied."""
    if catalog.dotfiles_skipped(cfg.shell):
        return "skipped — shell provides the session"
    if not cfg.wm_dotfiles:
        return "installer default"
    parts: list[str] = []
    for item in cfg.wm_dotfiles:
        atom, _, ident = str(item).partition("=")
        name = atom.split("/")[-1] or atom
        label = ident or "installer"
        for choice in catalog.dotfile_choices(atom):
            if choice.id == ident:
                label = choice.id
                break
        parts.append(f"{name}: {label}")
    return ", ".join(parts) or "installer default"


def _wm_dotfiles(cfg: InstallConfig) -> None:
    """Ask for a GitHub config when Hyprland or Niri was selected."""
    if catalog.dotfiles_skipped(cfg.shell):
        cfg.wm_dotfiles = []
        return
    atoms = [atom for atom in cfg.window_managers if catalog.dotfile_choices(atom)]
    if not atoms:
        cfg.wm_dotfiles = []
        return
    ui.info(
        "Window manager dotfiles. Hyprland: installer Lua default, end-4, or ML4W. "
        "Niri: installer or upstream default. "
        "A GitHub choice is downloaded from the latest commit on that branch. "
        "Only the compositor config is copied; upstream install scripts are not run. "
        "Hyprland configs are Lua (hyproverlay tip is Lua-only from 0.57)."
    )
    current: dict[str, str] = {}
    for item in cfg.wm_dotfiles or []:
        key, _, value = str(item).partition("=")
        if key and value:
            current[key] = value
    kept: list[str] = []
    for atom in atoms:
        choices = catalog.dotfile_choices(atom)
        title = next((pkg.desc for pkg in catalog.WINDOW_MANAGERS if pkg.atom == atom), atom)
        default = current.get(atom, "installer")
        if default not in {choice.id for choice in choices}:
            default = "installer"
        picked = ui.menu(f"{title} dotfiles", [(choice.id, choice.label) for choice in choices], default)
        kept.append(f"{atom}={picked}")
    cfg.wm_dotfiles = kept
    packages = catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, kept)
    if packages:
        ui.info("Gentoo packages from the chosen configs will be installed: " + ", ".join(packages))
        ui.info(
            "Quickshell, fuzzel, cliphist, swaync, wlogout, and brightnessctl "
            "are not in the Gentoo tree this installer uses, so they are not emerged."
        )


def _finalize(cfg: InstallConfig) -> None:
    """Wayland compositors and greetd. No desktop environment; no Portage shells."""
    ui.chapter("11", "Wayland session", f"{HANDBOOK}/Installation/Finalizing")
    cfg.desktop = "none"
    cfg.kde_games = False
    # Portage Wayland shells (Noctalia/DMS) removed — always compositor + utils/dots.
    cfg.shell = "none"
    ui.info("Each compositor installs its own required packages (portal, wallpaper, lock, terminal).")
    ui.info("Utilities that work on every compositor are a separate list in the next chapter.")
    ui.info(
        "Popular Wayland compositors (Hyprland, Sway, SwayFX, Niri, River, Wayfire, labwc, "
        "dwl, MangoWM, miracle-wm, Qtile, Gamescope, Weston) plus dwm and i3."
    )
    ui.info("Noctalia and DMS are not offered as Portage shells; pick starred WM configs instead.")
    cfg.window_managers = ui.paged_checklist(
        "Wayland compositors, dwl, dwm, and i3",
        catalog.wm_choices(),
        selected=[atom for atom in cfg.window_managers if atom in {p.atom for p in catalog.WINDOW_MANAGERS}],
        page_size=14,
    )
    guru_wms = [
        atom
        for atom in cfg.window_managers
        if catalog.atom_overlay(atom) == "guru"
    ]
    if guru_wms:
        ui.info(
            "GURU overlay compositors selected: "
            + ", ".join(guru_wms)
            + ". That overlay is enabled only because you chose them."
        )
    if "gui-wm/niri" in cfg.window_managers:
        ui.info(
            "Niri gets the GNOME portal, GNOME Keyring, and foot. "
            "Waybar, Wofi, Mako, swaybg, swayidle, and swaylock are in the next chapter."
        )
    _wm_dotfiles(cfg)
    graphical = bool(cfg.window_managers)
    cfg.pipewire = ui.ask_yes_no("Install PipeWire audio?", graphical)
    _ask_nonfree_codecs(cfg)
    # Wayland installs must boot to a greeter. Offering "none" failed that goal.
    if catalog.needs_login_greeter(cfg.window_managers, cfg.shell):
        cfg.display_manager = "greetd"
        who = cfg.username or "your user"
        ui.info(
            f"The installed system boots to greetd + gtkgreet. "
            f"Sign in as {who}. The compositors you chose are the sessions."
        )
        ui.info("A text console remains on tty2 if you need one.")
    else:
        cfg.display_manager = "none"
        if cfg.window_managers:
            ui.info("dwm and i3 start from the text console with startx.")
    ui.info("The next chapter is suckless tools and popular apps. There is no desktop list.")
    _ask_mise_agents(cfg)


def _ask_nonfree_codecs(cfg: InstallConfig) -> None:
    """Offer proprietary codecs only after an explicit license acceptance."""
    print()
    ui.info("—— Proprietary / non-free multimedia codecs (optional license step) ——")
    ui.info(
        "H.264/AAC and related GStreamer/FFmpeg pieces are optional. "
        "They are not installed unless you accept their licenses here."
    )
    ui.info("Packages if accepted: " + ", ".join(catalog.nonfree_codec_atoms()))
    if not ui.ask_yes_no(
        "Install proprietary / non-free multimedia codecs? (requires license accept next)",
        False,
    ):
        cfg.nonfree_codecs = False
        ui.info("Proprietary codecs skipped.")
        return
    ui.warn(
        "These packages include patented and proprietary codecs (including Fraunhofer FDK AAC). "
        "By continuing you confirm you accept those licenses and that using them is legal "
        "where you live."
    )
    if ui.ask_yes_no(
        "I accept the licenses for proprietary multimedia codecs and want them installed.",
        False,
    ):
        cfg.nonfree_codecs = True
        tokens = (cfg.accept_license or "").split()
        for lic in catalog.NONFREE_CODEC_LICENSES:
            if lic not in tokens:
                tokens.append(lic)
        if "*" not in tokens:
            # Allow other proprietary deps those ebuilds may pull.
            tokens.append("*")
        cfg.accept_license = " ".join(tokens)
        ui.ok("Proprietary codecs will be emerged after you confirm the disk.")
    else:
        cfg.nonfree_codecs = False
        ui.info("Proprietary codecs were declined and will not be installed.")


def _packages_locked_from_earlier_steps(cfg: InstallConfig) -> set[str]:
    """Atoms already chosen before the optional package lists (WMs, deps, dotfiles)."""
    locked = set(cfg.window_managers or [])
    locked.update(
        pkg.atom
        for pkg in catalog.packages_for_selection(cfg.window_managers, cfg.shell, cfg.pipewire)
    )
    locked.update(catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, cfg.wm_dotfiles))
    if getattr(cfg, "nonfree_codecs", False):
        locked.update(catalog.nonfree_codec_atoms())
    if catalog.needs_login_greeter(cfg.window_managers, cfg.shell) or cfg.display_manager == "greetd":
        locked.add("gui-libs/greetd")
        locked.add("gui-apps/gtkgreet")
        locked.add("gui-wm/labwc")
        # Entire greetd stack — do not ask again in display-managers.
        # tuigreet is REMOVED_GURU (blocked if typed); gtkgreet/labwc is the path.
        locked.update({"x11-misc/lightdm", "x11-misc/sddm", "x11-misc/slim"})
    # M9: hide catalogue ufw (and duplicate nftables) when installer nftables is on.
    locked.update(catalog.firewall_catalogue_hidden(bool(cfg.nftables)))
    # When the tools chapter chose UFW, do not re-offer catalogue nftables.
    if cfg.ufw and not cfg.nftables:
        locked.add(catalog.NFTABLES_ATOM)
    return locked


def _apply_firewall_exclusion(cfg: InstallConfig) -> None:
    """M9: keep one firewall stack after catalogue / typed-atom picks."""
    # Wizard UFW path queues the atom before mutual-exclusion runs.
    if cfg.ufw and not cfg.nftables:
        if not catalog.atom_list_has(cfg.selected_packages, catalog.UFW_ATOM):
            cfg.selected_packages = list(cfg.selected_packages or []) + [catalog.UFW_ATOM]
        if not catalog.atom_list_has(cfg.extra_packages, catalog.UFW_ATOM):
            cfg.extra_packages = list(cfg.extra_packages or []) + [catalog.UFW_ATOM]
    nftables, selected, extra, warnings = catalog.enforce_firewall_mutual_exclusion(
        bool(cfg.nftables),
        list(cfg.selected_packages or []),
        list(cfg.extra_packages or []),
    )
    cfg.nftables = nftables
    cfg.selected_packages = selected
    cfg.extra_packages = extra
    if cfg.nftables:
        cfg.ufw = False
        cfg.ufw_localsend = False
    elif catalog.atom_list_has(selected, catalog.UFW_ATOM) or catalog.atom_list_has(
        extra, catalog.UFW_ATOM
    ):
        cfg.ufw = True
    for message in warnings:
        ui.warn(message)


def _ask_mise_agents(cfg: InstallConfig) -> None:
    """Optional mise + small agent set — invite only; never force a panel or passwordless sudo."""
    tools = list(cfg.mise_agent_tools) or list(DEFAULT_MISE_AGENT_TOOLS)
    print()
    ui.info("—— Optional AI agents (mise) ——")
    ui.info(
        "Optional first-login invite for mise + a small agent set — "
        "no Quickshell agents panel, no passwordless sudo, and no forced agent packages."
    )
    ui.info(
        "dev-util/mise is not in the official Gentoo tree; the invite documents the "
        "upstream mise install. Agents stay optional after first boot."
    )
    cfg.mise_agents = ui.ask_yes_no(
        f"Write a first-login invite for mise + agents ({', '.join(tools)})?",
        bool(cfg.mise_agents),
    )
    if cfg.mise_agents:
        cfg.mise_agent_tools = tools
        ui.ok("First-login invite will be placed in the user home (and HOWTO note).")
    else:
        cfg.mise_agent_tools = []
        ui.info("mise/agent invite skipped.")


def _packages(cfg: InstallConfig) -> None:
    """Suckless tools, shared Wayland utilities, and popular apps.

    Categories advance linearly with Enter = continue (no category menu /
    “apply” / Back loop that broke package selection).
    """
    ui.chapter(
        "12",
        "Suckless tools and popular apps",
        "https://packages.gentoo.org/",
    )
    ui.info("Compositor requirements are already queued. This chapter is optional apps.")
    ui.info(
        "Browsers are optional: Firefox ESR is soft-default (untick to skip); "
        "Google Chrome is opt-in only (proprietary)."
    )
    ui.info(
        "Flatpak is suggested for most desktop apps (Flathub). Tick sys-apps/flatpak or a "
        "flatpak-app/* row (Pulsar, etc.), or install from Flathub after first boot."
    )
    ui.info(
        "Packages already chosen earlier (compositors, deps, dotfiles) stay selected "
        "and locked so you can see what will install (for example end-4 → btop)."
    )
    ui.info("Each category: Space toggles, Enter continues to the next category.")
    ui.info("The top row, Select all, ticks every package in that section.")

    if ui.ask_yes_no("Enable extra Portage overlays (eselect repository)?", True):
        ui.info(
            "Handbook Wine (virtual/wine, app-emulation/wine-*) is in ::gentoo — "
            "no wine overlay is required or offered."
        )
        cfg.overlays = ui.paged_checklist(
            "Overlays from overlays.gentoo.org / eselect repository — Enter continues",
            catalog.overlay_choices(),
            selected=[name for name in cfg.overlays if name not in catalog.STALE_OVERLAY_NAMES],
            page_size=14,
        )
        more = ui.prompt("Extra overlay names (space-separated, empty for none)", "")
        if more.strip():
            cfg.overlays = list(dict.fromkeys(cfg.overlays + more.split()))
        cfg.overlays = [
            name for name in cfg.overlays if name not in catalog.STALE_OVERLAY_NAMES
        ]
        catalog.refuse_unsupported_overlays(cfg.overlays)

    locked = _packages_locked_from_earlier_steps(cfg)
    # Mutual-exclusion rows stay hidden; queued dots/deps stay visible and selected.
    hide_always = set(catalog.firewall_catalogue_hidden(bool(cfg.nftables)))
    if cfg.ufw and not cfg.nftables:
        hide_always.add(catalog.NFTABLES_ATOM)
    show_locked = locked - hide_always
    picked: list[str] = [atom for atom in cfg.selected_packages if atom not in hide_always]
    for atom in show_locked:
        if atom not in picked:
            picked.append(atom)
    if cfg.ufw and not cfg.nftables and catalog.UFW_ATOM not in picked:
        picked.append(catalog.UFW_ATOM)
        ui.info(f"{catalog.UFW_ATOM} queued from the firewall step (deny-in / allow-out).")
    niri_rows = catalog.niri_app_rows() if "gui-wm/niri" in cfg.window_managers else []
    if niri_rows and not getattr(cfg, "_software_loaded", False):
        for atom in catalog.niri_preselected(cfg.shell):
            if atom not in picked and atom not in locked:
                picked.append(atom)

    # Soft-default Firefox ESR on fresh installs; honor saved browser id on resume.
    offered_browsers = set(catalog.offered_browser_atoms())
    browser_aliases = offered_browsers | {
        "www-client/firefox-bin",
        "www-client/firefox",
        catalog.CHROME_ATOM,
    }
    have_browser = any((a.split("::", 1)[0]) in browser_aliases for a in picked)
    if not have_browser:
        if getattr(cfg, "_software_loaded", False):
            seed = catalog.browser_atoms_for(cfg.browser)
        else:
            seed = catalog.default_browser_atoms()
        for atom in seed:
            if atom not in picked:
                picked.append(atom)

    # Linear category walk. Enter finishes one list and continues to the next.
    skip = {
        "window-managers",
        "desktop-environments",
        "wayland-utils",
        "display-managers",  # greetd (or none) already decided in chapter 11
        "games",  # games category removed from package selection
    }
    steps: list[tuple[str, str, list[tuple[str, str]]]] = []
    if niri_rows:
        steps.append(("niri-apps", "Niri apps", niri_rows))
        if cfg.shell == "none":
            ui.info("Niri apps are preselected when you open that list. Untick any you do not want.")
    for key, title, pkgs in catalog.CATEGORIES:
        if key in skip:
            continue
        steps.append((key, title, pkgs))

    claimed: set[str] = set()
    for index, (key, title, packages) in enumerate(steps, start=1):
        # Hide only exclusions and atoms already claimed in an earlier category.
        # Dotfile/WM deps stay visible and pre-selected (e.g. end-4 → btop).
        hide = set(hide_always) | claimed
        visible = catalog.without_already_selected(packages, hide)
        locked_here = {atom for atom, _desc in visible if atom in show_locked}
        rows = [
            (atom, f"{desc}  [already queued]" if atom in locked_here else desc)
            for atom, desc in visible
        ]
        ui.info(f"Category {index}/{len(steps)}: {title}")
        if not rows:
            ui.info(f"{title}: every package here was already chosen earlier — continuing.")
            continue
        if key == "browsers":
            ui.info(
                "Firefox ESR starts selected. Untick for no browser; tick Chrome only if you want it."
            )
        if key == "editors":
            ui.info("Editors include Sublime Text and Pulsar (Atom’s Flatpak successor) near the top.")
        selected = ui.paged_checklist(
            f"{title} — Enter continues to the next category",
            rows,
            selected=picked,
            page_size=14,
            select_all=True,
            locked=locked_here,
        )
        visible_atoms = {atom for atom, _ in rows}
        for atom in selected:
            if atom not in picked:
                picked.append(atom)
        for atom in list(picked):
            if atom in visible_atoms and atom not in selected and atom not in locked_here:
                picked.remove(atom)
        for atom in locked_here:
            if atom not in picked:
                picked.append(atom)
        claimed.update(visible_atoms)

    extra = ui.prompt("Additional emerge atoms (space-separated, e.g. app-misc/jq media-gfx/feh)", "")
    if extra.strip():
        for atom in extra.split():
            if atom not in picked:
                picked.append(atom)
    if any(a.startswith("flatpak-app/") or a in {"gui-apps/bazaar"} for a in picked):
        if "sys-apps/flatpak" not in picked:
            picked.append("sys-apps/flatpak")
        cfg.flatpak = True
    if any(a.startswith("sys-fs/fuse") for a in picked) or any("appimage" in a.lower() for a in picked):
        if "sys-fs/fuse:0" not in picked:
            picked.append("sys-fs/fuse:0")
    allowed_guru = catalog.offered_guru_atoms(cfg.shell)
    kept: list[str] = []
    for atom in picked:
        bare, _, suffix = atom.partition("::")
        overlay = suffix or catalog.atom_overlay(bare)
        if overlay == "guru" and bare not in allowed_guru:
            ui.warn(f"Skipping {atom} — GURU packages are not offered")
            continue
        kept.append(atom)
    picked = kept
    known_wm = {pkg.atom for pkg in catalog.WINDOW_MANAGERS}
    for atom in picked:
        bare = atom.split("::", 1)[0]
        if bare in known_wm and bare not in cfg.window_managers:
            cfg.window_managers.append(bare)
    cfg.selected_packages = picked
    needed = [pkg.atom for pkg in catalog.packages_for_selection(cfg.window_managers, cfg.shell, cfg.pipewire)]
    needed += catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, cfg.wm_dotfiles)
    if cfg.nonfree_codecs:
        needed += catalog.nonfree_codec_atoms()
    cfg.browser = catalog.browser_id_from_atoms(picked)
    # Chrome (and similar proprietary picks) need ACCEPT_LICENSE beyond @FREE.
    proprietary_picks = {
        catalog.CHROME_ATOM,
        "app-editors/sublime-text",
        "app-editors/vscode",
        "app-editors/logseq-desktop-bin",
    }
    if proprietary_picks & {a.split("::", 1)[0] for a in picked}:
        tokens = (cfg.accept_license or "").split()
        if "*" not in tokens:
            tokens.append("*")
            cfg.accept_license = " ".join(tokens)
            ui.info(
                "ACCEPT_LICENSE was expanded so Google Chrome and other proprietary apps can emerge."
            )
    # extra_packages is the emerge set: catalogue picks + WMs + compositor requirements.
    cfg.extra_packages = list(dict.fromkeys(cfg.window_managers + needed + picked))
    _apply_firewall_exclusion(cfg)
    cfg.overlays = [name for name in cfg.overlays if name != "guru"]
    cfg.overlays = catalog.overlays_required_by(cfg.extra_packages, cfg.overlays)
    catalog.refuse_unsupported_overlays(cfg.overlays)
    if cfg.window_managers or any("wayland" in a or "gui-wm/" in a for a in cfg.extra_packages):
        if "wayland" not in cfg.extra_use:
            cfg.extra_use.append("wayland")
            if "wayland" not in (cfg.use_flags or "").split():
                cfg.use_flags = (cfg.use_flags + " wayland").strip()
    if cfg.overlays:
        ui.ok("Overlays that will be enabled: " + ", ".join(cfg.overlays))
    if cfg.extra_use:
        ui.ok("USE flags for make.conf: " + " ".join(cfg.extra_use))
    ui.ok(f"{len(cfg.extra_packages)} package atoms queued for emerge")


def _plymouth_review(cfg: InstallConfig) -> str:
    if not cfg.plymouth:
        return "no"
    if not cfg.plymouth_theme:
        return "installed, theme not selected"
    extra = [theme for theme in cfg.plymouth_themes if theme != cfg.plymouth_theme]
    if extra:
        return cfg.plymouth_theme + " (also " + ", ".join(extra) + ")"
    return cfg.plymouth_theme


def _login_manager_label(cfg: InstallConfig) -> str:
    atoms = cfg.extra_packages + cfg.window_managers + cfg.selected_packages
    name = catalog.resolved_display_manager(cfg.display_manager, cfg.desktop, atoms)
    if name == "none":
        return "text console"
    sessions = catalog.ensure_login_sessions(cfg.desktop, atoms, cfg.shell)
    who = cfg.username or "root"
    if sessions:
        label = "session" if len(sessions) == 1 else "sessions"
        return f"{name} — {who}, {len(sessions)} {label}"
    return f"{name} — {who}"


def _swap_uses_partition(cfg: InstallConfig) -> bool:
    return cfg.swap_mode in {"partition", "both"} and cfg.layout not in {
        "lvm",
        "lvm-luks",
        "zfs",
        "existing",
    }


def _layout_partition_paths(cfg: InstallConfig, disk: str) -> list[str]:
    """Partition nodes this layout will format after the table is written."""
    if not disk:
        return []
    if cfg.firmware == "uefi" and cfg.partition_table == "gpt":
        paths = [part_name(disk, 1)]
        idx = 2
        if _swap_uses_partition(cfg):
            paths.append(part_name(disk, idx))
            idx += 1
        paths.append(part_name(disk, idx))
        return paths
    if cfg.partition_table == "gpt":
        paths = [part_name(disk, 2)]
        idx = 3
        if _swap_uses_partition(cfg):
            paths.append(part_name(disk, idx))
            idx += 1
        paths.append(part_name(disk, idx))
        return paths
    paths = [part_name(disk, 1)]
    if _swap_uses_partition(cfg):
        paths.extend([part_name(disk, 2), part_name(disk, 3)])
    else:
        paths.append(part_name(disk, 2))
    return paths


def _wipe_devices(cfg: InstallConfig) -> list[str]:
    """Disks and partitions this commit will destroy. Empty on resume."""
    if cfg.resume:
        return []
    # H8-B: mount-existing never destroys; confirmation uses a separate path.
    if cfg.wipe_mode == "mount":
        return []
    if getattr(cfg, "wipe_partitions", None):
        # Explicit partition wipe list wins for the confirmation banner.
        return list(dict.fromkeys(cfg.wipe_partitions))
    if cfg.layout == "existing":
        named = [cfg.existing_esp, cfg.existing_boot, cfg.existing_root, cfg.existing_swap]
        return [path for path in named if path]
    disks = [cfg.target_disk]
    if cfg.layout == "mdraid1" and cfg.extra_raid_disk:
        disks.append(cfg.extra_raid_disk)
    disks = [disk for disk in disks if disk]
    if cfg.wipe_disk:
        known = detect.list_partitions()
        paths: list[str] = []
        for disk in disks:
            paths.append(disk)
            paths.extend(part["path"] for part in known if part.get("disk") == disk)
        return paths
    paths = []
    for disk in disks:
        paths.extend(_layout_partition_paths(cfg, disk))
    return paths


def _warn_before_wipe(cfg: InstallConfig) -> None:
    """Name every disk and partition the commit will wipe, including Windows ones."""
    ui.danger("Oops-Installer does not support dual boot.")
    if cfg.resume:
        ui.info("Resume keeps the files already on the target. This confirmation will not wipe it.")
        return
    if cfg.wipe_mode == "mount":
        roles = _existing_role_devices(cfg)
        ui.info("Expert mount-existing: no wipe or mkfs. Devices to mount:")
        for role, path in roles:
            ui.warn(f"  {role}: {path}")
        if not ui.ask_yes_no("Continue and mount existing filesystems without formatting?", False):
            raise SystemExit("Aborted before mounting existing partitions.")
        return
    if cfg.dry_run:
        ui.info("Dry-run prints the wipe commands and does not change the disk.")
    devices = _wipe_devices(cfg)
    if not devices:
        ui.warn("No disk was selected. Continuing will not wipe a device until one is set.")
        return
    known = {part["path"]: part for part in detect.list_partitions()}
    ui.danger("Committing to this install wipes these disks and partitions:")
    microsoft: list[str] = []
    for path in devices:
        part = known.get(path)
        if part is None and path in {cfg.target_disk, cfg.extra_raid_disk}:
            ui.warn(f"{path}  whole disk — every partition on it")
            continue
        if part is None:
            ui.warn(f"{path}  will be formatted")
            continue
        bits = [path, str(part.get("size") or "?")]
        if part.get("fstype"):
            bits.append(str(part["fstype"]))
        if part.get("label"):
            bits.append(str(part["label"]))
        if part.get("parttype"):
            bits.append(str(part["parttype"]))
        if detect.partition_is_microsoft(part):
            bits.append("WILL BE WIPED")
            microsoft.append(path)
        ui.warn("  ".join(bits))
    if microsoft:
        ui.danger(
            "A Microsoft partition is in that list. It will be wiped with the rest: "
            + ", ".join(microsoft)
        )
    elif cfg.wipe_disk and not any(path in known for path in devices):
        ui.warn("Filesystems on that disk could not be read. A Microsoft or NTFS partition on it will be wiped too.")
    question = "Continue the dry-run?" if cfg.dry_run else "Wipe these disks and partitions?"
    if not ui.ask_yes_no(question, False):
        raise SystemExit("Aborted before wiping disks or partitions.")


def _warn_small_disk(cfg: InstallConfig) -> None:
    """Handbook suggests 40 GB. Arch's floor is lower because that install does not compile."""
    size = detect.disk_size_bytes(cfg.target_disk)
    if size is None or size >= 40 * 1024**3:
        return
    gib = size / (1024**3)
    ui.warn(f"{cfg.target_disk} is {gib:.1f} GiB. The handbook suggests at least 40 GB for a root filesystem.")


def _note_ssd_trim(cfg: InstallConfig) -> None:
    """Weekly fstrim when the target is non-rotational. Unknown disks are left alone."""
    rotational = detect.disk_rotational(cfg.target_disk)
    cfg.fstrim = rotational is False
    if cfg.fstrim:
        ui.info("This disk is an SSD. Weekly fstrim will be enabled.")


def _announce_estimate(cfg: InstallConfig) -> None:
    """How long this selection should take. The nap line waits until the disk is confirmed."""
    minutes = estimate_minutes(cfg)
    ui.ok(f"Estimated install time: {format_duration(minutes)}.")


def _review(cfg: InstallConfig) -> None:
    """Last safe abort point. User must re-type the disk path before execute()."""
    ui.chapter("✓", "Review — target disk has not been changed", HANDBOOK)
    ui.info("A YAML profile of these choices is already saved. The target disk is unchanged until you confirm it.")
    rows = [
        ("Firmware", cfg.firmware),
        ("Disk", cfg.target_disk),
        (
            "Wipe",
            (
                f"entire disk ({cfg.target_disk})"
                if cfg.wipe_disk
                else (
                    "partitions: " + ", ".join(cfg.wipe_partitions)
                    if getattr(cfg, "wipe_partitions", None)
                    else (
                        "expert mount-existing (no mkfs)"
                        if cfg.wipe_mode == "mount"
                        else "keep table; reformat ESP/root/swap"
                    )
                )
            ),
        ),
        ("USE flags", " ".join(cfg.extra_use) or cfg.use_flags or "(none)"),
        ("Layout", cfg.layout),
        ("Filesystem", cfg.filesystem),
        ("LUKS", str(cfg.luks) + (" + encrypted swap" if cfg.encrypted_swap else "")),
        ("Swap", f"{cfg.swap_mode} {cfg.swap_size_gib}G"),
        ("Init", cfg.init_system),
        ("Stage", f"{cfg.stage_level}/{cfg.install_type}/{cfg.stage_variant}"),
        (
            "Hardware",
            "alsa "
            + detect.hardware_note("alsa")
            + "; wifi "
            + detect.hardware_note("wifi")
            + "; bluetooth "
            + detect.hardware_note("bluetooth"),
        ),
        ("Profile", cfg.profile),
        ("Kernel", cfg.kernel),
        ("Bootloaders", ", ".join(cfg.bootloaders) + f" (primary {cfg.bootloader})"),
        (
            "Firewall",
            (
                "nftables"
                if cfg.nftables
                else (
                    "ufw deny-in/allow-out"
                    + (" + LocalSend ports" if cfg.ufw_localsend else "")
                    if cfg.ufw
                    or catalog.atom_list_has(cfg.selected_packages, catalog.UFW_ATOM)
                    or catalog.atom_list_has(cfg.extra_packages, catalog.UFW_ATOM)
                    else "none"
                )
            ),
        ),
        ("TRIM", "weekly" if cfg.fstrim else "no"),
        ("Mirrors", cfg.gentoo_mirrors or "curated + mirrorselect -D"),
        ("Plymouth", _plymouth_review(cfg)),
        ("Hostname", cfg.hostname),
        ("DNS", f"{cfg.dns_provider}: {cfg.dns_servers}"),
        ("Timezone", cfg.timezone),
        ("Locale", cfg.locale),
        ("User", cfg.username or "(root only)"),
        ("Compositors", ", ".join(cfg.window_managers) or "none"),
        ("Dotfiles", _dotfile_review(cfg)),
        ("Browsers", catalog.browser_review_label(cfg.browser)),
        ("Non-free codecs", "yes (license accepted)" if cfg.nonfree_codecs else "no"),
        ("ZRAM", "yes" if cfg.zram else "no"),
        (
            "mise/agents invite",
            (
                "yes (" + ", ".join(cfg.mise_agent_tools) + ")"
                if cfg.mise_agents and cfg.mise_agent_tools
                else ("yes" if cfg.mise_agents else "no")
            ),
        ),
        (
            "Login manager",
            _login_manager_label(cfg),
        ),
        ("Overlays", ", ".join(cfg.overlays) or "none"),
        ("Extra packages", str(len(cfg.extra_packages))),
        ("Video", _video_review(cfg)),
        ("Input", cfg.input_devices or "libinput"),
        ("Binhost", f"yes ({cfg.binhost_abi})" if cfg.binhost else "no"),
        ("Dry-run", str(cfg.dry_run)),
        ("Estimate", format_duration(estimate_minutes(cfg))),
    ]
    ui.summary_table(rows)
    _announce_estimate(cfg)
    _warn_before_wipe(cfg)
    ui.danger(f"Continuing will operate on {cfg.target_disk}")
    if not ui.ask_yes_no("Proceed with these settings?", False):
        raise SystemExit("Aborted by user before any disk changes.")
    _confirm_disk(cfg)


def _confirm_disk(cfg: InstallConfig) -> None:
    """Require an exact re-type of the disk path (empty Enter used to skip this)."""
    _refuse_live_installer_path(cfg.target_disk, action="confirm")
    if cfg.extra_raid_disk:
        _refuse_live_installer_path(cfg.extra_raid_disk, action="confirm")
    if cfg.dry_run:
        ui.info("Dry-run: skipping disk-path retype.")
        return
    typed = ui.prompt(f"Type the disk path to confirm ({cfg.target_disk})", "")
    if typed != cfg.target_disk:
        raise SystemExit("Disk confirmation did not match. Aborting.")
    # Immediate, hard-to-miss nap banner — do not wait for execute() to start.
    ui.install_wait_notice()


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
