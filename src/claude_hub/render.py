"""Rendu des messages : Markdown (via mdview.py), coloration, images, CSS."""
import base64
import re
from urllib.parse import quote, unquote

from .gtk import GLib, Gdk, GdkPixbuf, Gtk, Pango
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
        t = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"<b>\2</b>", t)
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
        lbl.set_markup(markup)
    except GLib.Error:
        lbl.set_text(plain if plain is not None else markup)
    lbl.add_css_class(css)
    lbl.connect("activate-link", on_link)
    return lbl


def on_link(lbl, uri):
    """Les liens hub-file: vont à la fenêtre (open_file_link), les autres au navigateur."""
    if not uri.startswith(FILE_SCHEME):
        return False
    opener = getattr(lbl.get_root(), "open_file_link", None)
    if opener:
        opener(lbl, *parse_file_href(uri))
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
    detail = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
              or inp.get("description") or inp.get("url") or inp.get("query")
              or next((v for v in inp.values() if isinstance(v, str) and v.strip()), ""))
    return " ".join(str(detail).split())[:160]


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
    click.connect("released", lambda *_: pic.get_clipboard().set(texture))
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


def plan_widget(plan, path=None, box=None):
    """Plan soumis par Claude (ExitPlanMode), rendu en Markdown dans un cadre (box, ou un nouveau).

    Le bouton « volet » appelle open_plan(plan, path) de la fenêtre ; tant que le
    volet montre ce plan, la fenêtre masque box.plan_body (repli de la carte).
    """
    box = box or Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("card")
    box.add_css_class("plan")
    head = Gtk.Box(spacing=6)
    head.append(Gtk.Label(label="📋 Plan proposé par Claude", xalign=0, hexpand=True,
                          css_classes=["card-title"]))
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
    box.plan_text, box.plan_side = plan, side
    box.plan_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    if isinstance(path, str) and path:
        box.plan_body.append(make_label(f'<a href="{esc(file_href(path))}">{esc(path)}</a>', "sid"))
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card-body"])
    render_md(plan or "(plan vide)", body)
    box.plan_body.append(body)
    box.append(box.plan_body)
    return box


def agent_message_widget(item):
    """Message relayé par un agent (rapport d'un sous-agent en arrière-plan…) : replié par
    défaut, avec sa première ligne en aperçu ; le Markdown n'est rendu qu'au dépliage."""
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
    if role == "tool":
        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
        if item.get("name") == "ExitPlanMode" and isinstance(inp.get("plan"), str):
            return plan_widget(inp["plan"], inp.get("planFilePath"))
        detail, path = tool_detail(inp), inp.get("file_path") or inp.get("notebook_path")
        if item.get("name") in TODO_TOOLS:
            detail = todo_detail(item["name"], inp)
        markup = esc(detail)
        if isinstance(path, str) and path:
            markup = f'<a href="{esc(file_href(path))}">{markup}</a>'
        return make_label(f"🔧 {esc(str(item.get('name')))}  {markup}", "tool")
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
        fork.connect("clicked", lambda _b: on_fork(item, role == "user", ov))
        actions.append(fork)
    btn = copy_button(item["text"], "Copier le message (Markdown)")
    btn.add_css_class("copy-md")
    actions.append(btn)
    ov.add_overlay(actions)
    return ov


