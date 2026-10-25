# gentoo-live-installer

Python installer that runs **in a terminal on Ubuntu Boot Image live media** and installs **Gentoo Linux onto a hard disk**.

## Notice

This is a personal project. The name **Oops-Installer** is a poke at Gentoo: an installer misses the value of installing Gentoo by hand. That is a statement about open source. You can do whatever you want with it. Fork a project, contribute, or make your own. Code for the sake of it.

The wizard is ordered like the [Gentoo AMD64 Handbook](https://wiki.gentoo.org/wiki/Handbook:AMD64) (linked from the [Gentoo wiki main page](https://wiki.gentoo.org/wiki/Main_Page)):

1. About the installation  
2. Installation medium (this Ubuntu live host)  
3. Network  
4. Disks  
5. Stage file  
6. Base system  
7. Kernel  
8. System configuration  
9. System tools  
10. Bootloader (multiple loaders + Plymouth themes)  
11. Wayland compositors, Noctalia or DMS, and greetd
12. Shared Wayland utilities, suckless tools, and popular apps  

GitHub installer research and the merged feature set: [RESEARCH.md](RESEARCH.md).

In chapter 12 you can search (`/text`), page (`n`/`p`), and toggle packages. Overlays such as `hyproverlay`, `wayland-desktop`, and `steam-overlay` are enabled with `eselect repository` when a chosen package needs them. The GURU overlay is enabled only when you choose Noctalia or DMS. You can also type any extra `category/package` atom for emerge.

## Requirements

- Ubuntu Boot Image live USB/ISO (or another Debian-like live system with `apt` and Python 3.10+)
- Wired or wireless internet
- Root privileges
- A target disk you can **wipe** (unless you choose existing partitions)

Read **[HOWTO.txt](HOWTO.txt)** before a real install (plain-text walkthrough, safety, resume, and limits).

## Usage

Boot Ubuntu live, open a terminal:

```bash
sudo apt-get update
sudo apt-get install -y python3 git
git clone <this-repo> 
cd gentoo-live-installer
sudo python3 install.py
```

Useful flags:

```bash
sudo python3 install.py --dry-run          # print commands, no disk changes
sudo python3 install.py --wizard-only --save-config ./gentoo-install.yaml
sudo python3 install.py --config ./gentoo-install.yaml   # reuse answers; passwords are prompted
sudo python3 install.py --resume
```

## Reusing choices (YAML or JSON)

The wizard can save every non-secret answer so the next live-USB run skips the Q&A:

```bash
# After answering the wizard, say yes to “Save these choices as YAML…”
# or pass a path:
sudo python3 install.py --save-config ./gentoo-install.yaml

# Later, on the same or another machine:
sudo python3 install.py --config ./gentoo-install.yaml
```

`.yaml` / `.yml` is YAML; `.json` is JSON. Passwords, LUKS, and Wi-Fi keys are **not** written unless you add `--include-secrets` (keep that file private). On import, the installer asks for those secrets and you still confirm the disk path.

See `example-config.yaml` for the file shape.

Answer every colourful prompt. A summary is shown; you must confirm the disk path before partitioning.

A full desktop emerge can take **many hours**. Prefer **binary packages** and **gentoo-kernel-bin** unless you want to compile everything.

## Safety

The default path **destroys all data on the selected disk**. Test in a VM first.

## Limits (also shown in the wizard)

- The package catalogue is a handbook-sized set plus overlays, not the entire Portage tree. You can still type any atom.
- **Encrypted swap** is a separate LUKS2 volume (`/dev/mapper/swap`) when you use a swap partition or LV. **LVM-on-LUKS** already encrypts the VG, so the swap LV is not wrapped twice. A **swap file** is not LUKS-wrapped.
- **TPM2** only writes a helper script; enroll keys after the first real boot.
- **Limine** copies `BOOTX64.EFI` / `BOOTAA64.EFI` to the ESP (or runs `limine bios-install` on BIOS) and writes `limine.conf` from the kernel currently in `/boot`. Kernel upgrades need that file or the `vmlinuz` symlink refreshed.
- **netifrc** uses the NIC you pick in the wizard (live `ip`/`sysfs` list), not a hardcoded `eth0`.

## Layout

Python modules have comments that map each step to the Gentoo AMD64 Handbook.

| Path | Role |
| --- | --- |
| `install.py` | Entry point |
| `gentoo_installer/wizard.py` | Handbook-ordered colourful Q&A |
| `gentoo_installer/execute.py` | Host, disk, stage3, chroot phases |
| `gentoo_installer/ui.py` | Colours, menus, passwords |
| `gentoo_installer/catalog.py` | Package categories, WMs, overlays, Plymouth themes |
