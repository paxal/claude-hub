"""Relecture du fil : seuls les éléments nouveaux ou changés sont à refaire (feedsync.py)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.feedsync import changed_from, same_item, unread  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"


def items(*texts):
    return [{"role": "assistant", "text": t} for t in texts]


class TestChangedFrom(unittest.TestCase):
    def test_rien_de_neuf(self):
        shown = items("a", "b", "c")
        self.assertEqual(changed_from(shown, 0, items("a", "b", "c"), 0), 3)

    def test_ajout_a_la_fin(self):
        shown = items("a", "b")
        self.assertEqual(changed_from(shown, 0, items("a", "b", "c", "d"), 0), 2)

    def test_dernier_element_change(self):
        """Commande « ! » du terminal : sa sortie arrive dans l'enregistrement suivant."""
        shown = items("a") + [{"role": "bash", "command": "ls"}]
        new = items("a") + [{"role": "bash", "command": "ls", "stdout": "x"}]
        self.assertEqual(changed_from(shown, 0, new, 0), 1)

    def test_fenetre_qui_avance(self):
        """300 derniers éléments : la lecture suivante commence plus loin."""
        shown = items(*"abcde")  # index 10 à 14
        new = items(*"cdefg")    # index 12 à 16
        self.assertEqual(changed_from(shown, 10, new, 12), 15)

    def test_changement_dans_le_recouvrement(self):
        shown = items(*"abcde")
        new = items("c", "X", "e", "f")
        self.assertEqual(changed_from(shown, 10, new, 12), 13)

    def test_a_reconstruire(self):
        self.assertIsNone(changed_from([], 0, items("a"), 0))            # rien d'affiché
        self.assertIsNone(changed_from(items("a", "b"), 5, items("a"), 3))  # raccourcie
        self.assertIsNone(changed_from(items("a", "b"), 0, items("z"), 9))  # trou

    def test_transcription_raccourcie_a_la_fin(self):
        self.assertEqual(changed_from(items("a", "b", "c"), 0, items("a", "b"), 0), 2)

    def test_images_comptees_pas_leur_contenu(self):
        """Le budget des images peut retirer le contenu d'une ancienne image."""
        a = {"role": "user", "text": "t", "images": [{"type": "image/png", "data": "xx"}]}
        b = {"role": "user", "text": "t", "images": [{"type": "image/png", "omitted": True}]}
        self.assertTrue(same_item(a, b))
        self.assertFalse(same_item(a, dict(a, images=[])))
        self.assertFalse(same_item(a, dict(a, ts=1)))


class TestNonLus(unittest.TestCase):
    """Messages transmis pendant la réponse : claude ne les écrit qu'en les lisant."""

    def user(self, text, ts):
        return {"role": "user", "text": text, "images": [], "ts": ts}

    def test_pas_encore_lu(self):
        sent = [{"text": "Pourquoi ?", "ts": 1000.5}]
        self.assertEqual(unread(sent, [self.user("Fais un plan", 900)]), sent)

    def test_lu(self):
        sent = [{"text": "Pourquoi ?", "ts": 1000.5}]
        lu = dict(self.user("Pourquoi ?", 1200), queued=True)
        self.assertEqual(unread(sent, [self.user("Fais un plan", 900), lu]), [])

    def test_meme_texte_envoye_avant(self):
        """Un « ok » plus ancien ne vaut pas lecture du nouveau."""
        sent = [{"text": "ok", "ts": 1000}]
        self.assertEqual(unread(sent, [self.user("ok", 500)]), sent)

    def test_horloges_decalees(self):
        """Hôte un peu en retard sur ce PC : toléré."""
        self.assertEqual(unread([{"text": "ok", "ts": 1000}], [self.user("ok", 990)]), [])

    def test_un_message_par_envoi(self):
        sent = [{"text": "ok", "ts": 1000}, {"text": "ok", "ts": 1001}]
        self.assertEqual(unread(sent, [self.user("ok", 1100)]), sent[1:])


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"


class TestTranscription(unittest.TestCase):
    """Deux lectures d'une transcription qui grandit, par les deux scripts distants."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        proj = Path(self.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        self.path = proj / f"{SID}.jsonl"
        self.path.write_text(
            line(type="user", uuid="u1", timestamp="2026-11-02T10:00:00Z",
                 message={"content": "Bonjour"})
            + line(type="assistant", uuid="a1", timestamp="2026-11-02T10:00:05Z",
                   message={"model": "m", "content": [
                       {"type": "text", "text": "Je regarde."},
                       {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]})
            + line(type="user", uuid="u2", timestamp="2026-11-02T10:00:06Z",
                   message={"content": "<bash-input>pwd</bash-input>"}))

    def tearDown(self):
        shutil.rmtree(self.home)

    def history(self, cmd, limit=300):
        script = REMOTE / ("helper.py" if cmd[0] == "python3" else "helper.js")
        out = subprocess.run([*cmd, "-", "history", SID, str(limit)], input=script.read_text(),
                             capture_output=True, text=True, check=True,
                             env=dict(os.environ, HOME=self.home))
        res = json.loads(out.stdout)
        return res["items"], res["total"] - len(res["items"])

    def grow(self):
        with open(self.path, "a") as f:
            f.write(line(type="user", uuid="u3", timestamp="2026-11-02T10:00:07Z",
                         message={"content": "<bash-stdout>/tmp/x</bash-stdout>"})
                    + line(type="assistant", uuid="a2", timestamp="2026-11-02T10:00:09Z",
                           message={"model": "m", "content": [{"type": "text", "text": "Fini."}]}))

    def check(self, cmd):
        before, start = self.history(cmd)
        self.grow()
        after, start2 = self.history(cmd)
        at = changed_from(before, start, after, start2)
        # la commande « ! » reçoit sa sortie : refaite, suivie de la réponse
        self.assertEqual(at, len(before) - 1)
        self.assertEqual(before[-1]["role"], "bash")
        self.assertEqual(after[at]["stdout"], "/tmp/x")
        self.assertEqual(after[-1]["text"], "Fini.")
        # fenêtre limitée aux 2 derniers éléments : même résultat, aligné sur l'index
        before, start = self.history(cmd, 2)
        after, start2 = self.history(cmd, 2)
        self.assertEqual(changed_from(before, start, after, start2), start + len(before))

    def test_python(self):
        self.check(["python3"])

    def test_node(self):
        self.check(["node"])


if __name__ == "__main__":
    unittest.main()
