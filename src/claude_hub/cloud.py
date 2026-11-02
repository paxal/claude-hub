"""Sessions cloud claude.ai/code."""
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from .cursor import CURSOR_SEARCH_DB
from .subagents import AGENT_TOOLS


# API interne utilisée par `claude --teleport` (non documentée : peut changer).
# Le jeton OAuth est relu à chaque appel car Claude Code le renouvelle.

CLOUD_API = "https://api.anthropic.com"
CLOUD_STATES = {"running": "busy", "idle": "idle", "requires_action": "requires_action"}

# Plateforme annoncée quand on écrit à une session Remote Control. Le serveur la
# recopie sur le message ; le Claude du terminal ne prend pour tapés par l'utilisateur
# que ceux venus de ios, android, web_claude_ai ou desktop_app. Sinon il les présente
# comme venant d'une autre session (« Another Claude session sent a message ») : ils
# ne valent pas, par exemple, accord pour une permission.
HUMAN_PLATFORM = "web_claude_ai"


KEYCHAIN_SERVICE = "Claude Code-credentials"


def claude_credentials():
    """Identifiants OAuth de Claude Code : ~/.claude/.credentials.json, ou sous macOS le
    même JSON rangé dans le trousseau. La sortie de `security` (le jeton) n'est jamais
    reprise dans un message d'erreur."""
    path = Path.home() / ".claude/.credentials.json"
    if sys.platform != "darwin" or path.exists():
        return json.loads(path.read_text())
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise OSError(f"« {KEYCHAIN_SERVICE} » absent du trousseau")
    return json.loads(r.stdout)


def cloud_auth():
    """En-têtes d'authentification de l'API (le jeton n'est jamais affiché)."""
    try:
        cred = claude_credentials()["claudeAiOauth"]
        org = json.loads((Path.home() / ".claude.json").read_text())["oauthAccount"]["organizationUuid"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"identifiants Claude introuvables ({e})")
    return {"Authorization": "Bearer " + cred["accessToken"], "anthropic-version": "2023-06-01",
            "x-organization-uuid": org}


def cloud_error(e):
    if e.code == 401:
        return RuntimeError("jeton Claude expiré : lance `claude` une fois pour le renouveler")
    return RuntimeError(f"API cloud : HTTP {e.code}")


