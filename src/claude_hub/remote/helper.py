import base64, calendar, json, os, re, stat, subprocess, sys, time
from pathlib import Path
C = Path.home() / ".claude"

def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return ""

def images_of(content):
    out = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict) or b.get("type") != "image":
            continue
        src = b.get("source") or {}
        if src.get("type") == "base64" and src.get("data"):
            out.append({"type": src.get("media_type") or "image/png", "data": src["data"]})
        else:
            out.append({"omitted": True})
    return out

def apply_budget(items, budget=8 * 1024 * 1024):
    """Garde les images les plus récentes dans la limite du budget (base64)."""
    for it in reversed(items):
        for img in it.get("images") or []:
            size = len(img.get("data") or "")
            if size > budget:
                img.pop("data", None)
                img["omitted"] = True
            budget -= size
    return items

PEER_PREFIX = "Another Claude session sent a message:"
PEER_SUFFIX = "\n\nThis came from another Claude session"

def peer_body(t):
    """Texte d'origine d'un message relayé entre sessions (sans l'avertissement ajouté)."""
    t = t.strip()
    if not t.startswith(PEER_PREFIX):
        return None
    t = t[len(PEER_PREFIX):]
    return t.split(PEER_SUFFIX, 1)[0].strip() or None

AGENT_MSG = re.compile(r'<agent-message from="([^"]*)">')
HANDBACK = "[Subagent hand-back]"
REPORT_START = "The report follows:"

def dedent(t):
    """Retire l'indentation commune (le harnais indente chaque ligne d'un rapport)."""
    lines = [l if l.strip() else "" for l in t.split("\n")]
    n = min([len(l) - len(l.lstrip(" ")) for l in lines if l] or [0])
    return "\n".join(l[n:] for l in lines).strip()

def peer_item(body):
    """Élément du fil pour un message relayé : message ou rapport d'un agent, sinon envoi Remote Control."""
    m = AGENT_MSG.match(body)
    if not m:
        return {"role": "user", "text": body, "via": "remote"}
    t = body[m.end():].strip()
    if t.endswith("</agent-message>"):
        t = t[:-len("</agent-message>")]
    report = t.startswith(HANDBACK)
    if report and REPORT_START in t:  # avertissement du harnais, puis le rapport
        t = t.split(REPORT_START, 1)[1]
    elif report:
        t = t[len(HANDBACK):]
    return {"role": "user", "text": dedent(t), "via": "agent", "from": m.group(1), "report": report}

def is_noise(t):
    t = t.strip()
    return not t or t.startswith("<") or t.startswith("Caveat:")

def live_ids():
    out = {}
    for f in (C / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            os.kill(int(d["pid"]), 0)
            out[d.get("sessionId")] = d.get("status") or "idle"
        except Exception:
            pass
    return out

def msg_time(line):
    """Date (epoch) d'une ligne de message, None si elle n'est pas lisible."""
    try:
        ts = json.loads(line).get("timestamp")
        t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, AttributeError, TypeError):
        return None
    ms = ts[20:23] if ts[19:20] == "." else ""
    return t + int(ms.ljust(3, "0")) / 1000.0 if ms.isdigit() else t

COMMAND_NAME = re.compile(r"<command-name>(/[^<]*)</command-name>")
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)

def command_text(t):
    """Commande slash telle que tapée (« /nom arguments ») d'après le message que claude
    enregistre pour elle (<command-name>, <command-args>…) ; None si t n'en est pas une."""
    m = COMMAND_NAME.search(t) if t.lstrip().startswith("<command-") else None
    if not m:
        return None
    a = COMMAND_ARGS.search(t)
    return " ".join((m.group(1) + " " + (a.group(1) if a else "")).split())

def is_caveat(line):
    """Avertissement que claude écrit juste avant une commande locale (/rename, /copy…)."""
    return '<local-command-caveat>' in line and '"type":"user"' in line

def is_command(line, caveat):
    """Commande locale de claude (/rename…), avertissement compris : pas un message, ni une
    activité. caveat : le message précédent était l'avertissement. Une commande qui en est
    dépourvue (skill, /plan suivi d'une demande) part à Claude : c'est un message."""
    return is_caveat(line) or (caveat and '"content":"<command-name>/' in line)

