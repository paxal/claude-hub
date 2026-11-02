"""Conversations Cursor (IDE local et agents cloud)."""
import ast
import difflib
import json
import os
import re
import subprocess
import unicodedata
from contextlib import closing
from pathlib import Path

from .hosts import CLOUD, run_helper
from .render import esc, tool_detail


# Cursor (IDE) garde tout en local dans state.vscdb, même quand l'espace de
# travail est distant (Remote-SSH). On lit en lecture seule ; on ne peut pas les
# reprendre telles quelles, mais on peut les continuer dans une session Claude.

CURSOR_DB = (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
             / "Cursor/User/globalStorage/state.vscdb")
CONTEXT_CHARS = 60000
_ssh_hostnames = {}


def cursor_db():
    """Connexion en lecture seule, fermée à la sortie du with (sqlite3 seul ne la ferme pas)."""
    import sqlite3
    return closing(sqlite3.connect(f"file:{CURSOR_DB}?mode=ro", uri=True, timeout=10))


def cursor_host(authority):
    """« ssh-remote+cerberus » ou « ssh-remote+<hex de {"hostName": …}> » -> hôte."""
    if not authority:
        return "local"
    target = authority.split("+", 1)[-1]
    try:
        return json.loads(bytes.fromhex(target))["hostName"]
    except (ValueError, KeyError, TypeError):
        return target


def ssh_hostname(host):
    """Nom réel d'un alias SSH (pour rapprocher les hôtes Cursor des nôtres)."""
    if host not in _ssh_hostnames:
        name = host
        try:
            out = subprocess.run(["ssh", "-G", host], capture_output=True, text=True,
                                 timeout=5).stdout
            name = next((l.split()[1] for l in out.splitlines() if l.startswith("hostname ")), host)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _ssh_hostnames[host] = name
    return _ssh_hostnames[host]


def cursor_list(host, all_hosts):
    """Conversations Cursor dont l'espace de travail est sur `host`. Celles des
    hôtes non configurés apparaissent sous « local », préfixées par leur hôte."""
    if not CURSOR_DB.exists():
        return []
    real = [h for h in all_hosts if h not in ("local", CLOUD)]
    known = {ssh_hostname(h): h for h in real}
    known.update({h: h for h in real})
    with cursor_db() as db:
        rows = db.execute("""
            select json_extract(value, '$.composerId'), json_extract(value, '$.name'),
                   json_extract(value, '$.lastUpdatedAt'), json_extract(value, '$.createdAt'),
                   json_extract(value, '$.workspaceIdentifier.uri.authority'),
                   json_extract(value, '$.workspaceIdentifier.uri.path'),
                   json_extract(value, '$.fullConversationHeadersOnly[0].grouping.textPreview'),
                   json_extract(value, '$.status')
            from cursorDiskKV
            where key like 'composerData:%'
              and json_extract(value, '$.subagentInfo') is null
              and json_array_length(value, '$.fullConversationHeadersOnly') > 0""").fetchall()
    archived = cursor_archived_ids()
    res = []
    for cid, name, updated, created, authority, path, preview, status in rows:
        chost = cursor_host(authority)
        mine = known.get(chost, "local" if chost != "local" and chost not in known else chost)
        if mine != host:
            continue
        cwd = path if chost == "local" or chost in known else f"{chost}:{path}"
        res.append({"id": cid, "source": "cursor", "title": name, "first": preview,
                    "cwd": cwd, "cursor_host": chost, "cursor_archived": cid in archived,
                    "mtime": (updated or created or 0) / 1000,
                    "live": "busy" if status == "generating" else None})
    return res


def check_dirs(host, sessions):
    """Renseigne cwd_exists pour des conversations Cursor (dossiers sur `host`)."""
    paths = sorted({s["cwd"] for s in sessions if s.get("cwd") and ":" not in s["cwd"]})
    if not paths:
        return
    if host == "local":
        found = {d: os.path.isdir(d) for d in paths}
    else:
        found = run_helper(host, "exists", json.dumps(paths))
    for s in sessions:
        if s.get("cwd") in found:
            s["cwd_exists"] = found[s["cwd"]]


# Outils Cursor -> (nom de l'outil Claude équivalent, {champ Claude : champs Cursor}) :
# chemins cliquables, détail, tâches et sous-agents s'affichent comme pour Claude.
_READ = ("Read", {"file_path": ("targetFile", "path", "target_file"), "offset": ("offset",),
                  "limit": ("limit",)})
