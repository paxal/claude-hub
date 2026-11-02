"""Notes Markdown des conversations : une note par conversation.

La note est rangée sur l'hôte de la conversation, dans
~/.config/claude-hub/notes/<id>.md (XDG_CONFIG_HOME respecté) : un autre PC qui
affiche le même hôte SSH la retrouve. Le cloud n'a pas d'hôte où écrire : les notes
de ses conversations restent sur ce PC. Rien n'est écrit dans ~/.claude.

Sur un hôte SSH, la lecture passe par les scripts distants (commande `note`, et
l'index des notes renvoyé par `list` ou `notes`) ; l'écriture par `sh`, le texte
arrivant sur l'entrée standard (les scripts distants la prennent pour leur code).
"""
import queue
import re
import subprocess
import threading

from .cursor import norm_text
from .gtk import GLib
from .hosts import CLOUD, CONFIG, host_cmd, run_helper

NOTES_DIR = CONFIG / "notes"
EXCERPT = 1000  # caractères de chaque note gardés dans l'index (recherche, info-bulle)
RE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")  # identifiants de session : uuid, session_…, bc-…
# Écriture sur un hôte SSH : $1 = identifiant ; texte sur l'entrée standard, vide = supprimer.
WRITE_SH = r'''d="${XDG_CONFIG_HOME:-$HOME/.config}/claude-hub/notes"
mkdir -p "$d" && cat > "$d/.$1.md.tmp" || exit 1
if [ -s "$d/.$1.md.tmp" ]; then mv -f "$d/.$1.md.tmp" "$d/$1.md"
else rm -f "$d/.$1.md.tmp" "$d/$1.md"; fi'''


def store_of(host):
    """Hôte où sont rangées les notes des conversations de host (ce PC pour le cloud)."""
    return "local" if host == CLOUD else host


def where_text(host):
    """« sur ce PC » ou « sur cerberus.stockly.tech » : où la note de host est rangée."""
    store = store_of(host)
    return "sur ce PC" if store == "local" else f"sur {store}"


def note_path(host, sid):
    """Chemin de la note, tel qu'on l'écrit sur son hôte (info-bulle du volet)."""
    if store_of(host) == "local":
        return str(NOTES_DIR / f"{sid}.md")
    return f"~/.config/claude-hub/notes/{sid}.md"


def valid_id(sid):
    return bool(sid) and RE_ID.match(sid) is not None


def excerpt(text):
    return text[:EXCERPT]


def local_index():
    """{id: début de la note} des notes rangées sur ce PC."""
    out = {}
    try:
        files = sorted(NOTES_DIR.glob("*.md"))
    except OSError:
        return out
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            out[p.stem] = excerpt(text)
    return out


def host_index(host, listed=None):
    """{id: début de la note} rangées sur l'hôte des conversations de host. listed :
    réponse de la commande `list` (qui donne l'index) ; sinon il est demandé à part."""
    store = store_of(host)
    if store == "local":
        return local_index()
    if listed is not None and isinstance(listed.get("notes"), dict):
        return listed["notes"]
    return run_helper(store, "notes")


def read_note(host, sid):
    """Texte de la note de la conversation sid de host ("" s'il n'y en a pas)."""
    if not valid_id(sid):
        raise ValueError("identifiant de conversation inattendu")
    store = store_of(host)
    if store == "local":
        try:
            with open(NOTES_DIR / f"{sid}.md", encoding="utf-8", errors="replace", newline="") as f:
                return f.read()
        except FileNotFoundError:
            return ""
    res = run_helper(store, "note", sid)
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res.get("text") or ""


def write_note(host, sid, text):
    """Enregistre la note (la supprime si elle est vide)."""
    if not valid_id(sid):
        raise ValueError("identifiant de conversation inattendu")
    if not text.strip():
        text = ""
    store = store_of(host)
    if store == "local":
        path = NOTES_DIR / f"{sid}.md"
        if not text:
            path.unlink(missing_ok=True)
            return
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        tmp = NOTES_DIR / f".{sid}.md.tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        tmp.replace(path)
        return
    r = subprocess.run(host_cmd(store, ["sh", "-c", WRITE_SH, "sh", sid]), input=text,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"code {r.returncode}")


class NoteQueue:
    """Lit et enregistre les notes l'une après l'autre, dans l'ordre des demandes, hors de
    la boucle GTK : une lecture voit les enregistrements demandés avant elle. Les
    rappels sont appelés dans la boucle GTK."""

    def __init__(self):
        self.jobs = queue.Queue()
        self.latest = {}  # (hôte, id) -> numéro du dernier enregistrement : les précédents sont sautés
        self.count = 0
        self.writes = 0   # enregistrements demandés pas encore faits
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.worker = None

    def save(self, host, sid, text, on_done=None, on_error=None):
        self.put((store_of(host), sid), lambda: write_note(host, sid, text) or text, on_done, on_error)

    def load(self, host, sid, on_done, on_error=None):
        self.put(None, lambda: read_note(host, sid), on_done, on_error)

    def put(self, key, fn, on_done, on_error):
        with self.lock:
            self.count += 1
            if key is not None:
                self.latest[key] = self.count
                self.writes += 1
            self.jobs.put((self.count, key, fn, on_done, on_error))
            if self.worker is None:
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()

    def run(self):
        while True:
            with self.lock:
                try:
                    n, key, fn, on_done, on_error = self.jobs.get_nowait()
                except queue.Empty:
                    self.worker = None
                    return
            if key is None or self.latest.get(key) == n:  # sinon une version plus récente suit
                try:
                    res = fn()
                except Exception as e:  # noqa: BLE001
                    if on_error:
                        GLib.idle_add(lambda e=e, f=on_error: f(e) and False)
                else:
                    if on_done:
                        GLib.idle_add(lambda r=res, f=on_done: f(r) and False)
            if key is not None:
                with self.lock:
                    self.writes -= 1
                    self.idle.notify_all()

    def wait(self, timeout):
        """Attend la fin des enregistrements en cours (fermeture de claude-hub)."""
        with self.idle:
            self.idle.wait_for(lambda: self.writes == 0, timeout)


def note_preview(text, lines=8, width=400):
    """Début d'une note pour une info-bulle."""
    out = "\n".join(text.strip().splitlines()[:lines])
    return out if len(out) <= width else out[:width].rstrip() + "…"


def note_snippet(text, q):
    """Ligne de la note où figure q (déjà normalisé, cf. norm_text), coupée un peu avant ;
    None si q n'y est pas."""
    for line in text.splitlines():
        low, origin = [], []
        for i, ch in enumerate(line):
            for c in norm_text(ch):
                low.append(c)
                origin.append(i)
        pos = "".join(low).find(q)
        if pos >= 0:
            start = max(0, origin[pos] - 30)
            return ("…" if start else "") + line[start:].strip()
    return None