def is_stop(line):
    """Ligne qui change l'ordre de la liste : message envoyé, réponse finie ou question
    posée. Les étapes d'une réponse (outils, réflexion, résultats) n'en font pas partie."""
    if '"isMeta":true' in line or '"isSidechain":true' in line or '"isCompactSummary":true' in line:
        return False  # isCompactSummary : résumé écrit par claude en compactant, pas un message
    if '"type":"user"' in line:
        return '"type":"tool_result"' not in line
    if '"type":"assistant"' in line:
        return ('"stop_reason":"tool_use"' not in line or '"name":"AskUserQuestion"' in line
                or '"name":"ExitPlanMode"' in line)
    return False

def summary(p):
    s = {"id": p.stem, "mtime": p.stat().st_mtime, "size": p.stat().st_size,
         "cwd": None, "title": None, "first": None, "next": None, "msgs": 0, "bridge": None}
    last = None  # dernier arrêt (is_stop) : sa date sert au tri, pas celle du fichier
    caveat = False  # message précédent : avertissement d'une commande locale
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type":"user"' in line:
                skip, caveat = is_command(line, caveat), is_caveat(line)
                if skip:
                    continue
            if is_stop(line):
                last = line
            if '"type":"user"' in line:
                s["msgs"] += 1
                if s["first"] and s["cwd"]:
                    continue
            elif not any(k in line for k in ('"ai-title"', '"agent-name"', '"custom-title"', '"continued-in"',
                                              '"bridge-session"')):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("type")
            if t == "user":
                s["cwd"] = s["cwd"] or r.get("cwd")
                txt = text_of(r.get("message", {}).get("content"))
                txt = command_text(txt) or txt
                if not s["first"] and not r.get("isMeta") and not is_noise(txt):
                    s["first"] = " ".join(txt.split())[:200]
            elif t == "custom-title":
                s["title"] = r.get("customTitle") or s["title"]
            elif t == "agent-name":
                s["title"] = r.get("agentName") or s["title"]
            elif t == "ai-title" and not s["title"]:
                s["title"] = r.get("aiTitle")
            elif t == "bridge-session":
                s["bridge"] = r.get("bridgeSessionId") or s["bridge"]
            elif t == "continued-in":
                s["next"] = r.get("continuedInSessionId")
    # une session restée ouverte dans un terminal réécrit ses métadonnées en fin de
    # fichier sans nouveau message : la date du fichier ne suffit pas.
    s["mtime"] = (msg_time(last) if last else None) or s["mtime"]
    return s

def find(sid):
    for p in (C / "projects").glob("*/" + sid + ".jsonl"):
        return p
    return None

def cmd_list():
    live = live_ids()
    res = []
    for p in (C / "projects").glob("*/*.jsonl"):
        s = summary(p)
        if s["msgs"] and not s["next"]:
            s["live"] = live.get(s["id"])
            s["cwd_exists"] = os.path.isdir(s["cwd"]) if s["cwd"] else None
            res.append(s)
    res.sort(key=lambda s: -s["mtime"])
    print(json.dumps({"home": str(Path.home()), "sessions": res}))

def last_bridge(p):
    """Dernière session Remote Control (cse_…) associée à la conversation."""
    bridge = None
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"bridge-session"' in line:
                try:
                    bridge = json.loads(line).get("bridgeSessionId") or bridge
                except ValueError:
                    pass
    return bridge

def cmd_stat(sid):
    p = find(sid)
    st = p.stat() if p else None
    print(json.dumps({"size": st.st_size if st else 0, "live": live_ids().get(sid),
                      "bridge": last_bridge(p) if p else None}))

TODO_TOOLS = ("TodoWrite", "TaskCreate", "TaskUpdate")

def created_task(r, creating):
    """Numéro attribué par TaskCreate, d'après le résultat de l'outil ; None sinon."""
    content = r.get("message", {}).get("content")
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in creating:
            res = r.get("toolUseResult")
            task = res.get("task") if isinstance(res, dict) else None
            text = b.get("content") if isinstance(b.get("content"), str) else " ".join(
                str(x.get("text") or "") for x in b.get("content") or [] if isinstance(x, dict))
            m = re.search(r"Task #(\w+) created", text)
            tid = str(task["id"]) if isinstance(task, dict) and task.get("id") is not None else (
                m.group(1) if m else None)
            return {"created": b["tool_use_id"], "task": tid}
    return None

