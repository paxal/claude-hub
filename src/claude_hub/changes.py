"""Fichiers modifiés par une conversation : diff de chaque modification (Edit, MultiEdit,
Write, NotebookEdit) et volet « Fichiers modifiés » qui les regroupe par fichier.

Pour une conversation Claude Code (ce PC, SSH), la liste vient de la commande
« edits » des scripts distants : toute la transcription et celles des sous-agents,
avec le diff calculé par claude (structuredPatch, numéros de ligne compris). Pour le
cloud, elle est tirée de l'historique affiché et le diff recalculé d'après l'entrée de
l'outil ; pour Cursor, dont les outils ne donnent que le fichier, le diff est lu dans
sa base au dépliage (sortie de l'outil).
"""
import difflib

from .gtk import Gtk, Pango, in_thread
from .render import code_block, copy_button, esc, file_href, make_label

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
CONTEXT = 3  # lignes de contexte autour d'un changement recalculé


def input_pairs(tool, inp):
    """[(ancien texte, nouveau texte)] d'une modification, d'après l'entrée de l'outil."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Edit":
        pairs = [(inp.get("old_string"), inp.get("new_string"))]
    elif tool == "MultiEdit":
        pairs = [(e.get("old_string"), e.get("new_string")) for e in inp.get("edits") or []
                 if isinstance(e, dict)]
    elif tool == "Write":
        pairs = [("", inp.get("content"))]
    else:
        return []
    return [(a, b) for a, b in pairs if isinstance(a, str) and isinstance(b, str)]


def input_hunks(tool, inp):
    """Morceaux de diff recalculés d'après l'entrée de l'outil (sans numéros de ligne :
    on ne sait pas où le texte se trouve dans le fichier)."""
    hunks = []
    for old, new in input_pairs(tool, inp):
        a, b = old.splitlines(), new.splitlines()
        matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for group in matcher.get_grouped_opcodes(CONTEXT):
            if all(op == "equal" for op, *_r in group):
                continue
            lines = []
            for op, i1, i2, j1, j2 in group:
                if op == "equal":
                    lines += [" " + x for x in a[i1:i2]]
                else:
                    lines += ["-" + x for x in a[i1:i2]] + ["+" + x for x in b[j1:j2]]
            hunks.append({"oldStart": None, "newStart": None, "lines": lines})
    return hunks


def edit_hunks(edit):
    """Morceaux de diff d'une modification : ceux de claude, sinon recalculés."""
    patch = edit.get("patch")
    if isinstance(patch, list) and patch:
        return [h for h in patch if isinstance(h, dict) and isinstance(h.get("lines"), list)]
    return input_hunks(edit.get("tool"), edit.get("input"))


def hunk_stats(hunks):
    """(lignes ajoutées, lignes retirées)."""
    added = removed = 0
    for h in hunks:
        for line in h.get("lines") or []:
            if isinstance(line, str) and line.startswith("+"):
                added += 1
            elif isinstance(line, str) and line.startswith("-"):
                removed += 1
    return added, removed


def hunks_text(hunks):
    """Morceaux d'un diff unifié : « @@ -12,5 +12,7 @@ » quand on connaît les lignes, sinon
    « @@ » entre deux morceaux seulement."""
    out = []
    for i, h in enumerate(hunks):
        if isinstance(h.get("oldStart"), int) and isinstance(h.get("newStart"), int):
            out.append(f"@@ -{h['oldStart']},{h.get('oldLines', 0)} "
                       f"+{h['newStart']},{h.get('newLines', 0)} @@")
        elif i:
            out.append("@@")
        out += [str(x) for x in h.get("lines") or []]
    return "\n".join(out)


def first_line(hunks):
    """Première ligne modifiée dans le nouveau fichier (pour ouvrir le fichier au bon endroit)."""
    for h in hunks:
        if isinstance(h.get("newStart"), int):
            n = h["newStart"]
            for line in h.get("lines") or []:
                if str(line)[:1] in ("+", "-"):
                    return max(n, 1)
                n += 1  # ligne de contexte
            return max(h["newStart"], 1)
    return None


def edits_from_items(items):
    """Modifications tirées d'un historique (cloud, Cursor) : entrées des outils seulement."""
    out = []
    for it in items or []:
        inp = it.get("input") if isinstance(it.get("input"), dict) else {}
        if it.get("role") != "tool" or it.get("name") not in EDIT_TOOLS:
            continue
        path = inp.get("file_path") or inp.get("notebook_path")
        if isinstance(path, str) and path:
            out.append({"id": it.get("id"), "tool": it["name"], "path": path, "time": None,
                        "agent": None, "status": "ok", "input": inp, "patch": None,
                        "created": False})
    return out


