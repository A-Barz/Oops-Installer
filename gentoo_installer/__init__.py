"""Gentoo live installer package.

This package is designed to run on an Ubuntu Boot Image live session and install
Gentoo onto a local disk. Layout:

  ui.py        Colourful terminal widgets (menus, checklists, passwords).
  config.py    Dataclass of every wizard answer (InstallConfig).
  detect.py    Host probes: UEFI, disks, GPU, TPM, network.
  runner.py    Command execution, dry-run, log file, resumable phases.
  catalog.py   Emerge atoms, overlays, window managers, Plymouth themes.
  wizard.py    Handbook-ordered Q&A that fills InstallConfig.
  execute.py   Destructive host work + generated chroot bash script.

Version is informational only; there is no packaging index.
The live banner prints INSTALLER_REV (bump that integer on each change).
"""

from .rev import INSTALLER_REV

__version__ = f"1.{INSTALLER_REV}.0"
