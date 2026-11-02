"""Heure des éléments de l'historique (« ts », epoch à la seconde), affichée à droite du fil."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.cursor import bubble_time  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"
T = 1793613600  # 2026-11-02T10:00:00Z


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


class TestHeures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", timestamp="2026-11-02T10:00:00.999Z",
                 message={"role": "user", "content": "bonjour"})
            # un enregistrement, deux éléments : même heure
            + line(type="assistant", timestamp="2026-11-02T10:01:05.500Z", message={"content": [
                {"type": "text", "text": "je regarde"},
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]})
            + line(type="assistant", isSidechain=True, timestamp="2026-11-02T10:02:00.000Z",
                   message={"content": [{"type": "text", "text": "sous-agent"}]})
            + line(type="assistant", message={"content": [{"type": "text", "text": "sans date"}]})
            + line(type="assistant", timestamp="pas une date",
                   message={"content": [{"type": "text", "text": "date illisible"}]})
            + line(type="assistant", timestamp="2026-11-03T08:30:00Z",
                   message={"content": [{"type": "text", "text": "le lendemain"}]}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        out = subprocess.run([interp, str(REMOTE / script), *args], capture_output=True, text=True,
                             env={"HOME": self.home, "PATH": "/usr/bin:/bin"}, check=True).stdout
        return json.loads(out)

    def test_heures(self):
        items = self.run_helper("python3", "helper.py", "history", SID, "50")["items"]
        self.assertEqual([(it.get("text") or it.get("name"), it.get("ts")) for it in items], [
            ("bonjour", T), ("je regarde", T + 65), ("Bash", T + 65), ("sans date", None),
            ("date illisible", None), ("le lendemain", T + 86400 - 5400)])

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        self.assertEqual(self.run_helper("python3", "helper.py", "history", SID, "50"),
                         self.run_helper("node", "helper.js", "history", SID, "50"))

    def test_bulle_cursor(self):
        self.assertEqual(bubble_time({"createdAt": "2026-11-02T10:00:00.557Z"}), T)
        self.assertIsNone(bubble_time({}))
        self.assertIsNone(bubble_time({"createdAt": "hier"}))
        self.assertIsNone(bubble_time({"createdAt": 12}))


if __name__ == "__main__":
    unittest.main()