_EDIT = ("Edit", {"file_path": ("relativeWorkspacePath", "file_path", "target_file")})
_BASH = ("Bash", {"command": ("command",), "description": ("commandDescription",)})
_GREP = ("Grep", {"pattern": ("pattern", "query"), "path": ("path",), "glob": ("glob",)})
_LS = ("LS", {"path": ("targetDirectory", "path")})
TOOLS = {
    "read_file_v2": _READ, "read_file": _READ,
    "edit_file_v2": _EDIT, "edit_file": _EDIT, "search_replace": _EDIT, "apply_patch": _EDIT,
    "run_terminal_command_v2": _BASH, "run_terminal_cmd": _BASH,
    "ripgrep_raw_search": _GREP, "grep": _GREP, "rg": _GREP,
    "glob_file_search": ("Glob", {"pattern": ("globPattern",), "path": ("targetDirectory",)}),
    "list_dir_v2": _LS, "list_dir": _LS,
    "web_search": ("WebSearch", {"query": ("searchTerm", "query")}),
    "web_fetch": ("WebFetch", {"url": ("url",)}),
    "task_v2": ("Agent", {"description": ("description",), "prompt": ("prompt",)}),
    "create_plan": ("ExitPlanMode", {"plan": ("plan",)}),
}
TODO_STATUS = {"cancelled": "cancelled", "completed": "completed", "in_progress": "in_progress"}


def loads(text):
    """Champ JSON de Cursor ; additionalData est parfois un repr Python ({'status': …})."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None


def cursor_tool_input(tool):
    """Arguments d'un appel : params complété par rawArgs (MCP : rawArgs["args"])."""
    data = {}
    for field in ("rawArgs", "params"):
        d = loads(tool.get(field))
        if isinstance(d, dict) and isinstance(d.get("args"), dict):  # MCP : rien d'autre d'utile
            data = d["args"]
            break
        if isinstance(d, dict):
            data.update({k: v for k, v in d.items() if k not in data})
    data.pop("toolCallId", None)
    return data


def cursor_tool_item(cid, bid, tool):
    """Élément d'historique d'un appel d'outil Cursor, aux noms de Claude si possible.

    L'id (« composer:bulle ») sert à relire sa sortie (cursor_result) ; agent_id est
    la conversation du sous-agent lancé par task_v2."""
    raw = cursor_tool_input(tool)
    name, fields = TOOLS.get(tool["name"], (tool["name"], None))
    inp = raw if fields is None else {
        key: next((raw[f] for f in names if raw.get(f) not in (None, "")), None)
        for key, names in fields.items()}
    inp = {k: v for k, v in inp.items() if v is not None}
    item = {"role": "tool", "name": name, "input": inp, "id": f"{cid}:{bid}"}
    result = loads(tool.get("result"))
    result = result if isinstance(result, dict) else {}
    if tool["name"] == "todo_write":
        item["name"] = "TodoWrite"
        todos = result.get("finalTodos") or raw.get("todos") or []
        inp["todos"] = [{"content": t.get("content") or "",
                         "status": TODO_STATUS.get(t.get("status"), "pending")}
                        for t in todos if isinstance(t, dict)]
    elif name == "Agent":
        extra = loads(tool.get("additionalData"))
        extra = extra if isinstance(extra, dict) else {}
        kind = raw.get("subagentType")
        inp["subagent_type"] = raw.get("name") or (kind if kind not in (None, "unspecified") else "Agent")
        item["agent_id"] = result.get("agentId") or extra.get("subagentComposerId")
        if tool.get("status") in ("completed", "error", "cancelled"):
            reason = extra.get("terminationReason") or tool["status"]
            item["agent"] = {"end": {"status": reason}}
    return item


def cursor_history(cid, limit):
    with cursor_db() as db:
        row = db.execute("select value from cursorDiskKV where key = ?",
                         (f"composerData:{cid}",)).fetchone()
        headers = json.loads(row[0]).get("fullConversationHeadersOnly", []) if row else []
        bubbles = {}
        for key, value in db.execute("select key, value from cursorDiskKV where key >= ? and key < ?",
                                     (f"bubbleId:{cid}:", f"bubbleId:{cid};")):
            bubbles[key.rsplit(":", 1)[1]] = value
        updated = json.loads(row[0]).get("lastUpdatedAt") if row else 0
    items = []
    for h in headers:
        try:
            b = json.loads(bubbles.get(h["bubbleId"]) or "{}")
        except ValueError:
            continue
        text = (b.get("text") or "").strip()
        if h.get("type") == 1:
            text = text or (h.get("grouping") or {}).get("textPreview") or ""
            if text:
                items.append({"role": "user", "text": text})
            continue
        tool = b.get("toolFormerData")
        if tool and tool.get("name"):
            items.append(cursor_tool_item(cid, h["bubbleId"], tool))
        if text:
            items.append({"role": "assistant", "text": text})
    return {"found": row is not None, "size": updated, "total": len(items),
            "items": items[-limit:]}


