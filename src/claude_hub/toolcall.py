"""Appels d'outils : la ligne « Outil  détail » se déplie sur la sortie de l'outil.

En direct, la sortie arrive dans le flux de claude -p (bloc tool_result d'un
message user) ; dans l'historique, elle est lue au premier dépliage (commande
« result » des scripts distants). Les appels consécutifs forment une suite
(ToolRun), repliée en « N étapes » à partir de trois.
"""
import re

from .gtk import Gtk, Pango, in_thread
from .changes import diff_block, input_hunks
from .render import code_block, copy_button, esc, make_label, tool_detail, tool_line
from .feedtime import Timed, time_label
from .listrow import BUSY_ICON
from .todos import TODO_TOOLS, todo_detail
from .widgets import weak_handler

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


def questions_box(inp):
    """Questions d'un AskUserQuestion, en lecture seule : intitulé, question, puis les
    choix et leur description. On répond à une question en attente par sa carte
    (RequestCard), ou dans le terminal si son Claude n'est pas relié à Remote Control."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["questions"])
    for q in inp.get("questions") or []:
        if not isinstance(q, dict):
            continue
        head = f"<b>{esc(str(q['header']))}</b> · " if q.get("header") else ""
        box.append(make_label(head + esc(str(q.get("question") or "")), "card-question"))
        mark = "☐" if q.get("multiSelect") else "○"
        for opt in q.get("options") or []:
            opt = opt if isinstance(opt, dict) else {"label": str(opt)}
            box.append(make_label(f"{mark} {esc(str(opt.get('label') or ''))}", "question-option"))
            if opt.get("description"):
                desc = make_label(esc(str(opt["description"])), "sid")
                desc.set_margin_start(22)
                box.append(desc)
    return box if box.get_first_child() else None


class ToolCall(Gtk.Box):
    """Ligne d'appel d'outil dépliable ; loader : fonction (appelée dans un thread) qui
    renvoie la sortie {"found", "text", "error", "length"}, None si elle n'arrive qu'en direct."""

    def __init__(self, item, loader=None, live=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["toolcall"])
        self.loader, self.loaded, self.output = loader, False, None
        self.name = item.get("name")
        # Un texte sur plusieurs lignes est mal mesuré dans l'en-tête d'un Gtk.Expander (il
        # déborde sur la ligne suivante) : une seule ligne, tronquée, complète en info-bulle.
        self.line = tool_line(item)
        if live:  # en attente de la sortie (roue fixe : une animation par outil coûte du CPU)
            self.line.indicator.append(Gtk.Image(icon_name=BUSY_ICON, css_classes=["tool-running"],
                                                 tooltip_text="En cours"))
        self.expander = Gtk.Expander(label_widget=self.line)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["tool-body"])
        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
        cmd = inp.get("command") if item.get("name") == "Bash" else None
        if isinstance(cmd, str) and (len(cmd) > 60 or "\n" in cmd.strip()):  # coupée sur la ligne
            self.body.append(code_block(cmd.strip(), "bash"))
        questions = questions_box(inp) if self.name == "AskUserQuestion" else None
        if questions:
            self.body.append(questions)
        # Edit, MultiEdit, Write : leur diff, avant la sortie (calculé au premier dépliage)
        self.edit = (item.get("name"), inp) if item.get("name") in ("Edit", "MultiEdit", "Write") else None
        self.state = Gtk.Label(label="Pas encore de sortie.", xalign=0, css_classes=["notice"])
        self.body.append(self.state)
        self.expander.set_child(self.body)
        self.expander.connect("notify::expanded", weak_handler(self, ToolCall.on_expanded))
        self.append(self.expander)

    def on_expanded(self, exp, _pspec):
        if exp.get_expanded() and self.edit:
            hunks, self.edit = input_hunks(*self.edit), None
            if hunks:
                self.body.prepend(diff_block(hunks))
        if exp.get_expanded() and self.loader and not self.loaded:
            self.loaded = True
            self.state.set_text("chargement…")
            in_thread(self.loader, self.show_loaded, self.load_failed)

    def show_loaded(self, res):
        if self.output is not None:  # arrivée entre-temps par le flux
            return
        if not res.get("found"):
            self.loaded = False  # outil en cours : on réessaiera au prochain dépliage
            if self.name == "AskUserQuestion":
                return self.state.set_text("Pas encore de réponse (question en attente, "
                                           "ou interrompue).")
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
        ind = self.line.indicator
        while (child := ind.get_first_child()):
            ind.remove(child)
        ind.append(Gtk.Image(icon_name="dialog-error-symbolic" if error else "object-select-symbolic",
                             css_classes=["tool-failed" if error else "tool-ok"],
                             tooltip_text="L'outil a échoué" if error else "Terminé"))
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


def step_summary(item):
    """« Read window.py », « Grep set_sort_func » : résumé d'un appel dans « N étapes »."""
    inp = item.get("input") if isinstance(item.get("input"), dict) else {}
    name = str(item.get("name"))
    path = inp.get("file_path") or inp.get("notebook_path")
    if isinstance(path, str) and path:
        detail = path.rstrip("/").rsplit("/", 1)[-1]
    elif name in TODO_TOOLS:
        detail = todo_detail(name, inp)
    else:
        detail = tool_detail(inp)
    if len(detail) > 32:
        detail = detail[:31] + "…"
    return f"{name} {detail}".strip()


class ToolRun(Gtk.Box):
    """Suite d'appels d'outils consécutifs du fil. Tant qu'elle est ouverte (réponse en
    direct), ses appels s'affichent tels quels ; fermée, elle se replie en « N étapes »
    s'il y en a au moins GROUP. Chaque appel porte son heure (« ts » de l'élément), la
    suite repliée celle du premier ; day : jour du dernier (séparateurs du fil)."""

    GROUP = 3

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["toolrun"])
        self.items, self.open, self.expander, self.day = [], True, None, None
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.body)

    def add(self, item, widget):
        self.items.append(item)
        if item.get("ts"):
            widget = Timed(widget, item["ts"])
            self.day = widget.day
        self.body.append(widget)

    def close(self):
        if not self.open:
            return
        self.open = False
        if len(self.items) < self.GROUP:
            return
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label=f"{len(self.items)} étapes", css_classes=["steps-count"]))
        summary = " · ".join(step_summary(it) for it in self.items[:12])
        head.append(Gtk.Label(label=summary, xalign=0, hexpand=True, css_classes=["steps-summary"],
                              ellipsize=Pango.EllipsizeMode.END))
        ts = self.items[0].get("ts")
        if ts:
            head.append(time_label(ts))
        self.remove(self.body)
        self.expander = Gtk.Expander(label_widget=head, child=self.body)
        self.append(self.expander)

    def reveal(self):
        """Déplie la suite (résultat de recherche dans l'un de ses appels)."""
        if self.expander:
            self.expander.set_expanded(True)
