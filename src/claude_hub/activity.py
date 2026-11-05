"""Ce qui tourne et ce qui attend, au-dessus de la saisie : Claude (étape, durée, « Arrêter »),
la demande qui attend une réponse, les sous-agents au travail, les workflows, les shells et
monitors en arrière-plan, les tâches de Claude et les messages en file.

Comme dans le terminal, un sous-agent tient sur une ligne (sa description, puis son étape),
dans l'ordre où ils ont démarré ; un workflow aussi (nom, barre d'avancement, agents finis
sur lancés), sans ses agents.

Une ligne par élément (ActivityRow), décrite par un dictionnaire (« spec ») : clé, genre,
libellé, détail… update_activity (window.py) est appelé à chaque événement du flux de
claude -p : les lignes sont mises à jour sur place, d'après leur clé, plutôt que refaites.

La liste se place au-dessus du texte, dans le cadre de la saisie (window.py), qui garde
ainsi toute la largeur de la colonne. Seul Claude au travail a une roue animée (voir
AGENTS.md, « Animations ») ; les autres lignes ont une icône fixe.

Les shells, les monitors et les tâches de Claude, qui restent là longtemps, sont repliés
derrière une ligne de résumé (« 1 monitor · 1 shell · tâches 3/7 ») : repliés au lancement,
un clic les déplie pour toutes les conversations, jusqu'au clic suivant. Claude, la demande,
les sous-agents et la file restent toujours visibles.
"""
import time

from .gtk import Gio, GLib, Gtk, Pango
from .agentsview import agent_line, step_of, workflow_detail
from .listrow import elapsed_text
from .shell import background_tooltip, is_monitor

MAX_HEIGHT = 300    # au-delà, la liste défile
MAX_AGENTS = 5      # sous-agents listés un par un ; les suivants en une ligne
GROUPED = ("monitor", "shell")  # genres repliés avec les tâches derrière la ligne de résumé

AGENT_ICON = ("claude-hub-agents-symbolic",)
WORKFLOW_ICON = ("view-process-tree-symbolic", "network-workgroup-symbolic", "claude-hub-agents-symbolic")
# Libellé et étape d'une ligne inline alignés sur leur ligne de base (tailles différentes).
BASELINE = getattr(Gtk.Align, "BASELINE_FILL", Gtk.Align.BASELINE)
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


def claude_spec(text, detail=None, since=None, stop=None, stop_tip="Interrompre Claude (Échap)"):
    """Claude au travail : étape (« Claude utilise Bash »), son détail et sa durée."""
    return {"key": "claude", "kind": "claude", "spin": True, "label": text,
            "detail": one_line(detail), "mono": True, "since": since, "timed": True,
            "tooltip": one_line(detail) or None,
            "actions": {"stop": (stop_tip, stop)} if stop else {}}


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
    """Sous-agent au travail, sur une ligne : sa description (ou son type), puis sa dernière
    étape (la description de l'appel, comme le terminal ; l'appel tel quel en info-bulle)."""
    step, code = step_of(a)
    step = one_line(step)
    return {"key": f"agent:{a.get('id') or a.get('tool')}", "kind": "agent", "icon": AGENT_ICON,
            "label": one_line(a.get("description") or a.get("type")) or "Sous-agent",
            "detail": step, "mono": code, "indent": indent, "inline": True,
            "tooltip": f"{a.get('type') or 'Agent'} · {agent_line(a)}"[:300],
            "actions": {"open": ("Voir dans le volet des sous-agents", open_agent)} if open_agent else {}}


def more_agents_spec(n, indent=False, open_panel=None):
    """Sous-agents au-delà de MAX_AGENTS, en une ligne."""
    return {"key": "agents-more", "kind": "agent", "icon": AGENT_ICON, "indent": indent, "inline": True,
            "label": f"… et {n} autre{'s' if n > 1 else ''} sous-agent{'s' if n > 1 else ''}",
            "actions": {"open": ("Volet des sous-agents", open_panel)} if open_panel else {}}


