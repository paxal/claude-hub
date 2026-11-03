"""Volet « Sous-agents » : l'arbre de tous les sous-agents d'une conversation Claude, puis
la transcription de celui qu'on choisit, suivie tant qu'il travaille.

La liste vient de la commande « agents » des scripts distants (fichiers
subagents/agent-*.jsonl et leur .meta.json, sous-agents imbriqués compris) ; la
transcription, de la commande « agent ». La fenêtre fournit les deux lecteurs et
rafraîchit la liste tant qu'un sous-agent travaille.

Les agents d'un workflow (subagents/workflows/<run>/) forment un groupe, à la place de son
premier agent : en-tête repliable (nom et résumé du workflow, agents finis sur total), puis
ses agents par phase. Déplié tant qu'il tourne, replié une fois fini, sauf choix contraire.
"""
import re
import time

from .gtk import GLib, Gtk, Pango, in_thread
from .listrow import BUSY_ICON
from .render import tool_detail
from .widgets import section_title

STALE = 600  # s sans nouvelle étape d'un agent non fini, dans une session arrêtée : on ne dit plus qu'il travaille
AGENT_ID = re.compile(r"^a[0-9a-f]{8,}$")  # destinataire de SendMessage qui désigne un sous-agent
MARKS = {"done": "✓", "waiting": "⧗", "idle": "⏸", "stopped": "✗"}


def agent_state(a, alive, now=None):
    """« running », « done », « stopped » (interrompu) ou « idle » (pas fini, mais sans
    nouvelles et session arrêtée)."""
    if a.get("done"):
        return "done"
    if a.get("interrupted"):
        return "stopped"
    now = time.time() if now is None else now
    return "running" if alive or now - (a.get("end") or 0) < STALE else "idle"


def duration(sec):
    sec = int(sec or 0)
    if sec < 60:
        return f"{sec} s"
    if sec < 3600:
        return f"{sec // 60} min"
    return f"{sec // 3600} h {sec % 3600 // 60:02d}"


def last_step(a):
    """Dernière étape d'un agent en une ligne : « Bash  cargo test… » ou début de son texte."""
    last = a.get("last") or {}
    if last.get("role") == "tool":
        return f"{last.get('name')}  {tool_detail(last.get('input'))}"
    return " ".join(str(last.get("text") or "").split())


def running_agents(agents, alive, now=None):
    """Sous-agents qui travaillent, le plus récemment actif en premier."""
    run = [a for a in agents or () if agent_state(a, alive, now) == "running"]
    return sorted(run, key=lambda a: -(a.get("end") or a.get("start") or 0))


def agent_line(a):
    """Un sous-agent en une ligne (info-bulle de sa ligne d'activité) : « Review fix › Bash cargo test… »."""
    name = " ".join(str(a.get("description") or a.get("type") or "Agent").split())
    step = " ".join(last_step(a).split())
    return f"{name} › {step}" if step else name


def workflow_text(workflows):
    """Avancement des workflows en arrière-plan (commande « stat »), pour la ligne de Claude :
    « review · 151/152 agents · review_style #5 : r5 · pass1 · General Rust 1/2 »."""
    parts = []
    for w in workflows or ():
        t = w.get("name") or "workflow"
        if w.get("total"):
            t += f" · {w.get('done', 0)}/{w['total']} agents"
        run = w.get("running") or []
        phases = {" ".join(str(r.get("phase") or "").lstrip("▸").split()) for r in run}
        phase = phases.pop() if len(phases) == 1 else ""
        labels = ", ".join(" ".join(str(r.get("label") or "").split()) or "?" for r in run)
        if len(run) == 1:
            t += " · " + (f"{phase} : " if phase else "") + labels
        elif run:
            t += f" · {len(run)} en cours" + (f" ({phase})" if phase else "") + " : " + labels
        parts.append(t)
    return " — ".join(parts)


