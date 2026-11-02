"""Tests de la liste de tâches : TodoWrite, TaskCreate, TaskUpdate, historique et flux."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.todos import TodoList, created_id, todo_detail  # noqa: E402
from claude_hub.window import Hub  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "33333333-4444-5555-6666-777777777777"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"  # comme claude


def use(tid, name, **inp):
    return line(type="assistant", message={"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]})


def result(tid, text, structured=None, **extra):
    r = dict(type="user", message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}, **extra)
    if structured is not None:
        r["toolUseResult"] = structured
    return line(**r)


class TestModele(unittest.TestCase):
    def test_task_create_et_update(self):
        t = TodoList()
        t.use("TaskCreate", {"subject": "Lire", "activeForm": "Lecture"}, "u1")
        t.use("TaskCreate", {"subject": "Écrire", "activeForm": "Écriture"}, "u2")
        # Numéros attribués par claude, pas forcément ceux qu'on aurait devinés
        self.assertTrue(t.created("u1", "7"))
        self.assertTrue(t.created("u2", "8"))
        t.use("TaskUpdate", {"taskId": "7", "status": "completed"})
        t.use("TaskUpdate", {"taskId": "8", "status": "in_progress"})
        self.assertEqual([(x["id"], x["status"]) for x in t.tasks],
                         [("7", "completed"), ("8", "in_progress")])
        self.assertEqual(t.current()["active"], "Écriture")
        self.assertEqual(t.done(), 1)
        self.assertTrue(t.active())
        t.use("TaskUpdate", {"taskId": "8", "status": "completed"})
        self.assertFalse(t.active())  # tout est fait : le volet se masque

    def test_suppression_et_inconnue(self):
        t = TodoList()
        t.use("TaskCreate", {"subject": "A"}, "u1")
        t.use("TaskCreate", {"subject": "B"}, "u2")  # numéros provisoires 1 et 2
        t.use("TaskUpdate", {"taskId": "1", "status": "deleted"})
        t.use("TaskUpdate", {"taskId": "99", "status": "completed"})  # ignorée
        self.assertEqual([x["subject"] for x in t.tasks], ["B"])

    def test_todowrite_remplace(self):
        t = TodoList()
        t.use("TaskCreate", {"subject": "ancienne"}, "u1")
        t.use("TodoWrite", {"todos": [
            {"content": "Un", "activeForm": "Un en cours", "status": "completed"},
            {"content": "Deux", "activeForm": "Deux en cours", "status": "in_progress"}]})
        self.assertEqual([x["subject"] for x in t.tasks], ["Un", "Deux"])
        self.assertEqual(t.current()["active"], "Deux en cours")
        self.assertFalse(t.use("Read", {"file_path": "/x"}))

    def test_created_id(self):
        self.assertEqual(created_id({"task": {"id": 4}}), "4")
        self.assertEqual(created_id(None, "Task #12 created successfully: x"), "12")
        self.assertEqual(created_id(None, [{"type": "text", "text": "Task #3 created"}]), "3")
        self.assertIsNone(created_id(None, "autre chose"))

    def test_resume_dans_le_fil(self):
        self.assertEqual(todo_detail("TaskUpdate", {"taskId": "2", "status": "completed"}),
                         "#2 → terminée")
        self.assertEqual(todo_detail("TaskCreate", {"subject": "Lire", "description": "long"}),
                         "Lire")
        self.assertEqual(todo_detail("TodoWrite", {"todos": [
            {"content": "a", "status": "completed"}, {"content": "b", "status": "pending"}]}),
            "1/2 terminées")


class TestFlux(unittest.TestCase):
    """Événements de claude -p --output-format stream-json."""

    def test_flux(self):
        s = {}
        ev = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "u1", "name": "TaskCreate", "input": {"subject": "A"}}]}}
        self.assertTrue(Hub.track_todos(ev, s))
        ev = {"type": "user", "tool_use_result": {"task": {"id": "5", "subject": "A"}},
              "message": {"content": [{"type": "tool_result", "tool_use_id": "u1",
                                       "content": "Task #5 created successfully: A"}]}}
        self.assertTrue(Hub.track_todos(ev, s))
        self.assertEqual(s["todos"].tasks[0]["id"], "5")
        sub = {"type": "assistant", "parent_tool_use_id": "agent1", "message": {"content": [
            {"type": "tool_use", "id": "u2", "name": "TaskCreate", "input": {"subject": "B"}}]}}
        self.assertFalse(Hub.track_todos(sub, s))  # tâche d'un sous-agent
        self.assertEqual(len(s["todos"].tasks), 1)


class TestHistorique(unittest.TestCase):
    """Champ « todos » de la commande history : toute la transcription, hors sous-agents."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", message={"role": "user", "content": "Bonjour"}, cwd="/tmp/x")
            + use("u1", "TaskCreate", subject="Lire", activeForm="Lecture")
            + result("u1", "Task #1 created successfully: Lire", {"task": {"id": "1"}})
            + use("u2", "TaskCreate", subject="Écrire")
            + result("u2", [{"type": "text", "text": "Task #2 created successfully: Écrire"}])
            + use("u3", "TaskCreate", subject="Sous-agent")  # ignorée : isSidechain
            .replace('"type":"assistant"', '"isSidechain":true,"type":"assistant"')
            + use("u4", "TaskUpdate", taskId="1", status="completed")
            + result("u4", "Updated task #1 status")
            + use("u5", "TaskUpdate", taskId="2", status="in_progress")
            + "".join(line(type="assistant", message={"content": [{"type": "text", "text": f"m{i}"}]})
                      for i in range(5)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script, *args):
        r = subprocess.run([interp, "-", *args], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True, env={"HOME": self.home,
                                                                             "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)

    def test_liste_complete_malgre_la_limite(self):
        res = self.run_helper("python3", "helper.py", "history", SID, "3")
        self.assertEqual(len(res["items"]), 3)  # historique tronqué…
        t = TodoList.from_events(res["todos"])  # … mais toutes les tâches
        self.assertEqual([(x["id"], x["subject"], x["status"]) for x in t.tasks],
                         [("1", "Lire", "completed"), ("2", "Écrire", "in_progress")])
        self.assertEqual(t.current()["subject"], "Écrire")

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_python_et_node_identiques(self):
        self.assertEqual(self.run_helper("python3", "helper.py", "history", SID, "3"),
                         self.run_helper("node", "helper.js", "history", SID, "3"))


if __name__ == "__main__":
    unittest.main()
