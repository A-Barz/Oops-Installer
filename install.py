#!/usr/bin/env python3
"""Entry point for the Gentoo live installer.

Run this as root from an Ubuntu XFCE live USB:

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
import sys
from pathlib import Path

from gentoo_installer import ui
from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import execute
from gentoo_installer.wizard import fill_missing_secrets, run_wizard


def parse_args() -> argparse.Namespace:
    """CLI flags for dry-run, resume, and saving answers without installing."""
    p = argparse.ArgumentParser(
        description="Colourful Gentoo installer (Ubuntu XFCE live → disk), handbook-ordered.",
    )
    p.add_argument(
        "--config",
        help="Load a saved YAML or JSON profile and skip the wizard (passwords still prompted)",
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


def main() -> int:
    ui.banner()
    args = parse_args()
    ran_wizard = False
    # The profile has to exist before the wizard finishes. A missing
    # --config used to raise "No such file or directory" ("not created yet").
    if args.config:
        config_path = Path(args.config)
        if config_path.is_file():
            cfg = InstallConfig.load(config_path)
            ui.ok(f"Loaded config {config_path.resolve()}")
            if args.dry_run:
                cfg.dry_run = True
            if args.resume:
                cfg.resume = True
            fill_missing_secrets(cfg)
        else:
            ui.warn(f"{config_path} is not created yet. Creating it, then starting the wizard.")
            cfg = InstallConfig()
            _write_profile(str(config_path), cfg)
            if args.dry_run:
                cfg.dry_run = True
            if args.resume:
                cfg.resume = True
            cfg = run_wizard(cfg)
            ran_wizard = True
            _write_profile(str(config_path), cfg, include_secrets=args.include_secrets)
    else:
        cfg = InstallConfig()
        starter = Path("gentoo-install.yaml")
        if not starter.exists():
            ui.info("Creating gentoo-install.yaml for this install.")
            _write_profile(str(starter), cfg)
        if args.dry_run:
            cfg.dry_run = True
        if args.resume:
            cfg.resume = True
        cfg = run_wizard(cfg)
        ran_wizard = True
        _write_profile(str(starter), cfg)
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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        ui.warn("Interrupted.")
        sys.exit(130)
