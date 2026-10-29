"""Hardware and live-environment detection.

These probes run on the Ubuntu live host *before* any disk is changed.
On Windows (developer machines) they return empty/False so the wizard
can still be walked through with --wizard-only / --dry-run.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_mounted(path: str) -> bool:
    """True if path is a current mountpoint (Linux). Safe on Windows (False)."""
    if not path:
        return False
    try:
        return Path(path).is_mount()
    except OSError:
        return False


def is_root() -> bool:
    if hasattr(os, "geteuid"):
        return os.geteuid() == 0
    return False


def is_uefi() -> bool:
    """Linux exposes firmware EFI vars at /sys/firmware/efi when booted in UEFI."""
    return Path("/sys/firmware/efi").is_dir()


def uefi_platform_size() -> int | None:
    """UEFI bitness from the kernel: 64, 32, or None when not booted in UEFI.

    Arch checks this file. 32 means IA32 firmware, including mixed mode, which
    cannot use a 64-bit EFI stub or systemd-boot.
    """
    path = Path("/sys/firmware/efi/fw_platform_size")
    if not path.is_file():
        return None
    text = path.read_text(errors="ignore").strip()
    if text in {"32", "64"}:
        return int(text)
    return None


def apply_live_keymap(keymap: str) -> bool:
    """Set the live text-console layout. A graphical terminal is unchanged."""
    if not keymap or not is_linux() or not shutil.which("loadkeys"):
        return False
    proc = subprocess.run(
        ["loadkeys", keymap],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def disk_size_bytes(path: str) -> int | None:
    """Size of a whole disk in bytes, from lsblk."""
    if not path or not is_linux() or not shutil.which("lsblk"):
        return None
    proc = subprocess.run(
        ["lsblk", "-bdn", "-o", "SIZE", path],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stdout or "").strip()
    if text.isdigit():
        return int(text)
    return None


def disk_rotational(path: str) -> bool | None:
    """True for a spinning disk, False for an SSD, None when sysfs is absent."""
    if not path or not is_linux():
        return None
    name = Path(parent_block_device(path)).name
    queue = Path("/sys/block") / name / "queue" / "rotational"
    if not queue.is_file():
        return None
    flag = queue.read_text(errors="ignore").strip()
    if flag == "1":
        return True
    if flag == "0":
        return False
    return None


def blkid_ids(node: str) -> tuple[str, str]:
    """Filesystem UUID and partition PARTUUID. Empty strings when blkid cannot see the node."""
    if not node or not is_linux() or not shutil.which("blkid"):
        return "", ""
    proc = subprocess.run(
        ["blkid", "-o", "export", node],
        capture_output=True,
        text=True,
        check=False,
    )
    uuid = ""
    partuuid = ""
    for line in (proc.stdout or "").splitlines():
        if line.startswith("UUID="):
            uuid = line.split("=", 1)[1].strip()
        elif line.startswith("PARTUUID="):
            partuuid = line.split("=", 1)[1].strip()
    return uuid, partuuid


def blkid_type(node: str) -> str:
    """Filesystem/crypto TYPE from blkid (lowercased), or empty when unknown."""
    if not node or not is_linux() or not shutil.which("blkid"):
        return ""
    proc = subprocess.run(
        ["blkid", "-o", "value", "-s", "TYPE", node],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip().lower()


def detect_amd_cpu() -> bool:
    cpu = Path("/proc/cpuinfo")
    if not cpu.exists():
        return False
    return "AuthenticAMD" in cpu.read_text(errors="ignore")


def is_virtual_machine() -> bool:
    """True when the live system is a guest. Used for the microcode warning."""
    if shutil.which("systemd-detect-virt"):
        proc = subprocess.run(
            ["systemd-detect-virt", "-q"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            return True
    cpu = Path("/proc/cpuinfo")
    if cpu.exists() and "hypervisor" in cpu.read_text(errors="ignore"):
        return True
    return False


def nproc() -> int:
    return os.cpu_count() or 2


def has_network() -> bool:
    """True if distfiles.gentoo.org is reachable (stage3 + Portage need this)."""
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", "3", "gentoo.org"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # ping may be missing on a stripped live image; HTTPS is the real check.
    except FileNotFoundError:
        pass
    try:
        import urllib.request

        urllib.request.urlopen("https://distfiles.gentoo.org", timeout=8)
        return True
    except Exception:
        return False


def list_nics() -> list[dict]:
    """Physical-ish live NICs for the static/Wi-Fi/netifrc menus (not lo/docker/veth)."""
    nics: list[dict] = []
    sysnet = Path("/sys/class/net")
    if not sysnet.is_dir():
        return nics
    skip_prefixes = ("docker", "veth", "br-", "virbr", "tun", "tap", "wg", "tailscale")
    for path in sorted(sysnet.iterdir()):
        name = path.name
        if name == "lo" or name.startswith(skip_prefixes):
            continue
        oper = "?"
        oper_file = path / "operstate"
        if oper_file.exists():
            oper = oper_file.read_text(errors="ignore").strip() or "?"
        nics.append(
            {
                "name": name,
                "state": oper,
                "wireless": (path / "wireless").is_dir(),
            }
        )
    return nics


def default_nic(prefer_wireless: bool | None = None) -> str:
    """First usable NIC, optionally preferring Wi-Fi or wired."""
    nics = list_nics()
    if prefer_wireless is True:
        wifi = [n for n in nics if n["wireless"]]
        if wifi:
            nics = wifi
    elif prefer_wireless is False:
        wired = [n for n in nics if not n["wireless"]]
        if wired:
            nics = wired
    if nics:
        up = [n for n in nics if n["state"] == "up"]
        return (up or nics)[0]["name"]
    return "eth0"


def parent_block_device(src: str) -> str:
    """Partition → whole disk. nvme0n1p2 → nvme0n1; sda1 → sda; sr0 stays sr0."""
    if re.search(r"(nvme\d+n\d+|mmcblk\d+|loop\d+|md\d+)p\d+$", src):
        return re.sub(r"p\d+$", "", src)
    if re.search(r"/[shv]d[a-z]+\d+$", src):
        return re.sub(r"\d+$", "", src)
    return src


def partition_number(dev: str) -> int:
    """Trailing partition index from a kernel name (`sda3`, `nvme0n1p1`)."""
    name = Path(dev).name
    match = re.search(r"p?(\d+)$", name)
    return int(match.group(1)) if match else 1


def list_disks() -> list[dict]:
    """Whole disks only (no partitions). Used for the target-disk menu."""
    if not is_linux() or not shutil.which("lsblk"):
        return []
    proc = subprocess.run(
        ["lsblk", "-J", "-d", "-n", "-o", "NAME,SIZE,MODEL,TRAN,TYPE,RM"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    data = json.loads(proc.stdout or "{}")
    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        name = "/dev/" + dev["name"]
        disks.append(
            {
                "path": name,
                "size": dev.get("size") or "?",
                "model": (dev.get("model") or "").strip(),
                "tran": dev.get("tran") or "",
                "removable": str(dev.get("rm")) in {"1", "true", "True"},
            }
        )
    return disks


def partitions_from_blockdevices(devices: list[dict]) -> list[dict]:
    """Flatten an lsblk JSON blockdevices tree into partition records."""
    rows: list[dict] = []

    def walk(nodes: list[dict], parent_disk: str) -> None:
        for dev in nodes or []:
            name = str(dev.get("name") or "")
            path = name if name.startswith("/dev/") else f"/dev/{name}"
            kind = str(dev.get("type") or "").lower()
            disk = path if kind == "disk" else parent_disk
            if kind == "part":
                rows.append(
                    {
                        "path": path,
                        "disk": disk,
                        "size": dev.get("size") or "?",
                        "fstype": str(dev.get("fstype") or "").lower(),
                        "label": str(dev.get("label") or "").strip(),
                        "parttype": str(dev.get("parttypename") or "").strip(),
                    }
                )
            walk(dev.get("children") or [], disk)

    walk(devices, "")
    return rows


def partition_is_ntfs(part: dict) -> bool:
    return str(part.get("fstype") or "").lower() in {"ntfs", "ntfs3"}


def partition_is_microsoft(part: dict) -> bool:
    """NTFS, or a partition type Windows uses (basic data, recovery, reserved)."""
    if partition_is_ntfs(part):
        return True
    kind = str(part.get("parttype") or "").lower()
    return "microsoft" in kind or "windows" in kind


def list_partitions() -> list[dict]:
    """Every partition lsblk can see, with filesystem and partition type."""
    if not is_linux() or not shutil.which("lsblk"):
        return []
    columns = ("NAME,SIZE,TYPE,FSTYPE,LABEL,PARTTYPENAME", "NAME,SIZE,TYPE,FSTYPE,LABEL")
    proc = None
    for cols in columns:
        proc = subprocess.run(
            ["lsblk", "-J", "-n", "-o", cols],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            break
    if proc is None or proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return partitions_from_blockdevices(data.get("blockdevices") or [])


def live_boot_disk() -> str | None:
    """Best-effort parent disk of the live USB so we can warn 'do not wipe this'.

    Casper / Ubuntu Boot Image media show up under several mountpoints. Any of
    them is enough to exclude that disk from wipe and from partprobe.
    """
    findmnt = shutil.which("findmnt")
    if not findmnt:
        return None
    # Prefer dedicated live-media mounts, then the rootfs source (often a dm
    # snapshot whose backing disk is still the installer USB).
    candidates = (
        "/cdrom",
        "/run/live/medium",
        "/lib/live/mount/medium",
        "/isodevice",
        "/",
    )
    for mount in candidates:
        proc = subprocess.run(
            [findmnt, "-n", "-o", "SOURCE", mount],
            capture_output=True,
            text=True,
            check=False,
        )
        src = (proc.stdout or "").strip().split()[0] if proc.stdout else ""
        if not src.startswith("/dev/"):
            continue
        # Overlay /mapper names → resolve to the underlying block disk when possible.
        real = src
        try:
            real = str(Path(src).resolve())
        except OSError:
            real = src
        parent = parent_block_device(real)
        if parent.startswith("/dev/") and Path(parent).exists():
            return parent
        if src.startswith("/dev/"):
            return parent_block_device(src)
    return None


def is_live_installer_disk(path: str) -> bool:
    """True when path is the Ubuntu Boot Image medium (or a partition on it)."""
    if not path or not path.startswith("/dev/"):
        return False
    live = live_boot_disk()
    if not live:
        return False
    try:
        target = str(Path(parent_block_device(path)).resolve())
        medium = str(Path(live).resolve())
    except OSError:
        target = parent_block_device(path)
        medium = live
    return target == medium


# Linux flag names for the x86-64-v3 psABI. avx in cpuinfo already means the OS
# enabled AVX state, so OSXSAVE is not listed separately.
_X86_64_V3_FLAGS = frozenset({"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe"})


def _lspci_text() -> str:
    if not shutil.which("lspci"):
        return ""
    proc = subprocess.run(["lspci"], capture_output=True, text=True, check=False)
    return (proc.stdout or "").lower()


def _usb_text() -> str:
    if not shutil.which("lsusb"):
        return ""
    proc = subprocess.run(["lsusb"], capture_output=True, text=True, check=False)
    return (proc.stdout or "").lower()


def ldso_supported_levels(help_text: str) -> set[str]:
    """glibc hwcaps levels whose ld.so --help line says supported."""
    found: set[str] = set()
    for line in help_text.splitlines():
        if "supported" not in line.lower():
            continue
        for level in ("x86-64-v2", "x86-64-v3", "x86-64-v4"):
            if level in line:
                found.add(level)
    return found


def cpu_flags() -> set[str]:
    """Flags from the first processor block in /proc/cpuinfo."""
    cpu = Path("/proc/cpuinfo")
    if not cpu.exists():
        return set()
    for line in cpu.read_text(errors="ignore").splitlines():
        if line.lower().startswith("flags"):
            _name, _, values = line.partition(":")
            return set(values.split())
    return set()


def flags_support_x86_64_v3(flags: set[str]) -> bool:
    return _X86_64_V3_FLAGS <= flags


def _glibc_linker() -> str | None:
    """Ubuntu keeps the loader under /lib/x86_64-linux-gnu; Gentoo uses /lib64."""
    names = (
        "/lib64/ld-linux-x86-64.so.2",
        "/usr/lib64/ld-linux-x86-64.so.2",
        "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
    )
    for name in names:
        if Path(name).is_file():
            return name
    found = shutil.which("ld.so")
    return found or None


def supports_x86_64_v3() -> bool:
    """True when this CPU can run Gentoo's x86-64-v3 binhost.

    ld.so --help is the check Gentoo documents. /proc/cpuinfo is the fallback
    when that loader is not on the live image.
    """
    linker = _glibc_linker()
    if linker:
        proc = subprocess.run([linker, "--help"], capture_output=True, text=True, check=False)
        levels = ldso_supported_levels((proc.stdout or "") + "\n" + (proc.stderr or ""))
        if levels:
            return "x86-64-v3" in levels
    return flags_support_x86_64_v3(cpu_flags())


def has_nvidia() -> bool:
    return "nvidia" in _lspci_text()


def has_wacom() -> bool:
    blob = _lspci_text() + "\n" + _usb_text()
    return "wacom" in blob


def has_bluetooth() -> bool:
    bt = Path("/sys/class/bluetooth")
    if not bt.is_dir():
        return False
    try:
        return any(bt.iterdir())
    except OSError:
        return False


def has_wifi() -> bool:
    return any(nic["wireless"] for nic in list_nics())


def has_sound() -> bool:
    return bool(sound_cards())


def sound_cards() -> list[str]:
    """Names from /proc/asound/cards, such as 'HDA Intel PCH'."""
    cards = Path("/proc/asound/cards")
    if not cards.is_file():
        return []
    found: list[str] = []
    for line in cards.read_text(errors="ignore").splitlines():
        head = line.split("[", 1)[0].strip()
        if not head.isdigit():
            continue
        name = line.split(" - ", 1)[1].strip() if " - " in line else line.strip()
        if name and name not in found:
            found.append(name)
    return found


def bluetooth_adapters() -> list[str]:
    """Bluetooth host adapters currently visible to the live system."""
    root = Path("/sys/class/bluetooth")
    if not root.is_dir():
        return []
    found: list[str] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        pretty = ""
        for candidate in (entry / "name", entry / "device" / "name"):
            try:
                if candidate.is_file():
                    pretty = candidate.read_text(errors="ignore").strip()
                    break
            except OSError:
                continue
        label = pretty or entry.name
        if label and label not in found:
            found.append(label)
    return found


def wifi_interfaces() -> list[str]:
    """Wireless NIC names from sysfs."""
    return [nic["name"] for nic in list_nics() if nic["wireless"]]


def gpu_devices() -> list[str]:
    """lspci display-controller names, without the bus id."""
    if not shutil.which("lspci"):
        return []
    proc = subprocess.run(["lspci"], capture_output=True, text=True, check=False)
    found: list[str] = []
    for line in (proc.stdout or "").splitlines():
        low = line.lower()
        if "vga" not in low and "3d" not in low and "display" not in low:
            continue
        name = line
        if ": " in name:
            name = name.split(": ", 1)[1]
        if ": " in name:
            name = name.split(": ", 1)[1]
        name = name.strip()
        if name and name not in found:
            found.append(name)
    return found


def hardware_note(flag: str) -> str:
    """Short device text for a hardware USE flag. Never empty."""
    if flag == "bluetooth":
        names = bluetooth_adapters()
        return "detected: " + ", ".join(names) if names else "no adapter found on this machine"
    if flag == "wifi":
        names = wifi_interfaces()
        return "detected: " + ", ".join(names) if names else "no wireless NIC found on this machine"
    if flag == "alsa":
        names = sound_cards()
        return "detected: " + ", ".join(names) if names else "no sound card found on this machine"
    return ""


def suggest_input_devices() -> str:
    """libinput always. wacom only when a tablet is on the bus."""
    devices = ["libinput"]
    if has_wacom():
        devices.append("wacom")
    return " ".join(devices)


def detect_gpu(nvidia: str = "nouveau") -> str:
    """VIDEO_CARDS tokens from lspci.

    NVIDIA defaults to nouveau. Pass nvidia='nvidia' only after the user asks
    for the proprietary driver. A guest with no physical GPU gets virtio,
    vmware, and qxl instead of fbdev vesa.
    """
    text = _lspci_text()
    token = nvidia if nvidia in {"nouveau", "nvidia"} else "nouveau"
    cards: list[str] = []
    if "nvidia" in text:
        cards.append(token)
    if "amd" in text or "ati" in text or "radeon" in text or "advanced micro devices" in text:
        if "navi" in text or "rdna" in text or "vega" in text or "amdgpu" in text:
            cards.append("amdgpu")
        else:
            cards.append("amdgpu radeon")
    if "intel" in text and ("vga" in text or "display" in text or "graphics" in text):
        cards.append("intel")
    if cards:
        return " ".join(cards)
    guest = is_virtual_machine() or any(
        name in text for name in ("virtio", "qxl", "vmware", "virtualbox", "qemu")
    )
    if guest:
        return "virtio vmware qxl"
    return "fbdev vesa"


def detect_intel_cpu() -> bool:
    cpu = Path("/proc/cpuinfo")
    if not cpu.exists():
        return False
    return "GenuineIntel" in cpu.read_text(errors="ignore")


def has_tpm2() -> bool:
    return Path("/dev/tpmrm0").exists() or Path("/dev/tpm0").exists()


def mem_gib() -> int:
    """Physical RAM in GiB; used to suggest swap size (handbook: often = RAM)."""
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 8
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemTotal:"):
            kb = int(line.split()[1])
            return max(1, kb // 1024 // 1024)
    return 8