def usage_tokens(u):
    """Taille du contexte après un message de Claude : jetons d'entrée (cache compris) et de sortie."""
    if not isinstance(u, dict):
        return 0
    return sum(u[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                              "output_tokens") if type(u.get(k)) is int)

def transcript_items(p, sidechain=False, todos=None, usage=None):
    """Messages et appels d'outils d'une transcription ; renvoie (items, dernier modèle, son effort).

    todos : liste complétée par les appels de TodoWrite, TaskCreate et TaskUpdate de
    toute la transcription, et par les numéros attribués aux tâches créées.
    usage : dict dont "context" reçoit la taille du contexte au dernier message de Claude
    ({"tokens", "model"}), None après une compaction.
    """
    items, model, effort, creating = [], None, None, set()
    caveat = False  # message précédent : avertissement d'une commande locale (/rename…)
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if ('"type":"user"' not in line and '"type":"assistant"' not in line
                    and '"queued_command"' not in line):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("isSidechain") and not sidechain:
                continue
            if r.get("type") == "attachment":
                # Message envoyé pendant une réponse : claude l'a lu entre deux étapes.
                a = r.get("attachment") or {}
                if a.get("type") == "queued_command" and a.get("commandMode", "prompt") == "prompt":
                    txt, imgs = text_of(a.get("prompt")), images_of(a.get("prompt"))
                    if imgs or not is_noise(txt):
                        items.append({"role": "user", "text": "" if is_noise(txt) else txt,
                                      "images": imgs, "queued": True})
                continue
            content = r.get("message", {}).get("content")
            if r.get("isCompactSummary"):  # conversation compactée : claude repart de ce résumé
                items.append({"role": "compact", "text": text_of(content)})
                if usage is not None:
                    usage["context"] = None
                continue
            if r.get("type") == "user":
                local, caveat = caveat, bool(r.get("isMeta")) and "<local-command-caveat>" in text_of(content)
            if r.get("isMeta"):
                # Message relayé par Remote Control (Claude Hub, autre session) : on garde son texte.
                body = peer_body(text_of(content)) if (r.get("origin") or {}).get("kind") == "peer" else None
                if body:
                    items.append(peer_item(body))
                continue
            if r["type"] == "user":
                ev = created_task(r, creating) if creating and todos is not None else None
                if ev:
                    todos.append(ev)
                txt = text_of(content)
                if not local:  # commande tapée (skill, /plan …) : affichée comme dans le terminal
                    txt = command_text(txt) or txt
                imgs = images_of(content)
                if imgs or not is_noise(txt):
                    items.append({"role": "user", "text": "" if is_noise(txt) else txt,
                                  "images": imgs})
                    if r.get("parentUuid") and not sidechain:  # point de copie « avant ce message »
                        items[-1]["parent"] = r["parentUuid"]
                continue
            m = r.get("message", {}).get("model")
            if m and m != "<synthetic>":  # dernier modèle qui a répondu, et son effort
                model, effort = m, r.get("effort")
                n = usage_tokens(r.get("message", {}).get("usage")) if usage is not None else 0
                if n:
                    usage["context"] = {"tokens": n, "model": m}
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    items.append({"role": "assistant", "text": b["text"]})
                    if r.get("uuid") and not sidechain:  # point de copie « jusqu'ici »
                        items[-1]["uuid"] = r["uuid"]
                elif b.get("type") == "tool_use":
                    items.append({"role": "tool", "name": b.get("name"), "input": b.get("input"),
                                  "id": b.get("id")})
                    if todos is not None and b.get("name") in TODO_TOOLS:
                        todos.append(items[-1])
                        if b.get("name") == "TaskCreate":
                            creating.add(b.get("id"))
    return items, model, effort

def cmd_history(sid, limit):
    p = find(sid)
    todos, usage = [], {"context": None}
    items, model, effort = transcript_items(p, todos=todos, usage=usage) if p else ([], None, None)
    print(json.dumps({"size": p.stat().st_size if p else 0, "model": model, "effort": effort,
                      "total": len(items), "items": apply_budget(items[-limit:]), "todos": todos,
                      "agents": agents_list(p) if p else [], "context": usage["context"]}))

