"""Sélecteur rapide (Ctrl+K) : retrouver une conversation en tapant quelques lettres.

La recherche est approximative : les lettres de chaque mot tapé doivent apparaître
dans l'ordre, pas forcément à la suite (« sepjr » trouve « Séparateurs de jour »),
dans le titre, le dossier, l'hôte ou les tags. Les débuts de mots et les lettres
consécutives comptent davantage, le titre plus que le reste. Sans texte, la liste
montre les épinglées puis les plus récentes.
"""
from .cursor import norm_text
from .gtk import Gdk, GLib, Gtk, Pango
from .listrow import StateIcon, short_date, status_kind
from .render import esc
from .widgets import host_chip

LIMIT = 60       # conversations affichées
TITLE_WEIGHT = 2  # le titre compte plus que le dossier, l'hôte ou les tags


def normalized(text):
    """Texte en minuscules sans accents, et pour chaque caractère sa position d'origine."""
    low, origin = [], []
    for i, ch in enumerate(text):
        for c in norm_text(ch):
            low.append(c)
            origin.append(i)
    return "".join(low), origin


def word_score(word, low):
    """Meilleur score de word (déjà normalisé) comme sous-suite de low, et les positions
    trouvées ; (None, []) si ses lettres n'y sont pas dans l'ordre."""
    best, best_pos = None, []
    start = low.find(word[0]) if word else -1
    while start >= 0:
        pos, i = [start], start + 1
        for c in word[1:]:
            i = low.find(c, i)
            if i < 0:
                return best, best_pos  # plus loin, il manquera aussi des lettres
            pos.append(i)
            i += 1
        score = 0.0
        for k, p in enumerate(pos):
            score += 1
            if p == 0 or not low[p - 1].isalnum():
                score += 3  # début de mot
            if k and p == pos[k - 1] + 1:
                score += 2  # à la suite de la précédente
        score -= 0.05 * (pos[-1] - pos[0] + 1 - len(pos))  # lettres éparpillées
        if best is None or score > best:
            best, best_pos = score, pos
        start = low.find(word[0], start + 1)
    return best, best_pos


def fuzzy_match(query, fields):
    """Score de query sur les champs [(texte, poids)] et positions trouvées dans le premier
    champ (le titre) ; None si un des mots ne se trouve dans aucun champ."""
    words = norm_text(query).split()
    prepared = [(normalized(text or ""), weight) for text, weight in fields]
    total, title_pos = 0.0, set()
    for w in words:
        best = None
        for k, ((low, origin), weight) in enumerate(prepared):
            score, pos = word_score(w, low)
            if score is None:
                continue
            score *= weight
            if best is None or score > best[0]:
                best = (score, k, [origin[p] for p in pos])
        if best is None:
            return None
        total += best[0]
        if best[1] == 0:
            title_pos.update(best[2])
    return total, title_pos


def marked(text, positions):
    """Balisage Pango de text avec les lettres trouvées en gras."""
    out = []
    for i, ch in enumerate(text):
        out.append(f"<b>{esc(ch)}</b>" if i in positions else esc(ch))
    return "".join(out).replace("</b><b>", "")


def rank(entries, query, limit=LIMIT):
    """entries : [{"title", "folder", "host", "tags", "mtime", "pinned"…}] -> les meilleures,
    avec "marks" (positions trouvées dans le titre)."""
    if not query.strip():
        out = sorted(entries, key=lambda e: (not e.get("pinned"), -(e.get("mtime") or 0)))
        return [dict(e, marks=set()) for e in out[:limit]]
    scored = []
    for e in entries:
        m = fuzzy_match(query, [(e["title"], TITLE_WEIGHT), (e.get("folder") or "", 1),
                                (e.get("host") or "", 1), (" ".join(e.get("tags") or ()), 1)])
        if m:
            scored.append((m[0], e.get("mtime") or 0, e, m[1]))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [dict(e, marks=marks) for _s, _t, e, marks in scored[:limit]]


