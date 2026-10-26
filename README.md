# gentoo-live-installer

Python installer that runs **in a terminal on Ubuntu Boot Image live media** and installs **Gentoo Linux onto a hard disk**.

## Notice

This is a personal project. The name **Oops-Installer** is a poke at Gentoo: an installer misses the value of installing Gentoo by hand. That is a statement about open source. Any likeness to an original Oops-Installer for Gentoo is purely coincidental, a Freudian slip at most, and was not intended. You can do whatever you want with it. Fork a project, contribute, or make your own. Code for the sake of it.

The project is open for testing and bug reports. Try it in a virtual machine, or on a disk you can wipe, and file what you find at [GitHub Issues](https://github.com/A-Barz/Oops-Installer/issues). Include the installer revision from the banner, the choices you made, and the command that failed.

## What the installer sets up

Oops-Installer runs in a terminal on an Ubuntu live USB and installs Gentoo onto a disk. The questions follow the AMD64 Handbook. A summary, including an estimated time, is shown before the disk is touched, and you re-type the disk path before partitioning. After that confirmation the installer tells you to step away, and a percent tracks emerge for the rest of the install.

- **Disks.** GPT or BIOS, a full wipe or existing partitions, LUKS, LVM, RAID1, Btrfs, ZFS, and swap. A weekly `fstrim` is offered for an SSD. Dual boot is not set up.
- **Base system.** OpenRC or systemd, a stage3, Portage profile and USE flags, and a kernel (including `gentoo-kernel-bin`). Binary packages are offered so a desktop install does not have to compile everything.
- **Hardware.** The live system is probed for CPU level, input devices, Wi-Fi, Bluetooth, and the GPU. NVIDIA can use nouveau or the proprietary driver. `CPU_FLAGS_X86` is written in the chroot with `cpuid2cpuflags`.
- **Boot.** GRUB, systemd-boot, EFI stub, and Limine, plus Plymouth themes. The EFI System Partition stays at `/efi`.
- **Session.** Wayland compositors (Hyprland, Niri, Sway, Wayfire, labwc, and others), plus dwm and i3. Noctalia Shell and DMS are optional. With Noctalia and a login manager, the greeter is noctalia-greeter. The installed system boots to that login screen, with the username ready and the chosen window managers as sessions. A text-console choice stays a shell.
- **Dotfiles.** Hyprland and Niri each offer the installer config or one of four GitHub configs. The chosen config is downloaded from the latest commit on its branch during the install, and the Gentoo packages that config starts are emerged. Noctalia and DMS skip this, because those shells bring their own session. Upstream install scripts are not run.
- **Apps.** PipeWire, suckless tools, and a handbook-sized list of common programs. `nano` is always installed. You can type any extra Portage atom.
- **Again.** Answers save to YAML or JSON. `--resume` continues an interrupted install. Passwords are asked again unless you saved them on purpose.

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
11. Wayland compositors, Noctalia or DMS, greetd, and Hyprland or Niri dotfiles
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

Answer every colourful prompt. A summary is shown, including how long this selection should take. You must confirm the disk path before partitioning.

After that confirmation, the installer repeats the estimate and says to get some fresh air or take a nap. While the install runs it prints a percent on its own line, under the command output. Partitioning and the stage download stay in the single digits. During `emerge`, the percent follows Portage’s finished-package count. `@world` moves its own slice, and each later package moves the slice that matches its share of the estimate. The number does not climb on the clock. It prints 100% when the install finishes, then asks you to power down and to remove the USB only after the machine is off.

A full desktop emerge can take **many hours**. Prefer **binary packages** and **gentoo-kernel-bin** unless you want to compile everything.

## Safety

This installer has been actively tested on bare metal. The default path **destroys all data on the selected disk**.

## Research

The installer comes from three comparisons: public Gentoo install scripts ([RESEARCH.md](RESEARCH.md)), the Arch wiki read beside the Gentoo Handbook, and hardware the Ubuntu boot image can see on the machine being installed.

### Arch install guide and the Gentoo Handbook

The [Arch Linux Installation guide](https://wiki.archlinux.org/title/Installation_guide) and [General recommendations](https://wiki.archlinux.org/title/General_recommendations) were read next to the [Gentoo AMD64 Handbook](https://wiki.gentoo.org/wiki/Handbook:AMD64) and the Gentoo wiki pages those chapters link to.

The wizard follows handbook order. Arch is stricter in a few places, and the installer follows Arch there:

- `/etc/fstab` names a plain partition by PARTUUID on GPT, or by filesystem UUID otherwise. LVM, LUKS, and ZFS stay on `/dev/mapper` names, because a filesystem UUID is shared with LVM snapshots. The EFI System Partition is mounted at `/efi` with `umask=0077`.
- GRUB keeps the kernel on `/boot`. systemd-boot and EFI stub install the kernel onto `/efi`, which is the handbook rule for those loaders. Arch gets the same result by mounting the ESP at `/boot`.
- An empty mirror answer emerges `mirrorselect`, speed-tests mirrors, and writes `GENTOO_MIRRORS`.
- The console keymap is asked before any LUKS passphrase, then kept for the installed system.
- An SSD gets a weekly `fstrim`. The tools chapter offers nftables and defaults to yes. Port 22 is opened only when sshd is selected.
- A disk under the handbook’s 40 GB suggestion gets a warning. 32-bit UEFI is limited to GRUB with the `i386-efi` target. Declining `linux-firmware` says that AMD CPU microcode lives in that package.
- `nano` is installed on the new system.
- At 100% the installer asks you to power down, and to remove the USB only after the machine is off.

Arch will reuse an existing EFI partition for another operating system. This installer still wipes the disk you choose and does not set up dual boot.

### USE flags from the Ubuntu boot image

The Ubuntu image is the same computer Gentoo will boot, so `lspci`, `lsusb`, `/proc/cpuinfo`, and `ld.so --help` are fair probes. Ubuntu’s own package list is not a source of Gentoo USE flags.

The installer uses that probe only as a default you can change:

- The binhost menu highlights `x86-64-v3` when glibc reports that level, or when the CPU has `avx`, `avx2`, `bmi1`, `bmi2`, `f16c`, `fma`, `abm`, and `movbe`. Otherwise it stays on generic `x86-64`. A v3 package on an older CPU stops with an illegal instruction.
- `INPUT_DEVICES` is `libinput`, plus `wacom` when a tablet is on the bus.
- `bluetooth`, `wifi`, and `alsa` start selected when that hardware is present.
- An NVIDIA card asks for nouveau or the proprietary driver. Nouveau is the default. The proprietary driver needs a matched kernel module and often fails on a first install.
- A virtual machine with no physical GPU is offered `virtio vmware qxl` instead of `fbdev vesa`.

`CPU_FLAGS_X86` still comes from `cpuid2cpuflags` after the chroot exists. Flag names in `/proc/cpuinfo` do not match Portage (`sse3` appears as `pni`). Global flags such as `opengl`, `vulkan`, `gtk`, and the Qt flags stay with the profile and the compositor questions. Binary packages still install when USE does not match exactly, so glibc is not rebuilt for every locale.

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
| `gentoo_installer/estimate.py` | Spoken install time and the percent slices |
| `gentoo_installer/ui.py` | Colours, menus, passwords, install percent |
| `gentoo_installer/catalog.py` | Package categories, WMs, overlays, Plymouth themes |

$\scriptsize\textcolor{purple}{\textsf{Y}}\textcolor{green}{\textsf{o}}\textcolor{purple}{\textsf{u}}\textcolor{green}{\textsf{'}}\textcolor{purple}{\textsf{r}}\textcolor{green}{\textsf{e}}\ \textcolor{purple}{\textsf{W}}\textcolor{green}{\textsf{e}}\textcolor{purple}{\textsf{l}}\textcolor{green}{\textsf{c}}\textcolor{purple}{\textsf{o}}\textcolor{green}{\textsf{m}}\textcolor{purple}{\textsf{e}}$ 😈
