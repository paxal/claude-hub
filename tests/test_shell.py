"""Tests des commandes « ! » : lancement (shell.py) et lecture dans les transcriptions."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.shell import SENT_LIMIT, Capture, claude_blocks, clean, shell_argv, shell_command  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


def user(content, **kw):
    return line(type="user", message={"role": "user", "content": content},
                timestamp="2026-11-02T10:00:00.000Z", **kw)


class TestShell(unittest.TestCase):
    def test_saisie(self):
        self.assertEqual(shell_command("! ls -l"), "ls -l")
        self.assertEqual(shell_command("!"), "")
        self.assertIsNone(shell_command("pas une commande !"))

    def test_nettoyage(self):
        self.assertEqual(clean("\x1b[1;31mrouge\x1b[0m\r\n"), "rouge\n")
        self.assertEqual(clean("10%\r50%\r100%\nfin"), "100%\nfin")

    def test_capture_coupee(self):
        c = Capture()
        c.add("a" * SENT_LIMIT)
        c.add("b" * 10)
        text = c.text()
        self.assertTrue(text.startswith("a" * (SENT_LIMIT // 2)))
        self.assertTrue(text.endswith("a" * (SENT_LIMIT // 2 - 10) + "b" * 10))
        self.assertIn("[… 10 caractères coupés …]", text)
        c = Capture()
        c.add("court\n")
        self.assertEqual(c.text(), "court\n")

    def test_blocs_pour_claude(self):
        self.assertEqual(claude_blocks("uname", "Linux\n", ""), [
            {"type": "text", "text": "<bash-input>uname</bash-input>"},
            {"type": "text", "text": "<bash-stdout>Linux</bash-stdout><bash-stderr></bash-stderr>"}])

    def test_lancement(self):
        # code de retour gardé, et rien de ~/.bash_logout (« clear ») dans la sortie
        argv, cwd = shell_argv("local", "/tmp", "echo a; echo b >&2; false")
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin",
                                                                              "TERM": "xterm"})
        self.assertEqual((p.stdout, p.stderr, p.returncode), ("a\n", "b\n", 1))
        argv, cwd = shell_argv("rpi4", "/le dossier", "ls")
        self.assertIsNone(cwd)
        line = subprocess.run(["bash", "-c", "printf %s " + argv[-1].split(" ", 2)[2]],
                              capture_output=True, text=True).stdout
        self.assertTrue(line.startswith("cd '/le dossier' || exit 1\nls\n"), line)


class TestTranscription(unittest.TestCase):
    """Faux ~/.claude : commande du terminal (deux messages), commandes envoyées par
    claude-hub avec un message ou seules, et pendant une réponse."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        out = "<bash-stdout>Linux</bash-stdout><bash-stderr></bash-stderr>"
        (proj / f"{SID}.jsonl").write_text(
            user("<bash-input> uname</bash-input>", cwd="/tmp/x", parentUuid="p0")
            + user(out, parentUuid="p1")
            + user([{"type": "text", "text": "<bash-input>false</bash-input>"},
                    {"type": "text", "text": "<bash-stdout></bash-stdout><bash-stderr>Exit code 1</bash-stderr>"},
                    {"type": "text", "text": "pourquoi ça échoue ?"}], parentUuid="p2")
            + line(type="assistant", message={"content": [{"type": "text", "text": "Parce que."}]}, uuid="a1")
            + user([{"type": "text", "text": "<bash-input>pwd</bash-input>"},
                    {"type": "text", "text": "<bash-stdout>/tmp/x</bash-stdout><bash-stderr></bash-stderr>"}],
                   parentUuid="p3")
            + line(type="attachment", attachment={"type": "queued_command", "prompt": [
                {"type": "text", "text": "<bash-input>date</bash-input>"},
                {"type": "text", "text": "<bash-stdout>lundi</bash-stdout><bash-stderr></bash-stderr>"},
                {"type": "text", "text": "et l'heure ?"}]}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        out = subprocess.run([interp, str(REMOTE / script), *args], capture_output=True, text=True,
                             env={"HOME": self.home, "PATH": "/usr/bin:/bin"}, check=True).stdout
        return json.loads(out)

    def test_historique(self):
        items = self.run_helper("python3", "helper.py", "history", SID, "50")["items"]
        self.assertEqual(items, [
            {"role": "bash", "command": "uname", "stdout": "Linux", "stderr": ""},
            {"role": "bash", "command": "false", "stdout": "", "stderr": "Exit code 1"},
            {"role": "user", "text": "pourquoi ça échoue ?", "images": [], "parent": "p2"},
            {"role": "assistant", "text": "Parce que.", "uuid": "a1"},
            {"role": "bash", "command": "pwd", "stdout": "/tmp/x", "stderr": ""},
            {"role": "bash", "command": "date", "stdout": "lundi", "stderr": ""},
            {"role": "user", "text": "et l'heure ?", "images": [], "queued": True}])

    def test_premier_message(self):
        s = self.run_helper("python3", "helper.py", "list")["sessions"][0]
        self.assertEqual(s["first"], "pourquoi ça échoue ?")

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        for args in (("history", SID, "50"), ("list",)):
            self.assertEqual(self.run_helper("python3", "helper.py", *args),
                             self.run_helper("node", "helper.js", *args), args)


if __name__ == "__main__":
    unittest.main()
