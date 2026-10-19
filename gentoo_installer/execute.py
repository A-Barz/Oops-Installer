"""Execute the handbook installation from an Ubuntu XFCE live host.

Two environments:

  Host (this Python process)
    apt install tools, partition/format/mount, wget+verify stage3, bind-mounts,
    write /mnt/gentoo/root/chroot-install.sh, then `chroot … bash` that script.

  Chroot (generated bash)
    Portage sync, overlays, profile, make.conf, emerge kernel + selected
    packages, fstab, users, bootloaders, Plymouth. That is where hours of
    compile time happen.

Phase names in execute() match runner.State so --resume can skip finished work.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import textwrap
from pathlib import Path

from . import catalog, detect, ui
from .config import InstallConfig, part_name
from .rev import INSTALLER_REV
from .runner import Runner, State

# Same integer as the startup banner. Bump INSTALLER_REV in rev.py on each change.
CHROOT_SCRIPT_REV = INSTALLER_REV


def selected_bootloaders(cfg: InstallConfig) -> list[str]:
    """All loaders to install; always includes primary `cfg.bootloader`."""
    loaders = list(cfg.bootloaders or [])
    if cfg.bootloader and cfg.bootloader not in loaders:
        loaders.insert(0, cfg.bootloader)
    if not loaders:
        loaders = ["grub"]
    return loaders


# Ubuntu packages needed so the live session can partition, encrypt, and fetch stage3.
HOST_PACKAGES = [
    "wget",
    "curl",
    "gnupg",
    "xz-utils",
    "tar",
    "rsync",
    "gdisk",
    "parted",
    "dosfstools",
    "e2fsprogs",
    "xfsprogs",
    "btrfs-progs",
    "f2fs-tools",
    "jfsutils",
    "cryptsetup",
    "lvm2",
    "mdadm",
    "efibootmgr",
    "pciutils",
    "iw",
    "wpasupplicant",
    "ntpdate",
]


def execute(cfg: InstallConfig) -> None:
    """Run host phases in handbook order. Requires root on Linux unless dry-run."""
    if not cfg.dry_run:
        if not detect.is_linux():
            raise SystemExit("Execution requires a Linux live environment (Ubuntu XFCE live).")
        if not detect.is_root():
            raise SystemExit("Re-run as root: sudo python3 install.py")

    # Live USBs (and a previous failed chroot) can export stale FEATURES/USE into
    # this process. Portage then prints "Run . /etc/profile". Drop them here so
    # --config/--resume does not require sourcing the profile by hand.
    os.environ.pop("FEATURES", None)
    os.environ.pop("PORTAGE_FEATURES", None)
    os.environ.pop("USE", None)

    runner = Runner(dry_run=cfg.dry_run)
    state = State(cfg.state_file)
    root = cfg.mountpoint
    profiles = [Path("gentoo-install.yaml")]
    if os.name != "nt":
        profiles.append(Path("/var/tmp/gentoo-install.yaml"))
    for profile in profiles:
        try:
            cfg.save(profile)
            ui.ok(f"Wrote {profile}")
        except OSError as exc:
            ui.warn(f"Could not write {profile}: {exc}")

    phases = [
        ("host_packages", lambda: phase_host(cfg, runner)),
        ("network", lambda: phase_network(cfg, runner)),
        ("disks", lambda: phase_disks(cfg, runner)),
        ("stage3", lambda: phase_stage3(cfg, runner, root)),
        ("chroot_mounts", lambda: phase_chroot_mounts(cfg, runner, root)),
        ("chroot_install", lambda: phase_chroot(cfg, runner, root)),
        ("done", lambda: phase_done(cfg, runner, root)),
    ]
    # A recorded chroot_install used to be skipped, so resume died on the same
    # missing packages. These phases are safe to repeat: apt/emerge skip what
    # is already installed, and nothing here formats a disk.
    rerun_on_resume = {"host_packages", "network", "chroot_install"}
    if cfg.resume:
        cfg.wipe_disk = False
        ui.info(
            "Resume: package installs will run again. "
            "Existing files stay. Disks are not formatted or repartitioned."
        )
    ui.info(f"Oops Installer rev {INSTALLER_REV}")
    for name, fn in phases:
        ui.chapter("→", f"Phase: {name}", "https://wiki.gentoo.org/wiki/Handbook:AMD64")
        if cfg.resume and name in rerun_on_resume:
            ui.info(f"Resume: running {name} again.")
            fn()
            state.mark(name)
            continue
        if state.skip(name, cfg.resume):
            # Skip means "already done", not "leave the target unmounted".
            # --config/--resume after a crash often still has /mnt/gentoo mounted;
            # if the machine rebooted, reopen LUKS and remount without mkfs.
            if name == "disks":
                _ensure_target_mounted(cfg, runner)
            elif name == "chroot_mounts":
                phase_chroot_mounts(cfg, runner, root)
            continue
        fn()
        state.mark(name)


def phase_host(cfg: InstallConfig, runner: Runner) -> None:
    """Install gdisk, cryptsetup, wget, etc. on the Ubuntu live system."""
    if cfg.skip_host_packages:
        ui.info("Skipping Ubuntu host package installation.")
        return
    pkgs = list(HOST_PACKAGES)
    if cfg.filesystem == "zfs" or cfg.layout == "zfs":
        pkgs.append("zfsutils-linux")
    runner.shell("apt-get update")
    required = "wget curl gnupg xz-utils tar rsync gdisk parted dosfstools e2fsprogs cryptsetup lvm2 pciutils"
    runner.shell("DEBIAN_FRONTEND=noninteractive apt-get install -y " + required)
    runner.shell(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y " + " ".join(pkgs) + " || true"
    )
    ui.ok("Host tools ready")


def phase_network(cfg: InstallConfig, runner: Runner) -> None:
    """Bring up live Wi-Fi/static IP and NTP so distfiles.gentoo.org works."""
    if cfg.network_mode == "wifi":
        conf = textwrap.dedent(
            f"""
            ctrl_interface=/run/wpa_supplicant
            network={{
                ssid="{cfg.wifi_ssid}"
                psk="{cfg.wifi_psk}"
            }}
            """
        ).strip()
        path = Path("/tmp/wpa-gentoo.conf")
        if not cfg.dry_run:
            path.write_text(conf + "\n", encoding="utf-8")
            path.chmod(0o600)
        runner.shell(
            f"wpa_supplicant -B -i {shlex.quote(cfg.wifi_iface)} -c /tmp/wpa-gentoo.conf || true"
        )
        runner.shell(f"dhclient {shlex.quote(cfg.wifi_iface)} || dhcpcd {shlex.quote(cfg.wifi_iface)} || true")
    elif cfg.network_mode == "static":
        iface = cfg.static_iface or detect.default_nic(prefer_wireless=False)
        ui.info(f"Configuring static IPv4 on {iface}")
        runner.shell(f"ip addr add {shlex.quote(cfg.static_ip)} dev {shlex.quote(iface)} || true")
        runner.shell(f"ip link set {shlex.quote(iface)} up")
        if cfg.static_gateway:
            runner.shell(f"ip route add default via {shlex.quote(cfg.static_gateway)} || true")
        if not cfg.dry_run:
            Path("/etc/resolv.conf").write_text(f"nameserver {cfg.static_dns}\n", encoding="utf-8")
    if cfg.ntp:
        runner.shell("ntpdate -u pool.ntp.org || timedatectl set-ntp true || true")
    if not cfg.dry_run and not detect.has_network():
        ui.warn("Network still looks down. Stage3 download may fail.")


def _luks_format_open(cfg: InstallConfig, runner: Runner, device: str, name: str) -> None:
    """LUKS2-format a partition/LV and open it as /dev/mapper/<name>."""
    runner.shell(
        f"printf '%s' {shlex.quote(cfg.luks_passphrase)} | cryptsetup luksFormat --type luks2 --batch-mode {shlex.quote(device)}"
    )
    runner.shell(
        f"printf '%s' {shlex.quote(cfg.luks_passphrase)} | cryptsetup open {shlex.quote(device)} {shlex.quote(name)}"
    )


def _netifrc_iface(cfg: InstallConfig) -> str:
    raw = cfg.netifrc_iface or cfg.static_iface or cfg.wifi_iface or detect.default_nic()
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", raw.strip())
    return match.group(0) if match else "eth0"


def _mountpoint_q(path: str) -> bool:
    return detect.is_mounted(path)


def _mount_if_needed(runner: Runner, source: str, dest: str, options: str = "") -> None:
    """Mount dest if it is not already a mountpoint (resume-safe)."""
    runner.shell(f"mkdir -p {shlex.quote(dest)}")
    opt = f"-o {options} " if options else ""
    runner.shell(
        f"mountpoint -q {shlex.quote(dest)} || mount {opt}{shlex.quote(source)} {shlex.quote(dest)}"
    )


def _unmount_target(cfg: InstallConfig, runner: Runner) -> None:
    """Drop mounts/swap/LUKS so wipefs can run. Safe if nothing is mounted."""
    root = cfg.mountpoint
    ui.info(f"Unmounting {root} if it is busy from a previous run")
    runner.shell(
        f"umount -R {shlex.quote(root)} || umount -Rl {shlex.quote(root)} || true"
    )
    runner.shell("swapoff -a || true")
    runner.shell("cryptsetup close swap || true")
    runner.shell("cryptsetup close root || true")
    if cfg.layout in {"lvm", "lvm-luks"}:
        runner.shell("vgchange -an vg0 || true")


def _luks_open_only(cfg: InstallConfig, runner: Runner, device: str, name: str) -> None:
    mapper = f"/dev/mapper/{name}"
    if Path(mapper).exists():
        return
    if not cfg.luks_passphrase:
        raise SystemExit(f"LUKS passphrase missing; cannot open {device} as {name}.")
    runner.shell(
        f"printf '%s' {shlex.quote(cfg.luks_passphrase)} | cryptsetup open "
        f"{shlex.quote(device)} {shlex.quote(name)}"
    )


def _runtime_devices(cfg: InstallConfig) -> dict[str, str]:
    """Partition map plus live mapper/LV names (no mkfs)."""
    devices = _map_devices(cfg)
    if cfg.luks and devices.get("luks_source"):
        devices["root"] = "/dev/mapper/root"
    if cfg.layout in {"lvm", "lvm-luks"}:
        if cfg.swap_mode in {"partition", "both"}:
            if cfg.layout == "lvm-luks":
                devices["swap"] = "/dev/vg0/swap"
            elif cfg.encrypted_swap and cfg.luks:
                devices["luks_swap_source"] = "/dev/vg0/swap"
                devices["swap"] = "/dev/mapper/swap"
            else:
                devices["swap"] = "/dev/vg0/swap"
        devices["root"] = "/dev/vg0/root"
    elif cfg.luks and cfg.encrypted_swap and cfg.swap_mode in {"partition", "both"}:
        devices["luks_swap_source"] = devices.get("swap", "")
        devices["swap"] = "/dev/mapper/swap"
    return devices


def _ensure_target_mounted(cfg: InstallConfig, runner: Runner) -> None:
    """Reopen LUKS/LVM and mount /mnt/gentoo without partitioning or mkfs."""
    if cfg.dry_run:
        ui.info("Would remount the existing target without wiping.")
        cfg._devices = _runtime_devices(cfg)  # type: ignore[attr-defined]
        return
    devices = _runtime_devices(cfg)
    if cfg.luks and devices.get("luks_source"):
        _luks_open_only(cfg, runner, devices["luks_source"], "root")
    if cfg.layout in {"lvm", "lvm-luks"}:
        runner.shell("vgchange -ay vg0 || true")
    if cfg.encrypted_swap and devices.get("luks_swap_source"):
        _luks_open_only(cfg, runner, devices["luks_swap_source"], "swap")
        runner.shell(f"swapon {shlex.quote(devices['swap'])} || true")
    if cfg.layout == "zfs":
        runner.shell(f"zpool import -R {shlex.quote(cfg.mountpoint)} rpool || true")
    elif not _mountpoint_q(cfg.mountpoint):
        _mount_tree(cfg, runner, devices, create_subvols=False)
    else:
        ui.info(f"{cfg.mountpoint} is already mounted; leaving it in place.")
        if cfg.firmware == "uefi" and devices.get("esp"):
            boot = cfg.mountpoint + "/boot"
            if not _mountpoint_q(boot):
                _mount_if_needed(runner, devices["esp"], boot)
    cfg._devices = devices  # type: ignore[attr-defined]
    ui.ok("Target filesystems are mounted")


def phase_disks(cfg: InstallConfig, runner: Runner) -> None:
    """Wipe/partition/LUKS/LVM, mkfs, and mount at cfg.mountpoint (handbook /mnt/gentoo)."""
    disk = cfg.target_disk
    if cfg.resume:
        ui.info("Resume: not wiping or formatting; ensuring the target is mounted.")
        _ensure_target_mounted(cfg, runner)
        return
    if cfg.wipe_disk and cfg.layout != "existing":
        _unmount_target(cfg, runner)
        ui.danger(f"Zapping partition tables on {disk}")
        runner.shell(f"wipefs -af {shlex.quote(disk)} || true")
        if cfg.layout == "mdraid1":
            runner.shell(f"wipefs -af {shlex.quote(cfg.extra_raid_disk)} || true")

        if cfg.layout == "zfs":
            _layout_zfs(cfg, runner)
            cfg._devices = {  # type: ignore[attr-defined]
                "esp": part_name(disk, 1) if cfg.firmware == "uefi" else "",
                "boot": part_name(disk, 1) if cfg.firmware == "uefi" else "",
                "swap": "",
                "root": "rpool/ROOT/gentoo",
                "luks_source": "",
            }
            return
        if cfg.layout == "mdraid1":
            _layout_mdraid(cfg, runner)
        elif cfg.partition_table == "gpt":
            _sgdisk_gpt(cfg, runner, disk)
        else:
            _parted_mbr(cfg, runner, disk)
        runner.shell("partprobe || true")
        runner.shell("sleep 2 || true")

    devices = _map_devices(cfg)
    # Encrypt the raw root partition, then treat /dev/mapper/root as the FS/LVM PV.
    if cfg.luks:
        _luks_format_open(cfg, runner, devices["luks_source"], "root")
        devices["root"] = "/dev/mapper/root"

    if cfg.layout in {"lvm", "lvm-luks"}:
        pv = devices["root"]
        runner.shell(f"pvcreate -ff -y {shlex.quote(pv)}")
        runner.shell(f"vgcreate vg0 {shlex.quote(pv)}")
        if cfg.swap_mode in {"partition", "both"}:
            runner.shell(f"lvcreate -y -L {cfg.swap_size_gib}G -n swap vg0")
            if cfg.layout == "lvm-luks":
                # Outer LUKS already wraps the VG; do not LUKS the LV again.
                devices["swap"] = "/dev/vg0/swap"
            elif cfg.encrypted_swap and cfg.luks:
                devices["luks_swap_source"] = "/dev/vg0/swap"
                _luks_format_open(cfg, runner, "/dev/vg0/swap", "swap")
                devices["swap"] = "/dev/mapper/swap"
            else:
                devices["swap"] = "/dev/vg0/swap"
        runner.shell("lvcreate -y -l 100%FREE -n root vg0")
        devices["root"] = "/dev/vg0/root"
    elif (
        cfg.luks
        and cfg.encrypted_swap
        and devices.get("swap")
        and cfg.swap_mode in {"partition", "both"}
    ):
        # Handbook / btrfs / RAID: dedicated swap partition → /dev/mapper/swap.
        devices["luks_swap_source"] = devices["swap"]
        _luks_format_open(cfg, runner, devices["swap"], "swap")
        devices["swap"] = "/dev/mapper/swap"

    _mkfs(cfg, runner, devices)
    _mount_tree(cfg, runner, devices)
    cfg._devices = devices  # type: ignore[attr-defined]


def _wants_partition_swap(cfg: InstallConfig) -> bool:
    """True when swap should be a real partition (not an LVM LV or ZFS dataset)."""
    return cfg.swap_mode in {"partition", "both"} and cfg.layout not in {
        "lvm",
        "lvm-luks",
        "zfs",
        "existing",
    }


def _gpt_root_index(cfg: InstallConfig) -> int:
    """1-based GPT partition number of the Linux root (or RAID member) partition."""
    n = 1
    if cfg.firmware == "uefi":
        n += 1  # ESP
    else:
        n += 2  # BIOS-boot + /boot
    if _wants_partition_swap(cfg):
        n += 1
    return n


def _sgdisk_gpt(cfg: InstallConfig, runner: Runner, disk: str) -> None:
    """GPT: ESP or BIOS-boot, optional swap, then Linux root (8300 / 8309 if LUKS)."""
    runner.shell(f"sgdisk --zap-all {shlex.quote(disk)}")
    n = 1
    if cfg.firmware == "uefi":
        runner.shell(f"sgdisk -n {n}:0:+{cfg.esp_size_mib}M -t {n}:EF00 -c {n}:EFI {shlex.quote(disk)}")
        n += 1
    else:
        runner.shell(f"sgdisk -n {n}:0:+2M -t {n}:EF02 -c {n}:BIOSBOOT {shlex.quote(disk)}")
        n += 1
        runner.shell(f"sgdisk -n {n}:0:+{cfg.boot_size_mib}M -t {n}:8300 -c {n}:BOOT {shlex.quote(disk)}")
        n += 1
    if _wants_partition_swap(cfg):
        runner.shell(f"sgdisk -n {n}:0:+{cfg.swap_size_gib}G -t {n}:8200 -c {n}:SWAP {shlex.quote(disk)}")
        n += 1
    ptype = "8309" if cfg.luks else "8300"
    runner.shell(f"sgdisk -n {n}:0:0 -t {n}:{ptype} -c {n}:ROOT {shlex.quote(disk)}")


def _parted_mbr(cfg: InstallConfig, runner: Runner, disk: str) -> None:
    """Handbook BIOS/MBR: boot + optional swap + root."""
    runner.shell(f"parted -s {shlex.quote(disk)} mklabel msdos")
    runner.shell(
        f"parted -s {shlex.quote(disk)} mkpart primary ext4 1MiB {cfg.boot_size_mib + 1}MiB"
    )
    runner.shell(f"parted -s {shlex.quote(disk)} set 1 boot on")
    start = cfg.boot_size_mib + 1
    n = 2
    if _wants_partition_swap(cfg):
        end = start + cfg.swap_size_gib * 1024
        runner.shell(f"parted -s {shlex.quote(disk)} mkpart primary linux-swap {start}MiB {end}MiB")
        start = end
        n = 3
    runner.shell(f"parted -s {shlex.quote(disk)} mkpart primary ext4 {start}MiB 100%")
    _ = n


def _layout_mdraid(cfg: InstallConfig, runner: Runner) -> None:
    """Mirror two disks' root partitions as /dev/md/root, then continue mkfs on that MD."""
    for d in (cfg.target_disk, cfg.extra_raid_disk):
        _sgdisk_gpt(cfg, runner, d)
    idx = _gpt_root_index(cfg)
    r1 = part_name(cfg.target_disk, idx)
    r2 = part_name(cfg.extra_raid_disk, idx)
    runner.shell(f"mdadm --create /dev/md/root --level=1 --raid-devices=2 {shlex.quote(r1)} {shlex.quote(r2)} --run")
    cfg._md_root = "/dev/md/root"  # type: ignore[attr-defined]


