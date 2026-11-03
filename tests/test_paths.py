"""Tests des chemins cliquables et de la commande `read` des scripts distants."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.render import (copied_link, file_href, label_links, label_target, md_inline,  # noqa: E402
                               parse_file_href, path_link)
from claude_hub.viewer import resolve  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"


class TestPathLinks(unittest.TestCase):
    def test_reconnus(self):
        for text, path, line in [("/home/a/plan.md", "/home/a/plan.md", None),
                                 ("~/.bashrc", "~/.bashrc", None),
                                 ("src/a.py:12", "src/a.py", 12),
                                 ("src/a.py#L3-L5", "src/a.py", 3),
                                 ("../x/y", "../x/y", None),
                                 ("/etc/hosts", "/etc/hosts", None)]:
            href = path_link(text)
            self.assertIsNotNone(href, text)
            self.assertEqual(parse_file_href(href)[:2], (path, line), text)

    def test_faux_positifs(self):
        for text in ["et/ou", "1/2", "km/h", "e.g", "v1.2", "10:30", "/plan", "/tmp",
                     "stockly/bazel", "a.b"]:
            self.assertIsNone(path_link(text), text)

    def test_nom_seul_dans_le_code(self):
        self.assertIsNone(path_link("render.py"))
        self.assertIsNotNone(path_link("render.py", code=True))
        self.assertIsNone(path_link("os.path", code=True))
        self.assertIn('href="hub-file:src/foo.py"', md_inline("`@src/foo.py`"))

    def test_texte(self):
        s = md_inline("voir /tmp/x/plan.md. Et https://a.b/c/d.py et mon_fichier")
        self.assertIn('<a href="hub-file:/tmp/x/plan.md">/tmp/x/plan.md</a>.', s)
        self.assertIn('<a href="https://a.b/c/d.py">', s)
        self.assertNotIn("hub-file:/c/d.py", s)
        self.assertIn("mon_fichier", s)  # pas d'italique dans un mot

    def test_gras_dans_un_mot(self):
        s = "Write, mcp__postgres__execute_sql, mcp__sentry__search"
        self.assertEqual(md_inline(s), s)  # __ dans un mot : pas de gras
        self.assertEqual(md_inline("un __mot__, x"), "un <b>mot</b>, x")
        self.assertEqual(md_inline("a**b**c"), "a<b>b</b>c")  # ** dans un mot : gras

    def test_url_en_gras(self):
        self.assertEqual(md_inline("**https://a.b/c-1**"),
                         '<b><a href="https://a.b/c-1">https://a.b/c-1</a></b>')
        self.assertIn('<i><a href="https://a.b/c">', md_inline("*https://a.b/c*"))
        self.assertIn('href="https://a.b/c**d"', md_inline("https://a.b/c**d"))

    def test_liens_markdown(self):
        self.assertIn('href="hub-file:docs/a.md#L3"', md_inline("[plan](docs/a.md:3)"))
        self.assertIn('href="hub-file:/x/y.md"', md_inline("[plan](file:///x/y.md)"))
        self.assertIn('href="https://x.y"', md_inline("[web](https://x.y)"))
        self.assertIn('href="#a"', md_inline("[ancre](#a)"))

    def test_aller_retour(self):
        href = file_href("/a b/c.py", 3, 5)
        self.assertEqual(parse_file_href(href), ("/a b/c.py", 3, 5))

    def test_lien_copie(self):
        # « Copier l'adresse du lien » : l'adresse web telle quelle, un fichier comme il est cité
        self.assertEqual(copied_link("https://x.y/p?a=1#b"), "https://x.y/p?a=1#b")
        self.assertEqual(copied_link(file_href("/a b/c.py")), "/a b/c.py")
        self.assertEqual(copied_link(file_href("src/a.py", 12)), "src/a.py:12")
        self.assertEqual(copied_link(file_href("src/a.py", 3, 5)), "src/a.py:3-5")

    def test_liens_des_labels(self):
        # Gtk.Label copie l'adresse telle quelle : celle d'un fichier est son chemin cité
        lien = label_links(md_inline("voir `src/a.py:12` et [doc](docs/b.md) ou https://x.y/z"))
        self.assertIn('<a href="src/a.py:12">', lien)
        self.assertIn('<a href="docs/b.md">', lien)
        self.assertIn('<a href="https://x.y/z">', lien)
        self.assertNotIn("hub-file:", lien)
        self.assertIn('<a href="/a b/c&amp;d.py:3-5">',
                      label_links(f'<a href="{file_href("/a b/c&d.py", 3, 5)}">c</a>'))
        # relus par on_link
        self.assertEqual(label_target("src/a.py:12"), ("src/a.py", 12, None))
        self.assertEqual(label_target("/a b/c.py:3-5"), ("/a b/c.py", 3, 5))
        self.assertEqual(label_target("~/.bashrc"), ("~/.bashrc", None, None))
        self.assertEqual(label_target(file_href("x.py", 2)), ("x.py", 2, None))
        for uri in ("https://x.y/z", "mailto:a@b.c", "#ancre"):
            self.assertIsNone(label_target(uri), uri)
        # chemins qui se reliraient autrement : adresse hub-file: gardée
        for path in ("a:b/c.py", "notes:12", "#x.md"):
            markup = f'<a href="{file_href(path)}">x</a>'
            self.assertEqual(label_links(markup), markup, path)

    def test_lexer(self):
        from claude_hub.render import lang_for, lex
        if lex is None:
            self.skipTest("pygments absent")
        self.assertEqual(lang_for("/x/a.py"), "python")
        self.assertEqual(lang_for("/x/Makefile"), "make")
        self.assertEqual(lang_for("/x/script", "#!/usr/bin/env python3\nimport sys\n"), "python")
        self.assertEqual(lang_for("/x/notes", "du texte"), "text")

    def test_resolve(self):
        self.assertEqual(resolve("src/a.py", "/repo"), "/repo/src/a.py")
        self.assertEqual(resolve("../b.md", "/repo/docs"), "/repo/b.md")
        self.assertEqual(resolve("~/x", "/repo"), "~/x")
        self.assertEqual(resolve("/abs", "/repo"), "/abs")
        self.assertEqual(resolve("a.py", None), "a.py")


class TestRead(unittest.TestCase):
    """Les deux scripts distants doivent donner la même sortie (erreurs comprises)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="claude-hub-read-")
        d = Path(cls.dir)
        (d / "t.txt").write_text("a\nb\n")
        (d / "u.md").write_text("é\n")
        (d / "bin").write_bytes(b"x\0y")
        (d / "big.txt").write_text("ligne\n" * 200000)
        (d / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (d / "i.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        (d / "sub").mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def run_helper(self, interp, script, path):
        r = subprocess.run([interp, "-", "read", path], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, cwd=self.dir, check=True)
        return json.loads(r.stdout)

    def test_sorties(self):
        cases = {"t.txt": "text", "u.md": "text", "bin": "error", "big.txt": "text",
                 "img.png": "image", "i.svg": "image", "sub": "error", "absent": "error"}
        for name, key in cases.items():
            res = self.run_helper("python3", "helper.py", name)
            self.assertIn(key, res, name)
            self.assertEqual(res["path"], os.path.join(self.dir, name))
        big = self.run_helper("python3", "helper.py", "big.txt")
        self.assertTrue(big["truncated"])
        self.assertTrue(big["text"].endswith("ligne\n"))

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        for name in ["t.txt", "u.md", "bin", "big.txt", "img.png", "i.svg", "sub", "absent",
                     "/dev/null"]:
            self.assertEqual(self.run_helper("python3", "helper.py", name),
                             self.run_helper("node", "helper.js", name), name)


if __name__ == "__main__":
    unittest.main()