def cloud_get(path):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(CLOUD_API + path, headers={
        **cloud_auth(), "anthropic-beta": "ccr-byoc-2025-07-29"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise cloud_error(e)


def iso_ts(text):
    from datetime import datetime
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def cloud_list():
    sessions, after = [], ""
    for _ in range(20):  # pagination
        page = cloud_get("/v1/sessions" + (f"?after_id={after}" if after else ""))
        sessions += page.get("data", [])
        if not page.get("has_more"):
            break
        after = page.get("last_id")
    res = []
    for c in sessions:
        summary = c.get("post_turn_summary") or {}
        res.append({"id": c["id"], "source": "claude-cloud", "title": c.get("title"),
                    "first": summary.get("description"),
                    "cwd": (c.get("session_context") or {}).get("cwd")
                    or {"bridge": "Remote Control"}.get(c.get("environment_kind"), "cloud"),
                    "mtime": iso_ts(c.get("updated_at")), "updated": c.get("updated_at"),
                    "live": CLOUD_STATES.get(c.get("session_status")),
                    "cloud_archived": c.get("session_status") == "archived",
                    "connected": c.get("connection_status") == "connected"})
    return res


def gui_peer_body(t):
    t = (t or "").strip()
    prefix = "Another Claude session sent a message:"
    if not t.startswith(prefix):
        return None
    return t[len(prefix):].split("\n\nThis came from another Claude session", 1)[0].strip() or None


def peer_item(body):
    """Élément du fil pour un message relayé (même logique que peer_item de remote/helper.py)."""
    m = re.match(r'<agent-message from="([^"]*)">', body)
    if not m:
        return {"role": "user", "text": body, "via": "remote"}
    t = body[m.end():].strip()
    if t.endswith("</agent-message>"):
        t = t[:-len("</agent-message>")]
    report = t.startswith("[Subagent hand-back]")
    if report and "The report follows:" in t:  # avertissement du harnais, puis le rapport
        t = t.split("The report follows:", 1)[1]
    elif report:
        t = t[len("[Subagent hand-back]"):]
    lines = [l if l.strip() else "" for l in t.split("\n")]
    n = min([len(l) - len(l.lstrip(" ")) for l in lines if l] or [0])
    return {"role": "user", "text": "\n".join(l[n:] for l in lines).strip(), "via": "agent",
            "from": m.group(1), "report": report}


def to_cse(sid):
    """session_X (API sessions) et cse_X (API code) désignent la même session."""
    return "cse_" + sid.split("_", 1)[1] if sid.startswith("session_") else sid


def remote_connected(sid):
    """La session Remote Control est-elle reliée à son Claude (terminal) en ce moment ?"""
    d = cloud_get(f"/v1/code/sessions/{to_cse(sid)}")
    d = d.get("response_shape", d)  # la réponse détaillée est enveloppée
    return d.get("connection_status") == "connected"


def post_event(sid, event):
    """Poste un événement à une session, au nom de l'utilisateur (voir HUMAN_PLATFORM)."""
    import urllib.error
    import urllib.request
    cse = to_cse(sid)
    event = {"uuid": str(uuid.uuid4()), "session_id": cse, **event}
    req = urllib.request.Request(f"{CLOUD_API}/v1/code/sessions/{cse}/events", method="POST",
                                 data=json.dumps({"events": [{"payload": event}]}).encode(), headers={
                                     **cloud_auth(), "anthropic-client-platform": HUMAN_PLATFORM,
                                     "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        raise cloud_error(e)


def remote_send(sid, text, images):
    """Envoie à une session Remote Control un message que l'utilisateur a tapé : c'est le
    Claude déjà lancé (terminal) qui le traite, comme un message écrit sur claude.ai.
    Ne jamais passer par ici un texte que l'utilisateur n'a pas écrit (voir HUMAN_PLATFORM).
    Pas de `claude -p --cloud` : la CLI n'envoie pas la plateforme, et le terminal
    présenterait le message comme venant d'une autre session."""
    content = [{"type": "image", "source": {"type": "base64", "media_type": img["type"], "data": img["data"]}}
               for img in images] + ([{"type": "text", "text": text}] if text else [])
    post_event(sid, {"type": "user", "parent_tool_use_id": None,
                     "message": {"role": "user", "content": content}})


def pending_requests(detail):
    """Demandes en attente d'une session (réponse de /v1/code/sessions/<id>), au format
    des control_request « can_use_tool » de claude -p : [(request_id, demande)].

    Le Claude relié les publie dans external_metadata.pending_actions. Pour une question
    ou un plan, request_id est vide et l'identifiant est dans suppressed_request_id (il
    sert aux notifications mobiles : pas de réponse d'un bouton, il faut ouvrir la session).
    Vide si son Claude n'est pas relié : il ne recevrait pas la réponse.
    """
    d = detail.get("response_shape", detail)
    if d.get("connection_status") != "connected":
        return []
    meta = d.get("external_metadata") or {}
    actions = meta.get("pending_actions") or ([meta["pending_action"]] if meta.get("pending_action") else [])
    details = {a.get("tool_use_id"): a for a in d.get("requires_action_details_list") or []
               if isinstance(a, dict)}
    out = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        rid = a.get("request_id") or a.get("suppressed_request_id")
        tool = a.get("tool_name")
        if not (isinstance(rid, str) and rid and isinstance(tool, str)) or tool.startswith("dialog:"):
            continue  # dialog:… : boîte de dialogue du terminal, pas une demande d'outil
        if any(r == rid for r, _ in out):
            continue
        desc = a.get("action_description")
        req = {"subtype": "can_use_tool", "tool_name": tool,
               "display_name": a.get("display_tool_name") or tool,
               "input": a.get("input") if isinstance(a.get("input"), dict) else {},
               "tool_use_id": a.get("tool_use_id"),
               "permission_suggestions": (details.get(a.get("tool_use_id")) or {}).get(
                   "permission_suggestions") or []}
        if desc and desc != a.get("raw_command") and tool not in ("AskUserQuestion", "ExitPlanMode"):
            req["description"] = desc
        out.append((rid, req))
    return out


def remote_pending(sid):
    """Demandes (question, plan, permission) qu'attend le Claude d'une session Remote
    Control ou claude.ai : [(request_id, demande)]."""
    return pending_requests(cloud_get(f"/v1/code/sessions/{to_cse(sid)}"))


def remote_respond(sid, request_id, response):
    """Répond à une demande de la session, comme claude.ai : le Claude du terminal la
    prend pour la décision de l'utilisateur. response : celle de permissions.allow/deny,
    toujours issue d'un choix fait dans claude-hub (bouton, texte tapé)."""
    post_event(sid, {"type": "control_response", "response": {
        "subtype": "success", "request_id": request_id, "response": response}})


def cloud_history(sid, limit):
    events, after = [], ""
    for _ in range(10):
        page = cloud_get(f"/v1/sessions/{sid}/events?limit=1000" + (f"&after_id={after}" if after else ""))
        events += page.get("data", [])
        if not page.get("has_more"):
            break
        after = page.get("last_id")
    items, agents = [], cloud_agents(events)
    for ev in events:
        content = (ev.get("message") or {}).get("content")
        if ev.get("type") == "user" and (ev.get("isCompactSummary") or not ev.get("isSynthetic")):
            text = content if isinstance(content, str) else "\n".join(
                b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
            body = gui_peer_body(text)
            if ev.get("isCompactSummary"):  # résumé d'une compaction, comme dans les .jsonl
                items.append({"role": "compact", "text": text})
            elif body:
                items.append(peer_item(body))
            elif text.strip() and not text.lstrip().startswith("<"):
                items.append({"role": "user", "text": text})
        elif ev.get("type") == "assistant":
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    items.append({"role": "assistant", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    items.append({"role": "tool", "name": b.get("name"), "input": b.get("input"),
                                  "id": b.get("id")})
                    if b.get("id") in agents:
                        items[-1]["agent"] = agents[b["id"]]
    return {"size": len(events), "total": len(items), "items": items[-limit:]}


def block_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def cloud_agents(events):
    """Travail des sous-agents d'une session cloud, par id de l'appel d'outil Agent.

    Le cloud ne transmet pas leurs messages, seulement leur avancement (task_progress :
    « Running … », dernier outil, jetons), leur fin (task_notification) et leur rapport
    (résultat de l'outil, ou pour un sous-agent en fond <result> de la notification,
    à défaut le résumé de task_notification, qui contient le rapport complet).
    Même forme que la commande « agent » des scripts distants, plus « end » (état final).
    """
    agents = {b.get("id"): {"meta": None, "items": [], "end": None, "usage": None, "report": None,
                            "prompt": str((b.get("input") or {}).get("prompt") or "")}
              for ev in events if ev.get("type") == "assistant"
              for b in (ev.get("message") or {}).get("content") or []
              if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in AGENT_TOOLS}
    agents.pop(None, None)
    for ev in events:
        content = (ev.get("message") or {}).get("content")
        if ev.get("type") == "system" and ev.get("tool_use_id") in agents:
            a = agents[ev["tool_use_id"]]
            if ev.get("subtype") == "task_progress":
                desc = str(ev.get("description") or "")
                desc = desc[len("Running "):] if desc.startswith("Running ") else desc
                step = {"role": "tool", "name": ev.get("last_tool_name") or "…", "input": {"description": desc}}
                if desc and (not a["items"] or a["items"][-1] != step):
                    a["items"].append(step)
                a["usage"] = ev.get("usage") or a["usage"]
            elif ev.get("subtype") == "task_notification":
                a["end"] = {"status": ev.get("status"), "summary": ev.get("summary"),
                            "usage": ev.get("usage") or a["usage"]}
        elif ev.get("type") == "user":
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in agents:
                    r = ev.get("tool_use_result")
                    if not (isinstance(r, dict) and r.get("isAsync")):  # sinon : simple accusé de lancement
                        agents[b["tool_use_id"]]["report"] = block_text(b.get("content"))
            text = block_text(content)
            m = re.search(r"<tool-use-id>([^<]+)</tool-use-id>", text) if "<task-notification>" in text else None
            report = re.search(r"<result>(.*)</result>", text, re.S) if m else None
            if report and m.group(1).strip() in agents:
                agents[m.group(1).strip()]["report"] = report.group(1).strip()
    for a in agents.values():
        a["report"] = a["report"] or (a["end"] or {}).get("summary")  # fin notée sans message <task-notification>
        if a["report"]:
            a["items"].append({"role": "assistant", "text": a["report"]})
        a["found"] = bool(a["items"] or a["end"])
        if a["prompt"]:
            a["items"].insert(0, {"role": "user", "text": a["prompt"]})
        a["total"] = len(a["items"])
        if a["end"] is None and a["report"] is not None:
            a["end"] = {"status": "completed", "usage": a["usage"]}
    return agents


def cursor_cloud_list():
    """Agents cloud de Cursor : seul l'index de recherche les connaît (titre + texte)."""
    if not CURSOR_SEARCH_DB.exists():
        return []
    import sqlite3
    with sqlite3.connect(f"file:{CURSOR_SEARCH_DB}?mode=ro", uri=True, timeout=5) as db:
        rows = db.execute("""select c.id, c.title, c.updated_at, c.is_archived, f.body
                             from conversations c join conversation_fts f on f.rowid = c.fts_rowid
                             where c.source = 'cloud-cache'""").fetchall()
    return [{"id": cid, "source": "cursor", "cursor_cloud": True, "title": title, "first": None,
             "cwd": "agent cloud Cursor", "mtime": (updated or 0) / 1000, "live": None,
             "cursor_archived": bool(archived), "body": body} for cid, title, updated, archived, body in rows]


def cursor_cloud_history(body, limit):
    items, role, buf = [], None, []
    for line in (body or "").splitlines() + ["user:"]:
        m = re.match(r"^(user|assistant):\s*$", line)
        if m:
            if role and "\n".join(buf).strip():
                items.append({"role": role, "text": "\n".join(buf).strip()})
            role, buf = m.group(1), []
        else:
            buf.append(line)
    return {"size": len(body or ""), "total": len(items), "items": items[-limit:]}
