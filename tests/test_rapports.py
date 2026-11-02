"""Messages relayés : rapports de sous-agents (<agent-message>) et envois Remote Control."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub.cloud import peer_item  # noqa: E402

REMOTE = Path(__file__).resolve().parent.parent / "src/claude_hub/remote"
SID = "11111111-2222-3333-4444-555555555555"
PREFIX = "Another Claude session sent a message:\n"
SUFFIX = "\n\nThis came from another Claude session, not the user."
HANDBACK = ('<agent-message from="a42287351845b5577">\n[Subagent hand-back] The text below is the final '
            "report of a subagent this session delegated to. The report follows:\n"
            "  **Verdict:** FedEx refusé.\n\n  1. Gates\n    - imbriqué\n</agent-message>")
BODIES = [HANDBACK, '<agent-message from="b1">  coucou  </agent-message>',
          '<agent-message from="c2">\n[Subagent hand-back] sans marqueur', "envoyé depuis Claude Hub"]
REPORT = "**Verdict:** FedEx refusé.\n\n1. Gates\n  - imbriqué"


def line(**r):
    return json.dumps(r, separators=(",", ":")) + "\n"


def peer(text):
    return line(type="user", isMeta=True, origin={"kind": "peer"},
                message={"role": "user", "content": PREFIX + text + SUFFIX})


class TestRapports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        proj = Path(cls.home, ".claude/projects/-tmp-x")
        proj.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text(
            line(type="user", message={"role": "user", "content": "bonjour"})
            + "".join(peer(b) for b in BODIES))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home)

    def run_helper(self, interp, script):
        r = subprocess.run([interp, "-", "history", SID, "50"], input=(REMOTE / script).read_text(),
                           capture_output=True, text=True, check=True,
                           env={"HOME": self.home, "PATH": "/usr/bin:/bin"})
        return json.loads(r.stdout)["items"]

    def test_history(self):
        items = self.run_helper("python3", "helper.py")
        self.assertEqual(items[1:], [
            {"role": "user", "text": REPORT, "via": "agent", "from": "a42287351845b5577", "report": True},
            {"role": "user", "text": "coucou", "via": "agent", "from": "b1", "report": False},
            {"role": "user", "text": "sans marqueur", "via": "agent", "from": "c2", "report": True},
            {"role": "user", "text": "envoyé depuis Claude Hub", "via": "remote"}])
        self.assertEqual(items, self.run_helper("node", "helper.js"))

    def test_cloud(self):
        self.assertEqual([peer_item(b) for b in BODIES], self.run_helper("python3", "helper.py")[1:])


if __name__ == "__main__":
    unittest.main()
