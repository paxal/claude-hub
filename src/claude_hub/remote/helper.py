import base64, calendar, json, os, re, signal, stat, subprocess, sys, time
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

def live_sessions():
    """Fichiers ~/.claude/sessions/<pid>.json des claude en cours, par id de conversation."""
    out = {}
    for f in (C / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            os.kill(int(d["pid"]), 0)
            out[d.get("sessionId")] = d
        except Exception:
            pass
    return out

def live_ids():
    return {sid: d.get("status") or "idle" for sid, d in live_sessions().items()}

def ms_of(v):
    """Date en millisecondes du fichier de session (None si absente ou invalide)."""
    return v if type(v) in (int, float) else None

def text_or_none(v):
    """Texte non vide du fichier de session (None sinon)."""
    return v if isinstance(v, str) and v else None

def cmd_live():
    """État de chaque session en cours, et sous « <id>:since » la date (ms) où elle y est passée."""
    out = {}
    for sid, d in live_sessions().items():
        out[sid] = d.get("status") or "idle"
        since = ms_of(d.get("statusUpdatedAt"))
        if since is not None:
            out[sid + ":since"] = since
    print(json.dumps(out))

def record_time(r):
    """Date (epoch) d'un enregistrement de la transcription, None si elle n'est pas lisible."""
    try:
        ts = r.get("timestamp")
        t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, AttributeError, TypeError):
        return None
    ms = ts[20:23] if ts[19:20] == "." else ""
    return t + int(ms.ljust(3, "0")) / 1000.0 if ms.isdigit() else t

def msg_time(line):
    """Date (epoch) d'une ligne de message, None si elle n'est pas lisible."""
    try:
        return record_time(json.loads(line))
    except ValueError:
        return None

def stamp(items, start, t):
    """Heure (epoch, à la seconde) des éléments items[start:], tirés d'un même enregistrement."""
    if t is not None:
        for it in items[start:]:
            it["ts"] = int(t)

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

BASH_TAG = re.compile(r"<(bash-input|bash-stdout|bash-stderr)>(.*?)</\1>", re.S)

def bash_items(t, items):
    """Commandes « ! » d'un message : mode bash du terminal (commande puis sortie, en deux
    messages) ou envoyées par claude-hub (avant le texte). Ajoutées à items ({"role":
    "bash", "command", "stdout", "stderr"}) ; renvoie le reste du texte."""
    if "<bash-" not in t:
        return t

    def take(m):
        tag, body = m.group(1), m.group(2)
        if tag == "bash-input":
            items.append({"role": "bash", "command": body.strip()})
            return ""
        key = "stdout" if tag == "bash-stdout" else "stderr"
        if not (items and items[-1].get("role") == "bash" and key not in items[-1]):
            items.append({"role": "bash", "command": ""})  # sortie sans sa commande
        items[-1][key] = body
        return ""
    return BASH_TAG.sub(take, t).strip()

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
                txt = bash_items(command_text(txt) or txt, [])
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

