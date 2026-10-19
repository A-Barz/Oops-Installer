"""Installation configuration collected from the colourful handbook wizard.

InstallConfig is the single source of truth passed from wizard.py to execute.py.
When adding a new user-facing option:

  1. Add a field here (with a safe default).
  2. Ask for it in wizard.py (matching handbook chapter if possible).
  3. Consume it in execute.py / render_chroot_script().

Save/load supports YAML (.yaml/.yml) and JSON (.json). Format is chosen from
the file suffix. Unknown keys are ignored so older files still load.

Secrets (passwords, LUKS, Wi-Fi PSK) are omitted unless include_secrets=True.
On the next run, missing secrets are prompted before execute().
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Fields that must never appear in a default saved profile.
SECRET_FIELDS = (
    "luks_passphrase",
    "root_password",
    "user_password",
    "wifi_psk",
    "grub_password",
)


@dataclass
class InstallConfig:
    # --- Handbook ch. 2: live host (Ubuntu XFCE instead of the Gentoo ISO) ---
    architecture: str = "amd64"
    dry_run: bool = False  # Print commands only; never wipe disks.
    resume: bool = False  # Skip phases already listed in state_file.
    state_file: str = "/var/tmp/gentoo-live-installer-state.json"
    mountpoint: str = "/mnt/gentoo"  # Handbook default bind-mount root.
    skip_host_packages: bool = False  # If True, assume gdisk/cryptsetup already exist.

    # --- Handbook ch. 3: network on the *live* session (needed to fetch stage3) ---
    network_mode: str = "dhcp"  # dhcp, static, wifi, skip
    wifi_iface: str = ""
    wifi_ssid: str = ""
    wifi_psk: str = ""
    static_iface: str = ""  # Live NIC for static IP (never assume eth0).
    static_ip: str = ""
    static_gateway: str = ""
    static_dns: str = "1.1.1.1"
    ntp: bool = True  # Stage3 GPG checks fail if the live clock is far off.

    # --- Handbook ch. 4: disks ---
    target_disk: str = ""  # e.g. /dev/sda or /dev/nvme0n1
    extra_raid_disk: str = ""  # Second member when layout == mdraid1
    partition_table: str = "gpt"  # gpt (UEFI) or mbr (legacy BIOS)
    firmware: str = "uefi"  # uefi | bios — drives ESP vs BIOS-boot partition
    wipe_disk: bool = True
    # handbook = ESP/boot + swap + root (wiki default scheme)
    layout: str = "handbook"  # handbook, btrfs-subvols, lvm, lvm-luks, zfs, existing, mdraid1
    filesystem: str = "ext4"  # ext4, xfs, btrfs, f2fs, jfs, zfs
    esp_size_mib: int = 1024
    boot_size_mib: int = 1024
    swap_mode: str = "partition"  # partition, file, zram, both, none
    swap_size_gib: int = 4
    luks: bool = False
    luks_passphrase: str = ""  # Never log this; only passed into cryptsetup / secrets file.
    tpm2_unlock: bool = False  # Writes a helper script; enrollment happens after first boot.
    encrypted_swap: bool = False
    # Used when layout == existing (user already partitioned).
    existing_esp: str = ""
    existing_boot: str = ""
    existing_root: str = ""
    existing_swap: str = ""

    # --- Handbook ch. 5: stage tarball flavour ---
    init_system: str = "openrc"  # openrc | systemd — selects latest-stage3-*-{init}.txt
    install_type: str = "desktop"  # desktop stage3 includes LLVM/Rust-bin
    stage_level: str = "stage3"  # stage1, stage2, or stage3 (handbook default)
    stage_variant: str = "standard"  # standard, hardened, musl
    verify_stage: bool = True  # SHA256 + GPG (Release Engineering keys)
    testing_keywords: bool = False  # Writes */* ~amd64 if True

    # --- Handbook ch. 6: Portage / make.conf ---
    profile: str = "default/linux/amd64/23.0/desktop"
    sync_method: str = "webrsync"  # webrsync, rsync, git
    binhost: bool = True  # Gentoo official binpackages (--getbinpkg, not FEATURES)
    binhost_abi: str = "x86-64"  # x86-64 or x86-64-v3
    # False avoids rebuilding glibc from source (which compiles ~500 locales).
    binpkg_respect_use: bool = False
    use_flags: str = "dist-kernel"
    extra_use: list[str] = field(default_factory=list)
    accept_license: str = "*"
    video_cards: str = "auto"  # auto = lspci heuristic in detect.detect_gpu()
    lto: bool = False
    hardened_flags: bool = False
    makeopts_jobs: int = 0  # 0 means os.cpu_count()
    gentoo_mirrors: str = ""  # Empty → distfiles.gentoo.org

    # --- Handbook ch. 7: kernel ---
    kernel: str = "gentoo-kernel-bin"  # dist-kernel binary is fastest on a live USB; alternatives: gentoo-kernel, gentoo-sources
    linux_firmware: bool = True
    sof_firmware: bool = False
    intel_microcode: bool = False
    initramfs: str = "dracut"  # Required for LUKS/LVM; "none" is expert-only
    uki: bool = False  # Unified Kernel Image (pairs with systemd-boot / EFI stub)

    # --- Handbook ch. 8: system identity ---
    hostname: str = "gentoo"
    timezone: str = "UTC"
    locale: str = "en_US.UTF-8"
    extra_locales: list[str] = field(default_factory=list)  # Extra glibc locales in locale.gen
    keymap: str = "us"
    root_password: str = ""
    username: str = "gentoo"
    user_password: str = ""
    user_groups: str = "wheel,audio,video,usb,users"

    # --- Handbook ch. 9: system tools on the *installed* Gentoo ---
    network_stack: str = "networkmanager"  # networkmanager, dhcpcd, netifrc, systemd-networkd
    netifrc_iface: str = ""  # OpenRC netifrc interface; empty → live iface or first NIC.
    wireless: bool = True
    sshd: bool = True
    cups: bool = False
    syslog: str = "sysklogd"  # Ignored on systemd (journald)
    cron: str = "cronie"  # Ignored on systemd (timers)
    chrony: bool = True
    zram: bool = True

    # --- Handbook ch. 10: bootloader + splash ---
    # bootloader = primary (installkernel USE). bootloaders = every loader to install.
    bootloader: str = "grub"
    bootloaders: list[str] = field(default_factory=lambda: ["grub"])
    plymouth: bool = False
    plymouth_theme: str = "spinner"  # Default theme passed to plymouth-set-default-theme
    plymouth_themes: list[str] = field(default_factory=list)  # Extra themes to emerge
    plymouth_openrc_plugin: bool = False
    secure_boot: bool = False  # Prepares installkernel signing USE; MOK is post-install
    grub_password: str = ""
    selinux: bool = False  # Hardened variant only; boots permissive
    selinux_policy: str = "targeted"

    # --- Handbook ch. 11–12: DE, WMs, overlays, extra emerge atoms ---
    desktop: str = "none"  # none, xfce, plasma, gnome, mate, cinnamon, i3
    display_manager: str = "auto"
    pipewire: bool = True
    browser: str = "firefox-bin"
    extra_packages: list[str] = field(default_factory=list)  # Final emerge set (atoms)
    selected_packages: list[str] = field(default_factory=list)  # Catalogue picks only
    window_managers: list[str] = field(default_factory=list)  # gui-wm/* and x11-wm/*
    overlays: list[str] = field(default_factory=list)  # eselect repository names
    kde_games: bool = False
    flatpak: bool = False

    def public_dict(self) -> dict[str, Any]:
        """Serializable view with credentials removed (empty string, not stored as ***)."""
        data = asdict(self)
        for secret in SECRET_FIELDS:
            if secret in data:
                data[secret] = ""
        return data

    def save(self, path: str | Path, include_secrets: bool = False) -> None:
        """Write answers for reuse. .yaml/.yml → YAML; anything else → JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self) if include_secrets else self.public_dict()
        if path.suffix.lower() in {".yaml", ".yml"}:
            path.write_text(_to_yaml(data), encoding="utf-8")
        else:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "InstallConfig":
        """Load YAML or JSON. `***` from older JSON files is treated as empty."""
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = _from_yaml(text)
        else:
            data = json.loads(text)
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        int_fields = {"esp_size_mib", "boot_size_mib", "swap_size_gib", "makeopts_jobs"}
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            if k not in known:
                continue
            if k in SECRET_FIELDS and v in {"***", None}:
                v = ""
            if k in int_fields and v not in {None, ""}:
                v = int(v)
            cleaned[k] = v
        return cls(**cleaned)