def group_files(edits):
    """Modifications regroupées par fichier, triées par chemin ; celles qui ont échoué
    sont écartées (le fichier n'a pas changé)."""
    files = {}
    for e in edits or []:
        path = e.get("path")
        if e.get("status") == "error" or not isinstance(path, str) or not path:
            continue
        f = files.get(path)
        if f is None:
            f = files[path] = {"path": path, "edits": [], "added": 0, "removed": 0,
                               "created": bool(e.get("created")), "agents": set(), "known": True}
        hunks = edit_hunks(e)
        added, removed = hunk_stats(hunks)
        # Sans diff (Cursor, cellule de notebook) : nombre de lignes inconnu.
        known = bool(hunks) or e.get("tool") == "Write"
        f["edits"].append(dict(e, hunks=hunks, added=added, removed=removed))
        f["added"] += added
        f["removed"] += removed
        f["known"] = f["known"] and known
        if e.get("agent"):
            f["agents"].add(e["agent"])
    return sorted(files.values(), key=lambda f: f["path"])


def full_diff(files):
    """Diff unifié de toutes les modifications, fichier par fichier (pour le copier)."""
    out = []
    for f in files:
        for e in f["edits"]:
            if not e["hunks"]:
                continue
            side = lambda prefix: prefix + ("" if f["path"].startswith("/") else "/") + f["path"]  # noqa: E731
            out.append("--- " + ("/dev/null" if e.get("created") else side("a")))
            out.append("+++ " + side("b"))
            out.append(hunks_text(e["hunks"]))
    return "\n".join(out) + ("\n" if out else "")


def short_path(path, cwd):
    """Chemin relatif au dossier de la conversation quand il est dessous."""
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    return path


def stats_box(added, removed, known=True):
    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    if not known:
        return box
    if added:
        box.append(Gtk.Label(label=f"+{added}", css_classes=["diff-add"]))
    if removed:
        box.append(Gtk.Label(label=f"−{removed}", css_classes=["diff-del"]))
    return box


DIFF_LINES = 400  # lignes affichées d'un diff (un Write peut créer un long fichier)


def diff_block(hunks, limit=DIFF_LINES):
    """Bloc de code d'un diff, coloré, coupé après limit lignes."""
    lines = hunks_text(hunks).split("\n")
    text = "\n".join(lines[:limit])
    if len(lines) > limit:
        text += f"\n… ({len(lines) - limit} lignes de plus)"
    return code_block(text, "diff")


def edit_label(e, time_text):
    """« Edit · 11:42 · sous-agent a1b2… · en cours »."""
    parts = [str(e.get("tool"))]
    if e.get("time"):
        parts.append(time_text(e["time"]))
    if e.get("agent"):
        parts.append(f"sous-agent {e['agent'][:8]}")
    if e.get("tool") == "NotebookEdit":
        parts.append("cellule " + str((e.get("input") or {}).get("edit_mode") or "modifiée"))
    if e.get("status") == "pending":
        parts.append("en cours")
    return " · ".join(parts)


