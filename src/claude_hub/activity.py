"""Ce qui tourne et ce qui attend, à côté de la saisie : Claude (étape, durée, « Arrêter »),
la demande qui attend une réponse, les sous-agents au travail, les shells et monitors en
arrière-plan, les tâches de Claude et les messages en file.

Une ligne par élément (ActivityRow), décrite par un dictionnaire (« spec ») : clé, genre,
libellé, détail… update_activity (window.py) est appelé à chaque événement du flux de
claude -p : les lignes sont mises à jour sur place, d'après leur clé, plutôt que refaites.

ActivitySplit place la liste à droite de la saisie quand la place le permet, au-dessus
sinon (volet ouvert, fenêtre étroite). Seul Claude au travail a une roue animée (voir
AGENTS.md, « Animations ») ; les autres lignes ont une icône fixe.
"""
import time

from .gtk import Gio, GLib, Graphene, Gsk, Gtk, Pango
from .agentsview import agent_line, last_step
from .listrow import elapsed_text
from .shell import background_tooltip, is_monitor

SIDE_WIDTH = 280    # largeur de la liste, à droite de la saisie
MAIN_MIN = 380      # largeur minimale laissée à la saisie à côté de la liste
MAX_HEIGHT = 300    # au-delà, la liste défile
MAX_AGENTS = 3      # sous-agents listés un par un ; les suivants en une ligne

AGENT_ICON = ("claude-hub-agents-symbolic",)
TERMINAL_ICON = ("utilities-terminal-symbolic", "terminal-symbolic")
MONITOR_ICON = ("view-visible-symbolic", "view-reveal-symbolic", "eye-symbolic")
WARNING_ICON = ("dialog-warning-symbolic",)
QUEUED_ICON = ("claude-hub-queued-symbolic",)

# Boutons possibles d'une ligne : nom (clé de spec["actions"]), icône, classes CSS.
BUTTONS = (("stop", "media-playback-stop-symbolic", ["stop", "act-stop"]),
           ("jump", "go-up-symbolic", ["flat", "act-btn"]),
           ("open", "go-next-symbolic", ["flat", "act-btn"]),
           ("edit", "document-edit-symbolic", ["flat", "act-btn"]),
           ("remove", "window-close-symbolic", ["flat", "act-btn"]))


def one_line(text):
    return " ".join(str(text or "").split())


def minutes_text(seconds):
    """Durée d'une tâche de fond, à la minute : « < 1 min », « 6 min », « 1 h 05 »."""
    minutes = max(0, int(seconds)) // 60
    if minutes < 1:
        return "< 1 min"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d}"


def claude_spec(text, detail=None, since=None, stop=None):
    """Claude au travail : étape (« Claude utilise Bash »), son détail et sa durée."""
    return {"key": "claude", "kind": "claude", "spin": True, "label": text,
            "detail": one_line(detail), "mono": True, "since": since, "timed": True,
            "tooltip": one_line(detail) or None,
            "actions": {"stop": ("Interrompre Claude (Échap)", stop)} if stop else {}}


def request_spec(entry, jump=None):
    """Demande en attente (request_entry de window.py) : claude lancé d'ici, que l'on
    rejoint dans le fil (jump), ou action à faire là où tourne claude (terminal…)."""
    kind, text = entry.get("kind"), one_line(entry.get("text"))
    detail = {"plan": "Plan à valider", "question": f"Question : {text}",
              "permission": f"Autorisation : {text}"}.get(kind, text)
    return {"key": "request", "kind": "request", "icon": WARNING_ICON,
            "label": "Claude attend ta réponse" if jump else "Action requise",
            "detail": detail, "tooltip": detail or None,
            "actions": {"jump": ("Aller à la demande", jump)} if jump else {}}


