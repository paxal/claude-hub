"""Tests des sorties d'outils (commande `result` des scripts distants, lignes dépliables)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.toolcall import RESULT_LIMIT, result_text  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"
LONG = "é😀x\n" * (RESULT_LIMIT // 3)  # plus long que la limite, hors BMP compris


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


def result(tid, content, **kw):
    return line(type="user", message={"role": "user", "content": [
        dict(type="tool_result", tool_use_id=tid, content=content, **kw)]})


class TestResult(unittest.TestCase):
    """Faux ~/.claude : appels d'outils de la conversation et d'un sous-agent, avec leurs sorties."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        sub = proj / SID / "subagents"
        sub.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="assistant", message={"content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {"file_path": "/a.png"}},
                {"type": "tool_use", "id": "toolu_3", "name": "Bash", "input": {"command": "false"}},
                {"type": "tool_use", "id": "toolu_4", "name": "Bash", "input": {"command": "yes"}}]})
            # le texte d'un message peut citer un id : seul le vrai bloc tool_result compte
            + line(type="user", message={"role": "user", "content": 'voir "tool_use_id":"toolu_1"'})
            + result("toolu_1", "a.txt\nb.txt\n")
            + result("toolu_2", [{"type": "text", "text": "Image :"},
                                 {"type": "image", "source": {"type": "base64", "data": "AA=="}}])
            + result("toolu_3", "Exit code 1", is_error=True)
            + result("toolu_4", LONG))
        (sub / "agent-a1.jsonl").write_text(
            line(type="assistant", isSidechain=True, message={"content": [
                {"type": "tool_use", "id": "toolu_S", "name": "Grep", "input": {"pattern": "x"}}]})
            + line(type="user", isSidechain=True, message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_S", "content": "x.py:1:x"}]}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True, env={"HOME": self.home,
                                                                             "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_result(self):
        res = self.run_helper("python3", "helper.py", "result", SID, "toolu_1")
        self.assertEqual(res, {"found": True, "error": False, "length": 12, "text": "a.txt\nb.txt\n"})
        self.assertEqual(self.run_helper("python3", "helper.py", "result", SID, "toolu_2")["text"],
                         "Image :\n[image]")
        self.assertTrue(self.run_helper("python3", "helper.py", "result", SID, "toolu_3")["error"])

    def test_tronquee(self):
        res = self.run_helper("python3", "helper.py", "result", SID, "toolu_4")
        self.assertEqual(res["length"], len(LONG))
        self.assertEqual(res["text"], LONG[:RESULT_LIMIT])

    def test_sous_agent(self):
        self.assertEqual(self.run_helper("python3", "helper.py", "result", SID, "toolu_S")["text"],
                         "x.py:1:x")

    def test_introuvable(self):
        self.assertEqual(self.run_helper("python3", "helper.py", "result", SID, "toolu_Z"),
                         {"found": False})
        self.assertEqual(self.run_helper("python3", "helper.py", "result", "inconnue", "toolu_1"),
                         {"found": False})

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        for tid in ("toolu_1", "toolu_2", "toolu_3", "toolu_4", "toolu_S", "toolu_Z"):
            args = ["result", SID, tid]
            self.assertEqual(self.run_helper("python3", "helper.py", *args),
                             self.run_helper("node", "helper.js", *args), tid)


class TestTexte(unittest.TestCase):
    def test_result_text(self):
        self.assertEqual(result_text("ok"), "ok")
        self.assertEqual(result_text([{"type": "text", "text": "a"}, {"type": "image"}, "?"]),
                         "a\n[image]")
        self.assertEqual(result_text(None), "")


if __name__ == "__main__":
    unittest.main()
