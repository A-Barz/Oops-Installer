"""Execute the handbook installation from an Ubuntu Boot Image live host.

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

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

from . import catalog, detect, ui
from .config import SECRET_FIELDS, InstallConfig, part_name
from .estimate import estimate_minutes, format_duration
from .rev import INSTALLER_REV
from .runner import Runner, State


def _secret_values(cfg: InstallConfig) -> list[str]:
    """Non-empty secret field values for Runner log/step redaction."""
    values: list[str] = []
    for name in SECRET_FIELDS:
        value = getattr(cfg, name, "") or ""
        if value:
            values.append(value)
    return values


def _install_secrets_path(mountpoint: str) -> Path:
    return Path(mountpoint) / "root" / ".install-secrets"


def _shred_install_secrets(mountpoint: str) -> None:
    """Overwrite and unlink $mountpoint/root/.install-secrets when present.

    Fail-closed hygiene: chroot failures, resume prompts, and unmount paths must
    not leave root/user/LUKS/GRUB passwords on the target.
    """
    if not mountpoint:
        return
    path = _install_secrets_path(mountpoint)
    if not path.is_file():
        return
    try:
        subprocess.run(
            ["shred", "-u", "-n", "1", "--", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        pass
    if path.exists():
        try:
            size = path.stat().st_size
            with path.open("r+b", buffering=0) as fh:
                fh.write(b"\0" * max(size, 1))
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            pass
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            ui.warn(f"Could not remove {path}: {exc}")

# Same integer as the startup banner. Bump INSTALLER_REV in rev.py on each change.
CHROOT_SCRIPT_REV = INSTALLER_REV

# linux-mod / linux-info consumers: need /usr/src/linux before emerge.
# Keep these out of the pre-kernel repair list (dracut/installkernel stay early).
KERNEL_SRC_DEPENDENT_ATOMS: frozenset[str] = frozenset(
    {
        "sys-fs/zfs",
        "app-emulation/virtualbox",
    }
)


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
    # Ubuntu Boot Image clock sync (one-shot client). Host-only — Gentoo keeps chrony.
    "ntpsec-ntpdate",
]

# Shown when live-host NTP tools are missing or sync fails (stage3 GPG needs a sane clock).
NTP_NTC_HINT = (
    "NTP sync tools were not found (or clock sync failed) on this Ubuntu Boot Image host. "
    "Install Ubuntu package ntpsec-ntpdate for live clock sync: "
    "sudo apt-get install -y ntpsec-ntpdate"
)


def execute(cfg: InstallConfig) -> None:
    """Run host phases in handbook order. Requires root on Linux unless dry-run."""
    if not cfg.dry_run:
        if not detect.is_linux():
            raise SystemExit("Execution requires a Linux live environment (Ubuntu Boot Image live).")
        if not detect.is_root():
            raise SystemExit("Re-run as root: sudo python3 install.py")

    # Live USBs (and a previous failed chroot) can export stale FEATURES/USE into
    # this process. Portage then prints "Run . /etc/profile". Drop them here so
    # --config/--resume does not require sourcing the profile by hand.
    os.environ.pop("FEATURES", None)
    os.environ.pop("PORTAGE_FEATURES", None)
    os.environ.pop("USE", None)

    from .wizard import _apply_firewall_exclusion, queue_saved_software

    queue_saved_software(cfg)
    # A1: without-systemd is not supported (eudev vs stage3 systemd-utils).
    catalog.refuse_unsupported_overlays(cfg.overlays or [])
    # M9: YAML/--config profiles can still list ufw alongside default nftables.
    _apply_firewall_exclusion(cfg)

    runner = Runner(dry_run=cfg.dry_run, secrets=_secret_values(cfg))
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

    minutes = estimate_minutes(cfg)
    ui.ok(f"Estimated install time: {format_duration(minutes)}.")
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
    # Disk path was confirmed before execute(). This is the last line before
    # the unattended phases, so it is not shown on the review screen.
    if not cfg.dry_run:
        ui.install_wait_notice()
    error: BaseException | None = None
    try:
        _run_phases(cfg, runner, state, phases, root, rerun_on_resume)
    except (SystemExit, KeyboardInterrupt) as exc:
        error = exc
    if error is None:
        dm = _resolved_dm(cfg)
        if dm != "none":
            who = f" Sign in as {cfg.username}." if cfg.username else ""
            ui.info(f"The next boot opens the {dm} login screen.{who} Choose a window manager there.")
        ui.congratulate()
        return
    decision = ask_resume_or_unmount(cfg)
    if decision == "resume":
        ui.ok("Resuming the interrupted install. The target stays mounted.")
        execute(cfg)
        return
    if decision == "fresh":
        raise RestartFromBeginning()
    if isinstance(error, KeyboardInterrupt):
        print()
        ui.warn("Interrupted.")
        raise SystemExit(130) from error
    raise error


def _run_phases(
    cfg: InstallConfig,
    runner: Runner,
    state: State,
    phases,
    root: str,
    rerun_on_resume: set[str],
) -> None:
    for name, fn in phases:
        ui.chapter("→", f"Phase: {name}", "https://wiki.gentoo.org/wiki/Handbook:AMD64")
        if cfg.resume and name in rerun_on_resume:
            ui.info(f"Resume: running {name} again.")
            fn()
            state.mark(name)
        elif state.skip(name, cfg.resume):
            # Skip means "already done", not "leave the target unmounted".
            # --config/--resume after a crash often still has /mnt/gentoo mounted;
            # if the machine rebooted, reopen LUKS and remount without mkfs.
            if name == "disks":
                _ensure_target_mounted(cfg, runner)
            elif name == "chroot_mounts":
                phase_chroot_mounts(cfg, runner, root)
        else:
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


def _host_ntp_tools_present() -> bool:
    """True when the live host has at least one usable clock-sync command."""
    return any(
        shutil.which(name)
        for name in ("ntpdate", "ntpdig", "ntpd", "timedatectl")
    )


def sync_live_clock(cfg: InstallConfig, runner: Runner) -> bool:
    """Synchronise the Ubuntu Boot Image clock before stage3 / GPG.

    Prefers ``ntpdate`` from Ubuntu ``ntpsec-ntpdate``. When tools are missing
    or every sync attempt fails, warn with :data:`NTP_NTC_HINT`.
    Returns True when sync looked successful (or was skipped / dry-run).
    """
    if not cfg.ntp:
        return True
    if cfg.dry_run:
        ui.info("Dry-run: would synchronise the live clock (ntpsec-ntpdate / timedatectl).")
        return True
    if not _host_ntp_tools_present():
        ui.warn("ntp update was not found on this live host.")
        ui.warn(NTP_NTC_HINT)
        return False
    # Prefer ntpdate from ntpsec-ntpdate, then other NTP clients / timedatectl.
    script = (
        "if command -v ntpdate >/dev/null 2>&1; then ntpdate -u pool.ntp.org; "
        "elif command -v ntpdig >/dev/null 2>&1; then ntpdig -s pool.ntp.org; "
        "elif command -v ntpd >/dev/null 2>&1; then ntpd -gq; "
        "elif command -v timedatectl >/dev/null 2>&1; then timedatectl set-ntp true; "
        "else exit 127; fi"
    )
    proc = runner.shell(script, check=False)
    if proc.returncode == 0:
        ui.ok("Live clock synchronised.")
        return True
    if proc.returncode == 127 or not _host_ntp_tools_present():
        ui.warn("ntp update was not found on this live host.")
    else:
        ui.warn("Live clock synchronisation failed.")
    ui.warn(NTP_NTC_HINT)
    return False


def _dns_nameservers(cfg: InstallConfig) -> list[str]:
    """Nameservers from dns_servers / dns_provider / static_dns."""
    servers = catalog.dns_servers_for(
        getattr(cfg, "dns_provider", "") or "cloudflare",
        getattr(cfg, "dns_servers", "") or "",
    )
    if not servers and (cfg.static_dns or "").strip():
        servers = [cfg.static_dns.strip()]
    return servers or list(catalog.DNS_PRESETS["cloudflare"])


def _resolv_conf_text(cfg: InstallConfig) -> str:
    lines = [
        f"# Oops-Installer DNS ({getattr(cfg, 'dns_provider', 'cloudflare')})",
        *[f"nameserver {ip}" for ip in _dns_nameservers(cfg)],
        "",
    ]
    return "\n".join(lines)


def _apply_live_dns(cfg: InstallConfig) -> None:
    """Write /etc/resolv.conf on the Ubuntu live host from the wizard DNS pick."""
    text = _resolv_conf_text(cfg)
    Path("/etc/resolv.conf").write_text(text, encoding="utf-8")
    ui.ok(f"Live DNS → {' '.join(_dns_nameservers(cfg))}")


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
    # Apply chosen DNS on the live host (dhcp/wifi/static) so stage3 uses the same resolvers.
    if cfg.network_mode != "skip" and not cfg.dry_run:
        _apply_live_dns(cfg)
    sync_live_clock(cfg, runner)
    if not cfg.dry_run and not detect.has_network():
        ui.warn("Network still looks down. Stage3 download may fail.")


def _luks_format_open(cfg: InstallConfig, runner: Runner, device: str, name: str) -> None:
    """LUKS2-format a partition/LV and open it as /dev/mapper/<name>.

    Passphrase is fed on stdin via --key-file=- so it never appears in the
    logged argv / shell script (H4).
    """
    if not cfg.luks_passphrase:
        raise SystemExit(f"LUKS passphrase missing; cannot format {device}.")
    runner.set_secrets(_secret_values(cfg))
    runner.run(
        [
            "cryptsetup",
            "luksFormat",
            "--type",
            "luks2",
            "--batch-mode",
            "--key-file=-",
            device,
        ],
        input_text=cfg.luks_passphrase,
    )
    runner.run(
        ["cryptsetup", "open", "--key-file=-", device, name],
        input_text=cfg.luks_passphrase,
    )


def _netifrc_iface(cfg: InstallConfig) -> str:
    raw = cfg.netifrc_iface or cfg.static_iface or cfg.wifi_iface or detect.default_nic()
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", raw.strip())
    return match.group(0) if match else "eth0"


def _mountpoint_q(path: str) -> bool:
    return detect.is_mounted(path)


def _leave_tree(mountpoint: str) -> None:
    """chdir to / when this process is standing inside the install tree.

    Unmounting /mnt/gentoo deletes the current directory. The next bash then
    fails before it can run `mkdir --parents /mnt/gentoo`.
    """
    root = os.path.abspath(mountpoint or "/")
    try:
        cwd = os.getcwd()
    except OSError:
        os.chdir("/")
        return
    if cwd == root or cwd.startswith(root + os.sep):
        os.chdir("/")


def _prepare_mountpoint(runner: Runner, dest: str) -> None:
    """Handbook: `mkdir --parents` the mount point, then mount onto a directory.

    `mkdir --parents` still exits "File exists" when the path is a symlink or a
    regular file, which a live session or an earlier download can leave behind.
    A wiped filesystem that is still mounted makes the same mkdir fail with an
    I/O error. Replace a non-directory, drop a stale mount, then create it.
    """
    if not dest or not str(dest).startswith("/"):
        raise SystemExit(f"Mount point must be an absolute path, not {dest!r}.")
    linux_path = str(dest).replace("\\", "/").rstrip("/") or "/"
    if linux_path == "/":
        raise SystemExit("Refusing to use / as the Gentoo mount point.")
    _leave_tree(dest)
    quoted = shlex.quote(dest)
    runner.shell(
        "\n".join(
            [
                f"dest={quoted}",
                'if [ -L "$dest" ] || { [ -e "$dest" ] && [ ! -d "$dest" ]; }; then',
                '  rm -f "$dest" || { echo ">> $dest exists and is not a directory" >&2; exit 1; }',
                "fi",
                'if mountpoint -q "$dest" 2>/dev/null && ! stat "$dest" >/dev/null 2>&1; then',
                '  echo ">> $dest is a stale mount; detaching it"',
                '  umount -R "$dest" || umount -l "$dest" || { echo ">> could not unmount $dest" >&2; exit 1; }',
                "  for _try in 1 2 3 4 5 6 7 8 9 10; do",
                '    mountpoint -q "$dest" || break',
                "    sleep 0.3",
                "  done",
                '  if mountpoint -q "$dest"; then echo ">> $dest is still mounted" >&2; exit 1; fi',
                "fi",
                'mkdir --parents "$dest" || {',
                '  echo ">> mkdir --parents $dest failed" >&2',
                '  ls -ld "$dest" "$(dirname "$dest")" >&2 || true',
                "  exit 1",
                "}",
            ]
        )
    )


def _root_fstype(cfg: InstallConfig) -> str:
    return {"ext4": "ext4", "xfs": "xfs", "btrfs": "btrfs", "f2fs": "f2fs", "jfs": "jfs"}.get(
        cfg.filesystem, "ext4"
    )


def _efi_mount(cfg: InstallConfig) -> str:
    """Handbook UEFI: ESP is mounted at /efi. /boot stays on the root filesystem."""
    return cfg.mountpoint.rstrip("/") + "/efi"


def _linux_root_type(cfg: InstallConfig) -> str:
    """sgdisk type for the root partition. Handbook fdisk type 23 is 8304 on amd64."""
    if cfg.luks:
        return "8309"
    if cfg.architecture == "arm64":
        return "8305"
    return "8304"


# Bare `partprobe` (no disk arg) walks every block device, including the live
# USB's casper device-mapper overlays. Those often hang on
# "device-mapper: reload ioctl … failed" — so we never probe the whole system.
PARTPROBE_TIMEOUT_SEC = 15


def _partprobe_disk(runner: Runner, disk: str) -> None:
    """Reread partition table for one target disk only, with a hard timeout.

    Skips the Ubuntu Boot Image medium. On ioctl / dm failure, logs and
    continues — udev + waiting for /dev nodes is enough for the next steps.
    """
    if not disk or not disk.startswith("/dev/"):
        return
    if detect.is_live_installer_disk(disk):
        ui.warn(
            f"Skipping partprobe on live installer medium {disk} "
            "(avoids USB device-mapper reload ioctl hangs)."
        )
        return
    q = shlex.quote(disk)
    t = int(PARTPROBE_TIMEOUT_SEC)
    ui.info(f"partprobe {disk} (timeout {t}s; target disk only)")
    # Prefer timeout(1); fall back to a short blockdev reread. Never call
    # partprobe with no arguments — that scans the live USB dm stack too.
    runner.shell(
        "\n".join(
            [
                "if command -v timeout >/dev/null 2>&1; then",
                f"  timeout {t} partprobe {q} 2>/dev/null || "
                f"timeout {t} blockdev --rereadpt {q} 2>/dev/null || "
                f'echo ">> partprobe {disk} timed out or failed '
                f'(device-mapper ioctl); continuing without hang" >&2',
                "else",
                f"  partprobe {q} 2>/dev/null || blockdev --rereadpt {q} 2>/dev/null || "
                f'echo ">> partprobe {disk} failed; continuing" >&2',
                "fi",
                # Bound udev wait so a busy live USB cannot stall here either.
                "if command -v timeout >/dev/null 2>&1; then",
                "  timeout 10 udevadm settle --timeout=8 2>/dev/null || true",
                "else",
                "  udevadm settle --timeout=8 2>/dev/null || udevadm settle || true",
                "fi",
            ]
        ),
        check=False,
    )


def _release_disk_mounts(runner: Runner, disk: str) -> None:
    """Unmount live-desktop automounts of this disk so mkfs and mount can use it.

    Ubuntu Boot Image mounts a Windows volume as soon as it sees one. Leaving that
    mount up makes the later Gentoo mount look at a busy or half-wiped NTFS.
    """
    if not disk or not disk.startswith("/dev/"):
        return
    if detect.is_live_installer_disk(disk):
        ui.warn(f"Not releasing mounts on live installer medium {disk}")
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
                "if command -v timeout >/dev/null 2>&1; then",
                "  timeout 10 udevadm settle --timeout=8 2>/dev/null || true",
                "else",
                "  udevadm settle --timeout=8 2>/dev/null || udevadm settle || true",
                "fi",
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
    _prepare_mountpoint(runner, dest)
    src = shlex.quote(source)
    dst = shlex.quote(dest)
    opt = f"-o {shlex.quote(options)} " if options else ""
    runner.shell(
        "\n".join(
            [
                f"if ! mountpoint -q {dst}; then",
                f"  dest_path={dst}",
                f"  findmnt -rn -o TARGET -S {src} 2>/dev/null | while IFS= read -r other; do",
                '    [ -z "$other" ] && continue',
                # Btrfs subvolumes are one block device. /mnt/gentoo is already
                # mounted when /mnt/gentoo/home is mounted. Unmounting that
                # parent removes the mount point, and mount(2) then fails
                # with "No such file or directory" (exit 32, dmesg(1)).
                '    [ "$other" = "$dest_path" ] && continue',
                '    case "$dest_path" in',
                '      "$other"|"$other"/*) continue ;;',
                "    esac",
                '    umount "$other" || umount -l "$other" || true',
                "  done",
                "  for _try in 1 2 3 4 5 6 7 8 9 10; do",
                f"    [ -b {src} ] && break",
                "    udevadm settle || true",
                "    sleep 0.3",
                "  done",
                f'  if [ ! -b {src} ]; then echo ">> block device {source} is missing" >&2; exit 1; fi',
                f"  mkdir --parents {dst}",
                f"  if ! mount -t {fstype} {opt}{src} {dst}; then",
                f'    echo ">> mount {source} on {dest} failed" >&2',
                "    dmesg | tail -n 15 >&2 || true",
                "    exit 1",
                "  fi",
                "fi",
            ]
        )
    )


def _unmount_path(runner: Runner, path: str) -> None:
    """Unmount path when it is a mountpoint. A chroot bind is detached before the root."""
    quoted = shlex.quote(path)
    runner.shell(
        f"if mountpoint -q {quoted}; then umount -R {quoted} || umount -l {quoted} || true; fi"
    )


def _disk_teardown_script(cfg: InstallConfig, mode: str = "full") -> str:
    """Bash that swapoffs, unmounts, and optionally deactivates dm/LVM.

    Order matters: a swap *file* on btrfs `@swap` (or partition swap) keeps the
    device busy if umount runs first — the resume→reformat path hit that and
    mkfs.btrfs / subvolume create failed with device busy. Fail closed if the
    target mountpoint or any partition of the target disk stays busy.

    mode:
      full — pre-wipe / resume-fresh / unmount-for-reuse: also cryptsetup close,
             vgchange -an, and md stop.
      mounts_only — pre-mkfs: swapoff + umount only. Do **not** close LUKS or
             deactivate LVM/MD maps this phase just opened (H1).
    """
    if mode not in {"full", "mounts_only"}:
        raise ValueError(f"unknown disk teardown mode: {mode!r}")
    root = (cfg.mountpoint or "/mnt/gentoo").rstrip("/") or "/mnt/gentoo"
    disks = [d for d in (cfg.target_disk, cfg.extra_raid_disk) if d and str(d).startswith("/dev/")]
    qroot = shlex.quote(root)
    disk_list = " ".join(shlex.quote(d) for d in disks)
    lvm = cfg.layout in {"lvm", "lvm-luks"}
    md = cfg.layout == "mdraid1"
    lines = [
        "set +e",
        f'ROOT={qroot}',
        f'DISKS="{disk_list}"',
        'echo ">> teardown: swapoff before unmount (resume reformat / wipe)"',
        # Swap first — partition swap and @swap swapfiles both pin block devices.
        "swapoff -a 2>/dev/null || true",
        "if command -v swapon >/dev/null 2>&1; then",
        "  swapon --show=NAME --noheadings 2>/dev/null | while IFS= read -r sw; do",
        '    [ -n "$sw" ] || continue',
        '    swapoff "$sw" 2>/dev/null || true',
        "  done",
        "fi",
        'echo ">> teardown: unmount chroot binds and target tree under $ROOT"',
        # Deepest mounts first so btrfs subvols (@home, @swap, …) detach cleanly.
        'if [ -n "$ROOT" ] && [ "$ROOT" != "/" ]; then',
        '  findmnt -Rn -o TARGET --submounts "$ROOT" 2>/dev/null | awk \'{print length, $0}\' | sort -rn | while read -r _ mnt; do',
        '    [ -n "$mnt" ] || continue',
        '    umount "$mnt" 2>/dev/null || umount -l "$mnt" 2>/dev/null || true',
        "  done",
        '  if mountpoint -q "$ROOT" 2>/dev/null; then',
        '    umount -R "$ROOT" 2>/dev/null || umount -l "$ROOT" 2>/dev/null || true',
        "  fi",
        "fi",
        'echo ">> teardown: release mounts on target disk partitions"',
        'for disk in $DISKS; do',
        '  [ -b "$disk" ] || continue',
        '  for part in $(lsblk -ln -o PATH "$disk" 2>/dev/null || true); do',
        '    findmnt -rn -o TARGET -S "$part" 2>/dev/null | while IFS= read -r mnt; do',
        '      [ -n "$mnt" ] || continue',
        '      umount "$mnt" 2>/dev/null || umount -l "$mnt" 2>/dev/null || true',
        "    done",
        "  done",
        "done",
    ]
    if mode == "full":
        lines.extend(
            [
                'echo ">> teardown: close LUKS / deactivate LVM / stop MD if used"',
                "cryptsetup close swap 2>/dev/null || true",
                "cryptsetup close root 2>/dev/null || true",
            ]
        )
        if lvm:
            lines.append("vgchange -an vg0 2>/dev/null || true")
            lines.append("vgchange -an 2>/dev/null || true")
        if md:
            lines.append("mdadm --stop /dev/md/root 2>/dev/null || true")
            lines.append("mdadm --stop --scan 2>/dev/null || true")
    else:
        # Keep mapper/VG/MD nodes this phase owns so mkfs can target them.
        lines.append(
            'echo ">> teardown: mounts-only (keeping LUKS/LVM/MD maps open for mkfs)"'
        )
    lines.extend(
        [
            "udevadm settle --timeout=8 2>/dev/null || udevadm settle 2>/dev/null || true",
            "sleep 0.2 || true",
            # ---- fail closed (no pipe subshells mutating busy) ----
            "busy=0",
            'if [ -n "$ROOT" ] && [ "$ROOT" != "/" ] && mountpoint -q "$ROOT" 2>/dev/null; then',
            '  echo ">> $ROOT is still mounted after teardown" >&2',
            '  findmnt "$ROOT" >&2 || true',
            "  busy=1",
            "fi",
            'if [ -n "$ROOT" ] && [ "$ROOT" != "/" ]; then',
            '  still=$(findmnt -Rn -o TARGET --submounts "$ROOT" 2>/dev/null || true)',
            '  if [ -n "$still" ]; then',
            '    echo ">> still mounted under target:" >&2',
            '    printf "%s\\n" "$still" >&2',
            "    busy=1",
            "  fi",
            "fi",
            'for disk in $DISKS; do',
            '  [ -b "$disk" ] || continue',
            '  for part in $(lsblk -ln -o PATH "$disk" 2>/dev/null || true); do',
            '    if findmnt -rn -S "$part" >/dev/null 2>&1; then',
            '      echo ">> partition $part is still mounted:" >&2',
            '      findmnt -rn -S "$part" >&2 || true',
            "      busy=1",
            "    fi",
            '    if command -v swapon >/dev/null 2>&1 && swapon --show=NAME --noheadings 2>/dev/null | grep -Fxq "$part"; then',
            '      echo ">> swap is still active on $part (swapoff failed)" >&2',
            "      busy=1",
            "    fi",
            "  done",
            "done",
            'if [ "$busy" -ne 0 ]; then',
            '  echo ">> disk teardown failed: target still busy (swap or mounts)." >&2',
            '  echo ">> Close anything using the install mountpoint, run swapoff -a, then retry." >&2',
            '  echo ">> Cannot wipe or format while devices are in use." >&2',
            "  exit 1",
            "fi",
            'echo ">> teardown: target disk is idle"',
        ]
    )
    return "\n".join(lines) + "\n"


def _teardown_disk_for_reformat(
    cfg: InstallConfig, runner: Runner, mode: str = "full"
) -> None:
    """Fail-closed teardown before wipe/mkfs (including resume → format again).

    Use mode=\"full\" before wipe / unmount-for-reuse. Use mode=\"mounts_only\"
    immediately before mkfs so LUKS/LVM maps opened in this phase stay open.
    """
    root = cfg.mountpoint.rstrip("/") or "/"
    _leave_tree(root)
    if mode == "mounts_only":
        ui.info(
            f"Unmounting swap and mounts under {root} before format "
            "(keeping LUKS/LVM maps open)"
        )
    else:
        ui.info(
            f"Tearing down swap and mounts under {root} before wipe/format "
            "(required after a previous install / resume restart)"
        )
    runner.shell(_disk_teardown_script(cfg, mode=mode), check=True)


def _assert_mkfs_block_devices(runner: Runner, devices: dict[str, str], cfg: InstallConfig) -> None:
    """Fail closed if root (and swap, when formatting it) is not a live block device."""
    nodes: list[tuple[str, str]] = []
    root = devices.get("root") or ""
    if root and str(root).startswith("/dev/"):
        nodes.append(("root", str(root)))
    swap = devices.get("swap") or ""
    if (
        swap
        and str(swap).startswith("/dev/")
        and cfg.swap_mode in {"partition", "both"}
    ):
        nodes.append(("swap", str(swap)))
    for role, node in nodes:
        quoted = shlex.quote(node)
        runner.shell(
            f'test -b {quoted} || {{ echo ">> mkfs: expected {role} block device missing: {node}" >&2; exit 1; }}',
            check=True,
        )


def _unmount_target(cfg: InstallConfig, runner: Runner) -> None:
    """Drop swap, chroot binds, target mounts, LUKS, and LVM so the disk can be reused.

    Used before wipe/reformat and when declining resume. Fail closed if busy.
    """
    _teardown_disk_for_reformat(cfg, runner, mode="full")


class RestartFromBeginning(Exception):
    """The user declined resume, and the failed target has been unmounted."""


def _target_mounted(mountpoint: str) -> bool:
    """True when the install root or one of its chroot binds is still mounted."""
    if not mountpoint:
        return False
    root = Path(mountpoint)
    if detect.is_mounted(mountpoint):
        return True
    return any(detect.is_mounted(str(root / name)) for name in ("proc", "sys", "dev", "run", "boot"))


def _chroot_binds_mounted(mountpoint: str) -> bool:
    root = Path(mountpoint)
    return any(detect.is_mounted(str(root / name)) for name in ("proc", "sys", "dev", "run"))


def failed_install_is_mounted(cfg: InstallConfig) -> bool:
    """A failed install left this mountpoint mounted. A finished install does not count."""
    if not detect.is_linux() or not _target_mounted(cfg.mountpoint):
        return False
    return "done" not in State(cfg.state_file).done


def _find_failed_mount(cfg: InstallConfig) -> InstallConfig | None:
    """The mounted config to unmount or resume. Saved profiles supply the real mountpoint."""
    if cfg.dry_run or not detect.is_linux():
        return None
    ordered: list[InstallConfig] = [cfg]
    for path in (Path("gentoo-install.yaml"), Path("/var/tmp/gentoo-install.yaml")):
        if not path.is_file():
            continue
        try:
            loaded = InstallConfig.load(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        ordered.append(loaded)
    failed = [item for item in ordered if item.mountpoint and failed_install_is_mounted(item)]
    if not failed:
        return None
    same = [item for item in failed if item.mountpoint == cfg.mountpoint]
    group = same or failed
    # A blank session config shares /mnt/gentoo with the saved profile. Prefer
    # the profile that still knows the target disk, or resume reinstalls defaults.
    with_disk = [item for item in group if item.target_disk]
    return with_disk[0] if with_disk else group[0]


def _clear_install_state(cfg: InstallConfig) -> None:
    path = Path(cfg.state_file)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        ui.warn(f"Could not remove {path}: {exc}")


def _unmount_failed_target(cfg: InstallConfig) -> None:
    """Unmount a failed chroot. Stop if the tree is still mounted."""
    _shred_install_secrets(cfg.mountpoint)
    runner = Runner(dry_run=False, secrets=_secret_values(cfg))
    _unmount_target(cfg, runner)
    if _target_mounted(cfg.mountpoint):
        ui.error(
            f"{cfg.mountpoint} is still mounted. Close anything using it, then start the installer again."
        )
        raise SystemExit(1)
    _clear_install_state(cfg)
    ui.ok(f"Unmounted {cfg.mountpoint}.")


def _copy_config(dst: InstallConfig, src: InstallConfig) -> None:
    """Replace dst's fields with src's. Used when resume finds a saved profile."""
    from dataclasses import asdict

    for key, value in asdict(src).items():
        setattr(dst, key, value)


def adopt_resume_profile(cfg: InstallConfig) -> None:
    """Fill an empty session from gentoo-install.yaml when that install is the one on disk."""
    if cfg.target_disk:
        return
    mounted = _find_failed_mount(cfg)
    if mounted is None or mounted is cfg or not mounted.target_disk:
        for path in (Path("gentoo-install.yaml"), Path("/var/tmp/gentoo-install.yaml")):
            if not path.is_file():
                continue
            try:
                loaded = InstallConfig.load(path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if loaded.target_disk:
                _copy_config(cfg, loaded)
                return
        return
    _copy_config(cfg, mounted)


def ask_resume_or_unmount(cfg: InstallConfig) -> str:
    """Ask what to do with a failed, still-mounted install.

    Returns "resume", "fresh", or "none". "fresh" means the target was unmounted
    and the caller should start the wizard from the first question.

    Always shreds `.install-secrets` on the failed mount (including resume) so
    passwords are not left on-disk while the tree stays mounted for debugging.
    """
    mounted = _find_failed_mount(cfg)
    if mounted is None:
        return "none"
    # Default fail-closed: shred before resume/unmount (H3).
    _shred_install_secrets(mounted.mountpoint)
    if _chroot_binds_mounted(mounted.mountpoint):
        ui.warn(f"The install did not finish. {mounted.mountpoint} is still chrooted and mounted.")
    else:
        ui.warn(f"The install did not finish. {mounted.mountpoint} is still mounted.")
    if ui.ask_yes_no("Resume the interrupted install?", True):
        if mounted is not cfg and mounted.target_disk:
            _copy_config(cfg, mounted)
        cfg.resume = True
        cfg.wipe_disk = False
        setattr(cfg, "_resume_decided", True)
        ui.ok("Resume enabled. Existing mounts stay in place.")
        return "resume"
    ui.warn("The target will be unmounted, and the installer will start from the beginning.")
    _unmount_failed_target(mounted)
    cfg.resume = False
    setattr(cfg, "_resume_decided", True)
    ui.info("Starting the installer from the beginning.")
    return "fresh"


def _luks_open_only(cfg: InstallConfig, runner: Runner, device: str, name: str) -> None:
    mapper = f"/dev/mapper/{name}"
    if Path(mapper).exists():
        return
    if not cfg.luks_passphrase:
        raise SystemExit(f"LUKS passphrase missing; cannot open {device} as {name}.")
    runner.set_secrets(_secret_values(cfg))
    runner.run(
        ["cryptsetup", "open", "--key-file=-", device, name],
        input_text=cfg.luks_passphrase,
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
            efi = _efi_mount(cfg)
            if not _mountpoint_q(efi):
                _mount_if_needed(runner, devices["esp"], efi, fstype="vfat")
    cfg._devices = devices  # type: ignore[attr-defined]
    ui.ok("Target filesystems are mounted")


def _disk_phase_target_paths(cfg: InstallConfig) -> list[str]:
    """Block devices phase_disks may wipefs / partition / mkfs / luksFormat."""
    paths = [
        cfg.target_disk,
        cfg.extra_raid_disk,
        cfg.existing_esp,
        cfg.existing_boot,
        cfg.existing_root,
        cfg.existing_swap,
    ]
    paths.extend(getattr(cfg, "wipe_partitions", None) or [])
    return [p for p in paths if p]


def refuse_live_installer_targets(cfg: InstallConfig) -> None:
    """H7: fail closed before wipefs/sgdisk/mkfs if any target is the live USB."""
    for path in _disk_phase_target_paths(cfg):
        if detect.is_live_installer_disk(path):
            raise SystemExit(
                f"Refusing to wipe or format the live installer medium ({path}). "
                "Choose a different target disk."
            )


def assert_wipe_layout_consistent(cfg: InstallConfig) -> None:
    """M14: wipe_disk=false must use layout=existing (no silent handbook geometry)."""
    if cfg.wipe_disk or cfg.layout == "existing":
        return
    raise SystemExit(
        f"wipe_disk is false but layout is {cfg.layout!r}. "
        "Set layout to existing (and name ESP/root/swap) or enable wipe_disk "
        "to create a new partition table."
    )


# Expected TYPE= values (blkid) for mount-existing fail-closed checks.
_FSTYPE_ALIASES: dict[str, frozenset[str]] = {
    "ext4": frozenset({"ext4"}),
    "xfs": frozenset({"xfs"}),
    "btrfs": frozenset({"btrfs"}),
    "f2fs": frozenset({"f2fs"}),
    "jfs": frozenset({"jfs"}),
    "vfat": frozenset({"vfat", "fat", "fat32", "fat16", "msdos"}),
    "swap": frozenset({"swap"}),
    "crypto_LUKS": frozenset({"crypto_luks"}),
}


def _fstype_matches(actual: str, expected: str) -> bool:
    want = (expected or "").strip().lower()
    got = (actual or "").strip().lower()
    if not want:
        return False
    aliases = _FSTYPE_ALIASES.get(want, frozenset({want}))
    return got in aliases


def assert_mount_existing_fstypes(cfg: InstallConfig, devices: dict[str, str]) -> None:
    """H8-B: fail closed when on-disk TYPE does not match the chosen filesystem."""
    checks: list[tuple[str, str, str]] = []
    root = devices.get("root") or ""
    if root:
        checks.append(("root", root, _root_fstype(cfg)))
    if cfg.firmware == "uefi" and devices.get("esp"):
        checks.append(("ESP", devices["esp"], "vfat"))
    boot = devices.get("boot") or ""
    if boot and boot not in {devices.get("esp"), devices.get("root")}:
        checks.append(("boot", boot, "ext4"))
    swap = devices.get("swap") or ""
    if swap and cfg.swap_mode in {"partition", "both"}:
        checks.append(("swap", swap, "swap"))
    for role, node, expected in checks:
        actual = detect.blkid_type(node)
        if not actual:
            raise SystemExit(
                f"mount-existing: could not read filesystem TYPE on {role} ({node}). "
                "Refuse to mount without a matching TYPE."
            )
        if not _fstype_matches(actual, expected):
            raise SystemExit(
                f"mount-existing: {role} {node} has TYPE={actual!r}, "
                f"expected {expected!r}. Refusing to mount (no mkfs)."
            )


def _phase_disks_mount_existing(cfg: InstallConfig, runner: Runner) -> None:
    """H8-B expert path: open LUKS if needed, assert fstypes, mount — never mkfs/luksFormat."""
    if cfg.layout != "existing":
        raise SystemExit(
            f"wipe_mode=mount requires layout=existing (got {cfg.layout!r})."
        )
    if cfg.wipe_disk or getattr(cfg, "wipe_partitions", None):
        raise SystemExit(
            "wipe_mode=mount refuses wipe_disk / wipe_partitions "
            "(mount-existing must not wipe)."
        )
    ui.info("Expert mount-existing: skipping wipefs, mkfs, mkswap, and luksFormat.")
    devices = _map_devices(cfg)
    if cfg.luks and devices.get("luks_source"):
        # Container TYPE must be LUKS before open; FS TYPE is checked on the mapper.
        source = devices["luks_source"]
        if not cfg.dry_run:
            actual = detect.blkid_type(source)
            if actual and not _fstype_matches(actual, "crypto_LUKS"):
                raise SystemExit(
                    f"mount-existing: root source {source} has TYPE={actual!r}, "
                    "expected crypto_LUKS before open."
                )
        _luks_open_only(cfg, runner, source, "root")
        devices["root"] = "/dev/mapper/root"
    if (
        cfg.luks
        and cfg.encrypted_swap
        and devices.get("swap")
        and cfg.swap_mode in {"partition", "both"}
    ):
        devices["luks_swap_source"] = devices["swap"]
        if not cfg.dry_run:
            actual = detect.blkid_type(devices["luks_swap_source"])
            if actual and not _fstype_matches(actual, "crypto_LUKS"):
                raise SystemExit(
                    f"mount-existing: swap source {devices['luks_swap_source']} has "
                    f"TYPE={actual!r}, expected crypto_LUKS before open."
                )
        _luks_open_only(cfg, runner, devices["luks_swap_source"], "swap")
        devices["swap"] = "/dev/mapper/swap"
    if not cfg.dry_run:
        assert_mount_existing_fstypes(cfg, devices)
    else:
        ui.info("Dry-run: would assert on-disk fstypes match before mount.")
    if devices.get("swap") and cfg.swap_mode in {"partition", "both"}:
        runner.shell(f"swapon {shlex.quote(devices['swap'])} || true")
    _mount_tree(cfg, runner, devices, create_subvols=False)
    cfg._devices = devices  # type: ignore[attr-defined]
    ui.ok("Existing filesystems mounted (no format)")


def phase_disks(cfg: InstallConfig, runner: Runner) -> None:
    """Wipe/partition/LUKS/LVM, mkfs, and mount at cfg.mountpoint (handbook /mnt/gentoo)."""
    disk = cfg.target_disk
    if cfg.resume:
        ui.info("Resume: not wiping or formatting; ensuring the target is mounted.")
        _ensure_target_mounted(cfg, runner)
        return
    refuse_live_installer_targets(cfg)
    assert_wipe_layout_consistent(cfg)
    if cfg.wipe_mode == "mount":
        _phase_disks_mount_existing(cfg, runner)
        return
    # Selected-partition wipe (one, several, or all listed) before using existing layout.
    wipe_parts = [p for p in (getattr(cfg, "wipe_partitions", None) or []) if p]
    if wipe_parts and cfg.layout == "existing":
        _unmount_target(cfg, runner)
        for part in wipe_parts:
            ui.danger(f"Wiping partition {part}")
            runner.shell(f"wipefs -af {shlex.quote(part)} || true")
        runner.shell("udevadm settle || true")
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
        _partprobe_disk(runner, disk)
        if cfg.layout == "mdraid1" and cfg.extra_raid_disk:
            _partprobe_disk(runner, cfg.extra_raid_disk)
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
    if int(cfg.swap_size_gib or 0) < 1:
        return False
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
    """GPT: ESP or BIOS-boot, optional swap, then Linux root (8304, or 8309 if LUKS)."""
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
    ptype = _linux_root_type(cfg)
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
    _prepare_mountpoint(runner, cfg.mountpoint)
    runner.shell(f"zpool import -R {shlex.quote(cfg.mountpoint)} rpool")
    if cfg.firmware == "uefi":
        _mount_if_needed(runner, part_name(disk, 1), _efi_mount(cfg), fstype="vfat")


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


def _mkfs_ext4(device: str) -> str:
    """ext4 create. Handbook: partitions under 8 GiB need -T small or inodes run out."""
    quoted = shlex.quote(device)
    return (
        f"bytes=$(blockdev --getsize64 {quoted} 2>/dev/null || echo 0); "
        f'if [ "$bytes" -gt 0 ] && [ "$bytes" -lt 8589934592 ]; then '
        f"mkfs.ext4 -F -T small {quoted}; else mkfs.ext4 -F {quoted}; fi"
    )


def _mkfs_xfs(device: str) -> str:
    """xfs create. Handbook: xfsprogs 6.18 defaults are not readable by the LTS kernel."""
    quoted = shlex.quote(device)
    conf = "/usr/share/xfsprogs/mkfs/lts_6.18.conf"
    return (
        f"if [ -f {shlex.quote(conf)} ]; then "
        f"mkfs.xfs -f -c options={shlex.quote(conf)} {quoted}; "
        f"else mkfs.xfs -f {quoted}; fi"
    )


def _mkfs(cfg: InstallConfig, runner: Runner, devices: dict[str, str]) -> None:
    """Format ESP as FAT32, swap with mkswap, root with the chosen filesystem."""
    # Mounts-only teardown: resume→fresh restart often leaves swap and btrfs
    # subvols mounted even when wipefs already ran earlier in this phase. Do
    # not cryptsetup close / vgchange -an maps this phase just opened (H1).
    _teardown_disk_for_reformat(cfg, runner, mode="mounts_only")
    _assert_mkfs_block_devices(runner, devices, cfg)
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
        runner.shell(_mkfs_ext4(devices["boot"]))
    if devices.get("swap") and cfg.swap_mode in {"partition", "both"}:
        runner.shell(f"mkswap {shlex.quote(devices['swap'])}")
        runner.shell(f"swapon {shlex.quote(devices['swap'])} || true")
    root = devices["root"]
    fs = cfg.filesystem
    if fs == "ext4":
        runner.shell(_mkfs_ext4(root))
    elif fs == "xfs":
        runner.shell(_mkfs_xfs(root))
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
    _prepare_mountpoint(runner, root)
    if cfg.layout == "zfs":
        return
    if cfg.layout == "btrfs-subvols":
        if create_subvols and not _mountpoint_q(root):
            ui.info("Creating Btrfs subvolumes @ @home @log @cache @tmp @swap")
            _mount_if_needed(runner, devices["root"], root, "subvolid=5", fstype="btrfs")
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
    # Handbook: mount the root partition first. For UEFI, mkdir /mnt/gentoo/efi
    # and mount the ESP there. /boot is created by the stage3 tarball on the
    # root filesystem. Mounting the ESP on /boot before extract puts the stage
    # tarball's /boot onto FAT, which rejects the permissions tar tries to set.
    if cfg.firmware == "uefi" and devices.get("esp"):
        _mount_if_needed(runner, devices["esp"], _efi_mount(cfg), fstype="vfat")
    elif devices.get("boot") and devices["boot"] != devices.get("esp") and devices["boot"] != devices.get("root"):
        _mount_if_needed(runner, devices["boot"], root + "/boot", fstype="ext4")


# Public Gentoo mirrors. distfiles.gentoo.org is the handbook default.
# A wget status of 8 is an HTTP error (404 and other 4xx/5xx). The stage
# download and the chroot emerge wrapper both walk this list after a 404.
GENTOO_MIRROR_BASES = [
    "https://distfiles.gentoo.org",
    "https://gentoo.osuosl.org",
    "https://mirrors.kernel.org/gentoo",
    "https://mirror.leaseweb.com/gentoo",
    "https://ftp.fau.de/gentoo",
    "https://mirror.csclub.uwaterloo.ca/gentoo-distfiles",
    "https://mirrors.mit.edu/gentoo-distfiles",
    "https://ftp.jaist.ac.jp/pub/Linux/Gentoo",
    "https://mirrors.tuna.tsinghua.edu.cn/gentoo",
    "https://ftp.halifax.rwth-aachen.de/gentoo",
]


def _mirror_bases(cfg: InstallConfig) -> list[str]:
    """User mirror first, then the public list, with duplicates removed."""
    ordered: list[str] = []
    for raw in (cfg.gentoo_mirrors or "").split():
        base = raw.strip().rstrip("/")
        if base and base not in ordered:
            ordered.append(base)
    for base in GENTOO_MIRROR_BASES:
        if base not in ordered:
            ordered.append(base)
    return ordered


def _stage_relpath(text: str) -> str:
    """Relative tarball path from a latest-<flavor>.txt pointer, or ''."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if ".tar." in stripped and "stage" in stripped:
            return stripped.split()[0]
    return ""


