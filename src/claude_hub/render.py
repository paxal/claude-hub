"""Rendu des messages : Markdown (via mdview.py), coloration, images, CSS."""
import base64
import html
import re
from urllib.parse import quote, unquote

from .gtk import GLib, Gdk, GdkPixbuf, Gio, Gtk, Pango
from .hosts import MODE_LABELS
from .todos import TODO_TOOLS, todo_detail


try:
    from pygments import lex
    from pygments.lexers import TextLexer, get_lexer_by_name, get_lexer_for_filename, guess_lexer
    from pygments.styles import get_style_by_name
    from pygments.util import ClassNotFound
except ImportError:  # coloration syntaxique optionnelle
    lex = None

PLACEHOLDER = "\ue000{}\ue001"  # zone privée Unicode (GLib coupe au NUL)

# Chemins de fichiers cités dans les messages : liens hub-file:, ouverts dans la
# visionneuse (viewer.py). Suffixes de ligne reconnus : « :12 », « :12-20 », « #L12-L20 ».
FILE_SCHEME = "hub-file:"
_SEG = r"[\w.+-]*[\w+-]"
_LINE = r"(?::(\d+)(?:-(\d+))?|#L(\d+)(?:-L?(\d+))?)?"
RE_PATH = re.compile(rf"(?<![\w/.~:\\-])((?:~|\.\.?)?/?(?:{_SEG}/)*{_SEG}){_LINE}(?![\w/])")
RE_EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
# Noms seuls (sans dossier ni ligne) reconnus dans le code en ligne : `README.md`.
CODE_EXTS = {"py", "js", "ts", "tsx", "jsx", "md", "json", "toml", "yaml", "yml", "sh", "rs",
             "go", "c", "h", "cc", "cpp", "java", "kt", "rb", "nix", "bzl", "css", "html",
             "txt", "cfg", "ini", "conf", "svg", "png", "jpg", "jpeg", "sql", "lua", "desktop"}


def path_ok(path, line, code):
    """Vrai si ce qui ressemble à un chemin en est sûrement un (pas « et/ou », « 1/2 »…)."""
    last = path.rsplit("/", 1)[-1]
    ext = RE_EXT.search(last)
    if path.startswith(("~/", "./", "../")):
        return True
    if path.startswith("/"):
        return path.count("/") >= 2 or bool(ext)
    if not ext:
        return False
    return "/" in path or bool(line) or (code and ext.group(0)[1:].lower() in CODE_EXTS)


def file_href(path, line=None, end=None):
    href = FILE_SCHEME + quote(path, safe="/~.-_+@")
    if line:
        href += f"#L{line}" + (f"-L{end}" if end else "")
    return href


def parse_file_href(uri):
    """hub-file:… -> (chemin, ligne, fin) ; ligne et fin valent None si absentes."""
    body, _, frag = uri[len(FILE_SCHEME):].partition("#")
    m = re.fullmatch(r"L(\d+)(?:-L(\d+))?", frag)
    return (unquote(body), int(m.group(1)) if m else None,
            int(m.group(2)) if m and m.group(2) else None)


def copied_link(uri):
    """Texte copié par « Copier l'adresse du lien » : l'adresse, ou pour un fichier
    son chemin tel qu'il est cité (chemin:12, chemin:12-20)."""
    if not uri.startswith(FILE_SCHEME):
        return uri
    path, line, end = parse_file_href(uri)
    return path + (f":{line}" if line else "") + (f"-{end}" if line and end else "")


def label_target(uri):
    """Lien d'un Gtk.Label (voir label_links) -> (chemin, ligne, fin) s'il désigne un
    fichier, None pour une adresse (https:, mailto:…) ou une ancre."""
    if uri.startswith(FILE_SCHEME):
        return parse_file_href(uri)
    if uri.startswith("#") or re.match(r"[A-Za-z][\w+.-]*:(?!\d)", uri):
        return None
    m = re.fullmatch(r"(.+?):(\d+)(?:-(\d+))?", uri)
    if not m:
        return uri, None, None
    return m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None


RE_FILE_A = re.compile(r'<a href="(hub-file:[^"]*)">')


def label_links(markup):
    """Balisage d'un Gtk.Label : l'adresse d'un lien de fichier devient le chemin cité
    (src/a.py:42). Le menu du label (« Copier l'adresse du lien ») copie l'adresse telle
    quelle, sans qu'on puisse le changer ; on_link la relit. Un chemin qui se relirait
    autrement (« a:b », « #x ») garde son adresse hub-file:."""
    def plain(m):
        uri = html.unescape(m.group(1))
        text = copied_link(uri)
        return f'<a href="{esc(text)}">' if label_target(text) == parse_file_href(uri) else m.group(0)
    return RE_FILE_A.sub(plain, markup)


def path_link(text, code=False):
    """Lien hub-file: si tout `text` est un chemin (suffixe de ligne compris), sinon None."""
    m = RE_PATH.fullmatch(text)
    if not m or "://" in text:
        return None
    line, end = m.group(2) or m.group(4), m.group(3) or m.group(5)
    if not path_ok(m.group(1), line, code):
        return None
    return file_href(m.group(1), line, end)


def local_target(target):
    """Cible d'un lien Markdown vers un fichier ([x](src/a.py:12), file:///…) -> hub-file:."""
    if target.startswith("file://"):
        target = unquote(target[7:])
    elif re.match(r"[A-Za-z][\w+.-]*:(?!\d)", target) or target.startswith("#"):
        return None  # URL (https:, mailto:…) ou ancre
    m = re.fullmatch(rf"(.+?){_LINE}", target)
    return file_href(m.group(1), m.group(2) or m.group(4), m.group(3) or m.group(5))


def esc(text):
    return GLib.markup_escape_text(text)


