"""Sous-agents (outils Agent / Task) : un bloc dépliable par sous-agent dans le fil.

En direct, ses messages arrivent dans le flux de claude -p avec parent_tool_use_id
égal à l'id de l'appel d'outil qui l'a lancé ; dans l'historique, sa transcription
est lue à la demande (commande « agent » des scripts distants).
"""
from .gtk import Gtk, Pango, in_thread
from .render import message_widget

AGENT_TOOLS = ("Agent", "Task")


def tokens(usage):
    n = (usage or {}).get("total_tokens")
    return f"{n / 1000:.1f} k jetons" if isinstance(n, (int, float)) and n >= 1000 else (
        f"{n} jetons" if n else "")


def open_button(on_open):
    btn = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat", "agent-open"],
                     valign=Gtk.Align.START, tooltip_text="Voir dans le volet des sous-agents")
    btn.connect("clicked", lambda _b: on_open())
    return btn


def with_open_button(widget, on_open):
    """Widget du fil (SendMessage, rapport d'un agent) suivi du bouton qui ouvre l'agent concerné."""
    widget.set_hexpand(True)
    row = Gtk.Box(spacing=4)
    row.append(widget)
    row.append(open_button(on_open))
    return row


class SubagentBlock(Gtk.Box):
    """En-tête « 🤖 type — description · état », puis la consigne et le travail du sous-agent.

    loader : fonction (appelée dans un thread) qui renvoie la transcription
    {"found", "items", …} ; None pour un sous-agent suivi en direct.
    make : fabrique du widget de chaque élément (appels d'outils dépliables…).
    on_open : bouton « voir dans le volet des sous-agents », s'il est donné.
    """

    def __init__(self, item, loader=None, make=message_widget, on_open=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["subagent"])
        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
        self.tool_id, self.loader, self.loaded, self.make = item.get("id"), loader, False, make
        kind = inp.get("subagent_type") or "Agent"
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label=f"🤖 {kind}", css_classes=["subagent-kind"]))
        head.append(Gtk.Label(label=str(inp.get("description") or ""), xalign=0,
                              ellipsize=Pango.EllipsizeMode.END))
        self.state = Gtk.Label(xalign=0, hexpand=True, css_classes=["sid"],
                               ellipsize=Pango.EllipsizeMode.END)
        head.append(self.state)
        self.expander = Gtk.Expander(label_widget=head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                            css_classes=["subagent-body"])
        self.prompt = str(inp.get("prompt") or "")
        if self.prompt:
            self.body.append(message_widget({"role": "user", "text": self.prompt}))
        self.expander.set_child(self.body)
        self.expander.connect("notify::expanded", self.on_expanded)
        self.steps = 0
        if not on_open:
            return self.append(self.expander)
        row = Gtk.Box(spacing=4)
        self.expander.set_hexpand(True)
        row.append(self.expander)
        row.append(open_button(on_open))
        self.append(row)

    def on_expanded(self, exp, _pspec):
        if exp.get_expanded() and self.loader and not self.loaded:
            self.loaded = True
            self.state.set_text("chargement…")
            in_thread(self.loader, self.show_transcript, self.load_failed)

    def show_transcript(self, res):
        self.state.set_text("")
        if not res.get("found"):
            return self.body.append(Gtk.Label(label="Transcription du sous-agent introuvable.",
                                              xalign=0, css_classes=["notice"]))
        while (child := self.body.get_first_child()):
            self.body.remove(child)
        if res["total"] > len(res["items"]):
            self.body.append(Gtk.Label(label=f"… {res['total'] - len(res['items'])} éléments "
                                       "plus anciens masqués", xalign=0, css_classes=["notice"]))
        for it in res["items"]:
            self.body.append(self.make(it))
        meta = res.get("meta") or {}
        if meta.get("requestShape") == "background":
            self.state.set_text("en arrière-plan")
        if res.get("end"):  # cloud : seul l'état final est connu
            self.finish(res["end"])

    def load_failed(self, e):
        self.loaded = False
        self.state.set_text(f"lecture impossible : {e}")

    # -- suivi en direct --
    def add(self, item):
        self.steps += 1
        self.body.append(self.make(item))
        if item.get("role") == "tool":
            self.state.set_text(f"⏳ {item.get('name')}…")

    def progress(self, ev):
        parts = [ev.get("description") or "", tokens(ev.get("usage"))]
        self.state.set_text("⏳ " + " · ".join(p for p in parts if p))

    def finish(self, ev):
        status = ev.get("status") or "completed"
        icon = "✓" if status == "completed" else "✗"
        parts = [f"{icon} {'terminé' if status == 'completed' else status}", tokens(ev.get("usage"))]
        self.state.set_text(" · ".join(p for p in parts if p))
        if ev.get("summary"):  # le texte complet arrive aussi comme dernier message du sous-agent
            self.state.set_tooltip_text(str(ev["summary"])[:500])