def cursor_agent(agent_id, limit):
    """Transcription d'un sous-agent (conversation Cursor à part, avec subagentInfo)."""
    if not agent_id:
        return {"found": False}
    return cursor_history(agent_id, limit)


RESULT_LIMIT = 100000  # comme les scripts distants


def _result_text(tool, db):
    """Sortie lisible d'un outil Cursor, d'après son champ result (JSON, varie selon l'outil)."""
    name, res = tool.get("name"), loads(tool.get("result"))
    if res is None:
        extra = loads(tool.get("additionalData"))
        files = extra.get("topFiles") if isinstance(extra, dict) else None
        if isinstance(files, list):  # recherche : Cursor n'en garde qu'un résumé
            return "\n".join([f"{extra.get('totalMatches')} résultats dans "
                              f"{extra.get('totalFiles')} fichiers (Cursor ne garde que ce résumé)"]
                             + [f"{f.get('uri')} ({f.get('matchCount')})" for f in files
                                if isinstance(f, dict)])
        return "(sortie non gardée par Cursor)"
    if not isinstance(res, dict):
        return str(res)
    if name in ("edit_file_v2", "edit_file") and res.get("afterContentId"):
        texts = []
        for key in ("beforeContentId", "afterContentId"):
            row = db.execute("select value from cursorDiskKV where key = ?",
                             (res.get(key) or "",)).fetchone()
            texts.append(row[0] if row and isinstance(row[0], str) else "")
        diff = difflib.unified_diff(texts[0].splitlines(), texts[1].splitlines(),
                                    "avant", "après", lineterm="")
        return "\n".join(diff) or "(aucun changement)"
    diff = res.get("diff") if isinstance(res.get("diff"), dict) else {}
    if diff.get("chunks"):  # search_replace, apply_patch
        return "\n".join(f"@@ ligne {c.get('newStart')} @@\n{c.get('diffString') or ''}"
                         for c in diff["chunks"] if isinstance(c, dict))
    for key in ("output", "contents", "markdown", "resultForModel"):
        if isinstance(res.get(key), str):
            text = res[key]
            if res.get("exitCode") not in (None, 0):
                text += f"\n[code de sortie {res['exitCode']}]"
            return text
    if isinstance(res.get("result"), str):  # MCP : {"content": [blocs texte]}
        inner = loads(res["result"])
        blocks = inner.get("content") if isinstance(inner, dict) else None
        if isinstance(blocks, list):
            return "\n".join(str(b.get("text") or "") for b in blocks
                             if isinstance(b, dict) and b.get("type") == "text")
        return res["result"]
    if isinstance(res.get("directories"), list):  # glob_file_search
        return "\n".join(f.get("relPath") or "" for d in res["directories"] if isinstance(d, dict)
                         for f in d.get("files") or [] if isinstance(f, dict)) or "(aucun fichier)"
    if isinstance(res.get("references"), list):  # web_search
        return "\n\n".join(str(r.get("chunk") or r.get("title") or "")
                           for r in res["references"] if isinstance(r, dict))
    if isinstance(res.get("finalTodos"), list):
        return "\n".join(f"[{t.get('status')}] {t.get('content')}"
                         for t in res["finalTodos"] if isinstance(t, dict))
    if res.get("totalLinesInFile") is not None and len(res) == 1:
        return f"(contenu non gardé par Cursor · {res['totalLinesInFile']} lignes dans le fichier)"
    return json.dumps(res, ensure_ascii=False, indent=1) if res else ""


def cursor_result(tool_id):
    """Sortie de l'appel d'outil « composer:bulle », au format de la commande result des
    scripts distants : {"found", "text", "error", "length"}."""
    cid, _, bid = (tool_id or "").partition(":")
    with cursor_db() as db:
        row = db.execute("select json_extract(value, '$.toolFormerData') from cursorDiskKV"
                         " where key = ?", (f"bubbleId:{cid}:{bid}",)).fetchone()
        tool = loads(row[0]) if row else None
        if not isinstance(tool, dict) or tool.get("status") == "loading":
            return {"found": False}
        err = loads(tool.get("error"))
        if isinstance(err, dict):
            text, error = (err.get("clientVisibleErrorMessage") or err.get("modelVisibleErrorMessage")
                           or json.dumps(err, ensure_ascii=False)), True
        elif tool.get("status") == "cancelled":
            text, error = "Annulé.", True
        else:
            text = _result_text(tool, db)
            error = tool.get("status") == "error"
    return {"found": True, "error": error, "length": len(text), "text": text[:RESULT_LIMIT]}