def agent_spec(a, indent=False, open_agent=None):
    """Sous-agent au travail : sa description (ou son type) et sa dernière étape."""
    step = one_line(last_step(a))
    return {"key": f"agent:{a.get('id') or a.get('tool')}", "kind": "agent", "icon": AGENT_ICON,
            "label": one_line(a.get("description") or a.get("type")) or "Sous-agent",
            "detail": step, "mono": True, "indent": indent,
            "tooltip": f"{a.get('type') or 'Agent'} · {agent_line(a)}"[:300],
            "actions": {"open": ("Voir dans le volet des sous-agents", open_agent)} if open_agent else {}}


def more_agents_spec(n, indent=False, open_panel=None):
    """Sous-agents au-delà de MAX_AGENTS, en une ligne."""
    return {"key": "agents-more", "kind": "agent", "icon": AGENT_ICON, "indent": indent,
            "label": f"… et {n} autre{'s' if n > 1 else ''} sous-agent{'s' if n > 1 else ''}",
            "actions": {"open": ("Volet des sous-agents", open_panel)} if open_panel else {}}


def agent_specs(agents, indent=False, open_agent=None, open_panel=None):
    """Lignes des sous-agents au travail : MAX_AGENTS au plus, puis « … et n autres »."""
    # une ligne « … et 1 autre » n'économiserait rien : tous listés jusqu'à MAX_AGENTS + 1
    shown = agents if len(agents) <= MAX_AGENTS + 1 else agents[:MAX_AGENTS]
    specs = [agent_spec(a, indent, open_agent and (lambda a=a: open_agent(a))) for a in shown]
    if len(agents) > len(shown):
        specs.append(more_agents_spec(len(agents) - len(shown), indent, open_panel))
    return specs


def shell_spec(sh):
    """Shell ou monitor en arrière-plan : sa description (sinon sa commande), depuis quand
    il tourne, et pour un monitor son dernier événement."""
    monitor = is_monitor(sh)
    desc = one_line(sh.get("description"))
    cmd = one_line(str(sh.get("command") or "").strip().split("\n")[0])
    events = [line for line in str(sh.get("event") or "").split("\n") if line.strip()]
    start = sh.get("start")
    return {"key": f"shell:{sh.get('id')}", "kind": "monitor" if monitor else "shell",
            "icon": MONITOR_ICON if monitor else TERMINAL_ICON,
            "label": "Monitor" if monitor else "Shell",
            "detail": desc or cmd or one_line(sh.get("id")), "mono": not desc,
            "sub": f"→ {one_line(events[-1])}" if monitor and events else "",
            "start": start if isinstance(start, (int, float)) and not isinstance(start, bool) else None,
            "tooltip": background_tooltip([sh])}


def queued_spec(i, text, images, edit=None, remove=None):
    """Message en file : envoyé à la fin de la réponse en cours."""
    summary = one_line(text) or ("(image seule)" if images else "(commandes « ! » seules)")
    if images:
        summary += f"  ·  {len(images)} image{'s' if len(images) > 1 else ''}"
    return {"key": f"queued:{i}:{hash(text)}", "kind": "queued", "icon": QUEUED_ICON,
            "label": "En file", "detail": summary,
            "tooltip": (text or summary) + "\n\nEnvoyé à la fin de la réponse en cours",
            "actions": {"edit": ("Remettre dans la saisie pour le modifier", edit),
                        "remove": ("Retirer de la file", remove)}}


def on_button(button, name):
    """Clic sur un bouton d'une ligne : l'action de sa spec. (Fonction du module, pas une
    méthode de la ligne : celle-ci serait retenue par son propre bouton, voir AGENTS.md.)"""
    row = button.get_ancestor(ActivityRow)
    action = row.actions.get(name) if row is not None else None
    if action and action[1]:
        action[1]()


def set_text(label, text):
    if label.get_text() != (text or ""):
        label.set_text(text or "")


