"""Workflows en arrière-plan d'une session de terminal (commande « stat » des scripts
distants) et durée du bandeau d'après le fichier de session de claude (« live »)."""
import calendar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "src/claude_hub/remote"
sys.path.insert(0, str(ROOT / "src"))

from claude_hub.activity import waiting_for, workflow_spec  # noqa: E402
from claude_hub.agentsview import agent_workflows, outside_workflows, workflow_detail  # noqa: E402
from claude_hub.window import Hub  # noqa: E402

PROJ = "-tmp-x"
STARTED = calendar.timegm((2026, 10, 7, 8, 30, 0)) * 1000  # démarrage du processus claude (ms)
SINCE = STARTED + 60000  # passage à « busy »
LAUNCHED = calendar.timegm((2026, 10, 7, 8, 31, 6))  # lancement du workflow (epoch)


def line(**r):
    return json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"  # comme claude


def ts(hms):
    return f"2026-10-07T{hms}.000Z"


def user(content, hms, **kw):
    return line(type="user", isSidechain=False, timestamp=ts(hms),
                message={"role": "user", "content": content}, **kw)


def assistant(content, hms):
    return line(type="assistant", isSidechain=False, timestamp=ts(hms),
                message={"role": "assistant", "content": content})


def turn_end(hms):
    return line(type="system", subtype="turn_duration", isSidechain=False, durationMs=5000,
                pendingWorkflowCount=1, timestamp=ts(hms))


def notification(task, hms):
    return line(type="queue-operation", operation="enqueue", timestamp=ts(hms),
                content=f"<task-notification>\n<task-id>{task}</task-id>\n<status>completed</status>\n"
                        "<summary>Dynamic workflow \"review\" completed</summary>\n</task-notification>")


class TestWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        cls.proj = Path(cls.home, ".claude/projects", PROJ)
        cls.proj.mkdir(parents=True)
        Path(cls.home, ".claude/sessions").mkdir()
        cls.sids = {}

        def launch(sid, task, run, hms, with_dir=True, sidechain=False):
            res = {"status": "async_launched", "taskId": task, "taskType": "local_workflow",
                   "workflowName": "review", "runId": run, "summary": "Revue de la branche"}
            if with_dir:
                res["transcriptDir"] = str(cls.proj / sid / "subagents/workflows" / run)
            return line(type="user", isSidechain=sidechain, timestamp=ts(hms), toolUseResult=res,
                        message={"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": "toolu_w",
                             "content": f"Workflow launched in background. Task ID: {task}"}]})

        def session(name, body, live=True, journal=True):
            sid = f"{len(cls.sids) + 1:08d}-0000-0000-0000-000000000000"
            cls.sids[name] = sid
            (cls.proj / f"{sid}.jsonl").write_text(body(sid))
            if journal:
                d = cls.proj / sid / "subagents/workflows/wf_1"
                d.mkdir(parents=True)
                (d / "journal.jsonl").write_text(
                    line(type="launched")
                    + line(type="started", key="k1", agentId="a1", label="pré-passe", phase="▸ review_shared")
                    + line(type="result", key="k1", agentId="a1", result={"files": []})
                    + line(type="started", key="k2", agentId="a2", label="r1 · Rust 1/2",
                           phase="▸ review_style #1")
                    + line(type="started", key="k3", agentId="a3", label="r1 · Rust 2/2",
                           phase="▸ review_style #1")
                    + line(type="result", key="k3", agentId="a3", result={"findings": []}))
            if live:
                Path(cls.home, f".claude/sessions/{len(cls.sids)}.json").write_text(json.dumps(
                    {"pid": os.getpid(), "sessionId": sid, "startedAt": STARTED, "status": "busy",
                     "statusUpdatedAt": SINCE}))

        def start(sid, extra=""):
            return (user("review fix", "08:31:00")
                    + assistant([{"type": "tool_use", "id": "toolu_w", "name": "Workflow", "input": {}}],
                                "08:31:05")
                    + launch(sid, "wt1", "wf_1", "08:31:06")
                    + assistant([{"type": "text", "text": "La revue tourne."}], "08:31:10")
                    + turn_end("08:31:11") + extra)

        # Claude a fini son tour ; /workflows tapé dans le terminal ne relance pas de tour
        session("attend", lambda sid: start(sid, (
            user("<local-command-caveat>Caveat: …</local-command-caveat>", "08:40:00", isMeta=True)
            + user("<command-name>/workflows</command-name>\n<command-message>workflows</command-message>",
                   "08:40:00")
            + user("<local-command-stdout>(fermé)</local-command-stdout>", "08:40:01"))))
        # nouveau message pendant le workflow : Claude travaille
        session("travaille", lambda sid: start(sid, user("et la PR ?", "08:45:00")
                                               + assistant([{"type": "text", "text": "Je regarde"}], "08:45:03")))
        session("fini", lambda sid: start(sid, notification("wt1", "09:50:00")))
        # lancé par un processus claude précédent : mort avec lui
        session("ancien", lambda sid: (user("review", "08:00:00") + launch(sid, "wt1", "wf_1", "08:00:05")
                                       + turn_end("08:00:10")))
        session("sans_chemin", lambda sid: user("review", "08:31:00")
                + launch(sid, "wt1", "wf_1", "08:31:06", with_dir=False) + turn_end("08:31:11"))
        session("sous_agent", lambda sid: user("review", "08:31:00")
                + launch(sid, "wt1", "wf_1", "08:31:06", sidechain=True) + turn_end("08:31:11"))
        session("arretee", lambda sid: start(sid), live=False)
        session("sans_journal", lambda sid: start(sid), journal=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, *args):
        """Sortie de la commande avec helper.py et helper.js, qui doivent être identiques."""
        outs = []
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                               capture_output=True, text=True, check=True,
                               env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            outs.append(json.loads(r.stdout))
        self.assertEqual(outs[0], outs[1], args)
        return outs[0]

    def stat(self, name):
        return self.run_helper("stat", self.sids[name])

    def test_attend(self):
        r = self.stat("attend")
        self.assertEqual((r["live"], r["since"], r["waiting"]), ("busy", SINCE, True))
        self.assertEqual(r["workflows"], [{"name": "review", "total": 3, "done": 2, "running": [
            {"label": "r1 · Rust 1/2", "phase": "▸ review_style #1"}], "id": "wf_1", "start": LAUNCHED}])

    def test_travaille(self):
        r = self.stat("travaille")
        self.assertFalse(r["waiting"])
        self.assertEqual(len(r["workflows"]), 1)

    def test_fini_ou_ancien(self):
        for name in ("fini", "ancien", "sous_agent"):
            r = self.stat(name)
            self.assertEqual((r["workflows"], r["waiting"]), ([], False), name)

    def test_sans_chemin(self):
        """Sans transcriptDir, le journal est cherché à sa place habituelle."""
        self.assertEqual(self.stat("sans_chemin")["workflows"][0]["total"], 3)

    def test_sans_journal(self):
        r = self.stat("sans_journal")
        self.assertEqual(r["workflows"], [{"name": "review", "total": 0, "done": 0, "running": [],
                                           "id": "wf_1", "start": LAUNCHED}])
        self.assertTrue(r["waiting"])

    def test_session_arretee(self):
        r = self.stat("arretee")
        self.assertEqual((r["live"], r["since"], r["workflows"], r["waiting"]), (None, None, [], False))

    def test_live(self):
        r = self.run_helper("live")
        sid = self.sids["attend"]
        self.assertEqual((r[sid], r[sid + ":since"]), ("busy", SINCE))
        self.assertNotIn(self.sids["arretee"], r)


