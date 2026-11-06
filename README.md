# Oops-Installer

**Oops-Installer — A Gentoo Wayland First and Suckless Setup Tool**

A terminal installer that runs on **Ubuntu Boot Image** live media and installs **Gentoo Linux** onto a disk. Questions follow the [Gentoo AMD64 Handbook](https://wiki.gentoo.org/wiki/Handbook:AMD64). Nothing is written to the target until you re-type the disk path at the end.

## Notice

This is a personal project. The name **Oops-Installer** is a poke at Gentoo: an installer misses the value of installing Gentoo by hand. That is a statement about open source. Any likeness to an original Oops-Installer for Gentoo is purely coincidental, a Freudian slip at most, and was not intended. You can do whatever you want with it. Fork a project, contribute, or make your own. Code for the sake of it.

The project is open for testing and bug reports. Try it in a virtual machine, or on a disk you can wipe, and file what you find at [GitHub Issues](https://github.com/A-Barz/Oops-Installer/issues). Include the installer revision from the banner, the choices you made, and the command that failed.

## Why

Oops-Installer aims at a **Wayland-first, compositor-centric** Gentoo system — not a GNOME or Plasma meta. There is **no desktop-environment picker** and **no games category**.

What you get:

- **Wayland compositors** — Hyprland, Sway, Niri, dwl, and similar. Login is greetd (gtkgreet + labwc) when you pick a Wayland session.
- **Quickshell on Hyprland** — Hyprland always emerges Quickshell (`qs` required). Dot choices: installer Lua rice, your own GitHub trees, or end-4 configs (hypr + quickshell copied; Arch install scripts never run).
- **OpenRC or systemd** — OpenRC is the default; systemd is available. Dual boot is not set up; the default path wipes the disk you confirm.
- **LUKS-leaning disks** — Encryption defaults on for wipe / full-disk paths (clear opt-out), with handbook-style layouts, kernels, and bootloaders.
- **Suckless tools and a handbook-sized catalogue** — Optional browsers (Firefox ESR soft-default; Chrome opt-in), overlays when you need them, configs and emerges on *your* disk.

Binary packages are optional so a desktop install need not compile everything. Hardware on the live USB seeds sensible defaults you can change.

Read **[HOWTO.txt](HOWTO.txt)** before a real install — chapter detail, package lists, mirrors/binhost notes, and post-install greeter checks live there.

## Usage

Boot an Ubuntu Boot Image (or similar Debian-like live system with `apt` and Python 3.10+). You need internet, root, and a target disk you can wipe (unless you choose existing partitions).

```bash
sudo apt-get update
sudo apt-get install -y python3 git ntpsec-ntpdate
git clone <this-repo>
cd Oops-Installer   # or gentoo-live-installer
sudo python3 install.py
```

`ntpsec-ntpdate` is for **host** clock sync before stage3 GPG. Gentoo still uses optional `net-misc/chrony` on the installed system — do not unmerge it.

Useful flags:

```bash
sudo python3 install.py --dry-run
sudo python3 install.py --wizard-only --save-config ./gentoo-install.yaml
sudo python3 install.py --config ./gentoo-install.yaml
sudo python3 install.py --resume
```

Passwords, LUKS, and Wi-Fi keys are omitted from saved YAML/JSON unless you pass `--include-secrets`. See `example-config.yaml`. After disk confirmation the installer shows a full-width nap banner (no progress bar); emerge output streams until finish, then you are asked to power down and remove the USB only after the machine is off.

## Wizard (short)

Handbook order, roughly:

- Banner, NTFS / resume / saved-profile gates, then preamble warnings
- About the install (dry-run, resume, mountpoint)
- Live host (arch, firmware, host tools)
- Network (DHCP / Wi-Fi / static; optional `ntpsec-ntpdate` sync)
- Disks (wipe mode, layout, filesystem, swap, LUKS lean-yes on wipe paths)
- Stage3 only (OpenRC or systemd; desktop/server; variant)
- Base system, kernel, hostname/users, system tools, bootloader
- Wayland session + suckless / apps catalogue
- Summary → estimate → wipe warning → **re-type the disk path** → nap → install

Defaults and every prompt: **[HOWTO.txt](HOWTO.txt)**.

## Honesty

Public autobuilds are **stage3-only** (stage1/2 refused). Hyprland **requires** `qs`. end-4 is configs + helpers, not a complete one-shot rice — expect more emerges and heavy Qt/Quickshell builds. If the greeter comes back after Hyprland, drop to a TTY and check `Hyprland` / `qs` / your hypr + quickshell trees before re-running the installer (checklist in HOWTO).

## Safety

This installer has been actively tested on bare metal. The default path **destroys all data on the selected disk**.

## Possible next changes

Useful ideas only (not a roadmap): harder greeter → Hyprland reliability; optional curated Quickshell rices if packaging stays Wayland-first; keep catalogue atoms honest against GURU / hyproverlay drift.

Not planned: Omarchy-style live UX sync, without-systemd / eudev migration, DE pickers, or games.

## Inspiration

Thanks to the projects that keep source, choice, and compositors interesting — **Gentoo**, **Funtoo**, **Omarchy**, **Garuda**, **Void**, and **Aeryn OS** — and to people whose work or stubbornness still matters here: **Richard Stallman**, **Jono Bacon**, **Bryan Lunduke**, **Terry Fox**, and **Noah Chelliah**. Inspiration is not affiliation; this installer is its own small thing.

$\scriptsize\textcolor{purple}{\textsf{Y}}\textcolor{green}{\textsf{o}}\textcolor{purple}{\textsf{u}}\textcolor{green}{\textsf{'}}\textcolor{purple}{\textsf{r}}\textcolor{green}{\textsf{e}}\ \textcolor{purple}{\textsf{W}}\textcolor{green}{\textsf{e}}\textcolor{purple}{\textsf{l}}\textcolor{green}{\textsf{c}}\textcolor{purple}{\textsf{o}}\textcolor{green}{\textsf{m}}\textcolor{purple}{\textsf{e}}$ 😈
