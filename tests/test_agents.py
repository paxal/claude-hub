"""Liste des sous-agents (commande « agents » des scripts distants) et volet des sous-agents."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "src/claude_hub/remote"
sys.path.insert(0, str(ROOT / "src"))
SID = "11111111-2222-3333-4444-555555555555"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"


def msg(kind, t, content):
    return line(type=kind, isSidechain=True, timestamp=f"2026-10-07T09:00:{t:02d}.500Z",
                message={"role": kind, "content": content})


class TestAgents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        sub = proj / SID / "subagents"
        sub.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(msg("user", 0, "bonjour"))
        # parent : a lancé un sous-agent puis a rendu la main
        (sub / "agent-a1.jsonl").write_text(
            msg("user", 1, "consigne")
            + msg("assistant", 2, [{"type": "tool_use", "id": "toolu_n", "name": "Agent",
                                    "input": {"description": "fouille", "prompt": "x" * 500}}])
            + msg("user", 3, [{"type": "tool_result", "tool_use_id": "toolu_n", "content": "lancé"}])
            + msg("assistant", 4, [{"type": "text", "text": "J'attends 🤖 " + "é" * 400}]))
        (sub / "agent-a1.meta.json").write_text(json.dumps(
            {"agentType": "opus-high", "description": "Revue", "toolUseId": "toolu_p",
             "spawnDepth": 1, "requestShape": "background"}))
        # imbriqué, encore au travail (outil en attente)
        (sub / "agent-a2.jsonl").write_text(
            msg("user", 5, "cherche")
            + msg("assistant", 7, [{"type": "tool_use", "id": "toolu_b", "name": "Bash",
                                    "input": {"command": "grep -r x", "timeout": 5}}]))
        (sub / "agent-a2.meta.json").write_text(json.dumps(
            {"agentType": "Explore", "description": "fouille", "toolUseId": "toolu_n",
             "parentAgentId": "a1", "spawnDepth": 2}))
        # interrompu pendant un outil
        (sub / "agent-a3.jsonl").write_text(
            msg("user", 8, "attends")
            + msg("assistant", 9, [{"type": "tool_use", "id": "toolu_w", "name": "Bash",
                                    "input": {"command": "sleep 99"}}])
            + msg("user", 10, [{"type": "text", "text": "[Request interrupted by user for tool use]"}]))
        (sub / "agent-a3.meta.json").write_text(json.dumps({"description": "attente", "toolUseId": "toolu_w0"}))
        (sub / "agent-orphelin.jsonl").write_text(msg("user", 9, "sans meta"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_liste(self):
        res = self.run_helper("python3", "helper.py", "agents", SID)
        self.assertEqual(res["live"], None)
        a1, a2, a3 = res["agents"]
        self.assertEqual((a3["done"], a3["interrupted"]), (False, True))
        self.assertFalse(a2["interrupted"])
        self.assertEqual((a1["id"], a1["type"], a1["tool"], a1["parent"], a1["background"]),
                         ("a1", "opus-high", "toolu_p", None, True))
        self.assertTrue(a1["done"])
        self.assertEqual(a1["steps"], 1)
        self.assertEqual(len(a1["last"]["text"]), 300)
        self.assertEqual((a2["parent"], a2["depth"], a2["done"]), ("a1", 2, False))
        self.assertEqual(a2["last"], {"role": "tool", "name": "Bash", "input": {"command": "grep -r x"}})
        self.assertAlmostEqual(a2["end"] - a2["start"], 2.0)

    def test_python_et_node(self):
        for cmd in (("agents", SID), ("history", SID, "50")):
            self.assertEqual(self.run_helper("python3", "helper.py", *cmd),
                             self.run_helper("node", "helper.js", *cmd), cmd[0])

    def test_historique(self):
        res = self.run_helper("python3", "helper.py", "history", SID, "50")
        self.assertEqual([a["id"] for a in res["agents"]], ["a1", "a2", "a3"])

    def test_inconnue(self):
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            self.assertEqual(self.run_helper(interp, script, "agents", "nope"),
                             {"live": None, "agents": []})


class TestVolet(unittest.TestCase):
    def test_arbre(self):
        from claude_hub.agentsview import tree
        agents = [{"id": "a", "parent": None}, {"id": "b", "parent": None},
                  {"id": "c", "parent": "a"}, {"id": "d", "parent": "c"}, {"id": "e", "parent": "x"}]
        self.assertEqual([a["id"] for a in tree(agents)], ["a", "c", "d", "b", "e"])

    def test_etat(self):
        from claude_hub.agentsview import agent_state, duration
        self.assertEqual(agent_state({"done": True}, False), "done")
        self.assertEqual(agent_state({"interrupted": True}, True), "stopped")
        self.assertEqual(agent_state({"end": 1000}, True, now=99999), "running")
        self.assertEqual(agent_state({"end": 1000}, False, now=1100), "running")
        self.assertEqual(agent_state({"end": 1000}, False, now=99999), "idle")
        self.assertEqual((duration(42), duration(185), duration(3725)), ("42 s", "3 min", "1 h 02"))


if __name__ == "__main__":
    unittest.main()