def tree(agents):
    """Agents dans l'ordre de l'arbre : chacun suivi de ceux qu'il a lancés."""
    ids = {a["id"] for a in agents}
    children = {}
    for a in agents:
        children.setdefault(a.get("parent") if a.get("parent") in ids else None, []).append(a)
    out, stack = [], list(reversed(children.get(None, [])))
    while stack:
        a = stack.pop()
        out.append(a)
        stack.extend(reversed(children.get(a["id"], [])))
    return out


def phase_label(phase):
    """Phase d'un agent de workflow, sans la marque de Claude Code (« ▸ review #2 »)."""
    return " ".join(str(phase or "").lstrip("▸").split())


def workflow_of(a, by_id):
    """Run du workflow d'un agent : le sien, ou celui de l'agent qui l'a lancé."""
    seen = set()
    while a and a["id"] not in seen:
        if a.get("workflow"):
            return a["workflow"]
        seen.add(a["id"])
        a = by_id.get(a.get("parent"))
    return None


def layout(agents, runs=()):
    """Ordre du volet, du plus ancien au plus récent : ("agent", a) pour un sous-agent hors
    workflow, suivi de ceux qu'il a lancés ; ("workflow", w) pour un workflow, à la place de
    son premier agent. w : {"id", "name", "summary", "agents", "phases"}, phases =
    [(nom, [agents dans l'ordre de l'arbre])] dans l'ordre où elles ont démarré ; un agent
    sans phase prend celle de l'agent qui l'a lancé. runs : workflow_runs des scripts
    distants (nom et résumé de chaque workflow)."""
    by_id = {a["id"]: a for a in agents}
    info = {r.get("id"): r for r in runs or () if isinstance(r, dict)}
    loose = [a for a in agents if not workflow_of(a, by_id)]
    ids = {a["id"] for a in loose}
    subtrees = {}  # racine -> elle et ceux qu'elle a lancés
    root = None
    for a in tree(loose):
        if a.get("parent") not in ids:
            root = a["id"]
        subtrees.setdefault(root, []).append(a)
    out, groups = [], {}
    for a in agents:
        run = workflow_of(a, by_id)
        if run:
            if run not in groups:
                r = info.get(run) or {}
                groups[run] = {"id": run, "name": r.get("name") or run, "summary": r.get("summary"),
                               "agents": []}
                out.append(("workflow", groups[run]))
            groups[run]["agents"].append(a)
        else:
            out += [("agent", x) for x in subtrees.get(a["id"], ())]
    for w in groups.values():
        phases, of = {}, {}
        for a in tree(w["agents"]):
            of[a["id"]] = phase_label(a.get("phase")) or of.get(a.get("parent"), "")
            phases.setdefault(of[a["id"]], []).append(a)
        w["phases"] = list(phases.items())
    return out


def workflow_state(states):
    """État d'un workflow d'après ceux de ses agents (agent_state, ou « waiting »)."""
    if "running" in states or "waiting" in states:
        return "running"
    if all(st == "done" for st in states):
        return "done"
    return "stopped" if "stopped" in states else "idle"


