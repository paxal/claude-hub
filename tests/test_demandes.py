"""Tests des sous-agents (commande `agent` des scripts distants) et des réponses aux demandes."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.permissions import allow, control_response, deny, plan_allow, plan_deny, session_only  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


class TestAgent(unittest.TestCase):
    """Faux ~/.claude : une conversation qui lance un sous-agent, avec sa transcription."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        sub = proj / SID / "subagents"
        sub.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", message={"role": "user", "content": "Fais un plan"}, cwd="/tmp/x")
            + line(type="assistant", message={"model": "claude-haiku-4-5", "content": [
                {"type": "tool_use", "id": "toolu_A", "name": "Agent",
                 "input": {"subagent_type": "Explore", "description": "Lister", "prompt": "Liste"}}]})
            + line(type="assistant", message={"content": [
                {"type": "tool_use", "id": "toolu_P", "name": "ExitPlanMode",
                 "input": {"plan": "# Plan\n1. a", "planFilePath": "/p.md"}}]}))
        (sub / "agent-a1.meta.json").write_text(json.dumps(
            {"agentType": "Explore", "description": "Lister", "toolUseId": "toolu_A"}))
        (sub / "agent-a1.jsonl").write_text(
            line(type="user", isSidechain=True, message={"role": "user", "content": "Liste"})
            + line(type="assistant", isSidechain=True, message={"model": "claude-haiku-4-5", "content": [
                {"type": "tool_use", "id": "toolu_B", "name": "Bash", "input": {"command": "ls"}},
                {"type": "text", "text": "Le dossier est vide."}]}))
        (sub / "agent-cassé.meta.json").write_text("{pas du json")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True, env={"HOME": self.home,
                                                                             "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_agent(self):
        res = self.run_helper("python3", "helper.py", "agent", SID, "toolu_A", "50")
        self.assertTrue(res["found"])
        self.assertEqual(res["meta"]["agentType"], "Explore")
        self.assertEqual([i["role"] for i in res["items"]], ["user", "tool", "assistant"])
        self.assertEqual(res["items"][1]["id"], "toolu_B")
        absent = self.run_helper("python3", "helper.py", "agent", SID, "toolu_Z", "50")
        self.assertFalse(absent["found"])

    def test_history_ids(self):
        res = self.run_helper("python3", "helper.py", "history", SID, "50")
        self.assertEqual([i.get("id") for i in res["items"]], [None, "toolu_A", "toolu_P"])
        self.assertEqual(res["items"][2]["input"]["plan"], "# Plan\n1. a")

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        for args in (["agent", SID, "toolu_A", "50"], ["agent", SID, "toolu_A", "1"],
                     ["agent", SID, "toolu_Z", "50"], ["agent", "inconnue", "toolu_A", "50"],
                     ["history", SID, "50"]):
            self.assertEqual(self.run_helper("python3", "helper.py", *args),
                             self.run_helper("node", "helper.js", *args), args)


class TestReponses(unittest.TestCase):
    def test_control_response(self):
        req = {"tool_name": "Bash", "input": {"command": "ls"}}
        msg = json.loads(control_response("r1", allow(req)))
        self.assertEqual(msg, {"type": "control_response", "response": {
            "subtype": "success", "request_id": "r1",
            "response": {"behavior": "allow", "updatedInput": {"command": "ls"}}}})
        self.assertEqual(deny("non"), {"behavior": "deny", "message": "non"})

    def test_session_seulement(self):
        """Claude ne doit jamais écrire dans ses réglages à cause d'une autorisation."""
        sugg = [{"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "allow",
                 "destination": "localSettings"},
                {"type": "setMode", "mode": "acceptEdits", "destination": "userSettings"},
                {"type": "addDirectories", "directories": ["/"], "destination": "projectSettings"}]
        out = session_only(sugg)
        self.assertEqual([s["type"] for s in out], ["addRules", "setMode"])
        self.assertTrue(all(s["destination"] == "session" for s in out))

    def test_plan(self):
        """Réponses au plan, qu'elles viennent de la carte ou du volet."""
        req = {"tool_name": "ExitPlanMode", "input": {"plan": "# P", "planFilePath": "/p.md"}}
        resp, summary, mode = plan_allow(req, "acceptEdits")
        self.assertEqual(resp, {"behavior": "allow", "updatedInput": req["input"],
                                "updatedPermissions": [{"type": "setMode", "mode": "acceptEdits",
                                                        "destination": "session"}]})
        self.assertEqual(mode, "acceptEdits")
        self.assertTrue(summary.startswith("✓ Plan validé"))
        resp, summary = plan_deny("Ajoute des tests")
        self.assertEqual(resp, deny("Ajoute des tests"))
        self.assertEqual(summary, "✗ Plan refusé : Ajoute des tests")


if __name__ == "__main__":
    unittest.main()