def md_inline(text):
    """Markdown en ligne -> balisage Pango. Code et liens sont mis de côté
    (placeholders) pour ne pas subir les autres transformations."""
    kept = []

    def keep(markup):
        kept.append(markup)
        return PLACEHOLDER.format(len(kept) - 1)

    def code_span(m):
        code = m.group(2).strip()
        span = ('<span font_family="monospace" bgcolor="#808080" bgalpha="22%">'
                + esc(code) + "</span>")
        href = path_link(code.removeprefix("@"), code=True)  # @fichier : mention Claude Code
        return keep(f'<a href="{esc(href)}">{span}</a>' if href else span)
    text = re.sub(r"(`+)(.+?)\1", code_span, text)

    def fmt(t):
        t = esc(t)
        t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", t)
        # __ ne compte pas dans un mot (CommonMark) : mcp__postgres__query reste tel quel.
        t = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?!\*)", r"<i>\1</i>", t)
        t = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?!\w)", r"<i>\1</i>", t)
        return re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", t)

    # Le texte d'un lien peut contenir du code déjà mis de côté : même liste, sans
    # son propre lien (chemin en code), car GTK refuse les liens imbriqués.
    def link(m):
        for i in re.findall("\ue000(\\d+)\ue001", m.group(1)):
            kept[int(i)] = re.sub(r"</?a\b[^>]*>", "", kept[int(i)])
        return keep(f'<a href="{esc(local_target(m.group(2)) or m.group(2))}">{fmt(m.group(1))}</a>')
    text = re.sub(r"!?\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", link, text)
    # Ponctuation finale exclue de l'URL (comme GFM), dont * _ ~ : **https://\u2026** reste en gras.
    text = re.sub(r"<?(https?://[^\s<>)\]\ue000]+[^\s<>)\].,;:!?'\"*_~\ue000])>?", lambda m: keep(
        f'<a href="{esc(m.group(1))}">{esc(m.group(1))}</a>'), text)

    def plain_path(m):
        href = path_link(m.group(0))
        return keep(f'<a href="{esc(href)}">{esc(m.group(0))}</a>') if href else m.group(0)
    text = RE_PATH.sub(plain_path, text)
    s = fmt(text)
    while "\ue000" in s:
        s = re.sub("\ue000(\\d+)\ue001", lambda m: kept[int(m.group(1))], s)
    return s


def make_label(markup, css, plain=None):
    lbl = Gtk.Label(xalign=0, wrap=True, selectable=True)
    lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    try:
        # Pango ignore <a> (géré par Gtk.Label) : on l'enlève pour valider.
        Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", markup), -1, "\0")
        lbl.set_markup(label_links(markup))
    except GLib.Error:
        lbl.set_text(plain if plain is not None else markup)
    lbl.add_css_class(css)
    lbl.connect("activate-link", on_link)
    return lbl


def on_link(lbl, uri):
    """Les liens de fichier vont à la fenêtre (open_file_link), les autres au navigateur."""
    target = label_target(uri)
    if target is None:
        return False
    opener = getattr(lbl.get_root(), "open_file_link", None)
    if opener:
        opener(lbl, *target)
    return True


_dark = None  # fixé au démarrage d'après la couleur réelle du texte


def is_dark():
    if _dark is not None:
        return _dark
    st = Gtk.Settings.get_default()
    return bool(st.get_property("gtk-application-prefer-dark-theme")
                or "dark" in (st.get_property("gtk-theme-name") or "").lower())


def detect_dark(widget):
    """Thème sombre si le texte est clair (plus fiable que le nom du thème)."""
    global _dark
    c = widget.get_color()
    _dark = 0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue > 0.5


def highlight(code, lang):
    """Code -> balisage Pango coloré avec pygments (ou simplement échappé)."""
    if lex is None:
        return esc(code)
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        lexer = TextLexer()
    style = get_style_by_name("one-dark" if is_dark() else "friendly")
    out = []
    try:
        tokens = list(lex(code, lexer))
    except Exception:  # noqa: BLE001 — un lexer qui plante ne doit pas casser l'affichage
        return esc(code)
    for ttype, value in tokens:
        while True:
            try:
                st = style.style_for_token(ttype)
                break
            except KeyError:  # jeton inconnu du style : on remonte au parent
                if ttype.parent is None:
                    st = {"color": None, "bold": False, "italic": False}
                    break
                ttype = ttype.parent
        attrs = ""
        if st["color"]:
            attrs += f' foreground="#{st["color"]}"'
        if st["bold"]:
            attrs += ' weight="bold"'
        if st["italic"]:
            attrs += ' style="italic"'
        out.append(f"<span{attrs}>{esc(value)}</span>" if attrs else esc(value))
    return "".join(out).rstrip("\n")


def copy_button(text, tooltip, label=None):
    """Bouton qui copie `text` ; affiche ✓ un instant pour confirmer."""
    btn = Gtk.Button(tooltip_text=tooltip, css_classes=["flat", "copy"])
    if label:
        btn.set_label(label)
    else:
        btn.set_icon_name("edit-copy-symbolic")

    def clicked(b):
        b.get_clipboard().set(text)
        b.set_label("✓") if label else b.set_icon_name("object-select-symbolic")
        GLib.timeout_add(1200, lambda: (b.set_label(label) if label
                                        else b.set_icon_name("edit-copy-symbolic")) and False)
    btn.connect("clicked", clicked)
    return btn


def lang_for(path, text=""):
    """Nom de lexer pygments d'après le nom du fichier, ou son début (#!/usr/bin/env
    python3…) ; « text » si inconnu."""
    if lex is None:
        return "text"
    try:
        return get_lexer_for_filename(path.rsplit("/", 1)[-1]).aliases[0]
    except (ClassNotFound, IndexError):
        pass
    if text.startswith("#!"):
        try:
            return guess_lexer(text[:4000]).aliases[0]
        except (ClassNotFound, IndexError):
            pass
    return "text"