def _layout_zfs(cfg: InstallConfig, runner: Runner) -> None:
    """ESP (UEFI) + ZFS rpool with ROOT/gentoo altroot at the Gentoo mountpoint."""
    disk = cfg.target_disk
    runner.shell(f"sgdisk --zap-all {shlex.quote(disk)}")
    if cfg.firmware == "uefi":
        runner.shell(f"sgdisk -n 1:0:+{cfg.esp_size_mib}M -t 1:EF00 {shlex.quote(disk)}")
        runner.shell(f"sgdisk -n 2:0:0 -t 2:BF01 {shlex.quote(disk)}")
        zpart = part_name(disk, 2)
        esp = part_name(disk, 1)
        runner.shell(f"mkfs.vfat -F32 {shlex.quote(esp)}")
    else:
        runner.shell(f"sgdisk -n 1:0:+2M -t 1:EF02 {shlex.quote(disk)}")
        runner.shell(f"sgdisk -n 2:0:0 -t 2:BF01 {shlex.quote(disk)}")
        zpart = part_name(disk, 2)
    runner.shell(
        f"zpool create -f -o ashift=12 -O compression=zstd -O atime=off -O mountpoint=none rpool {shlex.quote(zpart)}"
    )
    runner.shell("zfs create -o mountpoint=none rpool/ROOT")
    runner.shell("zfs create -o mountpoint=/ rpool/ROOT/gentoo")
    runner.shell("zfs create -o mountpoint=/home rpool/home")
    runner.shell("zpool set bootfs=rpool/ROOT/gentoo rpool")
    runner.shell("zfs umount -a || true")
    runner.shell("zpool export rpool || true")
    runner.shell(f"zpool import -R {shlex.quote(cfg.mountpoint)} rpool")
    if cfg.firmware == "uefi":
        _mount_if_needed(runner, part_name(disk, 1), cfg.mountpoint + "/boot")


def _map_devices(cfg: InstallConfig) -> dict[str, str]:
    """Partition paths for esp/boot/swap/root after sgdisk/parted ran.

    Keys:
      esp, boot, swap, root — block devices to format/mount
      luks_source — partition that cryptsetup opens (becomes /dev/mapper/root)
    """
    if cfg.layout == "existing":
        return {
            "esp": cfg.existing_esp,
            "boot": cfg.existing_boot or cfg.existing_esp,
            "swap": cfg.existing_swap,
            "root": cfg.existing_root,
            "luks_source": cfg.existing_root,
        }
    disk = cfg.target_disk
    if cfg.firmware == "uefi" and cfg.partition_table == "gpt":
        esp = part_name(disk, 1)
        idx = 2
        swap = ""
        if _wants_partition_swap(cfg):
            swap = part_name(disk, idx)
            idx += 1
        root = part_name(disk, idx)
        if getattr(cfg, "_md_root", None):
            root = cfg._md_root  # type: ignore[attr-defined]
        return {"esp": esp, "boot": esp, "swap": swap, "root": root, "luks_source": root}
    # BIOS GPT: 1 bios_grub, 2 boot, 3 swap?, 4 root
    if cfg.partition_table == "gpt":
        boot = part_name(disk, 2)
        idx = 3
        swap = ""
        if _wants_partition_swap(cfg):
            swap = part_name(disk, idx)
            idx += 1
        root = part_name(disk, idx)
        return {"esp": "", "boot": boot, "swap": swap, "root": root, "luks_source": root}
    # MBR
    boot = part_name(disk, 1)
    if _wants_partition_swap(cfg):
        return {
            "esp": "",
            "boot": boot,
            "swap": part_name(disk, 2),
            "root": part_name(disk, 3),
            "luks_source": part_name(disk, 3),
        }
    return {"esp": "", "boot": boot, "swap": "", "root": part_name(disk, 2), "luks_source": part_name(disk, 2)}


