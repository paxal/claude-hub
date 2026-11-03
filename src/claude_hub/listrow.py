"""Lignes de la liste des conversations et séparateurs de jour.

Une ligne : icône d'état, titre, puis hôte, note, dossier et tags ; à droite l'heure du
dernier message, ou « en cours », « à valider ». Au survol, l'heure laisse la place
aux boutons tags, épingle et archive. Les lignes sont regroupées par jour
(« Aujourd'hui », « Hier », « Samedi 31 octobre »…), d'où l'heure seule à droite ;
les épinglées viennent avant, sous « Épinglées ».
"""
import datetime
import time

from .gtk import Gtk, Pango
from .tagui import TAG_ICON, tag_chips
from .widgets import host_chip, weak_handler

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
# États de claude (fichier de session, API cloud) qui attendent l'utilisateur :
# question, plan ou autorisation à valider.
ACTION_STATES = ("requires_action", "waiting")
BUSY_ICON = "claude-hub-busy-symbolic"  # roue fixe (« en cours »)


def local_date(ts):
    return datetime.date.fromtimestamp(ts or 0)


def day_label(ts, now=None):
    """« Aujourd'hui », « Hier », « Samedi 31 octobre » (avec l'année si ce n'est pas celle-ci)."""
    date, today = local_date(ts), local_date(time.time() if now is None else now)
    delta = (today - date).days
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Hier"
    text = f"{DAYS[date.weekday()]} {date.day} {MONTHS[date.month - 1]}"
    if date.year != today.year:
        text += f" {date.year}"
    return text.capitalize()


def time_text(ts):
    return time.strftime("%H:%M", time.localtime(ts or 0))


def short_date(ts, now=None):
    """Heure si c'est aujourd'hui, sinon « 31/10 » (cartes de l'accueil)."""
    if local_date(ts) == local_date(time.time() if now is None else now):
        return time_text(ts)
    return time.strftime("%d/%m", time.localtime(ts or 0))


def status_kind(status):
    """État de la liste (cf. Hub.list_state) -> busy, action, unread, idle, draft,
    other (état inconnu de claude, affiché tel quel) ou None."""
    if status in ACTION_STATES:
        return "action"
    if status in ("busy", "unread", "idle", "draft"):
        return status
    return "other" if status else None


STATE_ICONS = {"busy": BUSY_ICON, "action": "dialog-warning-symbolic", "idle": "object-select-symbolic",
               "draft": "document-edit-symbolic"}


class StateIcon(Gtk.Box):
    """Icône d'état d'une ligne : pastille bleue (non lue), roue, triangle, coche, crayon.
    La roue est fixe : un Gtk.Spinner animé fait redessiner la fenêtre en continu
    (voir AGENTS.md, Animations)."""

    def __init__(self):
        # homogène : l'icône visible occupe toute la colonne et y est centrée (un hexpand sur
        # elle remonterait à la ligne, qui partagerait sa largeur avec le titre)
        super().__init__(valign=Gtk.Align.START, homogeneous=True, hexpand=False)
        self.add_css_class("row-state")
        self.image = Gtk.Image(visible=False, halign=Gtk.Align.CENTER)
        self.dot = Gtk.Box(css_classes=["dot"], visible=False, halign=Gtk.Align.CENTER,
                           valign=Gtk.Align.CENTER)
        self.append(self.image)
        self.append(self.dot)

    def show(self, kind):
        self.dot.set_visible(kind == "unread")
        icon = STATE_ICONS.get(kind)
        self.image.set_visible(icon is not None)
        if icon:
            self.image.set_from_icon_name(icon)
            self.image.set_css_classes([f"st-{kind}"])


PIN_ICON = "claude-hub-pin-symbolic"
NOTE_ICON = "claude-hub-note-symbolic"