def cursor_stat(cid):
    with cursor_db() as db:
        row = db.execute("select json_extract(value, '$.lastUpdatedAt'), json_extract(value, '$.status')"
                         " from cursorDiskKV where key = ?", (f"composerData:{cid}",)).fetchone()
    return {"size": row[0] if row else 0,
            "live": "busy" if row and row[1] == "generating" else None}


CURSOR_SEARCH_DB = CURSOR_DB.with_name("conversation-search.db")


def cursor_archived_ids():
    """Conversations archivées dans Cursor (lecture seule : on n'écrit pas chez Cursor)."""
    if not CURSOR_SEARCH_DB.exists():
        return set()
    import sqlite3
    try:
        with sqlite3.connect(f"file:{CURSOR_SEARCH_DB}?mode=ro", uri=True, timeout=5) as db:
            return {r[0] for r in db.execute("select id from conversations where is_archived = 1")}
    except sqlite3.Error:
        return set()


def norm_text(t):
    """Minuscules sans accents (pour comparer comme les helpers)."""
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def cursor_search(query, limit=200):
    """Recherche dans l'index FTS5 que Cursor maintient sur ses conversations."""
    if not CURSOR_SEARCH_DB.exists():
        return {}
    words = [w.replace('"', "") for w in query.split() if w.replace('"', "")]
    if not words:
        return {}
    import sqlite3
    match = " ".join(f'"{w}"*' for w in words)
    with sqlite3.connect(f"file:{CURSOR_SEARCH_DB}?mode=ro", uri=True, timeout=5) as db:
        rows = db.execute("""
            select c.id, snippet(conversation_fts, 1, '', '', '…', 16)
            from conversation_fts join conversations c on c.fts_rowid = conversation_fts.rowid
            where conversation_fts match ? order by c.updated_at desc limit ?""",
                          (match, limit)).fetchall()
    return {cid: {"role": "cursor", "snippet": " ".join(snip.split())} for cid, snip in rows}


def highlight_words(text, query):
    """Échappe text et met en gras les mots de la requête (sans tenir compte des accents)."""
    words = [norm_text(w) for w in query.split() if w]
    # Texte normalisé caractère par caractère, avec la position d'origine de chacun.
    low, origin = [], []
    for i, ch in enumerate(text):
        for c in norm_text(ch):
            low.append(c)
            origin.append(i)
    low, spans = "".join(low), []
    for w in words:
        spans += [(origin[m.start()], origin[m.end() - 1] + 1)
                  for m in re.finditer(re.escape(w), low)]
    out, pos = [], 0
    for a, b in sorted(spans):
        if a < pos:
            continue
        out += [esc(text[pos:a]), "<b>", esc(text[a:b]), "</b>"]
        pos = b
    return "".join(out) + esc(text[pos:])


def cursor_live():
    if not CURSOR_DB.exists():
        return {}
    with cursor_db() as db:
        rows = db.execute("select json_extract(value, '$.composerId') from cursorDiskKV"
                          " where key like 'composerData:%'"
                          " and json_extract(value, '$.status') = 'generating'").fetchall()
    return {r[0]: "busy" for r in rows}


STATES = {  # état -> (texte, classe CSS)
    "busy": ("⏳ travaille", "busy"),
    "idle": ("✓ terminé", "live"),
    "requires_action": ("⚠ action requise", "waiting"),
    "unread": ("● nouvelle réponse", "unread"),
    "draft": ("✎ brouillon", "draft"),
}


def state_text(status):
    return STATES.get(status, (f"● {status}", "waiting")) if status else ("", None)


def cursor_context(title, items):
    """Transcription d'une conversation Cursor, à donner à Claude pour la continuer."""
    lines = []
    for it in items:
        if it["role"] == "tool":
            lines.append(f"[outil {it['name']}: {tool_detail(it.get('input'))}]")
        else:
            who = "Utilisateur" if it["role"] == "user" else "Assistant (Cursor)"
            lines.append(f"### {who}\n{it['text']}\n")
    body = "\n".join(lines)
    if len(body) > CONTEXT_CHARS:
        body = "[… début tronqué …]\n" + body[-CONTEXT_CHARS:]
    return (f"Voici la transcription d'une conversation menée dans Cursor (« {title} ») "
            f"dans ce même dossier. Reprends-la là où elle s'est arrêtée.\n\n"
            f"<cursor-transcript>\n{body}\n</cursor-transcript>\n\n")
