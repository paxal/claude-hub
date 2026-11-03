"""Conversation ouverte dans un terminal sans Remote Control : commande « stat » des scripts
distants (Remote Control du claude en cours, ce qu'il attend), avertissement de l'en-tête et
carte du fil quand Claude attend une réponse dans son terminal."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "src/claude_hub/remote"
sys.path.insert(0, str(ROOT / "src"))

from claude_hub.hosts import CLOUD  # noqa: E402
from claude_hub.remotecontrol import help_texts, wait_texts  # noqa: E402
from claude_hub.window import Hub  # noqa: E402

PROJ = "-tmp-x"


def line(**r):
    return json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"  # comme claude


QUESTION = line(type="user", timestamp="2026-11-03T10:21:56.000Z", isSidechain=False,
                message={"role": "user", "content": "On merge ?"})
OLD_BRIDGE = line(type="bridge-session", sessionId="x", bridgeSessionId="cse_ancien", lastSequenceNum=0)


class StatTest(unittest.TestCase):
    """« stat » : la session Remote Control du claude en cours vient de son fichier de session
    (bridgeSessionId) ; la transcription garde celles des lancements précédents."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="claude-hub-test-")
        proj = Path(cls.home, ".claude/projects", PROJ)
        proj.mkdir(parents=True)
        Path(cls.home, ".claude/sessions").mkdir()
        cls.sids = {}

        def session(name, body, sess=None):
            sid = f"{len(cls.sids) + 1:08d}-0000-0000-0000-000000000000"
            cls.sids[name] = sid
            (proj / f"{sid}.jsonl").write_text(QUESTION + body)
            if sess is not None:
                Path(cls.home, f".claude/sessions/{len(cls.sids)}.json").write_text(json.dumps(
                    {"pid": os.getpid(), "sessionId": sid, "startedAt": 1793699833007, **sess}))

        session("reliee", OLD_BRIDGE, {"status": "busy", "bridgeSessionId": "session_actuel"})
        # relancée sans Remote Control : l'ancienne entrée de la transcription ne compte plus
        session("sans_rc", OLD_BRIDGE, {"status": "waiting", "waitingFor": "input needed"})
        session("autorisation", "", {"status": "waiting", "waitingFor": "permission prompt"})
        session("invalide", "", {"status": "waiting", "waitingFor": 3, "bridgeSessionId": ""})
        session("arretee", OLD_BRIDGE)  # pas de claude en cours : celle de la transcription

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def stat(self, name):
        """Sortie de « stat » avec helper.py et helper.js, qui doivent être identiques."""
        outs = []
        for interp, script in (("python3", "helper.py"), ("node", "helper.js")):
            r = subprocess.run([interp, "-", "stat", self.sids[name]], input=(REMOTE / script).read_text(),
                               capture_output=True, text=True, check=True,
                               env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
            outs.append(json.loads(r.stdout))
        self.assertEqual(outs[0], outs[1], name)
        return outs[0]["live"], outs[0]["bridge"], outs[0]["waiting_for"]

    def test_reliee(self):
        self.assertEqual(self.stat("reliee"), ("busy", "session_actuel", None))

    def test_sans_remote_control(self):
        self.assertEqual(self.stat("sans_rc"), ("waiting", None, "input needed"))
        self.assertEqual(self.stat("autorisation"), ("waiting", None, "permission prompt"))

    def test_valeurs_invalides(self):
        self.assertEqual(self.stat("invalide"), ("waiting", None, None))

    def test_arretee(self):
        self.assertEqual(self.stat("arretee"), (None, "cse_ancien", None))


def fake_hub(**kw):
    return types.SimpleNamespace(streams={}, pending={}, wait_card=None, **kw)


def terminal(**kw):
    return {"id": "s1", "host": "local", "live": "waiting", **kw}


class WindowTest(unittest.TestCase):
    def test_note_stat(self):
        s = terminal()
        Hub.note_stat(None, s, {"live": "waiting", "bridge": None, "waiting_for": "input needed"})
        self.assertEqual((s["linked"], s["waiting_for"]), (False, "input needed"))
        Hub.note_stat(None, s, {"live": "busy", "bridge": "session_x", "waiting_for": None})
        self.assertEqual((s["linked"], s["waiting_for"]), (True, None))
        Hub.note_stat(None, s, {"live": None, "bridge": "cse_ancien"})  # claude arrêté : inconnu
        self.assertNotIn("linked", s)
        self.assertNotIn("waiting_for", s)

    def test_sans_remote_control(self):
        hub = fake_hub(settings={"launched": {}})
        hub.is_streaming = lambda s: Hub.is_streaming(hub, s)
        hub.orphan = lambda s: Hub.orphan(hub, s)
        check = lambda s: Hub.without_remote_control(hub, s)  # noqa: E731
        self.assertTrue(check(terminal(linked=False)))
        self.assertTrue(check(terminal(linked=False, live="idle", host="cerberus")))
        self.assertFalse(check(terminal(linked=True)))
        self.assertFalse(check(terminal()))  # pas encore relu par « stat »
        self.assertFalse(check(terminal(linked=False, live=None)))  # claude arrêté
        self.assertFalse(check(terminal(linked=False, source="claude-cloud", host=CLOUD)))
        self.assertFalse(check(terminal(linked=False, source="cursor")))
        self.assertFalse(check(terminal(linked=False, new=True)))
        self.assertFalse(check(None))
        s = terminal(linked=False)
        hub.streams["s1"] = (object(), s)  # claude -p lancé d'ici
        self.assertFalse(check(s))
        del hub.streams["s1"]
        hub.settings["launched"]["s1"] = "local"  # lancé d'ici avant un redémarrage : pas de terminal
        self.assertFalse(check(s))

    def test_demande_du_terminal(self):
        hub = fake_hub()
        entry = lambda s: Hub.request_entry(hub, s)["text"]  # noqa: E731
        self.assertEqual(entry(terminal()), "Claude attend une réponse dans son terminal.")
        self.assertEqual(entry(terminal(waiting_for="permission prompt")),
                         "Claude demande une autorisation dans son terminal.")
        self.assertEqual(entry(terminal(source="claude-cloud")), "Claude attend une réponse sur claude.ai.")


class TextsTest(unittest.TestCase):
    def test_question_absente_du_fil(self):
        title, text, hint = wait_texts("input needed", linked=False, shown=False)
        self.assertEqual(title, "Claude attend ta réponse dans son terminal")
        self.assertIn("question ou présente un plan", text)
        self.assertIn("n'est visible que dans le terminal", text)
        self.assertIn("Sans Remote Control", text)
        self.assertIn("/remote-control", hint)

    def test_question_depliee(self):
        _title, text, hint = wait_texts("input needed", linked=False, shown=True)
        self.assertNotIn("visible que dans le terminal", text)
        self.assertIsNotNone(hint)

    def test_autorisation(self):
        _title, text, _hint = wait_texts("permission prompt", linked=False, shown=False)
        self.assertIn("autorisation", text)
        self.assertNotIn("visible que dans le terminal", text)

    def test_reliee(self):
        """Reliée, mais rien à quoi répondre d'ici (API indisponible…) : pas de conseil."""
        _title, text, hint = wait_texts(None, linked=True, shown=False)
        self.assertIn("Réponds-y dans le terminal", text)
        self.assertNotIn("Sans Remote Control", text)
        self.assertIsNone(hint)

    def test_explication(self):
        title, paragraphs = help_texts("local")
        self.assertEqual(title, "Sans Remote Control")
        self.assertIn("sur ce PC.", paragraphs[-1])
        self.assertIn("remoteControlAtStartup", paragraphs[-1])
        self.assertIn("sur cerberus.stockly.tech.", help_texts("cerberus.stockly.tech")[1][-1])
        self.assertIn("/remote-control", "\n".join(paragraphs))


if __name__ == "__main__":
    unittest.main()
