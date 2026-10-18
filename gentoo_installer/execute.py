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

import re
import shlex
import shutil
import textwrap
from pathlib import Path

from . import catalog, detect, ui
from .config import InstallConfig, part_name
from .runner import Runner, State


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

    runner = Runner(dry_run=cfg.dry_run)
    state = State(cfg.state_file)
    root = cfg.mountpoint

    phases = [
        ("host_packages", lambda: phase_host(cfg, runner)),
        ("network", lambda: phase_network(cfg, runner)),
        ("disks", lambda: phase_disks(cfg, runner)),
        ("stage3", lambda: phase_stage3(cfg, runner, root)),
        ("chroot_mounts", lambda: phase_chroot_mounts(cfg, runner, root)),
        ("chroot_install", lambda: phase_chroot(cfg, runner, root)),
        ("done", lambda: phase_done(cfg, runner, root)),
    ]
    for name, fn in phases:
        ui.chapter("→", f"Phase: {name}", "https://wiki.gentoo.org/wiki/Handbook:AMD64")
        if state.skip(name, cfg.resume):
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
        runner.shell(f"wpa_supplicant -B -i {shlex.quote(cfg.wifi_iface)} -c /tmp/wpa-gentoo.conf")
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


def phase_disks(cfg: InstallConfig, runner: Runner) -> None:
    """Wipe/partition/LUKS/LVM, mkfs, and mount at cfg.mountpoint (handbook /mnt/gentoo)."""
    disk = cfg.target_disk
    if cfg.wipe_disk and cfg.layout != "existing":
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
        runner.shell(f"mkdir -p {shlex.quote(cfg.mountpoint + '/boot')}")
        runner.shell(f"mount {shlex.quote(part_name(disk, 1))} {shlex.quote(cfg.mountpoint + '/boot')}")


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


def _mount_tree(cfg: InstallConfig, runner: Runner, devices: dict[str, str]) -> None:
    """Mount root (and Btrfs subvols / ESP) under cfg.mountpoint for stage3 extract."""
    root = cfg.mountpoint
    runner.shell(f"mkdir -p {shlex.quote(root)}")
    if cfg.layout == "zfs":
        return
    if cfg.layout == "btrfs-subvols" or cfg.filesystem == "btrfs" and cfg.layout == "btrfs-subvols":
        runner.shell(f"mount {shlex.quote(devices['root'])} {shlex.quote(root)}")
        for sub in ("@", "@home", "@log", "@cache", "@tmp", "@swap"):
            runner.shell(f"btrfs subvolume create {shlex.quote(root + '/' + sub)}")
        runner.shell(f"umount {shlex.quote(root)}")
        opts = "noatime,compress=zstd:3,ssd"
        runner.shell(f"mount -o {opts},subvol=@ {shlex.quote(devices['root'])} {shlex.quote(root)}")
        for sub, mp in (("@home", "home"), ("@log", "var/log"), ("@cache", "var/cache"), ("@tmp", "tmp"), ("@swap", "swap")):
            runner.shell(f"mkdir -p {shlex.quote(root + '/' + mp)}")
            extra = "noatime,nodatacow" if sub == "@swap" else opts
            runner.shell(
                f"mount -o {extra},subvol={sub} {shlex.quote(devices['root'])} {shlex.quote(root + '/' + mp)}"
            )
    else:
        runner.shell(f"mount {shlex.quote(devices['root'])} {shlex.quote(root)}")
    if cfg.firmware == "uefi" and devices.get("esp"):
        runner.shell(f"mkdir -p {shlex.quote(root + '/efi')} {shlex.quote(root + '/boot')}")
        # Handbook often mounts ESP at /efi with kernel in /boot, or ESP at /boot.
        # Use /boot for dist-kernel + GRUB simplicity (handbook UEFI GRUB common path).
        runner.shell(f"mount {shlex.quote(devices['esp'])} {shlex.quote(root + '/boot')}")
    elif devices.get("boot") and devices["boot"] != devices.get("root"):
        runner.shell(f"mkdir -p {shlex.quote(root + '/boot')}")
        runner.shell(f"mount {shlex.quote(devices['boot'])} {shlex.quote(root + '/boot')}")


def stage3_flavor(cfg: InstallConfig) -> str:
    """Name used in latest-stage3-<flavor>.txt on distfiles.gentoo.org."""
    arch = cfg.architecture
    init = cfg.init_system
    if cfg.stage_variant == "musl":
        return f"stage3-{arch}-musl-{init}" if init == "openrc" else f"stage3-{arch}-musl"
    if cfg.stage_variant == "hardened":
        return f"stage3-{arch}-hardened-{init}"
    if cfg.install_type == "desktop":
        return f"stage3-{arch}-desktop-{init}"
    return f"stage3-{arch}-{init}"


