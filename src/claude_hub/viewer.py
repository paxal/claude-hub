"""Visionneuse des fichiers cités dans les conversations (volet latéral ou fenêtre).

Le fichier est lu sur l'hôte de la conversation par la commande `read` des
scripts distants : la visionneuse marche aussi pour les hôtes SSH.
"""
import base64
import posixpath
import shutil
import subprocess

from .gtk import Gdk, Gio, GLib, Gtk, Pango, in_thread
from .hosts import CLOUD, run_helper
from .render import copy_button, esc, highlight, lang_for, render_md, texture_from
from .widgets import weak_handler

MD_EXTS = (".md", ".markdown")
HIGHLIGHT_LIMIT = 300000  # au-delà, texte brut (pygments serait trop lent)


def decode_image(data):
    """Texture à partir d'une image en base64. Les formats que GDK ne lit pas
    (WebP, AVIF… selon les modules installés) passent par ImageMagick s'il est là."""
    tex = texture_from(data)
    if tex:
        return tex
    tool = shutil.which("magick") or shutil.which("convert")
    if not tool:
        return None
    try:
        r = subprocess.run([tool, "-", "png:-"], input=base64.b64decode(data),
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return texture_from(r.stdout) if r.returncode == 0 and r.stdout else None


def prepare(res, path):
    """Hors de la boucle GTK : décode l'image, ou colore le texte (pygments est lent)."""
    if res.get("image"):
        res["texture"] = decode_image(res["image"])
    elif "text" in res and not res.get("path", path).lower().endswith(MD_EXTS):
        text = res["text"]
        res["lang"] = lang_for(res.get("path", path), text)
        res["markup"] = highlight(text, res["lang"]) if len(text) <= HIGHLIGHT_LIMIT else esc(text)
    return res


def resolve(path, base):
    """Chemin relatif -> chemin depuis `base` (dossier de la session ou du fichier)."""
    if path.startswith(("/", "~")) or not base:
        return path
    return posixpath.normpath(posixpath.join(base, path))


def shift_held(widget):
    kbd = widget.get_display().get_default_seat().get_keyboard()
    return bool(kbd and kbd.get_modifier_state() & Gdk.ModifierType.SHIFT_MASK)


def viewer_of(widget):
    """Visionneuse qui contient `widget` (lien cliqué dans un fichier Markdown), ou None."""
    while widget is not None and not isinstance(widget, FileViewer):
        widget = widget.get_parent()
    return widget


class FileViewer(Gtk.Box):
    def __init__(self, on_close=None, on_detach=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["viewer"])
        self.host = self.path = None
        self.line = self.end = None
        self.gen = 0
        head = Gtk.Box(spacing=4, margin_top=6, margin_start=8, margin_end=4, margin_bottom=4)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.where = Gtk.Label(xalign=0, css_classes=["sid"], selectable=True,
                               focusable=False, ellipsize=Pango.EllipsizeMode.START)
        names.append(self.title)
        names.append(self.where)
        head.append(names)
        reload = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"],
                            tooltip_text="Recharger", valign=Gtk.Align.CENTER)
        reload.connect("clicked", weak_handler(self, lambda v, _b: v.reload()))
        head.append(reload)
        self.copy_box = Gtk.Box(valign=Gtk.Align.CENTER)
        head.append(self.copy_box)
        self.launch_btn = Gtk.Button(icon_name="document-open-symbolic", css_classes=["flat"],
                                     tooltip_text="Ouvrir avec l'application par défaut",
                                     valign=Gtk.Align.CENTER)
        self.launch_btn.connect("clicked", weak_handler(self, lambda v, _b: Gtk.FileLauncher.new(
            Gio.File.new_for_path(v.path)).launch(v.get_root(), None, None)))
        head.append(self.launch_btn)
        if on_detach:
            detach = Gtk.Button(icon_name="window-new-symbolic", css_classes=["flat"],
                                tooltip_text="Ouvrir dans une fenêtre à part",
                                valign=Gtk.Align.CENTER)
            detach.connect("clicked", weak_handler(self, lambda v, _b: on_detach(v)))
            head.append(detach)
        if on_close:
            close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                               tooltip_text="Fermer", valign=Gtk.Align.CENTER)
            close.connect("clicked", weak_handler(self, lambda v, _b: on_close(v)))
            head.append(close)
        self.append(head)
        self.status = Gtk.Label(xalign=0, wrap=True, css_classes=["notice"], visible=False)
        self.append(self.status)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.append(self.scroll)

    def load(self, host, path, line=None, end=None):
        self.host, self.path, self.line, self.end = host, path, line, end
        self.reload()

    def reload(self):
        self.gen += 1
        gen, host, path = self.gen, self.host, self.path
        name = posixpath.basename(path.rstrip("/")) or path
        lines = (f"lignes {self.line}-{self.end}" if self.end and self.end > self.line
                 else f"ligne {self.line}") if self.line else ""
        self.title.set_markup(f"<b>{esc(name)}</b>" + (f"  <small>{lines}</small>" if lines else ""))
        self.where.set_text(path if host == "local" else f"{host}:{path}")
        self.where.set_tooltip_text(self.where.get_text())
        self.launch_btn.set_visible(False)
        self.set_status("Lecture…")
        if host == CLOUD:
            return self.set_status("Les fichiers des sessions cloud ne sont pas accessibles d'ici.")

        def done(res):
            if gen == self.gen:
                self.show(res)

        def fail(e):
            if gen == self.gen:
                self.set_status(f"Lecture impossible : {e}")
        in_thread(lambda: prepare(run_helper(host, "read", path), path), done, fail)

    def set_status(self, text):
        self.status.set_text(text or "")
        self.status.set_visible(bool(text))

    def show(self, res):
        self.path = res.get("path") or self.path  # chemin absolu, ~ développé
        self.where.set_text(self.path if self.host == "local" else f"{self.host}:{self.path}")
        while (child := self.copy_box.get_first_child()):
            self.copy_box.remove(child)
        ref = self.path + (f":{self.line}" if self.line else "")
        self.copy_box.append(copy_button(ref, f"Copier le chemin ({ref})"))
        self.launch_btn.set_visible(self.host == "local" and not res.get("error"))
        if res.get("error"):
            self.scroll.set_child(None)
            return self.set_status(f"{res['error'].capitalize()}.")
        self.set_status("Fichier tronqué : seul le début est affiché." if res.get("truncated")
                        else None)
        self.scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        if res.get("image"):
            self.show_image(res.get("texture"), res.get("type", "image"))
        elif self.path.lower().endswith(MD_EXTS):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["viewer-md"])
            render_md(res["text"], box)
            self.scroll.set_child(box)
        else:
            self.show_text(res["text"], res["lang"], res["markup"])

    def show_image(self, tex, kind):
        if not tex:
            self.scroll.set_child(None)
            return self.set_status(f"Format d'image non pris en charge ({kind}).")
        w, h = tex.get_width(), tex.get_height()
        pic = Gtk.Picture(paintable=tex, content_fit=Gtk.ContentFit.SCALE_DOWN,
                          valign=Gtk.Align.START, tooltip_text=f"{w} × {h} px — clic : taille réelle / ajustée")
        pic.set_cursor(Gdk.Cursor.new_from_name("zoom-in", None))

        def fit(viewer, pic, full):
            # Ajustée : la vue n'a pas de défilement, l'image est réduite pour tenir.
            pic.set_can_shrink(not full)
            pic.set_cursor(Gdk.Cursor.new_from_name("zoom-out" if full else "zoom-in", None))
            policy = Gtk.PolicyType.AUTOMATIC if full else Gtk.PolicyType.NEVER
            viewer.scroll.set_policy(policy, policy)
        click = Gtk.GestureClick()
        # image et visionneuse retrouvées depuis le contrôleur (widgets.weak_handler)
        click.connect("released", weak_handler(
            self, lambda v, g, *_a: fit(v, g.get_widget(), g.get_widget().get_can_shrink())))
        pic.add_controller(click)
        self.scroll.set_child(pic)
        fit(self, pic, False)
        self.where.set_text(f"{self.where.get_text()} · {w} × {h}")

    def show_text(self, text, lang, markup):
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                            css_classes=["viewer-code"], top_margin=6, bottom_margin=6,
                            left_margin=8, right_margin=8,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR if lang == "text" else Gtk.WrapMode.NONE)
        buf = view.get_buffer()
        try:
            buf.insert_markup(buf.get_end_iter(), markup, -1)
        except GLib.Error:
            buf.set_text(text)
        self.scroll.set_child(view)
        if not self.line:
            return
        start = buf.get_iter_at_line(self.line - 1)[1]
        stop = buf.get_iter_at_line(max(self.end or self.line, self.line))[1]
        if stop.get_line() < (self.end or self.line):  # dernière ligne du fichier
            stop = buf.get_end_iter()
        buf.apply_tag(buf.create_tag(None, paragraph_background="rgba(227, 162, 59, 0.25)"),
                      start, stop)
        mark = buf.create_mark(None, start, True)
        # Après la mise en page, sinon la hauteur des lignes n'est pas connue.
        GLib.idle_add(lambda: view.scroll_to_mark(mark, 0.0, True, 0.0, 0.3) and False)


