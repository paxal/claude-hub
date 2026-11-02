"""Tests du tri de la liste : seuls un message envoyé, une réponse finie ou une question
font remonter une conversation, pas les étapes d'une réponse (outils, résultats) ni une
compaction."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


def user(text, ts, **kw):
    return line(type="user", message={"role": "user", "content": text}, cwd="/tmp/x",
                timestamp=f"2026-10-06T{ts}.000Z", **kw)


def assistant(content, stop, ts, **kw):
    return line(type="assistant", message={"content": content, "stop_reason": stop},
                timestamp=f"2026-10-06T{ts}.000Z", **kw)


def bash(ts):
    return assistant([{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}], "tool_use", ts)


def result(ts):
    return user([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], ts)


COMPACTED = "11111111-0000-0000-0000-000000000004"
SESSIONS = {
    # réponse en cours : on garde l'heure du message envoyé
    "11111111-0000-0000-0000-000000000001":
        (user("Bonjour", "08:00:00") + assistant([{"type": "text", "text": "Je regarde"}], "tool_use",
                                                 "08:00:01")
         + bash("08:00:02") + result("08:00:03") + bash("08:05:00"), 8 * 3600),
    # réponse finie : son heure
    "11111111-0000-0000-0000-000000000002":
        (user("Bonjour", "08:00:00") + bash("08:00:02") + result("08:00:03")
         + assistant([{"type": "text", "text": "Fini"}], "end_turn", "08:00:10")
         + user("méta", "09:00:00", isMeta=True), 8 * 3600 + 10),
    # question posée : son heure
    "11111111-0000-0000-0000-000000000003":
        (user("Bonjour", "08:00:00") + bash("08:00:02") + result("08:00:03")
         + assistant([{"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {}}],
                     "tool_use", "08:00:20") + result("08:00:30"), 8 * 3600 + 20),
    # compaction pendant l'inactivité : le résumé n'est pas un message envoyé
    COMPACTED:
        (user("Bonjour", "08:00:00") + assistant([{"type": "text", "text": "Fini"}], "end_turn", "08:00:40")
         + line(type="system", subtype="compact_boundary", content="Conversation compacted",
                timestamp="2026-10-06T09:00:00.000Z")
         + user("This session is being continued from a previous conversation…", "09:00:01",
                isCompactSummary=True, isVisibleInTranscriptOnly=True), 8 * 3600 + 40),
}


class TestTri(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        for sid, (text, _) in SESSIONS.items():
            (proj / f"{sid}.jsonl").write_text(text)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *(args or ["list"])], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_date_du_dernier_arret(self):
        for s in self.run_helper("python3", "helper.py")["sessions"]:
            self.assertAlmostEqual(s["mtime"] % 86400, SESSIONS[s["id"]][1], msg=s["id"])

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        self.assertEqual(self.run_helper("python3", "helper.py"), self.run_helper("node", "helper.js"))


    def test_resume_de_compaction(self):
        items = self.run_helper("python3", "helper.py", "history", COMPACTED, "50")["items"]
        self.assertEqual([i["role"] for i in items], ["user", "assistant", "compact"])
        self.assertTrue(items[-1]["text"].startswith("This session is being continued"))
        self.assertEqual(self.run_helper("python3", "helper.py", "search", "continued", "5"), {})

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_compaction_python_et_node_identiques(self):
        for args in (("history", COMPACTED, "50"), ("search", "continued", "5")):
            self.assertEqual(self.run_helper("python3", "helper.py", *args),
                             self.run_helper("node", "helper.js", *args), args)


if __name__ == "__main__":
    unittest.main()
