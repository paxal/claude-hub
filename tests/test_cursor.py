"""Tests des conversations Cursor : outils aux noms de Claude, sorties, sous-agents, tâches."""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub import cursor  # noqa: E402
from claude_hub.render import tool_detail  # noqa: E402
from claude_hub.todos import TodoList  # noqa: E402

CID, SUB = "c0000000-0000-0000-0000-000000000001", "c0000000-0000-0000-0000-000000000002"


def tool(name, params=None, result=None, status="completed", **extra):
    t = {"name": name, "status": status, "params": json.dumps(params or {})}
    if result is not None:
        t["result"] = json.dumps(result)
    t.update(extra)
    return {"type": 2, "toolFormerData": t}


class TestCursor(unittest.TestCase):
    """Fausse base state.vscdb : une conversation et un sous-agent."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.db = Path(cls.dir, "state.vscdb")
        bubbles = {
            "u1": {"type": 1, "text": "Bonjour"},
            "r1": tool("read_file_v2", {"targetFile": "/src/a.py", "limit": 5},
                       {"contents": "print(1)\n", "totalLinesInFile": 1}),
            "r2": tool("read_file_v2", {"targetFile": "/src/b.py"}, {"totalLinesInFile": 9}),
            "e1": tool("edit_file_v2", {"relativeWorkspacePath": "/src/a.py"},
                       {"beforeContentId": "composer.content.av", "afterContentId": "composer.content.ap"}),
            "b1": tool("run_terminal_command_v2", {"command": "false", "commandDescription": "échoue"},
                       {"output": "oups", "exitCode": 1}),
            "b2": tool("run_terminal_command_v2", {"command": "echo"}, status="error",
                       error=json.dumps({"clientVisibleErrorMessage": "Plus de shell"})),
            "g1": tool("ripgrep_raw_search", {"pattern": "foo", "path": "/src"}),
            "m1": {"type": 2, "toolFormerData": {
                "name": "mcp-pg-sql", "status": "completed",
                "params": json.dumps({"tools": [{"name": "sql"}]}),
                "rawArgs": json.dumps({"name": "sql", "args": {"sql": "select 1"}}),
                "result": json.dumps({"result": json.dumps(
                    {"content": [{"type": "text", "text": "1 ligne"}]})})}},
            "t1": tool("todo_write", {"merge": True}, {"finalTodos": [
                {"content": "Lire", "status": "completed", "id": "a"},
                {"content": "Écrire", "status": "in_progress", "id": "b"},
                {"content": "Abandonné", "status": "cancelled", "id": "c"}]}),
            "a1": tool("task_v2", {"description": "Explorer", "prompt": "Cherche", "name": "explore"},
                       {"agentId": SUB},
                       additionalData=repr({"status": "success", "terminationReason": "completed"})),
            "p1": tool("create_plan", {"plan": "# Plan"}),
            "x1": {"type": 2, "text": "Fini."},
        }
        sub = {"s1": {"type": 1, "text": "Cherche"}, "s2": tool("glob_file_search",
               {"globPattern": "*.py", "targetDirectory": "/src"},
               {"directories": [{"files": [{"relPath": "a.py"}]}]})}
        with sqlite3.connect(cls.db) as db:
            db.execute("create table cursorDiskKV (key text unique on conflict replace, value blob)")
            for cid, bs in ((CID, bubbles), (SUB, sub)):
                db.execute("insert into cursorDiskKV values (?, ?)", (f"composerData:{cid}", json.dumps({
                    "composerId": cid, "lastUpdatedAt": 1000,
                    "fullConversationHeadersOnly": [{"bubbleId": b, "type": v["type"]}
                                                    for b, v in bs.items()]})))
                for b, v in bs.items():
                    db.execute("insert into cursorDiskKV values (?, ?)",
                               (f"bubbleId:{cid}:{b}", json.dumps(v)))
            db.execute("insert into cursorDiskKV values ('composer.content.av', 'a\nb\n')")
            db.execute("insert into cursorDiskKV values ('composer.content.ap', 'a\nc\n')")
        cls.patch = mock.patch.object(cursor, "CURSOR_DB", cls.db)
        cls.patch.start()
        cls.items = cursor.cursor_history(CID, 100)["items"]
        cls.tools = {it["id"].split(":")[1]: it for it in cls.items if it["role"] == "tool"}

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        shutil.rmtree(cls.dir)

    def test_noms_de_claude(self):
        names = {b: it["name"] for b, it in self.tools.items()}
        self.assertEqual(names, {"r1": "Read", "r2": "Read", "e1": "Edit", "b1": "Bash", "b2": "Bash",
                                 "g1": "Grep", "m1": "mcp-pg-sql", "t1": "TodoWrite",
                                 "a1": "Agent", "p1": "ExitPlanMode"})
        self.assertEqual(self.tools["r1"]["input"], {"file_path": "/src/a.py", "limit": 5})
        self.assertEqual(self.tools["b1"]["input"], {"command": "false", "description": "échoue"})
        self.assertEqual(self.tools["m1"]["input"], {"sql": "select 1"})  # sans « tools »
        self.assertEqual(tool_detail(self.tools["e1"]["input"]), "/src/a.py")
        self.assertEqual(self.tools["p1"]["input"], {"plan": "# Plan"})

    def test_sorties(self):
        def res(b):
            return cursor.cursor_result(self.tools[b]["id"])
        self.assertEqual(res("r1")["text"], "print(1)\n")
        self.assertIn("non gardé", res("r2")["text"])
        self.assertIn("-b\n+c", res("e1")["text"])
        self.assertEqual(res("b1")["text"], "oups\n[code de sortie 1]")
        self.assertEqual((res("b2")["text"], res("b2")["error"]), ("Plus de shell", True))
        self.assertIn("non gardée", res("g1")["text"])
        self.assertEqual(res("m1")["text"], "1 ligne")
        self.assertFalse(res("r1")["error"])
        self.assertFalse(cursor.cursor_result(f"{CID}:inconnue")["found"])

    def test_sous_agent(self):
        a = self.tools["a1"]
        self.assertEqual(a["input"]["subagent_type"], "explore")
        self.assertEqual(a["agent"], {"end": {"status": "completed"}})
        sub = cursor.cursor_agent(a["agent_id"], 100)
        self.assertTrue(sub["found"])
        self.assertEqual([it["role"] for it in sub["items"]], ["user", "tool"])
        glob = sub["items"][1]
        self.assertEqual((glob["name"], glob["input"]), ("Glob", {"pattern": "*.py", "path": "/src"}))
        self.assertEqual(cursor.cursor_result(glob["id"])["text"], "a.py")
        self.assertFalse(cursor.cursor_agent(None, 100)["found"])

    def test_taches(self):
        todos = TodoList.from_items(self.items)
        self.assertEqual([(t["subject"], t["status"]) for t in todos.tasks],
                         [("Lire", "completed"), ("Écrire", "in_progress"), ("Abandonné", "cancelled")])
        self.assertTrue(todos.active())
        todos.tasks[1]["status"] = "completed"
        self.assertFalse(todos.active())  # une tâche annulée ne garde pas le volet ouvert


if __name__ == "__main__":
    unittest.main()
