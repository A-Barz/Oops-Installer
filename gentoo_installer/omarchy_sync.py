"""Live Omarchy GitHub cues for optional Oops UX (fail soft).

At install time we prefer the official GitHub API / raw.githubusercontent.com
files from omacom/omarchy (default branch, else `quattro` / `main`). A short
timeout and TLS via the system trust store are required. Network failure falls
back to a on-disk cache, then the bundled snapshot under data/ — never block a
handbook install solely because Omarchy is unreachable.

Arch pacman names are mapped to Gentoo atoms when a safe mapping exists;
everything else is skipped with a clear note for the wizard UI.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OMARCHY_OWNER = "omacom"
OMARCHY_REPO = "omarchy"
# Prefer the product install tree on quattro when present; else repo default / main.
PREFERRED_REFS = ("quattro", "main", "master")

DEFAULT_TIMEOUT_SEC = 8.0
USER_AGENT = "Oops-Installer-omarchy-sync/1.0 (+https://github.com/A-Barz/Oops-Installer)"

BUNDLE_PATH = Path(__file__).resolve().parent / "data" / "omarchy_snapshot.json"
DEFAULT_CACHE_PATH = Path("/var/tmp/oops-omarchy-snapshot.json")

# Small agent set we are willing to invite (lazy mise tools). Live sync may
# reorder / filter this list; we never ship Quickshell panels or passwordless sudo.
SAFE_AGENT_TOOLS = ("claude", "codex", "opencode", "cursor-agent")

# Pacman-ish names → Gentoo atoms (or empty to skip with UI). Keep suckless-safe.
ARCH_TO_GENTOO: dict[str, str] = {
    "hyprland": "gui-wm/hyprland",
    "git": "dev-vcs/git",
    "foot": "gui-apps/foot",
    "imv": "media-gfx/imv",
    "fzf": "app-shells/fzf",
    "bat": "sys-apps/bat",
    "btop": "sys-process/btop",
    "jq": "app-misc/jq",
    "rsync": "net-misc/rsync",
    "curl": "net-misc/curl",
    "wget": "net-misc/wget",
    "openssh": "net-misc/openssh",
    "networkmanager": "net-misc/networkmanager",
    "ufw": "net-firewall/ufw",
    "wireguard-tools": "net-vpn/wireguard-tools",
    "docker": "app-containers/docker",
    "pipewire": "media-video/pipewire",
    "wireplumber": "media-video/wireplumber",
    "grim": "gui-apps/grim",
    "slurp": "gui-apps/slurp",
    "wl-clipboard": "gui-apps/wl-clipboard",
    "swaybg": "gui-apps/swaybg",
    "swaylock": "gui-apps/swaylock",
    "swayidle": "gui-apps/swayidle",
    "waybar": "gui-apps/waybar",
    "mako": "gui-apps/mako",
    "brightnessctl": "app-misc/brightnessctl",
    "playerctl": "media-sound/playerctl",
    "imagemagick": "media-gfx/imagemagick",
    "ffmpeg": "media-video/ffmpeg",
    "gnome-keyring": "gnome-base/gnome-keyring",
    "cups": "net-print/cups",
    "avahi": "net-dns/avahi",
    "bluez": "net-wireless/bluez",
    "lazygit": "dev-vcs/lazygit",
    "fd": "sys-apps/fd",
    "ripgrep": "sys-apps/ripgrep",
    "neovim": "app-editors/neovim",
    "vim": "app-editors/vim",
    "tmux": "app-misc/tmux",
    "unzip": "app-arch/unzip",
    "zip": "app-arch/zip",
    "less": "sys-apps/less",
    "man-db": "sys-apps/man-db",
    "plocate": "sys-apps/mlocate",
    "inetutils": "net-misc/netkit-telnetd",
    "dosfstools": "sys-fs/dosfstools",
    "exfatprogs": "sys-fs/exfatprogs",
    "libsecret": "app-crypt/libsecret",
    "polkit": "sys-auth/polkit",
    "xdg-desktop-portal-hyprland": "gui-apps/xdg-desktop-portal-hyprland",
    "hyprpaper": "gui-apps/hyprpaper",
    "hypridle": "gui-apps/hypridle",
    "hyprlock": "gui-apps/hyprlock",
    "hyprpicker": "gui-apps/hyprpicker",
    # Explicit skips (no safe Portage atom, or product-only):
    "mise": "",
    "mise-bin": "",
    "quickshell": "",
    "chromium": "",  # Chrome remains optional catalogue pick, not forced
    "yay": "",
    "localsend": "",  # LocalSend hole is opt-in UFW ports, not a forced package
    "linux-omarchy": "",
    "omarchy-chromium": "",
}


@dataclass
class OmarchySnapshot:
    """Cues used to adjust optional Omarchy-inspired wizard defaults for this run."""

    source: str  # live | cache | bundled
    repo: str = f"{OMARCHY_OWNER}/{OMARCHY_REPO}"
    ref: str = "quattro"
    default_branch: str = "quattro"
    fetched_at: str = ""
    encrypt_default_on: bool = True
    ufw_deny_incoming: bool = True
    ufw_allow_outgoing: bool = True
    localsend_ports: list[dict[str, Any]] = field(default_factory=list)
    mise_tools: list[str] = field(default_factory=list)
    preferred_agent_tools: list[str] = field(default_factory=list)
    base_packages: list[str] = field(default_factory=list)
    mapped_gentoo_atoms: list[str] = field(default_factory=list)
    skipped_packages: list[str] = field(default_factory=list)
    agent_invite_pattern: bool = True
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def agent_tools_for_invite(self, limit: int = 4) -> list[str]:
        """Small agent set for the first-login invite (live order when present)."""
        preferred = [t for t in self.preferred_agent_tools if t in SAFE_AGENT_TOOLS]
        if not preferred:
            preferred = [t for t in SAFE_AGENT_TOOLS if t in self.mise_tools]
        if not preferred:
            preferred = list(SAFE_AGENT_TOOLS)
        return preferred[:limit]


def cache_path() -> Path:
    override = os.environ.get("OOPS_OMARCHY_CACHE", "").strip()
    return Path(override) if override else DEFAULT_CACHE_PATH


def bundle_path() -> Path:
    override = os.environ.get("OOPS_OMARCHY_BUNDLE", "").strip()
    return Path(override) if override else BUNDLE_PATH


def _ssl_context() -> ssl.SSLContext:
    # Pin to system CA store (no custom insecure context).
    return ssl.create_default_context()


def _http_get(url: str, timeout: float, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read()


def _api_json(path: str, timeout: float) -> Any:
    url = f"https://api.github.com{path}"
    raw = _http_get(url, timeout, accept="application/vnd.github+json")
    return json.loads(raw.decode("utf-8"))


def _raw_text(ref: str, path: str, timeout: float) -> str:
    url = f"https://raw.githubusercontent.com/{OMARCHY_OWNER}/{OMARCHY_REPO}/{ref}/{path}"
    return _http_get(url, timeout).decode("utf-8")


def _detect_ref(timeout: float) -> tuple[str, str]:
    """Return (ref, default_branch). Prefer quattro when that install tree exists."""
    repo = _api_json(f"/repos/{OMARCHY_OWNER}/{OMARCHY_REPO}", timeout)
    default_branch = str(repo.get("default_branch") or "main")
    # Detect install/ presence on preferred refs without scraping HTML.
    for candidate in PREFERRED_REFS + (default_branch,):
        try:
            _api_json(
                f"/repos/{OMARCHY_OWNER}/{OMARCHY_REPO}/contents/install?ref={candidate}",
                timeout,
            )
            return candidate, default_branch
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
    return default_branch, default_branch


def _parse_mise_tools(toml_text: str) -> list[str]:
    tools: list[str] = []
    in_tools = False
    for line in toml_text.splitlines():
        stripped = line.strip()
        if stripped == "[tools]":
            in_tools = True
            continue
        if in_tools and stripped.startswith("["):
            break
        if not in_tools or not stripped or stripped.startswith("#"):
            continue
        match = re.match(r'^"?([^"=]+)"?\s*=', stripped)
        if match:
            tools.append(match.group(1).strip().strip('"'))
    return tools


def _parse_localsend_ports(firewall_sh: str) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for match in re.finditer(r"ufw\s+allow\s+(\d+)/(udp|tcp)", firewall_sh):
        key = (int(match.group(1)), match.group(2))
        if key in seen:
            continue
        seen.add(key)
        ports.append({"port": key[0], "proto": key[1]})
    if not ports:
        ports = [{"port": 53317, "proto": "udp"}, {"port": 53317, "proto": "tcp"}]
    return ports


def _parse_package_list(text: str) -> list[str]:
    pkgs: list[str] = []
    for line in text.splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        # Ignore stray non-package lines if a directory listing leaked.
        if "/" in name or " " in name:
            continue
        pkgs.append(name)
    return pkgs


def map_arch_packages(names: list[str]) -> tuple[list[str], list[str]]:
    """Map pacman names → Gentoo atoms; return (mapped, skipped_with_reason_prefix)."""
    mapped: list[str] = []
    skipped: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key in ARCH_TO_GENTOO:
            atom = ARCH_TO_GENTOO[key]
            if atom:
                if atom not in mapped:
                    mapped.append(atom)
            else:
                skipped.append(f"{name} (no Gentoo atom / intentional skip)")
        else:
            # Unknown Arch-only or Omarchy private package — skip quietly unless common.
            skipped.append(f"{name} (unmapped Arch/Omarchy package)")
    return mapped, skipped


def _from_dict(data: dict[str, Any], source: str, error: str = "") -> OmarchySnapshot:
    base_packages = list(data.get("base_packages") or [])
    mapped, skipped = map_arch_packages(base_packages)
    preferred = list(data.get("preferred_agent_tools") or [])
    if not preferred:
        mise = list(data.get("mise_tools") or [])
        preferred = [t for t in SAFE_AGENT_TOOLS if t in mise] or list(SAFE_AGENT_TOOLS)
    return OmarchySnapshot(
        source=source,
        repo=str(data.get("repo") or f"{OMARCHY_OWNER}/{OMARCHY_REPO}"),
        ref=str(data.get("ref") or "quattro"),
        default_branch=str(data.get("default_branch") or data.get("ref") or "quattro"),
        fetched_at=str(data.get("fetched_at") or ""),
        encrypt_default_on=bool(data.get("encrypt_default_on", True)),
        ufw_deny_incoming=bool(data.get("ufw_deny_incoming", True)),
        ufw_allow_outgoing=bool(data.get("ufw_allow_outgoing", True)),
        localsend_ports=list(data.get("localsend_ports") or []),
        mise_tools=list(data.get("mise_tools") or []),
        preferred_agent_tools=preferred,
        base_packages=base_packages,
        mapped_gentoo_atoms=mapped,
        skipped_packages=skipped,
        agent_invite_pattern=bool(data.get("agent_invite_pattern", True)),
        notes=list(data.get("notes") or []),
        error=error,
    )


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _write_cache(data: dict[str, Any]) -> None:
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # Cache is best-effort; bundled snapshot remains the offline floor.
        pass


def _fetch_live(timeout: float) -> dict[str, Any]:
    ref, default_branch = _detect_ref(timeout)
    firewall = _raw_text(ref, "install/config/firewall.sh", timeout)
    tools_toml = _raw_text(ref, "etc/mise/conf.d/omarchy-tools.toml", timeout)
    try:
        base = _raw_text(ref, "install/omarchy-base.packages", timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        base = ""
    try:
        agent_hook = _raw_text(ref, "install/user/first-run/setup-agent.hook", timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        agent_hook = ""

    mise_tools = _parse_mise_tools(tools_toml)
    preferred = [t for t in SAFE_AGENT_TOOLS if t in mise_tools]
    # Keep grok only as an extra when present — invite still caps via agent_tools_for_invite.
    if "grok" in mise_tools and "grok" not in preferred:
        preferred = preferred  # intentional: SAFE_AGENT_TOOLS stays the invite set
    base_packages = _parse_package_list(base)
    return {
        "repo": f"{OMARCHY_OWNER}/{OMARCHY_REPO}",
        "ref": ref,
        "default_branch": default_branch,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "encrypt_default_on": True,
        "ufw_deny_incoming": "default deny incoming" in firewall,
        "ufw_allow_outgoing": "default allow outgoing" in firewall,
        "localsend_ports": _parse_localsend_ports(firewall),
        "mise_tools": mise_tools,
        "preferred_agent_tools": preferred or list(SAFE_AGENT_TOOLS),
        "base_packages": base_packages,
        "agent_invite_pattern": (
            "agent-setup-invitation" in agent_hook
            or "invite" in agent_hook.lower()
            or True
        ),
        "notes": [
            "Live fetch from GitHub API + raw files (install/ + etc/mise).",
            "Encryption default is Omarchy product UX (ISO); Oops applies it to wipe paths only.",
            "UFW LocalSend ports are opt-in on Oops; docker DNS holes are not copied.",
        ],
    }


def load_omarchy_snapshot(
    *,
    timeout: float | None = None,
    offline: bool = False,
) -> OmarchySnapshot:
    """Fetch Omarchy cues; never raise for network — always return a usable snapshot."""
    timeout_sec = DEFAULT_TIMEOUT_SEC if timeout is None else float(timeout)
    env_offline = os.environ.get("OOPS_OMARCHY_OFFLINE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    offline = offline or env_offline
    errors: list[str] = []

    if not offline:
        try:
            data = _fetch_live(timeout_sec)
            _write_cache(data)
            return _from_dict(data, source="live")
        except Exception as exc:  # noqa: BLE001 — fail soft for any transport/parse issue
            errors.append(f"live: {exc}")

    cached = _load_json_file(cache_path())
    if cached:
        return _from_dict(cached, source="cache", error="; ".join(errors))

    bundled = _load_json_file(bundle_path())
    if bundled:
        return _from_dict(
            bundled,
            source="bundled",
            error="; ".join(errors) or ("offline" if offline else ""),
        )

    # Absolute last resort if the package tree lost its data file.
    return OmarchySnapshot(
        source="builtin",
        encrypt_default_on=True,
        ufw_deny_incoming=True,
        ufw_allow_outgoing=True,
        localsend_ports=[{"port": 53317, "proto": "udp"}, {"port": 53317, "proto": "tcp"}],
        preferred_agent_tools=list(SAFE_AGENT_TOOLS),
        mise_tools=list(SAFE_AGENT_TOOLS),
        agent_invite_pattern=True,
        notes=["Hard-coded fallback; bundle file missing."],
        error="; ".join(errors) or "no bundle",
    )


def summarize_for_ui(snap: OmarchySnapshot) -> str:
    """One-line status for the wizard."""
    base = f"Omarchy cues: {snap.source} @ {snap.repo}@{snap.ref}"
    if snap.fetched_at:
        base += f" ({snap.fetched_at})"
    if snap.error and snap.source != "live":
        base += " — network unavailable, using fallback"
    return base
