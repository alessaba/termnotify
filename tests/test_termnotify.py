import base64
import io
import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import termnotify  # noqa: E402
from termnotify import SUPPORTED_PROTOCOLS, detect_protocol, notify  # noqa: E402
from termnotify import __main__ as cli  # noqa: E402

ST = "\x1b\\"


def sent(message, title=None, protocol="osc9", env=None):
    buffer = io.BytesIO()
    ok = notify(message, title, protocol=protocol, file=buffer, env={} if env is None else env)
    return ok, buffer.getvalue().decode("utf-8")


class DetectProtocolTests(unittest.TestCase):
    def test_environment_heuristics(self):
        cases = [
            ({"TERM": "xterm-ghostty", "TERM_PROGRAM": "ghostty"}, "osc777"),
            ({"TERM": "xterm-kitty"}, "osc99"),
            ({"TERM": "foot-extra"}, "osc99"),
            ({"TERM": "xterm-256color", "TERM_PROGRAM": "iTerm.app"}, "osc9"),
            ({"TERM": "xterm-256color", "TERM_PROGRAM": "Apple_Terminal"}, "osc9"),
            ({"TERM": "xterm-256color", "TERM_PROGRAM": "WezTerm"}, "osc777"),
            ({"TERM": "xterm-256color", "TERM_PROGRAM": "vscode"}, None),
            ({"TERM": "xterm-256color"}, None),
            ({}, None),
            # Highest priority wins.
            ({"TERM": "xterm-kitty", "TERM_PROGRAM": "ghostty"}, "osc99"),
            ({"TERM": "xterm-256color", "TERM_PROGRAM": "gnome-terminal", "TERM_FEATURES": "TFooNoBar"}, "osc777"),
            ({"TERM": "xterm-256color", "TERM_FEATURES": "TFooNoBar"}, "osc9"),
            ({"TERM": "xterm-256color", "WT_SESSION": "some-guid"}, "osc777"),
            ({"TERM": "xterm-256color", "VTE_VERSION": "6800"}, "osc777"),
            # Overrides.
            ({"TERM": "xterm-kitty", termnotify.ENV_OVERRIDE: "osc9"}, "osc9"),
            ({"TERM": "xterm-kitty", termnotify.ENV_OVERRIDE: "OSC777"}, "osc777"),
            ({"TERM": "xterm-kitty", termnotify.ENV_OVERRIDE: "off"}, None),
            ({"TERM": "xterm-kitty", termnotify.ENV_OVERRIDE: "true"}, "osc99"),
            # Zellij: no heuristics, override still respected.
            ({"TERM": "xterm-kitty", "ZELLIJ": "1"}, None),
            ({"TERM": "xterm-kitty", "ZELLIJ": "1", termnotify.ENV_OVERRIDE: "osc99"}, "osc99"),
        ]
        for env, expected in cases:
            with self.subTest(env=env):
                self.assertEqual(detect_protocol(env), expected)

    def test_defaults_to_os_environ(self):
        self.assertIn(detect_protocol(), (None,) + SUPPORTED_PROTOCOLS)


class EncodeTests(unittest.TestCase):
    def test_osc9_with_title(self):
        ok, data = sent("Build finished", "CI")
        self.assertTrue(ok)
        self.assertEqual(data, "\x1b]9;CI: Build finished" + ST)

    def test_osc9_without_title(self):
        _, data = sent("Build finished")
        self.assertEqual(data, "\x1b]9;Build finished" + ST)

    def test_osc777_with_title(self):
        _, data = sent("Build finished", "CI", protocol="osc777")
        self.assertEqual(data, "\x1b]777;notify;CI;Build finished" + ST)

    def test_osc777_without_title(self):
        _, data = sent("Build finished", protocol="osc777")
        self.assertEqual(data, "\x1b]777;notify;Build finished;" + ST)

    def test_osc777_replaces_semicolons(self):
        _, data = sent("a;b", "t;t", protocol="osc777")
        self.assertEqual(data, "\x1b]777;notify;t t;a b" + ST)

    def test_osc99_with_title(self):
        _, data = sent("Build finished", "CI", protocol="osc99")
        identifier = re.search(r"i=(termnotify-\d+)", data).group(1)
        title = base64.b64encode(b"CI").decode()
        body = base64.b64encode(b"Build finished").decode()
        self.assertEqual(data.count(identifier), 2)
        self.assertEqual(
            data,
            "\x1b]99;i=%s:p=title:e=1:d=0;%s%s"
            "\x1b]99;i=%s:p=body:e=1:d=1;%s%s" % (identifier, title, ST, identifier, body, ST),
        )

    def test_osc99_without_title(self):
        _, data = sent("Build finished", protocol="osc99")
        body = base64.b64encode(b"Build finished").decode()
        self.assertNotIn("p=title", data)
        self.assertTrue(data.endswith(":p=body:e=1:d=1;%s%s" % (body, ST)))

    def test_osc99_ids_are_unique(self):
        _, first = sent("one", protocol="osc99")
        _, second = sent("two", protocol="osc99")
        self.assertNotEqual(first, second)


