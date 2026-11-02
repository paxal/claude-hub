"""Tests des notes : scripts distants (note, notes, index de list), écriture par sh
(chemin d'un hôte SSH, joué en local), file de lecture et d'écriture, extraits."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub import notes  # noqa: E402
from claude_hub.cursor import norm_text  # noqa: E402
from claude_hub.gtk import GLib  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "33333333-4444-5555-6666-777777777777"
NOTE = "# Suivi\n\n- [ ] relire `src/app.py:42`\r\n- déployer sur **cerberus** 🚀\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.config = Path(self.home, "conf")
        self.env = {"HOME": self.home, "XDG_CONFIG_HOME": str(self.config), "PATH": "/usr/bin:/bin"}

    def tearDown(self):
        shutil.rmtree(self.home)

    def helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True, env=self.env)
        return json.loads(r.stdout)

    def notes_dir(self):
        return self.config / "claude-hub" / "notes"

    def put(self, sid, text):
        self.notes_dir().mkdir(parents=True, exist_ok=True)
        (self.notes_dir() / f"{sid}.md").write_bytes(text.encode())


class TestScriptsDistants(Base):
    def setUp(self):
        super().setUp()
        self.put(SID, NOTE)
        self.put("session_01ABC", "x" * 1500)
        self.put("vide", "  \n")                      # note vide : ignorée
        self.notes_dir().joinpath(f".{SID}.md.tmp").write_text("en cours")  # écriture en cours
        self.notes_dir().joinpath("autre.txt").write_text("pas une note")

    def test_note(self):
        self.assertEqual(self.helper("python3", "helper.py", "note", SID), {"text": NOTE})  # \r\n gardé

    def test_note_absente(self):
        self.assertEqual(self.helper("python3", "helper.py", "note", "inconnue"), {"text": None})

    def test_identifiant_invalide(self):
        for sid in ("../settings", "a/b", "", ".cache", "a\n"):
            self.assertEqual(self.helper("python3", "helper.py", "note", sid),
                             {"error": "identifiant invalide"}, sid)

    def test_index(self):
        idx = self.helper("python3", "helper.py", "notes")
        self.assertEqual(sorted(idx), [SID, "session_01ABC"])
        self.assertEqual(idx[SID], NOTE)
        self.assertEqual(len(idx["session_01ABC"]), notes.EXCERPT)

    def test_list_donne_l_index(self):
        res = self.helper("python3", "helper.py", "list")
        self.assertEqual(res["sessions"], [])
        self.assertEqual(sorted(res["notes"]), [SID, "session_01ABC"])

    def test_sans_dossier_de_notes(self):
        shutil.rmtree(self.notes_dir())
        self.assertEqual(self.helper("python3", "helper.py", "notes"), {})
        self.assertEqual(self.helper("python3", "helper.py", "note", SID), {"text": None})

    def test_xdg_config_home_vide(self):
        """XDG_CONFIG_HOME vide vaut ~/.config, comme pour sh (${XDG_CONFIG_HOME:-…})."""
        self.env["XDG_CONFIG_HOME"] = ""
        d = Path(self.home, ".config/claude-hub/notes")
        d.mkdir(parents=True)
        (d / "x.md").write_text("ici")
        self.assertEqual(self.helper("python3", "helper.py", "notes"), {"x": "ici"})

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        for args in (["note", SID], ["note", "inconnue"], ["note", "../x"], ["notes"], ["list"]):
            self.assertEqual(self.helper("python3", "helper.py", *args),
                             self.helper("node", "helper.js", *args), args)


class TestEcriture(Base):
    """write_note et read_note, sur ce PC et par le chemin d'un hôte SSH (sh et scripts
    distants joués en local)."""

    def setUp(self):
        super().setUp()
        self.patches = [mock.patch.object(notes, "NOTES_DIR", self.notes_dir()),
                        mock.patch.object(notes, "host_cmd", lambda host, argv: argv),
                        mock.patch.object(notes, "run_helper", lambda host, *a: self.helper(
                            "python3", "helper.py", *a)),
                        mock.patch.dict(os.environ, {"HOME": self.home,
                                                     "XDG_CONFIG_HOME": str(self.config)})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def test_hotes(self):
        self.assertEqual(notes.store_of("cloud"), "local")
        self.assertEqual(notes.store_of("rpi4"), "rpi4")
        self.assertEqual(notes.where_text("cloud"), "sur ce PC")
        self.assertEqual(notes.where_text("cerberus.stockly.tech"), "sur cerberus.stockly.tech")

    def test_aller_retour(self):
        for host in ("local", "cerberus", "cloud"):
            with self.subTest(host=host):
                self.assertEqual(notes.read_note(host, SID), "")
                notes.write_note(host, SID, NOTE)
                self.assertEqual((self.notes_dir() / f"{SID}.md").read_bytes(), NOTE.encode())
                self.assertEqual(notes.read_note(host, SID), NOTE)
                notes.write_note(host, SID, "deuxième version")
                self.assertEqual(notes.read_note(host, SID), "deuxième version")
                self.assertEqual(notes.host_index(host), {SID: "deuxième version"})
                notes.write_note(host, SID, "  \n")  # vidée : supprimée
                self.assertFalse((self.notes_dir() / f"{SID}.md").exists())
                self.assertEqual(notes.host_index(host), {})
                self.assertEqual(sorted(os.listdir(self.notes_dir())), [])  # pas de fichier temporaire

    def test_index_donne_par_list(self):
        self.assertEqual(notes.host_index("cerberus", {"notes": {"a": "b"}}), {"a": "b"})

    def test_identifiant_refuse(self):
        for host in ("local", "cerberus"):
            with self.assertRaises(ValueError):
                notes.write_note(host, "../../.claude/settings", "x")
            with self.assertRaises(ValueError):
                notes.read_note(host, "a/b")

    def test_erreur_de_lecture(self):
        self.put(SID, "x")
        (self.notes_dir() / f"{SID}.md").chmod(0)
        try:
            if os.access(self.notes_dir() / f"{SID}.md", os.R_OK):
                self.skipTest("lancé en root")
            with self.assertRaises(RuntimeError):
                notes.read_note("cerberus", SID)
        finally:
            (self.notes_dir() / f"{SID}.md").chmod(0o644)

    def test_file_dans_l_ordre(self):
        """Les enregistrements se suivent dans l'ordre ; seul le dernier d'une note est
        écrit ; une lecture demandée après voit le texte enregistré."""
        q, got, written = notes.NoteQueue(), [], []
        real = notes.write_note

        def slow_write(host, sid, text):
            time.sleep(0.05)
            written.append(text)
            real(host, sid, text)
        with mock.patch.object(notes, "write_note", slow_write):
            for i in range(5):
                q.save("cerberus", SID, f"version {i}", lambda t: got.append(("saved", t)))
            q.load("cerberus", SID, lambda t: got.append(("read", t)))
            q.wait(10)
            ctx = GLib.MainContext.default()
            deadline = time.time() + 10
            while len(got) < 2 and time.time() < deadline:
                ctx.iteration(False)
                time.sleep(0.01)
        self.assertLess(len(written), 5)  # les versions dépassées sont sautées
        self.assertEqual(written[-1], "version 4")
        self.assertEqual(got, [("saved", "version 4"), ("read", "version 4")])


class TestExtraits(unittest.TestCase):
    def test_apercu(self):
        self.assertEqual(notes.note_preview("\n\n# Titre\nligne\n"), "# Titre\nligne")
        self.assertEqual(notes.note_preview("\n".join(map(str, range(20)))).count("\n"), 7)
        self.assertTrue(notes.note_preview("x" * 1000).endswith("…"))

    def test_extrait_de_recherche(self):
        text = "# Déploiement\n" + "a" * 80 + " penser à relancer le Hub\n"
        self.assertEqual(notes.note_snippet(text, norm_text("deploiement")), "# Déploiement")
        line = notes.note_snippet(text, norm_text("RELANCER"))
        self.assertTrue(line.startswith("…") and line.endswith("relancer le Hub"), line)
        self.assertIsNone(notes.note_snippet(text, "absent"))


if __name__ == "__main__":
    unittest.main()
