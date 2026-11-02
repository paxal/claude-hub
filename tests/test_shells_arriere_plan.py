"""Shells que Claude laisse tourner en arrière-plan (« 1 shell » du terminal) : commande
« stat » des scripts distants, flux de claude -p et textes de la pastille de l'en-tête."""
import calendar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "src/claude_hub/remote"
sys.path.insert(0, str(ROOT / "src"))

from claude_hub.shell import background_label, background_tooltip, since_text  # noqa: E402
from claude_hub.window import Hub  # noqa: E402

PROJ = "-tmp-x"
STARTED = calendar.timegm((2026, 10, 7, 8, 30, 0)) * 1000  # démarrage du processus claude (ms)
LAUNCH = calendar.timegm((2026, 10, 7, 8, 31, 6))


def line(**r):
    return json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"  # comme claude


def ts(hms):
    return f"2026-10-07T{hms}.000Z"


def user(content, hms, **kw):
    kw.setdefault("isSidechain", False)
    return line(type="user", timestamp=ts(hms), message={"role": "user", "content": content}, **kw)


def bash(tool, command, description, hms="08:31:05"):
    inp = {"command": command, "description": description, "run_in_background": True}
    return line(type="assistant", isSidechain=False, timestamp=ts(hms), message={
        "role": "assistant", "content": [{"type": "tool_use", "id": tool, "name": "Bash", "input": inp}]})


def launched(tool, task, hms="08:31:06", sidechain=False, **extra):
    res = {"stdout": "", "stderr": "", "interrupted": False, "isImage": False,
           "noOutputExpected": False, "backgroundTaskId": task, **extra}
    return user([{"type": "tool_result", "tool_use_id": tool,
                  "content": f"Command running in background with ID: {task}."}],
                hms, isSidechain=sidechain, toolUseResult=res)


def notification(task, hms="08:40:00"):
    return line(type="queue-operation", operation="enqueue", timestamp=ts(hms),
                content=f"<task-notification>\n<task-id>{task}</task-id>\n<status>completed</status>\n"
                        "<summary>Background command completed (exit code 0)</summary>\n</task-notification>")


def stopped(task, hms="08:35:00"):
    return user([{"type": "tool_result", "tool_use_id": "toolu_stop",
                  "content": json.dumps({"message": f"Successfully stopped task: {task} (sleep 60)"})}],
                hms, toolUseResult={"message": f"Successfully stopped task: {task} (sleep 60)",
                                    "task_id": task, "task_type": "local_bash", "command": "sleep 60"})


