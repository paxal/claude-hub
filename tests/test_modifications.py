"""Fichiers modifiés (commande « edits » des scripts distants, regroupement par fichier,
diffs) et taille du contexte lue dans la transcription (commande « history »)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = ROOT / "src/claude_hub/remote"
sys.path.insert(0, str(ROOT / "src"))
SID = "aaaaaaaa-2222-3333-4444-555555555555"

from claude_hub.changes import (  # noqa: E402
    edit_hunks, edits_from_items, first_line, full_diff, group_files, hunk_stats, hunks_text, input_hunks,
)


def line(**r):
    return json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"


def use(t, tid, name, inp, side=False, usage=None, model="claude-opus-5-5"):
    message = {"role": "assistant", "model": model,
               "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}
    if usage:
        message["usage"] = usage
    return line(type="assistant", isSidechain=side, timestamp=f"2026-11-02T10:00:{t:02d}.250Z",
                message=message)


def result(t, tid, side=False, error=False, res=None):
    r = {"type": "user", "isSidechain": side, "timestamp": f"2026-11-02T10:00:{t:02d}.500Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                  "content": "ok", "is_error": error}]}}
    if res is not None:
        r["toolUseResult"] = res
    return line(**r)


PATCH = [{"oldStart": 10, "oldLines": 3, "newStart": 10, "newLines": 4,
          "lines": [" a", "-b", "+B", "+B2", " c"]}]


class TestHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        sub = proj / SID / "subagents"
        sub.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", timestamp="2026-11-02T10:00:00.000Z",
                 message={"role": "user", "content": "modifie ça é"})
            + use(1, "t1", "Edit", {"file_path": "/p/a.py", "old_string": "b", "new_string": "B\nB2"},
                  usage={"input_tokens": 5, "cache_read_input_tokens": 1000,
                         "cache_creation_input_tokens": 200, "output_tokens": 40})
            + result(2, "t1", res={"filePath": "/p/a.py", "structuredPatch": PATCH})
            + use(3, "t2", "Edit", {"file_path": "/p/a.py", "old_string": "zz", "new_string": "y"})
            + result(4, "t2", error=True)
            + use(5, "t3", "Write", {"file_path": "/p/neuf.txt", "content": "un\ndeux"})
            + result(6, "t3", res={"type": "create", "filePath": "/p/neuf.txt", "structuredPatch": []})
            + use(7, "t4", "Read", {"file_path": "/p/a.py"})
            + use(8, "t5", "MultiEdit", {"file_path": "/p/b.py", "edits": [
                {"old_string": "x", "new_string": "y"}, {"old_string": "1", "new_string": "2"}]},
                usage={"input_tokens": 7, "output_tokens": 3}))
        (sub / "agent-a9.jsonl").write_text(
            use(9, "s1", "Edit", {"file_path": "/p/c.py", "old_string": "o", "new_string": "n"}, side=True)
            + result(9, "s1", side=True))
        # compactée : le contexte n'est plus connu jusqu'au message suivant de Claude
        (proj / "compacte.jsonl").write_text(
            use(1, "u1", "Read", {"file_path": "/x"}, usage={"input_tokens": 900})
            + line(type="user", isCompactSummary=True, message={"role": "user", "content": "résumé"}))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_edits(self):
        res = self.run_helper("python3", "helper.py", "edits", SID)
        edits = res["edits"]
        self.assertEqual([e["id"] for e in edits], ["t1", "t2", "t3", "t5", "s1"])  # pas le Read
        t1, t2, t3, t5, s1 = edits
        self.assertEqual((t1["status"], t1["patch"], t1["path"]), ("ok", PATCH, "/p/a.py"))
        self.assertEqual(t2["status"], "error")
        self.assertEqual((t3["created"], t3["patch"]), (True, None))  # structuredPatch vide
        self.assertEqual((t5["status"], len(t5["input"]["edits"])), ("pending", 2))
        self.assertEqual((s1["agent"], s1["status"]), ("a9", "ok"))
        self.assertAlmostEqual(t1["time"], 1793613601.25)

    def test_contexte(self):
        res = self.run_helper("python3", "helper.py", "history", SID, "50")
        self.assertEqual(res["context"], {"tokens": 10, "model": "claude-opus-5-5"})  # dernier message
        res = self.run_helper("python3", "helper.py", "history", "compacte", "50")
        self.assertIsNone(res["context"])

    def test_python_et_node(self):
        for cmd in (("edits", SID), ("history", SID, "50"), ("history", "compacte", "50"),
                    ("edits", "inconnue")):
            self.assertEqual(self.run_helper("python3", "helper.py", *cmd),
                             self.run_helper("node", "helper.js", *cmd), cmd)


class TestDiffs(unittest.TestCase):
    def test_edit_recalcule(self):
        hunks = input_hunks("Edit", {"old_string": "a\nb\nc", "new_string": "a\nB\nc"})
        self.assertEqual(hunks[0]["lines"], [" a", "-b", "+B", " c"])
        self.assertEqual(hunk_stats(hunks), (1, 1))
        self.assertEqual(hunks_text(hunks), " a\n-b\n+B\n c")
        two = input_hunks("Edit", {"old_string": "a\n1\n2\n3\n4\n5\n6\n7\nb", "new_string": "A\n1\n2\n3\n4\n5\n6\n7\nB"})
        self.assertEqual(hunks_text(two).count("@@"), 1)  # entre les deux morceaux seulement

    def test_write_et_multiedit(self):
        self.assertEqual(hunk_stats(input_hunks("Write", {"content": "un\ndeux\ntrois"})), (3, 0))
        multi = {"edits": [{"old_string": "x", "new_string": "y"}, {"old_string": "1", "new_string": "2"}]}
        self.assertEqual(hunk_stats(input_hunks("MultiEdit", multi)), (2, 2))
        self.assertEqual(input_hunks("Edit", {"old_string": "pareil", "new_string": "pareil"}), [])
        self.assertEqual(input_hunks("Read", {"file_path": "x"}), [])

    def test_patch_de_claude(self):
        hunks = edit_hunks({"tool": "Edit", "patch": PATCH, "input": {}})
        self.assertEqual(hunks_text(hunks).split("\n")[0], "@@ -10,3 +10,4 @@")
        self.assertEqual(first_line(hunks), 11)  # « -b » : deuxième ligne du morceau

    def test_regroupement(self):
        edits = [
            {"tool": "Edit", "path": "/p/a.py", "status": "ok", "patch": PATCH, "input": {}},
            {"tool": "Edit", "path": "/p/a.py", "status": "error", "patch": None,
             "input": {"old_string": "q", "new_string": "r"}},
            {"tool": "Write", "path": "/p/b.txt", "status": "ok", "patch": None, "created": True,
             "input": {"content": "x\ny"}, "agent": "a1"},
            {"tool": "Edit", "path": "/p/a.py", "status": "pending", "patch": None,
             "input": {"old_string": "c", "new_string": "C"}},
        ]
        a, b = group_files(edits)
        self.assertEqual((a["path"], len(a["edits"]), a["added"], a["removed"]), ("/p/a.py", 2, 3, 2))
        self.assertEqual((b["created"], b["agents"], b["added"]), (True, {"a1"}, 2))
        diff = full_diff([a, b])
        self.assertIn("--- a/p/a.py\n+++ b/p/a.py\n@@ -10,3 +10,4 @@", diff)
        self.assertIn("--- /dev/null\n+++ b/p/b.txt\n+x\n+y", diff)

    def test_cursor_sans_diff(self):
        items = [{"role": "tool", "name": "Edit", "id": "c:1", "input": {"file_path": "src/x.py"}},
                 {"role": "tool", "name": "Read", "id": "c:2", "input": {"file_path": "src/x.py"}},
                 {"role": "assistant", "text": "fini"}]
        edits = edits_from_items(items)
        self.assertEqual([(e["id"], e["path"]) for e in edits], [("c:1", "src/x.py")])
        (f,) = group_files(edits)
        self.assertFalse(f["known"])  # nombre de lignes inconnu : lu dans la base de Cursor


if __name__ == "__main__":
    unittest.main()
