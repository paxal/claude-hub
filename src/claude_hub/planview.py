"""Plan de Claude (ExitPlanMode) affiché dans le volet latéral, avec ses actions.

Le volet ne répond pas lui-même à claude : ses boutons appellent on_run(mode) et
on_refuse() fournis par la fenêtre, qui transmet la réponse et met à jour la
carte du fil et le volet ensemble.
"""
from .gtk import Gtk, Pango
from .permissions import EXEC_MODES, mode_row
from .render import copy_button, esc, file_href, make_label, render_md


class PlanPanel(Gtk.Box):
    def __init__(self, on_run, on_refuse, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["viewer"])
        self.sid = self.request_id = self.plan = self.path = None
        self.decision = None  # (résumé, accepté) une fois la demande close
        head = Gtk.Box(spacing=4, margin_top=6, margin_start=8, margin_end=4, margin_bottom=4)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        names.append(Gtk.Label(label="📋 Plan proposé par Claude", xalign=0,
                               css_classes=["card-title"], ellipsize=Pango.EllipsizeMode.END))
        self.where = make_label("", "sid")
        self.where.set_ellipsize(Pango.EllipsizeMode.START)
        self.where.set_wrap(False)
        names.append(self.where)
        head.append(names)
        self.copy_box = Gtk.Box(valign=Gtk.Align.CENTER)
        head.append(self.copy_box)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                           tooltip_text="Fermer", valign=Gtk.Align.CENTER)
        close.connect("clicked", lambda _b: on_close())
        head.append(close)
        self.append(head)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.append(self.scroll)
        # Pied : actions tant que Claude attend la réponse, puis la décision prise.
        self.actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6,
                               margin_bottom=8, margin_start=8, margin_end=8)
        modes, self.mode_dd = mode_row()
        self.actions.append(modes)
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.actions.append(buttons)
        refuse = Gtk.Button(label="Continuer à planifier", tooltip_text="Refuser le plan : le "
                            "texte de la saisie est transmis à Claude comme remarque")
        refuse.connect("clicked", lambda _b: on_refuse())
        buttons.append(refuse)
        run = Gtk.Button(label="Lancer", tooltip_text="Valider le plan : Claude commence l'exécution",
                         css_classes=["suggested-action"])
        run.connect("clicked", lambda _b: on_run(EXEC_MODES[self.mode_dd.get_selected()][0]))
        buttons.append(run)
        self.append(self.actions)
        self.summary = Gtk.Label(xalign=0, wrap=True, margin_top=6, margin_bottom=8,
                                 margin_start=12, margin_end=8, visible=False)
        self.append(self.summary)

    def show(self, sid, plan, path=None, request_id=None):
        """Affiche un plan ; request_id : demande en attente (actions proposées)."""
        self.sid, self.plan, self.path, self.request_id = sid, plan, path, request_id
        self.decision = None
        if path:
            self.where.set_markup(f'<a href="{esc(file_href(path))}">{esc(path)}</a>')
            self.where.set_tooltip_text(path)
        else:
            self.where.set_text("")
            self.where.set_tooltip_text(None)
        self.where.set_visible(bool(path))
        while (child := self.copy_box.get_first_child()):
            self.copy_box.remove(child)
        self.copy_box.append(copy_button(plan, "Copier le plan (Markdown)"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["viewer-md"])
        render_md(plan or "(plan vide)", box)
        self.scroll.set_child(box)
        self.actions.set_visible(request_id is not None)
        self.summary.set_visible(False)
        self.mode_dd.set_selected(0)

    def done(self, summary, ok=True):
        """Demande close (réponse donnée d'ici ou du fil, annulation) : le résumé remplace les actions."""
        self.request_id = None
        self.decision = (summary, ok)
        self.actions.set_visible(False)
        self.summary.set_text(summary)
        self.summary.set_css_classes(["plan-summary", "ok" if ok else "refused"])
        self.summary.set_visible(True)

