"""Markdown d'un message rendu dans un seul Gtk.TextView en lecture seule.

GTK 4 ne sait pas étendre une sélection d'un Gtk.Label au suivant : avec un
label par paragraphe, on ne pouvait pas copier plusieurs paragraphes, éléments
de liste ou blocs de code d'un coup. Ici tout le message est un seul texte
mis en forme par des tags ; seuls les tableaux et l'en-tête des blocs de code
(langage, bouton « copier ») sont des widgets intégrés. Les fonds des blocs de
code, les filets des citations et les séparateurs sont dessinés sous le texte
(do_snapshot_layer).
"""
import html
import re

from .gtk import Gdk, GLib, Graphene, Gsk, Gtk, Pango
from .render import (FILE_SCHEME, copy_button, esc, highlight, is_dark, make_label, md_inline,
                     parse_file_href)
from .widgets import weak_handler

RE_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+#.-]*)")
RE_HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RE_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(?:\[([ xX])\]\s+)?(.*)$")
RE_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
RE_TSEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

# Liens : Pango ne connaît pas <a>, remplacé par ces repères avant insert_markup.
LINK_OPEN, LINK_CLOSE = "", ""
RE_A = re.compile(r'<a href="([^"]*)">|</a>')
ANCHOR = "￼"  # caractère d'un widget intégré dans le texte

CODE_PAD = 10  # marge intérieure des blocs de code
QUOTE_STEP = 14  # retrait d'une citation
LIST_STEP = 18  # retrait d'un niveau de liste
HEADINGS = {1: (1.45, 6), 2: (1.25, 4), 3: (1.1, 2), 4: (1.0, 0)}  # échelle, marge au-dessus


def rgba(spec):
    c = Gdk.RGBA()
    c.parse(spec)
    return c


def split_row(line):
    """Cellules d'une ligne de tableau (les | entre backticks ne comptent pas)."""
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    cells, cur, in_code = [], "", False
    for i, ch in enumerate(line):
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code and (i == 0 or line[i - 1] != "\\"):
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return cells + [cur.strip()]


def table(rows):
    grid = Gtk.Grid(css_classes=["mdtable"], halign=Gtk.Align.START, valign=Gtk.Align.START)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            lbl = make_label(md_inline(cell), "th" if r == 0 else "td", cell)
            lbl.set_max_width_chars(48)
            if r % 2 == 0 and r:
                lbl.add_css_class("odd")
            grid.attach(lbl, c, r, 1, 1)
    return grid


def valid_markup(markup):
    try:
        Pango.parse_markup(markup, -1, "\0")
        return True
    except GLib.Error:
        return False


class Fit(Gtk.Widget):
    """Widget intégré au texte (tableau) : un TextView donne à ses widgets leur largeur
    minimale ; ici, la naturelle, réduite si besoin à la largeur du texte.

    Cette largeur ne remonte pas comme minimum du texte (MdView.do_measure) : sinon le
    texte ne pourrait pas être plus étroit que le tableau, qui ne se règle que sur la
    largeur précédente du texte, et tous deux ne perdraient que 4 pixels par image.

    Le texte est retrouvé comme ancêtre : un attribut qui le désignerait empêcherait sa
    libération (voir widgets.weak_handler)."""

    def __init__(self, child, left):
        super().__init__()
        self.child, self.left = child, left
        child.set_parent(self)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL and for_size < 0:
            for_size = self.measure(Gtk.Orientation.HORIZONTAL, -1)[1]  # (le TextView ne la donne pas)
        mini, nat, _mb, _nb = self.child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            view = self.get_ancestor(MdView)
            # celle qu'on est en train de lui donner
            avail = view.last_width - self.left - 4 if view is not None else 0
            mini = nat = max(mini, min(nat, avail)) if avail > 0 else nat
        return mini, nat, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.child.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None


