"""Tests de la complétion des commandes « / » : saisie, filtrage et liste demandée à claude."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.commands import clean, fetch_commands, match_commands, short, typed_command  # noqa: E402

COMMANDS = clean([
    {"name": "compact", "description": "Free up context", "argumentHint": "<instructions>"},
    {"name": "usage", "description": "Show cost", "aliases": ["cost", "stats"]},
    {"name": "context", "description": "Show context usage"},
    {"name": "security-review", "description": "Review"},
    {"name": "__remote-workflow", "description": "interne"},
])

# Faux claude : attend la requête initialize et répond comme le vrai (une ligne par événement).
FAKE = """#!/usr/bin/env python3
import json, sys
print(json.dumps({"type": "system", "subtype": "hook_started"}), flush=True)
req = json.loads(sys.stdin.readline())
assert req["request"]["subtype"] == "initialize", req
print(json.dumps({"type": "control_response", "response": {"subtype": "success",
      "request_id": req["request_id"], "response": {"commands": [
      {"name": "compact", "description": "Free  up\\ncontext", "argumentHint": "<x>"},
      {"name": "__interne", "description": ""}]}}}), flush=True)
sys.stdin.read()  # comme claude : ne s'arrête pas tout seul
"""


class TestSaisie(unittest.TestCase):
    def test_debut_de_commande(self):
        self.assertEqual(typed_command("/"), "")
        self.assertEqual(typed_command("/com"), "com")

    def test_pas_une_commande(self):
        self.assertIsNone(typed_command("bonjour /com"))
        self.assertIsNone(typed_command("/compact résume"))  # arguments : plus de suggestions
        self.assertIsNone(typed_command("/compact\n"))
        self.assertIsNone(typed_command(""))


class TestFiltre(unittest.TestCase):
    def names(self, query):
        return [c["name"] for c in match_commands(COMMANDS, query)]

    def test_internes_ecartees(self):
        self.assertNotIn("__remote-workflow", [c["name"] for c in COMMANDS])

    def test_tout(self):
        self.assertEqual(self.names(""), ["compact", "context", "security-review", "usage"])

    def test_debut_puis_milieu(self):
        self.assertEqual(self.names("co"), ["compact", "context", "usage"])  # usage : alias cost
        self.assertEqual(self.names("view"), ["security-review"])

    def test_casse(self):
        self.assertEqual(self.names("COMP"), ["compact"])

    def test_aucune(self):
        self.assertEqual(self.names("zzz"), [])

    def test_description_courte(self):
        self.assertEqual(short("Première phrase. Seconde."), "Première phrase")
        self.assertEqual(len(short("x" * 300)), 120)


class TestListe(unittest.TestCase):
    def test_faux_claude(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "claude"
            fake.write_text(FAKE)
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            old = os.environ["PATH"]
            os.environ["PATH"] = f"{d}:{old}"
            try:
                cmds = fetch_commands("local", d)
            finally:
                os.environ["PATH"] = old
        self.assertEqual(cmds, [{"name": "compact", "hint": "<x>", "description": "Free up context",
                                 "aliases": []}])


if __name__ == "__main__":
    unittest.main()
