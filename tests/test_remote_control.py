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


QUESTION = {"questions": [{"header": "Color", "multiSelect": False, "question": "Which color?",
                           "options": [{"label": "Red", "description": "Choose red"},
                                       {"label": "Blue", "description": "Choose blue"}]}]}


def detail(actions, status="connected", details=()):
    """Réponse de /v1/code/sessions/<id> (forme observée, claude 2.1.304)."""
    return {"response_shape": {"connection_status": status, "external_metadata": {
        "pending_action": actions[0] if actions else None, "pending_actions": actions},
        "requires_action_details_list": list(details)}}


class PendingRequestsTest(unittest.TestCase):
    def test_question(self):
        # Question : request_id vide, l'identifiant est dans suppressed_request_id.
        reqs = cloud.pending_requests(detail([{
            "action_description": "Color", "analytics_tool_name": "AskUserQuestion",
            "display_tool_name": "Question", "input": QUESTION, "request_id": "",
            "suppressed_request_id": "6cd1dcec", "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_1"}]))
        self.assertEqual(reqs, [("6cd1dcec", {
            "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "display_name": "Question",
            "input": QUESTION, "tool_use_id": "toolu_1", "permission_suggestions": []})])

    def test_permission(self):
        sugg = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "make"}],
                 "behavior": "allow", "destination": "localSettings"}]
        reqs = cloud.pending_requests(detail(
            [{"action_description": "Compile", "display_tool_name": "Bash", "raw_command": "make",
              "input": {"command": "make"}, "request_id": "r1", "tool_name": "Bash", "tool_use_id": "t1"},
             {"action_description": "make", "display_tool_name": "Bash", "raw_command": "make",
              "input": {"command": "make"}, "request_id": "r2", "tool_name": "Bash", "tool_use_id": "t2"}],
            details=[{"tool_use_id": "t1", "permission_suggestions": sugg}]))
        self.assertEqual([rid for rid, _ in reqs], ["r1", "r2"])
        self.assertEqual(reqs[0][1]["description"], "Compile")
        self.assertEqual(reqs[0][1]["permission_suggestions"], sugg)
        self.assertNotIn("description", reqs[1][1])  # la commande elle-même : déjà affichée

    def test_ignorees(self):
        self.assertEqual(cloud.pending_requests(detail([{"tool_name": "AskUserQuestion", "request_id": "r",
                                                         "input": QUESTION}], status="disconnected")), [])
        self.assertEqual(cloud.pending_requests(detail([
            {"tool_name": "dialog:chrome_setup", "request_id": "r1", "tool_use_id": "t"},
            {"tool_name": "Bash", "request_id": "", "tool_use_id": "t2"}])), [])
        self.assertEqual(cloud.pending_requests({"connection_status": "connected"}), [])


class RemoteRespondTest(unittest.TestCase):
    def test_reponse(self):
        sent = []
        with mock.patch.object(cloud, "cloud_auth", return_value={"Authorization": "Bearer x"}), \
                mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout: sent.append(req)
                           or mock.MagicMock()):
            response = {"behavior": "allow", "updatedInput": dict(QUESTION, answers={"Which color?": "Blue"})}
            cloud.remote_respond("session_abc", "6cd1dcec", response)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].full_url, cloud.CLOUD_API + "/v1/code/sessions/cse_abc/events")
        self.assertEqual(sent[0].get_header("Anthropic-client-platform"), "web_claude_ai")
        event = json.loads(sent[0].data)["events"][0]["payload"]
        self.assertEqual(event["type"], "control_response")
        self.assertEqual(event["session_id"], "cse_abc")
        self.assertTrue(event["uuid"])
        # Même forme que la réponse de claude.ai enregistrée dans la session
        self.assertEqual(event["response"], {"subtype": "success", "request_id": "6cd1dcec",
                                             "response": response})


if __name__ == "__main__":
    unittest.main()