def _wget(runner: Runner, dest: str, url: str, *, timeout: int = 60, tries: int = 2) -> int:
    """Download url to dest. Return wget's status. Non-zero removes a partial file."""
    if runner.dry_run:
        return 0
    proc = runner.shell(
        f"wget --timeout={timeout} --tries={tries} -O {shlex.quote(dest)} {shlex.quote(url)}",
        check=False,
    )
    if proc.returncode != 0 or not Path(dest).is_file() or Path(dest).stat().st_size == 0:
        runner.shell(f"rm -f {shlex.quote(dest)}", check=False)
        return proc.returncode or 1
    return 0


def _download_trying_mirrors(
    runner: Runner,
    bases: list[str],
    relpath: str,
    dest: str,
    *,
    timeout: int = 120,
) -> str:
    """Save relpath to dest. Return the mirror base that served it.

    wget status 8 is the HTTP error emerge and the stage fetch both see as 404.
    Any other failure also moves on, so one dead mirror does not stop the install.
    """
    relpath = relpath.lstrip("/")
    last = 1
    saw_404 = False
    for base in bases:
        url = f"{base}/{relpath}"
        ui.info(f"Fetching {url}")
        last = _wget(runner, dest, url, timeout=timeout)
        if last == 0:
            ui.ok(f"Downloaded from {base}")
            return base
        if last == 8:
            saw_404 = True
            ui.warn(f"HTTP 404 from {base}; checking the next mirror")
        else:
            ui.warn(f"{base} did not serve that file (wget {last}); checking the next mirror")
    name = relpath.split("/")[-1]
    if saw_404:
        raise SystemExit(f"HTTP 404 for {name} on every Gentoo mirror that was checked.")
    raise SystemExit(f"Could not download {name} (last wget status {last}).")


def _resolve_stage(cfg: InstallConfig, runner: Runner, root: str, flavor: str) -> tuple[str, str, str]:
    """Return (tarball filename, pointer-relative path, mirror that listed it)."""
    bases = _mirror_bases(cfg)
    if cfg.dry_run:
        rel = f"DATE/{flavor}-DATE.tar.xz"
        return f"{flavor}-DATE.tar.xz", rel, bases[0]
    pointer = f"{root}/latest-stage.txt"
    arch = cfg.architecture
    saw_404 = False
    for base in bases:
        ui.info(f"Resolving latest {flavor} from {base}")
        code = _wget(
            runner,
            pointer,
            f"{base}/releases/{arch}/autobuilds/latest-{flavor}.txt",
            timeout=30,
            tries=1,
        )
        if code == 8:
            saw_404 = True
            ui.warn(f"HTTP 404 from {base}; checking the next mirror")
            continue
        if code != 0:
            ui.warn(f"{base} did not answer (wget {code}); checking the next mirror")
            continue
        rel = _stage_relpath(Path(pointer).read_text(encoding="utf-8", errors="ignore"))
        if not rel:
            ui.warn(f"{base} did not list a {flavor} tarball; checking the next mirror")
            continue
        return rel.split("/")[-1], rel, base
    if saw_404:
        raise SystemExit(
            f"HTTP 404 for latest-{flavor}.txt on every Gentoo mirror that was checked. "
            "The installer moved to the next mirror after each 404. "
            "Public autobuilds provide stage3 files. Stage1 and stage2 tarballs are not on that "
            "mirror network, so those names answer 404 on every mirror."
        )
    raise SystemExit(f"Could not resolve latest-{flavor}.txt from any Gentoo mirror.")


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
    """Download the current stage tarball, optionally verify GPG, extract with xattrs.

    A 404 on latest-<flavor>.txt or the tarball itself tries the next mirror
    before the install stops. Stage2 names 404 on distfiles.gentoo.org because
    Gentoo does not publish them; the same check still runs on the other mirrors.
    """
    if cfg.resume and (Path(root) / "etc" / "portage").is_dir():
        ui.info("Resume: stage tarball already extracted; skipping download.")
        return
    flavor = stage_flavor(cfg)
    _prepare_mountpoint(runner, root)
    tarball, rel, used = _resolve_stage(cfg, runner, root, flavor)
    if not cfg.dry_run:
        bases = _mirror_bases(cfg)
        ordered = [used] + [base for base in bases if base != used]
        arch_rel = f"releases/{cfg.architecture}/autobuilds/{rel}"
        _download_trying_mirrors(runner, ordered, arch_rel, root + "/" + tarball)
        if cfg.verify_stage:
            # .sha256 is often a PGP-clearsigned file, not raw sha256sum input.
            # The detached .asc on the tarball is the check that must succeed.
            _download_trying_mirrors(
                runner,
                ordered,
                arch_rel + ".asc",
                root + "/" + tarball + ".asc",
                timeout=60,
            )
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
    ui.ok("Stage tarball extracted")


