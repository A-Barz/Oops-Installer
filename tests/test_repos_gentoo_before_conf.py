"""Create /var/db/repos/gentoo before repos.conf sets location= there."""

from __future__ import annotations

import unittest

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import _portage_tree_sync_script, render_chroot_script


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "webrsync"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    cfg.locale = "en_US.UTF-8"
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class ReposGentooBeforeConfTests(unittest.TestCase):
    def test_all_methods_mkdir_repos_before_sync(self) -> None:
        for method in ("webrsync", "rsync", "git"):
            script = _portage_tree_sync_script(_cfg(sync_method=method))
            create = script.find("create /var/db/repos/gentoo before repos.conf location=")
            mkdir = script.find("mkdir -p /var/db/repos/gentoo")
            self.assertGreaterEqual(create, 0, method)
            self.assertGreaterEqual(mkdir, 0, method)
            self.assertIn("failed to create /var/db/repos/gentoo", script, method)

    def test_git_writes_repos_conf_after_mkdir(self) -> None:
        script = _portage_tree_sync_script(_cfg(sync_method="git"))
        mkdir = script.find("mkdir -p /var/db/repos/gentoo")
        conf = script.find("/etc/portage/repos.conf/gentoo.conf")
        location = script.find("location = /var/db/repos/gentoo")
        sync = script.find("emerge --sync")
        self.assertGreaterEqual(mkdir, 0)
        self.assertGreaterEqual(conf, 0)
        self.assertLess(mkdir, conf)
        self.assertLess(mkdir, location)
        self.assertLess(location, sync)
        # Fail closed if create failed.
        self.assertIn("exit 1", script)

    def test_chroot_git_order_mkdir_then_conf_then_verify(self) -> None:
        script = render_chroot_script(
            _cfg(sync_method="git"),
            {"root": "/dev/vda2", "esp": "/dev/vda1"},
        )
        mkdir = script.find("mkdir -p /var/db/repos/gentoo")
        conf = script.find("repos.conf/gentoo.conf")
        verify = script.find("verify Portage tree at /var/db/repos/gentoo")
        self.assertLess(mkdir, conf)
        self.assertLess(conf, verify)
        self.assertIn("failed to create /var/db/repos/gentoo before Portage tree sync", script)


if __name__ == "__main__":
    unittest.main()