class ActivityRow(Gtk.Box):
    """Une ligne : icône (ou roue), libellé et durée, détail, deuxième détail, boutons."""

    def __init__(self, key):
        super().__init__(spacing=8)
        self.add_css_class("act-row")
        self.key, self.actions, self.icon = key, {}, None
        self.since = self.start = None
        icon = Gtk.Box(css_classes=["act-icon"], valign=Gtk.Align.START)
        self.spinner = Gtk.Spinner(visible=False)
        self.image = Gtk.Image(visible=False)
        icon.append(self.spinner)
        icon.append(self.image)
        self.append(icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        top = Gtk.Box(spacing=6)
        self.label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                               ellipsize=Pango.EllipsizeMode.END, css_classes=["act-label"])
        self.time = Gtk.Label(xalign=1, hexpand=True, valign=Gtk.Align.START, css_classes=["act-time"])
        top.append(self.label)
        top.append(self.time)
        texts.append(top)
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False,
                                css_classes=["act-detail"])
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False,
                             css_classes=["act-sub"])
        texts.append(self.detail)
        texts.append(self.sub)
        self.append(texts)
        buttons = Gtk.Box(valign=Gtk.Align.START, css_classes=["act-btns"])
        self.buttons = {}
        for name, icon_name, css in BUTTONS:
            btn = Gtk.Button(icon_name=icon_name, css_classes=css, visible=False,
                             valign=Gtk.Align.START)
            btn.connect("clicked", on_button, name)
            buttons.append(btn)
            self.buttons[name] = btn
        self.append(buttons)

    def update(self, spec):
        classes = ["act-row", spec["kind"]] + (["sub"] if spec.get("indent") else [])
        if set(self.get_css_classes()) != set(classes):
            self.set_css_classes(classes)
        set_text(self.label, spec.get("label"))
        for label, text in ((self.detail, spec.get("detail")), (self.sub, spec.get("sub"))):
            set_text(label, text)
            label.set_visible(bool(text))
        mono = bool(spec.get("mono"))
        if self.detail.has_css_class("mono") != mono:
            (self.detail.add_css_class if mono else self.detail.remove_css_class)("mono")
        tip = spec.get("tooltip")
        if self.get_tooltip_text() != tip:
            self.set_tooltip_text(tip)
        spin = bool(spec.get("spin"))
        self.spinner.set_visible(spin)
        self.spinner.set_spinning(spin)
        icon = spec.get("icon")
        self.image.set_visible(not spin and bool(icon))
        if icon and icon != self.icon:
            self.icon = icon
            self.image.set_from_gicon(Gio.ThemedIcon.new_from_names(list(icon)))
        # Durée : depuis since (time.monotonic, à la seconde) ou start (heure, à la minute).
        # Une étape sans heure de début garde celle de la ligne.
        if spec.get("timed"):
            self.since = spec.get("since") or self.since or time.monotonic()
        else:
            self.since = None
        self.start = spec.get("start")
        self.tick()
        self.actions = spec.get("actions") or {}
        for name, btn in self.buttons.items():
            action = self.actions.get(name)
            btn.set_visible(bool(action))
            if action and btn.get_tooltip_text() != action[0]:
                btn.set_tooltip_text(action[0])

    def tick(self):
        """Met la durée à jour ; renvoie True si la ligne en affiche une."""
        if self.since is not None:
            text = elapsed_text(time.monotonic() - self.since)
        elif self.start is not None:
            text = minutes_text(time.time() - self.start)
        else:
            text = ""
        set_text(self.time, text)
        return bool(text)


def sync_rows(box, specs):
    """Met les lignes de box en accord avec specs : celles de même clé sont mises à jour
    sur place, les autres créées ou retirées."""
    old, child = {}, box.get_first_child()
    while child is not None:
        old[child.key] = child
        child = child.get_next_sibling()
    prev = None
    for spec in specs:
        row = old.pop(spec["key"], None)
        if row is None:
            row = ActivityRow(spec["key"])
            box.insert_child_after(row, prev)
        elif row.get_prev_sibling() is not prev:
            box.reorder_child_after(row, prev)
        row.update(spec)
        prev = row
    for row in old.values():
        box.remove(row)


