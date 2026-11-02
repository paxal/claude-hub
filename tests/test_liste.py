"""Tests des fonctions de la refonte : séparateurs de jour, états des lignes, durées,
résumé des étapes et des plans, lignes ajoutées et retirées."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.home import plain, plan_digest  # noqa: E402
from claude_hub.listrow import day_label, elapsed_text, short_date, status_kind  # noqa: E402
from claude_hub.render import edit_stats  # noqa: E402
from claude_hub.toolcall import step_summary  # noqa: E402
from claude_hub.window import is_step, last_reply  # noqa: E402


NOW = time.mktime((2026, 11, 2, 12, 0, 0, 0, 0, -1))  # lundi 2 novembre 2026, midi


class TestJours(unittest.TestCase):
    def test_libelles(self):
        self.assertEqual(day_label(time.mktime((2026, 11, 2, 0, 5, 0, 0, 0, -1)), NOW), "Aujourd'hui")
        self.assertEqual(day_label(time.mktime((2026, 11, 1, 23, 59, 0, 0, 0, -1)), NOW), "Hier")
        self.assertEqual(day_label(time.mktime((2026, 10, 31, 10, 0, 0, 0, 0, -1)), NOW),
                         "Samedi 31 octobre")
        self.assertEqual(day_label(time.mktime((2025, 3, 4, 10, 0, 0, 0, 0, -1)), NOW), "Mardi 4 mars 2025")

    def test_date_courte(self):
        self.assertEqual(short_date(time.mktime((2026, 11, 2, 9, 7, 0, 0, 0, -1)), NOW), "09:07")
        self.assertEqual(short_date(time.mktime((2026, 10, 27, 9, 7, 0, 0, 0, -1)), NOW), "27/10")


class TestEtats(unittest.TestCase):
    def test_genres(self):
        self.assertEqual(status_kind("requires_action"), "action")
        self.assertEqual(status_kind("waiting"), "action")  # question posée dans un terminal
        for s in ("busy", "unread", "idle", "draft"):
            self.assertEqual(status_kind(s), s)
        self.assertEqual(status_kind("shell"), "other")
        self.assertIsNone(status_kind(None))

    def test_durees(self):
        self.assertEqual(elapsed_text(0), "0 s")
        self.assertEqual(elapsed_text(14.7), "14 s")
        self.assertEqual(elapsed_text(62), "1 min 02")
        self.assertEqual(elapsed_text(3900), "1 h 05")


class TestEtapes(unittest.TestCase):
    def test_lignes_modifiees(self):
        self.assertEqual(edit_stats("Edit", {"old_string": "a\nb\nc", "new_string": "a\nB\nc\nd"}), (2, 1))
        self.assertEqual(edit_stats("Write", {"content": "x\ny\n"}), (2, 0))
        self.assertEqual(edit_stats("MultiEdit", {"edits": [{"old_string": "a", "new_string": "b"},
                                                            {"old_string": "c", "new_string": ""}]}), (1, 2))
        self.assertIsNone(edit_stats("Bash", {"command": "ls"}))
        self.assertIsNone(edit_stats("Edit", {}))

    def test_resume(self):
        self.assertEqual(step_summary({"name": "Read", "input": {"file_path": "/a/b/window.py"}}),
                         "Read window.py")
        self.assertEqual(step_summary({"name": "Grep", "input": {"pattern": "set_sort_func"}}),
                         "Grep set_sort_func")
        long = step_summary({"name": "Bash", "input": {"command": "x" * 80}})
        self.assertTrue(long.endswith("…") and len(long) < 45)

    def test_suite(self):
        self.assertTrue(is_step({"role": "tool", "name": "Bash"}))
        for item in ({"role": "tool", "name": "Agent"}, {"role": "tool", "name": "ExitPlanMode"},
                     {"role": "assistant", "text": "x"}, None):
            self.assertFalse(is_step(item))

    def test_derniere_reponse(self):
        items = [{"role": "assistant", "text": "un"}, {"role": "tool", "name": "Bash"},
                 {"role": "assistant", "text": "deux"}, {"role": "user", "text": "merci"}]
        self.assertEqual(last_reply(items), "deux")
        self.assertIsNone(last_reply([{"role": "user", "text": "x"}]))


class TestPlan(unittest.TestCase):
    def test_resume(self):
        title, summary = plan_digest("# Plan : séparateurs\n\n1. `window.py` : en-tête\n"
                                     "2. `row_meta` : **l'heure** seule\n- tests/test_tri.py")
        self.assertEqual(title, "Plan : séparateurs")
        self.assertEqual(summary, "window.py : en-tête · row_meta : l'heure seule · tests/test_tri.py")
        self.assertEqual(plan_digest("")[0], "Plan")

    def test_markdown(self):
        self.assertEqual(plain("a *b* c_d _e_ **f** snake_case"), "a b c_d e f snake_case")


if __name__ == "__main__":
    unittest.main()