def code_block(code, lang):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["codeblock"])
    head = Gtk.Box(css_classes=["codehead"])
    head.append(Gtk.Label(label=lang or "code", xalign=0, hexpand=True, css_classes=["sid"]))
    head.append(copy_button(code, "Copier le code"))
    box.append(head)
    lbl = Gtk.Label(xalign=0, selectable=True, css_classes=["code"])
    markup = highlight(code, lang)
    try:
        Pango.parse_markup(markup, -1, "\0")
        lbl.set_markup(markup)
    except GLib.Error:
        lbl.set_text(code)
    box.append(Gtk.ScrolledWindow(child=lbl, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                  propagate_natural_height=True))
    return box


def render_md(text, box):
    """Ajoute à box le Markdown de text, rendu dans un seul texte sélectionnable (mdview.py)."""
    from .mdview import MdView  # mdview s'appuie sur ce module
    box.append(MdView(text))


def tool_detail(inp):
    inp = inp if isinstance(inp, dict) else {}
    qs = inp.get("questions")  # AskUserQuestion : sa première question
    if isinstance(qs, list) and qs and isinstance(qs[0], dict) and isinstance(qs[0].get("question"), str):
        return " ".join(qs[0]["question"].split())[:160]
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
              or inp.get("description") or inp.get("url") or inp.get("query")
              or next((v for v in inp.values() if isinstance(v, str) and v.strip()), ""))
    return " ".join(str(detail).split())[:160]


# Icône de chaque outil (premier nom connu du thème) ; les autres ont celle des réglages.
TOOL_ICONS = {
    "Read": ("text-x-generic-symbolic",),
    "Write": ("document-new-symbolic", "text-x-generic-symbolic"),
    "Bash": ("utilities-terminal-symbolic",),
    "Grep": ("edit-find-symbolic",),
    "Glob": ("edit-find-symbolic",),
    "ToolSearch": ("system-search-symbolic", "edit-find-symbolic"),
    "WebFetch": ("globe-symbolic", "web-browser-symbolic"),
    "WebSearch": ("globe-symbolic", "web-browser-symbolic"),
    "SendMessage": ("mail-send-symbolic",),
    "AskUserQuestion": ("dialog-question-symbolic", "help-about-symbolic"),
    **{n: ("document-edit-symbolic",) for n in ("Edit", "MultiEdit", "NotebookEdit")},
    **{n: ("checkbox-checked-symbolic", "view-list-symbolic") for n in TODO_TOOLS},
}
TOOL_ICON = ("preferences-system-symbolic", "applications-system-symbolic")


def edit_stats(name, inp):
    """(lignes ajoutées, lignes retirées) d'un Edit, MultiEdit ou Write ; None pour un autre outil."""
    import difflib
    inp = inp if isinstance(inp, dict) else {}
    if name == "Edit":
        pairs = [(inp.get("old_string"), inp.get("new_string"))]
    elif name == "MultiEdit":
        pairs = [(e.get("old_string"), e.get("new_string")) for e in inp.get("edits") or []
                 if isinstance(e, dict)]
    elif name == "Write":
        pairs = [("", inp.get("content"))]
    else:
        return None
    pairs = [(a, b) for a, b in pairs if isinstance(a, str) and isinstance(b, str)]
    if not pairs:
        return None
    added = removed = 0
    for old, new in pairs:
        ops = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
        for op, i1, i2, j1, j2 in ops.get_opcodes():
            if op != "equal":
                removed += i2 - i1
                added += j2 - j1
    return added, removed


def tool_line(item):
    """Ligne d'un appel d'outil : icône, nom, détail (lien si c'est un fichier), lignes
    ajoutées et retirées d'une modification ; line.indicator reçoit l'état (en cours, fini)."""
    inp = item.get("input") if isinstance(item.get("input"), dict) else {}
    name = str(item.get("name"))
    detail, path = tool_detail(inp), inp.get("file_path") or inp.get("notebook_path")
    if name in TODO_TOOLS:
        detail = todo_detail(name, inp)
    line = Gtk.Box(spacing=8, css_classes=["toolline"])
    icon = Gtk.Image(gicon=Gio.ThemedIcon.new_from_names(list(TOOL_ICONS.get(name, TOOL_ICON))),
                     css_classes=["tool-icon"])
    line.append(icon)
    line.append(Gtk.Label(label=name, css_classes=["tool-name"]))
    markup = esc(detail)
    if isinstance(path, str) and path:
        markup = f'<a href="{esc(file_href(path))}">{markup}</a>'
    lbl = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["tool-detail"],
                    tooltip_text=detail or None)
    try:
        Pango.parse_markup(re.sub(r"</?a\b[^>]*>", "", markup), -1, "\0")
        lbl.set_markup(label_links(markup))
    except GLib.Error:
        lbl.set_text(detail)
    lbl.connect("activate-link", on_link)
    line.append(lbl)
    stats = edit_stats(name, inp)
    if stats and any(stats):
        if stats[0]:
            line.append(Gtk.Label(label=f"+{stats[0]}", css_classes=["diff-add"]))
        if stats[1]:
            line.append(Gtk.Label(label=f"−{stats[1]}", css_classes=["diff-del"]))
    line.indicator = Gtk.Box(valign=Gtk.Align.CENTER)
    line.append(line.indicator)
    line.detail = lbl
    return line


IMG_MAX = (360, 260)


def texture_from(data):
    """Texture GDK à partir d'octets ou de base64 (None si illisible)."""
    try:
        raw = base64.b64decode(data) if isinstance(data, str) else data
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(raw))
    except (GLib.Error, ValueError, TypeError):
        return None


def thumbnail(texture, max_w, max_h):
    """Texture réduite (Gtk.Picture réclame toujours la taille de son image)."""
    w, h = texture.get_width(), texture.get_height()
    k = min(1, max_w / max(w, 1), max_h / max(h, 1))
    if k == 1:
        return texture
    loader = GdkPixbuf.PixbufLoader()
    loader.write(texture.save_to_png_bytes().get_data())
    loader.close()
    pb = loader.get_pixbuf().scale_simple(max(1, int(w * k)), max(1, int(h * k)),
                                          GdkPixbuf.InterpType.BILINEAR)
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(pb.save_to_bufferv("png", [], [])[1]))


