"""emerge @world: pass $RESPECT_USE=y when supported; retry --usepkg=n once."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import render_chroot_script
from gentoo_installer.rev import INSTALLER_REV


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "rsync"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.locale = "en_US.UTF-8"
    cfg.dry_run = True
    cfg.bootloader = "grub"
    cfg.bootloaders = ["grub"]
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class WorldUsepkgNRetryTests(unittest.TestCase):
    def test_rev_bumped_for_respect_use_world(self) -> None:
        self.assertGreaterEqual(INSTALLER_REV, 83)

    def test_primary_world_passes_respect_use(self) -> None:
        script = render_chroot_script(_cfg(binhost=True), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn(
            "emerge --verbose --update --deep --newuse --keep-going "
            "$RESPECT_USE $NR @world",
            script,
        )
        # Bare $NR @world without $RESPECT_USE is the weak/NOTE path — reject it.
        self.assertNotIn(
            "emerge --verbose --update --deep --newuse --keep-going $NR @world",
            script,
        )
        # portage_prepare sets =y when Portage supports it; never =n (force-binpkg).
        self.assertIn('RESPECT_USE="--binpkg-respect-use=y"', script)
        self.assertNotIn('RESPECT_USE="--binpkg-respect-use=n"', script)
        self.assertIn("binhost/usepkg on; RESPECT_USE=", script)
        for line in script.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "emerge" in stripped:
                self.assertNotIn(
                    "--binpkg-respect-use=n",
                    stripped,
                    msg=f"emerge must not pass =n: {stripped}",
                )

    def test_non_zero_retries_usepkg_n_with_dump(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        world = script.find('echo ">> emerge @world"')
        self.assertGreaterEqual(world, 0)
        chunk = script[world : world + 2500]
        self.assertIn("dump_world_emerge_failure", chunk)
        self.assertIn("retrying once from source (--usepkg=n)", chunk)
        self.assertIn(
            "--usepkg=n --getbinpkg=n @world",
            chunk,
        )
        self.assertIn("@world source rebuild failed", chunk)
        first_dump = chunk.find('dump_world_emerge_failure "$world_status"')
        retry = chunk.find("--usepkg=n --getbinpkg=n @world")
        second_dump = chunk.find('dump_world_emerge_failure "$_world_retry_status"')
        self.assertGreater(first_dump, 0)
        self.assertGreater(retry, first_dump)
        self.assertGreater(second_dump, retry)
        self.assertIn("exit 1", chunk[second_dump:])

    def test_dump_prefixes_portage_detail(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("dump_world_emerge_failure()", script)
        self.assertIn("@world Portage failure dump", script)
        self.assertIn("=y when Portage supports it; never =n", script)
        self.assertIn('echo ">> $_line" >&2', script)
        self.assertIn("/var/tmp/portage/*/temp/build.log", script)
        self.assertIn("Failed to emerge", script)
        self.assertIn("emerge --info", script)

    def test_sigill_still_disables_binpkgs_then_source(self) -> None:
        script = render_chroot_script(_cfg(), {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn(
            "@world died with Illegal instruction; rebuilding from source",
            script,
        )
        sigill = script.find('world_status" -eq 132')
        self.assertGreaterEqual(sigill, 0)
        disable = script.find("disable_binpkgs", sigill)
        retry = script.find("--usepkg=n --getbinpkg=n @world", sigill)
        self.assertGreater(disable, sigill)
        self.assertGreater(retry, disable)


if __name__ == "__main__":
    unittest.main()