class AgentsPanel(Gtk.Box):
    """loader(agent) (appelé dans un thread) renvoie sa transcription {"total", "items"} ;
    make(item) fabrique le widget d'un élément, comme dans le fil."""

    def __init__(self, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["viewer"])
        self.sid, self.agents, self.alive = None, [], False
        self.runs = {}           # workflows de la conversation, par run (nom, résumé)
        self.expanded = {}       # run -> déplié ou replié par l'utilisateur
        self.loader = self.make = None
        self.selected = None     # id de l'agent affiché, None : la liste
        self.shown = (None, -1)  # (id, taille) de la transcription affichée
        self.total = 0           # éléments de cette transcription déjà affichés
        self.gen = 0
        head = Gtk.Box(spacing=4, margin_top=6, margin_start=8, margin_end=4, margin_bottom=4)
        self.back = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["flat"], visible=False,
                               tooltip_text="Tous les sous-agents", valign=Gtk.Align.CENTER)
        self.back.connect("clicked", lambda _b: self.show_list())
        head.append(self.back)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(xalign=0, css_classes=["card-title"], ellipsize=Pango.EllipsizeMode.END)
        self.where = Gtk.Label(xalign=0, css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END)
        names.append(self.title)
        names.append(self.where)
        head.append(names)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                           tooltip_text="Fermer", valign=Gtk.Align.CENTER)
        close.connect("clicked", lambda _b: on_close())
        head.append(close)
        self.append(head)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["agents-list"])
        self.list.connect("row-activated", lambda _l, row: self.activate(row))
        self.empty = Gtk.Label(label="Pas de sous-agent dans cette conversation.", xalign=0,
                               css_classes=["notice"], visible=False)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.empty)
        page.append(self.list)
        self.stack.add_named(Gtk.ScrolledWindow(child=page, hscrollbar_policy=Gtk.PolicyType.NEVER),
                             "list")
        self.items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4,
                             margin_bottom=8, margin_start=4, margin_end=4)
        self.scroll = Gtk.ScrolledWindow(child=self.items, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.stack.add_named(self.scroll, "agent")
        self.append(self.stack)

    # -- données --
    def set_session(self, sid, loader, make):
        """Conversation dont le volet montre les agents (retour à la liste si elle change)."""
        if sid != self.sid:
            self.sid, self.agents, self.runs, self.expanded = sid, [], {}, {}
            self.show_list()
        self.loader, self.make = loader, make

    def update(self, agents, alive, runs=None):
        """runs : workflow_runs des scripts distants (None : ceux déjà connus)."""
        self.agents, self.alive = agents or [], alive
        if runs is not None:
            self.runs = {r["id"]: r for r in runs if isinstance(r, dict) and r.get("id")}
        self.fill_list()
        if self.selected:
            self.show_agent()

    def find(self, key):
        """Agent désigné par son id ou par l'id de l'appel d'outil qui l'a lancé."""
        return next((a for a in self.agents if key in (a["id"], a.get("tool"))), None)

    def state(self, a):
        st = agent_state(a, self.alive)
        if st == "done" and any(agent_state(c, self.alive) == "running"
                                for c in self.agents if c.get("parent") == a["id"]):
            return "waiting"
        return st

    def counts(self):
        running = sum(agent_state(a, self.alive) == "running" for a in self.agents)
        flows = len({a["workflow"] for a in self.agents if a.get("workflow")})
        return (f"{len(self.agents)} sous-agent{'s' if len(self.agents) > 1 else ''}"
                + (f" · {flows} workflow{'s' if flows > 1 else ''}" if flows else "")
                + (f" · {running} en cours" if running else ""))

    # -- liste --
    def fill_list(self):
        if not self.selected:
            self.title.set_text("🤖 Sous-agents")
            self.where.set_text(self.counts())
        while (row := self.list.get_first_child()):
            self.list.remove(row)
        self.empty.set_visible(not self.agents)
        now = time.time()
        for kind, x in layout(self.agents, self.runs.values()):
            if kind == "agent":
                self.list.append(self.agent_row(x, now))
                continue
            st = workflow_state([self.state(a) for a in x["agents"]])
            opened = self.expanded.get(x["id"], st == "running")
            self.list.append(self.workflow_row(x, st, opened, now))
            for phase, members in x["phases"] if opened else ():
                if phase:
                    done = sum(self.state(a) == "done" for a in members)
                    count = f"{len(members)}" if done == len(members) else f"{done}/{len(members)}"
                    title = section_title(f"{phase} · {count}")
                    title.set_margin_start(24)
                    self.list.append(Gtk.ListBoxRow(child=title, activatable=False, selectable=False))
                for a in members:
                    self.list.append(self.agent_row(a, now, in_workflow=True))

    def activate(self, row):
        """Clic sur une ligne : l'en-tête d'un workflow le replie ou le déplie, celle d'un
        agent ouvre sa transcription."""
        if getattr(row, "workflow_id", None):
            self.expanded[row.workflow_id] = not row.workflow_open
            self.fill_list()
        elif getattr(row, "agent_id", None):
            self.open_agent(row.agent_id)

    def workflow_row(self, w, st, opened, now):
        """En-tête d'un workflow : flèche, état, nom et résumé, agents finis sur total, durée."""
        row = Gtk.ListBoxRow()
        row.workflow_id, row.workflow_open = w["id"], opened
        box = Gtk.Box(spacing=8, css_classes=["wf-head"])
        box.append(Gtk.Image(icon_name="pan-down-symbolic" if opened else "pan-end-symbolic",
                             valign=Gtk.Align.CENTER, css_classes=["wf-arrow"]))
        if st == "running":
            box.append(Gtk.Image(icon_name=BUSY_ICON, valign=Gtk.Align.CENTER, css_classes=["st-busy"]))
        else:
            box.append(Gtk.Label(label=MARKS[st], valign=Gtk.Align.CENTER, width_chars=2,
                                 css_classes=["agent-mark", st]))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        col.append(Gtk.Label(label=w["name"], xalign=0, css_classes=["card-title"],
                             ellipsize=Pango.EllipsizeMode.END))
        agents = w["agents"]
        end = now if st == "running" else max((a.get("end") or 0 for a in agents), default=0)
        start = min((a["start"] for a in agents if a.get("start")), default=None)
        sub = " ".join(str(w.get("summary") or "").split())
        if st == "idle":
            sub = f"sans nouvelles depuis {duration(now - (end or now))}"
        elif st == "stopped":
            sub = f"interrompu · {sub}" if sub else "interrompu"
        if sub:
            col.append(Gtk.Label(label=sub, xalign=0, css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END))
        box.append(col)
        done = sum(self.state(a) == "done" for a in agents)
        box.append(Gtk.Label(label=f"{done}/{len(agents)}", valign=Gtk.Align.CENTER,
                             css_classes=["wf-count", "busy"] if st == "running" else ["sid"],
                             tooltip_text=f"{done} agent{'s' if done > 1 else ''} fini{'s' if done > 1 else ''}"
                                          f" sur {len(agents)}"))
        box.append(Gtk.Label(label=duration(end - start) if start and end else "",
                             valign=Gtk.Align.CENTER, css_classes=["sid"]))
        row.set_child(box)
        row.set_tooltip_text("\n".join(t for t in (w["name"], w.get("summary")) if t)[:600])
        return row

    def agent_row(self, a, now, in_workflow=False):
        """Ligne d'un agent ; dans un workflow, un cran de retrait et sans son type (ce que
        Claude Code donne aux agents d'un workflow : « workflow-subagent »)."""
        st = self.state(a)
        row = Gtk.ListBoxRow()
        row.agent_id = a["id"]
        box = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_end=6,
                      margin_start=6 + 18 * (max(0, (a.get("depth") or 1) - 1) + in_workflow))
        if st == "running":
            box.append(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))
        else:
            box.append(Gtk.Label(label=MARKS[st], valign=Gtk.Align.CENTER, width_chars=2,
                                 css_classes=["agent-mark", st]))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        col.append(Gtk.Label(label=a.get("description") or a["id"], xalign=0, css_classes=["card-title"],
                             ellipsize=Pango.EllipsizeMode.END))
        step = last_step(a)
        if st == "waiting":
            step = "attend ses sous-agents"
        elif st == "stopped":
            step = f"interrompu · {step}"
        elif st == "idle":
            step = f"sans nouvelles depuis {duration(now - (a.get('end') or now))} · {step}"
        col.append(Gtk.Label(label=step if in_workflow else f"{a.get('type') or 'Agent'} · {step}",
                             xalign=0, css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END))
        box.append(col)
        end = now if st == "running" else a.get("end")
        took = duration(end - a["start"]) if a.get("start") and end else ""
        box.append(Gtk.Label(label=took, valign=Gtk.Align.CENTER, css_classes=["sid"],
                             tooltip_text=f"{a.get('steps') or 0} appels d'outils"))
        row.set_child(box)
        row.set_tooltip_text(last_step(a)[:300] or None)
        return row

    def show_list(self):
        self.selected = None
        self.gen += 1
        self.back.set_visible(False)
        self.title.set_text("🤖 Sous-agents")
        self.where.set_text(self.counts())
        self.stack.set_visible_child_name("list")

    # -- transcription d'un agent --
    def open_agent(self, key):
        """Affiche un agent (id, ou id de l'appel d'outil) ; False s'il est inconnu."""
        a = self.find(key)
        if not a:
            return False
        if a["id"] != self.selected:
            self.selected, self.shown, self.total = a["id"], (None, -1), 0
            while (child := self.items.get_first_child()):
                self.items.remove(child)
        self.back.set_visible(True)
        self.stack.set_visible_child_name("agent")
        self.show_agent()
        return True

    def show_agent(self):
        a = self.find(self.selected)
        if not a:
            return self.show_list()
        st = self.state(a)
        self.title.set_text(f"🤖 {a.get('description') or a['id']}")
        label = {"running": "⏳ en cours", "done": "✓ terminé", "waiting": "⧗ attend ses sous-agents",
                 "idle": "⏸ sans nouvelles", "stopped": "⏹ interrompu"}[st]
        kind = a.get("type") or "Agent"
        if a.get("workflow"):  # « review-changes › Verify » plutôt que « workflow-subagent »
            kind = " › ".join(t for t in ((self.runs.get(a["workflow"]) or {}).get("name") or a["workflow"],
                                          phase_label(a.get("phase"))) if t)
        self.where.set_text(f"{kind} · {label} · {a.get('steps') or 0} appels d'outils")
        if self.shown == (a["id"], a.get("size")) or not self.loader:
            return
        self.shown = (a["id"], a.get("size"))
        self.gen += 1
        gen, loader = self.gen, self.loader
        if not self.total:
            self.items.append(Gtk.Label(label="chargement…", xalign=0, css_classes=["notice"]))
        in_thread(lambda: loader(a), lambda res: gen == self.gen and self.add_transcript(res),
                  lambda e: gen == self.gen and self.load_failed(e))

    def add_transcript(self, res):
        """Ajoute les éléments arrivés depuis la dernière lecture (tout, la première fois)."""
        if not res.get("found"):
            return self.load_failed("transcription introuvable")
        items, new = res.get("items") or [], res.get("total", 0) - self.total
        adj = self.scroll.get_vadjustment()
        at_end = not self.total or adj.get_value() + adj.get_page_size() >= adj.get_upper() - 40
        if not self.total or new > len(items):
            while (child := self.items.get_first_child()):
                self.items.remove(child)
            new = len(items)
            if res.get("total", 0) > len(items):
                self.items.append(Gtk.Label(label=f"… {res['total'] - len(items)} éléments plus anciens "
                                            "masqués", xalign=0, css_classes=["notice"]))
        for it in items[len(items) - new:] if new > 0 else []:
            self.items.append(self.make(it))
        self.total = res.get("total", 0)
        if at_end:  # suivre la fin, sauf si on est remonté lire
            GLib.timeout_add(100, lambda: adj.set_value(adj.get_upper() - adj.get_page_size()) and False)

    def load_failed(self, e):
        self.shown = (None, -1)  # on réessaiera au prochain rafraîchissement
        if not self.total:
            while (child := self.items.get_first_child()):
                self.items.remove(child)
            self.items.append(Gtk.Label(label=f"Lecture impossible : {e}", xalign=0, wrap=True,
                                        css_classes=["notice"]))
