# Gentoo installer research (GitHub)

Feature comparison of public Gentoo installation scripts, merged into this Python installer.

Handbook structure used as the organiser:
https://wiki.gentoo.org/wiki/Handbook:AMD64

| Source | Language | UI | Disks | Init | Kernel | Extras |
| --- | --- | --- | --- | --- | --- | --- |
| [oddlama/gentoo-install](https://github.com/oddlama/gentoo-install) | Shell | menuconfig TUI + config file | ext4, Btrfs, ZFS, LUKS, mdraid, EFI/BIOS | OpenRC + systemd | dist-kernel, initramfs | SSH, resume-via-config, chroot helper |
| [l0rdg3x/gentoo-install](https://github.com/l0rdg3x/gentoo-install) | Shell | dialog wizard then unattended | Btrfs subvols, LUKS, TPM2, NVMe | OpenRC + systemd | bin/source, dracut, Plymouth | Secure Boot, SELinux, ZRAM, binhost, variants |
| [pedro-pereira-dev/gentoo-installer](https://github.com/pedro-pereira-dev/gentoo-installer) | POSIX sh | interactive or CLI args | existing partitions, amd64/arm64, BIOS/UEFI | both via stage3 | dist-kernel | unattended flags |
| [nathanshearer/gentooinstall](https://github.com/nathanshearer/gentooinstall) | Shell | CLI phases | destination dir or disk | — | compile kernel, initrd | resume phases, many bootloaders, GPG/hash |
| [jeremypass96/gentoo-installer](https://github.com/jeremypass96/gentoo-installer) | Shell | dialog | auto GPT/MBR, swapfile | — | binary or source | GPU detect, DEs, browsers, CUPS, wireless |
| [readysloth/gentoo-autoinstall](https://github.com/readysloth/gentoo-autoinstall) | Python + bash | argparse | whole disk | OpenRC/systemd | — | dry-run, resume, binpkgs, USE, GUI levels |
| [NucleaPeon/gentooimgr](https://github.com/NucleaPeon/gentooimgr) | Python | JSON config | mount/chroot | — | — | QEMU/cloud, EFI |
| [realvolk/GForge](https://github.com/realvolk/GForge) | TUI | guided | LVM, LVM-on-LUKS, LUKS2, Btrfs, XFS, f2fs | OpenRC/systemd stages | UKI, several bootloaders | DE presets, overlays, Flatpak |

## Merged into this project

- Colourful start-to-finish prompts (dialog/TUI idea, Python stdlib)
- Dry-run and resumable phases
- Ubuntu live host bootstrap (`apt` tools) so the Gentoo ISO is not required
- GPT/MBR, UEFI/BIOS, handbook / Btrfs subvolumes / LVM / LVM-on-LUKS / RAID1 / ZFS / existing partitions
- LUKS2, optional TPM2 helper script, encrypted swap
- Stage3 flavour mapping + SHA256/GPG verification
- OpenRC or systemd, desktop or server, hardened/musl variants
- Profile, USE, binhost, git/rsync/webrsync, cpuid2cpuflags, optional LTO/hardened CFLAGS
- Dist-kernel bin or source, firmware, microcode, dracut, optional UKI
- GRUB, systemd-boot, rEFInd, EFI stub, Syslinux
- NetworkManager/dhcpcd/netifrc, sshd, CUPS, chrony, syslog, cron, ZRAM, Plymouth
  (Ubuntu Boot Image host clock sync uses apt **ntpsec**, which must replace host chrony — not a Gentoo emerge change)
- Users, sudo, hostname/timezone/locale/keymap
- XFCE/Plasma/GNOME/MATE/Cinnamon/i3, browsers, Flatpak, extra packages
- SELinux (hardened) and Secure Boot preparation flags
