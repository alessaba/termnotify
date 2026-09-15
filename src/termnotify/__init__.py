"""Terminal-mediated desktop notifications, the way OpenCode sends them.

OpenCode (through its TUI framework, OpenTUI) does not call macOS
notification APIs. It writes an OSC escape sequence to the terminal and
lets the terminal show the notification. This module is a small,
dependency-free Python implementation of the same idea.

Two protocols are all you need on a Mac:

* ``osc9``   -- iTerm2, Apple Terminal, ConEmu
* ``osc777`` -- Ghostty, WezTerm, Warp, VTE terminals, Windows Terminal
* ``osc99``  -- kitty, foot (base64-encoded title/body)

Basic usage::

    from termnotify import notify

    notify("Build finished", title="CI")

``notify()`` returns ``True`` when the sequence was written to a terminal.
That means the terminal was *asked* to notify; it is not a guarantee that a
banner was displayed (the window may be focused, notifications muted, ...).
"""

from __future__ import annotations

import base64
import itertools
import os
import re
import sys
from typing import IO, Mapping, Optional

__all__ = ["notify", "detect_protocol", "SUPPORTED_PROTOCOLS"]
__version__ = "0.1.0"

#: Protocols termnotify knows how to speak.
SUPPORTED_PROTOCOLS = ("osc9", "osc777", "osc99")

#: Environment variable to force a protocol, or disable notifications with
#: ``0``/``false``/``off``/``none``. Values are case-insensitive.
ENV_OVERRIDE = "TERMNOTIFY_PROTOCOL"

_OSC9_PREFIX = "\x1b]9;"
_OSC777_PREFIX = "\x1b]777;notify;"
_OSC99_PREFIX = "\x1b]99;"
_STRING_TERMINATOR = "\x1b\\"

_TMUX_PREFIX = "\x1bPtmux;"
_SCREEN_PREFIX = "\x1bP"

# Terminal identity hints, checked against TERM and TERM_PROGRAM.  These
# mirror OpenTUI's detectNotificationProtocol().
_OSC99_HINTS = ("kitty", "foot")
_OSC777_HINTS = (
    "ghostty",
    "wezterm",
    "warp",
    "hterm",
    "blink",
    "contour",
    "vte",
    "gnome",
    "tilix",
    "terminator",
    "xfce",
    "urxvt",
    "rxvt",
    "windows terminal",
    "windows_terminal",
)
_OSC9_HINTS = ("iterm", "apple_terminal", "terminal.app", "conemu")

