"""Shells et monitors que Claude laisse tourner en arrière-plan (« 1 shell », « 1 monitor » du
terminal) : commande « stat » des scripts distants, flux de claude -p et textes de la pastille
de l'en-tête."""
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

from claude_hub.shell import background_label, background_tooltip, since_text, waiting_text  # noqa: E402
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


def monitor(tool, task, command, description, hms="08:31:05", res_hms="08:31:06"):
    """Appel de l'outil Monitor et son résultat (comme claude 2.1.316)."""
    inp = {"command": command, "description": description, "timeout_ms": 1800000}
    return (line(type="assistant", isSidechain=False, timestamp=ts(hms), message={
        "role": "assistant", "content": [{"type": "tool_use", "id": tool, "name": "Monitor", "input": inp}]})
        + user([{"type": "tool_result", "tool_use_id": tool,
                 "content": f"Monitor started (task {task}, expires in 30m unless the source ends first)."}],
               res_hms, toolUseResult={"taskId": task, "timeoutMs": 1800000, "persistent": False}))


def event(task, text, hms="08:32:00"):
    """Événement d'un monitor : une notification sans <status>, écrite dans la file d'attente
    puis dans la conversation."""
    body = (f"<task-notification>\n<task-id>{task}</task-id>\n<summary>Monitor event: \"deploy\"</summary>\n"
            f"<event>{text}</event>\n</task-notification>")
    return (line(type="queue-operation", operation="enqueue", timestamp=ts(hms), content=body)
            + user(body, hms))


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

        def session(name, body, live=True, status="idle"):
            sid = f"{len(cls.sids) + 1:08d}-0000-0000-0000-000000000000"
            cls.sids[name] = sid
            (cls.proj / f"{sid}.jsonl").write_text(user("vas-y", "08:31:00") + body)
            if live:
                Path(cls.home, f".claude/sessions/{len(cls.sids)}.json").write_text(json.dumps(
                    {"pid": os.getpid(), "sessionId": sid, "startedAt": STARTED, "status": status}))

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
        # Monitors : chaque événement arrive en <task-notification>, sans <status> ; la session
        # est « shell » (au repos, une tâche de fond tourne) ou « busy ».
        watch = monitor("toolu_m", "bm1", "python3 watch.py --pr 27724 2>&1", "deploy of PR #27724")
        session("monitor", watch + event("bm1", "WATCHING pr=#27724")
                + event("bm1", 'STAGE stage="Build"\nBUILD build=#9230', "08:40:00"), status="shell")
        session("monitor_occupe", watch, status="busy")
        session("monitor_expire", watch + event("bm1", "WATCHING pr=#27724") + event(
            "bm1", "[Monitor expired after 30m with 1 event delivered. Re-arm it if you still need the watch.]",
            "09:01:06"), status="shell")
        session("monitor_fini", watch + event("bm1", "WATCHING pr=#27724") + notification("bm1"), status="shell")
        session("monitor_arrete", watch + stopped("bm1"), status="shell")
        session("monitor_idle", watch + event("bm1", "WATCHING pr=#27724"))  # claude : plus rien ne tourne
        session("monitor_ancien", monitor("toolu_m", "bm0", "sleep 99", "Vieux", "08:00:00", "08:00:01"),
                status="shell")

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
            "id": "bt1", "kind": "shell", "description": "Poll for cubic's review",
            "command": "N=27605; for _ in $(seq 40); do\n\tgh api …\ndone", "start": LAUNCH}])

    def test_finis(self):
        for name in ("fini", "arrete", "arretee", "ancien", "tue", "ignores"):
            self.assertEqual(self.stat(name), [], name)

    def test_ctrl_b(self):
        self.assertEqual(self.stat("ctrl_b"), [{"id": "bt_ctrlb", "kind": "shell", "description": "Tests",
                                                "command": "cargo test", "start": LAUNCH}])

    def test_commande_bang(self):
        self.assertEqual(self.stat("bang"), [{"id": "bt_bang", "kind": "shell", "description": "",
                                              "command": "cargo build --release", "start": LAUNCH}])

    def test_monitor(self):
        """Ses événements ne le disent pas fini ; le dernier est gardé, échappements décodés.
        Sa sortie n'est pas ouverte par un fils de claude (c'est claude qui l'écrit)."""
        self.assertEqual(self.stat("monitor"), [{
            "id": "bm1", "kind": "monitor", "description": "deploy of PR #27724",
            "command": "python3 watch.py --pr 27724 2>&1", "start": LAUNCH,
            "event": 'STAGE stage="Build"\nBUILD build=#9230'}])
        self.assertEqual(self.stat("monitor_occupe"), [{
            "id": "bm1", "kind": "monitor", "description": "deploy of PR #27724",
            "command": "python3 watch.py --pr 27724 2>&1", "start": LAUNCH, "event": None}])

    def test_monitors_finis(self):
        """Expiré, fini, arrêté par TaskStop, session revenue à « idle », ou lancé par un
        processus claude précédent."""
        for name in ("monitor_expire", "monitor_fini", "monitor_arrete", "monitor_idle", "monitor_ancien"):
            self.assertEqual(self.stat(name), [], name)


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

    def test_monitor(self):
        """Le flux décrit un monitor comme un shell (local_bash) : task_started le relie à
        l'appel de l'outil Monitor, ou son résultat le désigne (taskId et timeoutMs)."""
        s = {"id": "s1", "tasks": set(), "idle": False}
        hub = fake_hub(step=lambda *a: None, track_todos=lambda *a: False)
        Hub.on_event(hub, {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_use", "id": "toolu_m", "name": "Monitor",
             "input": {"command": "watch.sh", "description": "deploy"}}]}}, s)
        changed = {"type": "system", "subtype": "background_tasks_changed", "tasks": [
            {"task_id": "bm1", "task_type": "local_bash", "description": "deploy"},
            {"task_id": "bs1", "task_type": "local_bash", "description": "Sleep"}]}
        Hub.on_background_tasks(hub, changed, s)  # avant task_started : pas encore reconnu
        Hub.on_task(hub, {"type": "system", "subtype": "task_started", "task_id": "bm1",
                          "tool_use_id": "toolu_m", "task_type": "local_bash", "description": "deploy"}, s)
        self.assertEqual([(sh["id"], sh["kind"]) for sh in s["bg_shells"]], [("bm1", "monitor"), ("bs1", "shell")])
        Hub.on_background_tasks(hub, changed, s)
        self.assertEqual(background_label(s["bg_shells"]), "1 shell et 1 monitor en cours")
        # sans task_started (claude plus ancien) : le résultat de l'outil
        s = {"id": "s2", "tasks": set(), "idle": False}
        Hub.on_background_tasks(hub, dict(changed, tasks=changed["tasks"][:1]), s)
        Hub.on_event(hub, {"type": "user", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_m", "content": "Monitor started (task bm1…)"}]},
            "tool_use_result": {"taskId": "bm1", "timeoutMs": 1800000, "persistent": False}}, s)
        self.assertEqual(background_label(s["bg_shells"]), "1 monitor en cours")

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
        m = {"kind": "monitor"}
        self.assertEqual(background_label([m]), "1 monitor en cours")
        self.assertEqual(background_label([{}, m, m]), "1 shell et 2 monitors en cours")

    def test_bandeau(self):
        self.assertEqual(waiting_text([{}]), "Claude attend un shell")
        self.assertEqual(waiting_text([{}, {}]), "Claude attend 2 shells")
        self.assertEqual(waiting_text([{"kind": "monitor"}]), "Claude attend un monitor")
        self.assertEqual(waiting_text([{"kind": "shell"}, {"kind": "monitor"}]),
                         "Claude attend un shell et un monitor")

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

    def test_info_bulle_monitor(self):
        now = time.time()
        shells = [{"id": "bm1", "kind": "monitor", "description": "deploy of PR #27724",
                   "command": "python3 watch.py --pr 27724", "start": now - 180,
                   "event": "STAGE stage=Build\nBUILD   build=#9230\n"},
                  {"id": "bm2", "kind": "monitor", "description": "", "command": "tail -f log", "event": None}]
        self.assertEqual(background_tooltip(shells, now),
                         "Monitor : deploy of PR #27724 · depuis 3 min\n"
                         "  $ python3 watch.py --pr 27724\n"
                         "  → BUILD build=#9230\n"
                         "Monitor : tail -f log")