def cmd_agent(sid, tool_id, limit):
    """Transcription du sous-agent lancé par l'appel d'outil tool_id (subagents/agent-*.jsonl)."""
    p = find(sid)
    res = {"found": False, "meta": None, "total": 0, "items": []}
    base = str(p)[:-len(".jsonl")] + "/subagents" if p else None
    for root, _dirs, names in os.walk(base) if base else []:
        for n in sorted(names):
            if not n.endswith(".meta.json"):
                continue
            try:
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            path = os.path.join(root, n[:-len(".meta.json")] + ".jsonl")
            if not isinstance(meta, dict) or meta.get("toolUseId") != tool_id or not os.path.isfile(path):
                continue
            items, model, _effort = transcript_items(path, sidechain=True)
            res = {"found": True, "meta": meta, "model": model, "total": len(items),
                   "items": apply_budget(items[-limit:])}
            return print(json.dumps(res))
    print(json.dumps(res))

def short_input(inp):
    """Entrée d'un appel d'outil réduite à ses textes courts (aperçu de la dernière étape)."""
    return {k: v[:300] for k, v in inp.items() if isinstance(v, str)} if isinstance(inp, dict) else {}

def agent_summary(path, meta):
    """État d'un sous-agent d'après sa transcription : début, dernière étape, fini ou non.

    Fini : son dernier message est une réponse de Claude sans appel d'outil (il a rendu
    la main) ; interrompu : c'est la marque d'interruption de claude ; sinon il attend un
    outil ou la suite du modèle.
    """
    name = os.path.basename(path)[:-len(".jsonl")]
    a = {"id": name[len("agent-"):] if name.startswith("agent-") else name,
         "type": meta.get("agentType"), "description": meta.get("description"),
         "tool": meta.get("toolUseId"), "parent": meta.get("parentAgentId"),
         "depth": meta.get("spawnDepth") or 1, "background": meta.get("requestShape") == "background",
         "size": os.path.getsize(path), "start": None, "end": None, "steps": 0,
         "done": False, "interrupted": False, "last": None}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") not in ("user", "assistant") or r.get("isMeta"):
                continue
            t = msg_time(line)
            a["start"] = a["start"] or t
            a["end"] = t or a["end"]
            content = r.get("message", {}).get("content")
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            if r["type"] == "user":
                a["done"] = False
                a["interrupted"] = text_of(content).startswith("[Request interrupted by user")
                continue
            a["interrupted"] = False
            tools = [b for b in blocks if b.get("type") == "tool_use"]
            texts = [b["text"] for b in blocks if b.get("type") == "text" and (b.get("text") or "").strip()]
            a["steps"] += len(tools)
            if tools:
                a["last"] = {"role": "tool", "name": tools[-1].get("name"),
                             "input": short_input(tools[-1].get("input"))}
            elif texts:
                a["last"] = {"role": "assistant", "text": texts[-1][:300]}
            a["done"] = not tools and bool(texts or blocks)
    return a

def agents_list(p):
    """Sous-agents de la conversation p (subagents/, y compris imbriqués), du plus ancien au plus récent."""
    res = []
    base = str(p)[:-len(".jsonl")] + "/subagents" if p else None
    for root, _dirs, names in os.walk(base) if base else []:
        for n in sorted(names):
            if not n.endswith(".meta.json"):
                continue
            path = os.path.join(root, n[:-len(".meta.json")] + ".jsonl")
            try:
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict) and os.path.isfile(path):
                    res.append(agent_summary(path, meta))
            except (OSError, ValueError):
                continue
    res.sort(key=lambda a: (a["start"] or 0, a["id"]))
    return res

def cmd_agents(sid):
    p = find(sid)
    print(json.dumps({"live": live_ids().get(sid), "agents": agents_list(p) if p else []}))

RESULT_LIMIT = 100000  # caractères de sortie renvoyés au plus

