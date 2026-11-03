"""Commandes « ! » de la saisie, comme le mode bash du terminal de Claude Code.

claude -p ne connaît pas ce mode (il transmettrait le texte tel quel) : claude-hub
lance la commande lui-même, dans le dossier de la conversation et sur son hôte, et
l'affiche dans le fil. Comme dans le terminal, Claude ne répond pas tout de suite :
la commande et sa sortie partent avec le message suivant, dans les balises que
Claude Code enregistre pour elles (<bash-input>, <bash-stdout>, <bash-stderr>).

Aussi : textes des shells et des monitors (outil Monitor) qui tournent en arrière-plan,
pour la liste d'activité à côté de la saisie (activity.py).
"""
import os
import re
import shlex
import signal
import subprocess
import threading
import time

from .gtk import GLib, Gtk, Pango
from .hosts import SSH_OPTS
from .widgets import weak_handler
from .listrow import BUSY_ICON
from .render import copy_button

SENT_LIMIT = 30000    # caractères de chaque sortie transmis à Claude (début et fin), comme Claude Code
SHOWN_LIMIT = 100000  # caractères de sortie affichés dans le fil, comme pour les outils
OUTPUT_LINES = 15     # au-delà, la sortie défile dans une hauteur fixe…
OUTPUT_HEIGHT = 300   # … de tant de pixels
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
EXIT = re.compile(r"(?:^|\n)Exit code (-?\d+)$")
STOPPED = "[commande arrêtée par l'utilisateur]"


def shell_command(text):
    """Commande d'une saisie « ! commande » ("" pour « ! » seul), sinon None."""
    return text[1:].strip() if text.startswith("!") else None


def is_monitor(sh):
    """Tâche de fond lancée par l'outil Monitor (sinon : un shell)."""
    return sh.get("kind") == "monitor"


def mark_monitor(s, tid):
    """Flux de claude -p : la tâche tid de la conversation s est un monitor. Le flux le
    décrit comme un shell (task_type « local_bash ») ; on le sait par l'appel de l'outil
    Monitor (task_started) ou par son résultat (taskId et timeoutMs)."""
    s.setdefault("monitors", set()).add(tid)
    for sh in s.get("bg_shells") or ():
        if sh["id"] == tid:
            sh["kind"] = "monitor"


def monitor_started(result):
    """Id de la tâche lancée d'après le résultat d'un appel de l'outil Monitor, sinon None."""
    if isinstance(result, dict) and result.get("taskId") and "timeoutMs" in result:
        return str(result["taskId"])
    return None


def background_counts(shells, one="1"):
    """« 1 shell », « 2 monitors », « 1 shell et 1 monitor »… (one : écriture de 1)."""
    n = sum(1 for sh in shells or () if is_monitor(sh))
    parts = [(len(shells or ()) - n, "shell"), (n, "monitor")]
    return " et ".join(f"{one if k == 1 else k} {what}{'s' if k > 1 else ''}" for k, what in parts if k)


def waiting_text(shells):
    """Ligne de Claude, à côté de la saisie, quand il a fini son tour et attend ses tâches
    de fond."""
    return f"Claude attend {background_counts(shells, 'un')}"


def since_text(seconds):
    """Durée à la minute près, pour une info-bulle qui ne change pas à chaque relevé."""
    minutes = max(0, int(seconds)) // 60
    if minutes < 1:
        return "moins d'une minute"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


def background_tooltip(shells, now=None, limit=8):
    """Info-bulle des lignes des tâches de fond : par shell ou monitor, sa description (ou sa commande) et
    depuis quand il tourne, puis la première ligne de sa commande, et pour un monitor son
    dernier événement."""
    now = time.time() if now is None else now
    lines = []
    for sh in (shells or ())[:limit]:
        desc = " ".join(str(sh.get("description") or "").split())
        cmd = " ".join(str(sh.get("command") or "").strip().split("\n")[0].split())
        what = desc or cmd or str(sh.get("id") or "shell")
        if is_monitor(sh):
            what = f"Monitor : {what}"
        start = sh.get("start")
        if isinstance(start, (int, float)) and not isinstance(start, bool):
            what += f" · depuis {since_text(now - start)}"
        lines.append(what[:160])
        if desc and cmd:
            lines.append(f"  $ {cmd[:120]}{'…' if len(cmd) > 120 else ''}")
        event = [line for line in str(sh.get("event") or "").split("\n") if line.strip()]
        if is_monitor(sh) and event:
            last = " ".join(event[-1].split())
            lines.append(f"  → {last[:120]}{'…' if len(last) > 120 else ''}")
    if len(shells or ()) > limit:
        lines.append(f"… et {len(shells) - limit} autres")
    return "\n".join(lines)


