"""Complétion des commandes « / » : liste demandée à claude, filtrage et suggestions."""
import json
import os
import shlex
import subprocess
import threading

from .gtk import Gdk, Gtk, Pango
from .hosts import SSH_OPTS

TIMEOUT = 30  # secondes


def fetch_commands(host, cwd):
    """Demande à claude la liste de ses commandes (intégrées, skills, plugins, du projet).

    C'est la requête de contrôle `initialize` du SDK : pas de tour du modèle, et
    `--no-session-persistence` évite d'écrire une conversation. Le dossier compte :
    les commandes et skills du projet (.claude/) n'existent que là.
    """
    argv = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--no-session-persistence"]
    if host == "local":
        cmd, cwd = argv, cwd if cwd and os.path.isdir(cwd) else None
    else:  # comme launch() : cd côté distant puis claude, dans un shell de login
        line = (f"cd {shlex.quote(cwd)} 2>/dev/null; " if cwd else "") + f"exec {shlex.join(argv)}"
        cmd, cwd = ["ssh", *SSH_OPTS, host, "bash -lc " + shlex.quote(line)], None
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(TIMEOUT, proc.kill)
    timer.start()
    try:
        proc.stdin.write(json.dumps({"type": "control_request", "request_id": "commandes",
                                     "request": {"subtype": "initialize"}}) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "control_response":
                resp = ev.get("response") or {}
                if resp.get("subtype") == "error":
                    raise RuntimeError(str(resp.get("error")))
                return clean((resp.get("response") or {}).get("commands") or [])
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()
    raise RuntimeError("claude n'a pas donné la liste des commandes")


def clean(commands):
    """Garde nom, arguments, description et alias ; écarte les commandes internes (« __… »)."""
    out = []
    for c in commands:
        name = str(c.get("name") or "")
        if not name or name.startswith("__"):
            continue
        out.append({"name": name, "hint": str(c.get("argumentHint") or ""),
                    "description": " ".join(str(c.get("description") or "").split()),
                    "aliases": [str(a) for a in c.get("aliases") or []]})
    return out


def typed_command(text):
    """Ce qui suit « / » si la saisie n'est encore qu'un début de commande, sinon None."""
    if not text.startswith("/") or any(ch.isspace() for ch in text):
        return None
    return text[1:]


def match_commands(commands, query):
    """Commandes correspondant à query : début du nom ou d'un alias d'abord, puis le reste
    (sans tenir compte de la casse), chaque groupe par ordre alphabétique."""
    q = query.lower()
    starts, contains = [], []
    for c in commands:
        names = [c["name"].lower()] + [a.lower() for a in c["aliases"]]
        if any(n.startswith(q) for n in names):
            starts.append(c)
        elif any(q in n for n in names):
            contains.append(c)
    key = lambda c: c["name"].lower()  # noqa: E731
    return sorted(starts, key=key) + sorted(contains, key=key)


def short(description, limit=120):
    """Première phrase de la description, pour une ligne de suggestion."""
    first = description.split(". ")[0]
    return first if len(first) <= limit else first[:limit - 1] + "…"


class CommandPopover(Gtk.Popover):
    """Suggestions de commandes au-dessus de la saisie, pilotées au clavier par la fenêtre.

    autohide=False : la saisie garde le focus et le clavier, on continue d'écrire.
    on_pick(commande) est appelé au clic sur une suggestion.
    """

    MAX = 50

    def __init__(self, parent, on_pick):
        super().__init__(autohide=False, has_arrow=False, position=Gtk.PositionType.TOP,
                         can_focus=False, css_classes=["commands"])
        self.set_parent(parent)
        self.on_pick = on_pick
        self.matches = []
        self.list = Gtk.ListBox(css_classes=["navigation-sidebar"], can_focus=False,
                                selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.connect("row-activated", lambda _l, r: self.on_pick(self.matches[r.get_index()]))
        self.scroll = Gtk.ScrolledWindow(child=self.list, max_content_height=280, width_request=520,
                                         propagate_natural_height=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.message = Gtk.Label(xalign=0, css_classes=["sid"], margin_start=6, margin_end=6,
                                 margin_top=4, margin_bottom=4, wrap=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.scroll)
        box.append(self.message)
        self.set_child(box)

    def show_message(self, text):
        """Affiche un message à la place des suggestions (chargement, erreur)."""
        self.matches = []
        self.list.remove_all()
        self.scroll.set_visible(False)
        self.message.set_text(text)
        self.message.set_visible(True)
        self.place()

    def show_matches(self, matches):
        self.matches = matches[:self.MAX]
        self.list.remove_all()
        for c in self.matches:
            row = Gtk.Box(spacing=8, margin_start=4, margin_end=4)
            name = "/" + c["name"]
            if c["aliases"]:
                name += f" ({', '.join(c['aliases'])})"
            row.append(Gtk.Label(label=name, xalign=0, css_classes=["heading"]))
            if c["hint"]:
                row.append(Gtk.Label(label=c["hint"], xalign=0, css_classes=["sid"],
                                     ellipsize=Pango.EllipsizeMode.END, max_width_chars=30))
            row.append(Gtk.Label(label=short(c["description"]), xalign=0, hexpand=True,
                                 css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END))
            self.list.append(Gtk.ListBoxRow(child=row, can_focus=False,
                                            tooltip_text=c["description"] or None))
        self.scroll.set_visible(True)
        self.message.set_visible(False)
        self.list.select_row(self.list.get_row_at_index(0))
        self.place()

    def place(self):
        """Aligne la fenêtre sur le bord gauche de la saisie, juste au-dessus."""
        rect = Gdk.Rectangle()  # le constructeur ignore ses arguments
        rect.x, rect.y, rect.height = 0, 0, 1
        rect.width = min(520, max(1, self.get_parent().get_width()))
        self.set_pointing_to(rect)
        if not self.get_visible():
            self.popup()

    def selected(self):
        row = self.list.get_selected_row()
        return self.matches[row.get_index()] if row and self.matches else None

    def move(self, step):
        """Monte (-1) ou descend (+1) dans les suggestions, en bouclant."""
        if not self.matches:
            return
        row = self.list.get_selected_row()
        i = ((row.get_index() if row else -1) + step) % len(self.matches)
        row = self.list.get_row_at_index(i)
        self.list.select_row(row)
        # garder la ligne visible dans la liste déroulante
        ok, bounds = row.compute_bounds(self.list)
        if ok:
            adj = self.scroll.get_vadjustment()
            top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
            if top < adj.get_value():
                adj.set_value(top)
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size())
