"""Volet « Notes » : la note Markdown de la conversation affichée.

Aperçu (Markdown mis en forme, chemins cliquables comme dans le fil) ou édition du
texte brut (crayon). La note s'enregistre toute seule, une seconde après la dernière
frappe, et aussitôt quand on passe à l'aperçu, à une autre conversation ou qu'on
ferme le volet ; une note vidée est supprimée. Stockage : notes.py (sur l'hôte de
la conversation).
"""
import time

from .gtk import GLib, Gtk, Pango
from .notes import NoteQueue, note_path, where_text
from .render import copy_button, render_md
from .widgets import with_placeholder

SAVE_DELAY = 1000  # ms après la dernière frappe


class NotesPanel(Gtk.Box):
    """on_saved(hôte, id, texte) : note enregistrée (index, en-tête, liste) ;
    on_error(hôte, id, erreur) : échec d'un enregistrement."""

    def __init__(self, on_close, on_saved, on_error):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["viewer"])
        self.host = self.sid = None
        self.on_saved, self.on_error = on_saved, on_error
        self.io = NoteQueue()
        self.gen = 0
        self.loaded = False   # note lue : modifiable (sinon on écraserait ce qu'on n'a pas lu)
        self.saved = None     # dernier texte lu ou enregistré (None : à enregistrer)
        self.timer = 0
        self.syncing = False  # texte ou bouton réglé par le programme, pas par l'utilisateur
        self.focus = False    # donner le focus à l'édition une fois la note lue

        head = Gtk.Box(spacing=4, margin_top=6, margin_start=12, margin_end=4, margin_bottom=4)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        names.append(Gtk.Label(label="Notes", xalign=0, css_classes=["card-title"]))
        self.where = Gtk.Label(xalign=0, css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END)
        names.append(self.where)
        head.append(names)
        self.edit_btn = Gtk.ToggleButton(icon_name="document-edit-symbolic", css_classes=["flat"],
                                         valign=Gtk.Align.CENTER, tooltip_text="Modifier la note")
        self.edit_btn.connect("toggled", lambda b: self.set_editing(b.get_active(), not self.syncing))
        head.append(self.edit_btn)
        self.copy_slot = Gtk.Box(valign=Gtk.Align.CENTER)
        head.append(self.copy_slot)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                           tooltip_text="Fermer", valign=Gtk.Align.CENTER)
        close.connect("clicked", lambda _b: on_close())
        head.append(close)
        self.append(head)
        self.status = Gtk.Label(xalign=0, wrap=True, css_classes=["notice"], visible=False)
        self.append(self.status)

        self.stack = Gtk.Stack(vexpand=True)
        self.preview = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.stack.add_named(self.preview, "view")
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True, top_margin=10,
                                 bottom_margin=10, left_margin=12, right_margin=12)
        self.text.add_css_class("note-input")  # en plus de « view »
        self.text.get_buffer().connect("changed", self.on_changed)
        scroll = Gtk.ScrolledWindow(child=self.text, hscrollbar_policy=Gtk.PolicyType.NEVER)
        field, self.hint = with_placeholder(scroll, self.text, "Notes en Markdown…")
        self.stack.add_named(field, "edit")
        self.append(self.stack)
        # « Enregistrée à 14:02 », « Enregistrement… », erreur
        self.state = Gtk.Label(xalign=0, css_classes=["sid"], margin_start=12, margin_end=8,
                               margin_top=4, margin_bottom=6, ellipsize=Pango.EllipsizeMode.END)
        self.append(self.state)

    def show(self, host, sid, focus=False):
        """Affiche la note de la conversation sid de host (lue sur son hôte si c'en est
        une autre que celle déjà affichée). focus : ouverte par l'utilisateur, l'édition
        prend le focus (sinon il reste à la saisie du message)."""
        if (host, sid) == (self.host, self.sid) and self.loaded:
            if focus and self.edit_btn.get_active():
                self.text.grab_focus()
            return
        if (host, sid) == (self.host, self.sid):  # lecture en cours ou échouée : on relit
            self.focus = self.focus or focus
            return self.reload()
        self.flush()
        self.focus = focus
        self.host, self.sid = host, sid
        where = where_text(host)
        self.where.set_text(f"Rangée {where}")
        self.where.set_tooltip_text(note_path(host, sid))
        self.hint.set_text(f"Notes en Markdown, enregistrées {where}…")
        self.reload()

    def reload(self):
        self.gen += 1
        gen, host, sid = self.gen, self.host, self.sid
        self.loaded, self.saved = False, None
        self.set_text("")
        self.render(None)
        self.state.set_text("")
        self.state.set_tooltip_text(None)
        self.text.set_sensitive(False)
        self.edit_btn.set_sensitive(False)
        self.set_status("Lecture…")

        def done(text):
            if gen != self.gen:
                return
            self.loaded, self.saved = True, text
            self.set_status(None)
            self.set_text(text)
            self.text.set_sensitive(True)
            self.edit_btn.set_sensitive(True)
            editing = not text.strip()  # pas de note : directement en édition
            if self.edit_btn.get_active() == editing:
                self.set_editing(editing, self.focus)  # rafraîchit l'aperçu
            else:
                self.syncing = True
                self.edit_btn.set_active(editing)
                self.syncing = False
                self.set_editing(editing, self.focus)

        def fail(e):
            if gen == self.gen:  # rien de modifiable : on écraserait la note de l'hôte
                self.set_status(f"Lecture impossible : {e}")
        self.io.load(host, sid, done, fail)

    def set_status(self, text):
        self.status.set_text(text or "")
        self.status.set_visible(bool(text))

    def current_text(self):
        buf = self.text.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def set_text(self, text):
        buf = self.text.get_buffer()
        self.syncing = True
        buf.begin_irreversible_action()  # Ctrl+Z ne revient pas avant la lecture
        buf.set_text(text)
        buf.end_irreversible_action()
        self.syncing = False

    def set_editing(self, on, focus=True):
        if self.syncing:
            return  # appelé ensuite par qui règle le bouton
        self.edit_btn.set_tooltip_text("Aperçu" if on else "Modifier la note")
        if on:
            self.stack.set_visible_child_name("edit")
            if focus:
                self.text.grab_focus()
        else:
            self.flush()
            self.render(self.current_text())
            self.stack.set_visible_child_name("view")

    def render(self, text):
        """Aperçu de text (None : rien, pendant la lecture)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["viewer-md"])
        if text and text.strip():
            render_md(text, box)
        elif text is not None:
            box.append(Gtk.Label(label="Pas encore de note : le crayon permet d'en écrire une.",
                                 xalign=0, wrap=True, css_classes=["notice"]))
        self.preview.set_child(box)
        while (child := self.copy_slot.get_first_child()):
            self.copy_slot.remove(child)
        if text and text.strip():
            self.copy_slot.append(copy_button(text, "Copier la note (Markdown)"))

    def on_changed(self, _buf):
        if self.syncing or not self.loaded:
            return
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(SAVE_DELAY, self.save_later)

    def save_later(self):
        self.timer = 0
        self.flush()
        return False

    def flush(self):
        """Enregistre tout de suite la note affichée si elle a changé."""
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if not self.loaded or self.sid is None:
            return
        text = self.current_text()
        if text == self.saved:
            return
        self.saved = text
        host, sid = self.host, self.sid
        self.state.set_text("Enregistrement…")

        def done(t):
            self.on_saved(host, sid, t)
            if (host, sid) == (self.host, self.sid) and self.saved == t:
                self.state.set_tooltip_text(None)
                self.state.set_text(f"Enregistrée à {time.strftime('%H:%M')}" if t.strip()
                                    else "Note supprimée")

        def fail(e):
            if (host, sid) == (self.host, self.sid):
                self.saved = None  # réessayée à la prochaine modification
                self.state.set_text(f"Enregistrement impossible : {e}")
                self.state.set_tooltip_text(str(e))
            self.on_error(host, sid, e)
        self.io.save(host, sid, text, done, fail)

    def forget(self):
        """Ferme la note affichée (après l'avoir enregistrée) : la prochaine sera relue."""
        self.flush()
        self.gen += 1
        self.host = self.sid = None
        self.loaded = False
