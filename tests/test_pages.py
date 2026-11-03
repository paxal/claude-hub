"""Liste lue par pages : les scripts distants donnent les conversations dans l'ordre de leurs
fichiers (dernier écrit en tête), page après page, sans trou ni doublon ; la première page
relue garde les pages suivantes déjà lues."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.window import NOT_READ, first_page_merge  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
BASE_MS = 1793000000000


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"


def transcript(text):
    return (line(type="user", message={"role": "user", "content": text}, cwd="/tmp/x",
                 timestamp="2026-10-06T08:00:00.000Z")
            + line(type="assistant", message={"content": [{"type": "text", "text": "ok"}],
                                              "stop_reason": "end_turn"},
                   timestamp="2026-10-06T08:00:01.000Z"))


def sid(n):
    return f"22222222-0000-0000-0000-{n:012d}"


# id -> date du fichier (ms) ; 3 et 4 écrits à la même milliseconde (départagés par l'id) ;
# 5 n'a pas de message (absente de la liste, mais compte dans sa page)
FILES = {sid(1): BASE_MS + 9000, sid(2): BASE_MS + 8000, sid(3): BASE_MS + 7000,
         sid(4): BASE_MS + 7000, sid(5): BASE_MS + 6000, sid(6): BASE_MS + 5000,
         sid(7): BASE_MS + 4000}


class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        for s, ms in FILES.items():
            p = proj / f"{s}.jsonl"
            p.write_text(line(type="summary", summary="vide") if s == sid(5) else transcript(f"conv {s[-1]}"))
            os.utime(p, ns=(ms * 1000000, ms * 1000000 + 123456))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, opts=None):
        args = ["list"] + ([json.dumps(opts)] if opts is not None else [])
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def helpers(self):
        yield "python3", "helper.py"
        if shutil.which("node"):
            yield "node", "helper.js"

    def test_pages_successives(self):
        for interp, script in self.helpers():
            with self.subTest(interp):
                first = self.run_helper(interp, script, {"count": 3})
                self.assertEqual([s["id"] for s in first["sessions"]], [sid(1), sid(2), sid(4)])
                self.assertEqual(first["more"], [BASE_MS + 7000, sid(4)])
                self.assertEqual(first["sessions"][0]["file_ms"], BASE_MS + 9000)
                second = self.run_helper(interp, script, {"count": 3, "after": first["more"]})
                # 5 compte dans la page sans y figurer
                self.assertEqual([s["id"] for s in second["sessions"]], [sid(3), sid(6)])
                self.assertEqual(second["more"], [BASE_MS + 5000, sid(6)])
                last = self.run_helper(interp, script, {"count": 3, "after": second["more"]})
                self.assertEqual([s["id"] for s in last["sessions"]], [sid(7)])
                self.assertIsNone(last["more"])

    def test_sans_options_tout(self):
        for interp, script in self.helpers():
            with self.subTest(interp):
                res = self.run_helper(interp, script)
                self.assertEqual(len(res["sessions"]), 6)
                self.assertIsNone(res["more"])

    def test_conversations_demandees(self):
        for interp, script in self.helpers():
            with self.subTest(interp):
                res = self.run_helper(interp, script, {"count": 1, "ids": [sid(7), "inconnue"]})
                self.assertEqual([s["id"] for s in res["sessions"]], [sid(1), sid(7)])
                self.assertEqual(res["more"], [BASE_MS + 9000, sid(1)])
                only = self.run_helper(interp, script, {"count": 0, "ids": [sid(6)]})
                self.assertEqual([s["id"] for s in only["sessions"]], [sid(6)])
                self.assertIsNone(only["more"])

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        for opts in (None, {"count": 2}, {"count": 2, "after": [BASE_MS + 7000, sid(4)]},
                     {"count": 0, "ids": [sid(2)]}):
            self.assertEqual(self.run_helper("python3", "helper.py", opts),
                             self.run_helper("node", "helper.js", opts), opts)


def conv(n, ms):
    return {"id": sid(n), "file_ms": ms}


class TestPremierePage(unittest.TestCase):
    OLD = [conv(1, 900), conv(2, 800), conv(3, 700), conv(4, 600), {"id": "cursor-1"}]

    def test_premiere_lecture(self):
        page = [conv(1, 900), conv(2, 800)]
        self.assertEqual(first_page_merge([], page, [800, sid(2)], NOT_READ), (page, [800, sid(2)]))

    def test_tout_tient_dans_la_page(self):
        page = [conv(1, 950)]
        self.assertEqual(first_page_merge(self.OLD, page, None, [600, sid(4)]), (page, None))

    def test_garde_les_pages_suivantes(self):
        # 3 a été écrite depuis : elle remonte dans la page ; 2 et 4 restent ; la suite ne change pas
        page = [conv(3, 1000), conv(1, 900)]
        sessions, more = first_page_merge(self.OLD, page, [900, sid(1)], [600, sid(4)])
        self.assertEqual([s["id"] for s in sessions], [sid(3), sid(1), sid(2), sid(4)])
        self.assertEqual(sessions[0]["file_ms"], 1000)
        self.assertEqual(more, [600, sid(4)])

    def test_tout_etait_lu(self):
        sessions, more = first_page_merge(self.OLD, [conv(1, 900)], [900, sid(1)], None)
        self.assertEqual(len(sessions), 4)
        self.assertIsNone(more)

    def test_fichiers_supprimes(self):
        # la page descend plus bas que la suite d'avant : on reprend après la page
        page = [conv(1, 900), conv(4, 600)]
        sessions, more = first_page_merge(self.OLD[:2], page, [600, sid(4)], [800, sid(2)])
        self.assertEqual([s["id"] for s in sessions], [sid(1), sid(4)])
        self.assertEqual(more, [600, sid(4)])


if __name__ == "__main__":
    unittest.main()
