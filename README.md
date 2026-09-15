# termnotify

Desktop notifications **through your terminal**, the way OpenCode does it.
Zero dependencies, pure Python, no permission prompts, no code signing.

```python
from termnotify import notify

notify("Build finished", title="CI")
```

```console
$ termnotify "Build finished" -t CI
$ termnotify --detect
osc777
```

## Why this exists

OpenCode does not use `osascript`, `terminal-notifier` or pyobjc for its
notifications. Its TUI writes an *OSC escape sequence* to the terminal and
the terminal (Ghostty, iTerm2, kitty, WezTerm, ...) turns it into a native
notification. Compared to the system-API route that the existing Python
libraries take, that means:

- no code signing or app bundle required (see the macOS notes in
  [`desktop-notifier`](https://github.com/samschott/desktop-notifier))
- no notification/Automation permission prompts
- works over SSH: the notification appears on the machine running the
  terminal, not on the server
- nothing to install beyond Python itself

As far as we could tell there is no Python library that does this, so this
is one. It mirrors the detection heuristics and payload formats of the
OpenTUI terminal backend used by OpenCode:
[`packages/native/src/terminal.zig`](https://github.com/anomalyco/opentui/blob/main/packages/native/src/terminal.zig)
(called from
[`packages/tui/src/feature-plugins/system/notifications.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/tui/src/feature-plugins/system/notifications.ts)
in OpenCode).

## Protocols

| Protocol | Sequence | Terminals |
| --- | --- | --- |
| OSC 99 | `ESC ] 99 ; ...` with base64 title/body | kitty, foot |
| OSC 777 | `ESC ] 777 ; notify ; title ; body` | Ghostty, WezTerm, Warp, hterm/Blink, Contour, VTE (GNOME Terminal, Tilix, Terminator, ...), rxvt, Windows Terminal |
| OSC 9 | `ESC ] 9 ; title: body` | iTerm2, Apple Terminal, ConEmu |

The protocol is detected from the environment (`TERM`, `TERM_PROGRAM`,
`TERM_FEATURES`, `WT_SESSION`, plus `VTE_VERSION` as a VTE-family hint);
when several match, the highest protocol priority (osc99 > osc777 > osc9)
wins. No interactive capability query is performed, so detection is
instant and never blocks or touches the input stream.

Verified caveats (Sep 2026):

- **Windows Terminal** implements OSC 777 behind
  `compatibility.allowOSC777` (default `false`) and suppresses the toast
  while focused. OSC 9 there is the ConEmu subcommand family, not an
  iTerm2-style toast.
- **VTE-based terminals** (GNOME Terminal, Tilix, Terminator, Xfce, ...)
  expose OSC 777 as a *legacy* API
  (`vte_terminal_set_enable_legacy_osc777`), so whether it works depends
  on the app and version; VTE ignores OSC 9.
- **OSC 9 is overloaded**: a body starting with `N;` is a ConEmu
  subcommand (e.g. `9;4` progress), so don't start messages with digits
  and a semicolon.
- **Ghostty** handles OSC 9 and OSC 777; OSC 99 is implemented in its
  parser (with query replies) but is newer than its docs.
- **kitty** also understands the legacy OSC 9, and OSC 777 since 0.24.0.
- **Terminal.app, VS Code, Alacritty and Konsole** have no native OSC
  notification support (use `osascript` or an extension there).

Force a protocol when detection fails:

```console
$ TERMNOTIFY_PROTOCOL=osc9 termnotify "hello"
```

or per call: `notify("hello", protocol="osc99")`. Setting the variable to
`0`, `false`, `off` or `none` disables notifications entirely.

### Multiplexers

- **tmux**: sequences are wrapped in DCS passthrough. tmux needs
  `set -g allow-passthrough on`.
- **GNU Screen** (`STY` set): sequences are wrapped in a Screen DCS.
- **Zellij**: detection is disabled (Zellij only forwards OSC 99 and needs
  a query handshake), so pass `protocol="osc99"` explicitly if you know
  the terminal forwards notifications.

## API

### `notify(message, title=None, *, protocol=None, file=None, env=None) -> bool`

Writes the notification sequence. Returns `True` if the bytes were written
to a terminal. That means the terminal was *asked* to notify, not that a
banner appeared: the window may be focused, notifications may be muted in
the terminal or the OS, etc. Returns `False` when no protocol was
detected, the message is empty, or no terminal is reachable.

Output goes to the controlling terminal (`/dev/tty`) rather than stdout, so
redirections and pipes don't swallow notifications and program output is
never polluted. Pass a binary `file` to override.

### `detect_protocol(env=None) -> str | None`

Returns the protocol `notify()` would use, or `None`.

## Installation

```console
$ pip install -e .
# or simply copy src/termnotify into your project
```

Requires Python 3.9+.

## Limitations

- Focus gating is up to the terminal. On macOS, Ghostty only presents a
  banner when its window is not focused; while focused, the notification is
  delivered silently to Notification Center. (OpenCode appears to work
  around this by only sending notifications when its TUI is blurred -- the
  terminal applies the same rule regardless.) To test, switch to another
  app right after triggering a notification.
- No sound. OpenCode plays sounds through its own audio engine; that is not
  part of the terminal notification protocols.
- Environment-based detection only. If your terminal is not recognized,
  set `TERMNOTIFY_PROTOCOL`.
- Requires a terminal. For GUI apps without one, use a system API library
  such as `desktop-notifier`.

## Development

```console
$ python -m unittest discover -s tests -v
$ PYTHONPATH=src python -m termnotify --detect
```

## Releasing

Pushing a `v*` tag publishes to PyPI through GitHub Trusted Publishing
(`.github/workflows/publish.yml`). Configure the trusted publisher for
`alessaba/termnotify` (workflow `publish.yml`, environment `pypi`) once in
the PyPI project settings, then:

```console
$ git tag v0.1.0 && git push origin v0.1.0
```
