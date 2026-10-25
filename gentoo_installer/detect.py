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
    """Best-effort parent disk of the live USB so we can warn 'do not wipe this'."""
    findmnt = shutil.which("findmnt")
    if not findmnt:
        return None
    proc = subprocess.run(
        [findmnt, "-n", "-o", "SOURCE", "/cdrom"],
        capture_output=True,
        text=True,
        check=False,
    )
    src = proc.stdout.strip()
    if src.startswith("/dev/"):
        return parent_block_device(src)
    return None


def detect_gpu() -> str:
    """Guess VIDEO_CARDS tokens for make.conf from lspci (Intel / amdgpu / nvidia)."""
    if not shutil.which("lspci"):
        return "fbdev vesa"
    proc = subprocess.run(["lspci"], capture_output=True, text=True, check=False)
    text = (proc.stdout or "").lower()
    cards = []
    if "nvidia" in text:
        cards.append("nvidia")
    if "amd" in text or "ati" in text or "radeon" in text or "advanced micro devices" in text:
        if "navi" in text or "rdna" in text or "vega" in text or "amdgpu" in text:
            cards.append("amdgpu")
        else:
            cards.append("amdgpu radeon")
    if "intel" in text and ("vga" in text or "display" in text or "graphics" in text):
        cards.append("intel")
    return " ".join(cards) if cards else "fbdev vesa"


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