def result_text(content):
    """Texte d'un bloc tool_result (chaîne, ou liste de blocs texte et image)."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text") or ""))
        elif isinstance(b, dict) and b.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)

def find_result(path, tool_id):
    key = '"tool_use_id":' + json.dumps(tool_id)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if key not in line or '"type":"user"' not in line:
                continue
            try:
                content = json.loads(line).get("message", {}).get("content")
            except ValueError:
                continue
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tool_id:
                    return b
    return None

def cmd_result(sid, tool_id):
    """Sortie de l'appel d'outil tool_id, dans la conversation ou un de ses sous-agents."""
    p = find(sid)
    paths = [str(p)] if p else []
    base = str(p)[:-len(".jsonl")] + "/subagents" if p else None
    for root, _dirs, names in os.walk(base) if base else []:
        paths += [os.path.join(root, n) for n in sorted(names) if n.endswith(".jsonl")]
    for path in paths:
        b = find_result(path, tool_id)
        if b:
            text = result_text(b.get("content"))
            return print(json.dumps({"found": True, "error": bool(b.get("is_error")),
                                     "length": len(text), "text": text[:RESULT_LIMIT]}))
    print(json.dumps({"found": False}))

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
EDIT_LIMIT = 200000  # caractères gardés de chaque texte d'une modification
EDIT_KEYS = ("file_path", "notebook_path", "old_string", "new_string", "replace_all", "content",
             "new_source", "edit_mode", "cell_id")

def edit_input(inp):
    """Entrée d'une modification réduite à ce qui sert à son diff (textes tronqués)."""
    cut = lambda v: v[:EDIT_LIMIT] if isinstance(v, str) else v
    out = {k: cut(inp[k]) for k in EDIT_KEYS if k in inp}
    if isinstance(inp.get("edits"), list):
        out["edits"] = [{k: cut(e[k]) for k in ("old_string", "new_string") if k in e}
                        for e in inp["edits"] if isinstance(e, dict)]
    return out

def file_edits(path, agent=None):
    """Modifications de fichiers d'une transcription (Edit, MultiEdit, Write, NotebookEdit) :
    fichier, issue (ok, error, pending) et diff calculé par claude (structuredPatch)."""
    out, by_id = [], {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            tool_use = '"type":"assistant"' in line and '"tool_use"' in line
            if not tool_use and not (by_id and '"tool_result"' in line):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("isSidechain") and agent is None:
                continue
            content = r.get("message", {}).get("content")
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if r.get("type") == "assistant" and b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    e = {"id": b.get("id"), "tool": b.get("name"),
                         "path": inp.get("file_path") or inp.get("notebook_path") or None,
                         "time": msg_time(line), "agent": agent, "status": "pending",
                         "input": edit_input(inp), "patch": None, "created": False}
                    out.append(e)
                    if isinstance(b.get("id"), str):
                        by_id[b["id"]] = e
                elif r.get("type") == "user" and b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                    e = by_id[b["tool_use_id"]]
                    e["status"] = "error" if b.get("is_error") else "ok"
                    res = r.get("toolUseResult")
                    if isinstance(res, dict):
                        if isinstance(res.get("structuredPatch"), list) and res["structuredPatch"]:
                            e["patch"] = res["structuredPatch"]
                        e["created"] = res.get("type") == "create"
    return out

def cmd_edits(sid):
    """Fichiers modifiés par la conversation et ses sous-agents, dans l'ordre des modifications."""
    p = find(sid)
    paths = []
    base = str(p)[:-len(".jsonl")] + "/subagents" if p else None
    for root, _dirs, names in os.walk(base) if base else []:
        paths += [os.path.join(root, n) for n in names if n.endswith(".jsonl")]
    edits = file_edits(str(p)) if p else []
    for path in sorted(paths):
        name = os.path.basename(path)[:-len(".jsonl")]
        edits += file_edits(path, name[len("agent-"):] if name.startswith("agent-") else name)
    edits.sort(key=lambda e: e["time"] or 0)
    print(json.dumps({"size": p.stat().st_size if p else 0, "edits": edits}))

def norm(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))

def snippet(text, word, width=70):
    flat = " ".join(text.split())
    i = norm(flat).find(word)
    if i < 0:
        return flat[:2 * width]
    start = max(0, i - width)
    return ("…" if start else "") + flat[start:i + len(word) + width] + ("…" if i + len(word) + width < len(flat) else "")