class ChangesPanel(Gtk.Box):
    """Volet « Fichiers modifiés ». loader(edit) (dans un thread) renvoie le texte du diff
    d'une modification sans morceaux de diff (Cursor), ou None s'il n'y en a pas."""

    def __init__(self, on_close, time_text):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["viewer"])
        self.sid, self.files, self.cwd, self.loader = None, [], None, None
        self.time_text = time_text
        head = Gtk.Box(spacing=4, margin_top=6, margin_start=12, margin_end=4, margin_bottom=4)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        names.append(Gtk.Label(label="Fichiers modifiés", xalign=0, css_classes=["card-title"]))
        self.summary = Gtk.Label(xalign=0, css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END)
        names.append(self.summary)
        head.append(names)
        self.copy_slot = Gtk.Box(valign=Gtk.Align.CENTER)
        head.append(self.copy_slot)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                           tooltip_text="Fermer", valign=Gtk.Align.CENTER)
        close.connect("clicked", lambda _b: on_close())
        head.append(close)
        self.append(head)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=6,
                            margin_end=6, margin_bottom=8)
        self.empty = Gtk.Label(label="Aucun fichier modifié pour l'instant.", xalign=0,
                               css_classes=["notice"], visible=False)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.empty)
        page.append(self.list)
        self.append(Gtk.ScrolledWindow(child=page, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

    def show(self, sid, edits, cwd=None, loader=None, loading=False):
        """Affiche les modifications de la conversation sid (en gardant dépliés les fichiers
        qui l'étaient, si c'est la même conversation)."""
        opened = self.opened() if sid == self.sid else set()
        self.sid, self.cwd, self.loader = sid, cwd, loader
        self.files = group_files(edits)
        while (child := self.list.get_first_child()):
            self.list.remove(child)
        for f in self.files:
            self.list.append(self.file_row(f, f["path"] in opened))
        n = len(self.files)
        added = sum(f["added"] for f in self.files)
        removed = sum(f["removed"] for f in self.files)
        text = f"{n} fichier{'s' * (n > 1)}"
        if any(f["known"] for f in self.files) and (added or removed):
            text += f" · +{added} −{removed}"
        by_agents = sum(1 for f in self.files if f["agents"])
        if by_agents:
            text += f" · {by_agents} par des sous-agents" if by_agents > 1 else " · 1 par un sous-agent"
        self.summary.set_text("Chargement…" if loading and not n else text)
        self.empty.set_visible(not n and not loading)
        while (child := self.copy_slot.get_first_child()):
            self.copy_slot.remove(child)
        diff = full_diff(self.files)
        if diff:
            self.copy_slot.append(copy_button(diff, "Copier le diff de toutes les modifications"))

    def opened(self):
        out, child = set(), self.list.get_first_child()
        while child:
            if child.get_expanded():
                out.add(child.path)
            child = child.get_next_sibling()
        return out

    def file_row(self, f, expanded):
        head = Gtk.Box(spacing=8)
        name = short_path(f["path"], self.cwd)
        line = first_line(f["edits"][-1]["hunks"])
        link = make_label(f'<a href="{esc(file_href(f["path"], line))}">{esc(name)}</a>', "mono")
        link.set_ellipsize(Pango.EllipsizeMode.START)
        link.set_wrap(False)
        link.set_hexpand(True)
        link.set_tooltip_text(f["path"])
        head.append(link)
        if f["created"]:
            head.append(Gtk.Label(label="nouveau", css_classes=["badge"]))
        head.append(stats_box(f["added"], f["removed"], f["known"]))
        n = len(f["edits"])
        if n > 1:
            head.append(Gtk.Label(label=f"×{n}", css_classes=["sid"],
                                  tooltip_text=f"{n} modifications"))
        exp = Gtk.Expander(label_widget=head, css_classes=["change-file"])
        exp.path = f["path"]

        def fill(x, _pspec=None):  # diffs construits au premier dépliage (coloration coûteuse)
            if x.get_expanded() and x.get_child() is None:
                body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6,
                               margin_top=4, margin_bottom=6)
                for e in f["edits"]:
                    body.append(self.edit_row(e))
                x.set_child(body)
        exp.connect("notify::expanded", fill)
        exp.set_expanded(expanded)
        return exp

    def edit_row(self, e):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(spacing=8)
        top.append(Gtk.Label(label=edit_label(e, self.time_text), xalign=0, hexpand=True,
                             css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END))
        if e["hunks"]:
            top.append(stats_box(e["added"], e["removed"]))
        box.append(top)
        if e["hunks"]:
            box.append(diff_block(e["hunks"]))
        elif self.loader and e.get("id"):
            box.append(self.lazy_diff(e))
        elif e.get("tool") == "NotebookEdit" and isinstance((e.get("input") or {}).get("new_source"), str):
            box.append(code_block(e["input"]["new_source"], "python"))
        return box

    def lazy_diff(self, e):
        """Diff lu à la demande (Cursor : versions du fichier gardées dans sa base)."""
        exp = Gtk.Expander(label="Voir le diff")
        state = Gtk.Label(label="chargement…", xalign=0, css_classes=["notice"])
        exp.set_child(state)

        def shown(text):
            exp.set_child(code_block(text, "diff") if text else
                          Gtk.Label(label="Diff indisponible.", xalign=0, css_classes=["notice"]))

        def failed(err):
            exp.set_child(Gtk.Label(label=f"Lecture impossible : {err}", xalign=0, css_classes=["notice"]))
            exp.loaded = False

        def expanded(x, _pspec):
            if x.get_expanded() and not getattr(x, "loaded", False):
                x.loaded = True
                in_thread(lambda: self.loader(e), shown, failed)
        exp.connect("notify::expanded", expanded)
        return exp