def clean(text):
    """Sortie lisible : sans codes de couleur ni retours chariot (barres de progression :
    seul le dernier état de la ligne reste)."""
    text = ANSI.sub("", text).replace("\r\n", "\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


def shell_argv(host, cwd, command):
    """(argv, dossier local) pour lancer command dans cwd sur host, dans un shell de login
    (comme claude). Sur un hôte distant, un dossier introuvable fait échouer la commande
    plutôt que de la lancer ailleurs. Le exec final garde le code de retour et évite
    ~/.bash_logout (souvent un « clear », dont les codes finiraient dans la sortie)."""
    line = f'{command}\nexec "$BASH" -c "exit $?"'
    if host == "local":
        return ["bash", "-lc", line], cwd
    line = f"cd {shlex.quote(cwd)} || exit 1\n{line}"
    return ["ssh", *SSH_OPTS, host, "bash -lc " + shlex.quote(line)], None


class Capture:
    """Ce qu'on garde d'une sortie pour Claude : son début et sa fin dans la limite
    SENT_LIMIT, et sa longueur totale (mémoire bornée, même pour une sortie énorme)."""

    def __init__(self, limit=SENT_LIMIT):
        self.head, self.tail, self.total, self.half = "", "", 0, limit // 2

    def add(self, chunk):
        self.total += len(chunk)
        room = self.half - len(self.head)
        if room > 0:
            self.head, chunk = self.head + chunk[:room], chunk[room:]
        if chunk:
            self.tail = (self.tail + chunk)[-self.half:]

    def text(self):
        cut = self.total - len(self.head) - len(self.tail)
        if cut <= 0:
            return self.head + self.tail
        return f"{self.head}\n[… {cut} caractères coupés …]\n{self.tail}"


def claude_blocks(command, stdout, stderr):
    """Blocs de texte d'une commande pour le message de Claude, tels que le terminal les
    enregistre (commande, puis sorties sans retour à la ligne final)."""
    return [{"type": "text", "text": f"<bash-input>{command}</bash-input>"},
            {"type": "text", "text": f"<bash-stdout>{stdout.rstrip(chr(10))}</bash-stdout>"
                                     f"<bash-stderr>{stderr.rstrip(chr(10))}</bash-stderr>"}]


class ShellBlock(Gtk.Box):
    """Commande « ! » dans le fil : la commande, sa sortie et son code de retour.

    Lancée d'ici (start), sa sortie s'affiche au fil de l'eau ; tant qu'elle n'est pas
    partie avec un message, elle se retire du fil (et n'est pas envoyée). Lue dans
    l'historique (from_item), elle s'affiche telle quelle.
    """

    def __init__(self, command, output="", code=None, stopped=False, done=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["shell-block"])
        self.command, self.code, self.proc = command, code, None
        self.done, self.stopped, self.sent = done, stopped, False
        self.out, self.err = Capture(), Capture()
        self.shown, self.flushing, self.on_done = output, 0, None

        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label="!", css_classes=["shell-mark"]))
        head.append(Gtk.Label(label=command or "(commande inconnue)", xalign=0, hexpand=True,
                              ellipsize=Pango.EllipsizeMode.END, tooltip_text=command or None,
                              css_classes=["shell-cmd"]))
        self.indicator = Gtk.Box(valign=Gtk.Align.CENTER, spacing=4)
        head.append(self.indicator)
        self.note = Gtk.Label(css_classes=["sid"], visible=False)
        head.append(self.note)
        self.stop_btn = Gtk.Button(icon_name="media-playback-stop-symbolic", visible=False,
                                   css_classes=["flat", "copy"], tooltip_text="Arrêter la commande")
        self.stop_btn.connect("clicked", weak_handler(self, lambda block, _b: block.stop()))
        head.append(self.stop_btn)
        self.copy_box = Gtk.Box()
        head.append(self.copy_box)
        self.drop_btn = Gtk.Button(icon_name="window-close-symbolic", visible=False,
                                   css_classes=["flat", "copy"],
                                   tooltip_text="Retirer la commande : elle ne sera pas envoyée à Claude")
        head.append(self.drop_btn)
        self.append(head)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["codeblock"])
        # Lignes longues renvoyées à la ligne (comme dans un terminal) ; une sortie courte
        # prend sa hauteur, une longue défile (hauteur fixe : pas de mesure du texte, faussée
        # tant que le bloc n'est pas dans la fenêtre).
        self.label = Gtk.Label(xalign=0, yalign=0, selectable=True, wrap=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR, css_classes=["code"])
        self.scroll = Gtk.ScrolledWindow(child=self.label, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.get_vadjustment().connect("changed", weak_handler(self, ShellBlock.follow))
        box.append(self.scroll)
        self.append(box)
        self.show_output()
        self.show_state()

    @classmethod
    def from_item(cls, item):
        """Bloc d'une commande lue dans l'historique ({"command", "stdout", "stderr"})."""
        out, err = clean(item.get("stdout") or "").rstrip("\n"), clean(item.get("stderr") or "")
        code, stopped = None, err.endswith(STOPPED)
        m = EXIT.search(err)  # code de retour ou arrêt, ajoutés par claude-hub à la fin des erreurs
        if m:
            code, err = int(m.group(1)), err[:m.start()]
        elif stopped:
            err = err[:-len(STOPPED)]
        err = err.rstrip("\n")
        return cls(item.get("command") or "", "\n".join(t for t in (out, err) if t), code, stopped)

    # -- exécution --
    def start(self, argv, cwd, on_done):
        """Lance la commande (bloc créé avec done=False) ; on_done(bloc) à la fin. OSError si
        elle ne peut pas partir."""
        self.on_done = on_done
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, errors="replace",
                                     start_new_session=True)  # Arrêter tue aussi ses enfants
        err = threading.Thread(target=self.read, args=(self.proc.stderr, self.err), daemon=True)
        err.start()

        def run():
            self.read(self.proc.stdout, self.out)
            err.join()
            GLib.idle_add(self.finish, self.proc.wait())
        threading.Thread(target=run, daemon=True).start()

    def read(self, stream, capture):
        for line in stream:
            GLib.idle_add(self.add, capture, clean(line))

    def add(self, capture, text):
        capture.add(text)
        if len(self.shown) < SHOWN_LIMIT:
            self.shown += text
            if len(self.shown) >= SHOWN_LIMIT:
                self.shown = self.shown[:SHOWN_LIMIT] + "\n[… affichage limité, la suite n'est pas montrée …]"
            if not self.flushing:  # un affichage tous les dixièmes de seconde, pas par ligne
                self.flushing = GLib.timeout_add(100, self.flush)

    def flush(self):
        self.flushing = 0
        self.show_output()
        return False

    def stop(self):
        if self.done or not self.proc:
            return
        self.stopped = True
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            pass

    def finish(self, code):
        self.done, self.code = True, code
        if self.flushing:
            GLib.source_remove(self.flushing)
            self.flushing = 0
        self.show_output()
        self.show_state()
        if self.on_done:
            self.on_done(self)

    def fail(self, message):
        """La commande n'a pas pu partir : le bloc garde le message, sans rien à envoyer."""
        self.shown, self.code, self.done = message, None, True
        self.show_output()
        self.show_state()
        self.indicator.append(Gtk.Image(icon_name="dialog-error-symbolic", css_classes=["tool-failed"],
                                        tooltip_text="Commande non lancée"))

    # -- envoi à Claude --
    def set_pending(self, on_drop):
        """Commande en attente du prochain message ; on_drop(bloc) : bouton « retirer »."""
        self.drop_btn.connect("clicked", weak_handler(self, lambda block, _b: on_drop(block)))
        self.drop_btn.set_visible(True)
        self.show_state()

    def mark_sent(self):
        self.sent = True
        self.drop_btn.set_visible(False)
        self.show_state()

    def blocks(self):
        """Blocs de la commande pour le message de Claude."""
        err = self.err.text().rstrip("\n")
        if self.stopped:
            err += ("\n" if err else "") + STOPPED
        elif self.code:
            err += ("\n" if err else "") + f"Exit code {self.code}"
        return claude_blocks(self.command, self.out.text(), err)

    # -- affichage --
    def show_output(self):
        text = self.shown.rstrip("\n")
        self.label.set_text(text or ("…" if not self.done else "(aucune sortie)"))
        self.label.set_css_classes(["code"] if text else ["code", "dim"])
        tall = text.count("\n") >= OUTPUT_LINES
        self.scroll.set_policy(Gtk.PolicyType.NEVER,
                               Gtk.PolicyType.AUTOMATIC if tall else Gtk.PolicyType.NEVER)
        self.scroll.set_min_content_height(OUTPUT_HEIGHT if tall else -1)

    def show_state(self):
        ind = self.indicator
        while (child := ind.get_first_child()):
            ind.remove(child)
        running = not self.done
        self.stop_btn.set_visible(running)
        if running:  # roue fixe : une animation fait redessiner la fenêtre en continu
            ind.append(Gtk.Image(icon_name=BUSY_ICON, css_classes=["tool-running"], tooltip_text="En cours"))
        elif self.stopped:
            ind.append(Gtk.Label(label="arrêtée", css_classes=["tool-error", "sid"]))
        elif self.code:
            ind.append(Gtk.Image(icon_name="dialog-error-symbolic", css_classes=["tool-failed"]))
            ind.append(Gtk.Label(label=f"code {self.code}", css_classes=["tool-error", "sid"]))
        elif self.code == 0:
            ind.append(Gtk.Image(icon_name="object-select-symbolic", css_classes=["tool-ok"],
                                 tooltip_text="Terminée"))
        if self.done and self.copy_box.get_first_child() is None:
            text = f"$ {self.command}\n{self.shown.rstrip(chr(10))}".rstrip("\n")
            self.copy_box.append(copy_button(text, "Copier la commande et sa sortie"))
        pending = self.drop_btn.get_visible()
        self.note.set_visible(pending)
        self.note.set_text("partira avec ton prochain message")

    def follow(self, adj):
        """Pendant l'exécution, la sortie défile avec ce qui arrive."""
        if not self.done:
            adj.set_value(adj.get_upper() - adj.get_page_size())