class Switcher(Gtk.Popover):
    """Popover du sélecteur. entries() renvoie les conversations proposées ; open_sid(sid)
    ouvre celle choisie."""

    def __init__(self, entries, open_sid):
        super().__init__(has_arrow=False, autohide=True)
        self.add_css_class("switcher")  # en plus de « background »
        self.entries, self.open_sid, self.shown = entries, open_sid, []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=560)
        self.entry = Gtk.SearchEntry(placeholder_text="Aller à une conversation…", hexpand=True)
        self.entry.connect("search-changed", lambda _e: self.fill())
        self.entry.connect("activate", lambda _e: self.choose(self.list.get_selected_row()))
        self.entry.connect("stop-search", lambda _e: self.popdown())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.entry.add_controller(keys)
        box.append(self.entry)
        self.list = Gtk.ListBox(css_classes=["sessions", "switcher-list"],
                                selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.connect("row-activated", lambda _l, row: self.choose(row))
        self.list.set_placeholder(Gtk.Label(label="Aucune conversation ne correspond.", xalign=0,
                                            valign=Gtk.Align.START, css_classes=["notice"]))
        # hauteur fixe : la fenêtre ne saute pas au fil de la frappe
        self.scroll = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         min_content_height=420, max_content_height=420)
        box.append(self.scroll)
        box.append(Gtk.Label(label="↑↓ choisir · Entrée ouvrir · Échap fermer · "
                                   "Alt+↑↓ conversation précédente, suivante · "
                                   "Alt+Maj+↑↓ à traiter", xalign=0, wrap=True,
                             css_classes=["hint"]))
        self.set_child(box)

    def present(self):
        self.entry.set_text("")
        self.fill()
        self.popup()
        self.entry.grab_focus()

    def fill(self):
        self.list.remove_all()
        self.shown = rank(self.entries(), self.entry.get_text())
        for e in self.shown:
            self.list.append(self.row(e))
        self.scroll.get_vadjustment().set_value(0)
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)

    def row(self, e):
        line = Gtk.Box(spacing=10)
        state = StateIcon()
        state.show(status_kind(e.get("status")))
        line.append(state)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["row-title"])
        title.set_markup(marked(e["title"], e["marks"]))
        main.append(title)
        meta = Gtk.Box(spacing=6, css_classes=["row-meta"])
        if e.get("show_host"):
            meta.append(host_chip(e["host"]))
        if e.get("pinned"):
            meta.append(Gtk.Image(icon_name="claude-hub-pin-symbolic", css_classes=["dim"],
                                  tooltip_text="Épinglée"))
        meta.append(Gtk.Label(label=e.get("folder") or "", xalign=0, css_classes=["row-folder"],
                              ellipsize=Pango.EllipsizeMode.MIDDLE))
        main.append(meta)
        line.append(main)
        line.append(Gtk.Label(label=short_date(e.get("mtime")), css_classes=["row-time"],
                              valign=Gtk.Align.START))
        row = Gtk.ListBoxRow(child=line, css_classes=["session"])
        row.sid = e["sid"]
        return row

    def move(self, delta):
        row = self.list.get_selected_row()
        i = (row.get_index() if row else -1) + delta
        target = self.list.get_row_at_index(max(0, min(i, len(self.shown) - 1)))
        if target:
            self.list.select_row(target)
            GLib.idle_add(self.keep_visible, target)

    def keep_visible(self, row):
        """Fait défiler la liste jusqu'à la ligne choisie (le focus reste dans la saisie)."""
        ok, rect = row.compute_bounds(self.list)
        adj = self.scroll.get_vadjustment()
        if ok:
            top, bottom = rect.get_y(), rect.get_y() + rect.get_height()
            if top < adj.get_value():
                adj.set_value(top)
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size())
        return False

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self.move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self.move(-1)
            return True
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.move(8 if keyval == Gdk.KEY_Page_Down else -8)
            return True
        return False

    def choose(self, row):
        if row is None:
            return
        self.popdown()
        self.open_sid(row.sid)