def cmd_search(query, limit):
    words = norm(query).split()
    files = sorted((str(p) for p in (C / "projects").glob("*/*.jsonl")),
                   key=lambda f: -os.path.getmtime(f))  # les plus récentes d'abord
    if not words or not files:
        return print("{}")
    try:  # pré-filtre rapide sur le mot le plus long
        out = subprocess.run(["grep", "-l", "-i", "-F", "--", max(query.split(), key=len)] + files,
                             stdout=subprocess.PIPE, universal_newlines=True).stdout
        files = out.splitlines()
    except OSError:
        pass
    res = {}
    for f in files:
        found, first = set(), None
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if (('"type":"user"' not in line and '"type":"assistant"' not in line)
                        or '"tool_result"' in line or '"isCompactSummary":true' in line):
                    continue
                low = norm(line)
                if not any(w in low for w in words):
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                content = r.get("message", {}).get("content")
                texts = [text_of(content)]
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                        texts.append(" ".join(v for v in b["input"].values() if isinstance(v, str)))
                for t in texts:
                    nt = norm(t)
                    hit = [w for w in words if w in nt]
                    found.update(hit)
                    if hit and first is None and not (r["type"] == "user" and is_noise(t)):
                        first = ("user" if r["type"] == "user" else "assistant", snippet(t, hit[0]))
        if first and found >= set(words):
            res[Path(f).stem] = {"role": first[0], "snippet": first[1]}
            if len(res) >= limit:
                break
    print(json.dumps(res))

def cmd_dirs(path):
    p = os.path.abspath(os.path.expanduser(path or "~"))
    res = {"path": p, "home": str(Path.home()), "dirs": []}
    try:
        names = [e.name for e in os.scandir(p) if e.is_dir()]
        res["dirs"] = sorted(names, key=lambda n: (n.startswith("."), n.lower()))
    except OSError as e:
        res["error"] = e.strerror or str(e)
    print(json.dumps(res))

def cmd_defaults():
    """Modèle et effort que prendrait claude sans --model ni --effort (null si non réglés)."""
    try:
        with open(str(C / "settings.json")) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st = st if isinstance(st, dict) else {}
    env = st.get("env") if isinstance(st.get("env"), dict) else {}
    pick = lambda var, key: os.environ.get(var) or env.get(var) or st.get(key) or None
    print(json.dumps({"model": pick("ANTHROPIC_MODEL", "model"),
                      "effort": pick("CLAUDE_CODE_EFFORT_LEVEL", "effortLevel")}))

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
               ".bmp": "image/bmp", ".ico": "image/x-icon", ".tif": "image/tiff",
               ".tiff": "image/tiff", ".avif": "image/avif", ".jxl": "image/jxl"}
READ_LIMIT = 1000000            # texte : au-delà, coupé à la dernière fin de ligne
IMAGE_LIMIT = 8 * 1024 * 1024

def cmd_read(path):
    """Fichier cité dans une conversation : texte (tronqué) ou image en base64."""
    p = os.path.abspath(os.path.expanduser(path))
    res = {"path": p}
    try:
        st = os.stat(p)
        if stat.S_ISDIR(st.st_mode):
            res["error"] = "c'est un dossier"
        elif not stat.S_ISREG(st.st_mode):
            res["error"] = "pas un fichier ordinaire"
        else:
            res["size"] = st.st_size
            kind = IMAGE_TYPES.get(os.path.splitext(p)[1].lower())
            if kind and st.st_size > IMAGE_LIMIT:
                res["error"] = "image trop lourde"
            elif kind:
                with open(p, "rb") as f:
                    res["image"] = base64.b64encode(f.read()).decode("ascii")
                res["type"] = kind
            else:
                with open(p, "rb") as f:
                    data = f.read(READ_LIMIT)
                if b"\0" in data:
                    res["error"] = "fichier binaire"
                else:
                    res["truncated"] = st.st_size > len(data)
                    if res["truncated"] and b"\n" in data:
                        data = data[:data.rindex(b"\n") + 1]
                    res["text"] = data.decode("utf-8", "replace")
    except OSError as e:
        res["error"] = "lecture refusée" if e.errno in (1, 13) else "introuvable"
    print(json.dumps(res))

a = sys.argv[1:]
{"list": lambda: cmd_list(), "stat": lambda: cmd_stat(a[1]),
 "exists": lambda: print(json.dumps({d: os.path.isdir(d) for d in json.loads(a[1])})),
 "search": lambda: cmd_search(a[1], int(a[2])),
 "live": lambda: print(json.dumps(live_ids())),
 "dirs": lambda: cmd_dirs(a[1]),
 "defaults": lambda: cmd_defaults(),
 "read": lambda: cmd_read(a[1]),
 "history": lambda: cmd_history(a[1], int(a[2])),
 "agent": lambda: cmd_agent(a[1], a[2], int(a[3])),
 "agents": lambda: cmd_agents(a[1]),
 "result": lambda: cmd_result(a[1], a[2]),
 "edits": lambda: cmd_edits(a[1])}[a[0]]()