def image_widget(texture, max_size=IMG_MAX):
    """Vignette ; un clic copie l'image complète dans le presse-papier."""
    thumb = thumbnail(texture, *max_size)
    pic = Gtk.Picture(paintable=thumb, can_shrink=False, css_classes=["thumb"],
                      tooltip_text="Cliquer pour copier l'image")
    pic.set_size_request(thumb.get_width(), thumb.get_height())
    pic.set_halign(Gtk.Align.START)
    pic.set_valign(Gtk.Align.START)
    click = Gtk.GestureClick()
    click.connect("released", lambda g, *_: g.get_widget().get_clipboard().set(texture))
    pic.add_controller(click)
    pic.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
    # Sans cette boîte, GTK élargit l'image à la hauteur de sa voisine la plus haute.
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START,
                     halign=Gtk.Align.START)
    holder.append(pic)
    return holder


def images_row(images):
    row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                      column_spacing=6, row_spacing=6, homogeneous=False)
    for img in images:
        tex = None if img.get("omitted") else texture_from(img.get("data", ""))
        row.append(image_widget(tex) if tex else
                   Gtk.Label(label="🖼 image (trop lourde pour l'aperçu)", css_classes=["sid"]))
    return row


def plan_decision_text(decision):
    """Texte de la pastille d'un plan tranché : decision = {"ok", "mode", "text",
    "interrupted", "label"} (mode : celui choisi pour l'exécution, connu seulement quand la
    réponse est partie d'ici ; label : autre issue, demande expirée ou annulée)."""
    if decision.get("label"):
        return decision["label"]
    if decision.get("interrupted"):
        return "⏹ Interrompu"
    if not decision.get("ok"):
        return "✗ Refusé"
    mode = MODE_LABELS.get(decision.get("mode"))
    return f"✓ Validé · {mode.lower()}" if mode else "✓ Validé"


def show_plan_decision(box, decision):
    """Ajoute à un plan (plan_widget) la décision prise : pastille dans l'en-tête et, pour
    un refus, la remarque transmise à Claude. Visibles même plan replié."""
    badge = Gtk.Label(label=plan_decision_text(decision), ellipsize=Pango.EllipsizeMode.END,
                      css_classes=["plan-decision", "ok" if decision.get("ok") else "refused"])
    if decision.get("tip"):
        badge.set_tooltip_text(decision["tip"])
    box.plan_head.insert_child_after(badge, box.plan_head.get_first_child())
    text = " ".join(str(decision.get("text") or "").split())
    if text and not decision.get("ok"):
        remark = Gtk.Label(label=f"Remarque : {text}", xalign=0, wrap=True, lines=2,
                           ellipsize=Pango.EllipsizeMode.END, css_classes=["sid"],
                           tooltip_text=str(decision["text"]))
        box.insert_child_after(remark, box.plan_head)


def plan_widget(plan, path=None, box=None, decision=None):
    """Plan soumis par Claude (ExitPlanMode), rendu en Markdown dans un cadre (box, ou un nouveau).

    Le bouton « volet » appelle open_plan(plan, path) de la fenêtre ; tant que le
    volet montre ce plan, la fenêtre masque box.plan_body (repli de la carte).
    decision : la réponse donnée au plan, s'il a été tranché (show_plan_decision).
    """
    box = box or Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("card")
    box.add_css_class("plan")
    head = Gtk.Box(spacing=6)
    # Le titre ne s'étire pas : la pastille de la décision le suit, les boutons vont à droite.
    head.append(Gtk.Label(label="📋 Plan proposé par Claude", xalign=0,
                          css_classes=["card-title"]))
    head.append(Gtk.Box(hexpand=True))
    side = Gtk.Button(icon_name="view-dual-symbolic", css_classes=["flat"],
                      tooltip_text="Ouvrir dans le volet")

    def open_side(b):
        opener = getattr(b.get_root(), "open_plan", None)
        if opener:
            opener(plan, path if isinstance(path, str) else None)
    side.connect("clicked", open_side)
    head.append(side)
    head.append(copy_button(plan, "Copier le plan (Markdown)"))
    box.append(head)
    box.plan_text, box.plan_side, box.plan_head = plan, side, head
    box.plan_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    if isinstance(path, str) and path:
        box.plan_body.append(make_label(f'<a href="{esc(file_href(path))}">{esc(path)}</a>', "sid"))
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card-body"])
    render_md(plan or "(plan vide)", body)
    box.plan_body.append(body)
    box.append(box.plan_body)
    if decision:
        show_plan_decision(box, decision)
    return box


def agent_message_widget(item):
    """Message relayé par un agent (rapport d'un sous-agent en arrière-plan…) : replié par
    défaut, avec sa première ligne en aperçu."""
    text = item.get("text") or ""
    who = str(item.get("from") or "?")
    kind = "↩ Rapport du sous-agent" if item.get("report") else "✉ Message de l'agent"
    first = next((l for l in text.splitlines() if l.strip()), "")
    first = re.sub(r"[*_`#>]+", "", first).strip()
    head = Gtk.Box(spacing=6)
    head.append(Gtk.Label(label=kind, css_classes=["subagent-kind"]))
    head.append(Gtk.Label(label=who[:8], css_classes=["sid"], tooltip_text=who))
    head.append(Gtk.Label(label=first, xalign=0, hexpand=True, css_classes=["sid"],
                          ellipsize=Pango.EllipsizeMode.END))
    return folded_widget(head, text)


def compact_widget(item):
    """Résumé écrit par claude en compactant la conversation : comme dans le terminal, une
    simple mention, et le résumé (dont claude repart) au dépliage."""
    head = Gtk.Box(spacing=6)
    head.append(Gtk.Label(label="⟲ Conversation compactée", css_classes=["subagent-kind"]))
    head.append(Gtk.Label(label="la suite repart de ce résumé", xalign=0, hexpand=True,
                          css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END))
    return folded_widget(head, item.get("text") or "")


