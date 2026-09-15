"""Command line interface: ``termnotify 'Build finished' -t CI``."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import SUPPORTED_PROTOCOLS, __version__, detect_protocol, notify


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="termnotify",
        description="Show a desktop notification through the terminal (OSC 9/777/99).",
    )
    parser.add_argument("message", nargs="?", help="notification body")
    parser.add_argument("-t", "--title", help="notification title")
    parser.add_argument(
        "-p",
        "--protocol",
        choices=SUPPORTED_PROTOCOLS,
        help="force a protocol instead of detecting one",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="print the detected protocol and exit",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = parser.parse_args(argv)

    if args.detect:
        print(detect_protocol() or "none")
        return 0

    if not args.message:
        parser.error("the following arguments are required: message")

    if not notify(args.message, args.title, protocol=args.protocol):
        print(
            "termnotify: no notification protocol available "
            "(set TERMNOTIFY_PROTOCOL to override)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
