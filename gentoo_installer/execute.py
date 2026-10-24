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
from .estimate import estimate_minutes, format_duration
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
    minutes = estimate_minutes(cfg)
    ui.info("Go outside for some fresh air and walk around a bit. This may take a while.")
    ui.ok(f"Estimated install time: {format_duration(minutes)}.")
    gallop = None if cfg.dry_run else ui.InstallGallop(minutes)
    if gallop is not None:
        gallop.start()
    try:
        _run_phases(cfg, runner, state, phases, root, rerun_on_resume)
    finally:
        if gallop is not None:
            if "done" in state.done:
                gallop.finish()
            else:
                gallop.stop()


def _run_phases(cfg: InstallConfig, runner: Runner, state: State, phases, root: str, rerun_on_resume: set[str]) -> None:
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


def _root_fstype(cfg: InstallConfig) -> str:
    return {"ext4": "ext4", "xfs": "xfs", "btrfs": "btrfs", "f2fs": "f2fs", "jfs": "jfs"}.get(
        cfg.filesystem, "ext4"
    )


def _release_disk_mounts(runner: Runner, disk: str) -> None:
    """Unmount live-desktop automounts of this disk so mkfs and mount can use it.

    Ubuntu XFCE mounts a Windows volume as soon as it sees one. Leaving that
    mount up makes the later Gentoo mount look at a busy or half-wiped NTFS.
    """
    if not disk or not disk.startswith("/dev/"):
        return
    q = shlex.quote(disk)
    runner.shell(
        "\n".join(
            [
                f"for part in $(lsblk -ln -o PATH {q} 2>/dev/null || true); do",
                '  findmnt -rn -o TARGET -S "$part" 2>/dev/null | while IFS= read -r mnt; do',
                '    umount "$mnt" || umount -l "$mnt" || true',
                "  done",
                "done",
                "udevadm settle || true",
            ]
        )
    )


def _mount_if_needed(
    runner: Runner, source: str, dest: str, options: str = "", fstype: str = ""
) -> None:
    """Mount dest if it is not already a mountpoint (resume-safe).

    The type has to be explicit. A typeless mount on Ubuntu asks the in-kernel
    ntfs3 driver first, and that driver fails the whole call with
    "NTFS signature is missing". Btrfs is the case that always hits this:
    mkfs.btrfs writes its magic at 64KiB, so the first sector is not a
    filesystem signature and ntfs3 aborts the mount before subvolumes exist.
    """
    allowed = {"ext4", "vfat", "btrfs", "xfs", "f2fs", "jfs"}
    if fstype not in allowed:
        fstype = "ext4"
    runner.shell(f"mkdir -p {shlex.quote(dest)}")
    src = shlex.quote(source)
    dst = shlex.quote(dest)
    opt = f"-o {shlex.quote(options)} " if options else ""
    runner.shell(
        "\n".join(
            [
                f"if ! mountpoint -q {dst}; then",
                f"  findmnt -rn -o TARGET -S {src} 2>/dev/null | while IFS= read -r other; do",
                f'    [ "$other" = {dst} ] && continue',
                '    umount "$other" || umount -l "$other" || true',
                "  done",
                "  for _try in 1 2 3 4 5 6 7 8 9 10; do",
                f"    [ -b {src} ] && break",
                "    udevadm settle || true",
                "    sleep 0.3",
                "  done",
                f'  if [ ! -b {src} ]; then echo ">> block device {source} is missing" >&2; exit 1; fi',
                f"  mount -t {fstype} {opt}{src} {dst}",
                "fi",
            ]
        )
    )


def _unmount_target(cfg: InstallConfig, runner: Runner) -> None:
    """Drop mounts/swap/LUKS so wipefs can run. Safe if nothing is mounted."""
    root = cfg.mountpoint
    ui.info(f"Unmounting {root} if it is busy from a previous run")
    _release_disk_mounts(runner, cfg.target_disk)
    if cfg.extra_raid_disk:
        _release_disk_mounts(runner, cfg.extra_raid_disk)
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
                _mount_if_needed(runner, devices["esp"], boot, fstype="vfat")
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
        runner.shell("udevadm settle || true")
        runner.shell("sleep 1 || true")
        _release_disk_mounts(runner, disk)

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
        _mount_if_needed(runner, part_name(disk, 1), cfg.mountpoint + "/boot", fstype="vfat")


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
    for node in (devices.get("esp"), devices.get("boot"), devices.get("swap"), devices.get("root")):
        if not node or not str(node).startswith("/dev/"):
            continue
        quoted = shlex.quote(node)
        runner.shell(
            "findmnt -rn -o TARGET -S "
            + quoted
            + ' 2>/dev/null | while IFS= read -r mnt; do umount "$mnt" || umount -l "$mnt" || true; done'
        )
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
        # A previous Windows volume leaves an NTFS boot sector at the start of
        # this partition. wipefs on the whole disk does not reach it, and
        # mkfs.btrfs does not put its own magic there.
        runner.shell(f"wipefs -af {shlex.quote(root)} || true")
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
    if cfg.layout == "btrfs-subvols":
        if create_subvols and not _mountpoint_q(root):
            ui.info("Creating Btrfs subvolumes @ @home @log @cache @tmp @swap")
            _mount_if_needed(runner, devices["root"], root, fstype="btrfs")
            quoted_root = shlex.quote(root)
            for sub in ("@", "@home", "@log", "@cache", "@tmp", "@swap"):
                runner.shell(
                    f"btrfs subvolume list {quoted_root} | grep -q 'path {sub}$' "
                    f"|| btrfs subvolume create {shlex.quote(root + '/' + sub)}"
                )
            runner.shell(f"btrfs subvolume set-default {shlex.quote(root + '/@')}")
            runner.shell(
                f"umount {quoted_root} || umount -l {quoted_root}\n"
                f"if mountpoint -q {quoted_root}; then echo '>> {root} stayed mounted; cannot switch to subvol=@' >&2; exit 1; fi"
            )
        opts = "noatime,compress=zstd:3"
        _mount_if_needed(runner, devices["root"], root, f"{opts},subvol=@", fstype="btrfs")
        for sub, mp in (("@home", "home"), ("@log", "var/log"), ("@cache", "var/cache"), ("@tmp", "tmp"), ("@swap", "swap")):
            extra = "noatime,nodatacow" if sub == "@swap" else opts
            _mount_if_needed(runner, devices["root"], root + "/" + mp, f"{extra},subvol={sub}", fstype="btrfs")
    else:
        _mount_if_needed(runner, devices["root"], root, fstype=_root_fstype(cfg))
    if cfg.firmware == "uefi" and devices.get("esp"):
        runner.shell(f"mkdir -p {shlex.quote(root + '/efi')} {shlex.quote(root + '/boot')}")
        _mount_if_needed(runner, devices["esp"], root + "/boot", fstype="vfat")
    elif devices.get("boot") and devices["boot"] != devices.get("esp") and devices["boot"] != devices.get("root"):
        _mount_if_needed(runner, devices["boot"], root + "/boot", fstype="ext4")


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
    _write_text(use_dir / "installer", _package_use_text(cfg), keep_existing=False)
    keywords = _keyword_text(cfg)
    if keywords.strip():
        kw_dir = portage / "package.accept_keywords"
        kw_dir.mkdir(parents=True, exist_ok=True)
        _write_text(kw_dir / "installer", keywords, keep_existing=False)


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
    locale_conf = etc / "locale.conf"
    if locale_conf.is_dir() and not keep:
        shutil.rmtree(locale_conf)
    if not locale_conf.is_dir():
        _write_text(locale_conf, f'LANG="{loc}"\n', keep_existing=keep)
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