class TestLigneWorkflow(unittest.TestCase):
    """Ligne d'un workflow au-dessus de la saisie (activity.workflow_spec)."""

    def test_detail(self):
        a = {"label": "r1 · Rust 1/2", "phase": "▸ review_style #1"}
        b = {"label": "r1 · Rust 2/2", "phase": "▸ review_style #1"}
        c = {"label": "vérif", "phase": "▸ review_shared"}
        self.assertEqual(workflow_detail({"name": "review", "total": 3, "done": 2, "running": [a]}),
                         "2/3 · review_style #1 : r1 · Rust 1/2")
        self.assertEqual(workflow_detail({"name": "review", "total": 3, "done": 1, "running": [a, b]}),
                         "1/3 · 2 en cours (review_style #1) : r1 · Rust 1/2, r1 · Rust 2/2")
        self.assertEqual(workflow_detail({"name": "review", "total": 3, "done": 1, "running": [a, c]}),
                         "1/3 · 2 en cours : r1 · Rust 1/2, vérif")
        self.assertEqual(workflow_detail({"name": "", "total": 0, "done": 0, "running": []}), "")

    def test_ligne(self):
        w = {"name": "review", "total": 13, "done": 12, "id": "wf_1", "start": 1000.0,
             "running": [{"label": "r1 · pass1", "phase": "▸ review_style"}]}
        spec = workflow_spec(w, indent=True, open_panel=print)
        self.assertEqual((spec["key"], spec["kind"], spec["label"]), ("workflow:wf_1", "workflow", "review"))
        self.assertEqual((spec["detail"], spec["start"], spec["inline"]),
                         ("12/13 · review_style : r1 · pass1", 1000.0, True))
        self.assertAlmostEqual(spec["fraction"], 12 / 13)
        self.assertIn("12 agents finis sur 13", spec["tooltip"])
        self.assertIn("open", spec["actions"])
        empty = workflow_spec({"name": "", "total": 0, "done": 0, "running": [], "start": True})
        self.assertEqual((empty["label"], empty["fraction"], empty["start"]), ("workflow", 0.0, None))

    def test_claude_attend(self):
        self.assertEqual(waiting_for(1, 10), "Claude attend un workflow et 10 sous-agents")
        self.assertEqual(waiting_for(2, 0), "Claude attend 2 workflows")
        self.assertEqual(waiting_for(0, 1), "Claude attend un sous-agent")
        self.assertIsNone(waiting_for(0, 0))

    def test_d_apres_les_agents(self):
        """claude -p lancé d'ici : workflows déduits de la liste des sous-agents ; leurs
        agents, et ceux qu'ils ont lancés, ne sont pas listés comme sous-agents."""
        agents = [
            {"id": "a", "start": 1, "description": "Audit UPS"},
            {"id": "w1", "start": 2, "workflow": "wf_1", "phase": "▸ Review", "description": "r1", "done": True},
            {"id": "w2", "start": 3, "workflow": "wf_1", "phase": "▸ Verify", "description": "v1"},
            {"id": "n", "start": 4, "parent": "w2"},  # lancé par un agent du workflow
            {"id": "x", "start": 5, "workflow": "wf_2", "done": True},  # workflow fini
        ]
        runs = [{"id": "wf_1", "name": "review-changes"}]
        self.assertEqual(agent_workflows(agents, True, runs), [
            {"id": "wf_1", "name": "review-changes", "total": 2, "done": 1,
             "running": [{"label": "v1", "phase": "▸ Verify"}], "start": 2}])
        self.assertEqual([a["id"] for a in outside_workflows(agents, agents)], ["a"])
        self.assertEqual(agent_workflows([], True), [])


class TestDuree(unittest.TestCase):
    """Le bandeau compte depuis le passage à « busy » noté par claude, pas depuis l'ouverture."""

    def test_depuis(self):
        s = {"live": "busy", "workflows": [{}], "workflows_waiting": True}
        Hub.set_live_since(None, s, (time.time() - 3600) * 1000)
        self.assertAlmostEqual(time.monotonic() - Hub.busy_start(None, s), 3600, delta=2)
        s["live"] = "idle"
        Hub.set_live_since(None, s, time.time() * 1000)
        self.assertEqual(s, {"live": "idle"})

    def test_sans_date(self):
        s = {"live": "busy"}
        Hub.set_live_since(None, s, None)
        self.assertNotIn("live_since", s)
        self.assertAlmostEqual(Hub.busy_start(None, s), time.monotonic(), delta=1)


if __name__ == "__main__":
    unittest.main()