def phase_chroot_mounts(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Bind /proc /sys /dev /run and copy resolv.conf — handbook 'mounting the necessary filesystems'."""
    proc = shlex.quote(root + "/proc")
    sysfs = shlex.quote(root + "/sys")
    dev = shlex.quote(root + "/dev")
    run = shlex.quote(root + "/run")
    # Handbook, non-Gentoo live media: /dev/shm is often a symlink to /run/shm.
    # After rbind that link points nowhere inside the chroot.
    runner.shell(
        "\n".join(
            [
                "if [ -L /dev/shm ]; then rm /dev/shm && mkdir /dev/shm; fi",
                "if ! mountpoint -q /dev/shm; then mount --types tmpfs --options nosuid,nodev,noexec shm /dev/shm || true; fi",
                "chmod 1777 /dev/shm || true",
                "if [ -d /run/shm ]; then chmod 1777 /run/shm || true; fi",
            ]
        )
    )
    _prepare_mountpoint(runner, root + "/proc")
    _prepare_mountpoint(runner, root + "/sys")
    _prepare_mountpoint(runner, root + "/dev")
    _prepare_mountpoint(runner, root + "/run")
    runner.shell(f"mountpoint -q {proc} || mount --types proc /proc {proc}")
    runner.shell(f"mountpoint -q {sysfs} || mount --rbind /sys {sysfs}")
    runner.shell(f"mount --make-rslave {sysfs} || true")
    runner.shell(f"mountpoint -q {dev} || mount --rbind /dev {dev}")
    runner.shell(f"mount --make-rslave {dev} || true")
    runner.shell(f"mountpoint -q {run} || mount --bind /run {run} || true")
    runner.shell(f"mount --make-slave {run} || true")
    runner.shell(f"cp /etc/resolv.conf {shlex.quote(root + '/etc/resolv.conf')} || true")


def _chroot_env_argv(*command: str, locale: str = "C") -> list[str]:
    """env -i + a command inside the Gentoo tree.

    Use bare ``C`` only for pre-locale-gen helpers (localedef needs a safe
    locale). After ``_generate_locales``, pass a concrete UTF-8 locale so the
    main chroot-install.sh / emerge path is not launched under ambiguous C —
    Portage warns (and some emerges fail) when LANG/LC_ALL are bare C/POSIX.
    """
    term = os.environ.get("TERM") or "linux"
    loc = (locale or "C").strip() or "C"
    return [
        "env",
        "-i",
        "HOME=/root",
        f"TERM={term}",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        f"LANG={loc}",
        f"LC_ALL={loc}",
        *command,
    ]


def phase_chroot(
    cfg: InstallConfig,
    runner: Runner,
    root: str,
) -> None:
    """Write config files + chroot-install.sh, generate locales, then chroot."""
    _apply_live_firmware(cfg)
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
    secrets = _install_secrets_path(root)
    secrets.parent.mkdir(parents=True, exist_ok=True)
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
    runner.set_secrets(_secret_values(cfg))
    # Handbook + wiki "Installation alternatives": LiveCDs leak a messy env.
    # Never bash --login / never source /etc/profile in the installer process.
    # Locales were just generated above — launch the install script under UTF-8,
    # not bare C (that wrapper previously broke emerge of e.g. dev-vcs/git).
    chroot_argv = _chroot_env_argv(
        "chroot",
        root,
        "/bin/bash",
        "/root/chroot-install.sh",
        locale=_installer_utf8_locale(cfg),
    )
    # Stream emerge output; no percent / sticky bar — the pre-install nap notice
    # is the UX for the long wait. Always shred secrets afterward — Runner may
    # sys.exit on failure, and finally still runs for SystemExit (H3).
    try:
        runner.run_live(chroot_argv, lambda _line: None)
    finally:
        _shred_install_secrets(root)


# Not a Portage FEATURE. Valid relatives: getbinpkg, binpkg-request-signature.
# --binpkg-respect-use is an emerge option only (EMERGE_DEFAULT_OPTS).
_BAD_FEATURE = "binpkg-request-use"
# Only valid FEATURE names. Naming binpkg-request-use even with a leading '-'
# can still trip unknown-features-warn on some Portage builds.
_FEATURES_BASE = "-unknown-features-warn unknown-features-filter"
# Backward-compatible alias used by legacy FEATURES sanitizers / tests.
_FEATURES_SANITIZE = f'FEATURES="{_FEATURES_BASE}"'


def _features_line(cfg: InstallConfig) -> str:
    """Handbook/Quickstart: binhost adds getbinpkg + binpkg-request-signature."""
    feats = _FEATURES_BASE
    if cfg.binhost:
        feats = f"{feats} getbinpkg binpkg-request-signature"
    return f'FEATURES="{feats}"'


def _binrepos_conf_text(cfg: InstallConfig) -> str:
    """Official binhost repo file (Handbook Base + Binary Host Quickstart).

    Always includes verify-signature + location. When the user picks x86-64-v3,
    handbook Important requires *both* the base x86-64 and the v3 URI so missing
    v3 binpkgs can fall back.
    """
    if not cfg.binhost or cfg.architecture != "amd64":
        return ""
    abi = (cfg.binhost_abi or "x86-64").strip() or "x86-64"
    base = """[binhost]
priority = 9998
sync-uri = https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/
verify-signature = true
location = /var/cache/binhost/gentoo
"""
    if abi == "x86-64-v3":
        return (
            base
            + """
[binhost-x86-64-v3]
priority = 9999
sync-uri = https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64-v3/
verify-signature = true
location = /var/cache/binhost/gentoo-x86-64-v3
"""
        )
    if abi != "x86-64":
        # Unknown ABI token — still emit a single matching URI (legacy behaviour).
        return f"""[binhost]
priority = 9999
sync-uri = https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/{abi}/
verify-signature = true
location = /var/cache/binhost/gentoo
"""
    return base.replace("priority = 9998", "priority = 9999")


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
        if cfg.firmware != "uefi":
            grub_plat = 'GRUB_PLATFORMS="pc"'
        elif cfg.architecture != "arm64" and cfg.uefi_bits == 32:
            grub_plat = 'GRUB_PLATFORMS="efi-32"'
        else:
            grub_plat = 'GRUB_PLATFORMS="efi-64"'
    else:
        grub_plat = ""
    mirrors = cfg.gentoo_mirrors.strip()
    cflags = "-march=native -O2 -pipe"
    if cfg.hardened_flags:
        cflags += " -fstack-protector-strong -fstack-clash-protection"
    if cfg.lto:
        cflags += " -flto"
    # Never put --binpkg-respect-use in EMERGE_DEFAULT_OPTS: stage3 Portage may
    # not know it, and that makes every emerge exit before it builds anything.
    # After profile, portage_prepare detects support and sets $RESPECT_USE for
    # later emerge_one / emerge_keep_going calls. Pre-git sanitize strips any
    # leftover from older make.conf / resume copies before the first emerge.
    emerge_opts = f"--jobs={max(1, jobs // 2)} --load-average={jobs} --ask=n"
    if cfg.binhost:
        emerge_opts += " --getbinpkg --usepkg"
    l10n, linguas = _l10n_linguas(cfg)
    lines = [
        f'COMMON_FLAGS="{cflags}"',
        'CFLAGS="${COMMON_FLAGS}"',
        'CXXFLAGS="${COMMON_FLAGS}"',
        'FCFLAGS="${COMMON_FLAGS}"',
        'FFLAGS="${COMMON_FLAGS}"',
        f'MAKEOPTS="-j{jobs}"',
        f'EMERGE_DEFAULT_OPTS="{emerge_opts}"',
        _features_line(cfg),
        f'USE="{" ".join(sorted(set(use)))}"',
        f'ACCEPT_LICENSE="{cfg.accept_license}"',
        f'VIDEO_CARDS="{video}"',
        f'INPUT_DEVICES="{(cfg.input_devices or detect.suggest_input_devices()).strip() or "libinput"}"',
    ]
    if mirrors:
        lines.append(f'GENTOO_MIRRORS="{mirrors}"')
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
    unmask = _unmask_text(cfg)
    if unmask.strip():
        um_dir = portage / "package.unmask"
        um_dir.mkdir(parents=True, exist_ok=True)
        _write_text(um_dir / "installer", unmask, keep_existing=False)
    # Overlay helpers + */*::overlay before chroot emerges eselect-repository/git
    # or any overlay package (handbook accept_keywords / package.unmask).
    _write_overlay_portage_files(cfg, root)


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
    # Prefer a concrete UTF-8 locale (never bare C/POSIX) so Portage and the
    # installed system do not inherit an ambiguous C locale.
    for loc in [_installer_utf8_locale(cfg), cfg.locale, *extras, "C.UTF-8"]:
        loc = (loc or "").strip()
        if loc in {"", "C", "POSIX", "custom"}:
            continue
        if loc not in seen:
            seen.append(loc)
    if not seen:
        seen.append("C.UTF-8")
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
    # Never persist bare C/POSIX — Portage treats that as an ambiguous locale.
    loc = _installer_utf8_locale(cfg)
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
    _write_dns_config(cfg, rootp, keep_existing=keep)
    ui.ok("Wrote hostname, timezone, keymap, locale.gen, env.d/02locale, and DNS")


def _write_dns_config(cfg: InstallConfig, rootp: Path, *, keep_existing: bool) -> None:
    """Persist wizard DNS on the installed Gentoo (resolv.conf + stack helpers)."""
    etc = rootp / "etc"
    servers = _dns_nameservers(cfg)
    _write_text(etc / "resolv.conf", _resolv_conf_text(cfg), keep_existing=keep_existing)
    # NetworkManager would rewrite resolv.conf from DHCP unless dns=none.
    nm_dir = etc / "NetworkManager" / "conf.d"
    nm_dir.mkdir(parents=True, exist_ok=True)
    _write_text(
        nm_dir / "90-oops-dns-none.conf",
        "[main]\ndns=none\n",
        keep_existing=keep_existing,
    )
    # dhcpcd static nameservers (used when network_stack is dhcpcd).
    dhcpcd = etc / "dhcpcd.conf"
    dns_line = "static domain_name_servers=" + " ".join(servers)
    marker = "# oops-installer-dns"
    body = ""
    if dhcpcd.is_file():
        body = dhcpcd.read_text(encoding="utf-8", errors="replace")
    if marker not in body:
        body = body.rstrip() + f"\n{marker}\n{dns_line}\n"
        _write_text(dhcpcd, body if body.endswith("\n") else body + "\n", keep_existing=False)
    # systemd-resolved drop-in when using systemd.
    if cfg.init_system == "systemd":
        resolved = etc / "systemd" / "resolved.conf.d"
        resolved.mkdir(parents=True, exist_ok=True)
        _write_text(
            resolved / "oops-dns.conf",
            "[Resolve]\nDNS=" + " ".join(servers) + "\n",
            keep_existing=keep_existing,
        )
    ui.info(f"Installed DNS ({cfg.dns_provider}): {' '.join(servers)}")


def _generate_locales(cfg: InstallConfig, runner: Runner, root: str) -> None:
    """Handbook-style locale-gen, then activate UTF-8 before any locale set/emerge.

    ``locale-gen`` runs under bare C (safe while archives are missing). After
    that, export LANG/LC_ALL to a concrete UTF-8 locale and only then run
    ``eselect locale set`` — otherwise Portage/localectl warn about an
    ambiguous C locale. Never abort the installer on soft locale failures.
    """
    if cfg.stage_variant == "musl":
        ui.info("musl stage: skipping glibc locale-gen")
        return
    utf8 = _installer_utf8_locale(cfg)
    loc = shlex.quote(utf8)
    short = shlex.quote(utf8.split(".")[0])
    ui.chapter(
        "8",
        f"Generating locales → {utf8}",
        "https://wiki.gentoo.org/wiki/Handbook:AMD64/Installation/System",
    )
    # Step 1: localedef under C (archives not ready yet).
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
    # Step 2: activate UTF-8 in-env, then eselect (never leave LANG=C here).
    runner.run(
        _chroot_env_argv(
            "chroot",
            root,
            "/bin/bash",
            "-c",
            (
                f"export LANG={loc}; export LC_ALL={loc}; "
                f'case "$LANG" in C|POSIX|"") '
                f'echo ">> refuse locale set under bare LANG=$LANG" >&2; exit 1 ;; esac; '
                f"eselect locale set {loc} || eselect locale set {short} || true; "
                f'echo ">> host locale-gen done; LANG=$LANG LC_ALL=$LC_ALL"'
            ),
            locale=utf8,
        ),
        check=False,
    )


def phase_done(_cfg: InstallConfig, _runner: Runner, _root: str) -> None:
    """Mark the install finished. Power-down instructions are printed after 100%."""


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


def _emerge_checked_call(atom: str, test: tuple[str, ...], *, required: bool = False) -> str:
    """Soft-fail by default; required=True aborts the chroot if the probe still fails."""
    args = " ".join(shlex.quote(part) for part in (atom, *test))
    if required:
        return (
            f"emerge_checked {args} || {{ "
            f'echo ">> required package missing after emerge: {atom}" >&2; exit 1; }}'
        )
    return f"emerge_checked {args} || true"


def _probe_to_shell_test(test: tuple[str, ...]) -> str:
    """Turn an emerge_checked argv probe into a bash condition for final checks."""
    if not test:
        return "false"
    if test[0] == "command" and len(test) >= 3 and test[1] == "-v":
        return f"command -v {shlex.quote(test[2])} >/dev/null 2>&1"
    if test[0] == "test":
        return " ".join(shlex.quote(part) for part in test)
    if test[0] == "bash" and len(test) >= 3 and test[1] == "-c":
        return f"{{ {test[2]}; }}"
    if test[0] == "getent":
        return " ".join(shlex.quote(part) for part in test) + " >/dev/null 2>&1"
    return " ".join(shlex.quote(part) for part in test)


def required_atom_plan(cfg: InstallConfig) -> list[str]:
    """Service/network atoms shared by keywords, emerge, and H5 final checks (M4).

    Single ordered list so package.accept_keywords, the chroot emerge fetch set, and
    fail-closed probes cannot silently drift (elogind, nftables, …).
    """
    atoms: list[str] = []
    if cfg.network_stack == "networkmanager":
        atoms.extend(["net-misc/networkmanager", "sys-apps/dbus"])
        if cfg.init_system == "openrc":
            atoms.append("sys-auth/elogind")
    elif cfg.network_stack == "dhcpcd":
        atoms.append("net-misc/dhcpcd")
    elif cfg.network_stack == "systemd-networkd":
        atoms.append("sys-apps/systemd")
    if cfg.wireless:
        atoms.extend(["net-wireless/iw", "net-wireless/wpa_supplicant"])
    if cfg.sshd:
        atoms.append("net-misc/openssh")
    if cfg.cups:
        atoms.append("net-print/cups")
    if cfg.chrony:
        atoms.append("net-misc/chrony")
    if cfg.init_system == "openrc":
        if cfg.syslog != "none":
            atoms.append(f"app-admin/{cfg.syslog}")
        if cfg.cron != "none":
            atoms.append(f"sys-process/{cfg.cron}")
    if cfg.zram:
        atoms.append(
            "sys-apps/zram-generator" if cfg.init_system == "systemd" else "sys-block/zram-init"
        )
    if cfg.nftables:
        atoms.append("net-firewall/nftables")
    elif cfg.ufw:
        atoms.append("net-firewall/ufw")
    return atoms


def _critical_probe_for(cfg: InstallConfig, atom: str) -> tuple[str, ...] | None:
    """Probe for an H5 fail-closed service atom; None if the atom is plan-only support."""
    openrc = cfg.init_system == "openrc"
    if atom == "net-misc/networkmanager":
        return _service_probe(openrc, "NetworkManager")
    if atom == "net-misc/dhcpcd":
        return _service_probe(openrc, "dhcpcd")
    if atom == "sys-apps/systemd" and cfg.network_stack == "systemd-networkd":
        return ("command", "-v", "networkctl")
    if atom == "net-misc/chrony":
        return _service_probe(openrc, "chronyd")
    if atom == "net-firewall/nftables":
        return ("command", "-v", "nft")
    if atom == "net-firewall/ufw":
        return ("command", "-v", "ufw")
    if openrc and atom.startswith("app-admin/") and atom == f"app-admin/{cfg.syslog}":
        return _service_probe(True, cfg.syslog)
    if openrc and atom.startswith("sys-process/") and atom == f"sys-process/{cfg.cron}":
        svc = "cronie" if cfg.cron == "cronie" else cfg.cron
        return _service_probe(True, svc)
    return None


def _critical_service_tools(cfg: InstallConfig) -> list[tuple[str, tuple[str, ...]]]:
    """Network / chrony / firewall / syslog / cron — fail closed for install success (H5).

    Derived from required_atom_plan so keywords/emerge/checks stay aligned (M4).
    Target Gentoo chronyd when cfg.chrony is enabled. Never implies removing chrony.
    """
    tools: list[tuple[str, tuple[str, ...]]] = []
    for atom in required_atom_plan(cfg):
        probe = _critical_probe_for(cfg, atom)
        if probe is not None:
            tools.append((atom, probe))
    return tools


def _critical_service_final_checks(cfg: InstallConfig) -> list[tuple[str, str]]:
    """(shell test, label) for H5 services — missing sets missing=1."""
    checks: list[tuple[str, str]] = []
    for atom, test in _critical_service_tools(cfg):
        label = {
            "net-misc/networkmanager": "NetworkManager",
            "net-misc/dhcpcd": "dhcpcd",
            "sys-apps/systemd": "systemd-networkd",
            "net-misc/chrony": "chronyd",
            "net-firewall/nftables": "nftables",
            "net-firewall/ufw": "ufw",
        }.get(atom)
        if label is None:
            if atom.startswith("app-admin/"):
                label = atom.rsplit("/", 1)[-1]
            elif atom.startswith("sys-process/"):
                label = "cronie" if atom.endswith("/cronie") else atom.rsplit("/", 1)[-1]
            else:
                label = atom
        checks.append((_probe_to_shell_test(test), label))
    openrc = cfg.init_system == "openrc"
    if cfg.nftables and openrc:
        checks.append(("[ -e /etc/init.d/nftables ]", "nftables init"))
    if cfg.ufw and not cfg.nftables and openrc:
        checks.append(("[ -e /etc/init.d/ufw ]", "ufw init"))
    return checks


def _wm_exec_binary(cfg: InstallConfig, atom: str) -> str:
    """First token of the session Exec= for this compositor/WM atom."""
    sessions = catalog.login_sessions(
        cfg.desktop, [atom], getattr(cfg, "shell", "") or ""
    )
    if sessions:
        return (sessions[0][1] or "").split()[0] or atom.rsplit("/", 1)[-1]
    return atom.split("::", 1)[0].rsplit("/", 1)[-1]


def _required_window_manager_script(cfg: InstallConfig) -> str:
    """Emerge selected WMs with emerge_checked (no --noreplace) before the greeter (H6/M3)."""
    atoms = _window_managers(cfg)
    if not atoms:
        return ""
    lines = ['echo ">> required window managers / compositors"']
    for atom in atoms:
        binary = _wm_exec_binary(cfg, atom)
        if not binary:
            continue
        lines.append(
            _emerge_checked_call(atom, ("command", "-v", binary), required=True)
        )
    return "\n".join(lines) + "\n"


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
    add("app-editors/nano", "command", "-v", "nano")
    if cfg.nftables:
        add("net-firewall/nftables", "command", "-v", "nft")
    elif cfg.ufw:
        add("net-firewall/ufw", "command", "-v", "ufw")
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
        else:
            # Still emerge dbus so the NM stack is complete on systemd stages.
            add("sys-apps/dbus", "command", "-v", "dbus-daemon")
        add("net-misc/networkmanager", *_service_probe(openrc, "NetworkManager"))
    elif cfg.network_stack == "dhcpcd":
        add("net-misc/dhcpcd", *_service_probe(openrc, "dhcpcd"))
    elif cfg.network_stack == "systemd-networkd":
        add("sys-apps/systemd", "command", "-v", "networkctl")
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


def _atom_needs_kernel_sources(atom: str) -> bool:
    """True when this atom's ebuild expects /usr/src/linux (linux-mod / linux-info)."""
    return _bare_atom(atom) in KERNEL_SRC_DEPENDENT_ATOMS


def _pre_kernel_tools(
    cfg: InstallConfig, loaders: list[str]
) -> list[tuple[str, tuple[str, ...]]]:
    """Required tools that must exist before installkernel/dracut (not ZFS/modules)."""
    return [
        (atom, test)
        for atom, test in _required_tools(cfg, loaders)
        if not _atom_needs_kernel_sources(atom)
    ]


def _post_kernel_module_tools(
    cfg: InstallConfig, loaders: list[str]
) -> list[tuple[str, tuple[str, ...]]]:
    """Required tools that must wait until /usr/src/linux exists (e.g. sys-fs/zfs)."""
    return [
        (atom, test)
        for atom, test in _required_tools(cfg, loaders)
        if _atom_needs_kernel_sources(atom)
    ]


def _queued_needs_kernel_sources(
    cfg: InstallConfig, loaders: list[str], extra_atoms: list[str] | None = None
) -> bool:
    """True when any queued atom needs /usr/src/linux before its emerge."""
    if _post_kernel_module_tools(cfg, loaders):
        return True
    for atom in extra_atoms or []:
        if _atom_needs_kernel_sources(atom):
            return True
    for atom in dict.fromkeys(
        list(cfg.extra_packages or [])
        + list(cfg.selected_packages or [])
        + list(cfg.window_managers or [])
    ):
        if _atom_needs_kernel_sources(atom):
            return True
    return False


def _required_tool_repairs(cfg: InstallConfig, loaders: list[str]) -> str:
    """Install boot and service packages one failure at a time, before the kernel.

    A masked theme or a Plymouth build error used to sit in the same emerge
    as NetworkManager, chrony, rsyslog, cronie, and zram-init. Portage then
    aborted the whole calculation and rc-update reported every service missing.

    Critical services (H5) use emerge_checked required=True. emerge_checked never
    passes --noreplace, so resume still repairs packages that soft-failed earlier (M3).

    linux-mod consumers (ZFS, …) are deferred until after the kernel step so
    /usr/src/linux exists; see _post_kernel_module_repairs.
    """
    tools = _pre_kernel_tools(cfg, loaders)
    critical_atoms = {atom for atom, _test in _critical_service_tools(cfg)}
    lines = ["echo '>> install tools before dracut and OpenRC services'"]
    if tools:
        atoms = " ".join(shlex.quote(atom) for atom, _test in tools)
        lines.append(
            "emerge --ask=n --keep-going $RESPECT_USE $NR "
            + atoms
            + " || echo '>> some required packages failed; retrying one at a time' >&2"
        )
    openrc = cfg.init_system == "openrc"
    # NM OpenRC recovery before the hard emerge_checked so dbus/elogind can land first.
    if cfg.network_stack == "networkmanager" and openrc:
        lines.append(
            "\n".join(
                [
                    "if ! test -e /etc/init.d/NetworkManager; then",
                    '  echo ">> NetworkManager init script still missing; installing dbus and elogind first"',
                    "  emerge --ask=n $RESPECT_USE sys-apps/dbus || true",
                    "  emerge --ask=n $RESPECT_USE sys-auth/elogind || true",
                    "  emerge --ask=n --selective=n --usepkg=n --getbinpkg=n net-misc/networkmanager || "
                    "echo '>> emerge failed: net-misc/networkmanager' >&2",
                    "fi",
                ]
            )
        )
    for atom, test in tools:
        lines.append(
            _emerge_checked_call(atom, test, required=atom in critical_atoms)
        )
    return "\n".join(lines) + "\n"


def _post_kernel_module_repairs(cfg: InstallConfig, loaders: list[str]) -> str:
    """Emerge linux-mod consumers after kernel sources/symlink are in place."""
    tools = _post_kernel_module_tools(cfg, loaders)
    if not tools:
        return ""
    critical_atoms = {atom for atom, _test in _critical_service_tools(cfg)}
    lines = [
        'echo ">> emerge out-of-tree modules after /usr/src/linux"',
    ]
    atoms = " ".join(shlex.quote(atom) for atom, _test in tools)
    lines.append(
        "emerge --ask=n --keep-going $RESPECT_USE $NR "
        + atoms
        + " || echo '>> some module packages failed; retrying one at a time' >&2"
    )
    for atom, test in tools:
        lines.append(
            _emerge_checked_call(atom, test, required=atom in critical_atoms)
        )
    return "\n".join(lines) + "\n"


def _ensure_kernel_sources_script(
    cfg: InstallConfig, loaders: list[str], extra_atoms: list[str] | None = None
) -> str:
    """After kernel emerge: eselect/symlink /usr/src/linux; fail closed if modules need it."""
    need = _queued_needs_kernel_sources(cfg, loaders, extra_atoms)
    is_sources = catalog.kernel_package_name(cfg.kernel) == "gentoo-sources"
    if not need and not is_sources:
        return ""
    lines = [
        'echo ">> ensure /usr/src/linux"',
        "if [ ! -e /usr/src/linux ]; then",
        "  if command -v eselect >/dev/null 2>&1; then",
        "    if eselect kernel list 2>/dev/null | grep -qE '\\[[0-9]+\\]'; then",
        "      if ! eselect kernel list 2>/dev/null | grep -qE '\\[[0-9]+\\].*\\*'; then",
        "        eselect kernel set 1 || "
        "echo '>> eselect kernel set failed' >&2",
        "      fi",
        "    fi",
        "  fi",
        "fi",
    ]
    if need:
        lines.extend(
            [
                "if [ ! -e /usr/src/linux ]; then",
                '  echo ">> Unable to find kernel sources at /usr/src/linux '
                '(needed before ZFS / out-of-tree modules)" >&2',
                "  ls -la /usr/src 2>/dev/null || true",
                "  exit 1",
                "fi",
            ]
        )
    return "\n".join(lines) + "\n"


def _esp_removable_efi_name(cfg: InstallConfig) -> str:
    return "BOOTAA64.EFI" if cfg.architecture == "arm64" else "BOOTX64.EFI"


def _grub_branded_efi_name(cfg: InstallConfig) -> str:
    return "grubaa64.efi" if cfg.architecture == "arm64" else "grubx64.efi"


def _grub_boot_ok_test(cfg: InstallConfig) -> str:
    """GRUB success needs binaries plus grub.cfg (and ESP EFI on UEFI) — not command -v alone (M13)."""
    parts = [
        "command -v grub-install >/dev/null 2>&1",
        "command -v grub-mkconfig >/dev/null 2>&1",
        "[ -s /boot/grub/grub.cfg ]",
    ]
    if cfg.firmware == "uefi":
        rem = _esp_removable_efi_name(cfg)
        branded = _grub_branded_efi_name(cfg)
        parts.append(
            "{ "
            f"[ -s /efi/EFI/BOOT/{rem} ] || "
            f"[ -s /efi/EFI/Gentoo/{branded} ] || "
            f"[ -s /efi/EFI/gentoo/{branded} ]"
            "; }"
        )
    return " && ".join(parts)


def _bootloader_boot_ok_tests(cfg: InstallConfig, loaders: list[str]) -> list[str]:
    """Per-loader shell conditions for boot_ok — artifacts when the loader writes them (M13)."""
    tests: list[str] = []
    if "grub" in loaders:
        tests.append(_grub_boot_ok_test(cfg))
    if "systemd-boot" in loaders:
        tests.append(
            "command -v bootctl >/dev/null 2>&1 && "
            "{ [ -d /efi/loader ] || [ -d /boot/loader ] || [ -d /efi/EFI/systemd ]; }"
        )
    if "refind" in loaders:
        tests.append("command -v refind-install >/dev/null 2>&1")
    if "syslinux" in loaders:
        tests.append("command -v syslinux >/dev/null 2>&1")
    if "limine" in loaders:
        rem = _esp_removable_efi_name(cfg)
        tests.append(
            "command -v limine >/dev/null 2>&1 && "
            f"{{ [ -s /efi/limine.conf ] || [ -s /efi/EFI/BOOT/{rem} ]; }}"
        )
    if "lilo" in loaders:
        tests.append("command -v lilo >/dev/null 2>&1")
    if "efistub" in loaders or not tests:
        tests.append('[ "$kernel_ok" -eq 1 ]')
    return tests


def _final_tool_checks(cfg: InstallConfig, loaders: list[str]) -> str:
    """Fail closed when kernel, bootloader, required services, or greeter/WM are absent.

    The script runs under `set +e`. Optional catalogue packages may still be missing;
    critical services and the selected Wayland WM must not be (H5/H6).
    Bootloader checks require written artifacts (grub.cfg / ESP EFI), not only binaries (M13).
    """
    warn_checks: list[tuple[str, str]] = []
    if cfg.username:
        warn_checks.append(("[ -e /etc/sudoers.d/wheel ]", "/etc/sudoers.d/wheel"))
    warn_checks.append(("command -v nano >/dev/null 2>&1", "nano"))
    if cfg.plymouth:
        warn_checks.append(
            ("command -v plymouth-set-default-theme >/dev/null 2>&1", "plymouth-set-default-theme")
        )
    openrc = cfg.init_system == "openrc"
    if openrc and cfg.zram:
        warn_checks.append(("[ -e /etc/init.d/zram-init ]", "zram-init"))
        warn_checks.append(
            ("grep -q '^size0=\"[0-9][0-9]*\"' /etc/conf.d/zram-init", "zram-init size0")
        )
    elif cfg.zram:
        warn_checks.append(("[ -s /etc/systemd/zram-generator.conf ]", "zram-generator.conf"))
    lines = [
        'echo ">> verify kernel, bootloader, and services"',
        "missing=0",
        "kernel_ok=0",
        "boot_ok=0",
        _kernel_found_shell("kernel_ok").strip(),
    ]
    for test, label in warn_checks:
        lines.append(f'if ! {{ {test}; }}; then echo ">> still missing: {label}" >&2; fi')
    for test, label in _critical_service_final_checks(cfg):
        lines.append(
            f'if ! {{ {test}; }}; then echo ">> required service missing: {label}" >&2; missing=1; fi'
        )
    boot_tests = _bootloader_boot_ok_tests(cfg, loaders)
    joined = " || ".join(boot_tests)
    lines.append(
        f"if {joined}; then boot_ok=1; else "
        "echo '>> no selected bootloader left bootable artifacts' >&2; fi"
    )
    lines.append('if [ "$kernel_ok" -eq 0 ]; then echo ">> no kernel image was installed" >&2; missing=1; fi')
    lines.append('if [ "$boot_ok" -eq 0 ]; then missing=1; fi')
    # Explicit grub.cfg probe (clearer than boot_ok alone) — required for every GRUB install.
    if "grub" in loaders:
        lines.append(
            'if [ ! -s /boot/grub/grub.cfg ]; then '
            'echo ">> missing /boot/grub/grub.cfg" >&2; missing=1; fi'
        )
        if cfg.firmware == "uefi":
            rem = _esp_removable_efi_name(cfg)
            branded = _grub_branded_efi_name(cfg)
            lines.append(
                "if ! { "
                f"[ -s /efi/EFI/BOOT/{rem} ] || "
                f"[ -s /efi/EFI/Gentoo/{branded} ] || "
                f"[ -s /efi/EFI/gentoo/{branded} ]"
                "; }; then "
                f'echo ">> missing ESP EFI payload '
                f'(/efi/EFI/BOOT/{rem} or /efi/EFI/Gentoo/{branded})" >&2; '
                "missing=1; fi"
            )
    # LUKS + GRUB: kernels live on encrypted /boot; cryptodisk must be enabled or first boot fails.
    if cfg.luks and "grub" in loaders:
        lines.append(
            "if ! grep -Eq '^[[:space:]]*GRUB_ENABLE_CRYPTODISK=y' "
            "/etc/default/grub 2>/dev/null; then "
            'echo ">> LUKS+GRUB requires GRUB_ENABLE_CRYPTODISK=y" >&2; missing=1; fi'
        )
    # Selected WM session binaries — labwc as greeter helper is not a substitute (H6).
    wm_atoms = _window_managers(cfg)
    if wm_atoms:
        wm_probes = []
        for atom in wm_atoms:
            binary = _wm_exec_binary(cfg, atom)
            if binary:
                wm_probes.append(f"command -v {shlex.quote(binary)} >/dev/null 2>&1")
        if wm_probes:
            lines.append(
                "if ! { "
                + " || ".join(wm_probes)
                + '; }; then echo ">> no selected window manager binary is installed" >&2; missing=1; fi'
            )
    # Greeter presence is fatal when a greeter was requested — same as kernel/bootloader.
    greeter_fatal: list[tuple[str, str]] = []
    dm = _resolved_dm(cfg)
    if dm == "greetd":
        greeter_fatal.append(("command -v greetd >/dev/null 2>&1", "greetd binary"))
        greeter_fatal.append(("getent passwd greetd >/dev/null 2>&1", "greetd system user"))
        if openrc:
            # greetd ships no OpenRC init script; Gentoo uses display-manager-init.
            greeter_fatal.append(
                ("[ -e /etc/init.d/display-manager ]", "display-manager init script")
            )
            greeter_fatal.append(
                ("[ -e /etc/runlevels/default/display-manager ]", "display-manager in default runlevel")
            )
            greeter_fatal.append(
                (
                    "grep -Eq '^[[:space:]]*DISPLAYMANAGER=(\"greetd\"|greetd)' "
                    "/etc/conf.d/display-manager",
                    "DISPLAYMANAGER=greetd",
                )
            )
            greeter_fatal.append(
                (
                    "[ -e /etc/runlevels/boot/elogind ] || [ -e /etc/runlevels/default/elogind ]",
                    "elogind",
                )
            )
        else:
            greeter_fatal.append(
                (
                    _systemd_unit_test("greetd") + " || command -v greetd >/dev/null 2>&1",
                    "greetd systemd unit",
                )
            )
        greeter_fatal.append(("[ -s /etc/greetd/config.toml ]", "greetd config.toml"))
        greeter_fatal.append(
            (
                "find /usr/share/wayland-sessions /usr/share/xsessions "
                "-name '*.desktop' 2>/dev/null | grep -q .",
                "session .desktop",
            )
        )
        if not _greetd_uses_noctalia(cfg):
            greeter_fatal.append(("command -v gtkgreet >/dev/null 2>&1", "gtkgreet"))
            greeter_fatal.append(("command -v labwc >/dev/null 2>&1", "labwc"))
    for test, label in greeter_fatal:
        lines.append(
            f'if ! {{ {test}; }}; then echo ">> greeter check failed: {label}" >&2; missing=1; fi'
        )
    lines.append('if [ "$missing" -ne 0 ]; then echo ">> chroot configuration incomplete" >&2; exit 1; fi')
    return "\n".join(lines) + "\n"


def _resume_prelude(cfg: InstallConfig) -> str:
    """Keep files already on the target, and skip emerge of installed packages.

    --noreplace leaves an already-merged package's files alone and still
    installs anything that is missing. Critical services and selected WMs use
    emerge_checked (no $NR) so resume still repairs soft-failed probes (M3).
    append_once does not duplicate a line that is already in a config file.
    Stage3's stock /etc/fstab is not treated as ours; a fstab this installer
    already wrote is kept.
    """
    flag = "1" if cfg.resume else "0"
    return f"""
KEEP_EXISTING={flag}
NR=
if [ "$KEEP_EXISTING" = 1 ]; then
  NR="--noreplace"
  echo ">> resume: keep files already on the target; emerge skips packages already installed"
  echo ">> resume: required services and window managers still use emerge_checked (no --noreplace)"
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


def _mirror_retry_bash(cfg: InstallConfig) -> str:
    """Install a chroot emerge wrapper that switches mirrors after HTTP 404.

    The wrapper sits in front of emerge for the rest of this install. A 404 in
    the fetch log is probed on the next mirror; a mirror that has the file
    becomes GENTOO_MIRRORS and the same emerge runs again. A missing binpkg
    on every mirror drops --getbinpkg so the next try builds from source.
    """
    quoted = " ".join(shlex.quote(base) for base in _mirror_bases(cfg))
    script = r"""
# HTTP 404 during emerge: probe the next mirror for that file and retry.
install_fetch_retry() {
  mkdir -p /tmp/gentoo-installer-bin
  cat > /tmp/gentoo-installer-bin/retry404 << 'GENTOO_INSTALLER_RETRY404'
#!/bin/bash
# Run a command. When its output shows HTTP 404, check another Gentoo mirror
# for the missing file and run the command again.
set +e
MIRROR_CANDIDATES=(@@MIRRORS@@)

current_mirror_line() {
  grep -h '^GENTOO_MIRRORS=' /etc/portage/make.conf /etc/portage/make.conf/* /etc/make.conf 2>/dev/null | head -n 1
}

rel_from_url() {
  local url="$1"
  local base
  for base in "${MIRROR_CANDIDATES[@]}"; do
    case "$url" in
      "$base"/*)
        printf '%s\n' "${url#"$base"/}"
        return 0
        ;;
    esac
  done
  printf '%s\n' "$url" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/##'
}

failed_urls() {
  grep -E '404 Not Found|ERROR 404:|Error 404:|404 Client Error|HTTP Error 404|HTTP/[0-9.]+ 404' -B 12 "$1" 2>/dev/null \
    | grep -oE 'https?://[^[:space:]]+' \
    | sed -E 's/[),.;]+$//' \
    | awk '!seen[$0]++'
}

probe_url() {
  local url="$1"
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo ">> this stage has no wget or curl; switching mirrors without a probe" >&2
    return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fsIL --max-time 25 -o /dev/null "$url" && return 0
    curl -fsL --max-time 25 -r 0-0 -o /dev/null "$url" && return 0
    return 1
  fi
  wget -q --spider --timeout=25 --tries=1 "$url" && return 0
  wget -q --timeout=25 --tries=1 --header="Range: bytes=0-0" -O /dev/null "$url" && return 0
  return 1
}

make_conf_file() {
  if [ -d /etc/portage/make.conf ]; then
    printf '%s\n' /etc/portage/make.conf/99gentoo-live-installer
  else
    printf '%s\n' /etc/portage/make.conf
  fi
}

set_gentoo_mirrors() {
  local value="$*"
  local found=0
  local conf
  for conf in /etc/portage/make.conf /etc/portage/make.conf/* /etc/make.conf; do
    [ -f "$conf" ] || continue
    if grep -q '^GENTOO_MIRRORS=' "$conf"; then
      sed -i "s#^GENTOO_MIRRORS=.*#GENTOO_MIRRORS=\"${value}\"#" "$conf"
      found=1
    fi
  done
  if [ "$found" -eq 0 ]; then
    conf=$(make_conf_file)
    mkdir -p "$(dirname "$conf")"
    echo "GENTOO_MIRRORS=\"${value}\"" >> "$conf"
  fi
  echo ">> GENTOO_MIRRORS is now ${value}"
}

update_binhost() {
  local newbase="$1"
  local conf=/etc/portage/binrepos.conf/gentoobinhost.conf
  local tmp old suffix="" base line
  [ -f "$conf" ] || return 0
  # Dual x86-64 + x86-64-v3 repos share a host but keep distinct paths — rewrite
  # each sync-uri independently (Handbook Important for v3).
  tmp=$(mktemp) || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      sync-uri\ =*)
        old="${line#sync-uri = }"
        old="${old#"${old%%[![:space:]]*}"}"
        suffix=""
        for base in "${MIRROR_CANDIDATES[@]}"; do
          case "$old" in
            "$base"/*)
              suffix="${old#"$base"}"
              break
              ;;
          esac
        done
        if [ -z "$suffix" ]; then
          suffix=$(printf '%s\n' "$old" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+##')
        fi
        echo "sync-uri = ${newbase}${suffix}"
        echo ">> binhost mirror is now ${newbase}${suffix}"
        ;;
      *)
        printf '%s\n' "$line"
        ;;
    esac
  done < "$conf" > "$tmp"
  mv "$tmp" "$conf"
}

drop_binpkg_opts() {
  echo ">> no mirror has this binary package; further emerges use source" >&2
  local conf
  for conf in /etc/portage/make.conf /etc/portage/make.conf/*; do
    [ -f "$conf" ] || continue
    sed -i -e 's/ --getbinpkg / /g' -e 's/ --usepkg / /g' "$conf" || true
    # Handbook FEATURES getbinpkg must leave with the emerge opts.
    sed -i -E 's/(FEATURES="[^"]*)\bgetbinpkg\b */\1/g' "$conf" || true
    sed -i -E 's/(FEATURES="[^"]*)\bbinpkg-request-signature\b */\1/g' "$conf" || true
  done
  touch /tmp/gentoo-installer-binpkg-404-fallback
}

switch_mirror_for_404() {
  local log="$1"
  local urls url rel="" pick="" base missed="" skip=""
  urls=$(failed_urls "$log")
  [ -n "$urls" ] || return 1
  for url in $urls; do
    rel=$(rel_from_url "$url")
    case "$rel" in
      *binpackages*) continue ;;
    esac
    break
  done
  if [ -z "$rel" ]; then
    url=$(printf '%s\n' "$urls" | head -n 1)
    rel=$(rel_from_url "$url")
  fi
  [ -n "$rel" ] || return 1
  for url in $urls; do
    for base in "${MIRROR_CANDIDATES[@]}"; do
      case "$url" in
        "$base"/*) skip="$skip $base" ;;
      esac
    done
  done
  for base in "${MIRROR_CANDIDATES[@]}"; do
    case " $skip " in
      *" $base "*) continue ;;
    esac
    echo ">> checking ${base}/${rel}" >&2
    if probe_url "${base}/${rel}"; then
      pick=$base
      break
    fi
    echo ">> ${base} returned 404 for that file" >&2
    missed="$missed $base"
  done
  if [ -z "$pick" ]; then
    case "$rel" in
      *binpackages*)
        if [ ! -f /tmp/gentoo-installer-binpkg-404-fallback ]; then
          drop_binpkg_opts
          return 0
        fi
        ;;
    esac
    return 1
  fi
  local rest=""
  for base in "${MIRROR_CANDIDATES[@]}"; do
    [ "$base" = "$pick" ] && continue
    case " $skip $missed " in
      *" $base "*) continue ;;
    esac
    rest="$rest $base"
  done
  set_gentoo_mirrors $pick $rest
  case "$rel" in
    *binpackages*) update_binhost "$pick" ;;
  esac
  return 0
}

cmd="$1"
shift
if [ "$cmd" = "emerge" ]; then
  if [ -x /usr/bin/emerge ]; then
    cmd=/usr/bin/emerge
  elif [ -x /usr/sbin/emerge ]; then
    cmd=/usr/sbin/emerge
  fi
fi
log=/tmp/gentoo-installer-fetch.log
attempt=0
while [ "$attempt" -lt 8 ]; do
  attempt=$((attempt + 1))
  : > "$log"
  if command -v tee >/dev/null 2>&1; then
    "$cmd" "$@" 2>&1 | tee "$log"
    status=${PIPESTATUS[0]}
  else
    "$cmd" "$@" >"$log" 2>&1
    status=$?
    cat "$log"
  fi
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if ! grep -E -q '404 Not Found|ERROR 404:|Error 404:|404 Client Error|HTTP Error 404|HTTP/[0-9.]+ 404' "$log"; then
    exit "$status"
  fi
  echo ">> HTTP 404 while fetching; checking another mirror" >&2
  before=$(current_mirror_line)
  if ! switch_mirror_for_404 "$log"; then
    echo ">> every mirror that was checked returned 404 for that file" >&2
    exit "$status"
  fi
  after=$(current_mirror_line)
  if [ "$before" = "$after" ] && [ -f /tmp/gentoo-installer-binpkg-404-fallback ]; then
    echo ">> retrying emerge from source after the mirror 404" >&2
  else
    echo ">> retrying with the new mirror" >&2
  fi
done
echo ">> still HTTP 404 after checking the mirror list" >&2
exit 1
GENTOO_INSTALLER_RETRY404
  chmod 755 /tmp/gentoo-installer-bin/retry404
  cat > /tmp/gentoo-installer-bin/emerge << 'GENTOO_INSTALLER_EMERGEWRAP'
#!/bin/bash
exec /tmp/gentoo-installer-bin/retry404 emerge "$@"
GENTOO_INSTALLER_EMERGEWRAP
  chmod 755 /tmp/gentoo-installer-bin/emerge
  export PATH="/tmp/gentoo-installer-bin:${PATH}"
  hash -r
}
install_fetch_retry
"""
    return script.replace("@@MIRRORS@@", quoted)


def _source_setup_script(cfg: InstallConfig) -> str:
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
  echo ">> this CPU cannot run x86-64-v3 binpkgs (Illegal instruction); dropping v3 binhost (keep x86-64)"
  # Dual-repo handbook layout: delete the v3 section rather than renaming paths
  # (which would duplicate the base x86-64 URI).
  awk '
    BEGIN { skip=0 }
    /^[[]/ {
      if ($0 ~ /x86-64-v3/) { skip=1; next }
      skip=0
    }
    skip { next }
    /x86-64-v3/ { next }
    { print }
  ' /etc/portage/binrepos.conf/gentoobinhost.conf > /tmp/gentoobinhost.conf.$$ \
    && mv /tmp/gentoobinhost.conf.$$ /etc/portage/binrepos.conf/gentoobinhost.conf \
    || sed -i 's/x86-64-v3/x86-64/g' /etc/portage/binrepos.conf/gentoobinhost.conf || true
fi

disable_binpkgs() {
  echo ">> a binary raised Illegal instruction; further emerges use source"
  local conf
  for conf in /etc/portage/make.conf /etc/portage/make.conf/*; do
    [ -f "$conf" ] || continue
    sed -i -e 's/ --getbinpkg / /g' -e 's/ --usepkg / /g' "$conf" || true
    sed -i -E 's/(FEATURES="[^"]*)\bgetbinpkg\b */\1/g' "$conf" || true
    sed -i -E 's/(FEATURES="[^"]*)\bbinpkg-request-signature\b */\1/g' "$conf" || true
  done
}

# Stage3 Portage may not know --binpkg-respect-use. Leaving it in
# EMERGE_DEFAULT_OPTS makes every later emerge exit before it builds anything.
# make.conf no longer writes the option; sanitize_binpkg_respect_use (sed-only,
# before first emerge) and this prepare (after profile) both strip leftovers.
sanitize_binpkg_respect_use() {
  # Sed-only: safe before make.profile exists (no emerge --help / Portage load).
  local conf
  local changed=0
  for conf in /etc/portage/make.conf /etc/portage/make.conf/*; do
    [ -f "$conf" ] || continue
    if grep -q 'binpkg-respect-use' "$conf" 2>/dev/null; then
      sed -i -E 's/ --binpkg-respect-use=[^" ]*//g' "$conf" || true
      changed=1
    fi
  done
  if [ "$changed" -eq 1 ]; then
    echo ">> stripped --binpkg-respect-use from make.conf before first emerge"
  fi
}
RESPECT_USE=""
portage_prepare() {
  emerge --help >/tmp/emerge-help.txt 2>/tmp/emerge-help.err || true
  if grep -q 'binpkg-respect-use' /tmp/emerge-help.err 2>/dev/null; then
    echo ">> this Portage rejects --binpkg-respect-use; removing it so emerge can run"
    sanitize_binpkg_respect_use
    emerge --help >/tmp/emerge-help.txt 2>/tmp/emerge-help.err || true
  fi
  # =y when Portage documents the option: same safe ignore of USE-mismatched
  # binpkgs as the default, but silences the NOTE. Never =n (force-binpkg).
  # Leave empty only when unsupported so stage3 emerge does not abort.
  if grep -q 'binpkg-respect-use' /tmp/emerge-help.txt 2>/dev/null; then
    RESPECT_USE="--binpkg-respect-use=y"
  fi
  _binhost_usepkg=0
  for _conf in /etc/portage/make.conf /etc/portage/make.conf/*; do
    [ -f "$_conf" ] || continue
    if grep -Eq -- '--(getbinpkg|usepkg)( |")' "$_conf" 2>/dev/null; then
      _binhost_usepkg=1
      break
    fi
  done
  if [ "$_binhost_usepkg" -eq 1 ]; then
    if [ -n "$RESPECT_USE" ]; then
      echo ">> binhost/usepkg on; RESPECT_USE=$RESPECT_USE"
    else
      echo ">> binhost/usepkg on; RESPECT_USE empty (Portage lacks --binpkg-respect-use)"
    fi
  fi
  unset _binhost_usepkg _conf
  rm -f /tmp/emerge-help.txt /tmp/emerge-help.err
}
# emerge --help and eselect both load Portage. Portage prints
# "make.profile is not a symlink" when that path is missing, a real file,
# or a dangling link. Call portage_prepare only after link_make_profile.
# Call sanitize_binpkg_respect_use (sed-only) before any package emerge.

profile_has_marker() {
  local dir="$1"
  [ -e "${dir}/parent" ] || [ -e "${dir}/make.defaults" ] || [ -e "${dir}/packages" ] || [ -e "${dir}/use.force" ] || [ -e "${dir}/use.mask" ]
}

link_make_profile() {
  local profile="$1"
  local link=/etc/portage/make.profile
  local dest="/var/db/repos/gentoo/profiles/${profile}"
  if ! profile_has_marker "$dest"; then
    echo ">> ${dest} is not a Portage profile" >&2
    return 1
  fi
  if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo ">> ${link} is not a symlink; removing it" >&2
    rm -rf "$link" || return 1
  fi
  ln -sfn "$dest" "$link" || return 1
  if [ ! -L "$link" ] || ! profile_has_marker "$link"; then
    echo ">> ${link} does not point at a profile" >&2
    return 1
  fi
  echo ">> ${link} -> $(readlink "$link")"
}

# Dump Portage detail after a failed emerge @world so host log filter keeps it
# (every line prefixed with >>). Mirrors the git emerge failure dump.
dump_world_emerge_failure() {
  local status="${1:-?}"
  echo ">> @world Portage failure dump (exit $status) — detail follows" >&2
  echo ">> RESPECT_USE=[${RESPECT_USE:-}] (=y when Portage supports it; never =n)" >&2
  echo ">> USE=${USE:-}" >&2
  if [ -f /etc/portage/make.conf ]; then
    echo ">> make.conf USE / EMERGE_DEFAULT_OPTS / FEATURES:" >&2
    grep -E '^(USE|EMERGE_DEFAULT_OPTS|FEATURES)=' /etc/portage/make.conf 2>&1 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
  fi
  echo ">> emerge --info (head):" >&2
  emerge --info 2>&1 | head -n 60 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
  echo ">> Portage !!! / Failed to emerge / ERROR (emerge.log):" >&2
  if [ -f /var/log/emerge.log ]; then
    grep -E '!!!|Failed to emerge|\\* ERROR:' /var/log/emerge.log 2>/dev/null | tail -n 40 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
  else
    echo ">> no /var/log/emerge.log" >&2
  fi
  echo ">> newest build.log paths:" >&2
  _world_blogs=$(ls -1t /var/tmp/portage/*/temp/build.log 2>/dev/null | head -n 5)
  if [ -n "$_world_blogs" ]; then
    printf '%s\\n' "$_world_blogs" | while IFS= read -r _blog || [ -n "$_blog" ]; do
      [ -n "$_blog" ] && [ -f "$_blog" ] || continue
      echo ">> $_blog (last 80 lines)" >&2
      tail -n 80 "$_blog" 2>&1 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
    done
  else
    echo ">> no /var/tmp/portage/*/temp/build.log yet (often dep/mask/USE calculation)" >&2
  fi
}

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
""" + _mirror_retry_bash(cfg)


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


def _grub_cryptodisk_default_script() -> str:
    """Write GRUB_ENABLE_CRYPTODISK=y so grub-install embeds LUKS/cryptodisk modules.

    Handbook UEFI keeps kernels on encrypted /boot (ESP at /efi only). Without
    cryptodisk, GRUB cannot read grub.cfg or the kernel after reboot. Modules
    expected via this flag: cryptodisk, luks, luks2, and needed gcry_* hashes.
    """
    return """