class SessionRow(Gtk.ListBoxRow):
    """Ligne d'une conversation. on_tags(ligne), on_pin(ligne) et on_archive(ligne) :
    boutons du survol (absents pour une nouvelle conversation pas encore envoyée).
    note : début de la note de la conversation (icône, texte en info-bulle)."""

    def __init__(self, s, host, title, cwd, *, show_host, tags, on_tag, missing=False, archived=False,
                 source=None, snippet=None, on_tags=None, on_archive=None, pinned=False, on_pin=None,
                 note=None):
        super().__init__()
        self.add_css_class("session")  # en plus de « activatable »
        self.session, self.host, self.cwd_text, self.missing = s, host, cwd, missing
        self.pinned, self.on_pin, self.pin_btn = pinned, on_pin, None
        self.status = self.kind = None
        line = Gtk.Box(spacing=10)
        self.state = StateIcon()
        line.append(self.state)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.title = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                               css_classes=["row-title"])
        main.append(self.title)
        meta = Gtk.Box(spacing=6, css_classes=["row-meta"])
        if show_host:
            meta.append(host_chip(host))
        if source:
            meta.append(Gtk.Label(label=source[0], css_classes=["badge"] + source[1:]))
        self.note = Gtk.Image(icon_name=NOTE_ICON, css_classes=["row-note"], valign=Gtk.Align.CENTER)
        meta.append(self.note)
        self.set_note(note)
        folder = ("dossier supprimé · " + cwd) if missing else cwd
        meta.append(Gtk.Label(label=folder, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["row-folder"] + (["missing"] if missing else []),
                              tooltip_text=folder))
        self.draft = Gtk.Label(label="brouillon", css_classes=["row-draft"], visible=False)
        meta.append(self.draft)
        self.tag_box = Gtk.Box(spacing=4)
        tag_chips(self.tag_box, tags, on_tag)
        meta.append(self.tag_box)
        main.append(meta)
        if snippet:
            main.append(Gtk.Label(use_markup=True, label=snippet, xalign=0, css_classes=["snippet"],
                                  ellipsize=Pango.EllipsizeMode.END))
        line.append(main)

        self.right = Gtk.Stack(valign=Gtk.Align.START, hhomogeneous=False, vhomogeneous=False,
                               interpolate_size=False)
        self.time = Gtk.Label(css_classes=["row-time"], xalign=1)
        self.right.add_named(self.time, "time")
        self.on_tags, self.on_archive, self.archive_btn = on_tags, on_archive, None
        if on_tags:  # boutons créés au premier survol (des centaines de lignes)
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", weak_handler(self, lambda row, *_: row.show_actions(True)))
            hover.connect("leave", weak_handler(self, lambda row, *_: row.show_actions(False)))
            self.add_controller(hover)
        line.append(self.right)
        self.set_child(line)
        self.set_archived(archived)
        self.paint(None)

    def show_actions(self, shown):
        """Survol : les boutons tags et archive à la place de l'heure."""
        if shown and self.archive_btn is None:
            actions = Gtk.Box(spacing=2, css_classes=["row-actions"])
            tag_btn = Gtk.Button(icon_name=TAG_ICON, tooltip_text="Tags", css_classes=["flat", "row-btn"])
            # menu accroché à la ligne : le bouton disparaît quand la souris la quitte
            tag_btn.connect("clicked", weak_handler(self, lambda row, _b: row.on_tags(row)))
            actions.append(tag_btn)
            if self.on_pin:
                self.pin_btn = Gtk.ToggleButton(icon_name=PIN_ICON, css_classes=["flat", "row-btn"])
                self.pin_btn.connect("clicked", weak_handler(self, lambda row, _b: row.on_pin(row)))
                actions.append(self.pin_btn)
                self.set_pinned(self.pinned)
            self.archive_btn = Gtk.Button(css_classes=["flat", "row-btn"])
            self.archive_btn.connect("clicked", weak_handler(self, lambda row, _b: row.on_archive(row)))
            actions.append(self.archive_btn)
            self.right.add_named(actions, "actions")
            self.set_archived(self.has_css_class("archived"))
        self.right.set_visible_child_name("actions" if shown and self.archive_btn else "time")

    def set_note(self, note):
        """Icône de la note (None : pas de note), son début en info-bulle."""
        self.note.set_visible(bool(note))
        self.note.set_tooltip_text(f"Note :\n{note}" if note else None)

    def set_pinned(self, pinned):
        """Épinglée ou non ; le tri et les séparateurs de la liste sont à refaire."""
        self.pinned = pinned
        if self.pin_btn:
            self.pin_btn.set_active(pinned)  # émet « toggled », pas « clicked » : pas de boucle
            self.pin_btn.set_tooltip_text("Désépingler" if pinned else "Épingler en tête de liste")

    def set_archived(self, archived):
        (self.add_css_class if archived else self.remove_css_class)("archived")
        if self.archive_btn:
            self.archive_btn.set_icon_name("archive-extract-symbolic" if archived
                                           else "archive-insert-symbolic")
            self.archive_btn.set_tooltip_text("Désarchiver" if archived else "Archiver")

    def paint(self, status):
        """Affiche l'état (cf. Hub.list_state) : icône, graisse du titre, texte de droite."""
        self.status, kind = status, status_kind(status)
        self.kind = kind
        self.state.show(kind)
        (self.add_css_class if kind in ("unread", "action") else self.remove_css_class)("strong")
        self.draft.set_visible(kind == "draft")
        self.update_time()

    def update_time(self):
        kind = self.kind
        text = {"busy": "en cours", "action": "à valider"}.get(kind) or (
            self.status if kind == "other" else time_text(self.session.get("mtime")))
        self.time.set_text(text)
        self.time.set_css_classes(["row-time"] + ([kind] if kind in ("busy", "action", "unread") else []))


def elapsed_text(seconds):
    """Durée d'une étape : « 14 s », « 1 min 02 », « 1 h 05 »."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d}"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"
