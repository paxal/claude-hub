"""claude -p lancé d'ici avant un redémarrage de Claude Hub (orphelin) : commandes « stat »
(claude sans terminal, réponse finie) et « stop » des scripts distants, et suivi par la
fenêtre (Hub.orphan, follow_orphans, orphan_ended)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "src/claude_hub/remote"
sys.path.insert(0, str(ROOT / "src"))

from claude_hub.remotecontrol import orphan_texts  # noqa: E402
from claude_hub.window import Hub  # noqa: E402

PROJ = "-tmp-x"
STARTED = 1793699833007  # démarrage du claude (ms), avant les messages ci-dessous


def line(**r):
    return json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"  # comme claude


def user(text, t="10:00:00"):
    return line(type="user", timestamp=f"2026-11-03T{t}.000Z", isSidechain=False,
                message={"role": "user", "content": text})


def assistant(stop, *blocks, t="10:00:01"):
    return line(type="assistant", timestamp=f"2026-11-03T{t}.000Z", isSidechain=False,
                message={"role": "assistant", "stop_reason": stop, "content": list(blocks)})


def tool_result(tid, result=None, t="10:00:02"):
    r = {"type": "user", "timestamp": f"2026-11-03T{t}.000Z", "isSidechain": False,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                   "content": "ok"}]}}
    if result is not None:
        r["toolUseResult"] = result
    return line(**r)


TEXT = {"type": "text", "text": "Fini."}
CALL = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}
AGENT = {"type": "tool_use", "id": "toolu_2", "name": "Agent", "input": {"prompt": "x"}}
LAUNCHED = tool_result("toolu_2", {"status": "async_launched", "isAsync": True,
                                   "agentId": "a1440619f4d3d06fa", "description": "x"})
AGENT_DONE = user("<task-notification>\n<task-id>a1440619f4d3d06fa</task-id>\n"
                  "<status>completed</status>\n</task-notification>", t="10:00:05")
TAIL = line(type="last-prompt", lastPrompt="Fais-le", sessionId="x") + line(type="cost-state")


def run(script, *args, home):
    interp = "python3" if script.endswith(".py") else "node"
    r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                       capture_output=True, text=True, check=True,
                       env={"HOME": home, "PATH": "/usr/bin:/bin"})
    return json.loads(r.stdout)


class HelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="claude-hub-test-")
        cls.proj = Path(cls.home, ".claude/projects", PROJ)
        cls.proj.mkdir(parents=True)
        Path(cls.home, ".claude/sessions").mkdir()
        cls.sids = {}

        def session(name, body, entrypoint="sdk-cli", live=True):
            sid = f"{len(cls.sids) + 1:08d}-0000-0000-0000-000000000000"
            cls.sids[name] = sid
            (cls.proj / f"{sid}.jsonl").write_text(user("Fais-le") + body)
            if live:
                Path(cls.home, f".claude/sessions/{len(cls.sids)}.json").write_text(json.dumps(
                    {"pid": os.getpid(), "sessionId": sid, "startedAt": STARTED, "status": "busy",
                     "kind": "interactive", "entrypoint": entrypoint}))

        session("finie", assistant("tool_use", CALL) + tool_result("toolu_1")
                + assistant("end_turn", TEXT, t="10:00:03") + TAIL)
        session("appel", assistant("tool_use", CALL))
        session("resultat", assistant("tool_use", CALL) + tool_result("toolu_1"))
        session("relancee", assistant("end_turn", TEXT) + user("Et ça ?", t="10:00:04"))
        session("agent", assistant("tool_use", AGENT) + LAUNCHED + assistant("end_turn", TEXT, t="10:00:03"))
        session("agent_fini", assistant("tool_use", AGENT) + LAUNCHED + assistant("end_turn", TEXT, t="10:00:03")
                + AGENT_DONE + assistant("end_turn", TEXT, t="10:00:06"))
        session("terminal", assistant("end_turn", TEXT), entrypoint="cli")
        session("arretee", assistant("end_turn", TEXT), live=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def stat(self, name):
        """(headless, finished) de « stat », identiques avec helper.py et helper.js."""
        outs = [run(script, "stat", self.sids[name], home=self.home)
                for script in ("helper.py", "helper.js")]
        self.assertEqual(outs[0], outs[1], name)
        return outs[0]["headless"], outs[0]["finished"]

    def test_reponse_finie(self):
        self.assertEqual(self.stat("finie"), (True, True))

    def test_reponse_en_cours(self):
        self.assertEqual(self.stat("appel"), (True, False))     # outil en cours
        self.assertEqual(self.stat("resultat"), (True, False))  # Claude va lire le résultat
        self.assertEqual(self.stat("relancee"), (True, False))  # nouveau message pas encore lu

    def test_sous_agent_en_arriere_plan(self):
        self.assertEqual(self.stat("agent"), (True, False))
        self.assertEqual(self.stat("agent_fini"), (True, True))

    def test_terminal(self):
        """Le claude d'un terminal n'est jamais pris pour celui de Claude Hub."""
        self.assertEqual(self.stat("terminal"), (False, False))

    def test_arretee(self):
        self.assertEqual(self.stat("arretee"), (False, False))

    def test_stop(self):
        """« stop » arrête un claude sans terminal, jamais celui d'un terminal."""
        for script in ("helper.py", "helper.js"):
            for entrypoint, stops in (("cli", False), ("sdk-cli", True)):
                with self.subTest(script=script, entrypoint=entrypoint):
                    proc = subprocess.Popen(["sleep", "60"])
                    threading.Thread(target=proc.wait, daemon=True).start()  # pas de zombie
                    sid = f"stop-{entrypoint}"
                    f = Path(self.home, f".claude/sessions/{proc.pid}.json")
                    f.write_text(json.dumps({"pid": proc.pid, "sessionId": sid, "entrypoint": entrypoint}))
                    try:
                        self.assertEqual(run(script, "stop", sid, home=self.home), {"stopped": stops})
                        self.assertEqual(proc.poll() is not None, stops)
                    finally:
                        proc.kill()
                        f.unlink()

    def test_stop_sans_claude(self):
        for script in ("helper.py", "helper.js"):
            self.assertEqual(run(script, "stop", self.sids["arretee"], home=self.home), {"stopped": False})