class MdView(Gtk.TextView):
    """Texte Markdown sélectionnable d'un bout à l'autre."""

    def __init__(self, text):
        super().__init__(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                         hexpand=True, css_classes=["md"])
        self.buf = self.get_buffer()
        self.links = {}  # TextTag -> adresse
        self.sources = {}  # position -> texte copié à sa place (widgets intégrés, décor)
        self.natural = None  # largeur naturelle (texte non replié), calculée au besoin
        self.widths = []  # (balisage, position, décalage) des lignes, pour la calculer
        self.fits = []  # tableaux, à remesurer quand la largeur change
        self.anchored = []  # (widget intégré, retrait), pour la largeur minimale
        self.last_width = 0  # largeur allouée : celle des tableaux, et sa hauteur à remesurer
        self.decor = []  # (genre, position de début, position de fin, retrait) dessinés dessous
        self.after_gap = True  # rien encore : pas d'espace avant le premier bloc
        self.line_color = rgba("rgba(128,128,128,0.55)")
        self.code_bg = rgba("rgba(128,128,128,0.15)")
        tag = self.buf.create_tag
        tag("gap", scale=0.45)
        for level, (scale, above) in HEADINGS.items():
            tag(f"h{level}", weight=Pango.Weight.BOLD, scale=scale, pixels_above_lines=above)
        tag("code", family="monospace", scale=0.92, wrap_mode=Gtk.WrapMode.CHAR,
            right_margin=CODE_PAD)
        tag("codehead", justification=Gtk.Justification.RIGHT, right_margin=4, pixels_above_lines=2)
        tag("codeend", pixels_below_lines=8)
        tag("hr", scale=0.6, pixels_above_lines=4, pixels_below_lines=4)
        tag("dim")
        tag("li", pixels_above_lines=2)
        tag("done")
        self.link_tag = tag("links", underline=Pango.Underline.SINGLE)  # couleur : restyle
        self.restyle(is_dark())
        self.render(text or "", 0, ())
        if self.offset():  # chaque ligne finit par \n : pas de ligne vide en bas
            self.buf.delete(self.buf.get_iter_at_offset(self.offset() - 1), self.buf.get_end_iter())
        self.connect("copy-clipboard", self.on_copy)
        self.connect("realize", lambda v: v.restyle())
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", weak_handler(self, MdView.on_pressed))
        click.connect("released", weak_handler(self, MdView.on_released))
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", weak_handler(self, MdView.on_motion))
        self.add_controller(motion)

    def restyle(self, dark=None):
        """Couleurs d'après celle du texte (Breeze ne définit pas toutes les couleurs nommées)."""
        if dark is None:
            c = self.get_color()
            dark = 0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue > 0.5
            dim = Gdk.RGBA(red=c.red, green=c.green, blue=c.blue, alpha=c.alpha * 0.6)
        else:
            dim = rgba("rgba(255,255,255,0.6)" if dark else "rgba(0,0,0,0.55)")
        table = self.buf.get_tag_table()
        table.lookup("dim").props.foreground_rgba = dim
        table.lookup("done").props.foreground_rgba = dim
        self.link_tag.props.foreground_rgba = rgba("#3daee9" if dark else "#2475b8")

    # -- construction --
    def offset(self):
        return self.buf.get_char_count()

    def margin(self, left):
        """Tag de retrait (les left_margin de deux tags ne s'additionnent pas)."""
        tag = self.buf.get_tag_table().lookup(f"m{left}")
        return tag or self.buf.create_tag(f"m{left}", left_margin=left)

    def hanging(self, left, hang):
        """Élément de liste : « puce\ttexte », lignes repliées alignées sur le texte
        (indent négatif = retrait des lignes suivantes, taquet compté depuis left)."""
        name = f"li{left}:{hang}"
        tag = self.buf.get_tag_table().lookup(name)
        if not tag:
            tabs = Pango.TabArray.new(1, True)
            tabs.set_tab(0, Pango.TabAlign.LEFT, hang)
            tag = self.buf.create_tag(name, left_margin=left, indent=-hang, tabs=tabs)
        return tag

    def apply(self, tags, start, end=None):
        s = self.buf.get_iter_at_offset(start)
        e = self.buf.get_iter_at_offset(self.offset() if end is None else end)
        for t in tags:
            self.buf.apply_tag(t, s, e) if isinstance(t, Gtk.TextTag) else self.buf.apply_tag_by_name(t, s, e)

    def gap(self):
        """Espace entre deux blocs : une ligne vide basse (copiée comme une ligne vide)."""
        if not self.after_gap:
            start = self.offset()
            self.buf.insert(self.buf.get_end_iter(), "\n")
            self.apply(["gap"], start)
            self.after_gap = True

    def inline(self, markup, plain):
        """Insère du Markdown en ligne déjà converti par md_inline (liens compris)."""
        hrefs = []

        def mark(m):
            if m.group(1) is None:
                return LINK_CLOSE
            hrefs.append(html.unescape(m.group(1)))
            return LINK_OPEN
        markup = RE_A.sub(mark, markup)
        start = self.offset()
        if not valid_markup(markup):
            self.buf.insert(self.buf.get_end_iter(), plain)
            self.widths.append((esc(plain), start, 0))
            return
        self.buf.insert_markup(self.buf.get_end_iter(), markup, -1)
        self.widths.append((markup.replace(LINK_OPEN, "").replace(LINK_CLOSE, ""), start, 0))
        text = self.buf.get_text(self.buf.get_iter_at_offset(start), self.buf.get_end_iter(), True)
        # md_inline échappe le texte : ses <a> sont toujours appariés, jamais imbriqués.
        pairs = [(m.start(), m.end()) for m in re.finditer(f"{LINK_OPEN}[^{LINK_CLOSE}]*{LINK_CLOSE}", text)]
        for (a, b), href in reversed(list(zip(pairs, hrefs))):  # de la fin : positions stables
            a, b = start + a, start + b
            self.buf.delete(self.buf.get_iter_at_offset(b - 1), self.buf.get_iter_at_offset(b))
            self.buf.delete(self.buf.get_iter_at_offset(a), self.buf.get_iter_at_offset(a + 1))
            tag = self.buf.create_tag(None)
            self.links[tag] = href
            self.apply([tag, self.link_tag], a, b - 2)

    def line(self, markup, plain, tags):
        """Un paragraphe (une ligne du tampon) avec ses tags de paragraphe."""
        start = self.offset()
        self.inline(markup, plain)
        self.buf.insert(self.buf.get_end_iter(), "\n")
        self.apply(tags, start)
        self.after_gap = False

    def widget(self, child, source, left, tags):
        """Widget intégré sur sa propre ligne, retiré de `left` ; `source` le remplace à la copie."""
        start = self.offset()
        anchor = self.buf.create_child_anchor(self.buf.get_end_iter())
        self.add_child_at_anchor(child, anchor)
        self.anchored.append((child, left))
        self.sources[start] = source
        self.buf.insert(self.buf.get_end_iter(), "\n")
        self.apply(tags, start)
        self.after_gap = False

    def render(self, text, left, extra):
        """Blocs Markdown de `text`, retirés de `left` pixels, avec les tags `extra`."""
        lines = text.replace("\t", "    ").split("\n")
        i, n = 0, len(lines)
        base = [self.margin(left), *extra]
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if (m := RE_FENCE.match(line)):
                fence, lang, body = m.group(1), m.group(2), []
                i += 1
                while i < n and not lines[i].strip().startswith(fence):
                    body.append(lines[i])
                    i += 1
                i += 1
                indent = min((len(l) - len(l.lstrip()) for l in body if l.strip()), default=0)
                self.code("\n".join(l[indent:] for l in body), lang.lower(), left)
            elif (m := RE_HEAD.match(line)):
                self.gap()
                level = min(len(m.group(1)), 4)
                self.line(md_inline(m.group(2)), m.group(2), [*base, f"h{level}"])
                i += 1
            elif RE_HR.match(line):
                self.gap()
                start = self.offset()
                self.buf.insert(self.buf.get_end_iter(), "\n")
                self.apply(["hr"], start)
                self.sources[start] = "---\n"
                self.decor.append(("hr", start, start, left))
                self.after_gap = False
                i += 1
            elif line.lstrip().startswith("|") and i + 1 < n and RE_TSEP.match(lines[i + 1]):
                raw = [line, lines[i + 1]]
                rows = [split_row(line)]
                i += 2
                while i < n and lines[i].strip().startswith("|"):
                    raw.append(lines[i])
                    rows.append(split_row(lines[i]))
                    i += 1
                self.gap()
                fit = Fit(table(rows), left)
                self.fits.append((fit, self.offset()))
                self.widget(fit, "\n".join(l.strip() for l in raw), left, base)
            elif line.lstrip().startswith(">"):
                quoted = []
                while i < n and lines[i].lstrip().startswith(">"):
                    quoted.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                self.gap()
                start = self.offset()
                self.render("\n".join(quoted), left + QUOTE_STEP, (*extra, "dim"))
                if self.offset() > start:
                    self.decor.append(("quote", start, self.offset() - 1, left))
            elif RE_ITEM.match(line):
                i = self.list(lines, i, left, extra)
            else:
                para = [line.strip()]
                i += 1
                while i < n and lines[i].strip() and not (
                        RE_FENCE.match(lines[i]) or RE_HEAD.match(lines[i]) or RE_HR.match(lines[i])
                        or RE_ITEM.match(lines[i]) or lines[i].lstrip().startswith((">", "|"))):
                    para.append(lines[i].strip())
                    i += 1
                self.gap()
                for p in para:
                    self.line(md_inline(p), p, base)

    def code(self, code, lang, left):
        self.gap()
        start = self.offset()
        head = Gtk.Box(spacing=4)
        head.append(Gtk.Label(label=lang or "code", css_classes=["sid"]))
        head.append(copy_button(code, "Copier le code"))
        self.widget(head, "", left, [self.margin(left), "codehead"])
        body = self.offset()
        self.sources[start + 1] = ""  # en-tête du bloc : ni widget ni ligne à la copie
        markup = highlight(code, lang)
        if not valid_markup(markup):
            markup = esc(code)
        self.buf.insert_markup(self.buf.get_end_iter(), markup, -1)
        self.widths.append((f'<span font_family="monospace">{markup}</span>', body, 0))
        end = self.buf.get_end_iter()
        while self.offset() > body and (prev := self.buf.get_iter_at_offset(self.offset() - 1)).get_char() == "\n":
            self.buf.delete(prev, end)  # pygments finit par un saut de ligne, dans un <span>
            end = self.buf.get_end_iter()
        self.buf.insert(self.buf.get_end_iter(), "\n")
        self.apply([self.margin(left + CODE_PAD), "code"], body)
        last = self.buf.get_iter_at_offset(self.offset() - 1)
        last.set_line_offset(0)
        self.apply(["codeend"], last.get_offset())
        self.decor.append(("code", start, last.get_offset(), left))
        self.after_gap = False

    def list(self, lines, i, left, extra):
        """Liste (imbriquée, numérotée, cases à cocher) ; renvoie l'indice suivant."""
        items, n = [], len(lines)
        while i < n:
            m = RE_ITEM.match(lines[i])
            if not m:
                # Ligne de continuation indentée, ou fin de liste.
                if lines[i].strip() and lines[i].startswith("  ") and items:
                    items[-1][3].append(lines[i].strip())
                    i += 1
                    continue
                if not lines[i].strip() and i + 1 < n and RE_ITEM.match(lines[i + 1]):
                    i += 1
                    continue
                break
            indent, marker, check, content = m.groups()
            items.append([len(indent) // 2, marker, check, [content]])
            i += 1
        base = min(it[0] for it in items)
        bullets = []
        for depth, marker, check, _content in items:
            if check is not None:
                bullets.append("☑" if check.lower() == "x" else "☐")
            elif marker[0].isdigit():
                bullets.append(marker[:-1] + ".")
            else:
                bullets.append("•◦▪"[min(depth - base, 2)])
        # Colonne des puces : la plus large (« 10. ») ; le texte s'aligne après, lignes repliées comprises.
        hang = max(self.create_pango_layout(b + "  ").get_pixel_size()[0] for b in bullets)
        self.gap()
        for (depth, _marker, check, content), bullet in zip(items, bullets):
            indent = left + LIST_STEP * (depth - base)
            tags = [*extra, "done"] if check and check.lower() == "x" else list(extra)
            start = self.offset()
            self.buf.insert(self.buf.get_end_iter(), bullet + "\t")
            self.apply(["dim"], start)
            self.inline(md_inline(content[0]), content[0])
            self.widths[-1] = (*self.widths[-1][:2], hang)  # texte après la colonne des puces
            self.buf.insert(self.buf.get_end_iter(), "\n")
            self.apply([self.hanging(indent, hang), "li", *tags], start)
            for more in content[1:]:
                self.line(md_inline(more), more, [self.margin(indent + hang), *tags])
            self.after_gap = False
        return i

    # -- taille --
    def do_measure(self, orientation, for_size):
        """Largeur naturelle : celle du texte non replié, comme un Gtk.Label (sinon une
        bulle alignée à droite, qui prend sa largeur naturelle, se réduit à une colonne)."""
        mini, nat, mb, nb = Gtk.TextView.do_measure(self, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            if self.natural is None:
                self.natural = self.unwrapped_width()
            # Le TextView prend pour minimum la largeur de ses widgets ; un tableau (Fit)
            # y a celle du texte, qui le gonflerait : on compte chacun à son plus étroit.
            if self.anchored:
                mini = min(mini, self.narrowest())
            return mini, max(nat, self.natural), -1, -1
        return mini, nat, mb, nb

    def narrowest(self):
        """Largeur minimale des widgets intégrés (un tableau peut replier ses cellules)."""
        width = 0
        for child, left in self.anchored:
            inner = child.child if isinstance(child, Fit) else child
            width = max(width, inner.measure(Gtk.Orientation.HORIZONTAL, -1)[0] + left + 4)
        return width

    def unwrapped_width(self):
        width = 0
        for markup, start, shift in self.widths:
            it = self.buf.get_iter_at_offset(start)
            it.set_line_offset(0)
            scale, left = 1, 0
            for tag in it.get_tags():
                scale = tag.props.scale if tag.props.scale_set else scale
                left = tag.props.left_margin if tag.props.left_margin_set else left
            layout = self.create_pango_layout(None)
            layout.set_markup(markup, -1)
            w = max(layout.get_line_readonly(k).get_pixel_extents()[1].width
                    for k in range(layout.get_line_count()))
            width = max(width, int(w * scale) + left + shift + 4)
        for fit, _offset in self.fits:
            width = max(width, fit.child.measure(Gtk.Orientation.HORIZONTAL, -1)[1] + fit.left)
        return width

    def do_size_allocate(self, width, height, baseline):
        if width != self.last_width:
            self.last_width = width
            # Avant la mise en page du texte, qui remesure ses widgets : les tableaux
            # prennent la nouvelle largeur dès maintenant, pas à l'image suivante.
            for fit, _offset in self.fits:
                fit.queue_resize()
            # Après la remise en page (idle de priorité plus haute), au repos.
            GLib.idle_add(self.rewrapped, priority=GLib.PRIORITY_DEFAULT_IDLE)
        Gtk.TextView.do_size_allocate(self, width, height, baseline)

    def rewrapped(self):
        """Nouvelle largeur : le TextView replie son texte, mais annonce encore la hauteur
        mesurée à l'ancienne largeur (texte coupé, ou vide en bas) ; on la redemande.
        Les tableaux changent aussi de hauteur sans que celle de leur ligne suive :
        retoucher un tag de la ligne la fait recalculer (pas d'API pour ça)."""
        tag = self.buf.get_tag_table().lookup("refit") or self.buf.create_tag("refit")
        for fit, offset in self.fits:
            fit.queue_resize()
            s, e = self.buf.get_iter_at_offset(offset), self.buf.get_iter_at_offset(offset + 1)
            if s.has_tag(tag):
                self.buf.remove_tag(tag, s, e)
            else:
                self.buf.apply_tag(tag, s, e)
        self.queue_resize()
        return False

    # -- dessin sous le texte --
    def do_snapshot_layer(self, layer, snapshot):
        if layer != Gtk.TextViewLayer.BELOW_TEXT or not self.decor:
            return
        width = self.get_visible_rect().width
        for kind, start, end, left in self.decor:
            y0, _ = self.get_line_yrange(self.buf.get_iter_at_offset(start))
            y1, h1 = self.get_line_yrange(self.buf.get_iter_at_offset(end))
            if kind == "code":
                rect = Graphene.Rect().init(left, y0, max(0, width - left), y1 + h1 - y0)
                clip = Gsk.RoundedRect()
                clip.init_from_rect(rect, 8)  # (la valeur renvoyée est une copie invalide)
                snapshot.push_rounded_clip(clip)
                snapshot.append_color(self.code_bg, rect)
                snapshot.pop()
            elif kind == "quote":
                snapshot.append_color(self.line_color, Graphene.Rect().init(left, y0, 3, y1 + h1 - y0))
            elif kind == "hr":
                rect = Graphene.Rect().init(left, y0 + h1 // 2, max(0, width - left), 1)
                snapshot.append_color(self.line_color, rect)

    # -- copie et liens --
    def on_copy(self, view):
        """Copie de la sélection, les tableaux en Markdown à la place des widgets."""
        self.stop_emission_by_name("copy-clipboard")
        bounds = self.buf.get_selection_bounds()
        if not bounds:
            return
        s, e = bounds
        text, base = self.buf.get_slice(s, e, True), s.get_offset()
        if any(base <= k < base + len(text) for k in self.sources):
            text = "".join(self.sources.get(base + k, ch) for k, ch in enumerate(text))
        # Seules tabulations du tampon (le Markdown les change en espaces) : après les puces.
        self.get_clipboard().set(text.replace("\t", " "))

    def link_at(self, x, y):
        bx, by = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.get_iter_at_location(bx, by)
        if not ok:
            return None
        return next((self.links[t] for t in it.get_tags() if t in self.links), None)

    def on_pressed(self, _gesture, _n, x, y):
        self.press = (x, y)

    def on_released(self, _gesture, n, x, y):
        px, py = getattr(self, "press", (x, y))
        if n != 1 or abs(x - px) > 4 or abs(y - py) > 4 or self.buf.get_has_selection():
            return  # glissé (sélection) plutôt que clic
        uri = self.link_at(x, y)
        if not uri:
            return
        if uri.startswith(FILE_SCHEME):
            opener = getattr(self.get_root(), "open_file_link", None)
            if opener:
                opener(self, *parse_file_href(uri))
        else:
            Gtk.UriLauncher(uri=uri).launch(self.get_root(), None, None, None)

    def on_motion(self, _ctrl, x, y):
        self.set_cursor_from_name("pointer" if self.link_at(x, y) else "text")
