"""Message suivant prédit par claude (prompt_suggestion, --prompt-suggestions) : aide de la
saisie vide, repris par Tab, oublié dès qu'un message part ou que la conversation continue
ailleurs (Hub.got_suggestion, show_placeholder, accept_suggestion, drop_stale_suggestion)."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.window import CHAT_HINT, HOME_HINT, PLAN_HINT, Hub  # noqa: E402


class Buffer:
    def __init__(self):
        self.text, self.cursor = "", None

    def set_text(self, text):
        self.text = text

    def get_char_count(self):
        return len(self.text)

    def get_end_iter(self):
        return len(self.text)

    def place_cursor(self, at):
        self.cursor = at


def hub(page="chat"):
    """Hub réduit à ce que la suggestion utilise ; ses méthodes sont celles de Hub."""
    h = types.SimpleNamespace(suggestions={}, streams={}, pending={}, attachments=[], current=None)
    h.placeholder = types.SimpleNamespace(text=None, set_text=lambda t: setattr(h.placeholder, "text", t))
    h.suggest_key = types.SimpleNamespace(visible=None,
                                          set_visible=lambda v: setattr(h.suggest_key, "visible", v))
    h.pages = types.SimpleNamespace(get_visible_child_name=lambda: page)
    buf = Buffer()
    h.input = types.SimpleNamespace(get_buffer=lambda: buf)
    for name in ("pending_plan", "shown_suggestion", "show_placeholder", "accept_suggestion",
                 "got_suggestion", "drop_suggestion", "drop_stale_suggestion"):
        setattr(h, name, getattr(Hub, name).__get__(h))
    return h


class TestSuggestion(unittest.TestCase):
    def test_affichee_dans_la_saisie_vide(self):
        h = hub()
        h.current = {"id": "a"}
        h.got_suggestion({"id": "a"}, "  lance les\ntests  ")
        self.assertEqual(h.placeholder.text, "lance les tests")  # sur une ligne
        self.assertTrue(h.suggest_key.visible)

    def test_autre_conversation(self):
        h = hub()
        h.current = {"id": "b"}
        h.placeholder.text = CHAT_HINT
        h.got_suggestion({"id": "a"}, "lance les tests")
        self.assertEqual(h.placeholder.text, CHAT_HINT)  # rien ne change pour celle affichée
        h.current = {"id": "a"}  # retour dans la conversation : elle est retenue
        h.show_placeholder()
        self.assertEqual(h.placeholder.text, "lance les tests")

    def test_vide_ignoree(self):
        h = hub()
        h.got_suggestion({"id": "a"}, "  ")
        self.assertEqual(h.suggestions, {})

    def test_plan_accueil_et_reponse_en_cours(self):
        h = hub()
        h.current = {"id": "a"}
        h.got_suggestion(h.current, "lance les tests")
        h.pending = {"a": {"r1": {"tool_name": "ExitPlanMode"}}}
        h.show_placeholder()
        self.assertEqual((h.placeholder.text, h.suggest_key.visible), (PLAN_HINT, False))
        h.pending = {}
        h.streams = {"a": (None, {"idle": False})}  # Claude répond : la prédiction attend la fin
        h.show_placeholder()
        self.assertEqual((h.placeholder.text, h.suggest_key.visible), (CHAT_HINT, False))
        h.streams = {"a": (None, {"idle": True})}  # réponse finie, tâches de fond en cours
        h.show_placeholder()
        self.assertTrue(h.suggest_key.visible)
        home = hub(page="home")
        home.suggestions = dict(h.suggestions)
        home.show_placeholder()
        self.assertEqual((home.placeholder.text, home.suggest_key.visible), (HOME_HINT, False))

    def test_tab_la_reprend(self):
        h = hub()
        h.current = {"id": "a"}
        self.assertFalse(h.accept_suggestion())  # pas de prédiction : Tab garde son effet
        h.got_suggestion(h.current, "lance les tests")
        self.assertTrue(h.accept_suggestion())
        buf = h.input.get_buffer()
        self.assertEqual((buf.text, buf.cursor), ("lance les tests", len("lance les tests")))
        self.assertFalse(h.accept_suggestion())  # saisie déjà remplie
        buf.set_text("")
        h.attachments = [("image/png", b"")]
        self.assertFalse(h.accept_suggestion())  # une image attend : la saisie n'est pas vide

    def test_oubliee_apres_un_message_ecrit_ensuite(self):
        h = hub()
        h.current = {"id": "a"}
        h.got_suggestion(h.current, "lance les tests")
        since = h.suggestions["a"][1]
        h.drop_stale_suggestion(h.current, [{"role": "user", "ts": int(since) - 5},
                                            {"role": "assistant", "ts": int(since) + 5}])
        self.assertIn("a", h.suggestions)  # le message auquel Claude a répondu, puis sa réponse
        h.drop_stale_suggestion(h.current, [{"role": "user", "ts": int(since) + 5}])
        self.assertNotIn("a", h.suggestions)
        self.assertEqual((h.placeholder.text, h.suggest_key.visible), (CHAT_HINT, False))

    def test_oubliee_quand_un_message_part(self):
        h = hub()
        h.current = {"id": "a"}
        h.got_suggestion(h.current, "lance les tests")
        h.drop_suggestion("a")
        self.assertEqual(h.suggestions, {})
        self.assertFalse(h.suggest_key.visible)


if __name__ == "__main__":
    unittest.main()