class ViewerWindow(Gtk.ApplicationWindow):
    """Fichier ouvert dans une fenêtre à part (Maj+clic, ou bouton du volet)."""

    def __init__(self, app, host, path, line=None, end=None):
        super().__init__(application=app, default_width=820, default_height=900)
        self.viewer = FileViewer()
        self.set_child(self.viewer)
        self.viewer.load(host, path, line, end)
        self.viewer.title.connect("notify::label", weak_handler(self, lambda win, *_a: win.update_title()))
        self.update_title()

    def update_title(self):
        v = self.viewer
        self.set_title(f"{posixpath.basename(v.path)} — {v.host}")

    def open_file_link(self, lbl, path, line, end):
        follow_link(self.get_application(), lbl, path, line, end, None)


def follow_link(app, lbl, path, line, end, fallback):
    """Lien vers un fichier cliqué dans `lbl`.

    Dans une visionneuse, le chemin relatif part du dossier du fichier affiché et
    le lien s'ouvre à sa place ; ailleurs, `fallback(path, line, end)` s'en charge
    (volet de la fenêtre principale). Maj+clic ouvre une fenêtre à part.
    """
    v = viewer_of(lbl)
    if v is not None:
        host, path = v.host, resolve(path, posixpath.dirname(v.path))
        if shift_held(lbl):
            ViewerWindow(app, host, path, line, end).present()
        else:
            v.load(host, path, line, end)
    elif fallback:
        fallback(path, line, end, shift_held(lbl))