def phase_stage3(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Download the current stage3, optionally verify SHA256/GPG, extract with xattrs."""
    flavor = stage3_flavor(cfg)
    latest = f"https://distfiles.gentoo.org/releases/{cfg.architecture}/autobuilds/latest-{flavor}.txt"
    ui.info(f"Resolving latest {flavor}")
    pointer = f"{root}/latest-stage3.txt"
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
            if "stage3" in line and ".tar." in line:
                rel = line.split()[0]
                break
        if not rel:
            raise SystemExit(f"Could not parse stage3 pointer from {latest}")
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
    runner.shell(f"tar xpvf {shlex.quote(root + '/' + tarball)} --xattrs-include='*.*' --numeric-owner -C {shlex.quote(root)}")
    ui.ok("Stage3 extracted")


def phase_chroot_mounts(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Bind /proc /sys /dev /run and copy resolv.conf — handbook 'mounting the necessary filesystems'."""
    runner.shell(f"mount --types proc /proc {shlex.quote(root + '/proc')}")
    runner.shell(f"mount --rbind /sys {shlex.quote(root + '/sys')}")
    runner.shell(f"mount --make-rslave {shlex.quote(root + '/sys')}")
    runner.shell(f"mount --rbind /dev {shlex.quote(root + '/dev')}")
    runner.shell(f"mount --make-rslave {shlex.quote(root + '/dev')}")
    runner.shell(f"mkdir -p {shlex.quote(root + '/run')}")
    runner.shell(f"mount --bind /run {shlex.quote(root + '/run')} || true")
    runner.shell(f"cp /etc/resolv.conf {shlex.quote(root + '/etc/resolv.conf')}")


def phase_chroot(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Write chroot-install.sh + a 0600 secrets file, chroot, then delete secrets."""
    devices = getattr(cfg, "_devices", _map_devices(cfg))
    script = render_chroot_script(cfg, devices)
    path = Path(root) / "root" / "chroot-install.sh"
    if cfg.dry_run:
        ui.info("Would write chroot-install.sh and chroot into the target.")
        Path("chroot-install.preview.sh").write_text(script, encoding="utf-8")
        ui.ok("Wrote chroot-install.preview.sh in the current directory")
        return
    _repair_chroot_leftovers(cfg, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
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
    runner.shell(
        f"env -u FEATURES chroot {shlex.quote(root)} /bin/bash /root/chroot-install.sh"
    )
    if secrets.exists():
        secrets.unlink()


_BAD_FEATURE = "binpkg-request-use"


def _strip_bad_features(path: Path) -> bool:
    """Remove the invalid Portage FEATURES token from a text file. Returns True if changed."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if _BAD_FEATURE not in text:
        return False
    path.write_text(text.replace(_BAD_FEATURE, ""), encoding="utf-8")
    return True


def _repair_chroot_leftovers(cfg: InstallConfig, root: str) -> None:
    """Fix leftovers from earlier installer revisions before the chroot script runs.

    * FEATURES=binpkg-request-use in make.conf / profile.env (not a Portage feature)
    * /etc/vconsole.conf created as a directory by `mkdir -p`
    """
    rootp = Path(root)
    cleaned = False
    for path in [
        rootp / "etc" / "portage" / "make.conf",
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


def phase_done(cfg: InstallConfig, runner: Runner, root: str) -> None:
    ui.ok("Installation phases finished.")
    ui.info(f"When you are ready: umount -R {root} && reboot")
    ui.info("Remove the Ubuntu live USB so the machine boots from the Gentoo disk.")


def render_chroot_script(cfg: InstallConfig, devices: dict[str, str]) -> str:
    """Build the bash script that runs *inside* Gentoo (Portage, kernel, bootloader).

    Keep this as bash, not Python: the chroot only has the stage3 toolchain.
    """
    jobs = cfg.makeopts_jobs or detect.nproc()
    video = detect.detect_gpu() if cfg.video_cards == "auto" else cfg.video_cards
    use = ["dist-kernel"]
    use.extend(cfg.extra_use)
    if cfg.window_managers:
        use.append("wayland")
    if cfg.init_system == "systemd":
        use.append("systemd")
    else:
        use.append("elogind")
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
        # Prefer the emerge flag over FEATURES=getbinpkg. Do not set FEATURES at all:
        # it is incremental, and leftover binpkg-request-use from an earlier make.conf
        # made Portage print a red !!! warning on every emerge.
        emerge_opts += " --getbinpkg"
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
        f'echo "*/* ~{kw_arch}" >> /etc/portage/package.accept_keywords/testing'
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

    locale_short = cfg.locale.split(".")[0]
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
        dict.fromkeys(boot_pkgs + net_pkgs + extra_fw + de_pkgs + cfg.extra_packages + cfg.window_managers)
    )

    dm = cfg.display_manager
    if dm == "auto":
        dm = {"plasma": "sddm", "gnome": "gdm", "xfce": "lightdm", "mate": "lightdm", "cinnamon": "lightdm", "i3": "lightdm"}.get(
            cfg.desktop, "none"
        )

    openrc_services = []
    systemd_services = []
    if cfg.network_stack == "networkmanager":
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

    enable_openrc = "\n".join(f"rc-update add {s} default || true" for s in openrc_services)
    enable_systemd = "\n".join(f"systemctl enable {s} || true" for s in systemd_services)

    installkernel_conf = ""
    if cfg.bootloader == "grub":
        installkernel_conf = "echo 'sys-kernel/installkernel grub dracut' >> /etc/portage/package.use/installkernel"
    elif cfg.bootloader == "systemd-boot":
        installkernel_conf = "echo 'sys-kernel/installkernel systemd systemd-boot dracut' >> /etc/portage/package.use/installkernel"
    elif cfg.uki or cfg.bootloader == "efistub":
        installkernel_conf = "echo 'sys-kernel/installkernel dracut uki' >> /etc/portage/package.use/installkernel"
    else:
        installkernel_conf = "echo 'sys-kernel/installkernel dracut' >> /etc/portage/package.use/installkernel"

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
echo '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/wheel
chmod 440 /etc/sudoers.d/wheel
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
cat > /etc/conf.d/zram-init << 'EOF'
load_on_start=yes
num_devices=1
type0=swap
size0=$(($(awk '/MemTotal/ {print int($2/2)}' /proc/meminfo)))
algo0=zstd
EOF
rc-update add zram-init boot || true
"""

    swapfile = ""
    if cfg.swap_mode in {"file", "both"}:
        swapfile = f"""
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count={cfg.swap_size_gib * 1024} status=progress || true
  chmod 600 /swapfile
  mkswap /swapfile
fi
echo '/swapfile none swap sw 0 0' >> /etc/fstab
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

    make_conf = f"""
COMMON_FLAGS="{cflags}"
CFLAGS="${{COMMON_FLAGS}}"
CXXFLAGS="${{COMMON_FLAGS}}"
FCFLAGS="${{COMMON_FLAGS}}"
FFLAGS="${{COMMON_FLAGS}}"
MAKEOPTS="-j{jobs}"
EMERGE_DEFAULT_OPTS="{emerge_opts}"
USE="{' '.join(sorted(set(use)))}"
ACCEPT_LICENSE="{cfg.accept_license}"
VIDEO_CARDS="{video}"
GENTOO_MIRRORS="{mirrors}"
{grub_plat}
"""

    overlay_block = _overlay_script(cfg)
    plymouth_block = _plymouth_script(cfg)

    return f"""#!/bin/bash
set -eo pipefail
# gentoo-live-installer chroot script rev 7
# Do not enable `set -u`. Gentoo profile.d/debuginfod.sh reads optional DEBUGINFOD_*.
unset FEATURES
export DEBUGINFOD_URLS="${{DEBUGINFOD_URLS:-}}"
export DEBUGINFOD_CERT_PATH="${{DEBUGINFOD_CERT_PATH:-}}"
export DEBUGINFOD_IMA_CERT_PATH="${{DEBUGINFOD_IMA_CERT_PATH:-}}"

purge_bad_features() {{
  unset FEATURES
  # make.conf may be a file *or* a directory of fragments.
  find /etc/portage /etc/env.d /etc/profile.env /usr/share/portage/config \\
      -type f 2>/dev/null | while read -r f; do
    grep -Fq 'binpkg-request-use' "$f" 2>/dev/null || continue
    sed -i 's/binpkg-request-use//g' "$f" || true
  done || true
}}

source_profile() {{
  set +e
  purge_bad_features
  # shellcheck disable=SC1091
  source /etc/profile
  purge_bad_features
  unset FEATURES
  set -e
}}

# Hide the one known Portage !!! line if a fragment still mentions the token.
emerge() {{
  purge_bad_features
  command emerge "$@" 2> >(grep -v --line-buffered 'binpkg-request-use' >&2 || true)
}}

if [ -d /etc/vconsole.conf ]; then
  rm -rf /etc/vconsole.conf
fi
mkdir -p /etc/portage/package.use /etc/portage/package.accept_keywords /etc/portage/package.license /etc/portage/profile

if [ -d /etc/portage/make.conf ]; then
  cat > /etc/portage/make.conf/99gentoo-live-installer << 'MAKECONF'
{make_conf.strip()}
MAKECONF
else
  cat > /etc/portage/make.conf << 'MAKECONF'
{make_conf.strip()}
MAKECONF
fi
purge_bad_features
source_profile
export PS1="(chroot) ${{PS1:-}}"

{binrepo}
{installkernel_conf}
{dracut_luks}
echo 'sys-kernel/linux-firmware @BINARY-REDISTRIBUTABLE' > /etc/portage/package.license/firmware || true
{keywords}

{sync}
{overlay_block}
eselect profile set {shlex.quote(cfg.profile)} || eselect profile set --force {shlex.quote(cfg.profile)} || true
if command -v mirrorselect >/dev/null 2>&1; then
  mirrorselect -s3 -b10 || true
fi
emerge --verbose --update --deep --newuse @world || true
if command -v cpuid2cpuflags >/dev/null 2>&1 || emerge --ask=n app-portage/cpuid2cpuflags; then
  echo "*/* $(cpuid2cpuflags)" > /etc/portage/package.use/00cpu-flags || true
fi

echo '{cfg.hostname}' > /etc/hostname
echo 'hostname="{cfg.hostname}"' > /etc/conf.d/hostname || true
ln -sf /usr/share/zoneinfo/{cfg.timezone} /etc/localtime
echo '{cfg.timezone}' > /etc/timezone || true
sed -i 's/^#\\({cfg.locale}\\)/\\1/' /etc/locale.gen || echo '{cfg.locale} UTF-8' >> /etc/locale.gen
locale-gen
eselect locale set {cfg.locale} || eselect locale set {locale_short} || true
echo 'keymap="{cfg.keymap}"' > /etc/conf.d/keymaps || true
if [ -d /etc/vconsole.conf ]; then
  rm -rf /etc/vconsole.conf
fi
printf 'KEYMAP=%s\\n' {shlex.quote(cfg.keymap)} > /etc/vconsole.conf
purge_bad_features
env-update || true
source_profile

cat > /etc/fstab << 'FSTAB'
{fstab.strip()}
FSTAB
{crypttab_block}

emerge --ask=n sys-kernel/{pkgs_kernel} {all_extra}

{swapfile}
{zram_block}
{user_block}
{tpm_script}
{selinux_block}

{grub_install}
{plymouth_block}

if [ "{cfg.init_system}" = "openrc" ]; then
{textwrap.indent(enable_openrc, "  ")}
  echo 'rc_parallel="YES"' >> /etc/rc.conf || true
else
{textwrap.indent(enable_systemd, "  ")}
fi

if [ "{cfg.network_stack}" = "netifrc" ]; then
  echo 'config_{netifrc_iface}="dhcp"' > /etc/conf.d/net || true
  ln -s /etc/init.d/net.lo /etc/init.d/net.{netifrc_iface} || true
  rc-update add net.{netifrc_iface} default || true
fi

echo "Chroot configuration complete."
"""


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
        lines.append(f"eselect repository enable {shlex.quote(ov)} || true")
        lines.append(f"emaint sync --repo {shlex.quote(ov)} || true")
        lines.append(f'echo "*/*::{ov} {arch_kw}" >> /etc/portage/package.accept_keywords/overlays')
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
mkdir -p /etc/default
if [ -f /etc/default/grub ]; then
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"/' /etc/default/grub || true
fi
grep -q GRUB_CMDLINE_LINUX_DEFAULT /etc/default/grub 2>/dev/null || echo 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"' >> /etc/default/grub
grub-mkconfig -o /boot/grub/grub.cfg || true
"""
    return f"""
plymouth-set-default-theme {shlex.quote(theme_name)} || true
{grub}
"""


def _install_bootloaders(cfg: InstallConfig, devices: dict[str, str], loaders: list[str]) -> str:
    """Chroot commands to install every selected bootloader (not only the primary)."""
    cmds: list[str] = []
    for bl in loaders:
        if bl == "grub":
            if cfg.firmware == "uefi":
                grub_target = "arm64-efi" if cfg.architecture == "arm64" else "x86_64-efi"
                cmds.append(
                    f"grub-install --target={grub_target} --efi-directory=/boot --bootloader-id=Gentoo"
                )
            else:
                cmds.append(f"grub-install {shlex.quote(cfg.target_disk)}")
            cmds.append("grub-mkconfig -o /boot/grub/grub.cfg")
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
  grub-mkconfig -o /boot/grub/grub.cfg
fi
"""
                )
        elif bl == "systemd-boot":
            cmds.append("bootctl install || true")
        elif bl == "refind":
            cmds.append("refind-install || true")
        elif bl == "syslinux":
            boot = devices.get("boot") or devices.get("esp") or ""
            cmds.append(f"syslinux-install_update -iam || syslinux {boot} || true")
        elif bl == "efistub":
            cmds.append("echo 'EFI stub/UKI selected — kernel installkernel handles the EFI entry' ")
        elif bl == "limine":
            cmds.append(_limine_install(cfg, devices))
        elif bl == "lilo":
            cmds.append("lilo || true")
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
