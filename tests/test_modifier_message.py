"""Message modifié : l'historique ne garde que la branche que claude reprendra.

Modifier un message (`claude -p --resume <id> --resume-session-at <uuid>`, ou un retour en
arrière du terminal) ajoute un message de l'utilisateur rattaché à un enregistrement qui
a déjà une suite ; ce qui en descendait est abandonné.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"


def prompt(uid, parent, text):
    return line(type="user", uuid=uid, parentUuid=parent, message={"role": "user", "content": text})


def reply(uid, parent, text):
    return line(type="assistant", uuid=uid, parentUuid=parent,
                message={"content": [{"type": "text", "text": text}]})


def call(uid, parent, tid):
    return line(type="assistant", uuid=uid, parentUuid=parent, message={"content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "ls " + tid}}]})


def result(uid, parent, tid):
    return line(type="user", uuid=uid, parentUuid=parent, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]})


def end(uid, parent):  # fin de tour (pièce jointe ou durée) : parent du message suivant
    return line(type="system", subtype="turn_duration", uuid=uid, parentUuid=parent)


TRANSCRIPTS = {
    # u2 modifié deux fois depuis claude-hub : seul u2c compte
    "modifie": (prompt("u1", None, "un") + reply("a1", "u1", "réponse un") + end("e1", "a1")
                + prompt("u2", "e1", "deux") + reply("a2", "u2", "réponse deux") + end("e2", "a2")
                + prompt("u3", "e2", "trois") + reply("a3", "u3", "réponse trois") + end("e3", "a3")
                + prompt("u2b", "e1", "deux bis") + reply("a2b", "u2b", "réponse deux bis")
                + end("e2b", "a2b")
                + prompt("u2c", "e1", "deux ter") + reply("a2c", "u2c", "réponse deux ter")
                + end("e2c", "a2c") + prompt("u4", "e2c", "quatre")),
    # appels en parallèle : l'appel suivant et le résultat du premier ont le même parent
    "parallele": (prompt("u1", None, "liste") + call("c1", "u1", "t1") + call("c2", "c1", "t2")
                  + result("r1", "c1", "t1") + result("r2", "r1", "t2")
                  + reply("a1", "r2", "fini") + end("e1", "a1") + prompt("u2", "e1", "merci")),
    # interruption pendant un outil : pas un retour en arrière
    "interrompu": (prompt("u1", None, "lance") + call("c1", "u1", "t1") + result("r1", "c1", "t1")
                   + prompt("i1", "c1", "[Request interrupted by user for tool use]")
                   + reply("a1", "r1", "après")),
    # message d'avant /compact modifié : la compaction fait partie de la suite abandonnée
    "compacte": (prompt("u1", None, "un") + reply("a1", "u1", "réponse un") + end("e1", "a1")
                 + prompt("u2", "e1", "deux") + reply("a2", "u2", "réponse deux")
                 + line(type="system", subtype="compact_boundary", uuid="b1", parentUuid=None,
                        logicalParentUuid="a2")
                 + line(type="user", uuid="s1", parentUuid="b1", isCompactSummary=True,
                        message={"role": "user", "content": "Résumé"})
                 + prompt("u3", "s1", "trois") + reply("a3", "u3", "réponse trois")
                 + prompt("u2b", "e1", "deux bis") + reply("a2b", "u2b", "réponse deux bis")),
}

EXPECTED = {
    "modifie": ["un", "réponse un", "deux ter", "réponse deux ter", "quatre"],
    "parallele": ["liste", "ls t1", "ls t2", "fini", "merci"],
    "interrompu": ["lance", "ls t1", "⏹", "après"],
    "compacte": ["un", "réponse un", "deux bis", "réponse deux bis"],
}


class TestModifierMessage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        for sid, text in TRANSCRIPTS.items():
            (proj / f"{sid}.jsonl").write_text(text)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def history(self, interp, script, sid):
        r = subprocess.run([interp, "-", "history", sid, "50"], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_branche_gardee(self):
        for sid, expected in EXPECTED.items():
            res = self.history("python3", "helper.py", sid)
            texts = [("⏹" if (it.get("text") or "").startswith("[Request interrupted") else it.get("text"))
                     or it["input"]["command"] for it in res["items"]]
            self.assertEqual(texts, expected, sid)
            self.assertEqual(res["total"], len(expected), sid)
            self.assertEqual(res, self.history("node", "helper.js", sid), sid)

    def test_point_de_reprise(self):
        # le message modifié repart du même point que l'original
        items = self.history("python3", "helper.py", "modifie")["items"]
        self.assertEqual(items[2]["parent"], "e1")
        self.assertEqual(items[4]["parent"], "e2c")


if __name__ == "__main__":
    unittest.main()
