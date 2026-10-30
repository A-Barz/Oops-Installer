"""dev-vcs/git must be keyworded, emerged, and present before git Portage sync.

Also: chroot must switch off bare LANG=C before emerge/sync (Portage ambiguous locale).
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from gentoo_installer.config import InstallConfig
from gentoo_installer.execute import (
    GIT_ATOM,
    _chroot_helper_atoms,
    _ensure_git_script,
    _installer_utf8_locale,
    _locale_gen_lines,
    _locale_prep_script,
    _make_conf_text,
    _needs_git_for_sync,
    _overlay_script,
    _seed_gentoo_tree_script,
    _write_overlay_portage_files,
    _write_system_files,
    render_chroot_script,
)


def _cfg(**kwargs: object) -> InstallConfig:
    cfg = InstallConfig()
    cfg.architecture = "amd64"
    cfg.sync_method = "webrsync"
    cfg.kernel = "gentoo-kernel-bin"
    cfg.window_managers = []
    cfg.shell = "none"
    cfg.overlays = []
    for key, value in kwargs.items():
        setattr(cfg, key, value)
    return cfg


class GitBeforeSyncTests(unittest.TestCase):
    def test_git_sync_method_needs_git(self) -> None:
        self.assertTrue(_needs_git_for_sync(_cfg(sync_method="git")))
        self.assertFalse(_needs_git_for_sync(_cfg(sync_method="webrsync")))

    def test_overlays_need_git(self) -> None:
        self.assertTrue(_needs_git_for_sync(_cfg(window_managers=["gui-wm/niri"])))

    def test_ensure_git_script_keywords_emerges_and_fail_closed(self) -> None:
        script = _ensure_git_script(_cfg(sync_method="git"))
        self.assertIn("package.accept_keywords/git", script)
        self.assertIn(f"{GIT_ATOM} ~amd64", script)
        self.assertIn("package.unmask/git", script)
        self.assertIn(f"emerge_one {GIT_ATOM}", script)
        self.assertIn("gentoo tree/profile precondition", script)
        self.assertIn("seeding with emerge-webrsync", script)
        self.assertIn("webrsync seed failed before emerge", script)
        self.assertIn("git already on PATH", script)
        self.assertIn("--autounmask-write --autounmask-continue", script)
        self.assertIn("export LANG=en_US.UTF-8", script)
        self.assertIn("export LC_ALL=en_US.UTF-8", script)
        self.assertIn("emerge -pv", script)
        self.assertIn("build.log", script)
        self.assertIn("command -v git", script)
        self.assertIn("git_emerge_status", script)
        seed = script.find("seeding with emerge-webrsync")
        emerge = script.find(f"emerge_one {GIT_ATOM}")
        check = script.find("git missing after emerge")
        self.assertLess(seed, emerge)
        self.assertLess(emerge, check)
        # Keyword/unmask before emerge (inside the missing-git branch).
        self.assertLess(script.find("package.accept_keywords/git"), emerge)
        self.assertLess(script.find("package.unmask/git"), emerge)

    def test_seed_helper_is_git_free_webrsync(self) -> None:
        script = _seed_gentoo_tree_script()
        self.assertIn("emerge-webrsync", script)
        self.assertNotIn("sync-type = git", script)
        self.assertNotIn("github.com/gentoo-mirror", script)

    def test_ensure_git_empty_when_not_needed(self) -> None:
        self.assertEqual(_ensure_git_script(_cfg(sync_method="rsync")), "")

    def test_chroot_script_git_prep_before_tree_sync(self) -> None:
        cfg = _cfg(sync_method="git")
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        prep = script.find("ensure dev-vcs/git (before Portage git sync")
        seed = script.find("seeding with emerge-webrsync")
        tree = script.find("sync-type = git")
        sync_echo = script.find(">> sync Portage tree")
        self.assertGreaterEqual(prep, 0)
        self.assertGreaterEqual(seed, 0)
        self.assertGreaterEqual(tree, 0)
        self.assertLess(prep, seed)
        self.assertLess(seed, sync_echo)
        self.assertLess(sync_echo, tree)
        # Tree sync itself fail-closes if git vanished.
        self.assertIn("git missing before Portage tree sync", script)
        # Seed is git-free scaffolding; configured git sync still follows.
        self.assertLess(seed, script.find("emerge_one " + GIT_ATOM))

    def test_make_conf_omits_binpkg_respect_use(self) -> None:
        for respect in (False, True):
            cfg = _cfg(binhost=True, binpkg_respect_use=respect)
            text = _make_conf_text(cfg)
            self.assertNotIn("binpkg-respect-use", text)
            self.assertIn("--getbinpkg", text)
            self.assertIn("--usepkg", text)

    def test_sanitize_before_first_package_emerge(self) -> None:
        """No package emerge before make.conf is free of --binpkg-respect-use."""
        cfg = _cfg(sync_method="git", binhost=True, window_managers=["gui-wm/niri"])
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        self.assertIn("sanitize_binpkg_respect_use()", script)
        self.assertIn("s/ --binpkg-respect-use=[^\" ]*//g", script)
        sanitize_echo = script.find(">> sanitize make.conf before first emerge")
        sanitize = script.find("\nsanitize_binpkg_respect_use\n", sanitize_echo)
        git_emerge = script.find(f"emerge_one {GIT_ATOM}")
        self.assertGreaterEqual(sanitize_echo, 0)
        self.assertGreaterEqual(sanitize, 0, "pre-git sed sanitize must be invoked")
        self.assertGreaterEqual(git_emerge, 0)
        self.assertLess(sanitize, git_emerge)
        # Full emerge --help detection stays after profile (avoid make.profile noise).
        profile = script.find(">> eselect profile")
        prepare = script.find("\nportage_prepare\n", profile)
        self.assertGreaterEqual(profile, 0)
        self.assertGreaterEqual(prepare, 0)
        self.assertLess(sanitize, profile)
        self.assertLess(profile, prepare)
        # Top-level package emerges (column 0) must not precede sanitize.
        for match in re.finditer(r"(?m)^emerge (?!--help)", script):
            self.assertGreaterEqual(
                match.start(),
                sanitize,
                f"package emerge before sanitize: {match.group(0)!r}",
            )
            break

    def test_overlay_script_emerges_git_before_eselect_enable(self) -> None:
        script = _overlay_script(_cfg(window_managers=["gui-wm/niri"]))
        git_emerge = script.find(f"emerge_one {GIT_ATOM}")
        git_check = script.find("git missing after emerge")
        eselect = script.find("emerge --ask=n --verbose --noreplace app-eselect/eselect-repository")
        enable = script.find("eselect repository enable")
        self.assertGreaterEqual(git_emerge, 0)
        self.assertGreaterEqual(eselect, 0)
        self.assertLess(git_emerge, git_check)
        self.assertLess(git_check, eselect)
        self.assertLess(eselect, enable)
        self.assertIn("package.accept_keywords/git", script)
        self.assertIn("export LANG=en_US.UTF-8", script)
        self.assertIn("gentoo tree/profile precondition", script)

    def test_chroot_helpers_include_git_for_git_sync(self) -> None:
        atoms = _chroot_helper_atoms(_cfg(sync_method="git"))
        self.assertIn(GIT_ATOM, atoms)

    def test_host_writes_git_keywords_for_git_sync_without_overlays(self) -> None:
        cfg = _cfg(sync_method="git")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "etc" / "portage").mkdir(parents=True)
            _write_overlay_portage_files(cfg, str(root))
            kw = (root / "etc/portage/package.accept_keywords/git").read_text()
            um = (root / "etc/portage/package.unmask/git").read_text()
        self.assertIn(f"{GIT_ATOM} ~amd64", kw)
        self.assertIn(GIT_ATOM, um)


class LocaleBeforeSyncTests(unittest.TestCase):
    def test_installer_utf8_avoids_bare_c(self) -> None:
        self.assertEqual(_installer_utf8_locale(_cfg(locale="C")), "en_US.UTF-8")
        self.assertEqual(_installer_utf8_locale(_cfg(locale="POSIX")), "en_US.UTF-8")
        self.assertEqual(
            _installer_utf8_locale(_cfg(locale="C", stage_variant="musl")),
            "C.UTF-8",
        )
        self.assertEqual(_installer_utf8_locale(_cfg(locale="de_DE")), "de_DE.UTF-8")
        self.assertEqual(
            _installer_utf8_locale(_cfg(locale="en_GB.UTF-8")),
            "en_GB.UTF-8",
        )

    def test_locale_gen_lines_never_emit_bare_c(self) -> None:
        lines = _locale_gen_lines(_cfg(locale="C"))
        self.assertTrue(any("en_US.UTF-8" in line for line in lines))
        self.assertTrue(any("C.UTF-8" in line for line in lines))
        self.assertFalse(any(line.startswith("C UTF-8") for line in lines))

    def test_locale_prep_runs_locale_gen_and_exports_utf8(self) -> None:
        script = _locale_prep_script(_cfg(locale="en_US.UTF-8"))
        self.assertIn("locale-gen", script)
        self.assertIn("export LANG=en_US.UTF-8", script)
        self.assertIn("export LC_ALL=en_US.UTF-8", script)
        self.assertNotRegex(script, r"export LANG=C\n")
        self.assertNotRegex(script, r"export LC_ALL=C\n")
        # Fail closed before emerge so Portage never prints ambiguous C / localectl.
        self.assertIn("refuse emerge under ambiguous LANG", script)
        self.assertIn("need UTF-8", script)

    def test_chroot_locale_prep_before_sync_and_git(self) -> None:
        cfg = _cfg(sync_method="git", locale="en_US.UTF-8")
        script = render_chroot_script(cfg, {"root": "/dev/vda2", "esp": "/dev/vda1"})
        # Early header still uses C until locale-gen; Portage path must not.
        self.assertIn("export LANG=C", script)
        locale_echo = script.find("generate UTF-8 locales before emerge/sync")
        git_echo = script.find("ensure dev-vcs/git (before Portage git sync")
        sync_echo = script.find(">> sync Portage tree")
        self.assertGreaterEqual(locale_echo, 0)
        self.assertLess(locale_echo, git_echo)
        self.assertLess(git_echo, sync_echo)
        # After locale_prep, LANG must be UTF-8 before sync (not left as bare C).
        after_locale = script[locale_echo:sync_echo]
        self.assertIn("export LANG=en_US.UTF-8", after_locale)
        self.assertIn("export LC_ALL=en_US.UTF-8", after_locale)

    def test_host_02locale_uses_utf8_not_c(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_system_files(_cfg(locale="C"), str(root))
            body = (root / "etc/env.d/02locale").read_text()
            gen = (root / "etc/locale.gen").read_text()
        self.assertIn('LANG="en_US.UTF-8"', body)
        self.assertNotIn('LANG="C"', body)
        self.assertIn("en_US.UTF-8 UTF-8", gen)
        self.assertIn("C.UTF-8 UTF-8", gen)


if __name__ == "__main__":
    unittest.main()
