"""Accueil (aucune conversation affichée) : ce qui attend l'utilisateur, ce qui tourne,
et le choix du dossier d'une nouvelle conversation.

« À traiter » : une carte par conversation qui attend une action (plan à valider,
question, autorisation) ou dont la réponse n'a pas été lue ; « En cours » : les
conversations où Claude travaille. La fenêtre fournit les entrées et les actions.
"""
import re

from .gtk import Gtk, Pango
from .listrow import BUSY_ICON, short_date
from .widgets import Clamp, count_badge, host_chip, menu_popover, section_title, set_count

KINDS = {  # type de carte -> (icône, libellé, classe)
    "plan": ("dialog-warning-symbolic", "Plan à valider", "action"),
    "question": ("dialog-warning-symbolic", "Question de Claude", "action"),
    "permission": ("dialog-warning-symbolic", "Autorisation demandée", "action"),
    "action": ("dialog-warning-symbolic", "Action requise", "action"),
    "unread": (None, "Nouvelle réponse", "unread"),
}


def plan_digest(plan):
    """(titre, résumé) d'un plan en Markdown : premier titre, puis le début du texte."""
    lines = [line.strip() for line in (plan or "").splitlines() if line.strip()]
    title = next((line.lstrip("#").strip() for line in lines if line.startswith("#")),
                 lines[0] if lines else "Plan")
    body = [re.sub(r"^([-*+]|\d+[.)])\s+", "", line) for line in lines
            if not line.startswith("#") and line.lstrip("#").strip() != title]
    summary = plain(" · ".join(body[:4]))
    return plain(title)[:200], summary[:400]


def plain(text):
    """Markdown en ligne -> texte : sans **, __, ` ni *mot* (les _ des noms restent)."""
    text = re.sub(r"\*\*|__|`", "", text)
    return re.sub(r"(?<!\w)([*_])(\S|\S.*?\S)\1(?!\w)", r"\2", text)


def meta_line(host, cwd):
    line = Gtk.Box(spacing=6)
    line.append(host_chip(host))
    line.append(Gtk.Label(label=cwd or "", xalign=0, css_classes=["mono", "dim"],
                          ellipsize=Pango.EllipsizeMode.MIDDLE))
    return line


def button(label, on_click, *css):
    btn = Gtk.Button(label=label, css_classes=list(css))
    btn.connect("clicked", lambda _b: on_click())
    return btn


class HomePage(Gtk.ScrolledWindow):
    """Contenu de l'accueil ; actions : objet qui fournit open_sid(sid), mark_read(sid),
    plan_run_sid(sid, rid), plan_refuse_sid(sid, rid)."""

    def __init__(self, actions):
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.actions = actions
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["home"])
        self.set_child(Clamp(self.box, 780, hexpand=True))
        self.shown = None

    def update(self, todo, busy):
        """todo : [{"kind", "sid", "host", "title", "cwd", "ts", "text", "plan", "rid"}] ;
        busy : [{"sid", "host", "title", "cwd", "text"}]."""
        key = repr((todo, busy))
        if key == self.shown:
            return
        self.shown = key
        while (child := self.box.get_first_child()):
            self.box.remove(child)
        if todo:
            self.box.append(self.heading("À traiter", len(todo), "todo"))
            for entry in todo:
                self.box.append(self.card(entry))
        if busy:
            self.box.append(self.heading("En cours", len(busy), "busy", top=12 if todo else 0))
            for entry in busy:
                self.box.append(self.busy_row(entry))
        if not todo and not busy:
            self.box.append(Gtk.Label(
                label="Rien à traiter pour l'instant : aucune réponse non lue, aucune demande "
                      "de Claude en attente.\nChoisis une conversation à gauche, ou démarre-en "
                      "une nouvelle ci-dessous.",
                wrap=True, xalign=0, css_classes=["home-empty"]))

    @staticmethod
    def heading(text, n, css, top=0):
        box = Gtk.Box(spacing=8, margin_top=top)
        box.append(Gtk.Label(label=text, css_classes=["home-title"]))
        badge = count_badge(css)
        set_count(badge, n)
        box.append(badge)
        return box

    def card(self, e):
        icon, label, css = KINDS[e["kind"]]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["home-card"])
        head = Gtk.Box(spacing=8)
        if icon:
            head.append(Gtk.Image(icon_name=icon, css_classes=["st-action"]))
        else:
            head.append(Gtk.Box(css_classes=["dot"], valign=Gtk.Align.CENTER))
        head.append(Gtk.Label(label=label, css_classes=["card-kind", css]))
        head.append(Gtk.Label(label=e["title"], xalign=0, hexpand=True, css_classes=["home-card-title"],
                              ellipsize=Pango.EllipsizeMode.END, tooltip_text=e["title"]))
        head.append(Gtk.Label(label=short_date(e["ts"]), css_classes=["dim"]))
        card.append(head)
        card.append(meta_line(e["host"], e["cwd"]))
        sid, rid = e["sid"], e.get("rid")
        if e.get("plan"):
            title, summary = e["plan"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["plan-box"])
            box.append(Gtk.Label(label=title, xalign=0, wrap=True, css_classes=["card-title"]))
            if summary:
                box.append(Gtk.Label(label=summary, xalign=0, wrap=True, lines=2, css_classes=["excerpt"],
                                     ellipsize=Pango.EllipsizeMode.END))
            card.append(box)
        elif e.get("text"):
            card.append(Gtk.Label(label=" ".join(e["text"].split()), xalign=0, wrap=True, lines=2,
                                  ellipsize=Pango.EllipsizeMode.END, css_classes=["excerpt"]))
        buttons = Gtk.Box(spacing=8)
        a = self.actions
        if e["kind"] == "plan" and rid:
            buttons.append(button("Lancer", lambda: a.plan_run_sid(sid, rid), "suggested-action"))
            buttons.append(button("Continuer à planifier", lambda: a.plan_refuse_sid(sid, rid)))
            buttons.append(button("Ouvrir la conversation", lambda: a.open_sid(sid), "link-btn"))
        elif e["kind"] == "unread":
            buttons.append(button("Ouvrir", lambda: a.open_sid(sid)))
            buttons.append(button("Marquer comme lue", lambda: a.mark_read(sid), "link-btn"))
        else:
            buttons.append(button("Ouvrir la conversation", lambda: a.open_sid(sid)))
        card.append(buttons)
        return card

    def busy_row(self, e):
        line = Gtk.Box(spacing=10)
        line.append(Gtk.Image(icon_name=BUSY_ICON, css_classes=["st-busy"], valign=Gtk.Align.CENTER))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.append(Gtk.Label(label=e["title"], xalign=0, css_classes=["card-title"],
                               ellipsize=Pango.EllipsizeMode.END))
        texts.append(meta_line(e["host"], e["cwd"]))
        line.append(texts)
        line.append(Gtk.Label(label=e["text"], css_classes=["card-kind", "busy"], valign=Gtk.Align.CENTER))
        btn = Gtk.Button(child=line, css_classes=["home-card"])
        btn.connect("clicked", lambda _b: self.actions.open_sid(e["sid"]))
        return btn