def _mkfs(cfg: InstallConfig, runner: Runner, devices: dict[str, str]) -> None:
    """Format ESP as FAT32, swap with mkswap, root with the chosen filesystem."""
    if devices.get("esp") and cfg.firmware == "uefi":
        runner.shell(f"mkfs.vfat -F32 {shlex.quote(devices['esp'])}")
    if devices.get("boot") and devices["boot"] != devices.get("esp"):
        runner.shell(f"mkfs.ext4 -F {shlex.quote(devices['boot'])}")
    if devices.get("swap") and cfg.swap_mode in {"partition", "both"}:
        runner.shell(f"mkswap {shlex.quote(devices['swap'])}")
        runner.shell(f"swapon {shlex.quote(devices['swap'])} || true")
    root = devices["root"]
    fs = cfg.filesystem
    if fs == "ext4":
        runner.shell(f"mkfs.ext4 -F {shlex.quote(root)}")
    elif fs == "xfs":
        runner.shell(f"mkfs.xfs -f {shlex.quote(root)}")
    elif fs == "btrfs":
        runner.shell(f"mkfs.btrfs -f {shlex.quote(root)}")
    elif fs == "f2fs":
        runner.shell(f"mkfs.f2fs -f {shlex.quote(root)}")
    elif fs == "jfs":
        runner.shell(f"mkfs.jfs -f {shlex.quote(root)}")


def _mount_tree(
    cfg: InstallConfig,
    runner: Runner,
    devices: dict[str, str],
    create_subvols: bool = True,
) -> None:
    """Mount root (and Btrfs subvols / ESP) under cfg.mountpoint for stage3 extract."""
    root = cfg.mountpoint
    runner.shell(f"mkdir -p {shlex.quote(root)}")
    if cfg.layout == "zfs":
        return
    if cfg.layout == "btrfs-subvols" or cfg.filesystem == "btrfs" and cfg.layout == "btrfs-subvols":
        if create_subvols and not _mountpoint_q(root):
            _mount_if_needed(runner, devices["root"], root)
            for sub in ("@", "@home", "@log", "@cache", "@tmp", "@swap"):
                runner.shell(
                    f"btrfs subvolume list {shlex.quote(root)} | grep -q 'path {sub}$' "
                    f"|| btrfs subvolume create {shlex.quote(root + '/' + sub)}"
                )
            runner.shell(f"umount {shlex.quote(root)} || true")
        opts = "noatime,compress=zstd:3,ssd"
        _mount_if_needed(runner, devices["root"], root, f"{opts},subvol=@")
        for sub, mp in (("@home", "home"), ("@log", "var/log"), ("@cache", "var/cache"), ("@tmp", "tmp"), ("@swap", "swap")):
            extra = "noatime,nodatacow" if sub == "@swap" else opts
            _mount_if_needed(runner, devices["root"], root + "/" + mp, f"{extra},subvol={sub}")
    else:
        _mount_if_needed(runner, devices["root"], root)
    if cfg.firmware == "uefi" and devices.get("esp"):
        runner.shell(f"mkdir -p {shlex.quote(root + '/efi')} {shlex.quote(root + '/boot')}")
        _mount_if_needed(runner, devices["esp"], root + "/boot")
    elif devices.get("boot") and devices["boot"] != devices.get("esp") and devices["boot"] != devices.get("root"):
        _mount_if_needed(runner, devices["boot"], root + "/boot")


def stage_flavor(cfg: InstallConfig) -> str:
    """Name used in latest-<flavor>.txt on distfiles.gentoo.org."""
    arch = cfg.architecture
    init = cfg.init_system
    level = getattr(cfg, "stage_level", "stage3") or "stage3"
    if level not in {"stage1", "stage2", "stage3"}:
        level = "stage3"
    # Official desktop/LLVM autobuilds are stage3-only.
    if level != "stage3":
        if cfg.stage_variant == "musl":
            return f"{level}-{arch}-musl-{init}" if init == "openrc" else f"{level}-{arch}-musl"
        if cfg.stage_variant == "hardened":
            return f"{level}-{arch}-hardened-{init}"
        return f"{level}-{arch}-{init}"
    if cfg.stage_variant == "musl":
        return f"stage3-{arch}-musl-{init}" if init == "openrc" else f"stage3-{arch}-musl"
    if cfg.stage_variant == "hardened":
        return f"stage3-{arch}-hardened-{init}"
    if cfg.install_type == "desktop":
        return f"stage3-{arch}-desktop-{init}"
    return f"stage3-{arch}-{init}"


def stage3_flavor(cfg: InstallConfig) -> str:
    """Back-compat alias for stage_flavor()."""
    return stage_flavor(cfg)


