"""Envoi à une session Remote Control : le message doit arriver comme tapé par
l'utilisateur, pas comme venant d'une autre session."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from claude_hub import cloud  # noqa: E402


class RemoteSendTest(unittest.TestCase):
    def send(self, text, images=()):
        sent = []
        resp = mock.MagicMock()
        with mock.patch.object(cloud, "cloud_auth", return_value={"Authorization": "Bearer x"}), \
                mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout: sent.append(req) or resp), \
                mock.patch("subprocess.run") as run:
            cloud.remote_send("session_abc", text, list(images))
        run.assert_not_called()  # plus de `claude -p --cloud`
        self.assertEqual(len(sent), 1)
        return sent[0]

    def test_texte_seul(self):
        req = self.send("stop checking")
        self.assertEqual(req.full_url, cloud.CLOUD_API + "/v1/code/sessions/cse_abc/events")
        self.assertEqual(req.get_method(), "POST")
        # urllib met l'en-tête sous la forme « Anthropic-client-platform »
        self.assertEqual(req.get_header("Anthropic-client-platform"), "web_claude_ai")
        event = json.loads(req.data)["events"][0]["payload"]
        self.assertEqual(event["type"], "user")
        self.assertEqual(event["session_id"], "cse_abc")
        self.assertEqual(event["message"]["content"], [{"type": "text", "text": "stop checking"}])

    def test_images(self):
        req = self.send("", [{"type": "image/png", "data": "QUJD"}])
        self.assertEqual(req.get_header("Anthropic-client-platform"), "web_claude_ai")
        content = json.loads(req.data)["events"][0]["payload"]["message"]["content"]
        self.assertEqual(content, [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                "data": "QUJD"}}])


if __name__ == "__main__":
    unittest.main()