mkdir -p /etc/default
export GRUB_ENABLE_CRYPTODISK=y
if [ ! -f /etc/default/grub ]; then
  echo 'GRUB_ENABLE_CRYPTODISK=y' > /etc/default/grub
elif grep -q '^[[:space:]]*GRUB_ENABLE_CRYPTODISK=' /etc/default/grub; then
  sed -i -E 's/^[[:space:]]*GRUB_ENABLE_CRYPTODISK=.*/GRUB_ENABLE_CRYPTODISK=y/' /etc/default/grub
else
  echo 'GRUB_ENABLE_CRYPTODISK=y' >> /etc/default/grub
fi
echo ">> GRUB_ENABLE_CRYPTODISK=y (LUKS: embed cryptodisk/luks2 modules at grub-install)"
"""


def _grub_uefi_install(target: str, *, fail_closed: bool = False) -> str:
    """Install GRUB for UEFI. efivarfs is often read-only in an Ubuntu chroot.

    That makes efibootmgr exit with "No such file or directory". The removable
    fallback still writes EFI/BOOT/BOOTX64.EFI so the firmware can boot.
    A binpkg built without GRUB_PLATFORMS=efi-64 has no modinfo.sh; rebuild it.
    When fail_closed (LUKS), grub-install/grub-mkconfig failures abort the chroot.
    """
    rebuild_fail = (
        "echo '>> grub rebuild failed' >&2; exit 1"
        if fail_closed
        else "echo '>> grub rebuild failed' >&2"
    )
    source_fail = (
        "echo '>> grub source build failed' >&2; exit 1"
        if fail_closed
        else "echo '>> grub source build failed' >&2"
    )
    removable_fail = (
        "{ echo '>> grub-install failed' >&2; exit 1; }"
        if fail_closed
        else "echo '>> grub-install failed' >&2"
    )
    after_install = [
        'if [ "$grub_status" -ne 0 ]; then',
        '  echo ">> grub-install could not register NVRAM; writing the removable EFI fallback"',
        f"  grub-install --target={target} --efi-directory=/efi --bootloader-id=Gentoo --removable --no-nvram || {removable_fail}",
    ]
    if fail_closed:
        after_install += [
            "  grub_status=$?",
            "fi",
            'if [ "$grub_status" -ne 0 ]; then',
            '  echo ">> grub-install failed" >&2',
            "  exit 1",
            "fi",
        ]
    else:
        after_install.append("fi")
    mkconfig = [
        "if command -v grub-mkconfig >/dev/null 2>&1; then",
        (
            "  grub-mkconfig -o /boot/grub/grub.cfg || "
            "{ echo '>> grub-mkconfig failed' >&2; exit 1; }"
            if fail_closed
            else "  grub-mkconfig -o /boot/grub/grub.cfg || echo '>> grub-mkconfig failed' >&2"
        ),
        "else",
        (
            '  echo ">> grub-mkconfig does not exist" >&2; exit 1'
            if fail_closed
            else '  echo ">> grub-mkconfig does not exist" >&2'
        ),
        "fi",
    ]
    return "\n".join(
        [
            f"if [ ! -e /usr/lib/grub/{target}/modinfo.sh ]; then",
            '  echo ">> GRUB EFI modules missing; rebuilding sys-boot/grub from source"',
            f"  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || {rebuild_fail}",
            "fi",
            "if [ -d /sys/firmware/efi ]; then",
            "  mkdir -p /sys/firmware/efi/efivars",
            "  mountpoint -q /sys/firmware/efi/efivars || mount -t efivarfs efivarfs /sys/firmware/efi/efivars || true",
            "  mount -o remount,rw,nosuid,nodev,noexec -t efivarfs efivarfs /sys/firmware/efi/efivars || true",
            "fi",
            "mkdir --parents /efi /boot/grub",
            "if ! mountpoint -q /efi; then echo '>> /efi is not mounted; the ESP belongs there (handbook)' >&2; fi",
            "if ! command -v grub-install >/dev/null 2>&1; then",
            '  echo ">> grub-install does not exist; building sys-boot/grub from source"',
            f"  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || {source_fail}",
            "fi",
            f"grub-install --target={target} --efi-directory=/efi --bootloader-id=Gentoo",
            "grub_status=$?",
            'if [ "$grub_status" -eq 132 ]; then',
            '  echo ">> grub-install: illegal instruction; rebuilding sys-boot/grub from source"',
            "  disable_binpkgs",
            f"  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || {source_fail}",
            f"  grub-install --target={target} --efi-directory=/efi --bootloader-id=Gentoo",
            "  grub_status=$?",
            "fi",
            *after_install,
            *mkconfig,
        ]
    )


def _graphical_atoms(cfg: InstallConfig) -> list[str]:
    return list(
        dict.fromkeys(
            cfg.extra_packages
            + _window_managers(cfg)
            + cfg.selected_packages
            + catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, cfg.wm_dotfiles)
        )
    )


def _resolved_dm(cfg: InstallConfig) -> str:
    return catalog.resolved_display_manager(cfg.display_manager, cfg.desktop, _graphical_atoms(cfg))


def ensure_boot_to_greeter(cfg: InstallConfig) -> None:
    """Mutate cfg so Wayland installs always enable greetd with a session WM.

    Called before chroot script generation and by greeter assertion tests. A
    TTY-only boot after choosing a Wayland compositor is a project failure.
    Portage shells were removed (OI-79); legacy shell ids are coerced to none.
    """
    cfg.shell = "none"
    wms = list(cfg.window_managers or [])
    shell = "none"
    if catalog.needs_login_greeter(cfg.window_managers, shell, _graphical_atoms(cfg)):
        cfg.display_manager = "greetd"
        sessions = catalog.login_sessions(
            cfg.desktop, _graphical_atoms(cfg), shell
        )
        if not sessions and catalog.FALLBACK_WAYLAND_WM not in (cfg.window_managers or []):
            cfg.window_managers = list(cfg.window_managers or []) + [catalog.FALLBACK_WAYLAND_WM]
def greeter_boot_failures(cfg: InstallConfig) -> list[str]:
    """Return reasons this config would not reboot to a usable login greeter.

    Empty list means the post-install path looks sound for: reboot → greeter →
    session. Does not prove GPU drivers or real hardware seat access.
    """
    ensure_boot_to_greeter(cfg)
    failures: list[str] = []
    dm = _resolved_dm(cfg)
    shell = getattr(cfg, "shell", "") or "none"
    needs = catalog.needs_login_greeter(cfg.window_managers, shell, _graphical_atoms(cfg))
    if needs and dm == "none":
        failures.append("Wayland/shell selected but display manager resolved to none")
    if dm == "none":
        return failures
    sessions = _login_sessions_for(cfg)
    if not sessions:
        failures.append("no session .desktop would be written for the greeter")
    tools = {atom for atom, _test in _display_manager_tools(cfg)}
    if dm == "greetd":
        if "gui-libs/greetd" not in tools:
            failures.append("greetd package missing from display-manager tools")
        if "acct-user/greetd" not in tools:
            failures.append("acct-user/greetd missing (greetd system user)")
        if "acct-group/greetd" not in tools:
            failures.append("acct-group/greetd missing (greetd system group)")
        if _greetd_uses_noctalia(cfg):
            if catalog.NOCTALIA_GREETER.atom not in tools:
                failures.append("noctalia-greeter missing while Noctalia shell is selected")
        else:
            if "gui-apps/gtkgreet" not in tools:
                failures.append("gtkgreet missing for greetd greeter")
            if "gui-wm/labwc" not in tools:
                failures.append("labwc missing (required to host gtkgreet)")
        if cfg.init_system == "openrc":
            if "gui-libs/display-manager-init" not in tools:
                failures.append("display-manager-init missing (OpenRC greeter boot)")
            if "sys-auth/elogind" not in tools:
                failures.append("elogind missing (OpenRC greeter needs a seat manager)")
            if "sys-apps/dbus" not in tools:
                failures.append("dbus missing (OpenRC greeter needs dbus)")
        script = _display_manager_script(cfg)
        if "login manager:" not in script and ">> login manager:" not in script:
            failures.append("display-manager script is empty")
        if "config.toml" not in script:
            failures.append("greetd config.toml would not be written")
        if "ensure greetd system user" not in script and "getent passwd greetd" not in script:
            failures.append("greetd system user would not be ensured before greeter enablement")
        if not _greetd_uses_noctalia(cfg):
            if "/etc/greetd/environments" not in script:
                failures.append("greetd environments file would not be written")
            else:
                # Heredoc is: cat > /etc/greetd/environments << 'EOF'\ncmds...\nEOF
                marker = "cat > /etc/greetd/environments << 'EOF'"
                if marker not in script:
                    failures.append("greetd environments heredoc missing")
                else:
                    body = script.split(marker, 1)[1].split("\nEOF", 1)[0]
                    commands = [line.strip() for line in body.splitlines() if line.strip()]
                    if not commands:
                        failures.append("greetd environments file would be empty (no sessions)")
                    for _title, command, _kind in sessions:
                        if command not in commands:
                            failures.append(f"session {command!r} missing from greetd environments")
        enabled = _enabled_dm_services(cfg)
        if cfg.init_system == "openrc":
            if "display-manager" not in enabled:
                failures.append("display-manager would not be enabled as a boot service")
            if "DISPLAYMANAGER=" not in script and 'DISPLAYMANAGER="greetd"' not in script:
                failures.append("OpenRC DISPLAYMANAGER=greetd would not be configured")
        elif dm not in enabled:
            failures.append(f"{dm} would not be enabled as a boot service")
        if cfg.init_system != "openrc":
            if "default.target" not in script and "graphical.target" not in script:
                failures.append("systemd graphical boot target would not be set")
    return failures


def _enabled_dm_services(cfg: InstallConfig) -> set[str]:
    """Display-manager service names that render_chroot_script would enable."""
    dm = _resolved_dm(cfg)
    if dm in {"none", "auto", ""}:
        return set()
    # OpenRC greetd has no /etc/init.d/greetd — enable display-manager instead.
    if dm == "greetd" and cfg.init_system == "openrc":
        return {"display-manager"}
    return {dm}


def _greetd_uses_noctalia(cfg: InstallConfig) -> bool:
    """Noctalia/DMS Portage shells were removed; always use gtkgreet + labwc."""
    del cfg
    return False


def _accounts_daemon_probe() -> tuple[str, ...]:
    return (
        "bash",
        "-c",
        "test -x /usr/libexec/accounts-daemon || test -e /usr/libexec/accounts-daemon || command -v accounts-daemon",
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
        tools.append(("sys-apps/accountsservice", _accounts_daemon_probe()))
    elif dm == "lightdm":
        tools.append(("x11-misc/lightdm", _service_probe(openrc, "lightdm")))
        tools.append(("x11-misc/lightdm-gtk-greeter", ("command", "-v", "lightdm-gtk-greeter")))
        tools.append(("sys-apps/accountsservice", _accounts_daemon_probe()))
    elif dm == "greetd":
        # acct-user/greetd creates the system account greeter configs assume.
        # Emerge these before gui-libs/greetd so a soft-failed greetd cannot
        # leave usermod/chown talking to a missing user.
        tools.append(("acct-group/greetd", ("getent", "group", "greetd")))
        tools.append(("acct-user/greetd", ("getent", "passwd", "greetd")))
        # greetd's ebuild only installs a systemd unit — no OpenRC init script.
        tools.append(("gui-libs/greetd", ("command", "-v", "greetd")))
        if openrc:
            tools.append(
                (
                    "gui-libs/display-manager-init",
                    ("test", "-e", "/etc/init.d/display-manager"),
                )
            )
        if _greetd_uses_noctalia(cfg):
            tools.append(("gui-apps/noctalia-greeter", ("command", "-v", "noctalia-greeter-session")))
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


def _session_slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower() or "session"


def _login_username(cfg: InstallConfig) -> str:
    """Account the greeter should offer. Empty when the name is not safe to write."""
    user = (cfg.username or "").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.@+-]{0,31}", user):
        return user
    return ""


def _login_sessions_for(cfg: InstallConfig) -> list[tuple[str, str, str]]:
    return catalog.ensure_login_sessions(
        cfg.desktop, _graphical_atoms(cfg), getattr(cfg, "shell", "") or ""
    )


def _session_desktop(title: str, command: str, kind: str) -> str:
    binary = (command or "").split()[0]
    lines = [
        "[Desktop Entry]",
        f"Name={title}",
        "Comment=Session installed by gentoo-live-installer",
        f"Exec={command}",
        "Type=Application",
    ]
    if binary:
        lines.append(f"TryExec={binary}")
    if kind == "wayland":
        lines.append("X-LightDM-Session-Type=wayland")
    lines.append("")
    return "\n".join(lines)


def _display_manager_script(cfg: InstallConfig) -> str:
    """Install the greeter, list the chosen sessions, and boot to it.

    The password is still required. The greeter is given the username and the
    window managers so the first boot is that screen, not a text shell.
    """
    ensure_boot_to_greeter(cfg)
    dm = _resolved_dm(cfg)
    if dm == "none":
        return ""
    sessions = _login_sessions_for(cfg)
    if dm == "greetd" and not sessions:
        # Should be unreachable after ensure_login_sessions; keep a hard stop.
        sessions = [catalog.FALLBACK_WAYLAND_SESSION]
    user = _login_username(cfg)
    lines = [f'echo ">> login manager: {dm}"']
    # Greeter stack is fatal — soft-fail left gtkgreet/labwc/greetd missing on live installs.
    for atom, test in _display_manager_tools(cfg):
        lines.append(_emerge_checked_call(atom, test, required=True))
    # When gtkgreet needs a session host and labwc was only a fallback, emerge it.
    if dm == "greetd" and not _greetd_uses_noctalia(cfg):
        if not any(atom == "gui-wm/labwc" for atom, _ in _display_manager_tools(cfg)):
            lines.append(
                _emerge_checked_call("gui-wm/labwc", ("command", "-v", "labwc"), required=True)
            )
    if dm == "greetd":
        lines.extend(_ensure_greetd_user_lines())
    lines.append("mkdir -p /usr/share/wayland-sessions /usr/share/xsessions")
    for title, command, kind in sessions:
        folder = "/usr/share/wayland-sessions" if kind == "wayland" else "/usr/share/xsessions"
        path = f"{folder}/installer-{_session_slug(title)}.desktop"
        body = _session_desktop(title, command, kind)
        lines.append(f"if ! grep -RFqs {shlex.quote(command)} /usr/share/wayland-sessions /usr/share/xsessions 2>/dev/null; then")
        lines.append(f"cat > {shlex.quote(path)} << 'EOF'")
        lines.append(body.rstrip("\n"))
        lines.append("EOF")
        lines.append("fi")
    lines.append(f"login_user={shlex.quote(user)}")
    if sessions:
        lines.append(f"first_exec={shlex.quote(sessions[0][1])}")
    else:
        lines.append("first_exec=")
    lines.extend(
        [
            "session_file=",
            "session_id=",
            'if [ -n "$first_exec" ]; then',
            '  session_file=$(grep -RFls --include=\'*.desktop\' -F -e "Exec=$first_exec" /usr/share/wayland-sessions /usr/share/xsessions 2>/dev/null | head -n1 || true)',
            '  if [ -n "$session_file" ]; then session_id=$(basename "$session_file" .desktop); fi',
            "fi",
        ]
    )
    if user:
        lines.append(
            'for grp in video input render seat; do '
            'getent group "$grp" >/dev/null || continue; '
            f'usermod -aG "$grp" {shlex.quote(user)} || true; '
            "done"
        )
    if dm == "lightdm":
        lines.extend(
            [
                "mkdir -p /etc/lightdm/lightdm.conf.d",
                "{",
                "  echo '[Seat:*]'",
                "  echo 'greeter-session=lightdm-gtk-greeter'",
                "  echo 'greeter-hide-users=false'",
                '  if [ -n "$session_id" ]; then echo "user-session=$session_id"; fi',
                "} > /etc/lightdm/lightdm.conf.d/installer.conf",
            ]
        )
    elif dm == "greetd":
        lines.extend(_greetd_config_lines(cfg, sessions))
    elif dm == "sddm":
        lines.extend(
            [
                "mkdir -p /etc/sddm.conf.d /var/lib/sddm",
                "cat > /etc/sddm.conf.d/installer.conf << 'EOF'",
                "[General]",
                "RememberLastUser=true",
                "RememberLastSession=true",
                "",
                "[Users]",
                "RememberLastUser=true",
                "RememberLastSession=true",
                "MinimumUid=1000",
                "MaximumUid=60000",
                "",
                "[Wayland]",
                "SessionDir=/usr/share/wayland-sessions",
                "",
                "[X11]",
                "SessionDir=/usr/share/xsessions",
                "EOF",
                'if [ -n "$login_user" ]; then',
                "  {",
                "    echo '[Last]'",
                '    echo "User=$login_user"',
                '    if [ -n "$session_file" ]; then echo "Session=$session_file"; fi',
                "  } > /var/lib/sddm/state.conf",
                "fi",
            ]
        )
    elif dm == "gdm":
        lines.extend(
            [
                "mkdir -p /etc/gdm",
                "cat > /etc/gdm/custom.conf << 'EOF'",
                "[daemon]",
                "InitialSetupEnable=false",
                "WaylandEnable=true",
                "EOF",
            ]
        )
    elif dm == "slim" and user:
        lines.extend(
            [
                'if [ -f /etc/slim.conf ]; then',
                "  if grep -q '^default_user ' /etc/slim.conf; then",
                f"    sed -i 's/^default_user .*/default_user {user}/' /etc/slim.conf",
                "  else",
                f"    echo 'default_user {user}' >> /etc/slim.conf",
                "  fi",
                "  if grep -q '^focus_password ' /etc/slim.conf; then",
                "    sed -i 's/^focus_password .*/focus_password yes/' /etc/slim.conf",
                "  else",
                "    echo 'focus_password yes' >> /etc/slim.conf",
                "  fi",
                "fi",
            ]
        )
    if user and dm in {"lightdm", "gdm", "sddm"}:
        lines.extend(
            [
                'if [ -n "$login_user" ] && [ -n "$session_id" ]; then',
                "  mkdir -p /var/lib/AccountsService/users",
                '  cat > "/var/lib/AccountsService/users/${login_user}" << EOF',
                "[User]",
                "Session=${session_id}",
                "XSession=${session_id}",
                "SystemAccount=false",
                "EOF",
                "fi",
            ]
        )
    lines.extend(_login_manager_boot_lines(cfg, dm))
    # Hard assertions the chroot script can fail on before claiming success.
    if dm == "greetd":
        lines.extend(
            [
                'echo ">> verify greeter can present a login"',
                "greeter_ok=1",
                "if ! command -v greetd >/dev/null 2>&1 && ! [ -e /etc/init.d/greetd ] "
                "&& ! [ -e /usr/lib/systemd/system/greetd.service ] "
                "&& ! [ -e /lib/systemd/system/greetd.service ]; then",
                '  echo ">> greeter package missing: greetd" >&2',
                "  greeter_ok=0",
                "fi",
                "if ! [ -s /etc/greetd/config.toml ]; then",
                '  echo ">> greeter config missing: /etc/greetd/config.toml" >&2',
                "  greeter_ok=0",
                "fi",
                "if ! grep -q 'default_session' /etc/greetd/config.toml 2>/dev/null; then",
                '  echo ">> greeter config has no default_session" >&2',
                "  greeter_ok=0",
                "fi",
            ]
        )
        if not _greetd_uses_noctalia(cfg):
            lines.extend(
                [
                    "if ! [ -s /etc/greetd/environments ]; then",
                    '  echo ">> greetd environments empty — greeter would show no sessions" >&2',
                    "  greeter_ok=0",
                    "fi",
                    "if ! command -v gtkgreet >/dev/null 2>&1; then",
                    '  echo ">> greeter frontend missing: gtkgreet" >&2',
                    "  greeter_ok=0",
                    "fi",
                    "if ! command -v labwc >/dev/null 2>&1; then",
                    '  echo ">> greeter compositor missing: labwc" >&2',
                    "  greeter_ok=0",
                    "fi",
                ]
            )
        else:
            lines.extend(
                [
                    "if ! command -v noctalia-greeter-session >/dev/null 2>&1; then",
                    '  echo ">> greeter frontend missing: noctalia-greeter-session" >&2',
                    "  greeter_ok=0",
                    "fi",
                ]
            )
        lines.extend(
            [
                "if ! find /usr/share/wayland-sessions /usr/share/xsessions "
                "-name '*.desktop' 2>/dev/null | grep -q .; then",
                '  echo ">> no session .desktop files for the greeter" >&2',
                "  greeter_ok=0",
                "fi",
                "if ! getent passwd greetd >/dev/null 2>&1; then",
                '  echo ">> greetd system user missing" >&2',
                "  greeter_ok=0",
                "fi",
            ]
        )
        if cfg.init_system == "openrc":
            lines.extend(
                [
                    "if ! [ -e /etc/init.d/display-manager ]; then",
                    '  echo ">> OpenRC display-manager init script missing" >&2',
                    "  greeter_ok=0",
                    "fi",
                    "if ! grep -Eq '^[[:space:]]*DISPLAYMANAGER=(\"greetd\"|greetd)' "
                    "/etc/conf.d/display-manager 2>/dev/null; then",
                    '  echo ">> DISPLAYMANAGER=greetd not set in /etc/conf.d/display-manager" >&2',
                    "  greeter_ok=0",
                    "fi",
                ]
            )
        lines.extend(
            [
                'if [ "$greeter_ok" -ne 1 ]; then',
                '  echo ">> boot-to-greeter checks failed" >&2',
                "  exit 1",
                "fi",
            ]
        )
    return "\n".join(lines) + "\n"


def _toml_basic(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _noctalia_greeter_lines(cfg: InstallConfig, sessions: list[tuple[str, str, str]]) -> list[str]:
    """Point greetd at noctalia-greeter and preselect the install's user and session."""
    user = _login_username(cfg)
    keymap = _safe_token(getattr(cfg, "keymap", "") or "", "us")
    lines = [
        "greeter_cmd=$(command -v noctalia-greeter-session || true)",
        'if [ -z "$greeter_cmd" ]; then greeter_cmd=/usr/bin/noctalia-greeter-session; fi',
        "mkdir -p /etc/greetd",
        "cat > /etc/greetd/config.toml << EOF",
        "[terminal]",
        "vt = 1",
        "",
        "[default_session]",
        'command = "${greeter_cmd}"',
        'user = "greetd"',
        "EOF",
        "install -d -o greetd -g greetd -m 750 /var/lib/noctalia-greeter",
        "if command -v noctalia-greeter-apply-appearance >/dev/null 2>&1; then",
        "  GREETER_USER=greetd noctalia-greeter-apply-appearance --setup-system || true",
        "fi",
        "cat > /var/lib/noctalia-greeter/greeter.toml << EOF",
    ]
    if user:
        lines.extend(["[user]", f"default = {_toml_basic(user)}", ""])
    if sessions:
        lines.extend(["[session]", f"default = {_toml_basic(sessions[0][0])}", ""])
    if keymap:
        lines.extend(["[keyboard]", f"layout = {_toml_basic(keymap)}", ""])
    lines.extend(
        [
            "EOF",
            "chown greetd:greetd /var/lib/noctalia-greeter/greeter.toml || true",
            "chmod 640 /var/lib/noctalia-greeter/greeter.toml || true",
            'for grp in video input render seat; do '
            'getent group "$grp" >/dev/null || continue; '
            'usermod -aG "$grp" greetd || true; '
            "done",
        ]
    )
    return lines


