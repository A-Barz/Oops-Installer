"""Colourful terminal UI for the Gentoo handbook-ordered installer.

All user input goes through these helpers so the rest of the code stays
free of ANSI codes. Respects NO_COLOR; FORCE_COLOR=1 enables colour when
stdout is not a TTY.

Interactive menus (live USB / a real TTY):
  ↑ ↓         move highlight
  Space       select (single-choice) or toggle (checklists)
  Enter       confirm the highlighted item, or finish a checklist
  ← →  PgUp/PgDn   previous / next page on long lists
  /           search in checklists
  a / c       select / clear the visible page
  y / n       shortcuts on yes/no

If stdin is not a TTY, numbered prompts are used as a fallback.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import sys
import threading
import time
from typing import Iterable, Sequence

# Live chroot transcript and host step lines share stdout.
OUTPUT_LOCK = threading.RLock()


class C:
    """Gentoo brand colours (purple #54487A, green #DDDA55) plus status hues."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    PURPLE = "\033[38;2;84;72;122m"
    GREEN = "\033[38;2;221;218;85m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[38;2;84;72;122m"
    MAGENTA = "\033[38;2;84;72;122m"
    CYAN = "\033[38;2;84;72;122m"
    WHITE = "\033[97m"
    BG_PURPLE = "\033[48;2;84;72;122m"
    BG_GREEN = "\033[48;2;88;96;32m"
    BG_BLUE = "\033[48;2;84;72;122m"
    BG_MAGENTA = "\033[48;2;84;72;122m"
    BG_RED = "\033[41m"


def _enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def paint(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes, or return it unchanged when colour is off."""
    if not _enabled():
        return text
    return "".join(codes) + text + C.RESET


# Overlay name "guru" / "GURU" — Gentoo purple whenever the installer prints it.
_GURU_WORD = re.compile(r"(?<![A-Za-z0-9_])([Gg][Uu][Rr][Uu])(?![A-Za-z0-9_])")


def highlight_guru(text: str, *, after: str = "") -> str:
    """Paint the GURU overlay name in Gentoo purple (#54487A).

    `after` is re-applied after each match so a surrounding colour (e.g. yellow
    warn text) continues for the rest of the line.
    """
    if not text or not _enabled() or "guru" not in text.lower():
        return text
    repl = f"{C.BOLD}{C.PURPLE}\\1{C.RESET}{after}"
    return _GURU_WORD.sub(repl, text)


def width() -> int:
    return max(60, shutil.get_terminal_size((100, 24)).columns)


# Opening brand (user wording). Closing quote added so the tagline displays balanced.
BANNER_NAME = "Oops-Installer"
BANNER_TAGLINE = "'A Gentoo Wayland First and Suckless Setup Tool'"


def _oops_mark_ascii() -> list[str]:
    """Compact Gentoo-coloured mark above the wordmark (not a FIGlet / screensaver)."""
    return [
        r"   .--.  ",
        r"  /    \ ",
        r"  \ OO / ",
        r"   '--'  ",
    ]


def _oops_installer_ascii() -> list[str]:
    """Block ASCII for Oops-Installer — replaces the old Gentoo 'g' / 8-ball mark."""
    # Raw strings keep backslashes literal. Lines padded to a shared width for centering.
    return [
        r"  ___                   ___           _        _ _           ",
        r" / _ \  ___  _ __  ___ |_ _|_ __  ___| |_ __ _| | | ___ _ __ ",
        r"| | | |/ _ \| '_ \/ __| | || '_ \/ __| __/ _` | | |/ _ \ '__|",
        r"| |_| | (_) | |_) \__ \ | || | | \__ \ || (_| | | |  __/ |   ",
        r" \___/ \___/| .__/|___/|___|_| |_|___/\__\__,_|_|_|\___|_|   ",
        r"            |_|                                              ",
    ]


def banner() -> None:
    """Revision first, then Oops-Installer ASCII + tagline — first thing install.py prints."""
    from .rev import INSTALLER_REV

    w = width()
    line = "═" * (w - 2)
    rev = f"rev {INSTALLER_REV}"
    print()
    print(paint(f"  {BANNER_NAME}  {rev}", C.BOLD, C.PURPLE))
    # Small mark, then the wordmark — purple/green Gentoo palette only.
    for index, art in enumerate(_oops_mark_ascii()):
        tone = C.GREEN if index % 2 == 0 else C.PURPLE
        print(paint(art.center(w) if len(art) < w else art, C.BOLD, tone))
    for index, art in enumerate(_oops_installer_ascii()):
        # Gentoo palette: purple #54487A body, green #DDDA55 on alternating rows.
        tone = C.PURPLE if index % 2 == 0 else C.GREEN
        print(paint(art.center(w) if len(art) < w else art, C.BOLD, tone))
    print(paint(BANNER_TAGLINE.center(w) if len(BANNER_TAGLINE) < w else f"  {BANNER_TAGLINE}", C.BOLD, C.GREEN))
    print()
    print(paint("╔" + line + "╗", C.BOLD, C.PURPLE))
    # Never say XFCE here — the host is a generic Ubuntu Boot Image / Ubuntu image.
    print(paint("║" + "Host: Ubuntu Boot Image (Ubuntu image)".center(w - 2) + "║", C.BOLD, C.GREEN))
    print(paint("║" + "Ubuntu Boot Image live  →  Gentoo disk".center(w - 2) + "║", C.GREEN))
    print(paint("║" + "Handbook-ordered  ·  wiki.gentoo.org".center(w - 2) + "║", C.PURPLE))
    print(paint("║" + "Wayland-first · suckless · OpenRC-safe".center(w - 2) + "║", C.GREEN))
    print(paint("╚" + line + "╝", C.BOLD, C.PURPLE))
    print()
    opening_thanks()
    navigation_help()


def opening_thanks() -> None:
    """Brief welcome before handbook chapters. Stars are GitHub’s “rating”."""
    print(paint("  Thanks for testing Oops-Installer.", C.BOLD, C.GREEN))
    print(
        paint(
            "  If you enjoy it, a GitHub star is the usual rating "
            "(https://github.com/A-Barz/Oops-Installer), and sharing an install "
            "story on social media is welcome too — only if you want to.",
            C.PURPLE,
        )
    )
    print()


def navigation_help() -> None:
    """Keys used by every menu and checklist."""
    print(paint("  How to navigate", C.BOLD, C.GREEN))
    rows = [
        ("↑  /  ↓", "Move the highlight"),
        ("Space", "Select this item  ·  or toggle a checkbox"),
        ("Enter", "Confirm  ·  on checklists, finish and continue"),
        ("←  →   or  PgUp / PgDn", "Previous / next page (long lists)"),
        ("/", "Search packages, overlays, and window managers"),
        ("a  /  c", "Select all / clear all on the current page"),
        ("Select all", "Top row of a package list — tick every package in that section"),
        ("y  /  n", "Yes / no shortcuts"),
        ("Ctrl+C", "Abort the wizard (nothing is written until the disk confirm)"),
    ]
    label_w = max(len(k) for k, _ in rows)
    for key, desc in rows:
        print(f"    {paint(key.ljust(label_w), C.BOLD, C.GREEN)}  {paint(desc, C.PURPLE)}")
    print()
    print(paint("  Type answers only when you see a  ?  prompt (paths, names, sizes).", C.DIM, C.PURPLE))
    print()


def chapter(number: str, title: str, handbook_url: str) -> None:
    """Print a handbook chapter header so the user can open the matching wiki page."""
    w = width()
    print()
    print(paint("▄" * w, C.PURPLE))
    print(paint(f"  Handbook {number}: {title}", C.BOLD, C.GREEN, C.BG_PURPLE))
    print(paint(f"  {handbook_url}", C.DIM, C.GREEN))
    print(paint("▀" * w, C.PURPLE))
    print()


def info(msg: str) -> None:
    print(paint("  ℹ  ", C.PURPLE) + highlight_guru(msg))


def guru_note(msg: str) -> None:
    """Short reason labeled guru in Gentoo purple (#54487A)."""
    label = paint("guru", C.BOLD, C.PURPLE) if _enabled() else "guru"
    print(f"  {label}: {paint(msg, C.PURPLE) if _enabled() else msg}")


def ok(msg: str) -> None:
    print(paint("  ✓  ", C.GREEN, C.BOLD) + highlight_guru(msg))


def warn(msg: str) -> None:
    print(paint("  !  ", C.YELLOW, C.BOLD) + highlight_guru(paint(msg, C.YELLOW), after=C.YELLOW))


def error(msg: str) -> None:
    print(paint("  ✗  ", C.RED, C.BOLD) + highlight_guru(paint(msg, C.RED), after=C.RED))


def step(msg: str) -> None:
    """One host command about to run (used by runner.Runner)."""
    print(paint("  »  ", C.PURPLE, C.BOLD) + highlight_guru(msg))


# Shown right after the user re-types the target disk (must be impossible to miss).
INSTALL_WAIT_TITLE = "Take a nice relaxing nap"
INSTALL_WAIT_NOTICE = (
    "Dream about the source compiling in the clouds — "
    "there is no progress bar or percent. Come back after the estimate."
)


def install_wait_notice() -> None:
    """Friendly nap banner after disk confirmation (and again as execute starts).

    Uses a full-width rule + bold title so it stays visible; a plain info() line
    was easy to miss once host/chroot output started scrolling.
    """
    w = width()
    rule = "═" * w
    print()
    print(paint(rule, C.BOLD, C.PURPLE))
    print(paint(f"  {INSTALL_WAIT_TITLE}", C.BOLD, C.GREEN))
    print(paint(f"  {INSTALL_WAIT_NOTICE}", C.BOLD, C.PURPLE))
    print(paint(rule, C.BOLD, C.PURPLE))
    print()
    sys.stdout.flush()


def congratulate() -> None:
    """Closing lines after a finished install."""
    ok("Congratulations! Your Gentoo install is complete.")
    info("Power down the device.")
    info("After it has shut down, remove the USB.")
    ok("Let's hope it boots 😊 ❤️")


def danger(msg: str) -> None:
    """Destructive action (disk wipe). Keep wording explicit."""
    print(paint("  ⚠  DESTRUCTIVE: ", C.BOLD, C.WHITE, C.BG_RED) + " " + paint(msg, C.RED, C.BOLD))


def prompt(question: str, default: str | None = None) -> str:
    """Free-text prompt. Empty input returns default when given."""
    suffix = f" [{paint(default, C.GREEN)}]" if default is not None else ""
    raw = input(paint("  ?  ", C.GREEN, C.BOLD) + question + suffix + " : ").strip()
    if not raw and default is not None:
        return default
    return raw


def _decode_csi(seq: str) -> str:
    """Map the bytes after ESC to a key name.

    Terminals send CSI (`[B`) or SS3 (`OB`). Modified arrows look like `[1;2B`.
    A fixed two-byte read leaves the rest in the buffer, so the next Down is ignored.
    """
    if not seq:
        return "esc"
    if seq[0] == "O" and len(seq) >= 2:
        return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(seq[1], "esc")
    if not seq.startswith("["):
        return "esc"
    body = seq[1:]
    if body.endswith("~"):
        body = body[:-1]
    if body and body[-1].isalpha():
        return {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}.get(body[-1], "")
    code = body.split(";")[0]
    return {"5": "pgup", "6": "pgdn", "1": "home", "4": "end", "3": "delete"}.get(code, "")


def _read_escape_posix(fd: int) -> str:
    """Read the rest of an escape sequence. Bytes often arrive one at a time.

    80ms was short enough on a busy live USB that the '[' or the final letter
    was still in the kernel buffer. That press was ignored, and the leftover
    letter made the next arrow look like it needed a second tap.
    """
    import select

    seq = ""
    wait = 0.2
    while len(seq) < 16:
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            break
        chunk = os.read(fd, 16)
        if not chunk:
            break
        seq += chunk.decode("latin-1")
        if seq[-1].isalpha() or seq.endswith("~"):
            break
        wait = 0.12
    return _decode_csi(seq)


def _read_escape_nt() -> str:
    """Windows Terminal sometimes sends VT arrows instead of scan codes."""
    import msvcrt

    seq = ""
    end = time.monotonic() + 0.2
    while time.monotonic() < end and len(seq) < 16:
        if msvcrt.kbhit():
            seq += msvcrt.getwch()
            if seq[-1:].isalpha() or seq.endswith("~"):
                break
            end = time.monotonic() + 0.12
        else:
            time.sleep(0.005)
    return _decode_csi(seq) if seq else "esc"


def _read_key() -> str:
    """One keypress. Returns 'up','down','left','right','enter','space', 'pgup','pgdn', or a character."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in {"\x00", "\xe0"}:
            extra = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pgup", "Q": "pgdn"}.get(extra, "")
        if ch in {"\r", "\n"}:
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1b":
            return _read_escape_nt()
        if ch == "\x08":
            return "backspace"
        return ch

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        chunk = os.read(fd, 1)
        if not chunk:
            return ""
        ch = chunk.decode("latin-1")
        if ch == "\x1b":
            return _read_escape_posix(fd)
        if ch in {"\r", "\n"}:
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in {"\x7f", "\x08"}:
            return "backspace"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _clip(text: str, reserve: int = 18) -> str:
    """Keep a menu row on one screen line so cursor-up erases the real row."""
    limit = max(24, width() - reserve)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _hide_cursor() -> None:
    if _enabled() and sys.stdout.isatty():
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()


def _show_cursor() -> None:
    if _enabled() and sys.stdout.isatty():
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def _erase_lines(count: int) -> None:
    if count <= 0 or not sys.stdout.isatty():
        return
    for _ in range(count):
        sys.stdout.write("\033[1A\033[2K")
    sys.stdout.flush()


def ask_yes_no(question: str, default: bool = True) -> bool:
    if not _interactive():
        hint = "Y/n" if default else "y/N"
        while True:
            raw = prompt(f"{question} ({hint})", "").lower()
            if not raw:
                return default
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no"}:
                return False
            warn("Please answer yes or no.")

    choice = default
    drawn = 0
    _hide_cursor()
    try:
        while True:
            yes = paint("  Yes  ", C.BOLD, C.WHITE, C.BG_GREEN) if choice else paint("  Yes  ", C.DIM, C.GREEN)
            no = paint("  No   ", C.BOLD, C.WHITE, C.BG_PURPLE) if not choice else paint("  No   ", C.DIM, C.PURPLE)
            lines = [
                paint(f"  {question}", C.BOLD, C.GREEN),
                f"    {yes}  {no}",
                paint("    ↑↓ or ←→ move   Space/Enter confirm   y/n shortcut", C.DIM, C.PURPLE),
            ]
            _erase_lines(drawn)
            print("\n".join(lines))
            drawn = len(lines)
            key = _read_key()
            if key in {"left", "right", "up", "down", "space"}:
                if key == "space":
                    return choice
                choice = not choice if key in {"left", "right", "up", "down"} else choice
            elif key == "enter":
                return choice
            elif key in {"y", "Y"}:
                return True
            elif key in {"n", "N"}:
                return False
    finally:
        _show_cursor()
        print()


def ask_password(question: str, confirm: bool = True) -> str:
    """Hidden password entry. confirm=False is for Wi-Fi PSKs typed once."""
    while True:
        p1 = getpass.getpass(paint("  *  ", C.GREEN) + question + ": ")
        if not p1:
            warn("Password cannot be empty.")
            continue
        if not confirm:
            return p1
        p2 = getpass.getpass(paint("  *  ", C.GREEN) + "Confirm password: ")
        if p1 == p2:
            return p1
        warn("Passwords did not match.")


def _menu_fallback(title: str, choices: Sequence[tuple[str, str]], default: str | None) -> str:
    print(paint(f"  {title}", C.BOLD, C.GREEN))
    keys = [value for value, _ in choices]
    for i, (value, desc) in enumerate(choices, start=1):
        mark = paint("●", C.GREEN) if default == value else paint("○", C.DIM)
        print(
            f"    {mark} {paint(str(i), C.BOLD, C.GREEN)}. "
            f"{highlight_guru(paint(value, C.WHITE), after=C.WHITE)}  "
            f"{highlight_guru(paint(desc, C.DIM), after=C.DIM)}"
        )
    default_idx = str(keys.index(default) + 1) if default in keys else ""
    while True:
        raw = prompt("Enter number", default_idx)
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1][0]
        warn("Pick a listed number.")


def menu(title: str, choices: Sequence[tuple[str, str]], default: str | None = None) -> str:
    """Single-select list. Arrows move; Space or Enter confirms."""
    if not choices:
        raise ValueError(f"No choices to display for: {title}")
    if not _interactive():
        return _menu_fallback(title, choices, default)

    index = 0
    keys = [value for value, _ in choices]
    if default in keys:
        index = keys.index(default)
    page_size = max(8, shutil.get_terminal_size((100, 24)).lines - 10)
    page = index // page_size
    drawn = 0
    _hide_cursor()
    try:
        while True:
            pages = max(1, (len(choices) + page_size - 1) // page_size)
            page = min(max(0, page), pages - 1)
            start = page * page_size
            view = list(choices[start : start + page_size])
            if not (start <= index < start + len(view)):
                index = start
            lines = [
                paint(f"  {title}", C.BOLD, C.GREEN),
                paint(
                    f"  ↑↓ move   Space/Enter select   ←→ page  ·  {page + 1}/{pages}",
                    C.DIM,
                    C.PURPLE,
                ),
            ]
            for offset, (value, desc) in enumerate(view):
                abs_i = start + offset
                cursor = abs_i == index
                mark = paint("▸", C.BOLD, C.GREEN) if cursor else " "
                shown = _clip(desc or value)
                label_codes = (C.BOLD, C.GREEN) if cursor else (C.WHITE,)
                label = highlight_guru(paint(shown, *label_codes), after="".join(label_codes))
                extra = (
                    highlight_guru(paint(f"({value})", C.DIM, C.PURPLE), after=C.DIM + C.PURPLE)
                    if desc
                    else ""
                )
                prefix = paint("  > ", C.BOLD, C.GREEN) if cursor else "    "
                lines.append(f"{prefix}{mark} {label}  {extra}".rstrip())
            _erase_lines(drawn)
            print("\n".join(lines))
            drawn = len(lines)
            key = _read_key()
            if key == "up":
                index = (index - 1) % len(choices)
                page = index // page_size
            elif key == "down":
                index = (index + 1) % len(choices)
                page = index // page_size
            elif key in {"left", "pgup"}:
                page = max(0, page - 1)
                index = page * page_size
            elif key in {"right", "pgdn"}:
                page = min(pages - 1, page + 1)
                index = page * page_size
            elif key in {"enter", "space"}:
                return choices[index][0]
    finally:
        _show_cursor()
        print()


def checklist(title: str, choices: Sequence[tuple[str, str]], selected: Iterable[str] | None = None) -> list[str]:
    """Multi-select. Small lists use one page; large lists should call paged_checklist."""
    return paged_checklist(title, choices, selected=selected, page_size=max(12, len(choices)))


def _section_all_selected(
    choices: Sequence[tuple[str, str]], current: set[str], locked: set[str] | None = None
) -> bool:
    freeze = locked or set()
    ids = [value for value, _desc in choices if value not in freeze]
    return bool(ids) and all(value in current for value in ids)


def _toggle_section(
    choices: Sequence[tuple[str, str]], current: set[str], locked: set[str] | None = None
) -> None:
    """Tick every package in the section, or clear them when they are already all ticked."""
    freeze = locked or set()
    ids = [value for value, _desc in choices if value not in freeze]
    if _section_all_selected(choices, current, freeze):
        for value in ids:
            current.discard(value)
        for value in freeze:
            current.add(value)
        return
    for value in ids:
        current.add(value)
    for value in freeze:
        current.add(value)


def paged_checklist(
    title: str,
    choices: Sequence[tuple[str, str]],
    selected: Iterable[str] | None = None,
    page_size: int = 14,
    select_all: bool = False,
    locked: Iterable[str] | None = None,
) -> list[str]:
    """Searchable multi-select: arrows move, Space toggles, Enter finishes.

    select_all pins a row at the top that ticks every package in this section.
    locked atoms stay selected and cannot be unticked (dotfile / earlier deps).
    """
    freeze = {value for value in (locked or [])}
    if not _interactive():
        return _paged_checklist_fallback(title, choices, selected, page_size, select_all, freeze)

    current = set(selected or []) | freeze
    query = ""
    page = 0
    cursor = -1 if select_all else 0
    page_size = max(8, min(page_size, shutil.get_terminal_size((100, 24)).lines - 12))
    drawn = 0
    _hide_cursor()
    try:
        while True:
            filtered = [(v, d) for v, d in choices if query.lower() in (v + " " + d).lower()]
            pages = max(1, (len(filtered) + page_size - 1) // page_size) if filtered else 1
            page = min(page, pages - 1)
            start = page * page_size
            view = filtered[start : start + page_size]
            if view and cursor >= 0:
                cursor = min(cursor, len(view) - 1)
            hint = (
                "  ↑↓ move   Space toggle   Enter — continue   ←→ page   / search   a page   c clear"
            )
            if select_all:
                hint += "   top row: whole section"
            if freeze:
                hint += "   [already queued] stays on"
            lines = [
                paint(f"  {title}", C.BOLD, C.GREEN),
                paint(
                    f"  selected {len(current)}  ·  page {page + 1}/{pages}  ·  "
                    f"{start + 1}-{start + len(view)} of {len(filtered)}"
                    + (f"  filter=/{query}" if query else ""),
                    C.DIM,
                    C.PURPLE,
                ),
                paint(hint, C.DIM, C.GREEN),
            ]
            if select_all:
                all_on = _section_all_selected(choices, current, freeze)
                box = paint("[x]", C.GREEN, C.BOLD) if all_on else paint("[ ]", C.DIM)
                mark = paint("▸", C.BOLD, C.GREEN) if cursor == -1 else " "
                prefix = paint("  > ", C.BOLD, C.GREEN) if cursor == -1 else "    "
                label = paint("Select all", C.BOLD, C.GREEN if cursor == -1 else C.WHITE)
                lines.append(f"{prefix}{mark} {box} {label}")
            if not view:
                lines.append(paint("    No matches. Press / then Enter to clear the filter.", C.YELLOW))
            flag_w = max((len(value) for value, _desc in view), default=0)
            for i, (value, desc) in enumerate(view):
                on = value in current
                frozen = value in freeze
                box = paint("[x]", C.GREEN, C.BOLD) if on else paint("[ ]", C.DIM)
                mark = paint("▸", C.BOLD, C.GREEN) if i == cursor else " "
                prefix = paint("  > ", C.BOLD, C.GREEN) if i == cursor else "    "
                # The flag or package atom is the value. It stays at the left,
                # in a light colour, so a dark theme does not hide it.
                flag_codes = (C.BOLD, C.GREEN if on or i == cursor else C.WHITE)
                flag = highlight_guru(
                    paint(value.ljust(flag_w), *flag_codes),
                    after="".join(flag_codes),
                )
                room = max(16, width() - flag_w - 16)
                shown = desc if len(desc) <= room else desc[: room - 1] + "…"
                detail_codes = (C.DIM, C.PURPLE) if frozen else (C.WHITE,)
                detail = (
                    highlight_guru(paint(shown, *detail_codes), after="".join(detail_codes))
                    if shown
                    else ""
                )
                lines.append(f"{prefix}{mark} {box} {flag}  {detail}".rstrip())
            _erase_lines(drawn)
            print("\n".join(lines))
            drawn = len(lines)
            key = _read_key()
            if key == "up" and (view or cursor == -1):
                if cursor > 0:
                    cursor -= 1
                elif select_all and cursor == 0:
                    cursor = -1
                elif cursor == -1:
                    pass
                elif page > 0:
                    page -= 1
                    cursor = page_size - 1
            elif key == "down" and (view or cursor == -1):
                if cursor == -1:
                    if view:
                        cursor = 0
                elif cursor < len(view) - 1:
                    cursor += 1
                elif page < pages - 1:
                    page += 1
                    cursor = 0
            elif key in {"left", "pgup"}:
                page = max(0, page - 1)
                if cursor != -1:
                    cursor = 0
            elif key in {"right", "pgdn"}:
                page = min(pages - 1, page + 1)
                if cursor != -1:
                    cursor = 0
            elif key == "space" and select_all and cursor == -1:
                _toggle_section(choices, current, freeze)
            elif key == "space" and view and cursor >= 0:
                val = view[cursor][0]
                if val in freeze:
                    current.add(val)
                elif val in current:
                    current.remove(val)
                else:
                    current.add(val)
            elif key == "enter":
                current |= freeze
                order = [v for v, _ in choices if v in current]
                extra = [v for v in current if v not in {c[0] for c in choices}]
                return order + extra
            elif key == "/":
                _show_cursor()
                _erase_lines(drawn)
                drawn = 0
                query = prompt("Search (empty clears)", "").strip()
                page = 0
                cursor = -1 if select_all else 0
                _hide_cursor()
            elif key in {"a", "A"}:
                for value, _ in view:
                    current.add(value)
            elif key in {"c", "C"}:
                for value, _ in view:
                    if value not in freeze:
                        current.discard(value)
                current |= freeze
    finally:
        _show_cursor()
        print()


def _paged_checklist_fallback(
    title: str,
    choices: Sequence[tuple[str, str]],
    selected: Iterable[str] | None,
    page_size: int,
    select_all: bool = False,
    locked: set[str] | None = None,
) -> list[str]:
    freeze = locked or set()
    current = set(selected or []) | freeze
    query = ""
    page = 0
    info("Not a TTY — type a number to toggle, 0 when done.")
    while True:
        filtered = [(v, d) for v, d in choices if query.lower() in (v + " " + d).lower()]
        pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page = min(page, pages - 1)
        start = page * page_size
        view = filtered[start : start + page_size]
        print()
        print(paint(f"  {title}", C.BOLD, C.GREEN))
        if select_all:
            all_on = _section_all_selected(choices, current, freeze)
            mark = paint("[x]", C.GREEN, C.BOLD) if all_on else paint("[ ]", C.DIM)
            print(f"    {mark} {paint('*', C.GREEN)}. {paint('Select all', C.WHITE)}")
        for i, (value, desc) in enumerate(view, start=1):
            mark = paint("[x]", C.GREEN, C.BOLD) if value in current else paint("[ ]", C.DIM)
            print(
                f"    {mark} {paint(str(i), C.GREEN)}. "
                f"{highlight_guru(paint(value, C.BOLD, C.WHITE), after=C.BOLD + C.WHITE)}  "
                f"{highlight_guru(paint(desc, C.WHITE), after=C.WHITE)}"
            )
        raw = prompt("Package command", "0")
        if raw in {"", "0", "d", "done"}:
            current |= freeze
            order = [v for v, _ in choices if v in current]
            extra = [v for v in current if v not in {c[0] for c in choices}]
            return order + extra
        if raw in {"n", "N", ">"}:
            page = min(pages - 1, page + 1)
        elif raw in {"p", "P", "<"}:
            page = max(0, page - 1)
        elif raw.startswith("/"):
            query = raw[1:].strip()
            page = 0
        elif select_all and raw.lower() in {"*", "all"}:
            _toggle_section(choices, current, freeze)
        elif raw.lower() == "a":
            for value, _ in view:
                current.add(value)
        elif raw.lower() == "c":
            for value, _ in view:
                if value not in freeze:
                    current.discard(value)
            current |= freeze
        elif raw.isdigit() and 1 <= int(raw) <= len(view):
            val = view[int(raw) - 1][0]
            if val in freeze:
                current.add(val)
            elif val in current:
                current.remove(val)
            else:
                current.add(val)
        else:
            extra = ", * for Select all" if select_all else ""
            warn(f"Use a listed number{extra}, /search, n, p, a, c, or 0.")


def summary_table(rows: Sequence[tuple[str, str]]) -> None:
    """Final review table shown before the disk-path confirmation."""
    w = width()
    print()
    print(paint("  ┌" + "─" * (w - 6) + "┐", C.PURPLE))
    print(paint("  │" + " Installation summary".ljust(w - 6) + "│", C.BOLD, C.GREEN))
    print(paint("  ├" + "─" * (w - 6) + "┤", C.PURPLE))
    label_w = min(22, max(len(k) for k, _ in rows) + 2)
    inner = w - 6
    value_w = max(12, inner - label_w - 2)
    for key, value in rows:
        text = value if value else "(none)"
        parts: list[str] = []
        current = ""
        for word in text.split():
            while len(word) > value_w:
                if current:
                    parts.append(current)
                    current = ""
                parts.append(word[:value_w])
                word = word[value_w:]
            if not word:
                continue
            piece = word if not current else current + " " + word
            if len(piece) <= value_w:
                current = piece
            else:
                if current:
                    parts.append(current)
                current = word
        if current or not parts:
            parts.append(current)
        for index, part in enumerate(parts):
            label = key if index == 0 else ""
            line = f" {label.ljust(label_w)} {part}"
            cell = highlight_guru(
                paint(line[:inner].ljust(inner), C.GREEN),
                after=C.GREEN,
            )
            print(paint("  │", C.PURPLE) + cell + paint("│", C.PURPLE))
    print(paint("  └" + "─" * (w - 6) + "┘", C.PURPLE))
    print()
