"""Tags des conversations, rangés par identifiant dans ~/.config/claude-hub/tags.json.

Un tag peut contenir des « / » (stockly/bazel) : il se range alors comme dans un
dossier, et le filtre #stockly/ retrouve tout ce qui est dessous.

Avec les tags, on garde un résumé de la conversation et son hôte : la vue par tag
affiche ainsi les conversations de tous les hôtes sans avoir à les interroger.
"""
import json
import re
from collections import Counter

from .hosts import CONFIG

TAGS_FILE = CONFIG / "tags.json"
VOLATILE = ("live", "search", "new", "host")  # champs de session à ne pas garder
RE_TOKEN = re.compile(r'dir:"[^"]*"|\S+')


def normalize(tag):
    """« #Stockly / Bazel  CI » -> « stockly/bazel-ci »."""
    parts = ("-".join(p.split()) for p in tag.strip().lstrip("#").lower().split("/"))
    return "/".join(p for p in parts if p)


def snapshot(s):
    out = {}
    for k, v in s.items():
        if k in VOLATILE:
            continue
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        out[k] = v
    return out


class TagStore:
    def __init__(self):
        try:
            self.data = json.loads(TAGS_FILE.read_text())
        except (FileNotFoundError, ValueError):
            self.data = {}

    def save(self):
        CONFIG.mkdir(parents=True, exist_ok=True)
        tmp = TAGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        tmp.replace(TAGS_FILE)

    def tags(self, sid):
        return self.data.get(sid, {}).get("tags", [])

    def set(self, s, host, tags):
        tags = sorted({normalize(t) for t in tags} - {""})
        if tags:
            self.data[s["id"]] = {"host": host, "tags": tags, "session": snapshot(s)}
        else:
            self.data.pop(s["id"], None)
        self.save()
        return tags

    def refresh(self, host, sessions):
        """Met à jour les résumés gardés (titre, date…) d'après une liste fraîche."""
        changed = False
        for s in sessions:
            entry = self.data.get(s["id"])
            if entry and entry["host"] == host:
                snap = snapshot(s)
                if snap != entry["session"]:
                    entry["session"], changed = snap, True
        if changed:
            self.save()

    def tagged(self):
        """[(hôte, session)] de toutes les conversations taguées."""
        return [(e["host"], dict(e["session"], id=sid)) for sid, e in self.data.items()]

    def counts(self):
        """Nombre de conversations par tag, et par « dossier » (préfixe terminé par /)."""
        c = Counter()
        for e in self.data.values():
            names = set(e["tags"])
            for t in e["tags"]:
                parts = t.split("/")
                names.update("/".join(parts[:i]) + "/" for i in range(1, len(parts)))
            c.update(names)
        return c


def tag_matches(tags, f):
    if f.endswith("/"):
        return any(t.startswith(f) for t in tags)
    return any(t == f or t.startswith(f + "/") for t in tags)


def dir_matches(cwd, d, home):
    if not cwd:
        return False
    if d.startswith("~") and home:
        d = home + d[1:]
    d = d.rstrip("/") or "/"
    return cwd == d or cwd.startswith(d.rstrip("/") + "/")


def tokens(text):
    return RE_TOKEN.findall(text)


def parse_query(text):
    """« #stockly dir:~/devel bazel » -> (["stockly"], ["~/devel"], "bazel")."""
    tags, dirs, words = [], [], []
    for tok in tokens(text):
        if tok.startswith("#") and normalize(tok):
            tags.append(normalize(tok) + ("/" if tok.endswith("/") else ""))
        elif tok.startswith("dir:") and len(tok) > 4:
            dirs.append(tok[4:].strip('"'))
        else:
            words.append(tok)
    return tags, dirs, " ".join(words)


def toggle_token(text, token, kind):
    """Ajoute token à la recherche en remplaçant ceux du même genre (« # » ou
    « dir: ») ; le retire s'il y est déjà."""
    toks = tokens(text)
    if token in toks:
        return " ".join(t for t in toks if t != token)
    return " ".join([t for t in toks if not t.startswith(kind)] + [token])


def dir_token(path):
    return f'dir:"{path}"' if " " in path else f"dir:{path}"
