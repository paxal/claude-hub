"""Widgets communs : colonne de largeur limitée, bouton à choix, pastille d'hôte, texte indicatif."""
from .gtk import Gio, Graphene, Gsk, Gtk, Pango
from .hosts import CLOUD


def host_icon(host):
    return {"local": "computer-symbolic", CLOUD: "claude-hub-cloud-symbolic"}.get(
        host, "network-server-symbolic")


def short_host(host):
    """user@cerberus.stockly.tech -> cerberus (pastilles de la liste et de l'en-tête)."""
    return host.rsplit("@", 1)[-1].split(".")[0] or host


def host_kind(host):
    return "ce PC" if host == "local" else "cloud" if host == CLOUD else "SSH"


def themed_icon(*names):
    """Icône prise dans le premier nom connu du thème (Breeze n'a pas toutes celles d'Adwaita)."""
    return Gtk.Image(gicon=Gio.ThemedIcon.new_from_names(list(names)))


def host_chip(host):
    """Pastille « icône + nom court » de l'hôte d'une conversation."""
    chip = Gtk.Box(spacing=3, css_classes=["hostchip"], valign=Gtk.Align.CENTER,
                   tooltip_text=f"Sur {host}")
    chip.append(Gtk.Image(icon_name=host_icon(host), pixel_size=12))
    chip.append(Gtk.Label(label=short_host(host), ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=14))
    return chip


def weak_handler(obj, fn):
    """Gestionnaire de signal qui appelle fn(obj, *arguments du signal) sans garder obj en
    vie (et ne fait rien si obj a été libéré).

    À utiliser pour un signal d'un enfant ou d'un contrôleur de obj : self.methode, ou un
    lambda qui utilise self, formerait un cycle obj → enfant → gestionnaire → obj qui passe
    par GTK. Le ramasse-miettes de Python ne le voit pas, et obj n'est jamais libéré une
    fois retiré de l'affichage (voir AGENTS.md). Une référence faible de Python ne convient
    pas : PyGObject libère et recrée l'objet Python d'un widget toujours affiché ; celle de
    GObject suit le widget lui-même.
    """
    ref = obj.weak_ref()

    def handler(*args):
        target = ref()
        return None if target is None else fn(target, *args)
    return handler


def menu_popover(child):
    """Popover de menu sans flèche. (Sa classe « background » doit rester : passer
    css_classes au constructeur la retirerait, et le menu n'aurait plus de fond.)"""
    pop = Gtk.Popover(child=child, has_arrow=False)
    pop.add_css_class("menu-pop")
    return pop


def count_badge(css):
    """Pastille de nombre (filtres de la liste, titres de l'accueil) ; cachée à zéro."""
    return Gtk.Label(css_classes=["count", css], visible=False, valign=Gtk.Align.CENTER)


def set_count(badge, n):
    badge.set_text(str(n))
    badge.set_visible(n > 0)


def section_title(text):
    """Petit titre de section en capitales (menus, séparateurs de jour)."""
    return Gtk.Label(label=text.upper(), xalign=0, css_classes=["section-title"])