class TestNotifications(unittest.TestCase):
    """Pas de notification « Claude a terminé » tant que des shells ou des monitors tournent :
    chaque événement d'un monitor relance un tour. Fin notifiée au repos, ou réponse à un
    message écrit d'ici pendant l'attente."""

    def setUp(self):
        self.sent = []
        self.hub = fake_hub(held=set(), asked=set(),
                            reply_done=lambda s, **kw: self.sent.append(kw))
        self.s = {"id": "s1"}

    def poll(self, *states):
        """Relevés successifs de l'état de la session, comme poll_states."""
        for live in states:
            self.s["live"] = live
            Hub.follow_turn(self.hub, self.s)
            self.s["was_busy"] = live == "busy"

    def test_monitor(self):
        self.poll("idle", "busy", "shell")  # Claude lance un monitor et finit son tour
        self.poll("busy", "shell", "busy", "shell")  # événements du monitor
        self.assertEqual(self.sent, [])
        self.poll("busy", "idle")  # monitor fini, Claude en rend compte
        self.assertEqual(self.sent, [{"waiting": False, "replied": False}])
        self.poll("idle")
        self.assertEqual(len(self.sent), 1)

    def test_sans_tache_de_fond(self):
        self.poll("idle", "busy", "idle")
        self.assertEqual(self.sent, [{"waiting": False, "replied": False}])

    def test_dernier_tour_manque(self):
        """Dernier tour plus court que l'intervalle des relevés : shell puis idle."""
        self.poll("busy", "shell", "idle")
        self.assertEqual(self.sent, [{}])
        self.assertEqual(self.hub.held, set())

    def test_claude_quitte(self):
        self.poll("busy", "shell", None)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.hub.held, set())

    def test_demande(self):
        """Une question de Claude pendant l'attente est notifiée, la fin aussi."""
        self.poll("busy", "shell", "busy", "requires_action")
        self.assertEqual(self.sent, [{"waiting": True, "replied": False}])
        self.poll("idle")  # répondu dans le terminal, tour trop court pour le relevé
        self.assertEqual(self.sent[1:], [{}])

    def test_message_pendant_l_attente(self):
        self.poll("busy", "shell")
        self.hub.asked.add("s1")  # message envoyé d'ici (Remote Control)
        self.poll("busy", "shell")
        self.assertEqual(self.sent, [{"waiting": False, "replied": True}])
        self.poll("busy", "shell")  # événement suivant du monitor
        self.assertEqual(len(self.sent), 1)
        self.poll("busy", "idle")
        self.assertEqual(self.sent[1:], [{"waiting": False, "replied": False}])
        self.assertEqual((self.hub.held, self.hub.asked), (set(), set()))

    def test_flux(self):
        """claude -p : message transmis pendant qu'il attend ses tâches de fond, réponse
        notifiée sans attendre la fin du processus."""
        s = {"id": "s1", "tasks": {"bm1"}, "idle": True}
        proc = types.SimpleNamespace(stdin=types.SimpleNamespace(closed=False))
        status = types.SimpleNamespace(set_text=lambda t: None)
        hub = fake_hub(ready_shells=lambda sid: [],
                       write_input=lambda p, line: True, user_message=Hub.user_message,
                       shells_sent=lambda sid, sh: None, scroll_end=lambda: None, status=status,
                       settings={"context_windows": {}, "costs": {}}, show_usage=lambda s: None,
                       end_if_done=lambda s: None, touch=lambda sid: None,
                       track_todos=lambda ev, s: False,
                       reply_done=lambda s, **kw: self.sent.append(kw))
        hub.streams["s1"] = (proc, s)
        result = {"type": "result", "result": "Réponse", "is_error": False}
        Hub.on_event(hub, result, s)  # tour lancé par un événement du monitor
        self.assertEqual(self.sent, [])
        self.assertTrue(Hub.inject(hub, s, "", []))
        Hub.on_event(hub, result, s)
        self.assertEqual(self.sent, [{"text": "Réponse", "error": False, "replied": True}])
        s["tasks"] = set()  # sans tâche de fond : claude se termine, on_finished notifie
        Hub.inject(hub, s, "", [])
        Hub.on_event(hub, result, s)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
