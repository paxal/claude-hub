"""Tests des tags : python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="claude-hub-test-")

from claude_hub import tags as T  # noqa: E402
from claude_hub.tags import (  # noqa: E402
    TagStore, dir_matches, dir_token, normalize, parse_query, tag_matches, toggle_token,
)

# Avec `discover`, un autre test a pu importer claude_hub avant la ligne XDG ci-dessus :
# CONFIG pointerait alors sur la vraie config, dont TestStore efface tags.json.
T.CONFIG = Path(os.environ["XDG_CONFIG_HOME"]) / "claude-hub"
T.TAGS_FILE = T.CONFIG / "tags.json"


class TestQuery(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("#Stockly / Bazel  CI "), "stockly/bazel-ci")

    def test_parse(self):
        self.assertEqual(parse_query("#stockly dir:~/devel bazel"), (["stockly"], ["~/devel"], "bazel"))
        self.assertEqual(parse_query('#Stockly/ dir:"~/mes docs" l\'hôte d\'abord'),
                         (["stockly/"], ["~/mes docs"], "l'hôte d'abord"))
        self.assertEqual(parse_query("# seul"), ([], [], "# seul"))

    def test_tag_matches(self):
        self.assertTrue(tag_matches(["stockly/bazel"], "stockly"))
        self.assertTrue(tag_matches(["stockly/bazel"], "stockly/"))
        self.assertFalse(tag_matches(["stocklyx"], "stockly"))
        self.assertFalse(tag_matches(["stockly"], "stockly/"))

    def test_dir_matches(self):
        self.assertTrue(dir_matches("/home/p/devel/x", "~/devel", "/home/p"))
        self.assertFalse(dir_matches("/home/p/develop", "~/devel", "/home/p"))
        self.assertTrue(dir_matches("/home/p", "~", "/home/p"))
        self.assertTrue(dir_matches("/x/y", "/", ""))
        self.assertFalse(dir_matches(None, "/", ""))

    def test_toggle(self):
        self.assertEqual(toggle_token("#a bazel", "#b", "#"), "bazel #b")
        self.assertEqual(toggle_token("bazel #b", "#b", "#"), "bazel")
        self.assertEqual(toggle_token("#a", "dir:~/x", "dir:"), "#a dir:~/x")
        self.assertEqual(dir_token("~/mes docs"), 'dir:"~/mes docs"')


class TestStore(unittest.TestCase):
    def setUp(self):
        T.TAGS_FILE.unlink(missing_ok=True)

    def test_roundtrip(self):
        s = {"id": "abc", "title": "T", "mtime": 1, "live": "busy", "host": "x", "cwd": "/a"}
        self.assertEqual(TagStore().set(s, "rpi4", ["Stockly/Bazel", "#ops", "", "ops"]),
                         ["ops", "stockly/bazel"])
        st = TagStore()
        self.assertEqual(st.tags("abc"), ["ops", "stockly/bazel"])
        self.assertNotIn("live", st.data["abc"]["session"])
        self.assertEqual(st.counts(), {"ops": 1, "stockly/bazel": 1, "stockly/": 1})
        self.assertEqual(st.tagged()[0][0], "rpi4")

        st.refresh("rpi4", [dict(s, title="T2")])
        self.assertEqual(TagStore().data["abc"]["session"]["title"], "T2")
        st.refresh("local", [dict(s, title="T3")])  # autre hôte : ignoré
        self.assertEqual(TagStore().data["abc"]["session"]["title"], "T2")

        st.set(s, "rpi4", [])
        self.assertEqual(TagStore().data, {})


if __name__ == "__main__":
    unittest.main()
