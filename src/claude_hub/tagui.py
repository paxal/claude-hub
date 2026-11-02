"""Widgets des tags : pastilles, éditeur et menus de filtre (tags, dossiers)."""
from .gtk import Gtk, Pango
from .tags import normalize

TAG_ICON = "claude-hub-tag-symbolic"


def tag_chips(box, tags, on_click):
    """Remplit box de pastilles « #tag » ; un clic appelle on_click(tag)."""
    while (child := box.get_first_child()):
        box.remove(child)
    for t in tags:
        chip = Gtk.Button(label=f"#{t}", css_classes=["tagchip", "flat"],
                          tooltip_text=f"Filtrer sur #{t}")
        chip.connect("clicked", lambda _b, t=t: on_click(t))
        box.append(chip)
    box.set_visible(bool(tags))


class TagPopover(Gtk.Popover):
    """Édite les tags d'une conversation ; chaque changement appelle
    on_change(tags) qui renvoie la liste enregistrée (normalisée)."""

    def __init__(self, tags, known, on_change):
        super().__init__()
        self.tags, self.known, self.on_change = list(tags), known, on_change
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=280)
        self.set_child(box)

        self.chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                 column_spacing=4, row_spacing=4)
        box.append(self.chips)
        self.entry = Gtk.Entry(placeholder_text="tag, ou dossier/tag — Entrée pour ajouter")
        self.entry.connect("activate", lambda e: self.add(e.get_text()))
        self.entry.connect("changed", lambda _e: self.fill_suggestions())
        box.append(self.entry)
        self.suggest = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.suggest.connect("row-activated", lambda _l, r: self.add(r.tag))
        self.suggest_scroll = Gtk.ScrolledWindow(child=self.suggest, max_content_height=220,
                                                 propagate_natural_height=True,
                                                 hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(self.suggest_scroll)
        self.fill_chips()
        self.fill_suggestions()
        self.connect("map", lambda _p: self.entry.grab_focus())

    def add(self, text):
        new = [normalize(t) for t in text.split(",")]
        new = [t for t in new if t and t not in self.tags]
        self.entry.set_text("")
        if new:
            self.save(self.tags + new)

    def remove_tag(self, tag):
        self.save([t for t in self.tags if t != tag])

    def save(self, tags):
        self.tags = self.on_change(tags)
        self.fill_chips()
        self.fill_suggestions()

    def fill_chips(self):
        self.chips.remove_all()
        for t in self.tags:
            chip = Gtk.Box(spacing=2, css_classes=["tagchip"])
            chip.append(Gtk.Label(label=f"#{t}", ellipsize=Pango.EllipsizeMode.MIDDLE))
            rm = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "chip-close"],
                            tooltip_text="Retirer")
            rm.connect("clicked", lambda _b, t=t: self.remove_tag(t))
            chip.append(rm)
            self.chips.append(chip)
        self.chips.set_visible(bool(self.tags))

    def fill_suggestions(self):
        self.suggest.remove_all()
        typed = normalize(self.entry.get_text())
        shown = 0
        for t in self.known:
            if t in self.tags or t.endswith("/") or (typed and typed not in t):
                continue
            row = Gtk.ListBoxRow(child=Gtk.Label(label=f"#{t}", xalign=0, margin_start=4))
            row.tag = t
            self.suggest.append(row)
            shown += 1
            if shown >= 30:
                break
        self.suggest_scroll.set_visible(bool(shown))


def filter_popover(title, items, active, on_pick, empty):
    """Popover listant items = [(texte affiché, jeton, nombre)] ; un clic appelle
    on_pick(jeton). Le jeton actif est coché."""
    pop = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, width_request=300)
    box.append(Gtk.Label(label=title, xalign=0, css_classes=["sid"], margin_start=4))
    lb = Gtk.ListBox(css_classes=["navigation-sidebar"])
    for text, token, n in items:
        line = Gtk.Box(spacing=8, margin_start=4, margin_end=4)
        line.append(Gtk.Image(icon_name="object-select-symbolic", opacity=1 if token in active else 0))
        line.append(Gtk.Label(label=text, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
        line.append(Gtk.Label(label=str(n), css_classes=["sid"]))
        row = Gtk.ListBoxRow(child=line)
        row.token = token
        lb.append(row)
    if not items:
        box.append(Gtk.Label(label=empty, css_classes=["notice"], wrap=True))

    def picked(_lb, row):
        pop.popdown()
        on_pick(row.token)
    lb.connect("row-activated", picked)
    box.append(Gtk.ScrolledWindow(child=lb, max_content_height=420, propagate_natural_height=True,
                                  hscrollbar_policy=Gtk.PolicyType.NEVER))
    pop.set_child(box)
    return pop
