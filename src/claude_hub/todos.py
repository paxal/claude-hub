"""Liste de tâches de Claude (TodoWrite, TaskCreate, TaskUpdate), épinglée au-dessus de la saisie.

TodoWrite remplace toute la liste à chaque appel. Les versions récentes de Claude
Code utilisent TaskCreate (une tâche, dont le numéro n'est connu que par le
résultat de l'outil : « Task #3 created… ») et TaskUpdate (statut, titre, ou
suppression d'une tâche par son numéro). La liste se reconstitue en rejouant ces
appels : sur toute la transcription pour l'historique (champ « todos » de la
commande « history » des scripts distants), puis au fil du flux de claude -p.
"""
import re

from .gtk import Gtk, Pango

TODO_TOOLS = ("TodoWrite", "TaskCreate", "TaskUpdate")
STATUS = {"pending": "à faire", "in_progress": "en cours", "completed": "terminée",
          "deleted": "supprimée", "cancelled": "annulée"}
MARKS = {"completed": "✓", "in_progress": "▸", "cancelled": "✗"}
CREATED = re.compile(r"Task #(\w+) created")


def created_id(result, content=None):
    """Numéro attribué par TaskCreate, d'après le résultat de l'outil (structuré ou texte)."""
    task = result.get("task") if isinstance(result, dict) else None
    if isinstance(task, dict) and task.get("id") is not None:
        return str(task["id"])
    texts = [content] if isinstance(content, str) else [
        b.get("text") for b in content or [] if isinstance(b, dict)]
    for t in texts:
        m = CREATED.search(t or "")
        if m:
            return m.group(1)
    return None


def todo_detail(name, inp):
    """Résumé d'un appel de TodoWrite, TaskCreate ou TaskUpdate pour le fil."""
    if name == "TaskCreate":
        return str(inp.get("subject") or "")
    if name == "TaskUpdate":
        parts = [f"#{inp.get('taskId')}"]
        if inp.get("status"):
            parts.append(f"→ {STATUS.get(inp['status'], inp['status'])}")
        if inp.get("subject"):
            parts.append(f"« {inp['subject']} »")
        return " ".join(parts)
    todos = [t for t in inp.get("todos") or [] if isinstance(t, dict)]
    done = sum(t.get("status") == "completed" for t in todos)
    return f"{done}/{len(todos)} terminées" if todos else "liste vidée"


class TodoList:
    """Tâches d'une conversation : [{"id", "subject", "active", "status"}], dans l'ordre."""

    def __init__(self):
        self.tasks = []
        self.creating = {}  # id de l'appel TaskCreate -> tâche, en attente de son numéro

    @classmethod
    def from_events(cls, events):
        """Rejoue les événements du script distant : appels d'outils et numéros attribués."""
        todos = cls()
        for ev in events or []:
            if "created" in ev:
                todos.created(ev["created"], ev.get("task"))
            else:
                todos.use(ev.get("name"), ev.get("input"), ev.get("id"))
        return todos

    @classmethod
    def from_items(cls, items):
        """À défaut d'événements (Cursor, cloud) : d'après les appels d'outils de l'historique."""
        todos = cls()
        for it in items:
            if it.get("role") == "tool":
                todos.use(it.get("name"), it.get("input"), it.get("id"))
        return todos

    def find(self, task_id):
        return next((t for t in self.tasks if t["id"] == str(task_id)), None)

    def next_id(self):
        """Numéro provisoire d'une tâche créée, corrigé par le résultat de TaskCreate."""
        ids = [int(t["id"]) for t in self.tasks if str(t["id"]).isdigit()]
        return str(max(ids, default=0) + 1)

    def use(self, name, inp, tool_id=None):
        """Applique un appel d'outil ; renvoie True s'il concerne la liste."""
        if name not in TODO_TOOLS:
            return False
        inp = inp if isinstance(inp, dict) else {}
        if name == "TodoWrite":
            self.creating = {}
            self.tasks = [{"id": str(i + 1), "subject": str(t.get("content") or ""),
                           "active": str(t.get("activeForm") or ""),
                           "status": t.get("status") or "pending"}
                          for i, t in enumerate(inp.get("todos") or []) if isinstance(t, dict)]
        elif name == "TaskCreate":
            task = {"id": self.next_id(), "subject": str(inp.get("subject") or ""),
                    "active": str(inp.get("activeForm") or ""), "status": "pending"}
            self.tasks.append(task)
            if tool_id:
                self.creating[tool_id] = task
        else:
            task = self.find(inp.get("taskId"))
            if task is None:
                return True
            if inp.get("status") == "deleted":
                self.tasks.remove(task)
                return True
            for key, field in (("status", "status"), ("subject", "subject"), ("activeForm", "active")):
                if inp.get(key):
                    task[field] = str(inp[key])
        return True

    def created(self, tool_id, task_id):
        """Numéro définitif d'une tâche créée ; renvoie True si la liste a changé."""
        task = self.creating.pop(tool_id, None)
        if task is None or task_id is None:
            return False
        task["id"] = str(task_id)
        return True

    def done(self):
        return sum(t["status"] == "completed" for t in self.tasks)

    def current(self):
        return next((t for t in self.tasks if t["status"] == "in_progress"), None)

    def active(self):
        """Vrai tant qu'il reste une tâche à faire : la liste n'est montrée que dans ce cas."""
        return any(t["status"] not in ("completed", "cancelled") for t in self.tasks)


class TodoPanel(Gtk.Revealer):
    """Volet repliable « Tâches 3/7 · tâche en cours », au-dessus de la saisie."""

    def __init__(self):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                         transition_duration=200)
        self.title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.expander = Gtk.Expander(label_widget=self.title, expanded=True)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                            css_classes=["todo-list"])
        self.expander.set_child(Gtk.ScrolledWindow(
            child=self.rows, max_content_height=180, propagate_natural_height=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(Gtk.Box(css_classes=["todos"], margin_start=8, margin_end=8,
                               margin_top=6))
        self.get_child().append(self.expander)
        self.shown = None

    def show(self, todos):
        """Affiche la liste (TodoList) si une tâche reste à faire, sinon masque le volet."""
        if not todos or not todos.active():
            self.shown = None
            return self.set_reveal_child(False)
        tasks = [dict(t) for t in todos.tasks]
        if tasks != self.shown:
            self.shown = tasks
            cur = todos.current()
            title = f"Tâches {todos.done()}/{len(tasks)}"
            if cur:
                title += f" · {cur['active'] or cur['subject']}"
            self.title.set_text(title)
            while (child := self.rows.get_first_child()):
                self.rows.remove(child)
            for t in tasks:
                row = Gtk.Box(spacing=6, css_classes=["todo", t["status"]])
                row.append(Gtk.Label(label=MARKS.get(t["status"], "○"), css_classes=["todo-mark"]))
                row.append(Gtk.Label(label=t["subject"], xalign=0, hexpand=True,
                                     ellipsize=Pango.EllipsizeMode.END, tooltip_text=t["subject"]))
                self.rows.append(row)
        self.set_reveal_child(True)