def workflow_spec(w, indent=False, open_panel=None):
    """Workflow en arrière-plan (commande « stat », ou agent_workflows) : son nom, une barre
    d'avancement, agents finis sur lancés et ceux qui tournent, depuis quand il tourne."""
    total, done = w.get("total") or 0, w.get("done") or 0
    start = w.get("start")
    running = [one_line(r.get("label")) for r in w.get("running") or ()]
    tip = [f"Workflow {one_line(w.get('name')) or ''}".strip(),
           f"{done} agent{'s' if done > 1 else ''} fini{'s' if done > 1 else ''} sur {total}" if total else ""]
    tip += [f"• {t}" for t in running[:10] if t]
    return {"key": f"workflow:{w.get('id') or w.get('name')}", "kind": "workflow", "icon": WORKFLOW_ICON,
            "label": one_line(w.get("name")) or "workflow", "detail": workflow_detail(w),
            "indent": indent, "inline": True, "fraction": done / total if total else 0.0,
            "start": start if isinstance(start, (int, float)) and not isinstance(start, bool) else None,
            "tooltip": "\n".join(t for t in tip if t),
            "actions": {"open": ("Volet des sous-agents", open_panel)} if open_panel else {}}


def waiting_for(flows=0, agents=0):
    """Ligne de Claude quand il a fini son tour et attend ce qui tourne en arrière-plan :
    « Claude attend un workflow et 10 sous-agents » (None s'il n'attend ni l'un ni l'autre)."""
    parts = []
    if flows:
        parts.append("un workflow" if flows == 1 else f"{flows} workflows")
    if agents:
        parts.append("un sous-agent" if agents == 1 else f"{agents} sous-agents")
    return "Claude attend " + " et ".join(parts) if parts else None


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


def grouped_summary(kinds, todos=""):
    """Ligne de résumé de la partie repliable : « 1 monitor · 2 shells · tâches 3/7 »
    (kinds : genres des lignes repliées ; todos : résumé des tâches)."""
    parts = []
    for kind in GROUPED:
        n = sum(k == kind for k in kinds)
        if n:
            parts.append(f"{n} {kind}{'s' if n > 1 else ''}")
    if todos:
        parts.append(todos)
    text = " · ".join(parts)
    return text[:1].upper() + text[1:]


def on_toggle(button):
    """Clic sur la ligne de résumé : déplie ou replie les shells, monitors et tâches."""
    panel = button.get_ancestor(ActivityPanel)
    if panel is not None:
        panel.set_expanded(not panel.expanded)


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
    """Une ligne : icône (ou roue), libellé et durée, détail, deuxième détail, boutons.
    Sur une seule ligne (inline : sous-agents, workflows) : libellé, barre d'avancement,
    détail à côté, durée."""

    def __init__(self, key, inline=False):
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
        top = Gtk.Box(spacing=8 if inline else 6)
        line = BASELINE if inline else Gtk.Align.START
        if inline:  # le libellé laisse de la place à l'étape
            self.label = Gtk.Label(xalign=0, max_width_chars=45, ellipsize=Pango.EllipsizeMode.END,
                                   valign=line, css_classes=["act-label"])
        else:
            self.label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                                   ellipsize=Pango.EllipsizeMode.END, css_classes=["act-label"])
        self.time = Gtk.Label(xalign=1, hexpand=not inline, valign=line, css_classes=["act-time"])
        self.detail = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False,
                                hexpand=inline, valign=line, css_classes=["act-detail"])
        self.bar = Gtk.ProgressBar(visible=False, valign=Gtk.Align.CENTER) if inline else None
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False,
                             css_classes=["act-sub"])
        top.append(self.label)
        if inline:
            top.append(self.bar)
            top.append(self.detail)
        top.append(self.time)
        texts.append(top)
        if not inline:
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
        if self.bar is not None:
            self.bar.set_visible("fraction" in spec)
            fraction = min(1.0, max(0.0, float(spec.get("fraction") or 0.0)))
            if abs(self.bar.get_fraction() - fraction) > 1e-6:
                self.bar.set_fraction(fraction)
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
            row = ActivityRow(spec["key"], bool(spec.get("inline")))
            box.insert_child_after(row, prev)
        elif row.get_prev_sibling() is not prev:
            box.reorder_child_after(row, prev)
        row.update(spec)
        prev = row
    for row in old.values():
        box.remove(row)


