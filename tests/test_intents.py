"""Desktop URLs and CLI shortcuts only populate the corresponding UI."""
import configparser
import contextlib
import io
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))
from veliqen_link.intents import PAGES, parse_intent
from veliqen_link.__main__ import main


class IntentTests(unittest.TestCase):
    def test_phone_links_only_describe_prefilled_views(self):
        self.assertEqual(parse_intent("tel:+7(999)123-45-67"),
                         {"page": "calls", "number": "+79991234567"})
        self.assertEqual(parse_intent("sms:%2B79991234567?body=" + quote("Привет! 👋")),
                         {"page": "messages", "number": "+79991234567", "body": "Привет! 👋"})
        self.assertEqual(parse_intent("sms:+12345"), {"page": "messages", "number": "+12345", "body": ""})
        self.assertEqual(parse_intent("tel:+1.234.567"), {"page": "calls", "number": "+1234567"})

    def test_body_limit_is_exact_and_never_truncates(self):
        body = "🙂" * 4096
        self.assertEqual(parse_intent("sms:+123?body=" + quote(body))["body"], body)
        for body in ("a" * 4097, "🙂" * 4097):
            with self.subTest(length=len(body)), self.assertRaisesRegex(ValueError, "4096"):
                parse_intent("sms:+123?body=" + quote(body))

    def test_percent_decoding_happens_once(self):
        self.assertEqual(parse_intent("sms:+123?body=%2520%2B")["body"], "%20+")

    def test_control_characters_are_rejected_before_and_after_decoding(self):
        for control in ("\x00", "\t", "\n", "\r", "\x1f", "\x7f", "\x85"):
            for uri in ("sms:+123?body=a" + control, "sms:+123?body=a" + quote(control),
                        "tel:+123" + quote(control)):
                with self.subTest(uri=repr(uri)), self.assertRaises(ValueError):
                    parse_intent(uri)
        with self.assertRaises(ValueError):
            parse_intent("sms:+123?body=\ud800")

    def test_invalid_encoding_cannot_turn_into_replacement_text(self):
        for uri in ("sms:+123?body=%FF", "sms:+123?body=%E2%80", "sms:+123?body=%0G",
                    "sms:+123?body=%", "tel:%FF123"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                parse_intent(uri)

    def test_ambiguous_or_unsupported_phone_links_are_rejected(self):
        for uri in ("tel://+123", "tel:+123?dial=true", "tel:+123#456", "tel:*123%23",
                    "tel:+123;ext=4", "tel:++123", "tel:12+3", "sms:+123,+456",
                    "sms:+123?body=one&body=two", "sms:+123?send=true",
                    "sms:+123?body=a&x=1&y=2&z=3&q=4"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                parse_intent(uri)

    def test_only_known_internal_pages_and_no_actions_are_accepted(self):
        for page in PAGES:
            self.assertEqual(parse_intent("veliqen://" + page), {"page": page})
        for uri in ("veliqen://calls?number=123", "veliqen://call", "veliqen://calls/send", "veliqen://files#send"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                parse_intent(uri)
        self.assertIsNone(parse_intent("https://example.com"))
        self.assertIsNone(parse_intent("file:///tmp/document.pdf"))


class DesktopEntryTests(unittest.TestCase):
    def test_page_shortcuts_are_forwarded_as_uris_to_application_run(self):
        for page in PAGES:
            app = Mock()
            app.run.return_value = 0
            constructor = Mock(return_value=app)
            with patch.dict(sys.modules, {"veliqen_link.app": SimpleNamespace(LinkApplication=constructor)}):
                self.assertEqual(main(["--page", page]), 0)
            constructor.assert_called_once_with(background=False)
            app.run.assert_called_once_with(["veliqen-link", "veliqen://" + page])

    def test_existing_share_and_background_arguments_are_preserved(self):
        app = Mock()
        constructor = Mock(return_value=app)
        with patch.dict(sys.modules, {"veliqen_link.app": SimpleNamespace(LinkApplication=constructor)}):
            main(["--background", "--page", "files", "--share", "/tmp/a b.txt", "file:///tmp/c.txt"])
        constructor.assert_called_once_with(background=True)
        app.run.assert_called_once_with(["veliqen-link", "veliqen://files", "/tmp/a b.txt", "file:///tmp/c.txt"])

    def test_invalid_page_fails_before_constructing_the_application(self):
        constructor = Mock()
        with patch.dict(sys.modules, {"veliqen_link.app": SimpleNamespace(LinkApplication=constructor)}):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                main(["--page", "dial-now"])
        self.assertEqual(failure.exception.code, 2)
        constructor.assert_not_called()

    def test_desktop_actions_and_url_handlers_match_supported_entry_points(self):
        desktop = configparser.ConfigParser(interpolation=None)
        desktop.read(ROOT / "linux/veliqen-link.desktop")
        entry = desktop["Desktop Entry"]
        self.assertEqual(entry["Exec"], "veliqen-link %U")
        self.assertEqual(set(entry["MimeType"].strip(";").split(";")),
                         {"x-scheme-handler/tel", "x-scheme-handler/sms", "x-scheme-handler/veliqen"})
        for action, page in (("Calls", "calls"), ("Messages", "messages"), ("Screen", "screen"), ("Files", "files")):
            self.assertIn(action + ";", entry["Actions"])
            self.assertEqual(shlex.split(desktop["Desktop Action " + action]["Exec"]), ["veliqen-link", "--page", page])


if __name__ == "__main__":
    unittest.main()