def _ensure_greetd_user_lines() -> list[str]:
    """Create the greetd system account before config.toml / usermod / chown.

    gui-libs/greetd DEPENDS on acct-user/greetd, but a failed emerge used to
    soft-continue into usermod greetd. Fail closed if the account is still missing.
    """
    return [
        'echo ">> ensure greetd system user/group"',
        "if ! getent group greetd >/dev/null 2>&1; then",
        "  groupadd -r greetd || true",
        "fi",
        "if ! getent passwd greetd >/dev/null 2>&1; then",
        "  useradd -r -g greetd -d /var/lib/greetd -s /usr/sbin/nologin greetd || true",
        "fi",
        "install -d -o greetd -g greetd -m 755 /var/lib/greetd || true",
        "if ! getent passwd greetd >/dev/null 2>&1; then",
        '  echo ">> greetd system user does not exist after acct-user/fallback" >&2',
        "  exit 1",
        "fi",
        "if ! getent group greetd >/dev/null 2>&1; then",
        '  echo ">> greetd system group does not exist" >&2',
        "  exit 1",
        "fi",
    ]


def _greetd_config_lines(cfg: InstallConfig, sessions: list[tuple[str, str, str]]) -> list[str]:
    """Write greetd environments + gtkgreet/labwc (or Noctalia) default_session."""
    if _greetd_uses_noctalia(cfg):
        return _noctalia_greeter_lines(cfg, sessions)
    lines = [
        "mkdir -p /etc/greetd",
        "cat > /etc/greetd/environments << 'EOF'",
    ]
    for _title, command, _kind in sessions:
        lines.append(command)
    lines.append("EOF")
    # gui-apps/tuigreet stays in REMOVED_GURU_ATOMS — gtkgreet/labwc is the greetd path.
    lines.extend(
        [
            "mkdir -p /etc/greetd/labwc",
            "cat > /etc/greetd/labwc/autostart << 'EOF'",
            "#!/bin/sh",
            "if gtkgreet -h 2>&1 | grep -q -- '--layer-shell'; then",
            "  gtkgreet -l",
            "else",
            "  gtkgreet",
            "fi",
            "labwc --exit || true",
            "EOF",
            "chmod 755 /etc/greetd/labwc/autostart",
            "greeter_cmd='labwc --config-dir /etc/greetd/labwc'",
            "if ! labwc --help 2>&1 | grep -q -- '--config-dir'; then",
            "  greeter_cmd='labwc -s /etc/greetd/labwc/autostart'",
            "fi",
            "cat > /etc/greetd/config.toml << EOF",
            "[terminal]",
            "vt = 1",
            "",
            "[default_session]",
            'command = "${greeter_cmd}"',
            'user = "greetd"',
            "EOF",
        ]
    )
    lines.append(
        'for grp in video input render seat; do '
        'getent group "$grp" >/dev/null || continue; '
        "usermod -aG \"$grp\" greetd || true; "
        "done"
    )
    return lines


def _login_manager_boot_lines(cfg: InstallConfig, dm: str) -> list[str]:
    """Start the greeter when the machine is up, and keep a text console on tty2."""
    if cfg.init_system == "openrc":
        lines = [
            "if [ -f /etc/inittab ]; then",
            "  awk '",
            "    /^[[:space:]]*#/ { print; next }",
            "    /tty1/ && /(agetty|getty)/ { print \"# \" $0; next }",
            "    { print }",
            "  ' /etc/inittab > /etc/inittab.installer",
            "  mv /etc/inittab.installer /etc/inittab",
            '  echo ">> tty1 is the login manager; tty2 stays a text console"',
            "fi",
        ]
        if dm == "greetd":
            # Gentoo wiki: OpenRC uses display-manager + DISPLAYMANAGER=greetd.
            # greetd itself has no OpenRC init script (systemd unit only).
            lines.extend(
                [
                    "mkdir -p /etc/conf.d",
                    "if [ -f /etc/conf.d/display-manager ]; then",
                    "  if grep -q '^[[:space:]]*DISPLAYMANAGER=' /etc/conf.d/display-manager; then",
                    "    sed -i 's/^[[:space:]]*DISPLAYMANAGER=.*/DISPLAYMANAGER=\"greetd\"/' "
                    "/etc/conf.d/display-manager",
                    "  else",
                    "    echo 'DISPLAYMANAGER=\"greetd\"' >> /etc/conf.d/display-manager",
                    "  fi",
                    "  if grep -q '^[[:space:]]*CHECKVT=' /etc/conf.d/display-manager; then",
                    "    sed -i 's/^[[:space:]]*CHECKVT=.*/CHECKVT=1/' /etc/conf.d/display-manager",
                    "  else",
                    "    echo 'CHECKVT=1' >> /etc/conf.d/display-manager",
                    "  fi",
                    "else",
                    "  cat > /etc/conf.d/display-manager << 'EOF'",
                    'CHECKVT=1',
                    'DISPLAYMANAGER="greetd"',
                    "EOF",
                    "fi",
                    'echo ">> OpenRC display-manager will start greetd"',
                ]
            )
        return lines
    lines = [
        "graphical=",
        "for dir in /usr/lib/systemd/system /lib/systemd/system; do",
        '  if [ -e "$dir/graphical.target" ]; then graphical="$dir/graphical.target"; break; fi',
        "done",
        'if [ -n "$graphical" ]; then',
        "  mkdir -p /etc/systemd/system",
        '  ln -sfn "$graphical" /etc/systemd/system/default.target',
        '  echo ">> boot target is the login manager"',
        "fi",
    ]
    if dm == "greetd":
        lines.extend(
            [
                "mkdir -p /etc/systemd/system",
                "ln -sfn /dev/null /etc/systemd/system/getty@tty1.service",
                'echo ">> tty1 is the login manager; tty2 stays a text console"',
            ]
        )
    return lines


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
    if dm == "greetd" and not _greetd_uses_noctalia(cfg):
        lines.append("gui-apps/gtkgreet layer-shell")
    queued = set(_graphical_atoms(cfg))
    if "gui-wm/niri" in queued and "gui-apps/waybar" in queued:
        # Waybar's niri module is a local USE flag. Without it the bar has no workspaces.
        lines.append("gui-apps/waybar niri")
    if "x11-misc/rofi" in queued:
        lines.append("x11-misc/rofi wayland")
    if "x11-misc/dunst" in queued:
        lines.append("x11-misc/dunst wayland")
    # gentoo-sources only creates /usr/src/linux when USE=symlink (or eselect kernel).
    if catalog.kernel_package_name(cfg.kernel) == "gentoo-sources":
        lines.append("sys-kernel/gentoo-sources symlink")
    return "\n".join(lines) + "\n"