_PRIORITY = {"osc9": 1, "osc777": 2, "osc99": 3}
_DISABLE_VALUES = frozenset(("0", "false", "off", "none"))

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|][^\x1b\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_NEWLINE_RUN_RE = re.compile(r"[ \t]*[\r\n]+[ \t]*")
_TERM_FEATURE_RE = re.compile(r"[A-Z][a-z]*")

_notification_id = itertools.count(1)
_tty: Optional[IO[bytes]] = None


def notify(
    message: str,
    title: Optional[str] = None,
    *,
    protocol: Optional[str] = None,
    file: Optional[IO[bytes]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Ask the terminal to show a desktop notification.

    :param message: notification body (required).
    :param title: optional title, shown as ``title: message`` on OSC 9 and
        as a separate field on OSC 777/99.
    :param protocol: force ``"osc9"``, ``"osc777"`` or ``"osc99"`` instead
        of detecting one.
    :param file: binary stream to write to instead of the controlling
        terminal (mostly useful for tests).
    :param env: environment mapping to detect from (defaults to
        :data:`os.environ`).
    :returns: ``True`` if the sequence was written, ``False`` if no protocol
        was available, the message was empty, or no terminal was reachable.
    """
    environment = os.environ if env is None else env

    if protocol is not None:
        chosen = str(protocol).strip().lower()
        if chosen not in SUPPORTED_PROTOCOLS:
            raise ValueError(
                "protocol must be one of %s, not %r" % (SUPPORTED_PROTOCOLS, protocol)
            )
    else:
        chosen = detect_protocol(environment)
        if chosen is None:
            return False

    if not _clean(str(message)):
        return False

    sequence = _encode(chosen, str(message), "" if title is None else str(title))
    return _write(_wrap(sequence, environment), file)


def detect_protocol(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Return the protocol :func:`notify` would use, or ``None``.

    Detection mirrors OpenTUI: heuristics from ``TERM``, ``TERM_PROGRAM``,
    ``TERM_FEATURES`` and ``WT_SESSION``, with the highest protocol
    priority (osc99 > osc777 > osc9) winning. ``TERMNOTIFY_PROTOCOL``
    overrides everything.
    """
    environment = os.environ if env is None else env

    override = str(environment.get(ENV_OVERRIDE) or "").strip().lower()
    if override:
        if override in SUPPORTED_PROTOCOLS:
            return override
        if override in _DISABLE_VALUES:
            return None
        # "1"/"true"/"on" (or anything unknown) keeps automatic detection.

    if (
        "ZELLIJ" in environment
        or "ZELLIJ_SESSION_NAME" in environment
        or "ZELLIJ_PANE_ID" in environment
    ):
        # Zellij only forwards OSC 99 and enables it through a query
        # handshake OpenTUI performs but we do not. Stay silent unless the
        # caller passes an explicit protocol.
        return None

    best: Optional[str] = None
    for value in (environment.get("TERM") or "", environment.get("TERM_PROGRAM") or ""):
        candidate = _hint(value)
        if candidate is not None and (best is None or _PRIORITY[candidate] > _PRIORITY[best]):
            best = candidate

    if _has_term_feature(environment.get("TERM_FEATURES") or "", "No"):
        best = _prefer(best, "osc9")
    if environment.get("WT_SESSION"):
        best = _prefer(best, "osc777")
    if environment.get("VTE_VERSION"):
        # Not in OpenTUI's env heuristics, but VTE-based terminals
        # (GNOME Terminal, Tilix, ...) all understand OSC 777.
        best = _prefer(best, "osc777")

    return best


def _prefer(current: Optional[str], candidate: str) -> str:
    if current is None or _PRIORITY[candidate] > _PRIORITY[current]:
        return candidate
    return current


def _hint(value: str) -> Optional[str]:
    low = value.lower()
    for protocol, hints in (("osc99", _OSC99_HINTS), ("osc777", _OSC777_HINTS), ("osc9", _OSC9_HINTS)):
        if any(hint in low for hint in hints):
            return protocol
    return None


def _has_term_feature(features: str, name: str) -> bool:
    return any(match.group() == name for match in _TERM_FEATURE_RE.finditer(features))


def _clean(text: str, semicolons: bool = False) -> str:
    """Strip ANSI sequences and control characters from notification text.

    Mirrors the normalization OpenCode/OpenTUI apply before encoding:
    escape sequences are removed, newline runs collapse to a space and any
    remaining control character becomes a space. For OSC 777 semicolons are
    also replaced, since they would break the field layout.
    """
    text = _ANSI_RE.sub("", text)
    text = _NEWLINE_RUN_RE.sub(" ", text)
    cleaned = []
    for char in text:
        code = ord(char)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F or (semicolons and char == ";"):
            cleaned.append(" ")
        else:
            cleaned.append(char)
    return "".join(cleaned).strip()


def _osc99_payload(identifier: str, part: str, text: str, done: bool) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return "%si=%s:p=%s:e=1:d=%d;%s%s" % (
        _OSC99_PREFIX,
        identifier,
        part,
        int(done),
        encoded,
        _STRING_TERMINATOR,
    )


def _encode(protocol: str, message: str, title: str) -> str:
    title = _clean(title)
    if protocol == "osc9":
        prefix = title + ": " if title else ""
        return _OSC9_PREFIX + prefix + _clean(message) + _STRING_TERMINATOR

    if protocol == "osc777":
        body = _clean(message, semicolons=True)
        if title:
            fields = _clean(title, semicolons=True) + ";" + body
        else:
            # Same shape OpenTUI emits without a title.
            fields = body + ";"
        return _OSC777_PREFIX + fields + _STRING_TERMINATOR

    if protocol == "osc99":
        identifier = "termnotify-%d" % next(_notification_id)
        output = ""
        if title:
            output += _osc99_payload(identifier, "title", title, done=False)
        output += _osc99_payload(identifier, "body", _clean(message), done=True)
        return output

    raise ValueError("unknown protocol %r" % (protocol,))


def _wrap(sequence: str, env: Mapping[str, str]) -> str:
    """Wrap a sequence in tmux/screen DCS passthrough when needed."""
    term = env.get("TERM") or ""
    if env.get("TMUX") or term.startswith("tmux"):
        return _TMUX_PREFIX + sequence.replace("\x1b", "\x1b\x1b") + _STRING_TERMINATOR
    if env.get("STY"):
        return _SCREEN_PREFIX + sequence.replace("\x1b", "\x1b\x1b") + _STRING_TERMINATOR
    return sequence


def _open_tty() -> Optional[IO[bytes]]:
    global _tty
    for name in ("/dev/tty", "CONOUT$"):
        try:
            _tty = open(name, "wb", buffering=0)
            return _tty
        except OSError:
            continue
    for standard in (sys.stderr, sys.stdout):
        if standard is None:
            continue
        try:
            if not standard.isatty():
                continue
        except (AttributeError, ValueError):
            continue
        buffer = getattr(standard, "buffer", None)
        if buffer is not None:
            return buffer
    return None


def _write(data: str, file: Optional[IO[bytes]]) -> bool:
    payload = data.encode("utf-8")
    if file is not None:
        try:
            file.write(payload)
            flush = getattr(file, "flush", None)
            if flush is not None:
                flush()
        except (OSError, ValueError):
            return False
        return True

    global _tty
    for _ in range(2):
        stream = _tty or _open_tty()
        if stream is None:
            return False
        try:
            stream.write(payload)
            stream.flush()
            return True
        except (OSError, ValueError):
            _tty = None  # stale stream, try to reopen once
    return False
