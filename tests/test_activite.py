"""Lignes de la liste d'activité au-dessus de la saisie (activity.py) : ce qui tourne et ce
qui attend."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.activity import (MAX_AGENTS, MONITOR_ICON, TERMINAL_ICON, agent_specs,  # noqa: E402
                                 claude_spec, grouped_summary, minutes_text, queued_spec,
                                 request_spec, shell_spec)


class TestLignes(unittest.TestCase):
    def test_claude(self):
        calls = []
        spec = claude_spec("Claude utilise Bash", "make  test\n -k", 12.0, stop=lambda: calls.append(1))
        self.assertEqual((spec["key"], spec["label"], spec["detail"]), ("claude", "Claude utilise Bash", "make test -k"))
        self.assertTrue(spec["spin"] and spec["timed"])
        tip, action = spec["actions"]["stop"]
        action()
        self.assertEqual(calls, [1])
        self.assertIn("Échap", tip)
        # dans un terminal : pas d'« Arrêter » (claude n'a pas été lancé d'ici)
        self.assertEqual(claude_spec("Claude travaille (dans un terminal)")["actions"], {})

    def test_demande(self):
        jump = object()
        spec = request_spec({"kind": "permission", "text": "Bash · make test"}, jump=jump)
        self.assertEqual((spec["label"], spec["detail"]), ("Claude attend ta réponse", "Autorisation : Bash · make test"))
        self.assertIs(spec["actions"]["jump"][1], jump)
        self.assertEqual(request_spec({"kind": "plan", "rid": "r"}, jump=jump)["detail"], "Plan à valider")
        self.assertEqual(request_spec({"kind": "question", "text": "Quel nom ?"}, jump=jump)["detail"],
                         "Question : Quel nom ?")
        terminal = request_spec({"kind": "action", "text": "Claude attend une réponse dans son terminal."})
        self.assertEqual(terminal["label"], "Action requise")
        self.assertEqual(terminal["actions"], {})

    def test_sous_agents(self):
        agents = [{"id": f"a{i}", "type": "Explore", "description": f"Tâche {i}",
                   "last": {"role": "tool", "name": "Grep", "input": {"pattern": "x"}}}
                  for i in range(MAX_AGENTS + 1)]
        opened = []
        specs = agent_specs(agents, indent=True, open_agent=opened.append, open_panel=lambda: None)
        self.assertEqual(len(specs), MAX_AGENTS + 1)  # « … et 1 autre » n'économiserait rien
        self.assertEqual((specs[0]["label"], specs[0]["detail"]), ("Tâche 0", "Grep x"))
        self.assertTrue(specs[0]["mono"])  # appel tel quel : police fixe
        self.assertTrue(all(s["indent"] and s["inline"] for s in specs))
        specs[1]["actions"]["open"][1]()
        self.assertEqual([a["id"] for a in opened], ["a1"])
        more = agent_specs(agents + [{"id": "b", "tool": "toolu_b"}], open_panel=lambda: None)
        self.assertEqual(len(more), MAX_AGENTS + 1)
        self.assertEqual(more[-1]["label"], "… et 2 autres sous-agents")
        self.assertEqual(agent_specs([{"tool": "toolu_x", "type": "Plan"}])[0]["key"], "agent:toolu_x")
        # Bash avec une description : elle tient lieu d'étape, la commande va dans l'info-bulle
        bash = agent_specs([{"id": "u", "description": "Audit UPS", "type": "general-purpose", "last": {
            "role": "tool", "name": "Bash", "input": {"command": "git grep -n is_terminal",
                                                      "description": "Find is_terminal uses"}}}])[0]
        self.assertEqual((bash["detail"], bash["mono"]), ("Find is_terminal uses", False))
        self.assertEqual(bash["tooltip"], "general-purpose · Audit UPS › Bash git grep -n is_terminal")

    def test_shells_et_monitors(self):
        shell = shell_spec({"id": "b1", "command": "make serve\nautre ligne", "start": 1000.0})
        self.assertEqual((shell["kind"], shell["label"], shell["detail"]), ("shell", "Shell", "make serve"))
        self.assertTrue(shell["mono"])  # la commande, faute de description
        self.assertEqual((shell["icon"], shell["start"], shell["sub"]), (TERMINAL_ICON, 1000.0, ""))
        mon = shell_spec({"id": "m1", "kind": "monitor", "description": "CI de la PR",
                          "command": "gh run watch", "event": "lint ok\n\ntests en cours\n"})
        self.assertEqual((mon["kind"], mon["label"], mon["detail"]), ("monitor", "Monitor", "CI de la PR"))
        self.assertEqual((mon["icon"], mon["sub"], mon["mono"]), (MONITOR_ICON, "→ tests en cours", False))
        self.assertIn("$ gh run watch", mon["tooltip"])
        self.assertIsNone(shell_spec({"id": "x", "start": True})["start"])

    def test_file(self):
        spec = queued_spec(0, "Ensuite,\n  le README", [{"type": "image/png"}], edit=print, remove=print)
        self.assertEqual((spec["label"], spec["detail"]), ("En file", "Ensuite, le README  ·  1 image"))
        self.assertEqual(set(spec["actions"]), {"edit", "remove"})
        self.assertEqual(queued_spec(1, "", [{}, {}])["detail"], "(image seule)  ·  2 images")
        self.assertNotEqual(queued_spec(0, "a", [])["key"], queued_spec(0, "b", [])["key"])

    def test_resume_replie(self):
        kinds = [shell_spec({"id": i, "kind": k})["kind"] for i, k in
                 (("m1", "monitor"), ("b1", None), ("b2", None))]
        self.assertEqual(grouped_summary(kinds, "tâches 3/7"), "1 monitor · 2 shells · tâches 3/7")
        self.assertEqual(grouped_summary([], "tâches 0/2"), "Tâches 0/2")
        self.assertEqual(grouped_summary(["shell"]), "1 shell")
        self.assertEqual(grouped_summary([]), "")  # rien à replier : pas de ligne de résumé

    def test_durees(self):
        self.assertEqual([minutes_text(s) for s in (0, 59, 60, 359, 3600, 3725)],
                         ["< 1 min", "< 1 min", "1 min", "5 min", "1 h 00", "1 h 02"])


if __name__ == "__main__":
    unittest.main()
