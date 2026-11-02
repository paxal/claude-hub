"""Configuration, hôtes (local / SSH) et exécution des scripts d'inspection."""
import json
import os
import re
import shlex
import subprocess
from pathlib import Path


CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "claude-hub"
HOSTS_FILE = CONFIG / "hosts"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ControlMaster=auto",
            "-o", f"ControlPath={os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/claude-hub-%C",
            "-o", "ControlPersist=10m"]
PERMISSION_MODES = ["acceptEdits", "auto", "default", "plan", "dontAsk", "bypassPermissions"]
# Libellés affichés (--permission-mode attend les noms ci-dessus) et leur explication.
MODE_LABELS = {"acceptEdits": "Modifications acceptées", "auto": "Automatique",
               "default": "Demander à chaque fois", "plan": "Plan (lecture seule)",
               "dontAsk": "Refuser sans demander", "bypassPermissions": "Tout autoriser"}
MODE_HELP = {"acceptEdits": "modifie les fichiers sans demander, demande pour le reste",
             "auto": "Claude juge lui-même ce qui est sans risque",
             "default": "demande avant chaque outil sensible",
             "plan": "explore et propose un plan, sans rien modifier",
             "dontAsk": "refuse ce qui n'est pas déjà autorisé, sans demander",
             "bypassPermissions": "tout est permis, sans aucune demande"}
DEFAULT_MODE = "acceptEdits"  # avant tout choix
MODES_KEPT = 1000  # sessions dont on retient le mode
# Premier choix : ne rien passer à claude (réglage par défaut de l'hôte).
MODELS = ["Modèle par défaut", "fable", "opus", "sonnet", "haiku"]
EFFORTS = ["Effort par défaut", "low", "medium", "high", "xhigh", "max"]
# Premier choix une fois connu le réglage de l'hôte.
DEFAULT_LABEL = {"model": "Modèle : {} (défaut)", "effort": "Effort : {} (défaut)"}
# Modèle désigné par chaque alias à l'écriture de ce code. Remplacé par ce que
# claude annonce au démarrage (settings["model_versions"]) dès qu'on s'en sert.
MODEL_VERSIONS = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5",
                  "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5-20251001"}


def model_name(model_id):
    """claude-opus-5-5 -> Opus 5.5 ; un identifiant inconnu est rendu tel quel."""
    base, wide = (model_id[:-4], " (1M)") if model_id.endswith("[1m]") else (model_id, "")
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?", base)
    if not m:
        return model_id
    family, major, minor = m.groups()
    return f"{family.capitalize()} {major}{'.' + minor if minor else ''}{wide}"
HISTORY_LIMIT = 300
SEARCH_HISTORY_LIMIT = 2000  # pour retrouver un passage ancien après une recherche

# Exécutés par `python3 -` (ou `node -`) sur l'hôte (local ou distant) : lisent
# ~/.claude et renvoient du JSON. Ils restent compatibles avec un python3 un peu
# ancien et ne doivent dépendre que de la bibliothèque standard.
HELPER = (Path(__file__).parent / "remote/helper.py").read_text()
HELPER_JS = (Path(__file__).parent / "remote/helper.js").read_text()


CLOUD = "cloud"  # pseudo-hôte : sessions Claude sur claude.ai et agents cloud Cursor


def load_hosts():
    try:
        hosts = [h.strip() for h in HOSTS_FILE.read_text().splitlines() if h.strip()]
    except FileNotFoundError:
        hosts = []
    return ["local"] + [h for h in hosts if h not in ("local", CLOUD)] + [CLOUD]


def load_hosts_with(hosts):
    save_hosts(hosts)
    return load_hosts()


def save_hosts(hosts):
    CONFIG.mkdir(parents=True, exist_ok=True)
    HOSTS_FILE.write_text("\n".join(hosts) + "\n")


SETTINGS_FILE = CONFIG / "settings.json"


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        data = {}
    data.setdefault("claude", True)
    data.setdefault("cursor", True)
    data.setdefault("show_archived", False)
    data.setdefault("hosts_shown", ["local"])  # hôtes cochés : leurs conversations sont listées
    data.setdefault("model", None)
    data.setdefault("effort", None)
    data.setdefault("mode", DEFAULT_MODE)  # dernier mode choisi : celui des nouvelles conversations
    data.setdefault("modes", {})            # id de session -> dernier mode utilisé
    data["model_versions"] = dict(MODEL_VERSIONS, **data.get("model_versions", {}))
    data["archived"] = set(data.get("archived", []))      # archivées ici
    data["unarchived"] = set(data.get("unarchived", []))  # désarchivées ici (archives Cursor)
    data["unread"] = set(data.get("unread", []))          # réponses arrivées hors de la vue
    data["pinned"] = set(data.get("pinned", []))          # conversations épinglées en tête de liste
    data.setdefault("views", [])            # recherches enregistrées : [{"query"}]
    data.setdefault("costs", {})            # id de session -> coût ($) de la dernière réponse lancée d'ici
    data.setdefault("context_windows", {})  # modèle -> taille de sa fenêtre de contexte (apprise de claude)
    return data


def save_settings(data):
    CONFIG.mkdir(parents=True, exist_ok=True)
    out = dict(data, archived=sorted(data["archived"]), unarchived=sorted(data["unarchived"]),
               unread=sorted(data["unread"]), pinned=sorted(data["pinned"]))
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1))
    tmp.replace(SETTINGS_FILE)


def remember_mode(data, sid, mode):
    """Retient le mode d'une session (et comme dernier choix). Vrai si quelque chose a changé."""
    if mode not in PERMISSION_MODES or (data["modes"].get(sid) == mode and data["mode"] == mode):
        return False
    data["modes"].pop(sid, None)  # remis en fin : les plus anciennes partent d'abord
    data["modes"][sid] = mode
    for old in list(data["modes"])[:-MODES_KEPT]:
        del data["modes"][old]
    data["mode"] = mode
    return True


def host_cmd(host, argv):
    """argv à exécuter sur l'hôte, dans un shell de login (PATH de l'utilisateur)."""
    if host == "local":
        return argv
    return ["ssh", *SSH_OPTS, host, "bash -lc " + shlex.quote(shlex.join(argv))]


_interpreters = {}


def interpreter(host):
    """python3 si l'hôte l'a, sinon node (ex. NixOS où seul claude/node est installé)."""
    if host not in _interpreters:
        r = subprocess.run(host_cmd(host, ["sh", "-c", "command -v python3 >/dev/null && echo python3"
                                           " || { command -v node >/dev/null && echo node; }"]),
                           capture_output=True, text=True, timeout=30)
        found = r.stdout.strip()
        if r.returncode != 0 or not found:
            raise RuntimeError(r.stderr.strip() or "ni python3 ni node sur l'hôte")
        _interpreters[host] = found
    return _interpreters[host]


def run_helper(host, *args):
    interp = interpreter(host)
    script = HELPER if interp == "python3" else HELPER_JS
    r = subprocess.run(host_cmd(host, [interp, "-", *map(str, args)]),
                       input=script, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"code {r.returncode}")
    return json.loads(r.stdout)