def _bare_atom(atom: str) -> str:
    """category/package (slot kept) without ::overlay or a version operator."""
    bare = (atom or "").split("::", 1)[0].strip()
    while bare and bare[0] in "=><!*~":
        bare = bare[1:]
    return bare


def _service_stack_atoms(cfg: InstallConfig) -> list[str]:
    """Alias for required_atom_plan — kept for call sites / older tests."""
    return required_atom_plan(cfg)


# Emerged before any eselect-repository enable/sync. Must be keyworded+unmasked first.
GIT_ATOM = "dev-vcs/git"
OVERLAY_HELPER_ATOMS: tuple[str, ...] = (
    "app-eselect/eselect-repository",
    GIT_ATOM,
)


def _needs_git_for_sync(cfg: InstallConfig) -> bool:
    """True when Portage tree sync or overlay sync will invoke git."""
    if (cfg.sync_method or "").strip() == "git":
        return True
    return bool(_required_overlays(cfg))


def _git_keyword_text(cfg: InstallConfig) -> str:
    return f"{GIT_ATOM} {_arch_testing_keyword(cfg)}\n"


def _git_unmask_text() -> str:
    return f"{GIT_ATOM}\n"


def _portage_oneshot_script(*, reason: str) -> str:
    """Fail-closed Handbook oneshot of ``sys-apps/portage``.

    Never uses ``$NR`` / ``--noreplace`` — resume must still upgrade Portage
    when the tree is newer than the stage3 binary. On failure, retries from
    source (``--usepkg=n``) then exits.
    """
    return f"""
echo ">> emerge Portage oneshot ({reason})"
unset FEATURES
if ! emerge --ask=n --oneshot --verbose sys-apps/portage; then
  echo ">> Portage oneshot failed; retrying from source" >&2
  disable_binpkgs
  emerge --ask=n --oneshot --verbose --usepkg=n --getbinpkg=n sys-apps/portage || {{
    echo ">> required Portage oneshot failed ({reason})" >&2
    exit 1
  }}
fi
"""


def _seed_gentoo_tree_script(*, for_atom: str = GIT_ATOM) -> str:
    """Git-free webrsync/rsync seed when stage3 left no usable gentoo tree.

    Desktop stage3 often unpacks without ``/var/db/repos/gentoo``. Emerging
    ``dev-vcs/git`` (needed *for* later ``sync_method=git`` / overlay sync)
    still needs profiles + ebuilds first — handbook ``emerge-webrsync`` (or
    ``emerge --sync``) is the seed; the later ``_portage_tree_sync_script``
    refreshes or replaces with a git clone as configured.
    """
    return f"""
# Git-free seed: stage3 unpack alone is not enough for emerge/profile.
_gentoo_tree_usable=0
if [ -d /var/db/repos/gentoo/profiles ] \\
  && ls /var/db/repos/gentoo/dev-vcs/git/*.ebuild >/dev/null 2>&1; then
  _gentoo_tree_usable=1
fi
if [ "$_gentoo_tree_usable" -ne 1 ]; then
  echo ">> gentoo ebuild tree missing/incomplete before emerge {for_atom}; seeding with emerge-webrsync"
  mkdir -p /var/db/repos/gentoo
  if [ ! -d /var/db/repos/gentoo ]; then
    echo ">> failed to create /var/db/repos/gentoo before webrsync seed" >&2
    exit 1
  fi
  if command -v retry404 >/dev/null 2>&1; then
    retry404 emerge-webrsync || retry404 emerge --sync || {{
      echo ">> webrsync seed failed before emerge {for_atom} (emerge-webrsync / emerge --sync)" >&2
      exit 1
    }}
  else
    emerge-webrsync || emerge --sync || {{
      echo ">> webrsync seed failed before emerge {for_atom} (emerge-webrsync / emerge --sync)" >&2
      exit 1
    }}
  fi
fi
if [ ! -d /var/db/repos/gentoo ]; then
  echo ">> /var/db/repos/gentoo missing after webrsync seed (before emerge {for_atom})" >&2
  exit 1
fi
if [ ! -d /var/db/repos/gentoo/profiles ]; then
  echo ">> /var/db/repos/gentoo/profiles missing after webrsync seed (cannot link make.profile)" >&2
  echo ">> gentoo tree listing:" >&2
  ls -la /var/db/repos/gentoo 2>&1 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
  exit 1
fi
if ! ls /var/db/repos/gentoo/dev-vcs/git/*.ebuild >/dev/null 2>&1; then
  echo ">> no ebuilds for {for_atom} under /var/db/repos/gentoo after webrsync seed" >&2
  echo ">> gentoo tree listing:" >&2
  ls /var/db/repos/gentoo 2>&1 | head -n 40 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
  exit 1
fi
echo ">> gentoo tree/profile precondition OK for {for_atom}"
"""


def _emerge_git_block(cfg: InstallConfig, *, reason: str) -> str:
    """Seed tree if needed, oneshot Portage, then keyword/unmask + emerge git.

    Always asserts a concrete UTF-8 LANG/LC_ALL (never bare C), seeds a missing
    gentoo tree via ``emerge-webrsync`` / ``emerge --sync`` (git-free), fails
    closed if seed still leaves no profiles/ebuilds, links make.profile, runs a
    fail-closed Portage oneshot *before* any ``emerge -pv`` / ``emerge_one`` of
    git (clears the handbook nag after seed), then either skips emerge when
    ``git`` is already on PATH or runs git through ``emerge_one`` (SIGILL /
    ``--usepkg=n`` retry) with optional oneshot autounmask when ``emerge -pv``
    shows keyword masks. On failure dumps tree/profile/``emerge -pv`` lines
    prefixed with ``>>`` for the host filter.
    """
    utf8 = shlex.quote(_installer_utf8_locale(cfg))
    kw = _git_keyword_text(cfg).rstrip("\n")
    um = _git_unmask_text().rstrip("\n")
    pre_quoted = " ".join(shlex.quote(path) for path in _profile_candidates(cfg))
    seed = _seed_gentoo_tree_script(for_atom=GIT_ATOM).rstrip("\n")
    early_portage = _portage_oneshot_script(
        reason="before first emerge after tree/profile ready"
    ).rstrip("\n")
    return f"""
echo ">> ensure {GIT_ATOM} ({reason})"
# Never emerge git under ambiguous LANG=C from the installer env wrapper.
export LANG={utf8}
export LC_ALL={utf8}
case "$LANG" in
  C|POSIX|"") echo ">> refuse emerge {GIT_ATOM} under bare LANG='$LANG'" >&2; exit 1 ;;
esac
echo ">> installer locale for {GIT_ATOM} emerge: $LANG"
{seed}
mkdir -p /etc/portage/package.accept_keywords /etc/portage/package.unmask
# Link a real profile before any Portage calculation (seeded tree or stage3).
if [ -d /var/db/repos/gentoo/profiles ]; then
  for _pre_profile in {pre_quoted}; do
    link_make_profile "$_pre_profile" && break
  done
fi
if ! profile_has_marker /etc/portage/make.profile; then
  echo ">> make.profile not usable before emerge {GIT_ATOM} (tree/profile precondition)" >&2
  echo ">> make.profile -> $(readlink /etc/portage/make.profile 2>/dev/null || echo missing)" >&2
  ls -ld /etc/portage/make.profile 2>&1 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
  exit 1
fi
# Handbook: oneshot Portage once the tree+profile exist, before other emerges.
{early_portage}
# Some desktop stage3s already ship git — still require the seeded tree above.
if command -v git >/dev/null 2>&1; then
  echo ">> git already on PATH ($(command -v git)); skip emerge {GIT_ATOM}"
else
  cat > /etc/portage/package.accept_keywords/git << 'EOF'
{kw}
EOF
  cat > /etc/portage/package.unmask/git << 'EOF'
{um}
EOF
  echo ">> accept_keywords/git:"
  sed 's/^/>> /' /etc/portage/package.accept_keywords/git
  echo ">> package.unmask/git:"
  sed 's/^/>> /' /etc/portage/package.unmask/git
  unset FEATURES
  # Optional oneshot autounmask only when -pv already shows keyword masks on
  # git or a transitive dep (accept_keywords/git covers the atom itself only).
  _git_pv=$(emerge -pv {GIT_ATOM} 2>&1 || true)
  if printf '%s\\n' "$_git_pv" | grep -qiE 'masked by:.*keyword|masked by:.*~'; then
    echo ">> keyword mask in emerge -pv for {GIT_ATOM}; oneshot --autounmask-write --autounmask-continue" >&2
    emerge --ask=n --autounmask-write --autounmask-continue --verbose --noreplace {GIT_ATOM} || true
  fi
  # Route through emerge_one for SIGILL / --usepkg=n source retry (not bare emerge).
  _git_save_nr=$NR
  NR=--noreplace
  emerge_one {GIT_ATOM}
  git_emerge_status=$?
  NR=$_git_save_nr
  if [ "$git_emerge_status" -ne 0 ]; then
    echo ">> emerge {GIT_ATOM} failed (exit $git_emerge_status) — Portage detail follows" >&2
    echo ">> gentoo tree (head):" >&2
    ls /var/db/repos/gentoo 2>&1 | head -n 40 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
    echo ">> make.profile -> $(readlink /etc/portage/make.profile 2>/dev/null || echo missing)" >&2
    echo ">> emerge -pv {GIT_ATOM}:" >&2
    emerge -pv {GIT_ATOM} 2>&1 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
    echo ">> emerge --info (head):" >&2
    emerge --info 2>&1 | head -n 60 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
    echo ">> newest build.log (if any):" >&2
    _git_blog=$(ls -1t /var/tmp/portage/dev-vcs/git-*/temp/build.log 2>/dev/null | head -n 1)
    if [ -n "$_git_blog" ] && [ -f "$_git_blog" ]; then
      echo ">> $_git_blog (last 80 lines)" >&2
      tail -n 80 "$_git_blog" 2>&1 | while IFS= read -r _line || [ -n "$_line" ]; do echo ">> $_line" >&2; done || true
    else
      echo ">> no /var/tmp/portage/dev-vcs/git-*/temp/build.log yet (often mask/keyword/dep resolution)" >&2
    fi
    exit "$git_emerge_status"
  fi
fi
if ! command -v git >/dev/null 2>&1; then
  echo ">> git missing after emerge — cannot sync Portage overlays or a git tree" >&2
  exit 1
fi
echo ">> git ready ($(command -v git))"
"""


def _ensure_git_script(cfg: InstallConfig) -> str:
    """Keyword/unmask + emerge git before any git-backed Portage sync. Fail closed."""
    if not _needs_git_for_sync(cfg):
        return ""
    return _emerge_git_block(cfg, reason="before Portage git sync / overlay enable")


def _installer_utf8_locale(cfg: InstallConfig) -> str:
    """A concrete UTF-8 locale for Portage during chroot emerge/sync (not bare C)."""
    loc = (cfg.locale or "").strip()
    if loc in {"", "C", "POSIX", "custom"}:
        return "C.UTF-8" if cfg.stage_variant == "musl" else "en_US.UTF-8"
    # Prefer explicit UTF-8 forms; map common utf8 spelling.
    if loc.upper().endswith(".UTF8") and not loc.upper().endswith(".UTF-8"):
        loc = loc[:-4] + "UTF-8"
    if "UTF-8" in loc.upper() or "UTF8" in loc.upper():
        return loc
    # Bare language_COUNTRY from the wizard → assume UTF-8.
    if re.fullmatch(r"[A-Za-z]{2,3}_[A-Za-z0-9]+", loc):
        return f"{loc}.UTF-8"
    return "C.UTF-8" if cfg.stage_variant == "musl" else "en_US.UTF-8"


def _locale_prep_script(cfg: InstallConfig) -> str:
    """locale.gen + locale-gen, then export a real UTF-8 LANG before emerge/sync.

    Portage warns when LANG/LC_ALL are bare ``C`` / ``POSIX`` ("ambiguous locale").
    Host already wrote locale.gen; this regenerates archives in-chroot and switches
    the installer environment off C before any Portage sync/emerge.
    """
    utf8 = _installer_utf8_locale(cfg)
    q = shlex.quote(utf8)
    if cfg.stage_variant == "musl":
        return f"""
echo ">> musl: set UTF-8 locale env for Portage (no glibc locale-gen)"
export LANG={q}
export LC_ALL={q}
case "$LANG" in
  C|POSIX|"") echo ">> refuse emerge under ambiguous LANG='$LANG' (need UTF-8)" >&2; exit 1 ;;
esac
echo ">> installer locale for Portage: $LANG"
"""
    # Ensure the chosen UTF-8 line exists even if locale.gen was trimmed on resume.
    # Grep patterns are built in Python so locale names cannot break the shell.
    pat_utf8 = re.escape(utf8)
    return f"""
echo ">> generate UTF-8 locales before emerge/sync (avoid Portage ambiguous C warning)"
mkdir -p /etc
touch /etc/locale.gen
if ! grep -qE '^{pat_utf8}[[:space:]]+UTF-8' /etc/locale.gen 2>/dev/null; then
  echo {q} UTF-8 >> /etc/locale.gen
fi
if ! grep -qE '^C\\.UTF-8[[:space:]]+UTF-8' /etc/locale.gen 2>/dev/null; then
  echo 'C.UTF-8 UTF-8' >> /etc/locale.gen
fi
locale-gen || true
export LANG={q}
export LC_ALL={q}
# Prefer a locale that localedef actually produced; else C.UTF-8 (still not bare C).
if ! locale -a 2>/dev/null | grep -qiE "^$(echo "$LANG" | tr '[:upper:]' '[:lower:]')$"; then
  if locale -a 2>/dev/null | grep -qiE '^c\\.utf(-)?8$'; then
    export LANG=C.UTF-8
    export LC_ALL=C.UTF-8
  fi
fi
# Refuse bare C/POSIX before emerge — Portage warns and points at localectl.
case "$LANG" in
  C|POSIX|"") echo ">> refuse emerge under ambiguous LANG='$LANG' (need UTF-8)" >&2; exit 1 ;;
esac
case "$LC_ALL" in
  C|POSIX|"") echo ">> refuse emerge under ambiguous LC_ALL='$LC_ALL' (need UTF-8)" >&2; exit 1 ;;
esac
echo ">> installer locale for Portage: $LANG"
"""


def _news_read_script() -> str:
    """Handbook: after tree sync, before profile — list/read Gentoo news items.

    Unattended: mark unread items read so Portage stops nagging. Soft on empty
    news; fail closed only if ``eselect`` itself is missing (stage3 always has it).
    """
    return """
echo ">> eselect news (Handbook: after sync, before profile)"
if ! command -v eselect >/dev/null 2>&1; then
  echo ">> eselect missing after Portage sync — cannot continue" >&2
  exit 1
fi
eselect news list || true
# Prefer unread-only; fall back to reading all. Never block the install on news content.
eselect news read new >/dev/null 2>&1 || eselect news read >/dev/null 2>&1 || true
"""


def _locale_eselect_script(cfg: InstallConfig) -> str:
    """Handbook: eselect locale after profile (system-wide LANG via env.d).

    Early ``_locale_prep_script`` already ran locale-gen and exported LANG for
    Portage; this persists the choice through eselect and refuses bare C/POSIX.
    """
    utf8 = _installer_utf8_locale(cfg)
    q = shlex.quote(utf8)
    short = shlex.quote(utf8.split(".")[0])
    if cfg.stage_variant == "musl":
        return f"""
echo ">> musl: skip glibc eselect locale; keep UTF-8 env ({utf8})"
export LANG={q}
export LC_ALL={q}
case "$LANG" in
  C|POSIX|"") echo ">> installer LANG must be UTF-8, got: '$LANG'" >&2; exit 1 ;;
esac
"""
    return f"""
echo ">> eselect locale (Handbook: after profile)"
export LANG={q}
export LC_ALL={q}
if ! eselect locale set {q} && ! eselect locale set {short}; then
  echo ">> eselect locale set failed for {utf8}; checking env.d / LANG" >&2
  if [ -f /etc/env.d/02locale ] && grep -q 'LANG=.*UTF' /etc/env.d/02locale 2>/dev/null; then
    echo ">> host env.d/02locale already has a UTF-8 LANG; continuing"
  else
    echo ">> no UTF-8 locale configured via eselect or env.d" >&2
    exit 1
  fi
fi
case "$LANG" in
  C|POSIX|"") echo ">> installer LANG must be UTF-8 before emerge, got: '$LANG'" >&2; exit 1 ;;
esac
echo ">> system locale: $(eselect locale show 2>/dev/null | tr '\\n' ' ' | sed 's/  */ /g')"
"""


def _required_overlays(cfg: InstallConfig) -> list[str]:
    """Overlays this install will enable (guru, CachyOS-kernels, hyproverlay, …).

    Stale checklist names (wine, …) absent from repositories.xml are dropped —
    handbook Wine packages live in ::gentoo and do not need a wine overlay.
    Refused names (without-systemd, …) fail closed — never enable.
    """
    catalog.refuse_unsupported_overlays(cfg.overlays or [])
    overlay_names = [
        name
        for name in cfg.overlays
        if name != "guru" and name not in catalog.STALE_OVERLAY_NAMES
    ]
    if catalog.shell_atoms(getattr(cfg, "shell", "") or ""):
        overlay_names.append("guru")
    kernel_ov = catalog.kernel_overlay(cfg.kernel)
    if kernel_ov != "gentoo":
        overlay_names.append(kernel_ov)
    queued = [
        atom
        for atom in dict.fromkeys(
            [catalog.kernel_atom(cfg.kernel)]
            + list(cfg.extra_packages or [])
            + _window_managers(cfg)
            + list(cfg.selected_packages or [])
            + _support_atoms(cfg)
            + [atom for atom, _test in _display_manager_tools(cfg)]
        )
        if atom and (_atom_allowed(cfg, atom) or str(atom).startswith("sys-kernel/"))
    ]
    return [
        name
        for name in catalog.overlays_required_by(queued, overlay_names)
        if name not in catalog.STALE_OVERLAY_NAMES
    ]


def _arch_testing_keyword(cfg: InstallConfig) -> str:
    return "~amd64" if cfg.architecture == "amd64" else "~arm64"


def _overlay_helper_keyword_text(cfg: InstallConfig) -> str:
    """accept_keywords lines for eselect-repository / git (always explicit ~arch)."""
    kw = _arch_testing_keyword(cfg)
    return "\n".join(f"{atom} {kw}" for atom in OVERLAY_HELPER_ATOMS) + "\n"


def _overlay_helper_unmask_text() -> str:
    return "\n".join(OVERLAY_HELPER_ATOMS) + "\n"


def _overlay_repo_keyword_text(cfg: InstallConfig, overlays: list[str] | None = None) -> str:
    """Handbook: */*::overlay ~arch for every overlay this install enables."""
    ovs = overlays if overlays is not None else _required_overlays(cfg)
    if not ovs:
        return ""
    kw = _arch_testing_keyword(cfg)
    return "\n".join(f"*/*::{ov} {kw}" for ov in ovs) + "\n"


def _overlay_repo_unmask_text(cfg: InstallConfig, overlays: list[str] | None = None) -> str:
    """package.unmask */*::overlay so a package.mask cannot block overlay atoms."""
    ovs = overlays if overlays is not None else _required_overlays(cfg)
    if not ovs:
        return ""
    return "\n".join(f"*/*::{ov}" for ov in ovs) + "\n"


def _write_overlay_portage_files(cfg: InstallConfig, root: str) -> None:
    """Host-side: keywords + unmask for git, overlay helpers, and required overlays.

    Written into the mounted Gentoo root *before* chroot so the first
    git / eselect-repository emerge and later ::overlay packages are not masked.
    """
    overlays = _required_overlays(cfg)
    need_git = _needs_git_for_sync(cfg)
    if not overlays and not need_git:
        return
    portage = Path(root) / "etc" / "portage"
    kw_dir = portage / "package.accept_keywords"
    um_dir = portage / "package.unmask"
    kw_dir.mkdir(parents=True, exist_ok=True)
    um_dir.mkdir(parents=True, exist_ok=True)
    if need_git:
        _write_text(kw_dir / "git", _git_keyword_text(cfg), keep_existing=False)
        _write_text(um_dir / "git", _git_unmask_text(), keep_existing=False)
    if overlays:
        _write_text(kw_dir / "overlay-helpers", _overlay_helper_keyword_text(cfg), keep_existing=False)
        _write_text(um_dir / "overlay-helpers", _overlay_helper_unmask_text(), keep_existing=False)
        repo_kw = _overlay_repo_keyword_text(cfg, overlays)
        repo_um = _overlay_repo_unmask_text(cfg, overlays)
        if repo_kw.strip():
            _write_text(kw_dir / "overlays", repo_kw, keep_existing=False)
        if repo_um.strip():
            _write_text(um_dir / "overlays", repo_um, keep_existing=False)


def _chroot_helper_atoms(cfg: InstallConfig) -> list[str]:
    """Tooling the chroot script emerges outside the desktop package lists."""
    atoms = [
        "sys-apps/portage",
        "app-portage/cpuid2cpuflags",
    ]
    if not (cfg.gentoo_mirrors or "").strip():
        atoms.append("app-portage/mirrorselect")
    # Git tree sync and/or overlays (guru, hyproverlay, CachyOS, …) need git first.
    if _needs_git_for_sync(cfg):
        if GIT_ATOM not in atoms:
            atoms.append(GIT_ATOM)
    if _required_overlays(cfg):
        for atom in OVERLAY_HELPER_ATOMS:
            if atom not in atoms:
                atoms.append(atom)
    # Remote dotfile fetch uses curl from the chroot.
    for atom in cfg.window_managers or []:
        choices = catalog.dotfile_choices(atom)
        if not choices:
            continue
        wanted = "installer"
        for item in cfg.wm_dotfiles or []:
            key, _, value = str(item).partition("=")
            if key == atom and value:
                wanted = value
                break
        for choice in choices:
            if choice.id == wanted and choice.repo:
                atoms.append("net-misc/curl")
                break
    return atoms


def _atoms_to_unmask(cfg: InstallConfig) -> list[str]:
    """Every Portage atom this install plans to emerge (for keywords / unmask)."""
    loaders = selected_bootloaders(cfg)
    atoms: list[str] = [catalog.kernel_atom(cfg.kernel), "sys-kernel/gentoo-kernel"]
    atoms.extend(
        pkg.atom for pkg in catalog.packages_for_selection(cfg.window_managers, cfg.shell, cfg.pipewire)
    )
    atoms.extend(_graphical_atoms(cfg))
    atoms.extend(atom for atom, _test in _required_tools(cfg, loaders))
    atoms.extend(atom for atom, _test in _display_manager_tools(cfg))
    atoms.extend(_support_atoms(cfg))
    atoms.extend(required_atom_plan(cfg))
    atoms.extend(_chroot_helper_atoms(cfg))
    if cfg.linux_firmware:
        atoms.append("sys-kernel/linux-firmware")
    if cfg.sof_firmware:
        atoms.append("sys-firmware/sof-firmware")
    if cfg.intel_microcode:
        atoms.append("sys-firmware/intel-microcode")
    atoms.extend(
        pkg.atom
        for pkg in catalog.plymouth_packages_for(
            _chosen_plymouth_themes(cfg),
            cfg.plymouth_openrc_plugin and cfg.init_system == "openrc",
        )
    )
    if getattr(cfg, "nonfree_codecs", False):
        atoms.extend(catalog.nonfree_codec_atoms())
    atoms.extend(_desktop_packages(cfg))
    out: list[str] = []
    seen: set[str] = set()
    for atom in atoms:
        bare = _bare_atom(atom)
        if not bare or bare.startswith("flatpak-app/") or bare in seen:
            continue
        seen.add(bare)
        out.append(bare)
    return out


def _package_license_lines(cfg: InstallConfig) -> list[str]:
    """package.license entries for proprietary / binary apps this install may emerge."""
    planned = set(_atoms_to_unmask(cfg))
    # Map planned atoms → license tokens Portage needs beyond ACCEPT_LICENSE=* .
    extra = {
        "app-editors/sublime-text": "app-editors/sublime-text *",
        "app-editors/vscode": "app-editors/vscode *",
        "app-editors/logseq-desktop-bin": "app-editors/logseq-desktop-bin *",
        catalog.CHROME_ATOM: f"{catalog.CHROME_ATOM} google-chrome",
        "sys-kernel/linux-firmware": "sys-kernel/linux-firmware @BINARY-REDISTRIBUTABLE",
    }
    lines: list[str] = []
    seen: set[str] = set()
    for atom, line in extra.items():
        if atom in planned and line not in seen:
            lines.append(line)
            seen.add(line)
    return lines