CSS = """
/* Textes sélectionnables : pas de curseur quand on clique dedans. */
label { caret-color: transparent; }
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
.card-title { font-weight: bold; }
.card-question { margin-top: 4px; }
.subagent { margin: 0 20px; }
.subagent-kind { font-family: monospace; font-size: 0.85em; font-weight: bold; opacity: 0.8; }
.agent-mark { font-weight: bold; opacity: 0.75; }
.agent-mark.idle { opacity: 0.5; }
.agent-open { min-height: 0; min-width: 0; padding: 2px; }
.subagent-body { border-left: 2px solid rgba(128, 128, 128, 0.3); padding-left: 8px; margin: 4px 0 4px 6px; }
.tool { font-family: monospace; font-size: 0.85em; opacity: 0.6; margin: 0 20px; }
.toolcall { margin: 0 20px; }
.toolcall .tool { margin: 0; }
.tool-body { margin: 4px 0 4px 18px; }
.tool-error { color: #e5534b; }
.notice { opacity: 0.7; font-style: italic; margin: 4px 8px; }
.error { color: #e5534b; margin: 4px 8px; }
.sid { font-size: 0.85em; opacity: 0.6; }
.live { color: #2e9e45; }
.state { font-size: 0.85em; font-weight: bold; }
.busy { color: #d4860b; }
.waiting { color: #e5534b; }
.state.unread { color: #3d8fd1; }
.state.draft { font-weight: normal; font-style: italic; opacity: 0.75; }
row.unread { background: rgba(61, 174, 233, 0.10); box-shadow: inset 3px 0 #3d8fd1; }
row.unread .row-title { font-weight: bold; }
.activity { font-size: 0.9em; font-weight: bold; padding: 2px 10px; border-radius: 10px; }
.activity.busy { background: rgba(227, 162, 59, 0.18); }
.activity.waiting { color: #e5534b; background: rgba(229, 83, 75, 0.15); }
.activity.idle { color: #3fb950; background: rgba(63, 185, 80, 0.15); }
.thumb { border-radius: 6px; }
.snippet { font-size: 0.85em; opacity: 0.8; margin-top: 2px; }
.hit { outline: 2px solid #e3a23b; outline-offset: 2px; }
.archived { opacity: 0.55; }
row.missing { background: rgba(229, 83, 75, 0.14); box-shadow: inset 3px 0 #e5534b; }
row.missing .sid { color: #e5534b; opacity: 0.9; }
.archive-btn { opacity: 0.5; min-width: 24px; min-height: 24px; padding: 2px; }
row:hover .archive-btn { opacity: 1; }
.badge.cloud { color: #d97757; background: rgba(217, 119, 87, 0.18); }
.msg { margin: 2px 8px; }
.msg.user { margin-left: 80px; }
.msg.assistant { margin-right: 40px; }
.msg > .bubble { margin: 0; }
.msg.user > .bubble { padding-right: 40px; }
.copy-md { margin: 4px; opacity: 0; background: alpha(black, 0.25); border-radius: 6px; }
.msg:hover .copy-md { opacity: 0.9; }
.after-fork { opacity: 0.35; }
.fork-bar { border-radius: 6px; padding: 4px 8px; background: rgba(61, 174, 233, 0.15); }
.copy.flat { padding: 0 6px; }
.queued { padding: 2px 4px 2px 10px; border-radius: 8px; opacity: 0.8;
          background: rgba(61, 174, 233, 0.12); }
.todos { padding: 4px 10px; border-radius: 10px; background: rgba(61, 174, 233, 0.08);
         border: 1px solid rgba(61, 174, 233, 0.30); }
.todo-list { padding: 2px 0 4px 18px; }
.todo-mark { min-width: 14px; }
.todo.in_progress { font-weight: bold; }
.todo.completed { opacity: 0.55; }
.todo.cancelled { opacity: 0.55; text-decoration-line: line-through; }
.thinking { padding: 6px 12px; margin-top: 4px; border-radius: 10px; font-weight: bold;
            color: #d4860b; background: rgba(227, 162, 59, 0.14); }
.viewer { border-left: 1px solid rgba(128, 128, 128, 0.25); }
.side { border-left: 1px solid rgba(128, 128, 128, 0.25); }
.side .viewer { border-left: none; }
.viewer-md { padding: 8px 14px; }
.viewer-code { font-family: monospace; font-size: 0.92em; }
.badge { font-size: 0.75em; font-weight: bold; padding: 0 6px; border-radius: 6px;
         color: #a05c9c; background: rgba(160, 92, 156, 0.16); }
.badge.host { color: inherit; background: rgba(128, 128, 128, 0.18); font-weight: normal; }
.tagchip, button.tagchip { font-size: 0.75em; padding: 0 6px; border-radius: 6px; min-height: 0;
                           min-width: 0; color: #3d8fd1; background: rgba(61, 174, 233, 0.15); }
button.tagchip:hover { background: rgba(61, 174, 233, 0.3); }
button.chip-close { min-height: 0; min-width: 0; padding: 0; -gtk-icon-size: 10px; }
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
