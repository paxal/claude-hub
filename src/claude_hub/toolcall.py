"""Appels d'outils : la ligne « 🔧 Outil  détail » se déplie sur la sortie de l'outil.

En direct, la sortie arrive dans le flux de claude -p (bloc tool_result d'un
message user) ; dans l'historique, elle est lue au premier dépliage (commande
« result » des scripts distants).
"""
import re

from .gtk import Gtk, Pango, in_thread
from .render import code_block, copy_button, message_widget

RESULT_LIMIT = 100000  # comme les scripts distants


def result_text(content):
    """Texte d'un bloc tool_result (chaîne, ou liste de blocs texte et image)."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text") or ""))
        elif isinstance(b, dict) and b.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


class ToolCall(Gtk.Box):
    """Ligne d'appel d'outil dépliable ; loader : fonction (appelée dans un thread) qui
    renvoie la sortie {"found", "text", "error", "length"}, None si elle n'arrive qu'en direct."""

    def __init__(self, item, loader=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["toolcall"])
        self.loader, self.loaded, self.output = loader, False, None
        # Un texte sur plusieurs lignes est mal mesuré dans l'en-tête d'un Gtk.Expander (il
        # déborde sur la ligne suivante) : une seule ligne, tronquée, complète en info-bulle.
        title = message_widget(item)
        title.set_wrap(False)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_tooltip_text(title.get_text())
        self.expander = Gtk.Expander(label_widget=title)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["tool-body"])
        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
        cmd = inp.get("command") if item.get("name") == "Bash" else None
        if isinstance(cmd, str) and (len(cmd) > 60 or "\n" in cmd.strip()):  # coupée sur la ligne
            self.body.append(code_block(cmd.strip(), "bash"))
        self.state = Gtk.Label(label="Pas encore de sortie.", xalign=0, css_classes=["notice"])
        self.body.append(self.state)
        self.expander.set_child(self.body)
        self.expander.connect("notify::expanded", self.on_expanded)
        self.append(self.expander)

    def on_expanded(self, exp, _pspec):
        if exp.get_expanded() and self.loader and not self.loaded:
            self.loaded = True
            self.state.set_text("chargement…")
            in_thread(self.loader, self.show_loaded, self.load_failed)

    def show_loaded(self, res):
        if self.output is not None:  # arrivée entre-temps par le flux
            return
        if not res.get("found"):
            self.loaded = False  # outil en cours : on réessaiera au prochain dépliage
            return self.state.set_text("Pas encore de sortie (outil en cours ou interrompu).")
        self.show(res.get("text") or "", bool(res.get("error")), res.get("length") or 0)

    def load_failed(self, e):
        self.loaded = False
        self.state.set_text(f"Lecture impossible : {e}")

    def set_content(self, content, is_error):
        """Sortie reçue en direct (contenu du bloc tool_result)."""
        text = result_text(content)
        self.loaded = True
        self.show(text[:RESULT_LIMIT], is_error, len(text))

    def show(self, text, error, length):
        if self.output is not None:
            self.body.remove(self.output)
        self.state.set_visible(False)
        truncated, text = length > len(text), text.rstrip("\n")
        if error:  # « <tool_use_error>File does not exist.</tool_use_error> »
            text = re.sub(r"^\s*<tool_use_error>(.*)</tool_use_error>\s*$", r"\1", text, flags=re.S)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["codeblock"])
        head = Gtk.Box(css_classes=["codehead"])
        info = "erreur" if error else "sortie"
        if truncated:
            info += f" · tronquée ({length} caractères au total)"
        head.append(Gtk.Label(label=info, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["sid", "tool-error"] if error else ["sid"]))
        head.append(copy_button(text, "Copier la sortie"))
        box.append(head)
        lbl = Gtk.Label(label=text or "(aucune sortie)", xalign=0, selectable=True,
                        css_classes=["code", "tool-error"] if error else ["code"])
        # Sans hauteur minimale, une sortie tient dans quelques lignes (surtout si ses lignes
        # dépassent la largeur) : celle du texte, jusqu'à 400 px.
        scroll = Gtk.ScrolledWindow(child=lbl, propagate_natural_height=True, max_content_height=400)
        scroll.set_min_content_height(min(400, lbl.measure(Gtk.Orientation.VERTICAL, -1)[1]))
        box.append(scroll)
        self.output = box
        self.body.append(box)
