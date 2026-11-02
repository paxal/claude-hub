"""Points de copie d'une conversation (uuid des messages de Claude, parent de ceux de l'utilisateur)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"


class TestCopie(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        sub = proj / SID / "subagents"
        sub.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", uuid="u1", parentUuid="p0", message={"role": "user", "content": "bonjour"})
            + line(type="assistant", uuid="a1", parentUuid="u1", message={"content": [
                {"type": "text", "text": "salut"}]})
            + line(type="assistant", uuid="a2", parentUuid="a1", message={"content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]})
            + line(type="user", uuid="u2", message={"role": "user", "content": "sans parent"}))
        (sub / "agent-x.jsonl").write_text(
            line(type="user", isSidechain=True, uuid="s1", parentUuid="s0",
                 message={"role": "user", "content": "consigne"})
            + line(type="assistant", isSidechain=True, uuid="s2", parentUuid="s1",
                   message={"content": [{"type": "text", "text": "fait"}]}))
        (sub / "agent-x.meta.json").write_text(json.dumps({"toolUseId": "toolu_x"}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_history(self):
        items = self.run_helper("python3", "helper.py", "history", SID, "50")["items"]
        self.assertEqual(items[0], {"role": "user", "text": "bonjour", "images": [], "parent": "p0"})
        self.assertEqual(items[1], {"role": "assistant", "text": "salut", "uuid": "a1"})
        self.assertNotIn("uuid", items[2])  # appel d'outil : pas de point de copie
        self.assertNotIn("parent", items[3])
        self.assertEqual(items, self.run_helper("node", "helper.js", "history", SID, "50")["items"])

    def test_sous_agent(self):
        # la transcription d'un sous-agent n'est pas celle de la conversation : pas de copie
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            res = self.run_helper(interp, script, "agent", SID, "toolu_x", "50")
            self.assertEqual([it["text"] for it in res["items"]], ["consigne", "fait"], interp)
            for it in res["items"]:
                self.assertNotIn("uuid", it, interp)
                self.assertNotIn("parent", it, interp)


if __name__ == "__main__":
    unittest.main()
