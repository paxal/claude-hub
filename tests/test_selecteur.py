"""Sélecteur Ctrl+K (recherche approximative), vues enregistrées, épingles, contexte et coût,
compteur de l'icône."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub import hosts  # noqa: E402
from claude_hub.launcher import launcher_props  # noqa: E402
from claude_hub.switcher import fuzzy_match, marked, rank  # noqa: E402
from claude_hub.usage import (  # noqa: E402
    COSTS_KEPT, context_tokens, context_window, cost_text, learn_windows, remember_cost,
)
from claude_hub.views import add_view, remove_view, same_query, view_icon  # noqa: E402


def entry(title, folder="~", mtime=0, **kw):
    return dict({"sid": title, "title": title, "folder": folder, "host": "local", "tags": [],
                 "mtime": mtime}, **kw)


class TestRecherche(unittest.TestCase):
    def test_sous_suite_et_accents(self):
        score, pos = fuzzy_match("sepjr", [("Séparateurs de jour", 2)])
        self.assertGreater(score, 0)
        self.assertEqual(pos, {0, 1, 2, 15, 18})  # S é p … j … r
        self.assertIsNone(fuzzy_match("xyz", [("Séparateurs de jour", 2)]))
        self.assertIsNone(fuzzy_match("jours", [("Séparateurs de jour", 2)]))  # pas de « s » après

    def test_debut_de_mot_et_suite(self):
        a = fuzzy_match("pr", [("PR 27601", 1)])[0]
        b = fuzzy_match("pr", [("Partitions en images", 1)])[0]
        self.assertGreater(a, b)  # lettres à la suite, en début de mot

    def test_mots_sur_plusieurs_champs(self):
        fields = [("Shipments tracking", 2), ("~/Stockly/Main", 1), ("cerberus", 1), ("", 1)]
        score, pos = fuzzy_match("ship stockly", fields)
        self.assertGreater(score, 0)
        self.assertEqual(pos, {0, 1, 2, 3})  # seul le titre est surligné
        self.assertIsNone(fuzzy_match("ship rpi4", fields))

    def test_classement(self):
        entries = [entry("Investigating SpringGDS", mtime=3), entry("Séparateurs de jour", mtime=1),
                   entry("Shipments sentry", mtime=2, pinned=True)]
        self.assertEqual([e["title"] for e in rank(entries, "")],
                         ["Shipments sentry", "Investigating SpringGDS", "Séparateurs de jour"])
        self.assertEqual([e["title"] for e in rank(entries, "sep")], ["Séparateurs de jour"])
        # à score égal (« s » en début de mot partout), la plus récente d'abord
        self.assertEqual([e["title"] for e in rank(entries, "s", limit=2)],
                         ["Investigating SpringGDS", "Shipments sentry"])

    def test_surlignage(self):
        self.assertEqual(marked("a<b>c", {0, 1, 4}), "<b>a&lt;</b>b&gt;<b>c</b>")


class TestVues(unittest.TestCase):
    def test_ajout_et_retrait(self):
        views = add_view([], "  #stockly  ")
        self.assertEqual(views, [{"query": "#stockly"}])
        self.assertEqual(add_view(views, "#stockly"), views)  # pas de doublon
        self.assertEqual(add_view(views, "   "), views)
        views = add_view(views, 'dir:"~/chansons scout"')
        self.assertEqual(remove_view(views, "#stockly"), [{"query": 'dir:"~/chansons scout"'}])
        self.assertTrue(same_query("#a  dir:~", "#a dir:~"))

    def test_icones(self):
        self.assertEqual(view_icon("dir:~/x #a"), "folder-symbolic")
        self.assertEqual(view_icon("bazel"), "edit-find-symbolic")


class TestReglages(unittest.TestCase):
    def test_epingles_et_vues_gardees(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hosts, "CONFIG", Path(d)), \
                mock.patch.object(hosts, "SETTINGS_FILE", Path(d) / "settings.json"):
            st = hosts.load_settings()
            self.assertEqual((st["pinned"], st["views"], st["costs"]), (set(), [], {}))
            st["pinned"].update({"b", "a"})
            st["views"] = add_view(st["views"], "#x")
            hosts.save_settings(st)
            st = hosts.load_settings()
            self.assertEqual((st["pinned"], st["views"]), ({"a", "b"}, [{"query": "#x"}]))


class TestUsage(unittest.TestCase):
    def test_contexte(self):
        self.assertEqual(context_tokens({"input_tokens": 2, "cache_read_input_tokens": 11382,
                                         "cache_creation_input_tokens": 9791, "output_tokens": 4,
                                         "service_tier": "standard"}), 21179)
        self.assertEqual(context_tokens(None), 0)

    def test_fenetre(self):
        self.assertEqual(context_window("claude-opus-5-5", {}), (1_000_000, False))
        self.assertEqual(context_window("claude-haiku-4-5-20251001", {}), (200_000, False))
        self.assertEqual(context_window("claude-sonnet-4-5[1m]", {}), (1_000_000, False))
        self.assertEqual(context_window("claude-opus-5-5", {"claude-opus-5-5": 500_000}), (500_000, True))
        self.assertEqual(context_window(None, {}), (None, False))
        learned = {}
        self.assertTrue(learn_windows(learned, {"claude-haiku-5-5": {"contextWindow": 1000000}}))
        self.assertFalse(learn_windows(learned, {"claude-haiku-5-5": {"contextWindow": 1000000}}))
        self.assertFalse(learn_windows(learned, {"x": {"costUSD": 1}}))
        self.assertEqual(learned, {"claude-haiku-5-5": 1000000})

    def test_cout(self):
        self.assertEqual(cost_text(0.1234), "0,12 $")
        self.assertEqual(cost_text(0.00207), "0,002 $")
        self.assertEqual(cost_text(0.0004), "< 0,001 $")
        costs = {}
        self.assertFalse(remember_cost(costs, "a", None))
        self.assertFalse(remember_cost(costs, "a", 0))
        for i in range(COSTS_KEPT + 5):
            remember_cost(costs, str(i), 0.5)
        self.assertEqual(len(costs), COSTS_KEPT)
        self.assertNotIn("0", costs)
        remember_cost(costs, "5", 0.25)  # remise en fin : la plus récente
        self.assertEqual(list(costs)[-1], "5")


class TestIcone(unittest.TestCase):
    def test_proprietes(self):
        props = launcher_props(3)
        self.assertEqual((props["count"].unpack(), props["count-visible"].unpack()), (3, True))
        self.assertEqual(props["count"].get_type_string(), "x")
        self.assertFalse(launcher_props(0)["count-visible"].unpack())


if __name__ == "__main__":
    unittest.main()