def _yaml_quote(value: str) -> str:
    """Quote strings that would confuse a minimal YAML reader."""
    if value == "" or value in {"true", "false", "null", "[]"}:
        return json.dumps(value)
    if value[:1] in "-:" or any(c in value for c in ":#{}[]&*?|!<>=%@`'\n\t"):
        return json.dumps(value)
    return value


def _to_yaml(data: dict[str, Any]) -> str:
    """Write a flat YAML profile (scalars + string lists). No PyYAML required."""
    lines = [
        "# Gentoo live installer profile",
        "# Reuse with: sudo python3 install.py --config this-file.yaml",
        "# Passwords, LUKS, and Wi-Fi PSK are omitted unless saved with --include-secrets.",
        "",
    ]
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, int) and not isinstance(value, bool):
            lines.append(f"{key}: {value}")
        elif isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {_yaml_quote(str(item))}")
        elif value is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {_yaml_quote(str(value))}")
    return "\n".join(lines) + "\n"


def _from_yaml(text: str) -> dict[str, Any]:
    """Parse the YAML subset we emit. Uses PyYAML if installed, else a small reader."""
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("YAML profile must be a mapping of keys")
        return data
    except ImportError:
        pass
    return _from_yaml_simple(text)


def _from_yaml_simple(text: str) -> dict[str, Any]:
    """Enough YAML for this installer: key: value, key: [], and indented '- item' lists."""
    data: dict[str, Any] = {}
    current_list: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            if not current_list:
                raise ValueError(f"YAML list item with no key: {raw}")
            data[current_list].append(_parse_yaml_scalar(line[4:].strip()))
            continue
        current_list = None
        if ":" not in line:
            raise ValueError(f"YAML line needs a key: {raw}")
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest == "" or rest == "|":
            data[key] = []
            current_list = key
        elif rest == "[]":
            data[key] = []
        else:
            data[key] = _parse_yaml_scalar(rest)
    return data


def _parse_yaml_scalar(token: str) -> Any:
    if token in {"true", "True", "yes"}:
        return True
    if token in {"false", "False", "no"}:
        return False
    if token in {"null", "~", ""}:
        return ""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        inner = token[1:-1]
        return inner.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'")
    try:
        return int(token)
    except ValueError:
        return token


def part_name(disk: str, number: int) -> str:
    """Map disk + partition index to a kernel name.

    NVMe / MMC / loop devices use pN (/dev/nvme0n1p1).
    SCSI/SATA/VirtIO use N (/dev/sda1).
    """
    disk = disk.rstrip("/")
    if disk[-1].isdigit() or "nvme" in disk or "mmcblk" in disk or "loop" in disk:
        return f"{disk}p{number}"
    return f"{disk}{number}"
