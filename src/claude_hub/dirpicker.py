"""Sélecteur de dossier sur un hôte SSH (le sélecteur natif ne voit que ce PC)."""
import posixpath

from .gtk import Gdk, Gtk, Pango, in_thread
from .hosts import run_helper


class RemoteDirDialog(Gtk.Window):
    """Parcourt les dossiers de `host` à partir de `start` ; appelle
    on_chosen(chemin absolu) quand on valide."""

    def __init__(self, parent, host, start, on_chosen):
        super().__init__(transient_for=parent, modal=True, default_width=520, default_height=480,
                         title=f"Dossier sur {host}")
        self.host, self.on_chosen = host, on_chosen
        self.path, self.home, self.dirs = None, None, []
        self.gen = 0  # ignore les réponses d'un chargement dépassé

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8,
                      margin_bottom=8, margin_start=8, margin_end=8)
        self.set_child(box)

        row = Gtk.Box(spacing=4)
        up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Dossier parent")
        up.connect("clicked", lambda _b: self.path and self.load(posixpath.dirname(self.path)))
        home = Gtk.Button(icon_name="go-home-symbolic", tooltip_text="Dossier personnel")
        home.connect("clicked", lambda _b: self.load("~"))
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="chemin, ~ accepté")
        self.entry.connect("activate", lambda e: self.load(e.get_text().strip() or "~"))
        for w in (up, home, self.entry):
            row.append(w)
        box.append(row)

        self.listbox = Gtk.ListBox()
        self.listbox.connect("row-activated", lambda _l, r: self.load(
            posixpath.join(self.path, r.dir_name)))
        box.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True))

        row = Gtk.Box(spacing=8)
        self.hidden = Gtk.CheckButton(label="Dossiers cachés")
        self.hidden.connect("toggled", lambda _b: self.fill())
        row.append(self.hidden)
        self.status = Gtk.Label(xalign=0, hexpand=True, css_classes=["sid"],
                                ellipsize=Pango.EllipsizeMode.END)
        row.append(self.status)
        cancel = Gtk.Button(label="Annuler")
        cancel.connect("clicked", lambda _b: self.close())
        self.choose = Gtk.Button(label="Choisir", css_classes=["suggested-action"], sensitive=False)
        self.choose.connect("clicked", self.on_choose)
        row.append(cancel)
        row.append(self.choose)
        box.append(row)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        self.load(start or "~")

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def load(self, path):
        self.gen += 1
        gen = self.gen
        self.status.set_text("Chargement…")
        self.choose.set_sensitive(False)

        def done(res):
            if gen != self.gen:
                return
            if res.get("error"):  # dossier illisible : on reste où l'on était
                self.status.set_text(f"{res['path']} : {res['error']}")
                if self.path is None:
                    if path != "~":
                        self.load("~")
                else:
                    self.entry.set_text(self.path)
                    self.choose.set_sensitive(True)
                return
            self.path, self.home, self.dirs = res["path"], res["home"], res["dirs"]
            self.entry.set_text(self.path)
            self.choose.set_sensitive(True)
            self.status.set_text("")
            self.fill()

        def failed(e):
            if gen == self.gen:
                self.status.set_text(f"Erreur : {e}")
        in_thread(lambda: run_helper(self.host, "dirs", path), done, failed)

    def fill(self):
        while row := self.listbox.get_row_at_index(0):
            self.listbox.remove(row)
        show_hidden = self.hidden.get_active()
        for name in self.dirs:
            if name.startswith(".") and not show_hidden:
                continue
            line = Gtk.Box(spacing=8, margin_top=3, margin_bottom=3, margin_start=6)
            line.append(Gtk.Image(icon_name="folder-symbolic"))
            line.append(Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END))
            row = Gtk.ListBoxRow(child=line)
            row.dir_name = name
            self.listbox.append(row)
        if not self.listbox.get_row_at_index(0):
            self.status.set_text("Aucun sous-dossier")

    def on_choose(self, _btn):
        self.on_chosen(self.path, self.home)
        self.close()
