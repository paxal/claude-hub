"""Tests des sous-agents des sessions cloud (reconstitués d'après les événements de l'API)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.cloud import cloud_agents, cloud_history  # noqa: E402


def call(tid, name="Agent", prompt="Cherche"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": {"description": "d", "prompt": prompt}}]}}


def progress(tid, desc, tool="Bash", tokens=1000):
    return {"type": "system", "subtype": "task_progress", "tool_use_id": tid, "description": desc,
            "last_tool_name": tool, "usage": {"total_tokens": tokens}}


def tool_result(tid, text, background=False):
    return {"type": "user", "tool_use_result": {"isAsync": background} if background else "x",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                     "content": [{"type": "text", "text": text}]}]}}


def notification(tid, result):
    return {"type": "user", "message": {"content": (
        f"<task-notification>\n<tool-use-id>{tid}</tool-use-id>\n<status>completed</status>\n"
        f"<result>{result}</result>\n</task-notification>")}}


class CloudAgents(unittest.TestCase):
    def test_sous_agent_synchrone(self):
        a = cloud_agents([call("t1"), progress("t1", "Running Lire le schéma"),
                          progress("t1", "Running Lire le schéma"), progress("t1", "Chercher", "Grep", 2500),
                          tool_result("t1", "Rapport")])["t1"]
        self.assertTrue(a["found"])
        self.assertEqual([it.get("text") or it["input"]["description"] for it in a["items"]],
                         ["Cherche", "Lire le schéma", "Chercher", "Rapport"])
        self.assertEqual(a["items"][2]["name"], "Grep")
        self.assertEqual(a["total"], 4)
        self.assertEqual(a["end"], {"status": "completed", "usage": {"total_tokens": 2500}})

    def test_sous_agent_en_fond(self):
        evs = [call("t1"), tool_result("t1", "Async agent launched", background=True),
               progress("t1", "Lire"), notification("t1", "Rapport final"),
               {"type": "system", "subtype": "task_notification", "tool_use_id": "t1",
                "status": "completed", "summary": "Résumé"}]
        a = cloud_agents(evs)["t1"]
        self.assertEqual(a["items"][-1], {"role": "assistant", "text": "Rapport final"})
        self.assertEqual(a["end"]["status"], "completed")
        self.assertEqual(a["end"]["usage"], {"total_tokens": 1000})

    def test_rapport_dans_le_resume(self):
        """Sans message <task-notification> dans le fil, le rapport est le résumé de la notification."""
        evs = [call("t1"), tool_result("t1", "Async agent launched", background=True),
               {"type": "system", "subtype": "task_notification", "tool_use_id": "t1",
                "status": "failed", "summary": "Rapport complet"}]
        a = cloud_agents(evs)["t1"]
        self.assertEqual(a["items"][-1]["text"], "Rapport complet")
        self.assertEqual(a["end"]["status"], "failed")

    def test_sans_trace(self):
        a = cloud_agents([call("t1")])["t1"]
        self.assertFalse(a["found"])
        self.assertIsNone(a["end"])

    def test_autres_outils_ignores(self):
        agents = cloud_agents([call("t1"), call("b1", name="Bash"), progress("b1", "tâche de fond"),
                               tool_result("b1", "sortie")])
        self.assertEqual(list(agents), ["t1"])


class CloudPlans(unittest.TestCase):
    def test_decision_du_plan(self):
        """Plan refusé puis plan validé : la décision suit chaque plan, comme pour les .jsonl."""
        refus = tool_result("p1", "Ajoute la date")
        refus["message"]["content"][0]["is_error"] = True
        evs = [call("p1", name="ExitPlanMode"), refus, call("p2", name="ExitPlanMode"),
               tool_result("p2", "User has approved your plan."), call("p3", name="ExitPlanMode")]
        with mock.patch("claude_hub.cloud.cloud_get", return_value={"data": evs}):
            items = cloud_history("s", 50)["items"]
        self.assertEqual([i.get("decision") for i in items], [
            {"ok": False, "text": "Ajoute la date"}, {"ok": True, "text": ""}, None])


if __name__ == "__main__":
    unittest.main()