class DirButton(Gtk.MenuButton):
    """Dossier d'une nouvelle conversation : chemin saisi, dossiers récents de l'hôte, ou
    « Parcourir… ». recent() -> (titre de la section, [chemins]) ; browse() ouvre le
    sélecteur de dossier ; on_change(chemin) après chaque choix."""

    def __init__(self, recent, browse, on_change=None):
        super().__init__(tooltip_text="Dossier de la conversation")
        self.add_css_class("chip")
        self.add_css_class("path")
        self.recent, self.browse, self.on_change = recent, browse, on_change
        self.value = "~"
        box = Gtk.Box(spacing=5)
        box.append(Gtk.Image(icon_name="folder-symbolic", pixel_size=14))
        self.label = Gtk.Label(label=self.value, ellipsize=Pango.EllipsizeMode.START, max_width_chars=34)
        box.append(self.label)
        box.append(Gtk.Image(icon_name="pan-down-symbolic", css_classes=["chevron"]))
        self.set_child(box)

        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, width_request=320)
        self.entry = Gtk.SearchEntry(placeholder_text="Chemin ou filtre…")
        self.entry.connect("search-changed", lambda _e: self.list.invalidate_filter())
        self.entry.connect("activate", self.on_activate)
        pop_box.append(self.entry)
        self.section = section_title("Récents")
        pop_box.append(self.section)
        self.list = Gtk.ListBox(css_classes=["choices"], selection_mode=Gtk.SelectionMode.NONE)
        self.list.set_filter_func(self.keep_row)
        self.list.connect("row-activated", lambda _l, row: self.choose(row.path))
        pop_box.append(Gtk.ScrolledWindow(child=self.list, max_content_height=300,
                                          propagate_natural_height=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER))
        pop_box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        browse_btn = Gtk.Button(css_classes=["flat"])
        inner = Gtk.Box(spacing=10)
        inner.append(Gtk.Image(icon_name="folder-open-symbolic"))
        inner.append(Gtk.Label(label="Parcourir…"))
        browse_btn.set_child(inner)
        browse_btn.connect("clicked", lambda _b: (self.popdown(), self.browse()))
        pop_box.append(browse_btn)
        pop = menu_popover(pop_box)
        pop.connect("show", lambda _p: self.fill())
        self.set_popover(pop)

    def fill(self):
        self.entry.set_text("")
        title, dirs = self.recent()
        self.section.set_text(title.upper())
        self.list.remove_all()
        for path in dirs:
            row = Gtk.ListBoxRow(child=Gtk.Label(label=path, xalign=0, css_classes=["mono"],
                                                 ellipsize=Pango.EllipsizeMode.MIDDLE))
            row.path = path
            self.list.append(row)
        self.entry.grab_focus()

    def keep_row(self, row):
        typed = self.entry.get_text().strip().lower()
        return not typed or typed in row.path.lower()

    def on_activate(self, entry):
        typed = entry.get_text().strip()
        if typed.startswith(("/", "~")):
            return self.choose(typed)
        row = self.list.get_first_child()
        while row is not None and not (row.get_child_visible() and getattr(row, "path", None)):
            row = row.get_next_sibling()
        if row is not None:
            self.choose(row.path)

    def choose(self, path):
        self.popdown()
        self.set_value(path)
        if self.on_change:
            self.on_change(path)

    def set_value(self, path):
        self.value = path or "~"
        self.label.set_text(self.value)
        self.set_tooltip_text(f"Dossier de la conversation : {self.value}")