def _openrc_enable_line(service: str, runlevel: str = "default") -> str:
    """rc-update only after the init script exists, so a missing package is obvious."""
    return (
        f'if [ -e /etc/init.d/{service} ]; then rc-update add {service} {runlevel}; '
        f'else echo ">> OpenRC service {service} does not exist" >&2; fi'
    )


def _systemd_enable_line(service: str) -> str:
    test = _systemd_unit_test(service)
    return (
        f"if {test}; then systemctl enable {service}; "
        f'else echo ">> systemd unit {service}.service does not exist" >&2; fi'
    )


def _service_probe(openrc: bool, init_name: str, unit: str | None = None) -> tuple[str, ...]:
    """Argv that is true when the OpenRC script or systemd unit is installed."""
    if openrc:
        return ("test", "-e", f"/etc/init.d/{init_name}")
    unit_name = unit or init_name
    return (
        "bash",
        "-c",
        "test -e /usr/lib/systemd/system/"
        + unit_name
        + ".service || test -e /lib/systemd/system/"
        + unit_name
        + ".service",
    )


def _emerge_checked_call(atom: str, test: tuple[str, ...]) -> str:
    args = " ".join(shlex.quote(part) for part in (atom, *test))
    return f"emerge_checked {args} || true"


def _required_tools(cfg: InstallConfig, loaders: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """Packages that must be installed before dracut and rc-update run.

    Dracut's host scan calls `btrfs` whenever any mounted filesystem is btrfs
    (the target root, or the live USB's own mounts, visible through the
    bind-mounted /proc). Plymouth's default gtk USE fails to compile against
    gtk without X. OpenRC init scripts are omitted from systemd binpkgs.
    """
    tools: list[tuple[str, tuple[str, ...]]] = []

    def add(atom: str, *test: str) -> None:
        tools.append((atom, test))

    openrc = cfg.init_system == "openrc"
    if cfg.initramfs == "dracut":
        # Always, even on ext4: dracut-functions.sh btrfs_devs() runs for every
        # btrfs mount it can see, and installkernel runs dracut from the kernel ebuild.
        add("sys-fs/btrfs-progs", "command", "-v", "btrfs")
        add("sys-apps/kbd", "command", "-v", "loadkeys")
    fs_cmds = {
        "ext4": ("mkfs.ext4", "sys-fs/e2fsprogs"),
        "xfs": ("mkfs.xfs", "sys-fs/xfsprogs"),
        "btrfs": ("mkfs.btrfs", "sys-fs/btrfs-progs"),
        "f2fs": ("mkfs.f2fs", "sys-fs/f2fs-tools"),
        "jfs": ("mkfs.jfs", "sys-fs/jfsutils"),
        "zfs": ("zfs", "sys-fs/zfs"),
    }
    fs_cmd, fs_atom = fs_cmds.get(cfg.filesystem, ("mkfs.ext4", "sys-fs/e2fsprogs"))
    if fs_atom != "sys-fs/btrfs-progs":
        add(fs_atom, "command", "-v", fs_cmd)
    if cfg.firmware == "uefi":
        add("sys-fs/dosfstools", "command", "-v", "mkfs.vfat")
    add("app-admin/sudo", "command", "-v", "sudo")
    if "grub" in loaders:
        add("sys-boot/grub", "bash", "-c", "command -v grub-install >/dev/null 2>&1 && command -v grub-mkconfig >/dev/null 2>&1")
        if cfg.firmware == "uefi":
            add("sys-boot/efibootmgr", "command", "-v", "efibootmgr")
    if "systemd-boot" in loaders:
        boot_atom = "sys-apps/systemd" if cfg.init_system == "systemd" else "sys-apps/systemd-utils"
        add(boot_atom, "command", "-v", "bootctl")
    if "refind" in loaders:
        add("sys-boot/refind", "command", "-v", "refind-install")
    if "syslinux" in loaders:
        add("sys-boot/syslinux", "command", "-v", "syslinux")
    if "limine" in loaders:
        add("sys-boot/limine", "command", "-v", "limine")
    if "lilo" in loaders:
        add("sys-boot/lilo", "command", "-v", "lilo")
    if cfg.initramfs == "dracut":
        add("sys-kernel/dracut", "command", "-v", "dracut")
        add(
            "sys-kernel/installkernel",
            "bash",
            "-c",
            "command -v installkernel >/dev/null 2>&1 || test -d /usr/lib/kernel/install.d",
        )
    if cfg.luks:
        add("sys-fs/cryptsetup", "command", "-v", "cryptsetup")
    if cfg.plymouth:
        add("sys-boot/plymouth", "command", "-v", "plymouth-set-default-theme")
    if cfg.wireless:
        add("net-wireless/iw", "command", "-v", "iw")
        add("net-wireless/wpa_supplicant", "command", "-v", "wpa_supplicant")
    if cfg.network_stack == "networkmanager":
        if openrc:
            add("sys-apps/dbus", *_service_probe(True, "dbus"))
            add("sys-auth/elogind", *_service_probe(True, "elogind"))
        add("net-misc/networkmanager", *_service_probe(openrc, "NetworkManager"))
    elif cfg.network_stack == "dhcpcd":
        add("net-misc/dhcpcd", *_service_probe(openrc, "dhcpcd"))
    if cfg.sshd:
        add("net-misc/openssh", *_service_probe(openrc, "sshd"))
    if cfg.chrony:
        add("net-misc/chrony", *_service_probe(openrc, "chronyd"))
    if cfg.cups:
        add("net-print/cups", *_service_probe(openrc, "cupsd", "cups"))
    if openrc and cfg.syslog != "none":
        add(f"app-admin/{cfg.syslog}", *_service_probe(True, cfg.syslog))
    if openrc and cfg.cron != "none":
        svc = "cronie" if cfg.cron == "cronie" else cfg.cron
        add(f"sys-process/{cfg.cron}", *_service_probe(True, svc))
    if cfg.zram:
        if openrc:
            add("sys-block/zram-init", *_service_probe(True, "zram-init"))
        else:
            add(
                "sys-apps/zram-generator",
                "bash",
                "-c",
                "test -e /usr/lib/systemd/system-generators/zram-generator || test -e /lib/systemd/system-generators/zram-generator",
            )
    return tools


def _required_tool_repairs(cfg: InstallConfig, loaders: list[str]) -> str:
    """Install boot and service packages one failure at a time, before the kernel.

    A masked theme or a Plymouth build error used to sit in the same emerge
    as NetworkManager, chrony, rsyslog, cronie, and zram-init. Portage then
    aborted the whole calculation and rc-update reported every service missing.
    """
    tools = _required_tools(cfg, loaders)
    lines = ["echo '>> install tools before dracut and OpenRC services'"]
    if tools:
        atoms = " ".join(shlex.quote(atom) for atom, _test in tools)
        lines.append(
            "emerge --ask=n --keep-going $RESPECT_USE $NR "
            + atoms
            + " || echo '>> some required packages failed; retrying one at a time' >&2"
        )
    for atom, test in tools:
        lines.append(_emerge_checked_call(atom, test))
    openrc = cfg.init_system == "openrc"
    if cfg.network_stack == "networkmanager" and openrc:
        lines.append(
            "\n".join(
                [
                    "if ! test -e /etc/init.d/NetworkManager; then",
                    '  echo ">> NetworkManager init script still missing; installing dbus and elogind first"',
                    "  emerge --ask=n $RESPECT_USE sys-apps/dbus || true",
                    "  emerge --ask=n $RESPECT_USE sys-auth/elogind || true",
                    "  emerge --ask=n --selective=n --usepkg=n --getbinpkg=n net-misc/networkmanager || echo '>> emerge failed: net-misc/networkmanager' >&2",
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
        checks.append(
            ("grep -q '^size0=\"[0-9][0-9]*\"' /etc/conf.d/zram-init", "zram-init size0")
        )
    elif cfg.zram:
        checks.append(("[ -s /etc/systemd/zram-generator.conf ]", "zram-generator.conf"))
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

# Stage3 Portage may not know --binpkg-respect-use. Leaving it in
# EMERGE_DEFAULT_OPTS makes every later emerge exit before it builds anything.
RESPECT_USE=""
portage_prepare() {
  emerge --help >/tmp/emerge-help.txt 2>/tmp/emerge-help.err || true
  if grep -q 'binpkg-respect-use' /tmp/emerge-help.err 2>/dev/null; then
    echo ">> this Portage rejects --binpkg-respect-use; removing it so emerge can run"
    local conf
    for conf in /etc/portage/make.conf /etc/portage/make.conf/*; do
      [ -f "$conf" ] || continue
      sed -i -E 's/ --binpkg-respect-use=[^" ]*//g' "$conf" || true
    done
    emerge --help >/tmp/emerge-help.txt 2>/tmp/emerge-help.err || true
  fi
  if grep -q 'binpkg-respect-use' /tmp/emerge-help.txt 2>/dev/null; then
    RESPECT_USE="--binpkg-respect-use=y"
  fi
  rm -f /tmp/emerge-help.txt /tmp/emerge-help.err
}
portage_prepare

# One atom. A batch emerge treats one masked or missing ebuild as a failed
# calculation and installs nothing else in that command.
emerge_one() {
  local atom="$1"
  [ -n "$atom" ] || return 0
  echo ">> emerge $atom"
  emerge --ask=n --keep-going $RESPECT_USE $NR "$atom"
  local status=$?
  if [ "$status" -eq 0 ]; then
    return 0
  fi
  if [ "$status" -eq 132 ]; then
    echo ">> emerge died with Illegal instruction; rebuilding $atom from source" >&2
    disable_binpkgs
  else
    echo ">> emerge failed ($status): $atom; building this package from source" >&2
  fi
  emerge --ask=n --fetchonly --usepkg=n --getbinpkg=n "$atom" || echo ">> distfile not fetched: $atom" >&2
  if emerge --ask=n --keep-going --usepkg=n --getbinpkg=n "$atom"; then
    return 0
  fi
  echo ">> emerge failed: $atom" >&2
  return 1
}

emerge_with_source() {
  local atom failed=0
  for atom in "$@"; do
    emerge_one "$atom" || failed=1
  done
  return "$failed"
}

# emerge_checked ATOM TEST...
# Install ATOM, then rebuild from source if TEST still fails. A binpkg built
# with the wrong USE (systemd on an OpenRC machine, gtk Plymouth) can be
# recorded as installed and still omit the init script or plymouth-set-default-theme.
# --selective=n reinstalls that already-recorded package.
emerge_checked() {
  local atom="$1"
  shift
  if "$@"; then
    echo ">> $atom is installed"
    return 0
  fi
  echo ">> installing $atom"
  emerge --ask=n --keep-going $RESPECT_USE "$atom"
  local st=$?
  if [ "$st" -eq 132 ]; then
    disable_binpkgs
    emerge --ask=n --keep-going --usepkg=n --getbinpkg=n "$atom" || true
  fi
  if "$@"; then
    return 0
  fi
  echo ">> $atom still missing; building from source"
  emerge --ask=n --usepkg=n --getbinpkg=n --newuse "$atom" || true
  if "$@"; then
    return 0
  fi
  echo ">> forcing source rebuild of $atom"
  emerge --ask=n --selective=n --usepkg=n --getbinpkg=n "$atom" || echo ">> emerge failed: $atom" >&2
  if "$@"; then
    return 0
  fi
  echo ">> still missing after emerge: $atom" >&2
  return 1
}

# Set the theme the wizard recorded. Never substitute spinner.
# breeze-plymouth also ships breeze-text; use that only when breeze is absent.
apply_plymouth_theme() {
  local want="$1"
  local listed resolved="" current=""
  [ -n "$want" ] || return 0
  if ! command -v plymouth-set-default-theme >/dev/null 2>&1; then
    echo ">> plymouth-set-default-theme does not exist" >&2
    return 1
  fi
  listed=$(plymouth-set-default-theme --list 2>/dev/null || true)
  if printf '%s\\n' "$listed" | grep -qx -- "$want"; then
    resolved=$want
  elif printf '%s\\n' "$listed" | grep -qx -- "${want}-text"; then
    resolved="${want}-text"
  elif resolved=$(printf '%s\\n' "$listed" | grep -m1 -- "^${want}" || true); [ -n "$resolved" ]; then
    true
  fi
  if [ -z "$resolved" ]; then
    echo ">> Plymouth theme '$want' is not installed; the splash was not changed" >&2
    echo ">> themes on disk:" >&2
    printf '%s\\n' "$listed" >&2
    return 1
  fi
  plymouth-set-default-theme "$resolved" || {
    echo ">> plymouth-set-default-theme $resolved failed" >&2
    return 1
  }
  current=$(plymouth-set-default-theme 2>/dev/null | head -n1 | tr -d '[:space:]')
  if [ -n "$current" ] && [ "$current" != "$resolved" ]; then
    echo ">> Plymouth reports '$current' after setting '$resolved'" >&2
    return 1
  fi
  echo ">> Plymouth theme is ${current:-$resolved}"
}

rebuild_initramfs() {
  local dir kver
  if ! command -v dracut >/dev/null 2>&1; then
    return 0
  fi
  for dir in /lib/modules/*; do
    [ -d "$dir" ] || continue
    kver=$(basename "$dir")
    dracut --force --kver "$kver" || echo ">> dracut failed for $kver" >&2
  done
}
"""


def _chosen_plymouth_themes(cfg: InstallConfig) -> list[str]:
    """Theme ids the user checked. An empty string is not a request for spinner."""
    themes = [theme for theme in cfg.plymouth_themes if theme]
    if not themes and cfg.plymouth_theme:
        themes = [cfg.plymouth_theme]
    return themes


def _emerge_each_block(title: str, atoms: list[str]) -> str:
    """Emerge each atom alone so one masked ebuild cannot skip the rest."""
    clean = [atom for atom in dict.fromkeys(atoms) if atom]
    if not clean:
        return ""
    lines = [f'echo ">> {title}"']
    for atom in clean:
        lines.append(f"emerge_one {shlex.quote(atom)} || true")
    return "\n".join(lines) + "\n"


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


def _graphical_atoms(cfg: InstallConfig) -> list[str]:
    return list(dict.fromkeys(cfg.extra_packages + _window_managers(cfg) + cfg.selected_packages))


def _resolved_dm(cfg: InstallConfig) -> str:
    return catalog.resolved_display_manager(cfg.display_manager, cfg.desktop, _graphical_atoms(cfg))


def _greetd_uses_tuigreet(cfg: InstallConfig) -> bool:
    """tuigreet is a GURU package. Use it only when a saved profile already names it."""
    atoms = set(_graphical_atoms(cfg))
    return (
        "gui-apps/tuigreet" in atoms
        and "gui-apps/gtkgreet" not in atoms
        and _atom_allowed(cfg, "gui-apps/tuigreet")
    )


def _display_manager_tools(cfg: InstallConfig) -> list[tuple[str, tuple[str, ...]]]:
    """Packages for the greeter that must exist before rc-update."""
    dm = _resolved_dm(cfg)
    if dm == "none":
        return []
    openrc = cfg.init_system == "openrc"
    tools: list[tuple[str, tuple[str, ...]]] = []
    if dm == "sddm":
        tools.append(("x11-misc/sddm", _service_probe(openrc, "sddm")))
    elif dm == "gdm":
        tools.append(("gnome-base/gdm", _service_probe(openrc, "gdm")))
    elif dm == "lightdm":
        tools.append(("x11-misc/lightdm", _service_probe(openrc, "lightdm")))
        tools.append(("x11-misc/lightdm-gtk-greeter", ("command", "-v", "lightdm-gtk-greeter")))
    elif dm == "greetd":
        tools.append(("gui-libs/greetd", _service_probe(openrc, "greetd")))
        if _greetd_uses_tuigreet(cfg):
            tools.append(("gui-apps/tuigreet", ("command", "-v", "tuigreet")))
        else:
            tools.append(("gui-apps/gtkgreet", ("command", "-v", "gtkgreet")))
            tools.append(("gui-wm/labwc", ("command", "-v", "labwc")))
    elif dm == "ly":
        tools.append(("x11-misc/ly", _service_probe(openrc, "ly")))
    elif dm == "slim":
        tools.append(("x11-misc/slim", _service_probe(openrc, "slim")))
    if openrc:
        tools.append(("sys-apps/dbus", _service_probe(True, "dbus")))
        tools.append(("sys-auth/elogind", _service_probe(True, "elogind")))
    return tools


def _session_desktop(title: str, command: str, kind: str) -> str:
    lines = [
        "[Desktop Entry]",
        f"Name={title}",
        "Comment=Session installed by gentoo-live-installer",
        f"Exec={command}",
        "Type=Application",
    ]
    if kind == "wayland":
        lines.append("X-LightDM-Session-Type=wayland")
    lines.append("")
    return "\n".join(lines)


def _display_manager_script(cfg: InstallConfig) -> str:
    """Install the greeter, register every selected session, and leave it enabled.

    The greeter is the screen after GRUB. It does not skip the password.
    """
    dm = _resolved_dm(cfg)
    if dm == "none":
        return ""
    lines = [f'echo ">> login manager: {dm}"']
    for atom, test in _display_manager_tools(cfg):
        lines.append(_emerge_checked_call(atom, test))
    lines.append("mkdir -p /usr/share/wayland-sessions /usr/share/xsessions")
    for title, command, kind in catalog.login_sessions(
        cfg.desktop, _graphical_atoms(cfg), getattr(cfg, "shell", "") or ""
    ):
        folder = "/usr/share/wayland-sessions" if kind == "wayland" else "/usr/share/xsessions"
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower() or "session"
        path = f"{folder}/installer-{slug}.desktop"
        body = _session_desktop(title, command, kind)
        lines.append(f"if ! grep -RFqs {shlex.quote(command)} /usr/share/wayland-sessions /usr/share/xsessions 2>/dev/null; then")
        lines.append(f"cat > {shlex.quote(path)} << 'EOF'")
        lines.append(body.rstrip("\n"))
        lines.append("EOF")
        lines.append("fi")
    if dm == "lightdm":
        lines.extend(
            [
                "mkdir -p /etc/lightdm",
                "touch /etc/lightdm/lightdm.conf",
                "grep -q '^greeter-session=' /etc/lightdm/lightdm.conf || printf '\\n[Seat:*]\\ngreeter-session=lightdm-gtk-greeter\\n' >> /etc/lightdm/lightdm.conf",
            ]
        )
    elif dm == "greetd":
        if _greetd_uses_tuigreet(cfg):
            command = "/usr/bin/tuigreet --time --remember --remember-user-session --sessions /usr/share/wayland-sessions:/usr/share/xsessions"
        else:
            command = "labwc -s gtkgreet"
        lines.extend(
            [
                "mkdir -p /etc/greetd",
                'if [ "$KEEP_EXISTING" = 1 ] && [ -f /etc/greetd/config.toml ]; then',
                '  echo ">> skip existing greetd config"',
                "else",
                "cat > /etc/greetd/config.toml << 'EOF'",
                "[terminal]",
                "vt = 1",
                "",
                "[default_session]",
                f'command = "{command}"',
                'user = "greetd"',
                "EOF",
                "fi",
                "usermod -aG video,seat,input greetd || true",
            ]
        )
    elif dm == "sddm":
        lines.extend(
            [
                "mkdir -p /etc/sddm.conf.d",
                "cat > /etc/sddm.conf.d/installer.conf << 'EOF'",
                "[General]",
                "DisplayServer=x11",
                "EOF",
            ]
        )
    if cfg.username:
        lines.append(f"usermod -aG video,seat {shlex.quote(cfg.username)} || true")
    return "\n".join(lines) + "\n"


def _package_use_text(cfg: InstallConfig) -> str:
    """Per-package USE so OpenRC init scripts and Plymouth can actually be installed.

    Plymouth's default gtk USE fails to compile when gtk+ is built without X
    (Gentoo bug 850400). Themes need drm and pango, not gtk.
    A systemd binpkg of NetworkManager, dbus, or Plymouth does not ship
    /etc/init.d, so OpenRC installs must not reuse that USE.
    """
    wifi = "wifi" if cfg.wireless else "-wifi"
    if cfg.init_system == "openrc":
        lines = [
            "sys-boot/plymouth drm pango udev -gtk -systemd",
            "sys-apps/dbus -systemd",
            f"net-misc/networkmanager elogind -systemd {wifi} tools",
            "sys-auth/polkit elogind -systemd",
        ]
    else:
        lines = [
            "sys-boot/plymouth drm pango udev systemd -gtk",
            "sys-apps/dbus systemd",
            f"net-misc/networkmanager systemd -elogind {wifi} tools",
        ]
    dm = catalog.resolved_display_manager(
        cfg.display_manager,
        cfg.desktop,
        _graphical_atoms(cfg),
    )
    if cfg.init_system == "openrc":
        if dm == "sddm":
            lines.append("x11-misc/sddm elogind -systemd")
        elif dm == "lightdm":
            lines.append("x11-misc/lightdm elogind -systemd")
        elif dm == "gdm":
            lines.append("gnome-base/gdm elogind -systemd")
    elif dm == "sddm":
        lines.append("x11-misc/sddm systemd -elogind")
    elif dm == "gdm":
        lines.append("gnome-base/gdm systemd -elogind")
    return "\n".join(lines) + "\n"


def _keyword_text(cfg: InstallConfig) -> str:
    """Unmask packages the wizard offers that are still ~arch."""
    if cfg.testing_keywords:
        return ""
    keyword = "~amd64" if cfg.architecture == "amd64" else "~arm64"
    lines = []
    if cfg.plymouth and cfg.plymouth_openrc_plugin and cfg.init_system == "openrc":
        # plymouth-openrc-plugin is keyworded ~arch. Leaving it masked aborts the
        # whole emerge calculation, so NetworkManager and chrony never install.
        lines.append(f"sys-boot/plymouth-openrc-plugin {keyword}")
    dm = catalog.resolved_display_manager(
        cfg.display_manager,
        cfg.desktop,
        _graphical_atoms(cfg),
    )
    if dm == "greetd":
        lines.append(f"gui-libs/greetd {keyword}")
        lines.append(f"gui-apps/gtkgreet {keyword}")
        if _greetd_uses_tuigreet(cfg):
            lines.append(f"gui-apps/tuigreet {keyword}")
    shell_atoms = catalog.shell_atoms(getattr(cfg, "shell", ""))
    for atom in shell_atoms:
        lines.append(f"{atom} {keyword}")
    queued = set(_graphical_atoms(cfg))
    for atom in catalog.TESTING_ATOMS:
        if atom in queued:
            lines.append(f"{atom} {keyword}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _dracut_conf_text(cfg: InstallConfig) -> str:
    """Dracut settings read by installkernel while the kernel package is merged.

    Without i18n_vars, dracut prints "i18n_vars not set! Please set up i18n_vars
    in configuration file." The keymap and locale files are the OpenRC ones this
    installer writes. btrfs-progs has to be installed before this runs; omitting
    the module on a non-btrfs target keeps the host's btrfs mounts out of the image.
    """
    lines = [
        'i18n_vars="/etc/conf.d/keymaps:keymap-KEYMAP /etc/locale.conf:LANG"',
        'i18n_default_font="default8x16"',
        'kbddir="/usr/share"',
    ]
    modules: list[str] = []
    omit: list[str] = []
    if cfg.luks:
        modules.extend(["crypt", "dm"])
    if cfg.filesystem == "btrfs" or cfg.layout == "btrfs-subvols":
        modules.append("btrfs")
    else:
        omit.append("btrfs")
    if cfg.plymouth:
        modules.append("plymouth")
    if modules:
        lines.append(f'add_dracutmodules+=" {" ".join(modules)} "')
    if omit:
        lines.append(f'omit_dracutmodules+=" {" ".join(omit)} "')
    return "\n".join(lines) + "\n"


def _safe_token(raw: str, fallback: str) -> str:
    text = (raw or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.@+-]+", text):
        return text
    return fallback


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
    theme_pkgs: list[str] = []
    if cfg.plymouth:
        boot_pkgs.append("sys-boot/plymouth")
        # Theme packages are emerged one at a time, before dracut runs.
        # A masked theme used to sit in the same emerge as every other package.
        for extra_theme in catalog.plymouth_packages_for(
            _chosen_plymouth_themes(cfg),
            cfg.plymouth_openrc_plugin and cfg.init_system == "openrc",
        ):
            theme_pkgs.append(extra_theme.atom)
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
    required_atoms = [atom for atom, _test in _required_tools(cfg, loaders)]
    required_set = set(required_atoms)
    # Anything the older package lists named that the checker does not probe
    # still has to be installed. Themes stay out of this list.
    missed = [
        atom
        for atom in dict.fromkeys(boot_pkgs + net_pkgs)
        if atom and atom not in required_set and atom not in theme_pkgs
    ]
    dm_atoms = [atom for atom, _test in _display_manager_tools(cfg)]
    support_atoms = _support_atoms(cfg)
    blocked = [
        atom
        for atom in dict.fromkeys(cfg.extra_packages + cfg.window_managers + cfg.selected_packages)
        if atom and not _atom_allowed(cfg, atom)
    ]
    if blocked:
        ui.warn("Not installing GURU packages: " + ", ".join(blocked))
    late_atoms = [
        atom
        for atom in dict.fromkeys(
            de_pkgs + support_atoms + cfg.extra_packages + _window_managers(cfg) + dm_atoms
        )
        if atom
        and _atom_allowed(cfg, atom)
        and not atom.startswith("flatpak-app/")
        and atom not in required_set
        and atom not in set(theme_pkgs)
    ]
    fetch_atoms_extra = list(dict.fromkeys(required_atoms + missed + extra_fw + theme_pkgs + late_atoms))
    all_extra = " ".join(fetch_atoms_extra)
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

    dm = _resolved_dm(cfg)

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
        if cfg.init_system == "openrc" and "dbus" not in openrc_services:
            openrc_services.append("dbus")
        openrc_services.append(dm)
        systemd_services.append(dm)

    enable_openrc = "\n".join(_openrc_enable_line(s) for s in openrc_services)
    enable_systemd = "\n".join(_systemd_enable_line(s) for s in systemd_services)
    boot_enable = ""
    if cfg.init_system == "openrc" and (cfg.network_stack == "networkmanager" or dm != "none"):
        boot_enable = _openrc_enable_line("elogind", "boot")
    rc_plymouth = ""
    if cfg.plymouth and cfg.init_system == "openrc":
        rc_plymouth = "append_once /etc/rc.conf 'rc_interactive=\"NO\"'"
    repair_tools = _required_tool_repairs(cfg, loaders)
    final_checks = _final_tool_checks(cfg, loaders)
    package_use = _package_use_text(cfg).strip()
    accept_keywords = _keyword_text(cfg).strip()
    dracut_conf = _dracut_conf_text(cfg).strip() if cfg.initramfs == "dracut" else ""
    locale_safe = _safe_token(cfg.locale, "en_US.UTF-8")
    keymap_safe = _safe_token(cfg.keymap, "us")
    keyword_block = ""
    if accept_keywords:
        keyword_block = f"""cat > /etc/portage/package.accept_keywords/installer << 'EOF'
{accept_keywords}
EOF
"""
    dracut_block = ""
    if dracut_conf:
        dracut_block = f"""mkdir -p /etc/dracut.conf.d /etc/conf.d
cat > /etc/dracut.conf.d/gentoo-live.conf << 'EOF'
{dracut_conf}
EOF
if [ ! -s /etc/locale.conf ]; then echo 'LANG="{locale_safe}"' > /etc/locale.conf; fi
if [ ! -s /etc/conf.d/keymaps ]; then echo 'keymap="{keymap_safe}"' > /etc/conf.d/keymaps; fi
"""
    firmware_block = _emerge_each_block("firmware", extra_fw)
    missed_block = _emerge_each_block("remaining service packages", missed)
    late_block = _emerge_each_block("desktop and extra packages", late_atoms)
    theme_block = _emerge_each_block("Plymouth theme packages", theme_pkgs)
    plymouth_early = ""
    if cfg.plymouth and cfg.plymouth_theme:
        plymouth_early = (
            'echo ">> set Plymouth theme before the initramfs is built"\n'
            f"apply_plymouth_theme {shlex.quote(cfg.plymouth_theme)} || "
            "echo '>> Plymouth theme was not applied yet' >&2\n"
        )
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
    session_extra = _user_session_script(cfg)

    zram_block = ""
    if cfg.zram:
        if cfg.init_system == "systemd":
            zram_block = """
echo ">> configure zram (half of RAM, zstd)"
mkdir -p /etc/modules-load.d /etc/systemd
echo zram > /etc/modules-load.d/zram.conf
cat > /etc/systemd/zram-generator.conf << 'EOF'
[zram0]
zram-size = ram / 2
compression-algorithm = zstd
swap-priority = 100
EOF
if ! grep -q 'zram-size' /etc/systemd/zram-generator.conf; then
  echo ">> zram-generator.conf was not written" >&2
fi
"""
        else:
            zram_block = """
echo ">> configure zram (half of RAM, zstd)"
mkdir -p /etc/modules-load.d
echo zram > /etc/modules-load.d/zram.conf
emerge_checked sys-block/zram-init test -e /etc/init.d/zram-init || true
zram_mib=$(awk '/MemTotal/ { m=int($2/2048); if (m<256) m=256; print m }' /proc/meminfo)
if [ -z "$zram_mib" ]; then zram_mib=2048; fi
if [ ! -e /etc/init.d/zram-init ]; then
  echo ">> writing a small OpenRC zram-init service"
  cat > /etc/init.d/zram-init << 'ZRAM'
#!/sbin/openrc-run
description="Compressed RAM swap"
start() {
  modprobe zram || return 1
  if [ ! -e /sys/block/zram0 ]; then
    echo 1 > /sys/class/zram-control/hot_add || return 1
  fi
  local mib bytes
  mib=$(awk '/MemTotal/ { m=int($2/2048); if (m<256) m=256; print m }' /proc/meminfo)
  bytes=$((mib * 1024 * 1024))
  echo zstd > /sys/block/zram0/comp_algorithm 2>/dev/null || true
  echo "$bytes" > /sys/block/zram0/disksize || return 1
  mkswap /dev/zram0 || return 1
  swapon -p 100 /dev/zram0
}
stop() {
  swapoff /dev/zram0 || true
  echo 1 > /sys/block/zram0/reset 2>/dev/null || true
}
ZRAM
  chmod 755 /etc/init.d/zram-init
fi
cat > /etc/conf.d/zram-init << EOF
load_on_start="yes"
unload_on_stop="yes"
num_devices="1"
type0="swap"
size0="${zram_mib}"
algo0="zstd"
pri0="100"
EOF
echo ">> zram-init size0=${zram_mib} MiB"
if ! grep -q "^size0=\\\"${zram_mib}\\\"" /etc/conf.d/zram-init; then
  echo ">> zram-init config is missing size0" >&2
fi
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
    dm_setup = _display_manager_script(cfg)
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
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"
export LANG=C
export LC_ALL=C
export DEBUGINFOD_URLS="${{DEBUGINFOD_URLS:-}}"
export DEBUGINFOD_CERT_PATH="${{DEBUGINFOD_CERT_PATH:-}}"
export DEBUGINFOD_IMA_CERT_PATH="${{DEBUGINFOD_IMA_CERT_PATH:-}}"
echo "=== gentoo-live-installer chroot rev {CHROOT_SCRIPT_REV} ==="
{resume_prelude}
mkdir -p /etc/portage/package.use /etc/portage/package.accept_keywords /etc/portage/package.license /etc/portage/profile
echo ">> USE flags and dracut configuration"
cat > /etc/portage/package.use/installer << 'EOF'
{package_use}
EOF
{keyword_block}
{dracut_block}
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
emerge --verbose --update --deep --newuse --keep-going $NR @world
world_status=$?
if [ "$world_status" -eq 132 ]; then
  echo ">> @world died with Illegal instruction; rebuilding from source"
  disable_binpkgs
  emerge --verbose --update --deep --newuse --keep-going --usepkg=n --getbinpkg=n @world || echo ">> @world source rebuild failed" >&2
elif [ "$world_status" -ne 0 ]; then
  echo ">> emerge @world failed ($world_status); continuing with the selected packages" >&2
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
# btrfs-progs, kbd, Plymouth, and OpenRC services must exist before the kernel
# package's installkernel hook runs dracut. A later desktop failure must not
# be in that same emerge.
{repair_tools}
{theme_block}
{plymouth_early}
{missed_block}
{firmware_block}
echo ">> emerge kernel"
emerge_with_source {kernel_atom} || echo ">> kernel emerge failed" >&2
if ! ls /boot/vmlinuz* >/dev/null 2>&1; then
  echo ">> no kernel image in /boot; building sys-kernel/gentoo-kernel from source"
  emerge --ask=n --usepkg=n --getbinpkg=n sys-kernel/gentoo-kernel || echo ">> gentoo-kernel source build failed" >&2
fi
{late_block}
{stores_block}

{swapfile}
{zram_block}
{user_block}
{session_extra}
{tpm_script}
{selinux_block}

{grub_install}
{plymouth_block}
{dm_setup}

if [ "{cfg.init_system}" = "openrc" ]; then
{textwrap.indent(nm_cleanup, "  ")}
{textwrap.indent(boot_enable, "  ")}
{textwrap.indent(enable_openrc, "  ")}
  append_once /etc/rc.conf 'rc_parallel="YES"'
  {rc_plymouth}
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
    overlay_names = [name for name in cfg.overlays if name != "guru"]
    if catalog.shell_atoms(getattr(cfg, "shell", "") or ""):
        overlay_names.append("guru")
    queued = [
        atom
        for atom in dict.fromkeys(
            cfg.extra_packages
            + _window_managers(cfg)
            + cfg.selected_packages
            + _support_atoms(cfg)
            + [atom for atom, _test in _display_manager_tools(cfg)]
        )
        if atom and _atom_allowed(cfg, atom)
    ]
    overlays = catalog.overlays_required_by(queued, overlay_names)
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
    """Apply the chosen splash, then rebuild every initramfs and grub.cfg.

    The theme is also applied before the kernel emerge. This second pass
    covers a theme package that failed the first time, and a dracut image
    that was packed with Plymouth's packaged theme.
    """
    if not cfg.plymouth:
        return ""
    theme = (cfg.plymouth_theme or "").strip()
    theme_lines = [
        f"emerge_one {shlex.quote(pkg.atom)} || true"
        for pkg in catalog.plymouth_packages_for(
            _chosen_plymouth_themes(cfg),
            cfg.plymouth_openrc_plugin and cfg.init_system == "openrc",
        )
    ]
    if theme:
        apply = (
            f"apply_plymouth_theme {shlex.quote(theme)} || "
            "echo '>> Plymouth theme was not applied' >&2"
        )
    else:
        apply = 'echo ">> no Plymouth theme was selected; the splash was not changed" >&2'
    grub = ""
    if "grub" in selected_bootloaders(cfg):
        grub = """
mkdir -p /etc/default /boot/grub
if [ ! -f /etc/default/grub ]; then
  echo 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"' > /etc/default/grub
elif grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub; then
  if ! grep -Eq '^GRUB_CMDLINE_LINUX_DEFAULT=.*splash' /etc/default/grub; then
    sed -i -E 's/^(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*)"/\\1 quiet splash"/' /etc/default/grub || true
  fi
else
  echo 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"' >> /etc/default/grub
fi
"""
    grub_cfg = ""
    if grub:
        grub_cfg = """
if command -v grub-mkconfig >/dev/null 2>&1; then
  grub-mkconfig -o /boot/grub/grub.cfg || echo ">> grub-mkconfig failed" >&2
else
  echo ">> grub-mkconfig does not exist" >&2
fi
"""
    theme_retry = "\n".join(theme_lines)
    return f"""
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"
emerge_checked sys-boot/plymouth command -v plymouth-set-default-theme || true
{theme_retry}
{apply}
{grub}
if ! command -v btrfs >/dev/null 2>&1; then
  emerge_one sys-fs/btrfs-progs || true
fi
rebuild_initramfs
{grub_cfg}
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


def _window_managers(cfg: InstallConfig) -> list[str]:
    """Compositors from the profile, plus labwc when a shell has no Wayland host."""
    known = {pkg.atom for pkg in catalog.WINDOW_MANAGERS}
    atoms: list[str] = []
    for atom in list(cfg.window_managers) + list(cfg.extra_packages) + list(cfg.selected_packages):
        if not atom or not _atom_allowed(cfg, atom):
            continue
        bare = atom.split("::", 1)[0]
        if bare in known:
            atoms.append(bare)
    shell = getattr(cfg, "shell", "") or ""
    if shell in catalog.SHELL_PACKAGES and not catalog.wayland_selected(atoms):
        atoms.append("gui-wm/labwc")
    return list(dict.fromkeys(atoms))


def _support_atoms(cfg: InstallConfig) -> list[str]:
    """Compositor requirements and the selected shell. GURU stays limited to that shell."""
    shell = getattr(cfg, "shell", "") or ""
    return [pkg.atom for pkg in catalog.packages_for_selection(_window_managers(cfg), shell, cfg.pipewire)]


def _atom_allowed(cfg: InstallConfig, atom: str) -> bool:
    if not atom or atom.startswith("flatpak-app/"):
        return True
    bare, _, suffix = atom.partition("::")
    overlay = suffix or catalog.atom_overlay(bare)
    if overlay != "guru":
        return True
    return bare in set(catalog.shell_atoms(getattr(cfg, "shell", "") or ""))


def _user_session_script(cfg: InstallConfig) -> str:
    """Write a first compositor config and dwm/dwl starters. Existing files stay."""
    wms = set(_window_managers(cfg))
    shell = catalog.shell_exec(getattr(cfg, "shell", "") or "")
    keymap = _safe_token(cfg.keymap, "us")
    lines: list[str] = []
    if "x11-wm/dwm" in wms:
        lines += [
            "install -d /usr/local/bin",
            "cat > /usr/local/bin/start-dwm << 'EOF'",
            "#!/bin/sh",
            "slstatus >/dev/null 2>&1 &",
            "exec dwm",
            "EOF",
            "chmod 755 /usr/local/bin/start-dwm",
        ]
    if "gui-wm/dwl" in wms and shell:
        lines += [
            "install -d /usr/local/bin",
            "cat > /usr/local/bin/start-dwl << 'EOF'",
            "#!/bin/sh",
            f"{shell} >/dev/null 2>&1 &",
            "exec dwl",
            "EOF",
            "chmod 755 /usr/local/bin/start-dwl",
        ]
    if "x11-wm/qtile" in wms and shell:
        lines += [
            "install -d /usr/local/bin",
            "cat > /usr/local/bin/start-qtile << 'EOF'",
            "#!/bin/sh",
            f"{shell} >/dev/null 2>&1 &",
            "exec qtile start -b wayland",
            "EOF",
            "chmod 755 /usr/local/bin/start-qtile",
        ]
    if not cfg.username or not wms:
        return "\n".join(lines) + ("\n" if lines else "")
    user = shlex.quote(cfg.username)
    lines += [
        f"userhome=$(getent passwd {user} | cut -d: -f6 || true)",
        'if [ -n "$userhome" ]; then',
    ]
    if "gui-wm/hyprland" in wms:
        lines += [
            '  install -d -m 755 "$userhome/.config/hypr"',
            '  hypr="$userhome/.config/hypr/hyprland.conf"',
            '  if [ ! -f "$hypr" ]; then',
            '    cat > "$hypr" << EOF',
            "monitor=,preferred,auto,1",
            "input {",
            f"  kb_layout = {keymap}",
            "}",
        ]
        if shell:
            lines.append(f"exec-once = {shell}")
        lines += [
            "EOF",
            f'    chown -R {user}:{user} "$userhome/.config/hypr" || true',
            "  fi",
        ]
        if shell:
            lines += [
                f'  if [ -f "$hypr" ] && ! grep -q {shlex.quote(shell)} "$hypr"; then',
                f'    echo {shlex.quote("exec-once = " + shell)} >> "$hypr"',
                "  fi",
            ]
    if "gui-wm/sway" in wms:
        lines += [
            '  install -d -m 755 "$userhome/.config/sway"',
            '  swaycfg="$userhome/.config/sway/config"',
            '  if [ ! -f "$swaycfg" ]; then',
            '    if [ -f /etc/sway/config ]; then cp /etc/sway/config "$swaycfg"; else : > "$swaycfg"; fi',
            f'    chown -R {user}:{user} "$userhome/.config/sway" || true',
            "  fi",
        ]
        if shell:
            lines += [
                f'  if [ -f "$swaycfg" ] && ! grep -q {shlex.quote(shell)} "$swaycfg"; then',
                f'    echo {shlex.quote("exec " + shell)} >> "$swaycfg"',
                "  fi",
            ]
    if "gui-wm/labwc" in wms:
        lines += [
            '  install -d -m 755 "$userhome/.config/labwc"',
            '  labauto="$userhome/.config/labwc/autostart"',
            '  if [ ! -f "$labauto" ]; then : > "$labauto"; fi',
        ]
        if shell:
            lines += [
                f'  if ! grep -q {shlex.quote(shell)} "$labauto"; then',
                f'    echo {shlex.quote(shell)} >> "$labauto"',
                "  fi",
            ]
        lines.append(f'  chown -R {user}:{user} "$userhome/.config/labwc" || true')
    if "gui-wm/wayfire" in wms:
        lines += [
            '  install -d -m 755 "$userhome/.config"',
            '  wfcfg="$userhome/.config/wayfire.ini"',
            '  if [ ! -f "$wfcfg" ]; then',
            '    cat > "$wfcfg" << EOF',
            "[autostart]",
        ]
        if shell:
            lines.append(f"shell = {shell}")
        lines += [
            "EOF",
            f'    chown {user}:{user} "$userhome/.config" "$wfcfg" || true',
            "  fi",
        ]
        if shell:
            lines += [
                f'  if [ -f "$wfcfg" ] && ! grep -q {shlex.quote(shell)} "$wfcfg"; then',
                '    printf "\\n[autostart]\\nshell = %s\\n" ' + shlex.quote(shell) + ' >> "$wfcfg"',
                "  fi",
            ]
    if "x11-wm/dwm" in wms or "x11-wm/i3" in wms:
        lines += [
            '  xinit="$userhome/.xinitrc"',
            '  if [ ! -f "$xinit" ]; then',
            "    cat > \"$xinit\" << 'EOF'",
            "#!/bin/sh",
        ]
        if "x11-wm/dwm" in wms:
            lines += ["slstatus >/dev/null 2>&1 &", "exec dwm"]
        else:
            lines.append("exec i3")
        lines += [
            "EOF",
            '    chmod 755 "$xinit"',
            f'    chown {user}:{user} "$xinit" || true',
            "  fi",
        ]
    lines.append("fi")
    return "\n".join(lines) + "\n"


def _desktop_packages(cfg: InstallConfig) -> list[str]:
    """Browsers and Flatpak from the profile. Desktop metas are not installed."""
    pkgs: list[str] = []
    if cfg.pipewire:
        pkgs.extend(["media-video/pipewire", "media-video/wireplumber"])
    if cfg.browser == "firefox-bin":
        pkgs.append("www-client/firefox-bin")
    elif cfg.browser == "firefox":
        pkgs.append("www-client/firefox")
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