class Clamp(Gtk.Widget):
    """Centre son enfant en limitant sa largeur : colonne de lecture du fil et de la saisie.

    extra() : largeur que l'enfant peut prendre en plus, à droite de la colonne (la
    liste d'activité à côté de la saisie) ; la colonne reste alignée sur celle du fil
    tant que la marge de droite suffit, et se décale vers la gauche sinon."""

    def __init__(self, child, maximum=780, extra=None, **kw):
        super().__init__(**kw)
        self.maximum, self.extra = maximum, extra
        self.child = child
        child.set_parent(self)

    def widest(self):
        return self.maximum + (self.extra() if self.extra else 0)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL and for_size >= 0:
            # jamais sous la largeur minimale de l'enfant : il serait alloué plus large
            # (do_size_allocate) et sa hauteur ne correspondrait plus
            mn = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
            for_size = min(for_size, max(self.widest(), mn))
        mn, nat, _b, _n = self.child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            nat = max(mn, min(nat, self.widest()))
        return mn, nat, -1, -1

    def do_size_allocate(self, width, height, _baseline):
        mn = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        w = min(width, max(mn, self.widest()))
        # colonne centrée comme celle du fil, l'éventuel surplus à droite, dans la marge
        x = max(0, min((width - self.maximum) // 2, width - w))
        self.child.allocate(w, height, -1, Gsk.Transform().translate(Graphene.Point().init(x, 0)))

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None


class WrapBox(Gtk.Widget):
    """Enfants côte à côte, renvoyés à la ligne quand la largeur manque (pastilles). Un
    Gtk.FlowBox les alignerait en colonnes, donc les étirerait dès la deuxième ligne."""

    def __init__(self, spacing=4, row_spacing=4, **kw):
        super().__init__(**kw)
        self.spacing, self.row_spacing = spacing, row_spacing

    def append(self, child):
        child.set_parent(self)

    def remove_all(self):
        while (child := self.get_first_child()):
            child.unparent()

    def shown(self):
        out, child = [], self.get_first_child()
        while child:
            if child.get_visible():
                out.append(child)
            child = child.get_next_sibling()
        return out

    def rows(self, width):
        """[[(enfant, largeur, hauteur)]] : lignes remplies de gauche à droite."""
        rows, x = [[]], 0
        for child in self.shown():
            w = min(child.measure(Gtk.Orientation.HORIZONTAL, -1)[1], max(width, 0))
            w = max(w, child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
            if rows[-1] and x + w > width:
                rows.append([])
                x = 0
            h = child.measure(Gtk.Orientation.VERTICAL, w)[1]
            rows[-1].append((child, w, h))
            x += w + self.spacing
        return [r for r in rows if r]

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        kids = self.shown()
        if orientation == Gtk.Orientation.HORIZONTAL:
            sizes = [c.measure(orientation, -1) for c in kids]
            mn = max((s[0] for s in sizes), default=0)
            nat = sum(s[1] for s in sizes) + self.spacing * max(len(kids) - 1, 0)
            return mn, nat, -1, -1
        width = for_size if for_size >= 0 else self.do_measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        rows = self.rows(width)
        h = sum(max(x[2] for x in r) for r in rows) + self.row_spacing * max(len(rows) - 1, 0)
        return h, h, -1, -1

    def do_size_allocate(self, width, _height, _baseline):
        y = 0
        for row in self.rows(width):
            rh, x = max(h for _c, _w, h in row), 0
            for child, w, _h in row:
                child.allocate(w, rh, -1, Gsk.Transform().translate(Graphene.Point().init(x, y)))
                x += w + self.spacing
            y += rh + self.row_spacing

    def do_dispose(self):
        self.remove_all()


class ChoiceButton(Gtk.MenuButton):
    """Bouton « libellé ▾ » dont le menu liste les choix (coche, libellé, aide éventuelle).

    short(i, libellé) donne le texte du bouton (par défaut le libellé), icons(i) une
    icône à côté ; on_change(i) est appelé à chaque changement du choix, comme le
    signal notify::selected d'un Gtk.DropDown (choix de l'utilisateur ou du programme).
    """

    def __init__(self, labels, selected=0, *, helps=None, short=None, icons=None, tooltip=None,
                 on_change=None, max_chars=26):
        super().__init__(tooltip_text=tooltip)
        self.add_css_class("chip")  # en plus de « popup »
        self.labels, self.helps = list(labels), helps
        self.short, self.icons, self.on_change = short, icons, on_change
        self.selected = selected
        box = Gtk.Box(spacing=5)
        self.icon = Gtk.Image(visible=icons is not None, pixel_size=14)
        self.text = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=max_chars)
        box.append(self.icon)
        box.append(self.text)
        box.append(Gtk.Image(icon_name="pan-down-symbolic", css_classes=["chevron"]))
        self.set_child(box)
        self.list = Gtk.ListBox(css_classes=["choices"], selection_mode=Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", weak_handler(self, ChoiceButton.on_row))
        self.set_popover(menu_popover(self.list))
        self.fill()

    def fill(self):
        self.list.remove_all()
        self.rows = []
        for i, label in enumerate(self.labels):
            line = Gtk.Box(spacing=10)
            check = Gtk.Image(icon_name="object-select-symbolic", css_classes=["choice-check"],
                              valign=Gtk.Align.START)
            line.append(check)
            if self.icons:
                line.append(Gtk.Image(icon_name=self.icons(i), valign=Gtk.Align.START))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            name = Gtk.Label(label=label, xalign=0, css_classes=["choice-label"])
            texts.append(name)
            if self.helps and self.helps[i]:
                texts.append(Gtk.Label(label=self.helps[i], xalign=0, css_classes=["choice-help"]))
            line.append(texts)
            row = Gtk.ListBoxRow(child=line)
            row.index, row.check, row.name = i, check, name
            self.list.append(row)
            self.rows.append(row)
        self.refresh()

    def refresh(self):
        for row in self.rows:
            row.check.set_opacity(1 if row.index == self.selected else 0)
        label = self.labels[self.selected] if self.selected < len(self.labels) else ""
        text = self.short(self.selected, label) if self.short else label
        self.text.set_text(text)
        self.text.set_width_chars(min(len(text), 12))  # à l'étroit : « Modifications… », pas « Mo… »
        if self.icons:
            self.icon.set_from_icon_name(self.icons(self.selected))

    def on_row(self, _list, row):
        self.popdown()
        self.set_selected(row.index)

    def get_selected(self):
        return self.selected

    def set_selected(self, i):
        if i == self.selected or not 0 <= i < len(self.labels):
            return
        self.selected = i
        self.refresh()
        if self.on_change:
            self.on_change(i)

    def set_item_label(self, i, label):
        """Change le libellé d'un choix (versions des modèles, défauts de l'hôte)."""
        self.labels[i] = label
        self.rows[i].name.set_text(label)
        self.refresh()

    def set_labels(self, labels, selected=0):
        """Remplace tous les choix, sans appeler on_change."""
        self.labels, self.selected = list(labels), selected
        self.fill()


def with_placeholder(scroll, view, text):
    """Overlay de scroll (qui contient la saisie view) affichant text tant que la saisie
    est vide : renvoie (overlay, étiquette)."""
    hint = Gtk.Label(label=text, xalign=0, yalign=0, css_classes=["placeholder"],
                     halign=Gtk.Align.FILL, valign=Gtk.Align.START, can_target=False,
                     ellipsize=Pango.EllipsizeMode.END)
    overlay = Gtk.Overlay(child=scroll)
    overlay.add_overlay(hint)
    buf = view.get_buffer()
    buf.connect("changed", lambda b: hint.set_visible(b.get_char_count() == 0))
    return overlay, hint