def folded_widget(head, text):
    """Texte replié sous head ; son Markdown n'est rendu qu'au dépliage."""
    exp = Gtk.Expander(label_widget=head)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["subagent-body"])
    copy = copy_button(text, "Copier le message (Markdown)")
    copy.set_halign(Gtk.Align.END)
    body.append(copy)
    exp.set_child(body)

    def expanded(e, _pspec):
        if e.get_expanded() and not getattr(e, "rendered", False):
            e.rendered = True
            render_md(text or "(vide)", body)
    exp.connect("notify::expanded", expanded)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["subagent"])
    box.append(exp)
    return box


def message_widget(item, on_fork=None):
    """Widget d'un message ; on_fork(item, before, widget) : action « copier la conversation
    à partir d'ici », proposée si le message porte un point de copie (uuid, parent)."""
    role = item["role"]
    if item.get("via") == "agent":
        return agent_message_widget(item)
    if role == "compact":
        return compact_widget(item)
    if role == "tool":
        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
        if item.get("name") == "ExitPlanMode" and isinstance(inp.get("plan"), str):
            decision = item.get("decision") if isinstance(item.get("decision"), dict) else None
            return plan_widget(inp["plan"], inp.get("planFilePath"), decision=decision)
        return tool_line(item)
    if role == "user" and (item.get("text") or "").startswith("[Request interrupted by user"):
        return make_label("⏹ Interrompu", "notice")  # noté par claude après une interruption
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("bubble")
    box.add_css_class(role)
    box.set_halign(Gtk.Align.END if role == "user" else Gtk.Align.FILL)
    if item.get("via") == "remote":
        box.append(Gtk.Label(label="↪ via Remote Control", xalign=0, css_classes=["sid"]))
    elif item.get("queued"):
        box.append(Gtk.Label(label="↪ envoyé pendant la réponse", xalign=0, css_classes=["sid"]))
    if item.get("images"):
        box.append(images_row(item["images"]))
    if item.get("text"):
        render_md(item["text"], box)
    else:
        return box
    # Boutons « copier le Markdown » (et « copie de la conversation ») en surimpression,
    # visibles au survol.
    ov = Gtk.Overlay(child=box, halign=box.get_halign(), css_classes=["msg", role])
    actions = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START)
    point = item.get("parent") if role == "user" else item.get("uuid")
    if on_fork and point and not item.get("via"):
        fork = Gtk.Button(icon_name="claude-hub-fork-symbolic", css_classes=["flat", "copy", "copy-md"],
                          tooltip_text="Reprendre ce message dans une copie de la conversation"
                          if role == "user" else "Continuer dans une copie de la conversation, arrêtée ici")
        # ov retrouvé depuis le bouton : le capturer empêcherait sa libération (widgets.weak_handler)
        fork.connect("clicked", lambda b: on_fork(item, role == "user", b.get_ancestor(Gtk.Overlay)))
        actions.append(fork)
    btn = copy_button(item["text"], "Copier le message (Markdown)")
    btn.add_css_class("copy-md")
    actions.append(btn)
    ov.add_overlay(actions)
    return ov


