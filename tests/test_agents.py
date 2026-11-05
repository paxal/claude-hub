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
        # lancement du workflow wf_1 (résultat de l'outil Workflow), puis celui d'un workflow
        # sans agents, et une ligne qui cite « local_workflow » sans en être un
        launched = {"status": "async_launched", "taskId": "t1", "taskType": "local_workflow",
                    "workflowName": "review-changes", "runId": "wf_1", "summary": "Relit puis vérifie"}
        (proj / f"{SID}.jsonl").write_text(
            msg("user", 0, "bonjour")
            + line(type="user", isSidechain=False, timestamp="2026-10-07T09:00:10.000Z",
                   toolUseResult=launched,
                   message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_wf",
                                                         "content": "Workflow launched"}]})
            + line(type="user", isSidechain=False, toolUseResult=dict(launched, runId="wf_2", summary=""),
                   message={"role": "user", "content": "x"})
            + line(type="user", isSidechain=False, toolUseResult=dict(launched, runId="wf_1", workflowName="bis"),
                   message={"role": "user", "content": "doublon"})
            + line(type="assistant", message={"role": "assistant", "content": "local_workflow"}))
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
        # agent de workflow : fini par StructuredOutput (« toolEndsTurn »), puis le résultat
        # d'un appel parallèle s'écrit après
        wf = sub / "workflows" / "wf_1"
        wf.mkdir(parents=True)
        out = msg("user", 13, [{"type": "tool_result", "tool_use_id": "toolu_s",
                                "content": "Structured output provided successfully"}])
        (wf / "agent-a4.jsonl").write_text(
            msg("user", 11, "vérifie")
            + msg("assistant", 12, [{"type": "tool_use", "id": "toolu_s", "name": "StructuredOutput",
                                     "input": {"ok": True}},
                                    {"type": "tool_use", "id": "toolu_t", "name": "Bash",
                                     "input": {"command": "true"}}])
            + out[:-2] + ',"toolEndsTurn":true}\n'
            + msg("user", 14, [{"type": "tool_result", "tool_use_id": "toolu_t", "content": "ok"}]))
        (wf / "agent-a4.meta.json").write_text(json.dumps(
            {"agentType": "review_verifier", "description": "r1 · verify",
             "workflowPhase": "▸ review #1", "spawnDepth": 1, "requestShape": "foreground"}))

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
        a1, a2, a3, a4 = res["agents"]
        self.assertEqual((a4["id"], a4["done"], a4["interrupted"]), ("a4", True, False))
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
        # agent de workflow : son run (dossier) et sa phase ; les autres n'en ont pas
        self.assertEqual((a4["workflow"], a4["phase"], a4["tool"]), ("wf_1", "▸ review #1", None))
        self.assertEqual((a1["workflow"], a1["phase"]), (None, None))

    def test_workflows(self):
        """Nom, résumé et appel de lancement de chaque workflow, dans l'ordre, une fois chacun."""
        res = self.run_helper("python3", "helper.py", "agents", SID)
        self.assertEqual(res["workflow_runs"], [
            {"id": "wf_1", "name": "review-changes", "summary": "Relit puis vérifie", "tool": "toolu_wf",
             "start": 1791363610.0},
            {"id": "wf_2", "name": "review-changes", "summary": None, "tool": None, "start": None}])

    def test_agent_de_workflow(self):
        """Transcription d'un agent sans appel d'outil (agent de workflow) : par son id."""
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            res = self.run_helper(interp, script, "agent", SID, "a4", "50")
            self.assertTrue(res["found"], interp)
            self.assertEqual(res["meta"]["workflowPhase"], "▸ review #1")
            self.assertTrue(self.run_helper(interp, script, "agent", SID, "toolu_p", "50")["found"])
            self.assertFalse(self.run_helper(interp, script, "agent", SID, "a9", "50")["found"])

    def test_python_et_node(self):
        for cmd in (("agents", SID), ("history", SID, "50")):
            self.assertEqual(self.run_helper("python3", "helper.py", *cmd),
                             self.run_helper("node", "helper.js", *cmd), cmd[0])

    def test_historique(self):
        res = self.run_helper("python3", "helper.py", "history", SID, "50")
        self.assertEqual([a["id"] for a in res["agents"]], ["a1", "a2", "a3", "a4"])
        self.assertEqual([w["id"] for w in res["workflow_runs"]], ["wf_1", "wf_2"])

    def test_inconnue(self):
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            self.assertEqual(self.run_helper(interp, script, "agents", "nope"),
                             {"live": None, "agents": [], "workflow_runs": []})