class TestStat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        cls.proj = Path(cls.home, ".claude/projects", PROJ)
        cls.proj.mkdir(parents=True)
        Path(cls.home, ".claude/sessions").mkdir()
        # Shells qui tournent : fils de ce processus (le « claude » des fichiers de session),
        # sortie dans tasks/<id>.output comme ceux de Claude Code. fg1 : commande au premier plan.
        tasks = Path(cls.home, "tmp/claude-1000", PROJ, "sid/tasks")
        tasks.mkdir(parents=True)
        cls.procs = []
        for task in ("bt1", "bt_ctrlb", "bt_bang", "bt_old", "fg1"):
            with open(tasks / f"{task}.output", "w") as out:
                cls.procs.append(subprocess.Popen(["sleep", "60"], stdout=out, stderr=out))
        cls.sids = {}

        def session(name, body, live=True):
            sid = f"{len(cls.sids) + 1:08d}-0000-0000-0000-000000000000"
            cls.sids[name] = sid
            (cls.proj / f"{sid}.jsonl").write_text(user("vas-y", "08:31:00") + body)
            if live:
                Path(cls.home, f".claude/sessions/{len(cls.sids)}.json").write_text(json.dumps(
                    {"pid": os.getpid(), "sessionId": sid, "startedAt": STARTED, "status": "idle"}))

        start = bash("toolu_1", "N=27605; for _ in $(seq 40); do\n\tgh api …\ndone",
                     "Poll for cubic's review") + launched("toolu_1", "bt1")
        session("tourne", start)
        session("fini", start + notification("bt1"))
        session("arrete", start + stopped("bt1"))
        session("arretee", start, live=False)
        # lancé par un processus claude précédent : mort avec lui
        session("ancien", bash("toolu_1", "sleep 60", "Vieux", "08:00:00")
                + launched("toolu_1", "bt_old", "08:00:01"))
        # arrêté depuis le terminal : aucune trace, mais plus aucun processus ne l'écrit
        session("tue", bash("toolu_1", "sleep 60", "Tué") + launched("toolu_1", "bt_dead"))
        # Ctrl+B pendant une commande au premier plan
        session("ctrl_b", line(type="assistant", isSidechain=False, timestamp=ts("08:31:05"), message={
            "role": "assistant", "content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash",
                                              "input": {"command": "cargo test", "description": "Tests"}}]})
                + launched("toolu_2", "bt_ctrlb", backgroundedByUser=True))
        # commande « ! » tapée dans le terminal, renvoyée en arrière-plan
        session("bang", user("<bash-input>cargo build --release</bash-input>", "08:31:02")
                + user("<bash-stdout>Command was manually backgrounded by user with ID: bt_bang. Output is "
                       "being written to: /tmp/x/tasks/bt_bang.output.</bash-stdout><bash-stderr></bash-stderr>",
                       "08:31:06"))
        # shell d'un sous-agent (transcription à part) et texte qui ne fait que citer les marqueurs
        session("ignores", launched("toolu_3", "bt1", sidechain=True)
                + user([{"type": "tool_result", "tool_use_id": "toolu_4",
                         "content": "<bash-stdout>Command was manually backgrounded by user with ID: bt1."}],
                       "08:32:00"))

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
        shutil.rmtree(cls.home)

    def stat(self, name):
        """Shells de la commande « stat » avec helper.py et helper.js, qui doivent être identiques."""
        outs = []
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            r = subprocess.run([interp, "-", "stat", self.sids[name]], input=(REMOTE / script).read_text(),
                               capture_output=True, text=True, check=True,
                               env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            outs.append(json.loads(r.stdout))
        self.assertEqual(outs[0], outs[1], name)
        return outs[0]["shells"]

    def test_tourne(self):
        self.assertEqual(self.stat("tourne"), [{
            "id": "bt1", "description": "Poll for cubic's review",
            "command": "N=27605; for _ in $(seq 40); do\n\tgh api …\ndone", "start": LAUNCH}])

    def test_finis(self):
        for name in ("fini", "arrete", "arretee", "ancien", "tue", "ignores"):
            self.assertEqual(self.stat(name), [], name)

    def test_ctrl_b(self):
        self.assertEqual(self.stat("ctrl_b"), [{"id": "bt_ctrlb", "description": "Tests",
                                                "command": "cargo test", "start": LAUNCH}])

    def test_commande_bang(self):
        self.assertEqual(self.stat("bang"), [{"id": "bt_bang", "description": "",
                                              "command": "cargo build --release", "start": LAUNCH}])


def fake_hub(**kw):
    return types.SimpleNamespace(streams={}, current=None, update_activity=lambda: None, **kw)


class TestFlux(unittest.TestCase):
    """claude -p : background_tasks_changed donne la liste ; task_started d'un shell n'est
    pas un sous-agent (observé, claude 2.1.312)."""

    def test_liste(self):
        s = {"id": "s1"}
        hub = fake_hub()
        ev = {"type": "system", "subtype": "background_tasks_changed", "tasks": [
            {"task_id": "bpbk1yqgi", "task_type": "local_bash", "description": "Sleep for 8 seconds"},
            {"task_id": "a1", "task_type": "local_agent", "description": "Revue"}]}
        Hub.on_background_tasks(hub, ev, s)
        self.assertEqual([(sh["id"], sh["description"]) for sh in s["bg_shells"]],
                         [("bpbk1yqgi", "Sleep for 8 seconds")])
        start = s["bg_shells"][0]["start"]
        Hub.on_background_tasks(hub, ev, s)  # même liste : le début ne change pas
        self.assertEqual(s["bg_shells"][0]["start"], start)
        hub.streams["s1"] = (None, s)
        self.assertEqual(len(Hub.background_shells(hub, {"id": "s1"})), 1)  # vue rouverte : celle du flux
        Hub.on_background_tasks(hub, dict(ev, tasks=[]), s)
        self.assertEqual(Hub.background_shells(hub, s), [])

    def test_terminal(self):
        hub = fake_hub()
        self.assertEqual(Hub.background_shells(hub, {"id": "s1", "shells": [{"id": "b"}]}), [{"id": "b"}])
        self.assertEqual(Hub.background_shells(hub, None), [])

    def test_pas_un_sous_agent(self):
        s = {"id": "s1", "tasks": set(), "idle": False}
        Hub.on_task(fake_hub(), {"type": "system", "subtype": "task_started", "task_id": "b1",
                                 "tool_use_id": "toolu_1", "task_type": "local_bash",
                                 "description": "Sleep", "is_backgrounded": True}, s)
        self.assertEqual((s["tasks"], s["live_agents"]), ({"b1"}, {}))
        self.assertNotIn("agents_stale", s)

    def test_shells_oublies_a_l_arret(self):
        s = {"live": "idle", "shells": [{"id": "b"}]}
        Hub.set_live_since(None, s, None)
        self.assertIn("shells", s)
        s["live"] = None
        Hub.set_live_since(None, s, None)
        self.assertNotIn("shells", s)


class TestTextes(unittest.TestCase):
    def test_pastille(self):
        self.assertEqual(background_label([]), "")
        self.assertEqual(background_label([{}]), "1 shell en cours")
        self.assertEqual(background_label([{}, {}]), "2 shells en cours")

    def test_duree(self):
        self.assertEqual(since_text(30), "moins d'une minute")
        self.assertEqual(since_text(12 * 60 + 5), "12 min")
        self.assertEqual(since_text(3600 + 5 * 60), "1 h 05")

    def test_info_bulle(self):
        now = time.time()
        shells = [{"id": "b1", "description": "Poll for cubic's review",
                   "command": "N=1; for _ in $(seq 40); do\n  gh api …\ndone", "start": now - 125},
                  {"id": "b2", "description": "", "command": "cargo build", "start": None},
                  {"id": "b3"}]
        self.assertEqual(background_tooltip(shells, now),
                         "Poll for cubic's review · depuis 2 min\n"
                         "  $ N=1; for _ in $(seq 40); do\n"
                         "cargo build\n"
                         "b3")


if __name__ == "__main__":
    unittest.main()