CSS = """
/* Textes sélectionnables : pas de curseur quand on clique dedans. */
label { caret-color: transparent; }
.mono { font-family: monospace; font-size: 0.92em; }
.dim { opacity: 0.65; }
.bubble { border-radius: 12px; padding: 8px 12px; margin: 2px 8px; }
.bubble.user { background: rgba(61, 174, 233, 0.22); margin-left: 80px; }
.bubble.assistant { background: rgba(128, 128, 128, 0.13); margin-right: 40px; }
.codeblock { border-radius: 8px; background: rgba(128, 128, 128, 0.15); }
.codehead { padding: 2px 4px 0 10px; }
.copy { min-height: 20px; min-width: 20px; padding: 2px; }
.code { font-family: monospace; font-size: 0.92em; padding: 2px 10px 8px 10px; }
/* Markdown (mdview.py) : texte sur le fond du message, sans curseur. */
textview.md, textview.md > text { background: none; }
.mdtable { border: 1px solid rgba(128, 128, 128, 0.33); border-radius: 6px; }
.th, .td { padding: 4px 10px; border-bottom: 1px solid rgba(128, 128, 128, 0.22); }
.th { font-weight: bold; background: rgba(128, 128, 128, 0.15); }
.td.odd { background: rgba(128, 128, 128, 0.07); }
.card { border-radius: 10px; padding: 10px 12px; margin: 4px 8px;
        background: rgba(227, 162, 59, 0.10); border: 1px solid rgba(227, 162, 59, 0.45); }
.card.plan { background: rgba(61, 174, 233, 0.08); border-color: rgba(61, 174, 233, 0.45); }
.card.done { background: none; border: none; padding: 0 12px; margin: 0 20px; font-size: 0.9em; }
.card.done.ok { color: #3fb950; }
.card.done.refused { color: #e5534b; }
.plan-summary.ok { color: #3fb950; }
.plan-summary.refused { color: #e5534b; }
.plan-decision { border-radius: 10px; padding: 0 8px; font-size: 0.9em; font-weight: bold; }
.plan-decision.ok { color: #2f9e44; background: rgba(63, 185, 80, 0.14); }
.plan-decision.refused { color: #d6453d; background: rgba(229, 83, 75, 0.14); }
.card-title { font-weight: bold; }
.card-question { margin-top: 4px; }
.questions { margin-bottom: 4px; }
.question-option { margin-left: 8px; }
.subagent { margin: 0 8px; }
.subagent-kind { font-family: monospace; font-size: 0.85em; font-weight: bold; opacity: 0.8; }
.agent-mark { font-weight: bold; opacity: 0.75; }
.agent-mark.idle { opacity: 0.5; }
.agent-open { min-height: 0; min-width: 0; padding: 2px; }
.subagent-body { border-left: 2px solid rgba(128, 128, 128, 0.3); padding-left: 8px; margin: 4px 0 4px 6px; }
/* Appels d'outils : icône, nom, détail ; suites de 3 appels ou plus repliées en « N étapes ». */
.toolline { padding: 1px 0; font-size: 0.92em; }
.toolline > .tool-icon { opacity: 0.7; }
.tool-name { font-weight: bold; opacity: 0.85; }
.tool-detail { font-family: monospace; font-size: 0.95em; opacity: 0.7; }
.diff-add, .diff-del { font-family: monospace; font-size: 0.95em; }
.diff-add { color: #3fb950; }
.diff-del { color: #e5534b; }
.tool-running { color: #d4860b; -gtk-icon-size: 12px; }
.tool-ok { color: #3fb950; -gtk-icon-size: 12px; }
.tool-failed { color: #e5534b; -gtk-icon-size: 12px; }
.msgs > .toolline, .msgs > .toolcall, .msgs > .toolrun { margin: 0 8px; }
.toolrun > box > .toolline { margin: 0; }
.steps-count { font-weight: bold; font-size: 0.92em; opacity: 0.85; }
.steps-summary { font-family: monospace; font-size: 0.85em; opacity: 0.65; }
.tool-body { margin: 4px 0 4px 18px; }
.tool-error { color: #e5534b; }
/* Commandes « ! » (shell.py), en rose comme le mode bash du terminal de Claude Code */
.msgs > .shell-block { margin: 0 8px; }
.shell-mark { font-family: monospace; font-weight: bold; color: #c2408f; }
.shell-cmd { font-family: monospace; font-size: 0.95em; }
.composer.shell-mode { border-color: #c2408f; }
.notice { opacity: 0.7; font-style: italic; margin: 4px 8px; }
.error { color: #e5534b; margin: 4px 8px; }
.sid { font-size: 0.85em; opacity: 0.6; }
.thumb { border-radius: 6px; }
.snippet { font-size: 0.9em; opacity: 0.85; margin-top: 2px; }
.hit { outline: 2px solid #e3a23b; outline-offset: 2px; }
.archived { opacity: 0.55; }
.badge { font-size: 0.75em; font-weight: bold; padding: 0 6px; border-radius: 6px;
         color: #a05c9c; background: rgba(160, 92, 156, 0.16); }
.badge.cloud { color: #d97757; background: rgba(217, 119, 87, 0.18); }
.msg { margin: 2px 8px; }
.msg.user { margin-left: 80px; }
.msg.assistant { margin-right: 40px; }
.msg > .bubble { margin: 0; }
.msg.user > .bubble { padding-right: 40px; }
.copy-md { margin: 4px; opacity: 0; background: alpha(black, 0.25); border-radius: 6px; }
.msg:hover .copy-md { opacity: 0.9; }
/* Heure des éléments du fil (feedtime.py) : colonne à droite, alignée pour tous ; les
   suites d'étapes (.toolrun) ont déjà leur marge. */
.msgs > .timed { margin: 0 8px; }
.timed > * { margin-left: 0; margin-right: 0; }
.timed > .msg.assistant, .timed > .bubble.assistant { margin-right: 0; }
.msg-time { min-width: 40px; font-size: 0.85em; opacity: 0.6; font-feature-settings: "tnum"; }
.msg-time.on-bubble { margin-top: 10px; }
.feed-day { margin: 6px 8px 2px; }
.feed-day > label { font-size: 0.78em; font-weight: bold; letter-spacing: 1px; opacity: 0.7; }
.after-fork { opacity: 0.35; }
.fork-bar { border-radius: 6px; padding: 4px 8px; background: rgba(61, 174, 233, 0.15); }
.copy.flat { padding: 0 6px; }
.todo-list { padding: 2px 0 4px 18px; }
.todo-mark { min-width: 14px; }
.todo.in_progress { font-weight: bold; }
.todo.completed { opacity: 0.55; }
.todo.cancelled { opacity: 0.55; text-decoration-line: line-through; }
.viewer { border-left: 1px solid rgba(128, 128, 128, 0.25); }
.side { border-left: 1px solid rgba(128, 128, 128, 0.25); }
.side .viewer { border-left: none; }
.viewer-md { padding: 8px 14px; }
.viewer-code { font-family: monospace; font-size: 0.92em; }
textview.note-input { font-family: monospace; font-size: 0.92em; }
.tagchip, button.tagchip { font-size: 0.75em; padding: 0 6px; border-radius: 6px; min-height: 0;
                           min-width: 0; color: #3d8fd1; background: rgba(61, 174, 233, 0.15); }
button.tagchip:hover { background: rgba(61, 174, 233, 0.3); }
button.chip-close { min-height: 0; min-width: 0; padding: 0; -gtk-icon-size: 10px; }

/* Barre latérale : liste des conversations */
.sidebar-list { border-right: 1px solid rgba(128, 128, 128, 0.25); }
.sessions { background: none; }
.sessions > row.session { margin: 1px 6px; padding: 7px 10px; border-radius: 6px; background: none; }
.sessions > row.session:selected { background-color: rgba(61, 174, 233, 0.18); color: inherit; }
.sessions > row.session:hover:not(:selected) { background-color: rgba(128, 128, 128, 0.08); }
.day-header, .section-title { font-size: 0.78em; font-weight: bold; letter-spacing: 1px; opacity: 0.7; }
.day-header { padding: 10px 16px 4px; }
.section-title { padding: 6px 10px 2px; }
row.strong .row-title { font-weight: bold; }
.row-meta { font-size: 0.9em; }
.row-folder { opacity: 0.7; }
.row-folder.missing { color: #e5534b; opacity: 1; }
.row-draft { font-style: italic; opacity: 0.7; }
.row-note { -gtk-icon-size: 12px; color: #3d8fd1; }
button.has-note { color: #3d8fd1; }
.row-time { font-size: 0.9em; opacity: 0.7; font-feature-settings: "tnum"; }
.row-time.unread { color: #3d8fd1; font-weight: bold; opacity: 1; }
.row-time.busy { color: #d4860b; font-weight: bold; opacity: 1; }
.row-time.action { color: #e5534b; font-weight: bold; opacity: 1; }
.row-state { min-width: 16px; min-height: 18px; }
.row-state image { -gtk-icon-size: 14px; }
.dot { min-width: 8px; min-height: 8px; border-radius: 99px; background: #3d8fd1; }
.st-busy { color: #d4860b; }
.st-action { color: #e5534b; }
.st-idle { color: #3fb950; }
.st-draft { opacity: 0.7; }
button.row-btn { min-width: 24px; min-height: 24px; padding: 0; margin-top: -4px; }
.hostchip { font-size: 0.85em; padding: 0 6px; border-radius: 5px; background: rgba(128, 128, 128, 0.18); }
.hostchip image { opacity: 0.85; }
.count { font-size: 0.8em; font-weight: bold; min-width: 18px; min-height: 18px; padding: 0 5px;
         border-radius: 9px; }
.count.todo { background: #1d6a96; color: white; }
.count.busy { background: rgba(227, 162, 59, 0.22); color: #d4860b; }
.segments > togglebutton { padding: 0 6px; min-height: 28px; }
.segments > togglebutton:checked { font-weight: bold; }
.list-footer { padding: 6px 12px; border-top: 1px solid rgba(128, 128, 128, 0.25); font-size: 0.9em; }
.list-footer > label { opacity: 0.7; }

/* Vues enregistrées (pastilles sous la recherche) */
button.viewchip { font-size: 0.85em; min-height: 0; padding: 1px 8px; border-radius: 10px; box-shadow: none;
                  background: rgba(128, 128, 128, 0.12); border: 1px solid transparent; }
button.viewchip:hover { background: rgba(128, 128, 128, 0.22); }
button.viewchip.active { color: #3d8fd1; font-weight: bold; background: rgba(61, 174, 233, 0.16);
                         border-color: rgba(61, 174, 233, 0.5); }
button.viewsave { font-size: 0.85em; min-height: 0; padding: 1px 6px; opacity: 0.75; }
button.viewsave:hover { opacity: 1; }

/* Sélecteur rapide (Ctrl+K) */
.switcher > contents { padding: 10px; }
.switcher-list > row.session:focus { outline: none; }

/* Menus (hôtes, choix de la saisie, dossiers) */
.menu-pop > contents { padding: 6px; }
.choices { background: none; }
.choices > row { padding: 6px 10px; border-radius: 5px; }
.choice-check { color: #3d8fd1; }
.choice-help { font-size: 0.9em; opacity: 0.65; }
.choices.modes .choice-label { font-weight: bold; }
.menu-row { padding: 0 6px; min-height: 30px; }
.menu-kind { font-size: 0.9em; opacity: 0.65; }

/* En-tête de la conversation */
.chat-head { padding: 10px 16px 8px 20px; border-bottom: 1px solid rgba(128, 128, 128, 0.25); }
.chat-title { font-size: 1.2em; font-weight: bold; }
.head-meta { font-size: 0.9em; }
.head-meta > label { opacity: 0.7; }
/* Contexte rempli (jauge) et coût de la dernière réponse */
.usage > label, .usage > box > label { opacity: 0.7; }
levelbar.context { min-width: 44px; }
levelbar.context trough, levelbar.context block { min-height: 6px; border-radius: 3px; border: none;
                                                  box-shadow: none; }
levelbar.context block.empty { background: rgba(128, 128, 128, 0.25); }
levelbar.context block.ok { background: #3d8fd1; }
levelbar.context block.warn { background: #d4860b; }
levelbar.context block.full { background: #e5534b; }
.usage label.context-text.warn { color: #d4860b; opacity: 1; }
.usage label.context-text.full { color: #e5534b; opacity: 1; font-weight: bold; }
/* Volet « Fichiers modifiés » */
.change-file { padding: 3px 4px; }

/* Zone de saisie : un seul cadre (activité, texte, réglages, envoi) */
.composer { border: 1px solid alpha(currentColor, 0.25); border-radius: 10px; background: @theme_base_color; }
textview.composer-input, textview.composer-input > text { background: none; }
.placeholder { opacity: 0.5; padding: 10px 12px; }
.composer-bar { padding: 4px 8px 8px; }
.composer-new { padding: 8px 10px 0 12px; font-size: 0.95em; }
.composer-new > label { opacity: 0.7; }
.hint { font-size: 0.9em; opacity: 0.7; }
/* Ce qui tourne et ce qui attend (activity.py) : à droite de la saisie, ou au-dessus.
   Le trait de séparation est une ombre de 1px des deux côtés : le cadre de la saisie
   (overflow caché) masque celle qui tombe sur son bord. */
.activity-side { background: rgba(128, 128, 128, 0.07);
                 box-shadow: -1px 0 alpha(currentColor, 0.15), 0 1px alpha(currentColor, 0.15); }
.activity { padding: 4px 0; }
.act-row { padding: 5px 6px 5px 12px; }
.act-row.sub { padding-left: 32px; }
.act-row.claude { background: rgba(227, 162, 59, 0.08); }
.act-row.request { background: rgba(229, 83, 75, 0.08); }
.act-row.queued { background: rgba(61, 174, 233, 0.10); }
.act-icon { min-width: 16px; min-height: 18px; }
.act-icon image { -gtk-icon-size: 14px; }
.act-icon spinner { min-width: 14px; min-height: 14px; color: #d4860b; }
.act-label { font-weight: bold; }
.act-row.claude .act-label { color: #d4860b; }
.act-row.request .act-label, .act-row.request .act-icon { color: #e5534b; }
.act-row.queued .act-label, .act-row.queued .act-icon { color: #3d8fd1; }
.act-row.agent .act-icon { color: #d4860b; }
.act-detail { font-size: 0.92em; opacity: 0.85; }
.act-detail.mono { font-family: monospace; font-size: 0.88em; }
.act-sub { font-size: 0.88em; opacity: 0.65; }
.act-time { font-size: 0.88em; opacity: 0.7; font-feature-settings: "tnum"; }
button.act-btn { min-width: 24px; min-height: 24px; padding: 0; margin-top: -3px; }
button.stop.act-stop { min-width: 26px; min-height: 22px; padding: 0; margin-top: -2px; }
.todos { padding: 5px 6px 5px 6px; }
.todos expander-widget > box > title { padding: 0; }
button.stop { color: #e5534b; border: 1px solid rgba(229, 83, 75, 0.55); background: none;
              box-shadow: none; min-height: 24px; padding: 0 10px; font-weight: bold; }
button.stop:hover { background: rgba(229, 83, 75, 0.12); }
button.stop image { -gtk-icon-size: 10px; }
menubutton.chip > button { min-height: 26px; padding: 0 6px 0 9px; background: none; box-shadow: none;
                           border: 1px solid alpha(currentColor, 0.2); border-radius: 6px; font-size: 0.92em; }
menubutton.chip > button:hover { background: alpha(currentColor, 0.08); }
menubutton.chip.plan > button { color: #3d8fd1; background: rgba(61, 174, 233, 0.15);
                                border-color: rgba(61, 174, 233, 0.55); font-weight: bold; }
menubutton.chip.path > button label { font-family: monospace; font-size: 0.95em; }
.chevron { opacity: 0.6; -gtk-icon-size: 12px; }
button.active { background-image: none; background-color: alpha(currentColor, 0.14); }

/* Accueil : à traiter, en cours */
.home { padding: 28px 20px 12px; }
.home-title { font-size: 1.15em; font-weight: bold; }
.home-card { padding: 12px 14px; border-radius: 10px; background: rgba(128, 128, 128, 0.08);
             border: 1px solid rgba(128, 128, 128, 0.25); }
button.home-card { font-weight: normal; }
.home-card-title { font-weight: bold; font-size: 1.05em; }
.card-kind { font-size: 0.9em; font-weight: bold; }
.card-kind.action { color: #e5534b; }
.card-kind.unread { color: #3d8fd1; }
.card-kind.busy { color: #d4860b; }
.plan-box { padding: 8px 12px; border-radius: 8px; background: rgba(61, 174, 233, 0.08);
            border: 1px solid rgba(61, 174, 233, 0.30); }
.excerpt { font-size: 0.95em; opacity: 0.8; }
button.link-btn { background: none; box-shadow: none; border-color: transparent; color: #3d8fd1; }
button.link-btn:hover { background: rgba(61, 174, 233, 0.1); }
.home-empty { opacity: 0.65; }
"""

