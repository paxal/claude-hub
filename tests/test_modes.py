"""Tests du mode de permissions retenu par conversation : python3 -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub import hosts as H  # noqa: E402


class TestRememberMode(unittest.TestCase):
    def settings(self):
        return {"mode": H.DEFAULT_MODE, "modes": {}}

    def test_retenu_par_session_et_comme_dernier_choix(self):
        d = self.settings()
        self.assertTrue(H.remember_mode(d, "a", "auto"))
        self.assertTrue(H.remember_mode(d, "b", "plan"))
        self.assertEqual(d["modes"], {"a": "auto", "b": "plan"})
        self.assertEqual(d["mode"], "plan")

    def test_rien_de_change(self):
        d = self.settings()
        H.remember_mode(d, "a", "auto")
        self.assertFalse(H.remember_mode(d, "a", "auto"))

    def test_mode_inconnu_ignore(self):
        d = self.settings()
        self.assertFalse(H.remember_mode(d, "a", "n'importe quoi"))
        self.assertFalse(H.remember_mode(d, "a", None))
        self.assertEqual(d, self.settings())

    def test_les_plus_anciennes_partent(self):
        d = self.settings()
        for i in range(H.MODES_KEPT + 5):
            H.remember_mode(d, str(i), "auto" if i % 2 else "plan")
        H.remember_mode(d, "5", "default")  # réutilisée : remonte en fin de liste
        self.assertEqual(len(d["modes"]), H.MODES_KEPT)
        self.assertNotIn("0", d["modes"])
        self.assertEqual(list(d["modes"])[-1], "5")

    def test_libelles(self):
        self.assertEqual(set(H.MODE_LABELS), set(H.PERMISSION_MODES))
        self.assertEqual(set(H.MODE_HELP), set(H.PERMISSION_MODES))


if __name__ == "__main__":
    unittest.main()