def phase_stage3(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Download the current stage3, optionally verify SHA256/GPG, extract with xattrs."""
    if cfg.resume and (Path(root) / "etc" / "portage").is_dir():
        ui.info("Resume: stage tarball already extracted; skipping download.")
        return
    flavor = stage_flavor(cfg)
    latest = f"https://distfiles.gentoo.org/releases/{cfg.architecture}/autobuilds/latest-{flavor}.txt"
    ui.info(f"Resolving latest {flavor}")
    pointer = f"{root}/latest-stage.txt"
    runner.shell(f"mkdir -p {shlex.quote(root)}")
    runner.shell(f"wget -O {shlex.quote(pointer)} {shlex.quote(latest)}")
    if cfg.dry_run:
        tarball = f"{flavor}-DATE.tar.xz"
    else:
        text = Path(pointer).read_text(encoding="utf-8", errors="ignore")
        rel = ""
        for line in text.splitlines():
            if line.startswith("#") or line.startswith("-----"):
                continue
            if ".tar." in line and "stage" in line:
                rel = line.split()[0]
                break
        if not rel:
            raise SystemExit(
                f"Could not parse stage pointer from {latest}. "
                f"Official autobuilds may not publish {flavor}; try stage3."
            )
        tarball = rel.split("/")[-1]
        url = f"https://distfiles.gentoo.org/releases/{cfg.architecture}/autobuilds/{rel}"
        runner.shell(f"wget -O {shlex.quote(root + '/' + tarball)} {shlex.quote(url)}")
        if cfg.verify_stage:
            # .sha256 is often a PGP-clearsigned file, not raw sha256sum input.
            # The detached .asc on the tarball is the check that must succeed.
            runner.shell(f"wget -O {shlex.quote(root + '/' + tarball + '.asc')} {shlex.quote(url + '.asc')}")
            runner.shell(
                "gpg --keyserver hkps://keys.gentoo.org --recv-keys "
                "13EBBDBEDE7A12775DFDB1BABB572E0E2D182910 D99EAC7379A850BCE47DA5F29E3146ED0DF79DC3 || true"
            )
            runner.shell(
                f"gpg --verify {shlex.quote(root + '/' + tarball + '.asc')} {shlex.quote(root + '/' + tarball)}"
            )
    # --skip-old-files: a partial extract must not replace files already on the target.
    skip_old = "--skip-old-files " if cfg.resume else ""
    runner.shell(
        f"tar xpvf {shlex.quote(root + '/' + tarball)} {skip_old}"
        f"--xattrs-include='*.*' --numeric-owner -C {shlex.quote(root)}"
    )
    ui.ok("Stage3 extracted")


def phase_chroot_mounts(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Bind /proc /sys /dev /run and copy resolv.conf — handbook 'mounting the necessary filesystems'."""
    proc = shlex.quote(root + "/proc")
    sysfs = shlex.quote(root + "/sys")
    dev = shlex.quote(root + "/dev")
    run = shlex.quote(root + "/run")
    runner.shell(f"mkdir -p {proc} {sysfs} {dev} {run}")
    runner.shell(f"mountpoint -q {proc} || mount --types proc /proc {proc}")
    runner.shell(f"mountpoint -q {sysfs} || mount --rbind /sys {sysfs}")
    runner.shell(f"mount --make-rslave {sysfs} || true")
    runner.shell(f"mountpoint -q {dev} || mount --rbind /dev {dev}")
    runner.shell(f"mount --make-rslave {dev} || true")
    runner.shell(f"mountpoint -q {run} || mount --bind /run {run} || true")
    runner.shell(f"cp /etc/resolv.conf {shlex.quote(root + '/etc/resolv.conf')} || true")


def _chroot_env_argv(*command: str) -> list[str]:
    """env -i + a command inside the Gentoo tree. Always LANG=C for the installer.

    Exporting the user's locale (en_US.UTF-8) before locale-gen has filled
    the archive makes localedef and later tools fail. Persist LANG in
    /etc/env.d/02locale for the *booted* system instead.
    """
    term = os.environ.get("TERM") or "linux"
    return [
        "env",
        "-i",
        "HOME=/root",
        f"TERM={term}",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG=C",
        "LC_ALL=C",
        *command,
    ]


def phase_chroot(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Write config files + chroot-install.sh, generate locales, then chroot."""
    devices = getattr(cfg, "_devices", _map_devices(cfg))
    script = render_chroot_script(cfg, devices)
    path = Path(root) / "root" / "chroot-install.sh"
    if cfg.dry_run:
        ui.info("Would write chroot-install.sh and chroot into the target.")
        Path("chroot-install.preview.sh").write_text(script, encoding="utf-8")
        ui.ok("Wrote chroot-install.preview.sh in the current directory")
        return
    _repair_chroot_leftovers(cfg, root)
    _write_make_conf(cfg, root)
    _write_system_files(cfg, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    ui.ok(f"Wrote {path} (rev {CHROOT_SCRIPT_REV}, {script.count(chr(10)) + 1} lines)")
    for i, line in enumerate(script.splitlines()[:12], 1):
        ui.info(f"  {i:3} {line}")
    _generate_locales(cfg, runner, root)
    secrets = Path(root) / "root" / ".install-secrets"
    secrets.write_text(
        "\n".join(
            [
                f"ROOT_PW={shlex.quote(cfg.root_password)}",
                f"USER_PW={shlex.quote(cfg.user_password)}",
                f"LUKS_PW={shlex.quote(cfg.luks_passphrase)}",
                f"GRUB_PW={shlex.quote(cfg.grub_password)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    secrets.chmod(0o600)
    cfg.save(str(Path(root) / "root" / "install-config.json"), include_secrets=False)
    cfg.save(str(Path(root) / "root" / "install-config.yaml"), include_secrets=False)
    # Handbook + wiki "Installation alternatives": LiveCDs leak a messy env.
    # Never bash --login / never source /etc/profile in the installer process.
    runner.run(_chroot_env_argv("chroot", root, "/bin/bash", "/root/chroot-install.sh"))
    if secrets.exists():
        secrets.unlink()


# Not a Portage FEATURE. Valid relatives: getbinpkg, binpkg-request-signature.
# --binpkg-respect-use is an emerge option only (EMERGE_DEFAULT_OPTS).
_BAD_FEATURE = "binpkg-request-use"
# Only valid FEATURE names. Naming binpkg-request-use even with a leading '-'
# can still trip unknown-features-warn on some Portage builds.
_FEATURES_SANITIZE = 'FEATURES="-unknown-features-warn unknown-features-filter"'


def _strip_bad_features(path: Path) -> bool:
    """Remove the invalid Portage FEATURES token from a text file. Returns True if changed."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    new = re.sub(rf"\b{_BAD_FEATURE}\b", "", text)
    if path.name in {"profile.env", "05etc-profile"} or path.parent.name == "env.d":
        new = re.sub(r"^export FEATURES=.*\n?", "", new, flags=re.MULTILINE)
        new = re.sub(r"^FEATURES=.*\n?", "", new, flags=re.MULTILINE)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def _repair_chroot_leftovers(cfg: InstallConfig, root: str) -> None:
    """Fix leftovers from earlier installer revisions before the chroot script runs.

    * FEATURES=binpkg-request-use in make.conf / /etc/make.conf / profile.env
      (not a Portage feature; Portage also stacks FEATURES from /etc/make.conf)
    * /etc/vconsole.conf created as a directory by `mkdir -p`
    """
    rootp = Path(root)
    cleaned = False
    for path in [
        rootp / "etc" / "portage" / "make.conf",
        rootp / "etc" / "make.conf",
        rootp / "etc" / "profile.env",
    ]:
        if _strip_bad_features(path):
            cleaned = True
    for folder in (rootp / "etc" / "portage", rootp / "etc" / "env.d"):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and _strip_bad_features(path):
                cleaned = True
    if cleaned:
        ui.info("Removed unknown FEATURES token binpkg-request-use from Portage env files")
    vc = rootp / "etc" / "vconsole.conf"
    if vc.is_dir():
        shutil.rmtree(vc)
        ui.info("Removed leftover /etc/vconsole.conf directory")
    vc.write_text(f"KEYMAP={cfg.keymap}\n", encoding="utf-8")


def _make_conf_text(cfg: InstallConfig) -> str:
    """Portage make.conf body (written on the host, not via a bash heredoc)."""
    jobs = cfg.makeopts_jobs or detect.nproc()
    video = detect.detect_gpu() if cfg.video_cards == "auto" else cfg.video_cards
    use: list[str] = []
    for token in (cfg.use_flags or "dist-kernel").split():
        if token and token not in use:
            use.append(token)
    use.extend(flag for flag in cfg.extra_use if flag not in use)
    if cfg.window_managers:
        use.append("wayland")
    if cfg.init_system == "systemd":
        use.append("systemd")
    else:
        use.append("elogind")
    if cfg.network_stack == "networkmanager":
        use.append("networkmanager")
    if cfg.hardened_flags:
        use.extend(["hardened", "pie", "ssp"])
    if cfg.selinux:
        use.append("selinux")
    loaders = selected_bootloaders(cfg)
    if "grub" in loaders:
        grub_plat = 'GRUB_PLATFORMS="efi-64"' if cfg.firmware == "uefi" else 'GRUB_PLATFORMS="pc"'
    else:
        grub_plat = ""
    mirrors = cfg.gentoo_mirrors or "https://distfiles.gentoo.org"
    cflags = "-march=native -O2 -pipe"
    if cfg.hardened_flags:
        cflags += " -fstack-protector-strong -fstack-clash-protection"
    if cfg.lto:
        cflags += " -flto"
    emerge_opts = f"--jobs={max(1, jobs // 2)} --load-average={jobs} --ask=n"
    if cfg.binhost:
        emerge_opts += " --getbinpkg --usepkg"
        if getattr(cfg, "binpkg_respect_use", False):
            emerge_opts += " --binpkg-respect-use=y"
        else:
            emerge_opts += " --binpkg-respect-use=n"
    l10n, linguas = _l10n_linguas(cfg)
    lines = [
        f'COMMON_FLAGS="{cflags}"',
        'CFLAGS="${COMMON_FLAGS}"',
        'CXXFLAGS="${COMMON_FLAGS}"',
        'FCFLAGS="${COMMON_FLAGS}"',
        'FFLAGS="${COMMON_FLAGS}"',
        f'MAKEOPTS="-j{jobs}"',
        f'EMERGE_DEFAULT_OPTS="{emerge_opts}"',
        _FEATURES_SANITIZE,
        f'USE="{" ".join(sorted(set(use)))}"',
        f'ACCEPT_LICENSE="{cfg.accept_license}"',
        f'VIDEO_CARDS="{video}"',
        f'GENTOO_MIRRORS="{mirrors}"',
    ]
    if l10n:
        lines.append(f'L10N="{l10n}"')
    if linguas:
        lines.append(f'LINGUAS="{linguas}"')
    if grub_plat:
        lines.append(grub_plat)
    return "\n".join(lines) + "\n"


def _write_text(path: Path, text: str, *, keep_existing: bool) -> None:
    """Write path. Identical contents are left alone. keep_existing skips any file already there."""
    if path.is_file():
        current = path.read_text(encoding="utf-8", errors="replace")
        if current == text:
            ui.info(f"Keeping identical file {path}")
            return
        if keep_existing:
            ui.info(f"Keeping existing file {path}")
            return
    if path.is_dir():
        ui.warn(f"Not replacing directory {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_make_conf(cfg: InstallConfig, root: str) -> None:
    text = _make_conf_text(cfg)
    portage = Path(root) / "etc" / "portage"
    portage.mkdir(parents=True, exist_ok=True)
    dest = portage / "make.conf"
    if dest.is_dir():
        dest = dest / "99gentoo-live-installer"
    # make.conf is how emerge is configured. Refresh it when the text changed
    # so a resume does not keep a broken FEATURES line. Identical files are kept.
    _write_text(dest, text, keep_existing=False)
    # FEATURES is incremental: a leftover /etc/make.conf would still merge the
    # unknown token even after /etc/portage/make.conf is clean.
    legacy = Path(root) / "etc" / "make.conf"
    if legacy.exists() and not legacy.is_symlink():
        if legacy.is_dir():
            (legacy / "99gentoo-live-installer-clear").write_text(_FEATURES_SANITIZE + "\n", encoding="utf-8")
        else:
            _strip_bad_features(legacy)
            extra = legacy.read_text(encoding="utf-8", errors="replace")
            if "unknown-features-filter" not in extra:
                legacy.write_text(extra.rstrip() + "\n" + _FEATURES_SANITIZE + "\n", encoding="utf-8")
    use_dir = portage / "package.use"
    use_dir.mkdir(parents=True, exist_ok=True)
    # compile-locales makes glibc build every locale in the source tree (~500).
    _write_text(
        use_dir / "00glibc-locales",
        "sys-libs/glibc -compile-locales\n",
        keep_existing=False,
    )


def _safe_timezone(raw: str) -> str:
    text = (raw or "UTC").strip()
    if not re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*", text):
        return "UTC"
    return text


def _l10n_linguas(cfg: InstallConfig) -> tuple[str, str]:
    """Portage L10N (hyphenated) and LINGUAS (language) from the chosen locales."""
    l10n: list[str] = []
    linguas: list[str] = []
    extras = getattr(cfg, "extra_locales", None) or []
    for loc in [cfg.locale, *extras]:
        base = (loc or "").split(".")[0].strip()
        if not base or base in {"C", "POSIX"}:
            continue
        parts = base.split("_")
        lang = parts[0].lower()
        if lang and lang not in linguas:
            linguas.append(lang)
        token = f"{lang}-{parts[1]}" if len(parts) == 2 else lang
        if token not in l10n:
            l10n.append(token)
    return " ".join(l10n), " ".join(linguas)


def _locale_gen_lines(cfg: InstallConfig) -> list[str]:
    """Handbook locale.gen entries. musl does not use glibc locale-gen."""
    if cfg.stage_variant == "musl":
        return []
    seen: list[str] = []
    extras = getattr(cfg, "extra_locales", None) or []
    for loc in [cfg.locale, *extras, "C.UTF-8"]:
        loc = (loc or "").strip()
        if loc and loc not in seen and loc != "custom":
            seen.append(loc)
    return [f"{loc} UTF-8" for loc in seen]


def _write_system_files(cfg: InstallConfig, root: str) -> None:
    """Hostname, timezone, keymap, locale.gen, env.d/02locale — written on the host.

    Doing this in Python means chroot-install.sh never has a 'locale-gen' line
    whose number bash can report as the crash site, and never sources /etc/profile
    (debuginfod nounset + FEATURES re-export).
    """
    rootp = Path(root)
    etc = rootp / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    keep = cfg.resume
    _write_text(etc / "hostname", cfg.hostname + "\n", keep_existing=keep)
    confd = etc / "conf.d"
    confd.mkdir(parents=True, exist_ok=True)
    _write_text(confd / "hostname", f'hostname="{cfg.hostname}"\n', keep_existing=keep)
    _write_text(confd / "keymaps", f'keymap="{cfg.keymap}"\n', keep_existing=keep)
    tz = _safe_timezone(cfg.timezone)
    _write_text(etc / "timezone", tz + "\n", keep_existing=keep)
    localtime = etc / "localtime"
    if keep and (localtime.exists() or localtime.is_symlink()):
        ui.info(f"Keeping existing file {localtime}")
    else:
        try:
            if localtime.exists() or localtime.is_symlink():
                localtime.unlink()
            localtime.symlink_to(f"/usr/share/zoneinfo/{tz}")
        except OSError:
            ui.warn(f"Could not link /etc/localtime to zoneinfo/{tz}")
    lines = _locale_gen_lines(cfg)
    loc_gen = etc / "locale.gen"
    loc_gen_d = etc / "locale.gen.d"
    locale_body = (
        "# generated by gentoo-live-installer — only these locales, not the full glibc set\n"
        + "\n".join(lines)
        + ("\n" if lines else "")
    )
    if keep and (loc_gen.exists() or loc_gen_d.exists()):
        ui.info("Keeping existing locale.gen")
    else:
        if loc_gen.is_dir():
            shutil.rmtree(loc_gen)
        if loc_gen_d.is_dir():
            shutil.rmtree(loc_gen_d)
        _write_text(loc_gen, locale_body, keep_existing=False)
    envd = etc / "env.d"
    envd.mkdir(parents=True, exist_ok=True)
    loc = cfg.locale.strip() or "C.UTF-8"
    _write_text(envd / "02locale", f'LANG="{loc}"\nLC_COLLATE="C"\n', keep_existing=keep)
    vc = etc / "vconsole.conf"
    if vc.is_dir() and not keep:
        shutil.rmtree(vc)
    if not vc.is_dir():
        _write_text(vc, f"KEYMAP={cfg.keymap}\n", keep_existing=keep)
    ui.ok("Wrote hostname, timezone, keymap, locale.gen, and env.d/02locale")


def _generate_locales(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Run locale-gen inside the chroot with LANG=C. Never abort the installer."""
    if cfg.stage_variant == "musl":
        ui.info("musl stage: skipping glibc locale-gen")
        return
    ui.chapter("8", "Generating locales (LANG=C)", "https://wiki.gentoo.org/wiki/Handbook:AMD64/Installation/System")
    runner.run(
        _chroot_env_argv(
            "chroot",
            root,
            "/bin/bash",
            "-c",
            "locale-gen || true",
        ),
        check=False,
    )
    loc = shlex.quote(cfg.locale)
    short = shlex.quote(cfg.locale.split(".")[0])
    runner.run(
        _chroot_env_argv(
            "chroot",
            root,
            "/bin/bash",
            "-c",
            f"eselect locale set {loc} || eselect locale set {short} || true",
        ),
        check=False,
    )


def phase_done(cfg: InstallConfig, runner: Runner, root: str) -> None:
    ui.ok("Installation phases finished.")
    ui.info(f"When you are ready: umount -R {root} && reboot")
    ui.info("Remove the Ubuntu live USB so the machine boots from the Gentoo disk.")


def _systemd_unit_test(unit: str) -> str:
    """True when unit is installed under either merged-/usr or /lib."""
    return (
        f"[ -e /usr/lib/systemd/system/{unit}.service ] || "
        f"[ -e /lib/systemd/system/{unit}.service ]"
    )


def _openrc_enable_line(service: str) -> str:
    """rc-update only after the init script exists, so a missing package is obvious."""
    return (
        f'if [ -e /etc/init.d/{service} ]; then rc-update add {service} default; '
        f'else echo ">> OpenRC service {service} does not exist" >&2; fi'
    )


def _systemd_enable_line(service: str) -> str:
    test = _systemd_unit_test(service)
    return (
        f"if {test}; then systemctl enable {service}; "
        f'else echo ">> systemd unit {service}.service does not exist" >&2; fi'
    )


def _required_tool_repairs(cfg: InstallConfig, loaders: list[str]) -> str:
    """Re-emerge every tool the later chroot lines call.

    Portage stops the rest of a multi-package emerge when one package fails
    unless --keep-going is set, and even then a package can still be skipped.
    grub-install, plymouth-set-default-theme, and rc-update then report that
    the command or service does not exist.
    """
    checks: list[tuple[str, str]] = [("command -v sudo >/dev/null 2>&1", "app-admin/sudo")]
    if "grub" in loaders:
        checks.append(
            (
                "command -v grub-install >/dev/null 2>&1 && command -v grub-mkconfig >/dev/null 2>&1",
                "sys-boot/grub",
            )
        )
        if cfg.firmware == "uefi":
            checks.append(("command -v efibootmgr >/dev/null 2>&1", "sys-boot/efibootmgr"))
    if "systemd-boot" in loaders:
        boot_atom = "sys-apps/systemd" if cfg.init_system == "systemd" else "sys-apps/systemd-utils"
        checks.append(("command -v bootctl >/dev/null 2>&1", boot_atom))
    if "refind" in loaders:
        checks.append(("command -v refind-install >/dev/null 2>&1", "sys-boot/refind"))
    if "syslinux" in loaders:
        checks.append(("command -v syslinux >/dev/null 2>&1", "sys-boot/syslinux"))
    if "limine" in loaders:
        checks.append(("command -v limine >/dev/null 2>&1", "sys-boot/limine"))
    if "lilo" in loaders:
        checks.append(("command -v lilo >/dev/null 2>&1", "sys-boot/lilo"))
    if cfg.plymouth:
        checks.append(("command -v plymouth-set-default-theme >/dev/null 2>&1", "sys-boot/plymouth"))
    openrc = cfg.init_system == "openrc"
    if cfg.network_stack == "networkmanager":
        if openrc:
            checks.append(("[ -e /etc/init.d/dbus ]", "sys-apps/dbus"))
            checks.append(("[ -e /etc/init.d/NetworkManager ]", "net-misc/networkmanager"))
        else:
            checks.append((_systemd_unit_test("NetworkManager"), "net-misc/networkmanager"))
    elif cfg.network_stack == "dhcpcd":
        if openrc:
            checks.append(("[ -e /etc/init.d/dhcpcd ]", "net-misc/dhcpcd"))
        else:
            checks.append((_systemd_unit_test("dhcpcd"), "net-misc/dhcpcd"))
    if cfg.sshd:
        if openrc:
            checks.append(("[ -e /etc/init.d/sshd ]", "net-misc/openssh"))
        else:
            checks.append((_systemd_unit_test("sshd"), "net-misc/openssh"))
    if cfg.chrony:
        if openrc:
            checks.append(("[ -e /etc/init.d/chronyd ]", "net-misc/chrony"))
        else:
            checks.append((_systemd_unit_test("chronyd"), "net-misc/chrony"))
    if cfg.cups:
        if openrc:
            checks.append(("[ -e /etc/init.d/cupsd ]", "net-print/cups"))
        else:
            checks.append((_systemd_unit_test("cups"), "net-print/cups"))
    if openrc and cfg.syslog != "none":
        checks.append((f"[ -e /etc/init.d/{cfg.syslog} ]", f"app-admin/{cfg.syslog}"))
    if openrc and cfg.cron != "none":
        svc = "cronie" if cfg.cron == "cronie" else cfg.cron
        checks.append((f"[ -e /etc/init.d/{svc} ]", f"sys-process/{cfg.cron}"))
    if openrc and cfg.zram:
        checks.append(("[ -e /etc/init.d/zram-init ]", "sys-block/zram-init"))
    dm = cfg.display_manager
    if dm == "auto":
        dm = {
            "plasma": "sddm",
            "gnome": "gdm",
            "xfce": "lightdm",
            "mate": "lightdm",
            "cinnamon": "lightdm",
            "i3": "lightdm",
        }.get(cfg.desktop, "none")
    dm_atoms = {"sddm": "x11-misc/sddm", "gdm": "gnome-base/gdm", "lightdm": "x11-misc/lightdm"}
    if dm in dm_atoms:
        if openrc:
            checks.append((f"[ -e /etc/init.d/{dm} ]", dm_atoms[dm]))
        else:
            checks.append((_systemd_unit_test(dm), dm_atoms[dm]))
    lines = ["echo '>> repair tools that must exist before bootloader and services'"]
    for test, atom in checks:
        quoted = shlex.quote(atom)
        lines.append(
            "\n".join(
                [
                    f"if ! {{ {test}; }}; then",
                    f'  echo ">> {atom} is not installed"',
                    f"  emerge --ask=n {quoted}",
                    "  _st=$?",
                    '  if [ "$_st" -eq 132 ]; then disable_binpkgs; fi',
                    "fi",
                    f"if ! {{ {test}; }}; then",
                    f'  echo ">> {atom} still missing; building from source"',
                    f"  emerge --ask=n --usepkg=n --getbinpkg=n {quoted} || echo '>> emerge failed: {atom}' >&2",
                    "fi",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _final_tool_checks(cfg: InstallConfig, loaders: list[str]) -> str:
    """Warn when a service is absent. Exit only when the disk cannot boot.

    The script runs under `set +e`. A missing optional package must not hide a
    finished kernel and bootloader, and a missing kernel must not be marked done.
    """
    checks: list[tuple[str, str]] = []
    if cfg.username:
        checks.append(("[ -e /etc/sudoers.d/wheel ]", "/etc/sudoers.d/wheel"))
    if cfg.plymouth:
        checks.append(("command -v plymouth-set-default-theme >/dev/null 2>&1", "plymouth-set-default-theme"))
    openrc = cfg.init_system == "openrc"
    if cfg.network_stack == "networkmanager":
        checks.append(
            (
                "[ -e /etc/init.d/NetworkManager ]" if openrc else _systemd_unit_test("NetworkManager"),
                "NetworkManager",
            )
        )
    if cfg.chrony:
        checks.append(
            ("[ -e /etc/init.d/chronyd ]" if openrc else _systemd_unit_test("chronyd"), "chronyd")
        )
    if openrc and cfg.syslog != "none":
        checks.append((f"[ -e /etc/init.d/{cfg.syslog} ]", cfg.syslog))
    if openrc and cfg.cron != "none":
        svc = "cronie" if cfg.cron == "cronie" else cfg.cron
        checks.append((f"[ -e /etc/init.d/{svc} ]", svc))
    if openrc and cfg.zram:
        checks.append(("[ -e /etc/init.d/zram-init ]", "zram-init"))
    lines = [
        'echo ">> verify kernel, bootloader, and services"',
        "missing=0",
        "kernel_ok=0",
        "boot_ok=0",
        "if ls /boot/vmlinuz* >/dev/null 2>&1 || ls /boot/EFI/Linux/*.efi >/dev/null 2>&1; then kernel_ok=1; fi",
    ]
    for test, label in checks:
        lines.append(f"if ! {{ {test}; }}; then echo \">> still missing: {label}\" >&2; fi")
    boot_tests = []
    if "grub" in loaders:
        boot_tests.append("command -v grub-install >/dev/null 2>&1 && command -v grub-mkconfig >/dev/null 2>&1")
    if "systemd-boot" in loaders:
        boot_tests.append("command -v bootctl >/dev/null 2>&1")
    if "refind" in loaders:
        boot_tests.append("command -v refind-install >/dev/null 2>&1")
    if "syslinux" in loaders:
        boot_tests.append("command -v syslinux >/dev/null 2>&1")
    if "limine" in loaders:
        boot_tests.append("command -v limine >/dev/null 2>&1")
    if "lilo" in loaders:
        boot_tests.append("command -v lilo >/dev/null 2>&1")
    if "efistub" in loaders or not boot_tests:
        boot_tests.append('[ "$kernel_ok" -eq 1 ]')
    joined = " || ".join(boot_tests)
    lines.append(f"if {joined}; then boot_ok=1; else echo '>> no selected bootloader was installed' >&2; fi")
    lines.append('if [ "$kernel_ok" -eq 0 ]; then echo ">> no kernel image was installed" >&2; missing=1; fi')
    lines.append('if [ "$boot_ok" -eq 0 ]; then missing=1; fi')
    lines.append('if [ "$missing" -ne 0 ]; then echo ">> chroot configuration incomplete" >&2; exit 1; fi')
    return "\n".join(lines) + "\n"


def _resume_prelude(cfg: InstallConfig) -> str:
    """Keep files already on the target, and skip emerge of installed packages.

    --noreplace leaves an already-merged package's files alone and still
    installs anything that is missing. append_once does not duplicate a line
    that is already in a config file. Stage3's stock /etc/fstab is not treated
    as ours; a fstab this installer already wrote is kept.
    """
    flag = "1" if cfg.resume else "0"
    return f"""
KEEP_EXISTING={flag}
NR=
if [ "$KEEP_EXISTING" = 1 ]; then
  NR="--noreplace"
  echo ">> resume: keep files already on the target; emerge skips packages already installed"
fi
append_once() {{
  local file="$1"
  local line="$2"
  mkdir -p "$(dirname "$file")"
  if [ -f "$file" ] && grep -qxF "$line" "$file"; then
    echo ">> skip existing line in $file"
    return 0
  fi
  echo "$line" >> "$file"
}}
"""


def _source_setup_script() -> str:
    """Bash helpers: refuse v3 binpkgs on older CPUs, and rebuild after SIGILL.

    Exit 132 is the shell's report for "Illegal instruction". Official
    x86-64-v3 binpkgs (and a gentoo-kernel-bin built for that ABI) raise it
    on CPUs without AVX2. Source builds use -march=native from make.conf.
    """
    return """
cpu_has_v3=0
if grep -qw avx2 /proc/cpuinfo && grep -qw bmi1 /proc/cpuinfo && grep -qw bmi2 /proc/cpuinfo && grep -qw fma /proc/cpuinfo && grep -qw movbe /proc/cpuinfo; then
  cpu_has_v3=1
fi
if [ "$cpu_has_v3" -eq 0 ] && [ -f /etc/portage/binrepos.conf/gentoobinhost.conf ] && grep -q x86-64-v3 /etc/portage/binrepos.conf/gentoobinhost.conf; then
  echo ">> this CPU cannot run x86-64-v3 binpkgs (Illegal instruction); using the x86-64 binhost"
  sed -i 's/x86-64-v3/x86-64/g' /etc/portage/binrepos.conf/gentoobinhost.conf || true
fi

disable_binpkgs() {
  echo ">> a binary raised Illegal instruction; further emerges use source"
  local conf
  for conf in /etc/portage/make.conf /etc/portage/make.conf/*; do
    [ -f "$conf" ] || continue
    sed -i -e 's/ --getbinpkg / /g' -e 's/ --usepkg / /g' "$conf" || true
  done
}

emerge_with_source() {
  # Distfiles are fetched even when a binpkg will be installed, so a later
  # Illegal instruction can be rebuilt without another mirror lookup.
  emerge --ask=n --fetchonly --usepkg=n --getbinpkg=n --keep-going "$@" || echo ">> some distfiles were not fetched" >&2
  emerge --ask=n --keep-going $NR "$@"
  status=$?
  if [ "$status" -eq 0 ]; then
    return 0
  fi
  if [ "$status" -eq 132 ]; then
    echo ">> emerge died with Illegal instruction; rebuilding from source" >&2
    disable_binpkgs
    emerge --ask=n --keep-going --usepkg=n --getbinpkg=n "$@"
    return $?
  fi
  echo ">> emerge failed ($status); required tools are rebuilt from source next" >&2
  return "$status"
}
"""


def _grub_uefi_install(target: str) -> str:
    """Install GRUB for UEFI. efivarfs is often read-only in an Ubuntu chroot.

    That makes efibootmgr exit with "No such file or directory". The removable
    fallback still writes EFI/BOOT/BOOTX64.EFI so the firmware can boot.
    A binpkg built without GRUB_PLATFORMS=efi-64 has no modinfo.sh; rebuild it.
    """
    return "\n".join(
        [
            f"if [ ! -e /usr/lib/grub/{target}/modinfo.sh ]; then",
            '  echo ">> GRUB EFI modules missing; rebuilding sys-boot/grub from source"',
            "  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || echo '>> grub rebuild failed' >&2",
            "fi",
            "if [ -d /sys/firmware/efi ]; then",
            "  mkdir -p /sys/firmware/efi/efivars",
            "  mountpoint -q /sys/firmware/efi/efivars || mount -t efivarfs efivarfs /sys/firmware/efi/efivars || true",
            "  mount -o remount,rw,nosuid,nodev,noexec -t efivarfs efivarfs /sys/firmware/efi/efivars || true",
            "fi",
            "mkdir -p /boot/grub /boot/EFI",
            "if ! command -v grub-install >/dev/null 2>&1; then",
            '  echo ">> grub-install does not exist; building sys-boot/grub from source"',
            "  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || echo '>> grub source build failed' >&2",
            "fi",
            f"grub-install --target={target} --efi-directory=/boot --bootloader-id=Gentoo",
            "grub_status=$?",
            'if [ "$grub_status" -eq 132 ]; then',
            '  echo ">> grub-install: illegal instruction; rebuilding sys-boot/grub from source"',
            "  disable_binpkgs",
            "  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || echo '>> grub source build failed' >&2",
            f"  grub-install --target={target} --efi-directory=/boot --bootloader-id=Gentoo",
            "  grub_status=$?",
            "fi",
            'if [ "$grub_status" -ne 0 ]; then',
            '  echo ">> grub-install could not register NVRAM; writing the removable EFI fallback"',
            f"  grub-install --target={target} --efi-directory=/boot --bootloader-id=Gentoo --removable --no-nvram || echo '>> grub-install failed' >&2",
            "fi",
            "if command -v grub-mkconfig >/dev/null 2>&1; then",
            "  grub-mkconfig -o /boot/grub/grub.cfg || echo '>> grub-mkconfig failed' >&2",
            "else",
            '  echo ">> grub-mkconfig does not exist" >&2',
            "fi",
        ]
    )


def render_chroot_script(cfg: InstallConfig, devices: dict[str, str]) -> str:
    """Build the bash script that runs *inside* Gentoo (Portage, kernel, bootloader).

    Keep this as bash, not Python: the chroot only has the stage3 toolchain.
    """
    loaders = selected_bootloaders(cfg)
    binrepo = ""
    if cfg.binhost and cfg.architecture == "amd64":
        binrepo = f"""
mkdir -p /etc/portage/binrepos.conf
cat > /etc/portage/binrepos.conf/gentoobinhost.conf << 'EOF'
[binhost]
priority = 9999
sync-uri = https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/{cfg.binhost_abi}/
EOF
"""
    kw_arch = "amd64" if cfg.architecture == "amd64" else "arm64"
    keywords = (
        f"append_once /etc/portage/package.accept_keywords/testing '*/* ~{kw_arch}'"
        if cfg.testing_keywords
        else ""
    )
    sync = {
        "webrsync": "emerge-webrsync || emerge --sync",
        "rsync": "emerge --sync",
        "git": """
mkdir -p /etc/portage/repos.conf
cat > /etc/portage/repos.conf/gentoo.conf << 'EOF'
[gentoo]
location = /var/db/repos/gentoo
sync-type = git
sync-uri = https://github.com/gentoo-mirror/gentoo.git
auto-sync = yes
EOF
emerge --sync
""",
    }[cfg.sync_method]

    fstab = _fstab(cfg, devices)
    crypttab = _crypttab(cfg, devices)
    crypttab_block = ""
    if crypttab:
        crypttab_block = f"""
cat > /etc/crypttab << 'CRYPTTAB'
{crypttab.strip()}
CRYPTTAB
"""
    dracut_luks = ""
    if cfg.luks and cfg.initramfs == "dracut":
        dracut_luks = """
mkdir -p /etc/dracut.conf.d
echo 'add_dracutmodules+=" crypt dm "' > /etc/dracut.conf.d/luks.conf
"""
    netifrc_iface = _netifrc_iface(cfg)
    pkgs_kernel = cfg.kernel
    extra_fw = []
    if cfg.linux_firmware:
        extra_fw.append("sys-kernel/linux-firmware")
    if cfg.sof_firmware:
        extra_fw.append("sys-firmware/sof-firmware")
    if cfg.intel_microcode:
        extra_fw.append("sys-firmware/intel-microcode")
    net_pkgs = []
    if cfg.network_stack == "networkmanager":
        net_pkgs.append("net-misc/networkmanager")
        net_pkgs.append("sys-apps/dbus")
    elif cfg.network_stack == "dhcpcd":
        net_pkgs.append("net-misc/dhcpcd")
    elif cfg.network_stack == "systemd-networkd":
        net_pkgs.append("sys-apps/systemd")
    if cfg.wireless:
        net_pkgs.extend(["net-wireless/iw", "net-wireless/wpa_supplicant"])
    if cfg.sshd:
        net_pkgs.append("net-misc/openssh")
    if cfg.cups:
        net_pkgs.append("net-print/cups")
    if cfg.chrony:
        net_pkgs.append("net-misc/chrony")
    if cfg.init_system == "openrc":
        if cfg.syslog != "none":
            net_pkgs.append(f"app-admin/{cfg.syslog}")
        if cfg.cron != "none":
            net_pkgs.append(f"sys-process/{cfg.cron}")
    if cfg.zram:
        net_pkgs.append("sys-apps/zram-generator" if cfg.init_system == "systemd" else "sys-block/zram-init")

    boot_pkgs = []
    if "grub" in loaders:
        boot_pkgs.append("sys-boot/grub")
        if cfg.firmware == "uefi":
            boot_pkgs.append("sys-boot/efibootmgr")
    if "systemd-boot" in loaders:
        boot_pkgs.append("sys-apps/systemd" if cfg.init_system == "systemd" else "sys-apps/systemd-utils")
    if "refind" in loaders:
        boot_pkgs.append("sys-boot/refind")
    if "syslinux" in loaders:
        boot_pkgs.append("sys-boot/syslinux")
    if "limine" in loaders:
        boot_pkgs.append("sys-boot/limine")
        if cfg.firmware == "uefi":
            boot_pkgs.append("sys-boot/efibootmgr")
    if "lilo" in loaders:
        boot_pkgs.append("sys-boot/lilo")
    boot_pkgs = [p for p in boot_pkgs if p]
    if cfg.initramfs == "dracut":
        boot_pkgs.append("sys-kernel/dracut")
        boot_pkgs.append("sys-kernel/installkernel")
    if cfg.plymouth:
        boot_pkgs.append("sys-boot/plymouth")
        for extra_theme in catalog.plymouth_packages_for(
            cfg.plymouth_themes or [cfg.plymouth_theme],
            cfg.plymouth_openrc_plugin and cfg.init_system == "openrc",
        ):
            boot_pkgs.append(extra_theme.atom)
    if cfg.luks:
        boot_pkgs.append("sys-fs/cryptsetup")
    fs_tools = {
        "ext4": "sys-fs/e2fsprogs",
        "xfs": "sys-fs/xfsprogs",
        "btrfs": "sys-fs/btrfs-progs",
        "f2fs": "sys-fs/f2fs-tools",
        "jfs": "sys-fs/jfsutils",
        "zfs": "sys-fs/zfs",
    }
    boot_pkgs.append(fs_tools.get(cfg.filesystem, "sys-fs/e2fsprogs"))
    boot_pkgs.append("sys-fs/dosfstools")
    boot_pkgs.append("app-admin/sudo")

    de_pkgs = _desktop_packages(cfg)
    all_extra = " ".join(
        dict.fromkeys(
            a
            for a in boot_pkgs + net_pkgs + extra_fw + de_pkgs + cfg.extra_packages + cfg.window_managers
            if a and not a.startswith("flatpak-app/")
        )
    )
    kernel_atom = f"sys-kernel/{pkgs_kernel}"
    # gentoo-kernel is the source dist-kernel. Fetch it even when the selected
    # kernel is a binpkg or gentoo-sources, so a SIGILL or a sources-only tree
    # can still produce /boot/vmlinuz.
    fetch_atoms = [kernel_atom]
    if kernel_atom != "sys-kernel/gentoo-kernel":
        fetch_atoms.insert(0, "sys-kernel/gentoo-kernel")
    fetch_line = " ".join(dict.fromkeys(fetch_atoms))
    if all_extra:
        fetch_line = f"{fetch_line} {all_extra}"
    source_setup = _source_setup_script()
    resume_prelude = _resume_prelude(cfg)

    dm = cfg.display_manager
    if dm == "auto":
        dm = {"plasma": "sddm", "gnome": "gdm", "xfce": "lightdm", "mate": "lightdm", "cinnamon": "lightdm", "i3": "lightdm"}.get(
            cfg.desktop, "none"
        )

    openrc_services = []
    systemd_services = []
    if cfg.network_stack == "networkmanager":
        # NetworkManager's OpenRC script needs dbus already in the runlevel.
        openrc_services.append("dbus")
        openrc_services.append("NetworkManager")
        systemd_services.append("NetworkManager")
    elif cfg.network_stack == "dhcpcd":
        openrc_services.append("dhcpcd")
        systemd_services.append("dhcpcd")
    if cfg.sshd:
        openrc_services.append("sshd")
        systemd_services.append("sshd")
    if cfg.chrony:
        openrc_services.append("chronyd")
        systemd_services.append("chronyd")
    if cfg.cups:
        openrc_services.append("cupsd")
        systemd_services.append("cups")
    if cfg.init_system == "openrc" and cfg.syslog != "none":
        openrc_services.append(cfg.syslog)
    if cfg.init_system == "openrc" and cfg.cron != "none":
        openrc_services.append("cronie" if cfg.cron == "cronie" else cfg.cron)
    if dm not in {"none", "auto", ""}:
        openrc_services.append(dm)
        systemd_services.append(dm)

    enable_openrc = "\n".join(_openrc_enable_line(s) for s in openrc_services)
    enable_systemd = "\n".join(_systemd_enable_line(s) for s in systemd_services)
    repair_tools = _required_tool_repairs(cfg, loaders)
    final_checks = _final_tool_checks(cfg, loaders)
    nm_cleanup = ""
    if cfg.init_system == "openrc" and cfg.network_stack == "networkmanager":
        nm_cleanup = """
rc-update del dhcpcd default 2>/dev/null || true
for netinit in /etc/runlevels/default/net.*; do
  [ -e "$netinit" ] || continue
  rc-update del "$(basename "$netinit")" default || true
done
"""

    installkernel_conf = ""
    if cfg.bootloader == "grub":
        installkernel_conf = (
            "append_once /etc/portage/package.use/installkernel 'sys-kernel/installkernel grub dracut'"
        )
    elif cfg.bootloader == "systemd-boot":
        installkernel_conf = (
            "append_once /etc/portage/package.use/installkernel "
            "'sys-kernel/installkernel systemd systemd-boot dracut'"
        )
    elif cfg.uki or cfg.bootloader == "efistub":
        installkernel_conf = (
            "append_once /etc/portage/package.use/installkernel 'sys-kernel/installkernel dracut uki'"
        )
    else:
        installkernel_conf = (
            "append_once /etc/portage/package.use/installkernel 'sys-kernel/installkernel dracut'"
        )

    grub_install = _install_bootloaders(cfg, devices, loaders)

    user_block = ""
    if cfg.username:
        user_block = f"""
useradd -m -G {cfg.user_groups} -s /bin/bash {shlex.quote(cfg.username)} || true
if [ -f /root/.install-secrets ]; then
  # shellcheck disable=SC1091
  . /root/.install-secrets
  printf '%s:%s\\n' root "${{ROOT_PW}}" | chpasswd
  printf '%s:%s\\n' {shlex.quote(cfg.username)} "${{USER_PW}}" | chpasswd
fi
# app-admin/sudo does not keepdir /etc/sudoers.d. Portage drops that empty
# directory, so a redirect here fails: "No such file or directory".
if ! command -v sudo >/dev/null 2>&1; then
  emerge --ask=n app-admin/sudo || echo ">> sudo emerge failed" >&2
fi
mkdir -p /etc/sudoers.d
chmod 750 /etc/sudoers.d
echo '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/wheel
chmod 440 /etc/sudoers.d/wheel
if [ -f /etc/sudoers ] && ! grep -Eq '^[#@]includedir[[:space:]]+/etc/sudoers\\.d([[:space:]]|$)' /etc/sudoers; then
  echo '@includedir /etc/sudoers.d' >> /etc/sudoers
fi
if command -v visudo >/dev/null 2>&1; then
  visudo -cf /etc/sudoers || echo ">> sudoers syntax check failed" >&2
fi
"""
    else:
        user_block = """
if [ -f /root/.install-secrets ]; then
  . /root/.install-secrets
  echo "root:${ROOT_PW}" | chpasswd
fi
"""

    zram_block = ""
    if cfg.zram:
        if cfg.init_system == "systemd":
            zram_block = """
mkdir -p /etc/systemd/zram-generator.conf.d
cat > /etc/systemd/zram-generator.conf << 'EOF'
[zram0]
zram-size = ram / 2
compression-algorithm = zstd
EOF
"""
        else:
            zram_block = """
if [ ! -e /etc/init.d/zram-init ]; then
  echo ">> OpenRC service zram-init is missing; building sys-block/zram-init from source"
  emerge --ask=n --usepkg=n --getbinpkg=n sys-block/zram-init || echo ">> zram-init source build failed" >&2
fi
cat > /etc/conf.d/zram-init << 'EOF'
load_on_start=yes
num_devices=1
type0=swap
size0=$(($(awk '/MemTotal/ {print int($2/2)}' /proc/meminfo)))
algo0=zstd
EOF
if [ -e /etc/init.d/zram-init ]; then rc-update add zram-init boot; else echo ">> OpenRC service zram-init does not exist" >&2; fi
"""

    swapfile = ""
    if cfg.swap_mode in {"file", "both"}:
        swapfile = f"""
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count={cfg.swap_size_gib * 1024} status=progress || true
  chmod 600 /swapfile
  mkswap /swapfile
fi
append_once /etc/fstab '/swapfile none swap sw 0 0'
"""

    tpm_script = ""
    if cfg.tpm2_unlock and cfg.luks:
        tpm_script = f"""
cat > /usr/local/sbin/gentoo-tpm-enroll.sh << 'EOF'
#!/bin/bash
set -e
cryptsetup luksDump {devices.get("luks_source", "/dev/sda2")}
echo "Enroll TPM2 after a real boot. systemd: systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7+14 DEVICE"
echo "OpenRC: use clevis luks bind -d DEVICE tpm2 '{{}}'"
EOF
chmod +x /usr/local/sbin/gentoo-tpm-enroll.sh
"""

    selinux_block = ""
    if cfg.selinux:
        selinux_block = f"""
emerge --ask=n sec-policy/selinux-base sec-policy/selinux-base-policy sys-apps/policycoreutils || true
cat > /etc/selinux/config << EOF
SELINUX=permissive
SELINUXTYPE={cfg.selinux_policy}
EOF
"""

    overlay_block = _overlay_script(cfg)
    stores_block = _app_stores_script(cfg)
    plymouth_block = _plymouth_script(cfg)
    level = getattr(cfg, "stage_level", "stage3") or "stage3"
    bootstrap = ""
    if level == "stage1":
        bootstrap = """
echo ">> stage1 bootstrap (seed toolchain, then @system)"
emerge --ask=n --oneshot sys-devel/binutils sys-devel/gcc sys-libs/glibc sys-devel/make || true
emerge --ask=n --emptytree @system || true
"""
    elif level == "stage2":
        bootstrap = """
echo ">> stage2 bootstrap (@system)"
emerge --ask=n --emptytree @system || true
"""

    return f"""#!/bin/bash
# gentoo-live-installer chroot script rev {CHROOT_SCRIPT_REV}
# Locales, hostname, timezone, keymap, make.conf are written by the host.
# This script must never `source /etc/profile` (debuginfod nounset + FEATURES).
# This script must never `export LANG=` to the user locale (archive not ready).
set +eu
unset FEATURES
unset PORTAGE_FEATURES
export LANG=C
export LC_ALL=C
export DEBUGINFOD_URLS="${{DEBUGINFOD_URLS:-}}"
export DEBUGINFOD_CERT_PATH="${{DEBUGINFOD_CERT_PATH:-}}"
export DEBUGINFOD_IMA_CERT_PATH="${{DEBUGINFOD_IMA_CERT_PATH:-}}"
echo "=== gentoo-live-installer chroot rev {CHROOT_SCRIPT_REV} ==="
{resume_prelude}
mkdir -p /etc/portage/package.use /etc/portage/package.accept_keywords /etc/portage/package.license /etc/portage/profile
cat > /root/.bashrc << 'BASHRC'
unset FEATURES
export PS1="(chroot) ${{PS1:-}}"
BASHRC

echo ">> binhost / installkernel"
{binrepo}
{source_setup}
{installkernel_conf}
{dracut_luks}
echo 'sys-kernel/linux-firmware @BINARY-REDISTRIBUTABLE' > /etc/portage/package.license/firmware || true
{keywords}

echo ">> sync Portage tree"
{sync}
unset FEATURES
{bootstrap}
echo ">> emerge Portage"
emerge --ask=n --oneshot --verbose $NR sys-apps/portage || true
unset FEATURES
{overlay_block}
emerge --ask=n --oneshot --verbose $NR sys-apps/portage || true
unset FEATURES
echo ">> eselect profile"
eselect profile set {shlex.quote(cfg.profile)} || eselect profile set --force {shlex.quote(cfg.profile)} || true
if command -v mirrorselect >/dev/null 2>&1; then
  mirrorselect -s3 -b10 || true
fi
echo ">> emerge @world"
emerge --verbose --update --deep --newuse $NR @world
world_status=$?
if [ "$world_status" -eq 132 ]; then
  echo ">> @world died with Illegal instruction; rebuilding from source"
  disable_binpkgs
  emerge --verbose --update --deep --newuse --usepkg=n --getbinpkg=n @world || echo ">> @world source rebuild failed" >&2
fi
unset FEATURES
if command -v cpuid2cpuflags >/dev/null 2>&1 || emerge --ask=n $NR app-portage/cpuid2cpuflags; then
  echo "*/* $(cpuid2cpuflags)" > /etc/portage/package.use/00cpu-flags || true
fi

echo ">> fstab"
if [ "$KEEP_EXISTING" = 1 ] && grep -q 'generated by gentoo-live-installer' /etc/fstab 2>/dev/null; then
  echo ">> skip existing /etc/fstab"
else
cat > /etc/fstab << 'FSTAB'
{fstab.strip()}
FSTAB
fi
{crypttab_block}

unset FEATURES
echo ">> fetch kernel and package sources"
emerge --ask=n --fetchonly --usepkg=n --getbinpkg=n --keep-going {fetch_line} || echo ">> some distfiles were not fetched; emerge will try again" >&2
echo ">> emerge kernel and selected packages"
# --keep-going: a kernel, Plasma, or Firefox failure must not skip sudo,
# GRUB, Plymouth, NetworkManager, chrony, rsyslog, cronie, or zram-init.
# Exit 132 (Illegal instruction) rebuilds the same atoms from those distfiles.
emerge_with_source {kernel_atom} {all_extra} || echo ">> one or more packages failed; repairing required tools" >&2
if ! ls /boot/vmlinuz* >/dev/null 2>&1; then
  echo ">> no kernel image in /boot; building sys-kernel/gentoo-kernel from source"
  emerge --ask=n --usepkg=n --getbinpkg=n sys-kernel/gentoo-kernel || echo ">> gentoo-kernel source build failed" >&2
fi
{repair_tools}
{stores_block}

{swapfile}
{zram_block}
{user_block}
{tpm_script}
{selinux_block}

{grub_install}
{plymouth_block}

if [ "{cfg.init_system}" = "openrc" ]; then
{textwrap.indent(nm_cleanup, "  ")}
{textwrap.indent(enable_openrc, "  ")}
  append_once /etc/rc.conf 'rc_parallel="YES"'
else
{textwrap.indent(enable_systemd, "  ")}
fi

if [ "{cfg.network_stack}" = "netifrc" ]; then
  echo 'config_{netifrc_iface}="dhcp"' > /etc/conf.d/net || true
  ln -s /etc/init.d/net.lo /etc/init.d/net.{netifrc_iface} || true
  rc-update add net.{netifrc_iface} default || true
fi

{final_checks}
echo "Chroot configuration complete."
"""


def _app_stores_script(cfg: InstallConfig) -> str:
    """Add Flathub and install Flatpak-distributed stores (Bazaar) after emerge."""
    atoms = set(cfg.extra_packages + cfg.window_managers + cfg.selected_packages)
    if cfg.flatpak:
        atoms.add("sys-apps/flatpak")
    want_flatpak = cfg.flatpak or any(
        a == "sys-apps/flatpak"
        or a.startswith("flatpak-app/")
        or a in {"gui-apps/bazaar"}
        for a in atoms
    )
    if not want_flatpak:
        return ""
    apps = [a.split("/", 1)[1] for a in sorted(atoms) if a.startswith("flatpak-app/")]
    if "gui-apps/bazaar" in atoms and "io.github.kolunmi.Bazaar" not in apps:
        apps.append("io.github.kolunmi.Bazaar")
    lines = [
        'echo ">> Flathub / Flatpak stores"',
        "if command -v flatpak >/dev/null 2>&1; then",
        "  flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || "
        "flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || true",
    ]
    for app in apps:
        q = shlex.quote(app)
        lines.append(f"  flatpak install -y --noninteractive flathub {q} || true")
    lines.append("fi")
    return "\n".join(lines) + "\n"


def _overlay_script(cfg: InstallConfig) -> str:
    """Chroot snippet: eselect-repository enable + sync + ~arch keywords per overlay."""
    overlays = catalog.overlays_required_by(
        cfg.extra_packages + cfg.window_managers + cfg.selected_packages,
        cfg.overlays,
    )
    if not overlays:
        return ""
    arch_kw = "~amd64" if cfg.architecture == "amd64" else "~arm64"
    lines = [
        "emerge --ask=n --noreplace app-eselect/eselect-repository dev-vcs/git || true",
        "mkdir -p /etc/portage/package.accept_keywords",
    ]
    for ov in overlays:
        qov = shlex.quote(ov)
        lines.append("unset FEATURES")
        lines.append(f"eselect repository enable {qov} || true")
        lines.append("unset FEATURES")
        lines.append(f"emaint sync --repo {qov} || true")
        lines.append(
            f"append_once /etc/portage/package.accept_keywords/overlays '*/*::{ov} {arch_kw}'"
        )
    return "\n".join(lines) + "\n"


def _plymouth_script(cfg: InstallConfig) -> str:
    """Set the default splash and add quiet splash to GRUB if GRUB was selected."""
    if not cfg.plymouth:
        return ""
    theme = cfg.plymouth_theme or "spinner"
    if theme in catalog.PLYMOUTH_THEME_PACKAGES:
        theme_name = "breeze" if theme == "breeze" else "dracula" if theme == "dracula" else theme
    else:
        theme_name = theme
    grub = ""
    if "grub" in selected_bootloaders(cfg):
        grub = """
mkdir -p /etc/default /boot/grub
if [ ! -f /etc/default/grub ]; then
  echo 'GRUB_CMDLINE_LINUX_DEFAULT=""' > /etc/default/grub
fi
sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"/' /etc/default/grub || true
"""
    # Theme must be selected before dracut packs the initramfs, and grub.cfg
    # must be regenerated after that initramfs exists.
    refresh = """
if command -v dracut >/dev/null 2>&1; then
  kver=$(ls -1 /lib/modules 2>/dev/null | tail -n1 || true)
  if [ -n "$kver" ]; then
    dracut --force --kver "$kver" || echo ">> dracut failed" >&2
  fi
fi
"""
    if grub:
        refresh += """
if command -v grub-mkconfig >/dev/null 2>&1; then
  grub-mkconfig -o /boot/grub/grub.cfg || echo ">> grub-mkconfig failed" >&2
else
  echo ">> grub-mkconfig does not exist" >&2
fi
"""
    return f"""
if ! command -v plymouth-set-default-theme >/dev/null 2>&1; then
  echo ">> installing sys-boot/plymouth"
  emerge --ask=n sys-boot/plymouth || emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/plymouth || echo ">> plymouth emerge failed" >&2
fi
if command -v plymouth-set-default-theme >/dev/null 2>&1; then
  plymouth-set-default-theme {shlex.quote(theme_name)} || echo ">> plymouth theme failed" >&2
else
  echo ">> plymouth-set-default-theme does not exist" >&2
fi
{grub}
{refresh}
"""


def _retry_sigill(atom: str, command: str) -> str:
    """Run a bootloader step. Exit 132 means the binary cannot run on this CPU."""
    quoted = shlex.quote(atom)
    body = command.strip()
    return (
        "{\n"
        + body
        + "\n}\n"
        + "boot_status=$?\n"
        + 'if [ "$boot_status" -eq 132 ]; then\n'
        + f'  echo ">> illegal instruction installing {atom}; rebuilding from source"\n'
        + "  disable_binpkgs\n"
        + f"  emerge --ask=n --usepkg=n --getbinpkg=n {quoted} || echo '>> source build failed: {atom}' >&2\n"
        + "  {\n"
        + body
        + "\n}\n"
        + "  boot_status=$?\n"
        + "fi\n"
        + 'if [ "$boot_status" -ne 0 ]; then\n'
        + f"  echo '>> bootloader step failed: {atom}' >&2\n"
        + "fi\n"
    )


def _install_bootloaders(cfg: InstallConfig, devices: dict[str, str], loaders: list[str]) -> str:
    """Chroot commands to install every selected bootloader (not only the primary)."""
    cmds: list[str] = []
    for bl in loaders:
        if bl == "grub":
            if cfg.firmware == "uefi":
                grub_target = "arm64-efi" if cfg.architecture == "arm64" else "x86_64-efi"
                cmds.append(_grub_uefi_install(grub_target))
            else:
                disk = shlex.quote(cfg.target_disk)
                cmds.append(
                    "\n".join(
                        [
                            "mkdir -p /boot/grub",
                            f"grub-install {disk}",
                            "grub_status=$?",
                            'if [ "$grub_status" -eq 132 ]; then',
                            '  echo ">> grub-install: illegal instruction; rebuilding sys-boot/grub from source"',
                            "  disable_binpkgs",
                            "  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || echo '>> grub source build failed' >&2",
                            f"  grub-install {disk}",
                            "  grub_status=$?",
                            "fi",
                            'if [ "$grub_status" -ne 0 ]; then echo ">> grub-install failed" >&2; fi',
                            "grub-mkconfig -o /boot/grub/grub.cfg || echo '>> grub-mkconfig failed' >&2",
                        ]
                    )
                )
            if cfg.grub_password:
                cmds.append(
                    """
if [ -f /root/.install-secrets ]; then
  . /root/.install-secrets
  HASH=$(printf '%s\\n%s\\n' "$GRUB_PW" "$GRUB_PW" | grub-mkpasswd-pbkdf2 | awk '/grub.pbkdf2/ {print $NF}')
  cat > /etc/grub.d/01_password << EOF
#!/bin/sh
echo 'set superusers="root"'
echo "password_pbkdf2 root $HASH"
EOF
  chmod 700 /etc/grub.d/01_password
  mkdir -p /boot/grub
  grub-mkconfig -o /boot/grub/grub.cfg || echo ">> grub-mkconfig failed" >&2
fi
"""
                )
        elif bl == "systemd-boot":
            atom = "sys-apps/systemd" if cfg.init_system == "systemd" else "sys-apps/systemd-utils"
            cmds.append(_retry_sigill(atom, "bootctl install"))
        elif bl == "refind":
            cmds.append(_retry_sigill("sys-boot/refind", "refind-install"))
        elif bl == "syslinux":
            boot = devices.get("boot") or devices.get("esp") or ""
            cmds.append(_retry_sigill("sys-boot/syslinux", f"syslinux-install_update -iam || syslinux {boot}"))
        elif bl == "efistub":
            cmds.append("echo 'EFI stub/UKI selected — kernel installkernel handles the EFI entry' ")
        elif bl == "limine":
            cmds.append(_retry_sigill("sys-boot/limine", _limine_install(cfg, devices).strip()))
        elif bl == "lilo":
            cmds.append(_retry_sigill("sys-boot/lilo", "lilo"))
    return "\n".join(cmds)


def _desktop_packages(cfg: InstallConfig) -> list[str]:
    """Meta packages for the DE chosen in chapter 11 (WMs from chapter 12 are separate)."""
    pkgs: list[str] = []
    mapping = {
        "xfce": ["xfce-base/xfce4-meta", "x11-misc/lightdm"],
        "plasma": ["kde-plasma/plasma-meta", "x11-misc/sddm"],
        "gnome": ["gnome-base/gnome", "gnome-base/gdm"],
        "mate": ["mate-base/mate", "x11-misc/lightdm"],
        "cinnamon": ["gnome-extra/cinnamon", "x11-misc/lightdm"],
        "i3": ["x11-wm/i3", "x11-misc/i3status", "x11-misc/lightdm", "x11-terms/xterm"],
    }
    pkgs.extend(mapping.get(cfg.desktop, []))
    if cfg.pipewire and cfg.desktop != "none":
        pkgs.extend(["media-video/pipewire", "media-video/wireplumber"])
    if cfg.browser == "firefox-bin":
        pkgs.append("www-client/firefox-bin")
    elif cfg.browser == "firefox":
        pkgs.append("www-client/firefox")
    elif cfg.browser == "chromium":
        pkgs.append("www-client/chromium")
    if cfg.kde_games:
        pkgs.extend(["kde-apps/kpat", "kde-apps/kmines", "kde-apps/kbounce"])
    if cfg.flatpak:
        pkgs.append("sys-apps/flatpak")
    return pkgs


def _crypttab(cfg: InstallConfig, devices: dict[str, str]) -> str:
    """Unlock map for dracut/cryptsetup. Swap uses the dedicated LUKS volume when present."""
    if not cfg.luks:
        return ""
    lines = ["# /etc/crypttab: generated by gentoo-live-installer"]
    if devices.get("luks_source"):
        lines.append(f"root {devices['luks_source']} none luks")
    swap_src = devices.get("luks_swap_source", "")
    if swap_src:
        lines.append(f"swap {swap_src} none luks,swap")
    return "\n".join(lines) + "\n"


def _limine_install(cfg: InstallConfig, devices: dict[str, str]) -> str:
    """UEFI: copy BOOT*.EFI to the ESP fallback path + optional NVRAM entry.

    BIOS: `limine bios-install` on the target disk.
    Kernel/initramfs names come from dist-kernel; upgrades still need limine.conf
    refreshed (disclaimer in the wizard).
    """
    root_dev = "/dev/mapper/root" if cfg.luks else devices.get("root", "")
    cmdline = f"root={root_dev} rw"
    if cfg.luks:
        cmdline += " rd.luks.crypttab=1"
    conf = textwrap.dedent(
        f"""
        timeout: 5
        /Gentoo
            protocol: linux
            path: boot():/vmlinuz
            cmdline: {cmdline}
            module_path: boot():/initramfs
        """
    ).strip()
    if cfg.firmware != "uefi":
        return f"""
limine bios-install {shlex.quote(cfg.target_disk)}
mkdir -p /boot
cat > /boot/limine.conf << 'LIMINE'
{conf}
LIMINE
KERNEL=$(ls -1 /boot/vmlinuz-* 2>/dev/null | tail -n1 || true)
INITRD=$(ls -1 /boot/initramfs* /boot/initrd* 2>/dev/null | tail -n1 || true)
[ -n "$KERNEL" ] && ln -sfn "$(basename "$KERNEL")" /boot/vmlinuz
[ -n "$INITRD" ] && ln -sfn "$(basename "$INITRD")" /boot/initramfs
"""
    efi_bin = "BOOTAA64.EFI" if cfg.architecture == "arm64" else "BOOTX64.EFI"
    esp = devices.get("esp") or devices.get("boot") or ""
    disk = detect.parent_block_device(esp) if esp else cfg.target_disk
    part = detect.partition_number(esp) if esp else 1
    loader = "\\EFI\\BOOT\\" + efi_bin
    return f"""
mkdir -p /boot/EFI/BOOT /boot/EFI/limine
cp -v /usr/share/limine/{efi_bin} /boot/EFI/BOOT/{efi_bin}
cp -v /usr/share/limine/{efi_bin} /boot/EFI/limine/{efi_bin} || true
cat > /boot/limine.conf << 'LIMINE'
{conf}
LIMINE
cp /boot/limine.conf /boot/EFI/BOOT/limine.conf || true
KERNEL=$(ls -1 /boot/vmlinuz-* 2>/dev/null | tail -n1 || true)
INITRD=$(ls -1 /boot/initramfs* /boot/initrd* 2>/dev/null | tail -n1 || true)
[ -n "$KERNEL" ] && ln -sfn "$(basename "$KERNEL")" /boot/vmlinuz
[ -n "$INITRD" ] && ln -sfn "$(basename "$INITRD")" /boot/initramfs
if command -v efibootmgr >/dev/null 2>&1; then
  efibootmgr --create --disk {shlex.quote(disk)} --part {part} --label "Gentoo Limine" --loader '{loader}' --unicode || true
fi
"""


def _fstab(cfg: InstallConfig, devices: dict[str, str]) -> str:
    """Generate /etc/fstab text (UUIDs are not resolved here; device nodes from this install)."""
    lines = ["# /etc/fstab: generated by gentoo-live-installer"]
    root = devices.get("root", "")
    fs = "ext4" if cfg.filesystem == "ext4" else cfg.filesystem
    if cfg.layout == "btrfs-subvols":
        lines.append(f"{root} / btrfs noatime,compress=zstd:3,subvol=@ 0 1")
        lines.append(f"{root} /home btrfs noatime,compress=zstd:3,subvol=@home 0 2")
        lines.append(f"{root} /var/log btrfs noatime,compress=zstd:3,subvol=@log 0 2")
        lines.append(f"{root} /var/cache btrfs noatime,compress=zstd:3,subvol=@cache 0 2")
        lines.append(f"{root} /tmp btrfs noatime,subvol=@tmp 0 2")
        lines.append(f"{root} /swap btrfs noatime,nodatacow,subvol=@swap 0 0")
    elif cfg.filesystem != "zfs":
        lines.append(f"{root} / {fs} noatime 0 1")
    if cfg.firmware == "uefi" and devices.get("esp"):
        lines.append(f"{devices['esp']} /boot vfat noatime 0 2")
    elif devices.get("boot") and devices["boot"] != devices.get("esp"):
        lines.append(f"{devices['boot']} /boot ext4 noatime 0 2")
    if devices.get("swap") and cfg.swap_mode in {"partition", "both"}:
        lines.append(f"{devices['swap']} none swap sw 0 0")
    lines.append("proc /proc proc defaults 0 0")
    return "\n".join(lines) + "\n"