# Thème sombre : les teintes des maquettes (Breeze sombre). Le thème clair garde les
# teintes moyennes ci-dessus, lisibles sur les deux fonds.
DARK_CSS = """
.sessions > row.session:selected { background-color: #1b313e; }
.dot { background: #3daee9; }
.row-time.unread { color: #3daee9; }
.row-time.busy, .card-kind.busy, .act-row.claude .act-label, .act-row.agent .act-icon { color: #f0b85a; }
.st-busy, .act-icon spinner, .tool-running { color: #e3a23b; }
.row-time.action, .st-action, .card-kind.action, .act-row.request .act-label, .act-row.request .act-icon,
.diff-del, .tool-failed, button.stop, .row-folder.missing { color: #f0716a; }
.card-kind.unread, .choice-check { color: #3daee9; }
.hostchip { background: rgba(255, 255, 255, 0.08); color: #d0d4d8; }
.tagchip, button.tagchip, button.link-btn { color: #6cc0f0; }
.act-row.queued .act-label, .act-row.queued .act-icon { color: #8fd0f4; }
.composer { border-color: #464b51; }
.shell-mark { color: #f07dc0; }
.composer.shell-mode { border-color: #f07dc0; }
.home-card { background: #26292d; border-color: #3a3f45; }
menubutton.chip > button { border-color: #3a3f45; color: #dfe3e7; }
menubutton.chip.plan > button { color: #8fd0f4; }
.count.busy { color: #f0b85a; }
levelbar.context block.ok { background: #3daee9; }
levelbar.context block.warn { background: #e3a23b; }
.usage label.context-text.warn { color: #f0b85a; }
levelbar.context block.full { background: #f0716a; }
.usage label.context-text.full { color: #f0716a; }
button.viewchip.active { color: #8fd0f4; }
.plan-decision.ok { color: #56d364; }
.plan-decision.refused { color: #f0716a; }
"""

# Breeze : curseur de défilement de 6px + bordures 2×5px + marges 2×-9px = -2px, d'où
# « GtkGizmo (slider) reported min width -2 ». 8px ramène le minimum à 0 ; la largeur
# affichée reste celle de la gouttière (trough), donc rien ne change à l'écran.
# Liens : Breeze les colore en @theme_unfocused_selected_bg_color_breeze quand la fenêtre
# est inactive (bleu très sombre, illisible sur fond sombre), et GTK 4.20 ne remet pas à
# jour les liens d'un label créé fenêtre inactive : ils restaient sombres après le retour
# du focus (fil rechargé en fin de réponse pendant qu'on était ailleurs). Même couleur
# dans les deux états, donc plus rien à rafraîchir.
BREEZE_CSS = """
scrollbar.vertical slider { min-width: 8px; }
scrollbar.horizontal slider { min-height: 8px; }
label link:link:backdrop { color: @link_color_breeze; }
label link:visited:backdrop { color: @link_visited_color_breeze; }
/* Breeze colore toute row.activatable survolée, y compris le choix affiché
   dans le bouton d'une liste déroulante : le texte semblait sélectionné. */
dropdown > button row.activatable { background: none; }
"""