def fake_hub(launched=None, sessions=(), **kw):
    hub = types.SimpleNamespace(settings={"launched": dict(launched or {})}, streams={},
                                sessions=list(sessions), current=None, queues={}, calls=[], **kw)
    hub.orphan = lambda s: Hub.orphan(hub, s)
    hub.unmark_launched = lambda sid: hub.settings["launched"].pop(sid, None)
    return hub


def conv(sid="s1", **kw):
    return {"id": sid, "host": "local", "live": "busy", **kw}


class WindowTest(unittest.TestCase):
    def test_orphelin(self):
        hub = fake_hub({"s1": "local"})
        self.assertTrue(hub.orphan(conv()))
        self.assertTrue(hub.orphan(conv(live="idle")))
        self.assertFalse(hub.orphan(conv(live=None)))   # arrêté
        self.assertFalse(hub.orphan(conv("s2")))        # pas lancé d'ici
        self.assertFalse(hub.orphan(conv(source="cursor")))
        self.assertFalse(hub.orphan(None))
        hub.streams["s1"] = (object(), conv())          # lancé par ce Claude Hub : pas orphelin
        self.assertFalse(hub.orphan(conv()))

    def test_releve(self):
        """Après le relevé des états : un orphelin arrêté est oublié, un autre est relu ; ceux
        de cette instance, et ceux d'un hôte pas relevé, ne sont pas touchés."""
        hub = fake_hub({"vivant": "local", "fini": "local", "ici": "local", "loin": "rpi4"})
        hub.streams["ici"] = (object(), conv("ici"))
        hub.check_orphan = lambda sid, host: hub.calls.append(("check", sid, host))
        hub.orphan_ended = lambda sid: hub.calls.append(("ended", sid))
        Hub.follow_orphans(hub, {"vivant": "busy", "ici": "busy", "loin": "busy"}, {"local": {}})
        self.assertEqual(sorted(hub.calls), [("check", "vivant", "local"), ("ended", "fini")])

    def test_fin_de_l_orphelin(self):
        """L'orphelin arrêté : la conversation n'est plus « en cours », et la file part."""
        listed, shown = conv(), conv()
        hub = fake_hub({"s1": "local"}, sessions=[listed])
        hub.current = shown
        hub.queues["s1"] = [("Et ensuite ?", [])]
        hub.refresh_state = lambda sid: hub.calls.append(("refresh", sid))
        hub.update_status = lambda: hub.calls.append(("status",))
        hub.send_queued = lambda s: hub.calls.append(("send", s is shown))
        Hub.orphan_ended(hub, "s1")
        self.assertEqual((listed["live"], shown["live"]), (None, None))
        self.assertEqual(hub.settings["launched"], {})
        self.assertEqual(hub.calls, [("refresh", "s1"), ("status",), ("send", True)])

    def test_fin_sans_file(self):
        hub = fake_hub({"s1": "local"}, sessions=[conv()])
        hub.refresh_state = lambda sid: None
        hub.send_queued = lambda s: hub.calls.append("send")
        Hub.orphan_ended(hub, "s1")
        self.assertEqual(hub.calls, [])


class TextsTest(unittest.TestCase):
    def test_explication(self):
        title, paragraphs = orphan_texts()
        self.assertEqual(title, "Lancée avant le redémarrage")
        text = "\n".join(paragraphs)
        self.assertIn("file", text)
        self.assertIn("arrête-le", text)


if __name__ == "__main__":
    unittest.main()