class ActivityPanel(Gtk.Box):
    """La liste : Claude, demande, sous-agents, tâches de fond, puis les tâches de Claude
    (todos, volet repliable de todos.py) et les messages en file."""

    def __init__(self, todos):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["activity"],
                         valign=Gtk.Align.START)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.queued = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.todos = todos
        for w in (self.rows, todos, self.queued):
            self.append(w)
        self.timer = 0

    def show(self, specs=None, queued=None):
        """Remplace les lignes d'activité (specs) et/ou celles des messages en file."""
        if specs is not None:
            sync_rows(self.rows, specs)
        if queued is not None:
            sync_rows(self.queued, queued)
        timed = self.tick()
        if timed and not self.timer:
            self.timer = GLib.timeout_add(1000, self.on_timer)
        elif not timed and self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def tick(self):
        timed, child = False, self.rows.get_first_child()
        while child is not None:
            timed |= child.tick()
            child = child.get_next_sibling()
        return timed

    def on_timer(self):
        if self.tick():
            return True
        self.timer = 0
        return False

    def has_content(self):
        return (self.rows.get_first_child() is not None or self.queued.get_first_child() is not None
                or self.todos.get_reveal_child())

    def labels(self):
        """Textes des lignes (libellé · détail), pour les tests."""
        out = []
        for box in (self.rows, self.queued):
            child = box.get_first_child()
            while child is not None:
                out.append(" · ".join(t for t in (child.label.get_text(), child.detail.get_text()) if t))
                child = child.get_next_sibling()
        return out


class ActivitySplit(Gtk.Widget):
    """Saisie (main) et liste d'activité (side) : côte à côte quand la saisie garde au
    moins MAIN_MIN pixels, sinon la liste au-dessus. Côte à côte, la saisie prend la
    hauteur de la liste si celle-ci est plus haute (le champ de texte s'étire)."""

    def __init__(self, main, side, **kw):
        # vexpand explicite : celui du champ de texte ne doit pas remonter à la colonne
        super().__init__(vexpand=False, **kw)
        self.main, self.side = main, side
        main.set_parent(self)
        side.set_parent(self)  # dessinée après la saisie : son trait de séparation par-dessus

    def wide(self, width):
        need = max(self.main.measure(Gtk.Orientation.HORIZONTAL, -1)[0], MAIN_MIN)
        return width - SIDE_WIDTH >= need

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        side = self.side.get_visible()
        if orientation == Gtk.Orientation.HORIZONTAL:
            mn, nat = self.main.measure(orientation, -1)[:2]
            if side:
                mn = max(mn, self.side.measure(orientation, -1)[0])
                nat += SIDE_WIDTH
            return mn, nat, -1, -1
        if not side:
            mn, nat = self.main.measure(orientation, for_size)[:2]
            return mn, nat, -1, -1
        if for_size < 0:
            for_size = self.do_measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        if self.wide(for_size):
            m = self.main.measure(orientation, for_size - SIDE_WIDTH)
            s = self.side.measure(orientation, SIDE_WIDTH)
            return max(m[0], s[0]), max(m[1], s[1]), -1, -1
        m = self.main.measure(orientation, for_size)
        s = self.side.measure(orientation, for_size)
        return m[0] + s[0], m[1] + s[1], -1, -1

    def do_size_allocate(self, width, height, _baseline):
        if not self.side.get_visible():
            self.main.allocate(width, height, -1, None)
        elif self.wide(width):
            self.main.allocate(width - SIDE_WIDTH, height, -1, None)
            self.side.allocate(SIDE_WIDTH, height, -1, translate(width - SIDE_WIDTH, 0))
        else:
            main_min = self.main.measure(Gtk.Orientation.VERTICAL, width)[0]
            side = max(0, min(self.side.measure(Gtk.Orientation.VERTICAL, width)[1], height - main_min))
            self.side.allocate(width, side, -1, None)
            self.main.allocate(width, height - side, -1, translate(0, side))

    def do_dispose(self):
        for child in (self.main, self.side):
            if child is not None:
                child.unparent()
        self.main = self.side = None


def translate(x, y):
    return Gsk.Transform().translate(Graphene.Point().init(x, y))