class SanitizeTests(unittest.TestCase):
    def test_strips_ansi_and_control_characters(self):
        _, data = sent("hello\x1b[31m\nworld\x00", protocol="osc9")
        self.assertEqual(data, "\x1b]9;hello world" + ST)

    def test_strips_ansi_from_title(self):
        _, data = sent("body", "\x1b[1mbold\x1b[0m", protocol="osc9")
        self.assertEqual(data, "\x1b]9;bold: body" + ST)

    def test_empty_message_is_not_sent(self):
        buffer = io.BytesIO()
        ok = notify(" \n\x1b[31m ", file=buffer, env={}, protocol="osc9")
        self.assertFalse(ok)
        self.assertEqual(buffer.getvalue(), b"")


class WrapTests(unittest.TestCase):
    def test_tmux_passthrough(self):
        _, data = sent("hi", env={"TMUX": "/tmp/tmux-501/default,1,0"})
        inner = "\x1b]9;hi" + ST
        self.assertEqual(data, "\x1bPtmux;" + inner.replace("\x1b", "\x1b\x1b") + ST)

    def test_tmux_passthrough_via_term(self):
        _, data = sent("hi", env={"TERM": "tmux-256color"})
        self.assertTrue(data.startswith("\x1bPtmux;"))

    def test_screen_passthrough(self):
        _, data = sent("hi", env={"STY": "12345.pts-0.host"})
        inner = "\x1b]9;hi" + ST
        self.assertEqual(data, "\x1bP" + inner.replace("\x1b", "\x1b\x1b") + ST)

    def test_no_wrapper_on_plain_terminal(self):
        _, data = sent("hi", env={"TERM": "xterm-ghostty"})
        self.assertEqual(data, "\x1b]9;hi" + ST)


class NotifyTests(unittest.TestCase):
    def test_invalid_protocol(self):
        with self.assertRaises(ValueError):
            notify("hi", protocol="osc123")

    def test_returns_false_when_no_protocol(self):
        buffer = io.BytesIO()
        self.assertFalse(notify("hi", file=buffer, env={"TERM": "dumb"}))

    def test_explicit_protocol_bypasses_detection(self):
        buffer = io.BytesIO()
        self.assertTrue(notify("hi", protocol="osc9", file=buffer, env={"TERM": "dumb"}))
        self.assertEqual(buffer.getvalue().decode(), "\x1b]9;hi" + ST)

    def test_file_write_failure_is_reported(self):
        class Broken:
            def write(self, _data):
                raise OSError("nope")

        self.assertFalse(notify("hi", protocol="osc9", file=Broken(), env={}))


class CliTests(unittest.TestCase):
    def test_prints_detected_protocol(self):
        import contextlib

        output = io.StringIO()
        with unittest.mock.patch.object(cli, "detect_protocol", return_value="osc777"):
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(["--detect"]), 0)
        self.assertEqual(output.getvalue().strip(), "osc777")

    def test_success_and_failure_exit_codes(self):
        with unittest.mock.patch.object(cli, "notify", return_value=True) as mocked:
            self.assertEqual(cli.main(["hello", "-t", "T", "-p", "osc9"]), 0)
            mocked.assert_called_once_with("hello", "T", protocol="osc9")
        with unittest.mock.patch.object(cli, "notify", return_value=False):
            self.assertEqual(cli.main(["hello"]), 1)


if __name__ == "__main__":
    unittest.main()