class TestVolet(unittest.TestCase):
    def test_arbre(self):
        from claude_hub.agentsview import tree
        agents = [{"id": "a", "parent": None}, {"id": "b", "parent": None},
                  {"id": "c", "parent": "a"}, {"id": "d", "parent": "c"}, {"id": "e", "parent": "x"}]
        self.assertEqual([a["id"] for a in tree(agents)], ["a", "c", "d", "b", "e"])

    def test_workflows(self):
        """Un groupe par workflow à la place de son premier agent, ses agents par phase."""
        from claude_hub.agentsview import layout
        agents = [
            {"id": "a", "start": 1},
            {"id": "w1", "start": 2, "workflow": "wf_1", "phase": "▸ Review"},
            {"id": "b", "start": 3},
            {"id": "w2", "start": 4, "workflow": "wf_1", "phase": "Verify"},
            {"id": "c", "start": 5, "parent": "a"},
            {"id": "w3", "start": 6, "workflow": "wf_1", "phase": "▸ Review"},
            {"id": "n", "start": 7, "parent": "w1"},  # lancé par un agent du workflow, sans phase
            {"id": "x1", "start": 8, "workflow": "wf_2", "phase": None},
        ]
        runs = [{"id": "wf_1", "name": "review-changes", "summary": "Relit puis vérifie"}]
        out = layout(agents, runs)
        self.assertEqual([(k, x["id"]) for k, x in out],
                         [("agent", "a"), ("agent", "c"), ("workflow", "wf_1"), ("agent", "b"),
                          ("workflow", "wf_2")])
        w = out[2][1]
        self.assertEqual((w["name"], w["summary"]), ("review-changes", "Relit puis vérifie"))
        self.assertEqual([(p, [a["id"] for a in m]) for p, m in w["phases"]],
                         [("Review", ["w1", "n", "w3"]), ("Verify", ["w2"])])
        x = out[4][1]
        self.assertEqual((x["name"], x["summary"], [p for p, _ in x["phases"]]), ("wf_2", None, [""]))
        self.assertEqual(layout([], runs), [])

    def test_groupes(self):
        """Le volet déplace ensemble un agent et ceux qu'il a lancés (section « En cours »)."""
        from claude_hub.agentsview import layout, units
        agents = [{"id": "a", "start": 1}, {"id": "w1", "start": 2, "workflow": "wf_1"},
                  {"id": "b", "start": 3}, {"id": "c", "start": 4, "parent": "a"},
                  {"id": "d", "start": 5, "parent": "c"}, {"id": "e", "start": 6}]
        out = units(layout(agents))
        self.assertEqual([(k, x["id"] if k == "workflow" else [a["id"] for a in x]) for k, x in out],
                         [("agents", ["a", "c", "d"]), ("workflow", "wf_1"), ("agents", ["b"]),
                          ("agents", ["e"])])

    def test_etat_workflow(self):
        from claude_hub.agentsview import phase_label, workflow_state
        self.assertEqual(workflow_state(["done", "running"]), "running")
        self.assertEqual(workflow_state(["done", "waiting"]), "running")
        self.assertEqual(workflow_state(["done", "done"]), "done")
        self.assertEqual(workflow_state(["done", "stopped", "idle"]), "stopped")
        self.assertEqual(workflow_state(["done", "idle"]), "idle")
        self.assertEqual((phase_label("▸ review_style  #2"), phase_label(None)), ("review_style #2", ""))

    def test_etat(self):
        from claude_hub.agentsview import agent_state, duration
        self.assertEqual(agent_state({"done": True}, False), "done")
        self.assertEqual(agent_state({"interrupted": True}, True), "stopped")
        self.assertEqual(agent_state({"end": 1000}, True, now=99999), "running")
        self.assertEqual(agent_state({"end": 1000}, False, now=1100), "running")
        self.assertEqual(agent_state({"end": 1000}, False, now=99999), "idle")
        self.assertEqual((duration(42), duration(185), duration(3725)), ("42 s", "3 min", "1 h 02"))

    def test_bandeau(self):
        """Sous-agents au travail et leur étape, pour la liste d'activité de la saisie."""
        from claude_hub.agentsview import agent_line, running_agents
        agents = [
            {"id": "a1", "type": "Explore", "description": "Trouver  les\ntests", "start": 10, "end": 50,
             "last": {"role": "tool", "name": "Grep", "input": {"pattern": "set_sort_func"}}},
            {"id": "a2", "type": "general-purpose", "description": "Review fix", "start": 20, "end": 90,
             "last": {"role": "tool", "name": "Bash", "input": {"command": "cargo test\n  -p shipments"}}},
            {"id": "a3", "description": "fini", "done": True, "end": 95},
            {"id": "a4", "description": "arrêté", "interrupted": True, "end": 96},
            {"id": "a5", "type": "Plan", "start": 30, "last": {"role": "assistant", "text": "Je  lis\nle code"}},
        ]
        run = running_agents(agents, alive=True)
        self.assertEqual([a["id"] for a in run], ["a1", "a2", "a5"])  # par début : un ordre stable
        self.assertEqual([a["id"] for a in running_agents(agents, False, now=100)], ["a1", "a2", "a5"])
        self.assertEqual(running_agents(agents, False, now=99999), [])  # session arrêtée depuis longtemps
        self.assertEqual(running_agents(None, True), [])
        self.assertEqual(agent_line(run[1]), "Review fix › Bash cargo test -p shipments")
        self.assertEqual(agent_line(run[0]), "Trouver les tests › Grep set_sort_func")
        self.assertEqual(agent_line(run[2]), "Plan › Je lis le code")  # sans description : le type
        self.assertEqual(agent_line({"id": "x"}), "Agent")

    def test_etape(self):
        """L'étape d'un agent est la description de son appel, s'il en a une (comme le
        terminal) ; l'appel tel quel reste dans l'info-bulle."""
        from claude_hub.agentsview import agent_line, last_step, step_of
        bash = {"description": "Revue du parseur", "last": {"role": "tool", "name": "Bash", "input": {
            "command": "cd ~/proj; cargo test -p parser", "description": "Run the  parser\ntests"}}}
        self.assertEqual(step_of(bash), ("Run the parser tests", False))
        self.assertEqual(agent_line(bash), "Revue du parseur › Bash cd ~/proj; cargo test -p parser")
        # la description d'un appel Agent est celle du sous-agent qu'il lance
        agent = {"last": {"role": "tool", "name": "Agent", "input": {"description": "Lire la doc",
                                                                     "prompt": "…"}}}
        self.assertEqual(step_of(agent), ("Agent  Lire la doc", True))
        grep = {"last": {"role": "tool", "name": "Grep", "input": {"pattern": "x", "description": " "}}}
        self.assertEqual(step_of(grep), ("Grep  x", True))
        self.assertEqual(step_of({"last": {"role": "assistant", "text": "Je  lis"}}), ("Je lis", False))
        self.assertEqual(last_step({}), "")


if __name__ == "__main__":
    unittest.main()