def list_files():
    """[(date du fichier en ms, id, chemin)] des transcriptions, la dernière écrite en tête.
    Des ms entières : python et node doivent trouver les mêmes dates."""
    out = []
    for p in (C / "projects").glob("*/*.jsonl"):
        try:
            out.append((p.stat().st_mtime_ns // 1000000, p.stem, p))
        except OSError:
            continue  # disparue entre-temps
    out.sort(key=lambda f: (f[0], f[1]), reverse=True)
    return out

def cmd_list(opts):
    """Conversations de l'hôte, par pages, dans l'ordre des fichiers (dernier écrit en tête) :
    lire une transcription entière coûte, un stat presque rien. "count" : taille de la page
    (toutes sans lui) ; "after" : la page commence après ce fichier ([ms, id], le "more" de
    la page précédente, null après la dernière) ; "ids" : à joindre où qu'elles soient
    (épinglées, trouvées par une recherche), comme les conversations ouvertes dans un claude
    sur la première page ("count" à 0 : seulement "ids")."""
    live = live_ids()
    files = list_files()
    after, count = opts.get("after"), opts.get("count")
    rest = [f for f in files if after is None or [f[0], f[1]] < after]
    page = rest if count is None else rest[:count]
    more = [page[-1][0], page[-1][1]] if page and count is not None and len(rest) > count else None
    wanted = set(opts.get("ids") or []) | (set(live) if after is None and count != 0 else set())
    seen = {f[1] for f in page}
    page = page + [f for f in files if f[1] in wanted and f[1] not in seen]
    res = []
    for ms, sid, p in page:
        s = summary(p)
        if s["msgs"] and not s["next"]:
            s["file_ms"] = ms
            s["live"] = live.get(sid)
            s["cwd_exists"] = os.path.isdir(s["cwd"]) if s["cwd"] else None
            res.append(s)
    res.sort(key=lambda s: -s["mtime"])
    print(json.dumps({"home": str(Path.home()), "sessions": res, "notes": notes_index(), "more": more}))

# Notes de claude-hub : ~/.config/claude-hub/notes/<id>.md (pas dans ~/.claude).
NOTE_EXCERPT = 1000
NOTE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

def notes_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(str(Path.home()), ".config")
    return os.path.join(base, "claude-hub", "notes")

def read_note(p):
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()

def notes_index():
    """Début de chaque note rangée sur cet hôte, par id de conversation."""
    d, out = notes_dir(), {}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if n.startswith(".") or not n.endswith(".md"):
            continue
        try:
            t = read_note(os.path.join(d, n))
        except OSError:
            continue
        if t.strip():
            out[n[:-3]] = t[:NOTE_EXCERPT]
    return out

def cmd_note(sid):
    """Texte de la note d'une conversation (null s'il n'y en a pas)."""
    if not NOTE_ID.match(sid):
        return print(json.dumps({"error": "identifiant invalide"}))
    try:
        res = {"text": read_note(os.path.join(notes_dir(), sid + ".md"))}
    except OSError as e:
        res = {"text": None} if e.errno == 2 else {"error": "lecture refusée"}
    print(json.dumps(res))

TASK_ID = re.compile(r"<task-id>([^<]+)</task-id>")
EVENT = re.compile(r"<event>(.*?)</event>", re.S)
EVENT_LIMIT = 500  # caractères gardés du dernier événement d'un monitor
TURN_LINES = 20  # dernières lignes « user » ou « assistant » gardées pour trouver la fin de la réponse
BASH_INPUT = re.compile(r"<bash-input>(.*?)</bash-input>", re.S)
USER_BACKGROUNDED = re.compile(r"<bash-stdout>Command was manually backgrounded by user with ID: (\w+)")

def user_record(line):
    """Ligne « user » de la conversation principale (pas d'un sous-agent), None sinon."""
    try:
        r = json.loads(line)
    except ValueError:
        return None
    return r if isinstance(r, dict) and r.get("type") == "user" and not r.get("isSidechain") else None

def shell_event(line, last_input):
    """Shell ou monitor lancé en arrière-plan, ou arrêté, d'après une ligne : (« start », id,
    appel d'outil, commande, « shell » ou « monitor »), (« stop », id, None, None, None) ou
    None ; et la dernière commande « ! » tapée. Shell : run_in_background, délai dépassé,
    Ctrl+B (backgroundTaskId), ou commande « ! » renvoyée en arrière-plan. Monitor : résultat
    de l'outil Monitor (taskId et timeoutMs). Arrêt : résultat de TaskStop (ou de l'ancien
    KillShell), qui n'écrit pas de <task-notification>."""
    if not ('"backgroundTaskId"' in line or "<bash-" in line or "Successfully" in line
            or '"timeoutMs"' in line):
        return None, last_input
    r = user_record(line)
    if r is None:
        return None, last_input
    res, content = r.get("toolUseResult"), (r.get("message") or {}).get("content")
    shell = isinstance(res, dict) and res.get("backgroundTaskId")
    if shell or (isinstance(res, dict) and res.get("taskId") and "timeoutMs" in res):
        tool = None
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tool = b.get("tool_use_id")
                break
        return (("start", str(res["backgroundTaskId"]), tool, None, "shell") if shell
                else ("start", str(res["taskId"]), tool, None, "monitor")), last_input
    if isinstance(res, dict) and str(res.get("message") or "").startswith("Successfully"):
        tid = res.get("task_id") or res.get("shell_id")
        return (("stop", str(tid), None, None, None) if tid else None), last_input
    t = text_of(content)
    m = BASH_INPUT.search(t)
    if m:
        last_input = m.group(1).strip()
    m = USER_BACKGROUNDED.search(t)
    return (("start", m.group(1), None, last_input, "shell") if m else None), last_input

def json_text(raw):
    """Texte d'un morceau de chaîne JSON pris tel quel dans une ligne (échappements décodés)."""
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw

def notified(line):
    """Tâches des <task-notification> d'une ligne : [(id, événement)], événement None quand
    la notification dit la tâche finie. Un monitor en envoie une à chaque événement (<event>,
    sans <status>) ; il finit sur un <status>, ou sur un message de Claude Code entre
    crochets (« [Monitor expired …] », « [Monitor stopped] »…)."""
    out = []
    for part in line.split("<task-notification>")[1:]:
        part = part.split("</task-notification>", 1)[0]
        m = TASK_ID.search(part)
        if not m:
            continue
        ev = EVENT.search(part) if "<status>" not in part else None
        text = json_text(ev.group(1)) if ev else None
        out.append((m.group(1), None if text is None or text.startswith("[Monitor ") else text))
    return out

def scan_main(p, started):
    """Une passe sur la conversation p : dernière session Remote Control (cse_…), workflows
    lancés en arrière-plan depuis started (ms, démarrage du processus claude ; un workflow
    plus ancien est mort avec son processus) et pas encore notifiés comme finis, lignes
    écrites depuis la fin du dernier tour de Claude (None si aucun tour n'est fini), et
    shells et monitors lancés en arrière-plan depuis started, ni notifiés comme finis ni
    arrêtés ({"id", "kind" : shell ou monitor, "tool" : appel d'outil, "command" : commande
    « ! », "start" : epoch, "event" : dernier événement d'un monitor}). Aussi : sous-agents
    lancés en arrière-plan depuis started et pas encore notifiés comme finis (ids), et si la
    réponse de Claude est finie (« answered » : son dernier message clôt le tour)."""
    bridge, launched, ended, tail, shells, last_input = None, {}, set(), None, [], None
    agents, turns = [], []
    events = {}  # monitor -> son dernier événement
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"bridge-session"' in line:
                try:
                    bridge = json.loads(line).get("bridgeSessionId") or bridge
                except (ValueError, AttributeError):
                    pass
            ev, last_input = shell_event(line, last_input)
            if ev and ev[0] == "stop":
                ended.add(ev[1])
            elif ev:
                t = msg_time(line)
                if started is None or t is None or t * 1000 >= started:
                    shells.append({"id": ev[1], "kind": ev[4], "tool": ev[2], "command": ev[3], "start": t})
            if '"local_workflow"' in line:
                try:
                    r = json.loads(line)
                    res = r.get("toolUseResult")
                    if (r.get("type") == "user" and not r.get("isSidechain") and isinstance(res, dict)
                            and res.get("taskType") == "local_workflow"
                            and res.get("status") == "async_launched" and res.get("taskId")):
                        t = msg_time(line)
                        if started is None or t is None or t * 1000 >= started:
                            launched[str(res["taskId"])] = res
                except (ValueError, AttributeError):
                    pass
            if '"async_launched"' in line and '"agentId"' in line:
                try:
                    r = json.loads(line)
                    res = r.get("toolUseResult")
                    if (r.get("type") == "user" and not r.get("isSidechain") and isinstance(res, dict)
                            and res.get("status") == "async_launched"
                            and isinstance(res.get("agentId"), str) and res["agentId"]):
                        t = msg_time(line)
                        if started is None or t is None or t * 1000 >= started:
                            agents.append(res["agentId"])
                except (ValueError, AttributeError):
                    pass
            if '"type":"assistant"' in line or '"type":"user"' in line:
                turns.append(line)
                if len(turns) > 2 * TURN_LINES:
                    del turns[:-TURN_LINES]
            if "<task-notification>" in line:
                for tid, event in notified(line):
                    if event is None:
                        ended.add(tid)
                    else:
                        events[tid] = event[:EVENT_LIMIT]
            if '"subtype":"turn_duration"' in line:
                tail = []
            elif tail is not None:
                tail.append(line)
    pending = [w for tid, w in launched.items() if tid not in ended]
    for sh in shells:
        if sh["kind"] == "monitor":
            sh["event"] = events.get(sh["id"])
    last = next((l for l in reversed(turns[-TURN_LINES:]) if starts_turn(l)), None)
    return {"bridge": bridge, "pending": pending, "tail": tail,
            "shells": [sh for sh in shells if sh["id"] not in ended],
            "agents": [a for a in agents if a not in ended],
            "answered": last is not None and ends_turn(last)}

def task_outputs(pid):
    """Ids des tâches dont la sortie (…/tasks/<id>.output) est ouverte par un descendant du
    processus claude pid : ses commandes Bash qui tournent, au premier plan comme en
    arrière-plan. None si /proc ne permet pas de le savoir (macOS…)."""
    try:
        root = int(pid)
    except (TypeError, ValueError):
        return None
    out, todo, seen = set(), [root], set()
    while todo and len(seen) < 1000:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            for t in os.listdir("/proc/%d/task" % p):
                with open("/proc/%d/task/%s/children" % (p, t)) as f:
                    todo.extend(int(c) for c in f.read().split())
        except (OSError, ValueError):
            if p == root:  # pas de /proc, ou noyau sans la liste des fils
                return None
        for fd in (("1", "2") if p != root else ()):
            try:
                d, name = os.path.split(os.readlink("/proc/%d/fd/%s" % (p, fd)))
            except OSError:
                continue
            if os.path.basename(d) == "tasks" and name.endswith(".output"):
                out.add(name[:-len(".output")])
    return out

def shell_inputs(p, shells):
    """Description et commande des appels Bash ou Monitor qui ont lancé les shells et les
    monitors (seconde passe, faite seulement quand l'un d'eux tourne)."""
    want = {}
    for sh in shells:
        if sh.get("tool"):
            want[sh["tool"]] = sh
    if not want:
        return
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"tool_use"' not in line or not any(t in line for t in want):
                continue
            try:
                content = json.loads(line).get("message", {}).get("content")
            except (ValueError, AttributeError):
                continue
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") in want:
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    sh = want[b["id"]]
                    sh["description"] = inp.get("description") if isinstance(inp.get("description"), str) else ""
                    sh["command"] = inp.get("command") if isinstance(inp.get("command"), str) else sh["command"]

def running_shells(p, shells, pid, status):
    """Shells et monitors en arrière-plan qui tournent encore. Shell : sa sortie est encore
    ouverte (un shell arrêté depuis le terminal ne laisse aucune trace dans la transcription),
    ou tous si /proc ne le dit pas. Monitor : claude lit sa sortie et l'écrit lui-même, mais
    sa fin est notifiée ; et la session (status) ne repasse à « idle » qu'une fois ses shells
    et monitors finis (« shell » d'ici là). {"id", "kind", "description", "command", "start"},
    plus "event" pour un monitor."""
    alive = task_outputs(pid) if any(sh["kind"] == "shell" for sh in shells) else None
    shells = [sh for sh in shells if (status != "idle" if sh["kind"] == "monitor"
                                      else alive is None or sh["id"] in alive)]
    shell_inputs(p, shells)
    out = []
    for sh in shells:
        o = {"id": sh["id"], "kind": sh["kind"], "description": sh.get("description") or "",
             "command": sh.get("command") or "", "start": sh["start"]}
        if sh["kind"] == "monitor":
            o["event"] = sh.get("event")
        out.append(o)
    return out

def starts_turn(line):
    """Vrai si la ligne montre Claude au travail : sa réponse, un résultat d'outil, ou un
    message qui lance un tour (pas une commande locale comme /workflows)."""
    try:
        r = json.loads(line)
    except ValueError:
        return False
    if not isinstance(r, dict) or r.get("isSidechain"):
        return False
    if r.get("type") == "assistant":
        return True
    if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary"):
        return False
    content = (r.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                         for b in content):
        return True
    t = text_of(content).lstrip()
    return bool(t) and not t.startswith(("<command-", "<local-command-"))

def ends_turn(line):
    """Vrai si la ligne est un message de Claude qui clôt son tour (pas un appel d'outil)."""
    try:
        r = json.loads(line)
    except ValueError:
        return False
    if not isinstance(r, dict) or r.get("type") != "assistant" or r.get("isSidechain"):
        return False
    m = r.get("message")
    stop = m.get("stop_reason") if isinstance(m, dict) else None
    return isinstance(stop, str) and stop not in ("", "tool_use")

def workflow_progress(w, p):
    """Avancement d'un workflow d'après son journal (une ligne « started » puis « result »
    par agent) : nom, agents lancés et finis, et ceux qui tournent (libellé, phase)."""
    d = w.get("transcriptDir")
    if not isinstance(d, str) or not d:
        d = os.path.join(str(p)[:-len(".jsonl")], "subagents", "workflows", str(w.get("runId")))
    started, done = {}, set()
    try:
        with open(os.path.join(d, "journal.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("type") == "started":
                    started[str(r.get("agentId") or r.get("key"))] = r
                elif r.get("type") == "result":
                    done.add(str(r.get("agentId") or r.get("key")))
    except OSError:
        pass
    running = [{"label": r.get("label") if isinstance(r.get("label"), str) else "",
                "phase": r.get("phase") if isinstance(r.get("phase"), str) else ""}
               for k, r in started.items() if k not in done]
    name = w.get("workflowName")
    return {"name": name if isinstance(name, str) else "", "total": len(started),
            "done": len(started) - len(running), "running": running}

def headless(sess):
    """Vrai pour un claude lancé sans terminal (claude -p, celui de Claude Hub)."""
    return bool(sess) and sess.get("entrypoint") == "sdk-cli"

def cmd_stat(sid):
    p = find(sid)
    st = p.stat() if p else None
    sess = live_sessions().get(sid)
    scan = (scan_main(p, ms_of((sess or {}).get("startedAt"))) if p else
            {"bridge": None, "pending": [], "tail": None, "shells": [], "agents": [], "answered": False})
    workflows = [workflow_progress(w, p) for w in scan["pending"]] if sess else []
    bridge, tail = scan["bridge"], scan["tail"]
    if sess:
        # claude en cours : sa session Remote Control est notée dans son fichier de session
        # (la transcription garde celles des lancements précédents)
        bridge = text_or_none(sess.get("bridgeSessionId"))
    shells = running_shells(p, scan["shells"], sess.get("pid"), sess.get("status")) if sess else []
    print(json.dumps({"size": st.st_size if st else 0,
                      "live": (sess.get("status") or "idle") if sess else None, "bridge": bridge,
                      # ce qu'attend claude : « input needed » (question, plan), « permission prompt »
                      "waiting_for": text_or_none(sess.get("waitingFor")) if sess else None,
                      "since": ms_of(sess.get("statusUpdatedAt")) if sess else None,
                      "workflows": workflows,
                      # shells et monitors en arrière-plan (« 1 shell », « 1 monitor » du terminal)
                      "shells": shells,
                      # Claude a fini son tour et attend la fin des workflows
                      "waiting": bool(workflows) and tail is not None
                      and not any(starts_turn(l) for l in tail),
                      # claude sans terminal (claude -p) : son fichier de session reste à
                      # « busy » une fois la réponse finie
                      "headless": headless(sess),
                      # … dont la réponse est finie et qui n'attend plus rien en arrière-plan :
                      # l'arrêter ne perd rien
                      "finished": headless(sess) and scan["answered"] and not workflows
                      and not shells and not scan["agents"]}))

def cmd_stop(sid):
    """Arrête le claude sans terminal (claude -p) de la session sid, que Claude Hub avait
    lancé avant de redémarrer ; jamais celui d'un terminal. Attend sa fin (5 s au plus)."""
    sess = live_sessions().get(sid)
    stopped = False
    try:
        pid = int(sess["pid"]) if headless(sess) else 0
    except (KeyError, TypeError, ValueError):
        pid = 0
    if pid > 1:  # 0 ou -1 viseraient tout un groupe de processus
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                time.sleep(0.1)
                os.kill(pid, 0)
        except OSError:
            stopped = True
    print(json.dumps({"stopped": stopped}))

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

PLAN_REMARK = 2000  # caractères gardés de la remarque jointe au refus d'un plan

def plan_decision(content, plans):
    """Décision prise sur un plan (ExitPlanMode), d'après le résultat de l'outil : validé
    (ok), refusé avec la remarque transmise à Claude (text), ou demande interrompue.
    Notée sur l'élément du plan (plans : id de l'appel -> élément). Le mode d'exécution
    choisi en validant n'est pas dans la transcription."""
    for b in content if isinstance(content, list) else []:
        if not (isinstance(b, dict) and b.get("type") == "tool_result"
                and isinstance(b.get("tool_use_id"), str) and b["tool_use_id"] in plans):
            continue
        ok = not b.get("is_error")
        text = "" if ok else result_text(b.get("content"))
        if text.startswith("[Request interrupted by user"):
            d = {"ok": False, "text": "", "interrupted": True}
        else:
            d = {"ok": ok, "text": text[:PLAN_REMARK]}
        plans[b["tool_use_id"]]["decision"] = d

def usage_tokens(u):
    """Taille du contexte après un message de Claude : jetons d'entrée (cache compris) et de sortie."""
    if not isinstance(u, dict):
        return 0
    return sum(u[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                              "output_tokens") if type(u.get(k)) is int)

def is_prompt(r):
    """Message tapé par l'utilisateur : ni résultat d'outil, ni texte ajouté par claude,
    ni mention d'une interruption."""
    if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary"):
        return False
    content = (r.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                         for b in content):
        return False
    return not text_of(content).startswith("[Request interrupted")

def abandoned(p):
    """uuid des enregistrements abandonnés de la transcription p.

    Un message modifié (claude-hub lance `claude -p --resume <id> --resume-session-at
    <uuid>`) ou un retour en arrière du terminal ajoute un message de l'utilisateur
    rattaché (parentUuid) à un enregistrement qui a déjà une suite : claude ne garde que
    la branche la plus récente, ce qui descendait déjà de ce point est abandonné. Les
    appels d'outils en parallèle forment aussi des branches (l'appel suivant et le
    résultat du premier ont le même parent) : seul un message tapé marque un retour.
    """
    kids, dropped = {}, set()
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"uuid"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            uid = r.get("uuid") if isinstance(r, dict) and not r.get("isSidechain") else None
            # après /compact, la conversation repart du dernier message d'avant
            up = (r.get("parentUuid") or r.get("logicalParentUuid")) if uid else None
            if not up:
                continue
            if up in kids and is_prompt(r):
                todo = list(kids[up])
                while todo:
                    u = todo.pop()
                    if u not in dropped:
                        dropped.add(u)
                        todo.extend(kids.get(u, ()))
            kids.setdefault(up, []).append(uid)
    return dropped

def transcript_items(p, sidechain=False, todos=None, usage=None):
    """Messages et appels d'outils d'une transcription ; renvoie (items, dernier modèle, son effort).

    Pour la conversation principale, les branches abandonnées (message modifié) sont
    ignorées : seule compte celle que claude reprendra.
    todos : liste complétée par les appels de TodoWrite, TaskCreate et TaskUpdate de
    toute la transcription, et par les numéros attribués aux tâches créées.
    usage : dict dont "context" reçoit la taille du contexte au dernier message de Claude
    ({"tokens", "model"}), None après une compaction.
    """
    dropped = set() if sidechain else abandoned(p)
    items, model, effort, creating = [], None, None, set()
    plans = {}  # id de l'appel ExitPlanMode -> élément du plan, qui recevra la décision
    caveat = False  # message précédent : avertissement d'une commande locale (/rename…)
    mark, when = 0, None  # éléments de l'enregistrement en cours (à dater), sa date
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if ('"type":"user"' not in line and '"type":"assistant"' not in line
                    and '"queued_command"' not in line):
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            stamp(items, mark, when)
            mark, when = len(items), record_time(r)
            if (r.get("isSidechain") and not sidechain) or (dropped and r.get("uuid") in dropped):
                continue
            if r.get("type") == "attachment":
                # Message envoyé pendant une réponse : claude l'a lu entre deux étapes.
                a = r.get("attachment") or {}
                if a.get("type") == "queued_command" and a.get("commandMode", "prompt") == "prompt":
                    txt, imgs = bash_items(text_of(a.get("prompt")), items), images_of(a.get("prompt"))
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
                if plans:
                    plan_decision(content, plans)
                ev = created_task(r, creating) if creating and todos is not None else None
                if ev:
                    todos.append(ev)
                txt = text_of(content)
                if not local:  # commande tapée (skill, /plan …) : affichée comme dans le terminal
                    txt = command_text(txt) or txt
                txt = bash_items(txt, items)  # commandes « ! », avant le texte du message
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
                    if b.get("name") == "ExitPlanMode" and isinstance(b.get("id"), str):
                        plans[b["id"]] = items[-1]
                    if todos is not None and b.get("name") in TODO_TOOLS:
                        todos.append(items[-1])
                        if b.get("name") == "TaskCreate":
                            creating.add(b.get("id"))
    stamp(items, mark, when)
    return items, model, effort

def cmd_history(sid, limit):
    p = find(sid)
    todos, usage = [], {"context": None}
    items, model, effort = transcript_items(p, todos=todos, usage=usage) if p else ([], None, None)
    print(json.dumps({"size": p.stat().st_size if p else 0, "model": model, "effort": effort,
                      "total": len(items), "items": apply_budget(items[-limit:]), "todos": todos,
                      "agents": agents_list(p) if p else [], "workflow_runs": workflow_runs(p),
                      "context": usage["context"]}))

def cmd_agent(sid, key, limit):
    """Transcription du sous-agent lancé par l'appel d'outil key (subagents/agent-*.jsonl), ou
    dont l'id est key : les agents d'un workflow n'ont pas d'appel d'outil (pas de toolUseId)."""
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
            if (not isinstance(meta, dict) or not os.path.isfile(path)
                    or meta.get("toolUseId") != key and n != "agent-" + key + ".meta.json"):
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
    la main), ou le résultat d'un outil qui termine l'agent (« toolEndsTurn » :
    StructuredOutput des agents de workflow) ; interrompu : c'est la marque
    d'interruption de claude ; sinon il attend un outil ou la suite du modèle.
    """
    name = os.path.basename(path)[:-len(".jsonl")]
    d = os.path.dirname(path)  # agent d'un workflow : subagents/workflows/<run>/
    run = os.path.basename(d) if os.path.basename(os.path.dirname(d)) == "workflows" else None
    phase = meta.get("workflowPhase")
    a = {"id": name[len("agent-"):] if name.startswith("agent-") else name,
         "type": meta.get("agentType"), "description": meta.get("description"),
         "tool": meta.get("toolUseId"), "parent": meta.get("parentAgentId"),
         "depth": meta.get("spawnDepth") or 1, "background": meta.get("requestShape") == "background",
         "workflow": run, "phase": phase if isinstance(phase, str) else None,
         "size": os.path.getsize(path), "start": None, "end": None, "steps": 0,
         "done": False, "interrupted": False, "last": None}
    ended = False  # un outil a terminé l'agent (le résultat d'un appel parallèle peut suivre)
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
                ended = ended or r.get("toolEndsTurn") is True
                a["done"] = ended
                a["interrupted"] = text_of(content).startswith("[Request interrupted by user")
                continue
            ended = a["interrupted"] = False
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

def workflow_runs(p):
    """Workflows lancés par la conversation p, dans l'ordre : run (dossier de ses agents sous
    subagents/workflows/), nom, résumé, appel d'outil qui l'a lancé et date. Lus dans le
    résultat de l'outil Workflow ; la transcription n'est relue que si un workflow a laissé
    des agents."""
    if not p or not os.path.isdir(str(p)[:-len(".jsonl")] + "/subagents/workflows"):
        return []
    runs = {}
    with open(str(p), encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"local_workflow"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            res = r.get("toolUseResult") if isinstance(r, dict) else None
            if (not isinstance(res, dict) or r.get("type") != "user" or r.get("isSidechain")
                    or res.get("taskType") != "local_workflow" or not text_or_none(res.get("runId"))
                    or res["runId"] in runs):
                continue
            content = r["message"].get("content") if isinstance(r.get("message"), dict) else None
            tools = [b.get("tool_use_id") for b in content if isinstance(b, dict)
                     and b.get("type") == "tool_result"] if isinstance(content, list) else []
            runs[res["runId"]] = {"id": res["runId"], "name": text_or_none(res.get("workflowName")),
                                  "summary": text_or_none(res.get("summary")),
                                  "tool": text_or_none(tools[0]) if tools else None,
                                  "start": record_time(r)}
    return list(runs.values())

def cmd_agents(sid):
    p = find(sid)
    print(json.dumps({"live": live_ids().get(sid), "agents": agents_list(p) if p else [],
                      "workflow_runs": workflow_runs(p)}))

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
{"list": lambda: cmd_list(json.loads(a[1]) if len(a) > 1 else {}),"stat": lambda: cmd_stat(a[1]),
 "exists": lambda: print(json.dumps({d: os.path.isdir(d) for d in json.loads(a[1])})),
 "search": lambda: cmd_search(a[1], int(a[2])),
 "live": lambda: cmd_live(),
 "stop": lambda: cmd_stop(a[1]),
 "dirs": lambda: cmd_dirs(a[1]),
 "defaults": lambda: cmd_defaults(),
 "read": lambda: cmd_read(a[1]),
 "note": lambda: cmd_note(a[1]),
 "notes": lambda: print(json.dumps(notes_index())),
 "history": lambda: cmd_history(a[1], int(a[2])),
 "agent": lambda: cmd_agent(a[1], a[2], int(a[3])),
 "agents": lambda: cmd_agents(a[1]),
 "result": lambda: cmd_result(a[1], a[2]),
 "edits": lambda: cmd_edits(a[1])}[a[0]]()
