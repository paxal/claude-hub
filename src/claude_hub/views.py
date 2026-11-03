"""Vues enregistrées : recherches gardées (« #stockly », « dir:~/chansons scout »…),
affichées en pastilles sous la recherche pour y revenir en un clic.

Un clic applique la recherche de la vue, un second l'enlève ; le clic droit propose
de la retirer. Le bouton « Enregistrer la vue » apparaît quand la recherche en cours
n'est pas déjà une vue. Les vues sont gardées dans settings.json (« views »).
"""
from .gtk import GLib, Gtk, Pango
from .tagui import TAG_ICON
from .widgets import WrapBox, menu_popover, themed_icon, weak_handler


def same_query(a, b):
    """Deux recherches identiques, aux espaces près."""
    return " ".join((a or "").split()) == " ".join((b or "").split())


def view_icon(query):
    """Icône d'une vue d'après son premier filtre : tag, dossier ou texte."""
    first = (query.split() or [""])[0]
    if first.startswith("#"):
        return TAG_ICON
    if first.startswith("dir:"):
        return "folder-symbolic"
    return "edit-find-symbolic"


def add_view(views, query):
    """views avec query en plus (à la fin), sans doublon ; une recherche vide n'est pas gardée."""
    query = " ".join(query.split())
    if not query or any(same_query(v.get("query"), query) for v in views):
        return list(views)
    return list(views) + [{"query": query}]


def remove_view(views, query):
    return [v for v in views if not same_query(v.get("query"), query)]


class ViewsBar(WrapBox):
    """Pastilles des vues. apply(query) remplace la recherche ; save(query) enregistre la
    recherche en cours ; remove(query) retire une vue."""

    def __init__(self, apply, save, remove):
        super().__init__(visible=False)
        self.add_css_class("views")
        self.apply, self.save, self.remove = apply, save, remove

    def update(self, views, query):
        self.remove_all()
        query = " ".join((query or "").split())
        for v in views:
            self.append(self.chip(v["query"], same_query(v["query"], query)))
        saved = any(same_query(v["query"], query) for v in views)
        if query and not saved:
            btn = Gtk.Button(css_classes=["flat", "viewsave"],
                             tooltip_text=f"Garder la recherche « {query} » en pastille, pour y "
                                          "revenir en un clic")
            inner = Gtk.Box(spacing=4)
            inner.append(themed_icon("bookmark-new-symbolic", "starred-symbolic"))
            inner.append(Gtk.Label(label="Enregistrer la vue"))
            btn.set_child(inner)
            btn.connect("clicked", lambda _b: self.save(query))
            self.append(btn)
        self.set_visible(bool(views) or bool(query))

    def chip(self, query, active):
        btn = Gtk.Button(css_classes=["viewchip"] + (["active"] if active else []),
                         tooltip_text=(f"{query}\nClic : {'retirer le filtre' if active else 'appliquer'}"
                                       " · clic droit : supprimer la vue"))
        inner = Gtk.Box(spacing=4)
        inner.append(Gtk.Image(icon_name=view_icon(query), pixel_size=12))
        inner.append(Gtk.Label(label=query, ellipsize=Pango.EllipsizeMode.END, max_width_chars=24))
        btn.set_child(inner)
        btn.connect("clicked", lambda _b: self.apply("" if active else query))
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", lambda g, *_a: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                self.ask_remove(g.get_widget(), query)))
        btn.add_controller(menu)
        return btn

    def ask_remove(self, btn, query):
        remove = Gtk.Button(label="Supprimer la vue", css_classes=["flat"])
        pop = menu_popover(remove)
        pop.set_parent(btn)
        pop.removing = False

        def closed(_p):
            # après la fermeture : retirer la vue reconstruit la barre, donc détruit btn
            def later():
                if pop.get_parent() is not None:
                    pop.unparent()
                if pop.removing:
                    self.remove(query)
                return False
            GLib.idle_add(later)
        remove.connect("clicked", weak_handler(pop, lambda p, _b: (setattr(p, "removing", True), p.popdown())))
        pop.connect("closed", closed)
        pop.popup()