def _keyword_text(cfg: InstallConfig) -> str:
    """Accept ~arch for every package the installer will emerge.

    Skipping this leaves ~amd64/~arm64 packages masked and abort the emerge
    calculation (NetworkManager, chrony, overlay kernels, …). When the user
    already accepted testing keywords globally, nothing extra is needed here
    except the Gentoo LTS sources pin (stable keywords only).
    """
    arch = "amd64" if cfg.architecture == "amd64" else "arm64"
    keyword = f"~{arch}"
    lines: list[str] = []
    # Real LTS path: sys-kernel/gentoo-sources at Gentoo stable. Negate ~arch so
    # a global */* ~arch line cannot pull a testing series for that package.
    if catalog.kernel_uses_stable_lts(cfg.kernel):
        lines.append(f"sys-kernel/gentoo-sources -~{arch}")
    if cfg.testing_keywords:
        return ("\n".join(lines) + "\n") if lines else ""
    skip_lts = set()
    if catalog.kernel_uses_stable_lts(cfg.kernel):
        skip_lts.add("sys-kernel/gentoo-sources")
    for atom in _atoms_to_unmask(cfg):
        if atom in skip_lts:
            continue
        lines.append(f"{atom} {keyword}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _unmask_text(cfg: InstallConfig) -> str:
    """package.unmask entries so a package.mask line cannot block emerge."""
    atoms = _atoms_to_unmask(cfg)
    if not atoms:
        return ""
    return "\n".join(atoms) + "\n"


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
    ensure_boot_to_greeter(cfg)
    loaders = selected_bootloaders(cfg)
    # Handbook: --newuse with binhost; --changed-use for source-only installs.
    world_use_flag = "--newuse" if cfg.binhost else "--changed-use"
    binrepo = ""
    binrepos_body = _binrepos_conf_text(cfg).rstrip("\n")
    if binrepos_body:
        binrepo = f"""
mkdir -p /etc/portage/binrepos.conf /var/cache/binhost
cat > /etc/portage/binrepos.conf/gentoobinhost.conf << 'EOF'
{binrepos_body}
EOF
echo ">> getuto (Handbook binpkg signature trust)"
if command -v getuto >/dev/null 2>&1; then
  getuto || {{
    echo ">> getuto failed — cannot verify binpkg signatures" >&2
    exit 1
  }}
else
  echo ">> getuto not on PATH yet; Portage runs it on first signed binpkg fetch" >&2
fi
"""
    kw_arch = "amd64" if cfg.architecture == "amd64" else "arm64"
    keywords = (
        f"append_once /etc/portage/package.accept_keywords/testing '*/* ~{kw_arch}'"
        if cfg.testing_keywords
        else ""
    )
    # git sync-type needs `git` on PATH — `_ensure_git_script` runs before this.
    # Fail closed: with ``set +eu`` a bare failing sync would otherwise continue.
    # Create /var/db/repos/gentoo *before* any repos.conf sets location= there.
    sync = _portage_tree_sync_script(cfg)
    git_prep = _ensure_git_script(cfg)
    locale_prep = _locale_prep_script(cfg)
    news_block = _news_read_script()
    locale_eselect = _locale_eselect_script(cfg)
    tree_verify = _gentoo_tree_present_script()

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
    dns_servers_netifrc = " ".join(_dns_nameservers(cfg))
    pkgs_kernel = (cfg.kernel or "gentoo-kernel-bin").split("::", 1)[0]
    if "/" in pkgs_kernel:
        pkgs_kernel = pkgs_kernel.rsplit("/", 1)[-1]
    extra_fw = []
    if cfg.linux_firmware:
        extra_fw.append("sys-kernel/linux-firmware")
    if cfg.sof_firmware:
        extra_fw.append("sys-firmware/sof-firmware")
    if cfg.intel_microcode:
        extra_fw.append("sys-firmware/intel-microcode")
    # Same plan as keywords + H5 checks — do not rebuild a parallel net_pkgs list (M4).
    net_pkgs = required_atom_plan(cfg)

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
    codec_atoms = catalog.nonfree_codec_atoms() if getattr(cfg, "nonfree_codecs", False) else []
    # Selected WMs are hard-required before the greeter (H6); keep them out of the soft late block.
    required_wm_set = set(_window_managers(cfg))
    late_atoms = [
        atom
        for atom in dict.fromkeys(
            de_pkgs
            + support_atoms
            + cfg.extra_packages
            + cfg.selected_packages
            + dm_atoms
            + catalog.dotfile_package_atoms(cfg.shell, cfg.window_managers, cfg.wm_dotfiles)
            + codec_atoms
        )
        if atom
        and _atom_allowed(cfg, atom)
        and not atom.startswith("flatpak-app/")
        and atom not in required_set
        and atom not in required_wm_set
        and atom not in set(theme_pkgs)
    ]
    fetch_atoms_extra = list(dict.fromkeys(required_atoms + missed + extra_fw + theme_pkgs + late_atoms))
    all_extra = " ".join(fetch_atoms_extra)
    kernel_atom = catalog.kernel_atom(pkgs_kernel)
    # gentoo-kernel is the source dist-kernel. Fetch it even when the selected
    # kernel is a binpkg, overlay kernel, or gentoo-sources, so a SIGILL or a
    # sources-only tree can still produce /boot/vmlinuz.
    fetch_atoms = [kernel_atom]
    if kernel_atom != "sys-kernel/gentoo-kernel":
        fetch_atoms.insert(0, "sys-kernel/gentoo-kernel")
    fetch_line = " ".join(dict.fromkeys(fetch_atoms))
    if all_extra:
        fetch_line = f"{fetch_line} {all_extra}"
    source_setup = _source_setup_script(cfg)
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
    if cfg.nftables:
        openrc_services.append("nftables")
        systemd_services.append("nftables")
    elif cfg.ufw:
        openrc_services.append("ufw")
        systemd_services.append("ufw")
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
        if dm in {"lightdm", "gdm"}:
            openrc_services.append("accounts-daemon")
            systemd_services.append("accounts-daemon")
        if dm == "greetd" and cfg.init_system == "openrc":
            # greetd has no OpenRC init script; display-manager-init starts it.
            openrc_services.append("display-manager")
        else:
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
    post_kernel_modules = _post_kernel_module_repairs(cfg, loaders)
    final_checks = _final_tool_checks(cfg, loaders)
    package_use = _package_use_text(cfg).strip()
    accept_keywords = _keyword_text(cfg).strip()
    unmask_atoms = _unmask_text(cfg).strip()
    dracut_conf = _dracut_conf_text(cfg).strip() if cfg.initramfs == "dracut" else ""
    locale_safe = _safe_token(cfg.locale, "en_US.UTF-8")
    keymap_safe = _safe_token(cfg.keymap, "us")
    keyword_block = ""
    if accept_keywords:
        keyword_block = f"""cat > /etc/portage/package.accept_keywords/installer << 'EOF'
{accept_keywords}
EOF
"""
    unmask_block = ""
    if unmask_atoms:
        unmask_block = f"""mkdir -p /etc/portage/package.unmask
cat > /etc/portage/package.unmask/installer << 'EOF'
{unmask_atoms}
EOF
"""
    codec_license_block = ""
    if getattr(cfg, "nonfree_codecs", False):
        license_lines = [
            f"{atom} {' '.join(catalog.NONFREE_CODEC_LICENSES)}"
            for atom in catalog.nonfree_codec_atoms()
        ]
        codec_license_block = (
            "mkdir -p /etc/portage/package.license\n"
            "cat > /etc/portage/package.license/codecs << 'EOF'\n"
            + "\n".join(license_lines)
            + "\nEOF\n"
            'echo ">> proprietary codec licenses accepted by the user"\n'
        )
    license_apps = "\n".join(_package_license_lines(cfg))
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
    # Keep linux-mod consumers out of the soft pre-kernel missed list too.
    missed_pre = [atom for atom in missed if not _atom_needs_kernel_sources(atom)]
    missed_block = _emerge_each_block("remaining service packages", missed_pre)
    late_block = _emerge_each_block("desktop and extra packages", late_atoms)
    theme_block = _emerge_each_block("Plymouth theme packages", theme_pkgs)
    need_kernel_src = _queued_needs_kernel_sources(cfg, loaders, late_atoms)
    kernel_sources_gate = _ensure_kernel_sources_script(cfg, loaders, late_atoms)
    if need_kernel_src:
        kernel_emerge_line = (
            f"emerge_with_source {kernel_atom} || {{ "
            f'echo ">> kernel emerge failed (needed before ZFS/modules)" >&2; exit 1; }}'
        )
        kernel_fallback = (
            "emerge --ask=n --usepkg=n --getbinpkg=n sys-kernel/gentoo-kernel || { "
            'echo ">> gentoo-kernel source build failed (needed before ZFS/modules)" >&2; '
            "exit 1; }"
        )
    else:
        kernel_emerge_line = (
            f'emerge_with_source {kernel_atom} || echo ">> kernel emerge failed" >&2'
        )
        kernel_fallback = (
            "emerge --ask=n --usepkg=n --getbinpkg=n sys-kernel/gentoo-kernel || "
            'echo ">> gentoo-kernel source build failed" >&2'
        )
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

    installkernel_conf = _installkernel_setup(cfg)
    mirrorselect_block = _mirrorselect_block(cfg)
    nftables_block = _nftables_block(cfg)
    ufw_block = _ufw_block(cfg)
    mise_invite_block = _mise_agents_invite_block(cfg)
    fstrim_block = _fstrim_block(cfg)
    kernel_seek = _kernel_found_shell("kernel_found")

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
        # btrfs-subvols mounts @swap at /swap (nodatacow). Put the file there so
        # CoW stays off; other layouts keep the classic /swapfile on root.
        if cfg.layout == "btrfs-subvols":
            swap_path = "/swap/swapfile"
        else:
            swap_path = "/swapfile"
        swapfile = f"""
if [ ! -f {swap_path} ]; then
  mkdir -p "$(dirname {swap_path})"
  dd if=/dev/zero of={swap_path} bs=1M count={cfg.swap_size_gib * 1024} status=progress || true
  chmod 600 {swap_path}
  mkswap {swap_path}
fi
append_once /etc/fstab '{swap_path} none swap sw 0 0'
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
    wm_required_block = _required_window_manager_script(cfg)
    dm_setup = _display_manager_script(cfg)
    level = getattr(cfg, "stage_level", "stage3") or "stage3"
    bootstrap = ""
    if level == "stage1":
        bootstrap = """
echo ">> stage1 bootstrap (seed toolchain, then @system)"
echo ">> an HTTP 404 during this emerge checks the next mirror and retries"
emerge --ask=n --oneshot sys-devel/binutils sys-devel/gcc sys-libs/glibc sys-devel/make || true
emerge --ask=n --emptytree @system || true
"""
    elif level == "stage2":
        bootstrap = """
echo ">> stage2 bootstrap (@system)"
echo ">> an HTTP 404 during this emerge checks the next mirror and retries"
emerge --ask=n --emptytree @system || true
"""

    return f"""#!/bin/bash
# gentoo-live-installer chroot script rev {CHROOT_SCRIPT_REV}
# Locales, hostname, timezone, keymap, make.conf are written by the host.
# This script must never `source /etc/profile` (debuginfod nounset + FEATURES).
# Host launches this script with a UTF-8 locale after locale-gen. Keep a brief
# C fallback only until locale_prep reasserts UTF-8 before any emerge/sync
# (avoids Portage "ambiguous locale … localectl" once emerge starts).
set +eu
unset FEATURES
unset PORTAGE_FEATURES
export PATH="/usr/sbin:/usr/bin:/sbin:/bin"
# Prefer the wrapper locale when it is already UTF-8; else C until locale_prep.
case "${{LANG:-C}}" in
  C|POSIX|"") export LANG=C; export LC_ALL=C ;;
  *) export LANG="${{LANG}}"; export LC_ALL="${{LC_ALL:-$LANG}}" ;;
esac
export DEBUGINFOD_URLS="${{DEBUGINFOD_URLS:-}}"
export DEBUGINFOD_CERT_PATH="${{DEBUGINFOD_CERT_PATH:-}}"
export DEBUGINFOD_IMA_CERT_PATH="${{DEBUGINFOD_IMA_CERT_PATH:-}}"
echo "=== gentoo-live-installer chroot rev {CHROOT_SCRIPT_REV} ==="
echo ">> chroot entry locale: LANG=${{LANG:-}} LC_ALL=${{LC_ALL:-}}"
{resume_prelude}
mkdir -p /etc/portage/package.use /etc/portage/package.accept_keywords /etc/portage/package.unmask /etc/portage/package.license /etc/portage/profile
echo ">> USE flags, keywords, unmask, and dracut configuration"
cat > /etc/portage/package.use/installer << 'EOF'
{package_use}
EOF
{keyword_block}
{unmask_block}
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
# Proprietary / binary apps this install may emerge (ACCEPT_LICENSE also set in make.conf).
cat > /etc/portage/package.license/apps << 'EOF'
{license_apps}
EOF
{codec_license_block}
{keywords}

{locale_prep}
echo ">> sanitize make.conf before first emerge"
sanitize_binpkg_respect_use
{git_prep}
echo ">> sync Portage tree"
{sync}
unset FEATURES
{tree_verify}
unset FEATURES
# Handbook:AMD64/Installation/Base — after sync: news → profile, then repositories,
# then system locale via eselect. Profile must precede overlay enables and @world.
{news_block}
unset FEATURES
{_profile_set_script(cfg)}
unset FEATURES
portage_prepare
{bootstrap}
{_portage_oneshot_script(reason="after sync/profile (Handbook final tree)")}
unset FEATURES
{overlay_block}
unset FEATURES
{locale_eselect}
unset FEATURES
{mirrorselect_block}
echo ">> emerge @world"
# Handbook Base: source path uses --changed-use; binhost example uses --newuse
# (plus -g via EMERGE_DEFAULT_OPTS / FEATURES). Pass $RESPECT_USE (=y when
# Portage supports it) so binhost/usepkg @world silences the ignore-NOTE.
# Never =n (force mismatched binpkgs; wiki: breaks the system). Empty only
# when unsupported. On failure, retry once from source (--usepkg=n).
emerge --verbose --update --deep {world_use_flag} --keep-going $RESPECT_USE $NR @world
world_status=$?
if [ "$world_status" -ne 0 ]; then
  if [ "$world_status" -eq 132 ]; then
    echo ">> @world died with Illegal instruction; rebuilding from source" >&2
    disable_binpkgs
  else
    echo ">> emerge @world failed ($world_status); retrying once from source (--usepkg=n)" >&2
  fi
  dump_world_emerge_failure "$world_status"
  emerge --verbose --update --deep {world_use_flag} --keep-going --usepkg=n --getbinpkg=n @world || {{
    _world_retry_status=$?
    echo ">> @world source rebuild failed ($_world_retry_status)" >&2
    dump_world_emerge_failure "$_world_retry_status"
    exit 1
  }}
fi
echo ">> emerge selected packages"
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
# package's installkernel hook runs dracut. ZFS / linux-mod packages wait until
# /usr/src/linux exists (after kernel emerge + symlink/eselect).
{repair_tools}
{theme_block}
{plymouth_early}
{missed_block}
{firmware_block}
echo ">> emerge kernel"
{kernel_emerge_line}
{kernel_seek}
if [ "$kernel_found" -eq 0 ]; then
  echo ">> no kernel image in /boot or on /efi; building sys-kernel/gentoo-kernel from source"
  {kernel_fallback}
  {kernel_seek}
fi
{kernel_sources_gate}
{post_kernel_modules}
{late_block}
{stores_block}

{swapfile}
{zram_block}
{user_block}
{session_extra}
{tpm_script}
{selinux_block}
{nftables_block}
{ufw_block}
{mise_invite_block}
{fstrim_block}

{grub_install}
{plymouth_block}
{wm_required_block}
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
  echo 'dns_servers_{netifrc_iface}="{dns_servers_netifrc}"' >> /etc/conf.d/net || true
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


def _profile_fallbacks(cfg: InstallConfig) -> list[str]:
    """Valid 23.0 profiles to try when the saved path is missing or broken."""
    arch = "amd64" if cfg.architecture == "amd64" else "arm64"
    if cfg.stage_variant == "musl":
        return [f"default/linux/{arch}/23.0/musl", f"default/linux/{arch}/23.0"]
    if cfg.init_system == "systemd":
        return [
            f"default/linux/{arch}/23.0/desktop/systemd",
            f"default/linux/{arch}/23.0/systemd",
            f"default/linux/{arch}/23.0/desktop",
            f"default/linux/{arch}/23.0",
        ]
    return [
        f"default/linux/{arch}/23.0/desktop",
        f"default/linux/{arch}/23.0",
        f"default/linux/{arch}/23.0/desktop/systemd",
        f"default/linux/{arch}/23.0/systemd",
    ]


def _portage_tree_sync_script(cfg: InstallConfig) -> str:
    """Create ``/var/db/repos/gentoo``, then (for git) write repos.conf, then sync.

    Order matters: never point ``repos.conf`` ``location=`` at a path that does
    not exist yet. Fail closed if sync does not leave the tree on disk (the
    caller also runs ``_gentoo_tree_present_script``).
    """
    method = (cfg.sync_method or "webrsync").strip()
    # Always create the location first (stage3 may not have made it).
    prelude = """
echo ">> create /var/db/repos/gentoo before repos.conf location="
mkdir -p /var/db/repos/gentoo
if [ ! -d /var/db/repos/gentoo ]; then
  echo ">> failed to create /var/db/repos/gentoo before Portage tree sync" >&2
  exit 1
fi
"""
    if method == "git":
        return (
            prelude
            + """
if ! command -v git >/dev/null 2>&1; then
  echo ">> git missing before Portage tree sync (sync_method=git)" >&2
  exit 1
fi
# Stage3 often ships /var/db/repos/gentoo without .git. Git sync-type cannot
# convert that tree in place — move it aside first so Portage can clone into
# an empty location. webrsync remains the default.
if [ -e /var/db/repos/gentoo ] && [ ! -e /var/db/repos/gentoo/.git ]; then
  if [ -n "$(ls -A /var/db/repos/gentoo 2>/dev/null)" ]; then
    aside="/var/db/repos/gentoo.pre-git.$$"
    echo ">> move aside non-git stage3 tree -> $aside"
    mv /var/db/repos/gentoo "$aside" || {
      echo ">> failed to move aside non-git /var/db/repos/gentoo for sync_method=git" >&2
      exit 1
    }
    mkdir -p /var/db/repos/gentoo
    if [ ! -d /var/db/repos/gentoo ]; then
      echo ">> failed to recreate /var/db/repos/gentoo after move-aside" >&2
      exit 1
    fi
  fi
fi
# Location directory exists — now safe to point repos.conf at it.
mkdir -p /etc/portage/repos.conf
cat > /etc/portage/repos.conf/gentoo.conf << 'EOF'
[gentoo]
location = /var/db/repos/gentoo
sync-type = git
sync-uri = https://github.com/gentoo-mirror/gentoo.git
auto-sync = yes
EOF
if ! grep -q 'location = /var/db/repos/gentoo' /etc/portage/repos.conf/gentoo.conf; then
  echo ">> repos.conf gentoo location not set to /var/db/repos/gentoo" >&2
  exit 1
fi
emerge --sync || {
  echo ">> Portage tree sync failed (sync_method=git)" >&2
  exit 1
}
"""
        )
    if method == "rsync":
        return (
            prelude
            + """
emerge --sync || {
  echo ">> Portage tree sync failed (emerge --sync / rsync)" >&2
  exit 1
}
"""
        )
    # webrsync (default)
    return (
        prelude
        + """
retry404 emerge-webrsync || retry404 emerge --sync || {
  echo ">> Portage tree sync failed (webrsync/emerge --sync)" >&2
  exit 1
}
"""
    )


def _gentoo_tree_present_script() -> str:
    """Fail closed unless the synced Gentoo ebuild tree is on disk.

    Stage3 unpack alone is not enough for ``eselect profile`` / emerge — the
    handbook tree sync must leave ``/var/db/repos/gentoo`` (with profiles).
    """
    return """
echo ">> verify Portage tree at /var/db/repos/gentoo"
if [ ! -d /var/db/repos/gentoo ]; then
  echo ">> /var/db/repos/gentoo missing after Portage tree sync (stage unpack is not enough)" >&2
  exit 1
fi
if [ ! -d /var/db/repos/gentoo/profiles ]; then
  echo ">> /var/db/repos/gentoo/profiles missing after Portage tree sync" >&2
  ls -la /var/db/repos/gentoo >&2 || true
  exit 1
fi
echo ">> Portage tree OK ($(ls -d /var/db/repos/gentoo/profiles | head -n 1))"
"""


def _profile_candidates(cfg: InstallConfig) -> list[str]:
    """Saved profile first, then the 23.0 fallbacks, without duplicates."""
    wanted = (cfg.profile or "").strip() or _profile_fallbacks(cfg)[0]
    candidates: list[str] = []
    for path in [wanted, *_profile_fallbacks(cfg)]:
        if path and path not in candidates:
            candidates.append(path)
    return candidates


def _profile_set_script(cfg: InstallConfig) -> str:
    """eselect profile set so make.profile is a symlink. Fail closed if not."""
    quoted = " ".join(shlex.quote(p) for p in _profile_candidates(cfg))
    return f"""echo ">> eselect profile"
if [ ! -d /var/db/repos/gentoo/profiles ]; then
  echo ">> cannot set profile — /var/db/repos/gentoo/profiles missing" >&2
  exit 1
fi
# Stage3 / resume can leave make.profile as a real directory or file. eselect
# profile set must create a symlink into /var/db/repos/gentoo — clear blockers first.
if [ -e /etc/portage/make.profile ] && [ ! -L /etc/portage/make.profile ]; then
  echo ">> removing non-symlink /etc/portage/make.profile so eselect can link into the gentoo tree"
  ls -la /etc/portage/make.profile >&2 || true
  rm -rf /etc/portage/make.profile || {{
    echo ">> failed to remove non-symlink /etc/portage/make.profile" >&2
    exit 1
  }}
fi
profile_ok=0
for profile_path in {quoted}; do
  echo ">> trying profile $profile_path"
  # Link first. eselect loads Portage, which warns when make.profile is missing.
  link_make_profile "$profile_path" || {{
    echo ">> profile $profile_path was not accepted" >&2
    continue
  }}
  if eselect profile set "$profile_path" || eselect profile set --force "$profile_path"; then
    if ! profile_has_marker /etc/portage/make.profile; then
      link_make_profile "$profile_path" || true
    fi
    # Handbook: make.profile must be a symlink into the tree (not a plain dir/file).
    if [ -L /etc/portage/make.profile ] && profile_has_marker /etc/portage/make.profile; then
      _mp_target=$(readlink -f /etc/portage/make.profile 2>/dev/null || true)
      if [ -n "$_mp_target" ] && [ -e "$_mp_target" ]; then
        echo ">> Portage profile is $profile_path (make.profile -> $_mp_target)"
        profile_ok=1
        break
      fi
    fi
    echo ">> /etc/portage/make.profile is not a symlink after eselect profile set $profile_path" >&2
    ls -la /etc/portage/make.profile >&2 || true
  fi
  echo ">> profile $profile_path was not accepted" >&2
done
if [ "$profile_ok" -ne 1 ]; then
  echo ">> valid Portage profile could not be set before emerge" >&2
  eselect profile list 2>/dev/null || true
  ls -la /etc/portage/make.profile >&2 || true
  exit 1
fi
if [ ! -L /etc/portage/make.profile ] || ! profile_has_marker /etc/portage/make.profile; then
  echo ">> /etc/portage/make.profile must be a symlink after eselect profile set" >&2
  ls -la /etc/portage/make.profile >&2 || true
  exit 1
fi
if [ ! -d /var/db/repos/gentoo ]; then
  echo ">> /var/db/repos/gentoo missing after profile set" >&2
  exit 1
fi
"""