class ActivityPanel(Gtk.Box):
    """La liste : Claude, demande, sous-agents, puis la ligne de résumé qui replie les shells
    et monitors (grouped) et les tâches de Claude (todos, volet de todos.py), et enfin les
    messages en file."""

    def __init__(self, todos):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["activity"],
                         valign=Gtk.Align.START)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.grouped = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.queued = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.todos = todos
        self.expanded = False  # replié au lancement (choix de l'utilisateur)
        self.kinds = []        # genres des lignes repliées, pour le résumé
        self.toggle = Gtk.Button(visible=False, hexpand=True)
        for css in ("flat", "act-toggle"):
            self.toggle.add_css_class(css)
        inner = Gtk.Box(spacing=8)
        icon = Gtk.Box(css_classes=["act-icon"], valign=Gtk.Align.CENTER)
        self.chevron = Gtk.Image()
        icon.append(self.chevron)
        inner.append(icon)
        self.summary = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["act-summary"])
        inner.append(self.summary)
        self.toggle.set_child(inner)
        self.toggle.connect("clicked", on_toggle)
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        group.append(self.grouped)
        group.append(todos)
        self.group = Gtk.Revealer(child=group, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                  transition_duration=150)
        for w in (self.rows, self.toggle, self.group, self.queued):
            self.append(w)
        self.timer = 0
        self.set_expanded(False)

    def set_expanded(self, expanded):
        self.expanded = expanded
        self.group.set_reveal_child(expanded)
        self.chevron.set_from_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")
        self.toggle.set_tooltip_text("Replier les shells, monitors et tâches" if expanded
                                     else "Déplier les shells, monitors et tâches")

    def show(self, specs=None, queued=None):
        """Remplace les lignes d'activité (specs : les shells et monitors vont dans la partie
        repliable) et/ou celles des messages en file."""
        if specs is not None:
            grouped = [s for s in specs if s["kind"] in GROUPED]
            sync_rows(self.rows, [s for s in specs if s["kind"] not in GROUPED])
            sync_rows(self.grouped, grouped)
            self.kinds = [s["kind"] for s in grouped]
            self.update_summary()
        if queued is not None:
            sync_rows(self.queued, queued)
        timed = self.tick()
        if timed and not self.timer:
            self.timer = GLib.timeout_add(1000, self.on_timer)
        elif not timed and self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def show_todos(self, todos):
        """Tâches de Claude (TodoList, ou None) : volet de todos.py et ligne de résumé."""
        self.todos.show(todos)
        self.update_summary()

    def update_summary(self):
        """La ligne de résumé n'apparaît que s'il y a quelque chose à replier."""
        text = grouped_summary(self.kinds, self.todos.summary())
        set_text(self.summary, text)
        self.toggle.set_visible(bool(text))

    def tick(self):
        timed = False
        for box in (self.rows, self.grouped):
            child = box.get_first_child()
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
                or self.toggle.get_visible())

    def labels(self):
        """Textes des lignes (libellé · détail), repliées comprises, pour les tests."""
        out = []
        for box in (self.rows, self.grouped, self.queued):
            child = box.get_first_child()
            while child is not None:
                out.append(" · ".join(t for t in (child.label.get_text(), child.detail.get_text()) if t))
                child = child.get_next_sibling()
        return out
