"""Tests du renommage : lecture du titre par les scripts distants et appel de /rename."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.rename import can_rename, rename_session  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "22222222-3333-4444-5555-666666666666"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


class TestTitre(unittest.TestCase):
    """Transcription telle que la laisse `claude -p --resume … "/rename Mon titre"`."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        caveat = ("<local-command-caveat>The command below was run directly in Claude Code"
                  "</local-command-caveat>")
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", message={"role": "user", "content": "Bonjour"}, cwd="/tmp/x",
                 timestamp="2026-10-06T08:00:00.000Z")
            + line(type="assistant", message={"content": [{"type": "text", "text": "OK"}]},
                   timestamp="2026-10-06T08:00:01.500Z")
            + line(type="custom-title", customTitle="Mon titre", sessionId=SID)
            + line(type="agent-name", agentName="Mon titre", sessionId=SID)
            + line(type="user", isMeta=True, message={"role": "user", "content": caveat},
                   timestamp="2026-10-06T09:00:00.000Z")
            + line(type="user", message={"role": "user", "content":
                   "<command-name>/rename</command-name>\n<command-args>Mon titre</command-args>"},
                   timestamp="2026-10-06T09:00:00.000Z"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True, env={"HOME": self.home,
                                                                             "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_titre_et_date(self):
        s, = self.run_helper("python3", "helper.py", "list")["sessions"]
        self.assertEqual(s["title"], "Mon titre")
        self.assertEqual(s["first"], "Bonjour")
        self.assertEqual(s["msgs"], 1)  # /rename n'est pas un message
        self.assertAlmostEqual(s["mtime"] % 86400, 8 * 3600 + 1.5)  # ni une activité

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        self.assertEqual(self.run_helper("python3", "helper.py", "list"),
                         self.run_helper("node", "helper.js", "list"))


class TestRenommer(unittest.TestCase):
    """rename_session avec un faux claude qui imite la sortie de `/rename` en -p."""

    def setUp(self):
        self.bin = tempfile.mkdtemp()
        fake = Path(self.bin, "claude")
        fake.write_text("#!/bin/sh\n"
                        'echo "$@" > "$(dirname "$0")/args"\n'
                        'case "$6" in\n'
                        '  "/rename") n=nom-choisi ;;\n'
                        '  "/rename echec") echo \'{"is_error":true,"result":"Session introuvable"}\'; exit 1 ;;\n'
                        '  *) n="${6#/rename }" ;;\n'
                        "esac\n"
                        'printf \'{"is_error":false,"num_turns":0,"result":"Session renamed to: %s"}\\n\' "$n"\n')
        fake.chmod(0o755)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = self.bin + os.pathsep + self.path

    def tearDown(self):
        os.environ["PATH"] = self.path
        shutil.rmtree(self.bin)

    def test_nom(self):
        s = {"id": SID, "host": "local", "cwd": self.bin}
        self.assertEqual(rename_session(s, "  Nouveau\n nom "), "Nouveau nom")
        self.assertEqual(Path(self.bin, "args").read_text().split()[:4],
                         ["-p", "--resume", SID, "--output-format"])

    def test_nom_choisi_par_claude(self):
        self.assertEqual(rename_session({"id": SID, "host": "local", "cwd": None}, ""), "nom-choisi")

    def test_echec(self):
        with self.assertRaisesRegex(RuntimeError, "introuvable"):
            rename_session({"id": SID, "host": "local", "cwd": "/n/existe/pas"}, "echec")

    def test_can_rename(self):
        self.assertTrue(can_rename({"id": SID, "host": "local"}))
        self.assertFalse(can_rename({"id": SID, "new": True}))
        self.assertFalse(can_rename({"id": SID, "source": "cursor"}))
        self.assertFalse(can_rename({"id": SID, "source": "claude-cloud"}))
        self.assertFalse(can_rename(None))


if __name__ == "__main__":
    unittest.main()