def _overlay_script(cfg: InstallConfig) -> str:
    """Chroot: keyword+unmask helpers → emerge them → enable/sync each overlay → */*::ov.

    Runs *after* Handbook profile set. Wiki eselect/repository: mkdir repos.conf,
    emerge app-eselect/eselect-repository, then ``eselect repository enable`` and
    ``emaint sync``. package.accept_keywords / package.unmask (including
    */*::overlay) must exist before later emerge of overlay packages.
    """
    overlays = _required_overlays(cfg)
    if not overlays:
        return ""
    helper_kw = _overlay_helper_keyword_text(cfg).rstrip("\n")
    helper_um = _overlay_helper_unmask_text().rstrip("\n")
    repo_kw = _overlay_repo_keyword_text(cfg, overlays).rstrip("\n")
    repo_um = _overlay_repo_unmask_text(cfg, overlays).rstrip("\n")
    # Git: same keyword/UTF-8/verbose/error-surface path as pre-sync ensure.
    git_block = _emerge_git_block(cfg, reason="before overlay enable/sync").rstrip("\n")
    lines = [
        "echo \">> eselect repository (overlays; after profile)\"",
        # Wiki: REPOS_CONF directory must exist before the module works.
        "mkdir -p /etc/portage/repos.conf /etc/portage/package.accept_keywords /etc/portage/package.unmask",
        "cat > /etc/portage/package.accept_keywords/overlay-helpers << 'EOF'",
        helper_kw,
        "EOF",
        "cat > /etc/portage/package.unmask/overlay-helpers << 'EOF'",
        helper_um,
        "EOF",
        git_block,
        # Same emerge_one path as git (SIGILL / --usepkg=n source retry).
        "_eselect_save_nr=$NR",
        "NR=--noreplace",
        "emerge_one app-eselect/eselect-repository",
        "eselect_emerge_status=$?",
        "NR=$_eselect_save_nr",
        'if [ "$eselect_emerge_status" -ne 0 ]; then '
        'echo ">> eselect-repository required for overlays" >&2; '
        "emerge -pv app-eselect/eselect-repository >&2 || true; exit 1; fi",
        "if ! command -v eselect >/dev/null 2>&1; then "
        'echo ">> eselect missing — cannot enable overlays" >&2; exit 1; fi',
    ]
    for ov in overlays:
        qov = shlex.quote(ov)
        qdir = shlex.quote(f"/var/db/repos/{ov}")
        # Word-ish match on list -i; overlay names are simple (guru, suckless-extra).
        ov_re = re.escape(ov)
        lines.append("unset FEATURES")
        # Idempotent: resume re-runs this block; "already enabled" must not be fatal.
        # Names absent from remote repositories.xml get a clear stale-name error
        # instead of the raw "not listed in repositories.xml" eselect dump.
        lines.append(
            f"if eselect repository list -i 2>/dev/null | grep -Eq '{ov_re}'; then\n"
            f"  echo \">> overlay {ov} already enabled; skip enable\"\n"
            f"elif [ -d {qdir} ]; then\n"
            f"  echo \">> overlay {ov} present at {qdir}; skip enable\"\n"
            f"elif eselect repository list 2>/dev/null | grep -Eq '{ov_re}'; then\n"
            f"  eselect repository enable {qov} || {{\n"
            f"    if eselect repository list -i 2>/dev/null | grep -Eq '{ov_re}' "
            f"|| [ -d {qdir} ]; then\n"
            f"      echo \">> overlay {ov} already enabled/present; continue\"\n"
            f"    else\n"
            f"      echo \">> failed to enable overlay {ov}\" >&2\n"
            f"      exit 1\n"
            f"    fi\n"
            f"  }}\n"
            f"else\n"
            f"  echo \">> overlay {ov} is not listed in repositories.xml "
            f'(stale or renamed name)" >&2\n'
            f"  exit 1\n"
            f"fi"
        )
        lines.append(
            f"if ! eselect repository list -i 2>/dev/null | grep -Eq '{ov_re}'; then\n"
            f"  if [ -d {qdir} ]; then\n"
            f"    echo \">> overlay {ov} present at {qdir}; continue\"\n"
            f"  else\n"
            f"    echo \">> overlay {ov} not listed as installed after enable\" >&2\n"
            f"    exit 1\n"
            f"  fi\n"
            f"fi"
        )
        lines.append("unset FEATURES")
        lines.append(
            f"emaint sync --repo {qov} || {{ "
            f'echo ">> failed to sync overlay {ov}" >&2; exit 1; }}'
        )
    # After every selected overlay is enabled+synced, keyword/unmask */*::name
    # before any later emerge of packages from those overlays.
    lines.extend(
        [
            "cat > /etc/portage/package.accept_keywords/overlays << 'EOF'",
            repo_kw,
            "EOF",
            "cat > /etc/portage/package.unmask/overlays << 'EOF'",
            repo_um,
            "EOF",
        ]
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
    luks_grub = bool(cfg.luks and "grub" in loaders)
    if luks_grub:
        # Before grub-install: cryptodisk must be on so EFI payload embeds LUKS modules.
        cmds.append(_grub_cryptodisk_default_script())
    for bl in loaders:
        if bl == "grub":
            if cfg.firmware == "uefi":
                if cfg.architecture == "arm64":
                    grub_target = "arm64-efi"
                elif cfg.uefi_bits == 32:
                    grub_target = "i386-efi"
                else:
                    grub_target = "x86_64-efi"
                cmds.append(_grub_uefi_install(grub_target, fail_closed=luks_grub))
            else:
                disk = shlex.quote(cfg.target_disk)
                bios_source_fail = (
                    "echo '>> grub source build failed' >&2; exit 1"
                    if luks_grub
                    else "echo '>> grub source build failed' >&2"
                )
                bios_install_fail = (
                    'echo ">> grub-install failed" >&2; exit 1'
                    if luks_grub
                    else 'echo ">> grub-install failed" >&2'
                )
                bios_mkconfig = (
                    "grub-mkconfig -o /boot/grub/grub.cfg || "
                    "{ echo '>> grub-mkconfig failed' >&2; exit 1; }"
                    if luks_grub
                    else "grub-mkconfig -o /boot/grub/grub.cfg || echo '>> grub-mkconfig failed' >&2"
                )
                cmds.append(
                    "\n".join(
                        [
                            "mkdir -p /boot/grub",
                            f"grub-install {disk}",
                            "grub_status=$?",
                            'if [ "$grub_status" -eq 132 ]; then',
                            '  echo ">> grub-install: illegal instruction; rebuilding sys-boot/grub from source"',
                            "  disable_binpkgs",
                            f"  emerge --ask=n --usepkg=n --getbinpkg=n sys-boot/grub || {bios_source_fail}",
                            f"  grub-install {disk}",
                            "  grub_status=$?",
                            "fi",
                            f'if [ "$grub_status" -ne 0 ]; then {bios_install_fail}; fi',
                            bios_mkconfig,
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
            cmds.append(_retry_sigill(atom, "bootctl install --esp-path=/efi"))
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
    return bare in catalog.offered_guru_atoms(getattr(cfg, "shell", "") or "")


def _dotfile_for(cfg: InstallConfig, atom: str) -> catalog.DotfileChoice | None:
    """The saved config choice for this compositor, or the installer default."""
    choices = catalog.dotfile_choices(atom)
    if not choices:
        return None
    if catalog.dotfiles_skipped(getattr(cfg, "shell", "") or ""):
        return choices[0]
    wanted = "installer"
    for item in cfg.wm_dotfiles or []:
        key, _, value = str(item).partition("=")
        if key == atom and value:
            wanted = value
            break
    for choice in choices:
        if choice.id == wanted:
            return choice
    return choices[0]


def _wm_dotfile_fetch_lines() -> list[str]:
    """Shell helper: copy the latest GitHub compositor config into the user's home.

    The commit at the tip of the recorded branch is resolved first, then that
    commit is downloaded. A cached branch archive is only the fallback.
    """
    return [
        "  fetch_wm_dotfiles() {",
        '    repo="$1"; branch="$2"; source="$3"; dest="$4"; kind="$5"',
        "    tmp=$(mktemp -d)",
        "    _wm_download() {",
        '      url="$1"; out="$2"',
        '      if command -v curl >/dev/null 2>&1 && curl -fsSL -H "Cache-Control: no-cache" -H "Pragma: no-cache" "$url" -o "$out"; then return 0; fi',
        '      if command -v wget >/dev/null 2>&1 && wget -q --no-cache -O "$out" "$url"; then return 0; fi',
        '      if emerge --ask=n net-misc/curl && curl -fsSL -H "Cache-Control: no-cache" -H "Pragma: no-cache" "$url" -o "$out"; then return 0; fi',
        "      return 1",
        "    }",
        '    sha=""',
        '    if _wm_download "https://api.github.com/repos/${repo}/commits/${branch}" "$tmp/commit.json"; then',
        '      sha=$(sed -n \'s/.*"sha": *"\\([0-9a-f]\\{40\\}\\)".*/\\1/p\' "$tmp/commit.json" | head -n 1)',
        "    fi",
        '    ref="$branch"',
        '    folder="$branch"',
        '    if [ -n "$sha" ]; then',
        '      ref="$sha"',
        '      folder="$sha"',
        '      echo ">> syncing latest ${repo} ${branch} at ${sha}"',
        "    else",
        '      echo ">> could not read the latest ${repo} commit; downloading branch ${branch}" >&2',
        "    fi",
        '    if [ "$kind" = "file" ]; then',
        '      mkdir -p "$(dirname "$dest")"',
        '      if _wm_download "https://raw.githubusercontent.com/${repo}/${ref}/${source}" "$dest"; then',
        '        rm -rf "$tmp"; return 0',
        "      fi",
        '      rm -f "$dest"; rm -rf "$tmp"; return 1',
        "    fi",
        '    if [ -n "$sha" ]; then',
        '      archive="https://github.com/${repo}/archive/${sha}.tar.gz"',
        "    else",
        '      archive="https://github.com/${repo}/archive/refs/heads/${branch}.tar.gz"',
        "    fi",
        '    if ! _wm_download "$archive" "$tmp/src.tar.gz"; then',
        '      rm -rf "$tmp"; return 1',
        "    fi",
        '    tar -xzf "$tmp/src.tar.gz" -C "$tmp" || { rm -rf "$tmp"; return 1; }',
        '    base="${repo##*/}"',
        '    root="$tmp/${base}-${folder}/${source}"',
        '    if [ ! -e "$root" ]; then',
        '      root=$(find "$tmp" -path "*/${source}" | head -n 1)',
        "    fi",
        '    if [ -z "$root" ] || [ ! -e "$root" ]; then rm -rf "$tmp"; return 1; fi',
        '    rm -rf "$dest"',
        '    mkdir -p "$(dirname "$dest")"',
        '    if ! cp -a "$root" "$dest"; then rm -rf "$tmp"; return 1; fi',
        '    rm -rf "$tmp"',
        "    return 0",
        "  }",
    ]


def _dotfile_attempt(choice: catalog.DotfileChoice, dest_shell: str, flag: str, title: str) -> list[str]:
    """Try the GitHub copy. The flag stays 0 so the installer config can be written."""
    args = " ".join(
        [
            shlex.quote(choice.repo),
            shlex.quote(choice.branch),
            shlex.quote(choice.source),
            dest_shell,
            shlex.quote(choice.kind),
        ]
    )
    return [
        f"  {flag}=0",
        f"  if fetch_wm_dotfiles {args}; then",
        f"    {flag}=1",
        f'    echo ">> {title} dotfiles: {choice.id}"',
        "  else",
        f'    echo ">> {title} dotfiles were not copied; writing the installer config" >&2',
        "  fi",
    ]


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
    hypr_choice = _dotfile_for(cfg, "gui-wm/hyprland") if "gui-wm/hyprland" in wms else None
    niri_choice = _dotfile_for(cfg, "gui-wm/niri") if "gui-wm/niri" in wms else None
    if (hypr_choice and hypr_choice.repo) or (niri_choice and niri_choice.repo):
        lines += _wm_dotfile_fetch_lines()
    if "gui-wm/hyprland" in wms:
        # hyproverlay tip is Lua-only from Hyprland 0.57; write hyprland.lua.
        lines.append('  hypr="$userhome/.config/hypr/hyprland.lua"')
        if hypr_choice and hypr_choice.repo:
            lines += _dotfile_attempt(hypr_choice, '"$userhome/.config/hypr"', "hypr_dots", "Hyprland")
            lines.append('  if [ "$hypr_dots" != 1 ]; then')
        lines += [
            '  install -d -m 755 "$userhome/.config/hypr"',
            '  if [ ! -f "$hypr" ]; then',
            '    cat > "$hypr" << EOF',
            "hl.monitor({",
            '    output = "",',
            '    mode = "preferred",',
            '    position = "auto",',
            "    scale = 1,",
            "})",
            "hl.config({",
            "    input = {",
            f'        kb_layout = "{keymap}",',
            "    },",
            "})",
            "EOF",
            f'    chown -R {user}:{user} "$userhome/.config/hypr" || true',
            "  fi",
        ]
        if hypr_choice and hypr_choice.repo:
            lines.append("  fi")
            lines.append(f'  chown -R {user}:{user} "$userhome/.config/hypr" || true')
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
    if "gui-wm/niri" in wms:
        queued = set(_graphical_atoms(cfg))
        spawns: list[str] = []
        if shell:
            spawns.append(f'spawn-at-startup "{shell}"')
        if "gui-apps/waybar" in queued:
            spawns.append('spawn-at-startup "waybar"')
        if "gui-apps/mako" in queued:
            spawns.append('spawn-at-startup "mako"')
        if "gui-apps/swaybg" in queued:
            spawns.append('spawn-at-startup "swaybg" "-c" "#54487A"')
        if "gui-apps/swayidle" in queued and "gui-apps/swaylock" in queued:
            spawns.append('spawn-at-startup "swayidle" "-w" "timeout" "300" "swaylock"')
        binds = [
            '    Mod+Return { spawn "foot"; }',
            '    Mod+Q { close-window; }',
            '    Mod+F { fullscreen-window; }',
            "    Mod+Left { focus-column-left; }",
            "    Mod+Right { focus-column-right; }",
            "    Mod+Shift+E { quit; }",
        ]
        if "gui-apps/wofi" in queued:
            binds.insert(1, '    Mod+D { spawn "wofi" "--show" "drun"; }')
        if "gui-apps/swaylock" in queued:
            binds.append('    Mod+Shift+L { spawn "swaylock"; }')
        lines.append('  niricfg="$userhome/.config/niri/config.kdl"')
        niri_remote = bool(niri_choice and niri_choice.repo)
        if niri_remote and niri_choice is not None:
            lines += _dotfile_attempt(
                niri_choice,
                f'"$userhome/{niri_choice.dest}"',
                "niri_dots",
                "Niri",
            )
            lines.append('  if [ "$niri_dots" != 1 ]; then')
        lines += [
            '  install -d -m 755 "$userhome/.config/niri"',
            '  if [ ! -f "$niricfg" ]; then',
            "    copied=0",
            "    for sample in /usr/share/niri/default-config.kdl /usr/share/niri/config.kdl /etc/niri/config.kdl; do",
            '      if [ -f "$sample" ]; then cp "$sample" "$niricfg"; copied=1; break; fi',
            "    done",
            '    if [ "$copied" = 0 ]; then',
            "      cat > \"$niricfg\" << 'EOF'",
            "input {",
            "    keyboard {",
            "        xkb {",
            f'            layout "{keymap}"',
            "        }",
            "    }",
            "}",
        ]
        lines.extend(spawns)
        lines.append("binds {")
        lines.extend(binds)
        lines += [
            "}",
            "EOF",
            "    fi",
            "  fi",
        ]
        if niri_remote:
            lines.append("  fi")
        shell_spawns = [spawn for spawn in spawns if shell and f'"{shell}"' in spawn]
        extra_spawns = [spawn for spawn in spawns if spawn not in shell_spawns]

        def _append_niri_spawns(items: list[str]) -> None:
            for spawn in items:
                lines.append(
                    f'  if [ -f "$niricfg" ] && ! grep -q {shlex.quote(spawn)} "$niricfg"; then'
                )
                lines.append(f'    echo {shlex.quote(spawn)} >> "$niricfg"')
                lines.append("  fi")

        _append_niri_spawns(shell_spawns)
        if niri_remote and extra_spawns:
            lines.append('  if [ "$niri_dots" != 1 ]; then')
            _append_niri_spawns(extra_spawns)
            lines.append("  fi")
        elif not niri_remote:
            _append_niri_spawns(extra_spawns)
        lines.append(f'  chown -R {user}:{user} "$userhome/.config/niri" || true')
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
    """Optional browsers (from cfg.browser), PipeWire, and Flatpak. No DE metas."""
    pkgs: list[str] = list(catalog.browser_atoms_for(cfg.browser))
    if cfg.pipewire:
        pkgs.extend(["media-video/pipewire", "media-video/wireplumber"])
    if cfg.flatpak:
        pkgs.append("sys-apps/flatpak")
    return list(dict.fromkeys(pkgs))


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
mkdir --parents /efi/EFI/BOOT /efi/EFI/limine
cp -v /usr/share/limine/{efi_bin} /efi/EFI/BOOT/{efi_bin}
cp -v /usr/share/limine/{efi_bin} /efi/EFI/limine/{efi_bin} || true
cat > /efi/limine.conf << 'LIMINE'
{conf}
LIMINE
cp /efi/limine.conf /efi/EFI/BOOT/limine.conf || true
KERNEL=$(ls -1 /boot/vmlinuz-* 2>/dev/null | tail -n1 || true)
INITRD=$(ls -1 /boot/initramfs* /boot/initrd* 2>/dev/null | tail -n1 || true)
# The ESP is FAT. Symlinks are refused, and Limine loads boot(): from that partition.
[ -n "$KERNEL" ] && cp -f "$KERNEL" /efi/vmlinuz
[ -n "$INITRD" ] && cp -f "$INITRD" /efi/initramfs
if command -v efibootmgr >/dev/null 2>&1; then
  efibootmgr --create --disk {shlex.quote(disk)} --part {part} --label "Gentoo Limine" --loader '{loader}' --unicode || true
fi
"""


def _apply_live_firmware(cfg: InstallConfig) -> None:
    """Read IA32 UEFI from the live system and keep GRUB as the only loader."""
    if cfg.firmware != "uefi":
        return
    bits = detect.uefi_platform_size()
    if bits in {32, 64}:
        cfg.uefi_bits = bits
    if cfg.uefi_bits != 32:
        return
    dropped = [name for name in cfg.bootloaders if name != "grub"]
    if cfg.bootloader == "grub" and not dropped:
        return
    ui.warn(
        "32-bit UEFI: GRUB will be installed with the i386-efi target. "
        "systemd-boot, rEFInd, and EFI stub need 64-bit firmware."
    )
    cfg.bootloaders = ["grub"]
    cfg.bootloader = "grub"


def _kernel_found_shell(var: str) -> str:
    """Set var=1 when a kernel image is in /boot or on the ESP."""
    return (
        f"{var}=0\n"
        f"if ls /boot/vmlinuz* >/dev/null 2>&1; then {var}=1; fi\n"
        f"if ls /efi/EFI/Linux/*.efi >/dev/null 2>&1; then {var}=1; fi\n"
        f"if ls /boot/EFI/Linux/*.efi >/dev/null 2>&1; then {var}=1; fi\n"
        f"if ls /efi/EFI/Gentoo/*.efi >/dev/null 2>&1; then {var}=1; fi\n"
        f"if ls /efi/vmlinuz* >/dev/null 2>&1; then {var}=1; fi\n"
    )


def _installkernel_setup(cfg: InstallConfig) -> str:
    """package.use plus install.conf. systemd-boot and EFI stub keep the kernel on /efi."""
    if cfg.bootloader == "systemd-boot":
        flags = ["systemd", "systemd-boot", "dracut"]
        layout = "systemd-boot"
        if cfg.uki:
            flags.append("uki")
            layout = "uki"
        esp = True
    elif cfg.bootloader == "efistub":
        flags = ["efistub", "dracut"]
        layout = "efistub"
        if cfg.uki:
            flags.append("uki")
            layout = "uki"
        esp = True
    elif cfg.bootloader == "grub":
        flags = ["grub", "dracut"]
        layout = ""
        esp = False
    else:
        flags = ["dracut"]
        layout = ""
        esp = False
    use = "sys-kernel/installkernel " + " ".join(dict.fromkeys(flags))
    lines = [f"append_once /etc/portage/package.use/installkernel '{use}'"]
    if not layout:
        return "\n".join(lines)
    conf = f"layout={layout}\ninitrd_generator=dracut\n"
    if layout == "uki":
        conf += "uki_generator=dracut\n"
    lines.append("mkdir -p /etc/kernel /efi/EFI/Gentoo /efi/EFI/Linux" if esp else "mkdir -p /etc/kernel")
    lines.append("cat > /etc/kernel/install.conf << 'EOF'")
    lines.append(conf.rstrip())
    lines.append("EOF")
    return "\n".join(lines)


def _mirrorselect_block(cfg: InstallConfig) -> str:
    """Emerge mirrorselect and write GENTOO_MIRRORS when the wizard left it empty."""
    if cfg.gentoo_mirrors.strip():
        return ""
    return """
echo ">> select fast mirrors"
emerge --ask=n --oneshot --verbose $NR app-portage/mirrorselect || true
if command -v mirrorselect >/dev/null 2>&1; then
  mirrorselect -s3 -b10 -o >> /etc/portage/make.conf || true
fi
if ! grep -q '^GENTOO_MIRRORS=' /etc/portage/make.conf; then
  echo 'GENTOO_MIRRORS="https://distfiles.gentoo.org"' >> /etc/portage/make.conf
fi
"""


def _nftables_block(cfg: InstallConfig) -> str:
    """Write the same ruleset for OpenRC (rules-save) and systemd (nftables.conf)."""
    if not cfg.nftables:
        return ""
    ssh = "        tcp dport 22 accept\n" if cfg.sshd else ""
    rules = f"""flush ruleset
table inet filter {{
        chain input {{
                type filter hook input priority filter; policy drop;
                ct state invalid drop
                ct state established,related accept
                iifname "lo" accept
                meta l4proto icmp accept
                meta l4proto icmpv6 accept
{ssh}        }}
        chain forward {{
                type filter hook forward priority filter; policy drop;
        }}
        chain output {{
                type filter hook output priority filter; policy accept;
        }}
}}
"""
    return f"""
echo ">> nftables"
mkdir -p /var/lib/nftables /etc
cat > /etc/nftables.conf << 'EOF'
{rules.strip()}
EOF
cp -f /etc/nftables.conf /var/lib/nftables/rules-save
"""


# LocalSend-style ports when cfg.ufw_localsend is True (opt-in).
_LOCALSEND_UFW_PORTS = (
    {"port": 53317, "proto": "udp"},
    {"port": 53317, "proto": "tcp"},
)

# Default mise agent names when the invite is enabled without an explicit list.
_DEFAULT_MISE_AGENT_TOOLS = ("claude", "codex", "opencode", "cursor-agent")


def _ufw_block(cfg: InstallConfig) -> str:
    """Configure UFW deny-incoming / allow-outgoing when UFW is the chosen stack.

    LocalSend-style holes are opt-in only (Gentoo/OpenRC-safe minimal defaults).
    """
    if cfg.nftables or not cfg.ufw:
        return ""

    ports = list(_LOCALSEND_UFW_PORTS) if cfg.ufw_localsend else []
    allow_lines = []
    if cfg.sshd:
        allow_lines.append("ufw allow OpenSSH || ufw allow 22/tcp || true")
    for entry in ports:
        port = int(entry.get("port", 53317))
        proto = str(entry.get("proto", "tcp"))
        allow_lines.append(f"ufw allow {port}/{proto} || true")
    allows = "\n".join(allow_lines)
    enable_openrc = ""
    if cfg.init_system == "openrc":
        enable_openrc = """
if [ -e /etc/init.d/ufw ]; then
  rc-update add ufw default || true
fi
"""
    else:
        enable_openrc = """
systemctl enable ufw || true
"""
    return f"""
echo ">> ufw (deny incoming / allow outgoing)"
if ! command -v ufw >/dev/null 2>&1; then
  echo ">> ufw binary missing; skipping UFW defaults" >&2
else
  ufw --force reset || true
  ufw default deny incoming || true
  ufw default allow outgoing || true
{textwrap.indent(allows, "  ") if allows else ""}
  sed -i 's/^ENABLED=.*/ENABLED=yes/' /etc/ufw/ufw.conf 2>/dev/null || true
  ufw --force enable || true
{enable_openrc}
fi
"""


def _mise_agents_invite_block(cfg: InstallConfig) -> str:
    """First-login invite for optional mise + small agent set (no panel / NOPASSWD)."""
    if not cfg.mise_agents:
        return ""
    tools = list(cfg.mise_agent_tools) or list(_DEFAULT_MISE_AGENT_TOOLS)
    tools_csv = ", ".join(tools)
    tools_toml = "\n".join(
        f'{name} = {{ version = "latest", lazy = true }}' for name in tools
    )
    user = (cfg.username or "").strip()
    if not user:
        return """
echo ">> mise/agents invite skipped (no non-root user)"
"""
    # Invite + sample mise config only — never enable passwordless sudo for agents.
    return f"""
echo ">> first-login mise/agents invite"
userhome=$(getent passwd {shlex.quote(user)} | cut -d: -f6 || true)
if [ -n "$userhome" ] && [ -d "$userhome" ]; then
  install -d -m 755 "$userhome/.config/mise" "$userhome/.config/oops-installer"
  cat > "$userhome/.config/oops-installer/mise-agents-invite.txt" << 'INVITE'
Oops-Installer — optional mise + AI agents (first login)
========================================================

This note is an invitation only. No agent was installed as a default.
There is no Quickshell agents panel and no passwordless sudo for agents.

1. Install mise (not in the official Gentoo tree at the time of this install):

     curl https://mise.run | sh
     # then add mise to your shell per https://mise.jdx.dev/getting-started.html

   Or emerge from a trusted third-party overlay that ships dev-util/mise, if you
   prefer Portage.

2. Optional sample tools were written to ~/.config/mise/config.toml when missing.
   Activate lazy shims after mise is on PATH:

     mise trust
     mise install
     mise reshim

Suggested tools for this install: {tools_csv}

3. Decline anytime: delete ~/.config/oops-installer/mise-agents-invite.txt and
   ~/.config/mise/config.toml (or edit tools out).
INVITE
  if [ ! -f "$userhome/.config/mise/config.toml" ]; then
    cat > "$userhome/.config/mise/config.toml" << 'MISE'
# Optional Oops-Installer sample mise tools.
# Safe to delete. No passwordless sudo is configured by the installer.
[tools]
{tools_toml}
MISE
  fi
  chown -R {shlex.quote(user)}:{shlex.quote(user)} \\
    "$userhome/.config/oops-installer" "$userhome/.config/mise" || true
  # Also drop a short pointer next to HOWTO-style docs if the tree has HOWTO.txt.
  if [ -f /root/HOWTO.txt ] || [ -f /HOWTO.txt ]; then
    true
  fi
fi
"""


def _fstrim_block(cfg: InstallConfig) -> str:
    """Weekly trim. systemd uses fstrim.timer. OpenRC uses cron."""
    if not cfg.fstrim:
        return ""
    if cfg.init_system == "systemd":
        return """
echo ">> weekly fstrim"
systemctl enable fstrim.timer || echo ">> fstrim.timer was not enabled" >&2
"""
    fcron = ""
    if cfg.cron == "fcron":
        fcron = """
mkdir -p /etc/fcron
if ! grep -q '/etc/cron.weekly/fstrim' /etc/fcron/systab 2>/dev/null; then
  echo '%weekly * * /etc/cron.weekly/fstrim' >> /etc/fcron/systab
fi
"""
    return f"""
echo ">> weekly fstrim"
mkdir -p /etc/cron.weekly
cat > /etc/cron.weekly/fstrim << 'EOF'
#!/bin/sh
exec fstrim -A
EOF
chmod 755 /etc/cron.weekly/fstrim
{fcron}
"""


def _fstab_keep_node(node: str) -> bool:
    """LVM, LUKS mapper paths, and ZFS dataset names stay as device nodes.

    The handbook says filesystem UUIDs on LVM collide with snapshots.
    """
    if not node.startswith("/dev/"):
        return True
    if node.startswith("/dev/mapper/"):
        return True
    parts = [part for part in node.split("/") if part]
    if len(parts) >= 3:
        return True
    return False


def _stable_fstab_source(cfg: InstallConfig, node: str) -> str:
    """PARTUUID on GPT, otherwise the filesystem UUID. Dry-run keeps the node."""
    if not node or cfg.dry_run or _fstab_keep_node(node):
        return node
    uuid, partuuid = detect.blkid_ids(node)
    if cfg.partition_table == "gpt" and partuuid:
        return f"PARTUUID={partuuid}"
    if uuid:
        return f"UUID={uuid}"
    return node


def _fstab(cfg: InstallConfig, devices: dict[str, str]) -> str:
    """Generate /etc/fstab. Plain partitions use PARTUUID or UUID."""
    lines = ["# /etc/fstab: generated by gentoo-live-installer"]
    root = _stable_fstab_source(cfg, devices.get("root", ""))
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
        esp = _stable_fstab_source(cfg, devices["esp"])
        lines.append(f"{esp} /efi vfat noatime,umask=0077 0 2")
    elif devices.get("boot") and devices["boot"] != devices.get("esp"):
        boot = _stable_fstab_source(cfg, devices["boot"])
        lines.append(f"{boot} /boot ext4 noatime 0 2")
    if devices.get("swap") and cfg.swap_mode in {"partition", "both"}:
        swap = _stable_fstab_source(cfg, devices["swap"])
        lines.append(f"{swap} none swap sw 0 0")
    lines.append("proc /proc proc defaults 0 0")
    return "\n".join(lines) + "\n"
