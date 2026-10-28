#!/usr/bin/env python3
"""Entry point for the Gentoo live installer.

Run this as root from an Ubuntu Boot Image live USB:

    sudo python3 install.py

Flow:
  1. Colourful wizard (gentoo_installer.wizard) collects every handbook choice.
  2. execute() (gentoo_installer.execute) partitions the disk, extracts stage3,
     then generates a bash script that runs *inside* the Gentoo chroot.

The question order matches the AMD64 Handbook:
https://wiki.gentoo.org/wiki/Handbook:AMD64
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gentoo_installer import ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import RestartFromBeginning, adopt_resume_profile, ask_resume_or_unmount, execute
from gentoo_installer.wizard import (
    fill_missing_secrets,
    profile_has_software,
    queue_saved_software,
    run_wizard,
    warn_if_ntfs_present,
)

_SOFTWARE_FIELDS = (
    "window_managers",
    "wm_dotfiles",
    "selected_packages",
    "extra_packages",
    "shell",
    "overlays",
    "pipewire",
    "nonfree_codecs",
    "accept_license",
    "display_manager",
    "browser",
    "flatpak",
    "extra_use",
)


def parse_args() -> argparse.Namespace:
    """CLI flags for dry-run, resume, and saving answers without installing."""
    p = argparse.ArgumentParser(
        description="Colourful Gentoo installer (Ubuntu Boot Image live → disk), handbook-ordered.",
    )
    p.add_argument(
        "--config",
        help="Load a saved YAML or JSON profile and install its software (passwords still prompted)",
    )
    p.add_argument(
        "--save-config",
        help="Write answers after the wizard (.yaml/.yml or .json). Default omits secrets",
    )
    p.add_argument(
        "--include-secrets",
        action="store_true",
        help="Include passwords/LUKS/Wi-Fi in --save-config (keep that file private)",
    )
    p.add_argument("--wizard-only", action="store_true", help="Collect answers, do not install")
    p.add_argument("--dry-run", action="store_true", help="Print commands without changing disks")
    p.add_argument("--resume", action="store_true", help="Skip phases recorded in the state file")
    return p.parse_args()


def _write_profile(path: str, cfg: InstallConfig, include_secrets: bool = False) -> None:
    """Create the YAML/JSON profile, including parent directories."""
    dest = Path(path)
    try:
        cfg.save(dest, include_secrets=include_secrets)
    except OSError as exc:
        ui.warn(f"Could not write {dest}: {exc}")
        return
    ui.ok(f"Wrote {dest.resolve()}")


def _read_profile(path: Path) -> InstallConfig | None:
    try:
        return InstallConfig.load(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        ui.warn(f"Could not read {path}")
        return None


def _attempt_paths(explicit: str | None) -> list[Path]:
    """YAML files that can still hold the first attempt, explicit path first."""
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    for candidate in (Path("gentoo-install.yaml"), Path("/var/tmp/gentoo-install.yaml")):
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _copy_software(dst: InstallConfig, src: InstallConfig) -> None:
    for key in _SOFTWARE_FIELDS:
        value = getattr(src, key)
        if isinstance(value, list):
            value = list(value)
        setattr(dst, key, value)


def _borrow_software(cfg: InstallConfig, skip: Path | None) -> None:
    """Fill an empty profile from the software saved on the first attempt."""
    if profile_has_software(cfg):
        return
    skip_resolved = None
    if skip is not None:
        try:
            skip_resolved = skip.resolve()
        except OSError:
            skip_resolved = skip
    for path in _attempt_paths(None):
        if not path.is_file():
            continue
        try:
            if skip_resolved is not None and path.resolve() == skip_resolved:
                continue
        except OSError:
            continue
        other = _read_profile(path)
        if other is None or not profile_has_software(other):
            continue
        _copy_software(cfg, other)
        ui.ok(f"Using the software chosen in {path}")
        return


def _load_attempt(explicit: str | None) -> tuple[InstallConfig, bool]:
    """Load a YAML profile. A blank stub is not treated as a finished attempt."""
    explicit_path = Path(explicit) if explicit else None
    if explicit_path is not None and not explicit_path.is_file():
        ui.warn(f"{explicit_path} is not created yet. Creating it, then starting the wizard.")
        cfg = InstallConfig()
        _write_profile(str(explicit_path), cfg)
        return cfg, False
    loaded_cfg: InstallConfig | None = None
    loaded_from: Path | None = None
    for path in _attempt_paths(explicit):
        if not path.is_file():
            continue
        cfg = _read_profile(path)
        if explicit_path is not None and path == explicit_path:
            if cfg is None:
                return InstallConfig(), False
            loaded_cfg = cfg
            loaded_from = path
            break
        if cfg is None:
            continue
        if profile_has_software(cfg) or cfg.target_disk:
            loaded_cfg = cfg
            loaded_from = path
            break
    if loaded_cfg is None or loaded_from is None:
        cfg = InstallConfig()
        starter = Path("gentoo-install.yaml")
        if not starter.exists():
            ui.info("Creating gentoo-install.yaml for this install.")
            _write_profile(str(starter), cfg)
        return cfg, False
    _borrow_software(loaded_cfg, loaded_from)
    ui.ok(f"Loaded config {loaded_from.resolve()}")
    if profile_has_software(loaded_cfg):
        setattr(loaded_cfg, "_software_loaded", True)
    return loaded_cfg, True


def _begin_again(args: argparse.Namespace) -> int:
    """Wizard from the first question after a failed mount was unmounted."""
    cfg, loaded = _load_attempt(args.config)
    cfg.resume = False
    setattr(cfg, "_resume_decided", True)
    if args.dry_run:
        cfg.dry_run = True
    if loaded and profile_has_software(cfg):
        queue_saved_software(cfg)
        ui.ok("The wizard is starting over. Saved software stays ticked.")
    cfg = run_wizard(cfg)
    starter = Path(args.config) if args.config else Path("gentoo-install.yaml")
    _write_profile(str(starter), cfg, include_secrets=bool(args.config and args.include_secrets))
    if args.save_config:
        _write_profile(args.save_config, cfg, include_secrets=args.include_secrets)
    if args.wizard_only:
        ui.info("Wizard-only mode; not executing.")
        return 0
    execute(cfg)
    return 0


def _run_session(args: argparse.Namespace) -> int:
    ran_wizard = False
    # A previous attempt's YAML is the software list. A blank stub still
    # starts the wizard, and a missing --config file is created first.
    cfg, loaded_profile = _load_attempt(args.config)
    if args.dry_run:
        cfg.dry_run = True
    if args.resume:
        adopt_resume_profile(cfg)
        cfg.resume = True
        cfg.wipe_disk = False
        setattr(cfg, "_resume_decided", True)
    elif not args.wizard_only and ask_resume_or_unmount(cfg) == "fresh":
        raise RestartFromBeginning()
    # Resume keeps the saved disk, packages, and keymap. A blank config used
    # to walk the wizard and then emerge that blank selection onto the mount.
    saved_resume = bool(getattr(cfg, "_resume_decided", False) and cfg.resume and cfg.target_disk)
    accepted = False
    if saved_resume or loaded_profile:
        accepted = fill_missing_secrets(cfg)
    if not accepted:
        if args.resume:
            cfg.resume = True
        cfg = run_wizard(cfg)
        ran_wizard = True
        dest = args.config if args.config else "gentoo-install.yaml"
        _write_profile(dest, cfg, include_secrets=bool(args.config and args.include_secrets))
    if args.dry_run:
        cfg.dry_run = True
    if args.resume:
        cfg.resume = True
    save_path = args.save_config
    if not save_path and ran_wizard and ui.ask_yes_no("Also copy these choices to another YAML path?", False):
        save_path = ui.prompt("Config path", "./gentoo-install.yaml")
    if save_path:
        if args.include_secrets:
            ui.warn("Writing passwords into the config file. Restrict its permissions.")
        _write_profile(save_path, cfg, include_secrets=args.include_secrets)
    if args.wizard_only:
        ui.info("Wizard-only mode; not executing.")
        return 0
    execute(cfg)
    return 0


def main() -> int:
    ui.banner()
    warn_if_ntfs_present()
    args = parse_args()
    # A declined resume can happen at startup or after a later failure.
    # Each decline unmounts the target and comes back through the wizard.
    begin = False
    while True:
        try:
            if begin:
                return _begin_again(args)
            return _run_session(args)
        except RestartFromBeginning:
            begin = True


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        ui.warn("Interrupted.")
        sys.exit(130)
