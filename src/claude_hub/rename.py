"""Renommage d'une conversation Claude par `/rename`, exécuté par claude lui-même."""
import json
import os
import shlex
import subprocess

from .gtk import Gtk
from .hosts import SSH_OPTS
from .widgets import weak_handler

RENAMED = "Session renamed to: "


def can_rename(s):
    """Seules les conversations Claude Code d'un hôte (local ou SSH) ont /rename."""
    return bool(s) and not s.get("new") and s.get("source") not in ("cursor", "claude-cloud")


def rename_session(s, name):
    """Lance `claude -p --resume id "/rename nom"` sur l'hôte et renvoie le nom retenu.

    Sans nom, claude en choisit un d'après la conversation. La commande est
    locale à claude : pas de tour du modèle, juste custom-title et agent-name
    ajoutés à la transcription.
    """
    name = " ".join(name.split())
    argv = ["claude", "-p", "--resume", s["id"], "--output-format", "json",
            "/rename " + name if name else "/rename"]
    cwd = s.get("cwd")
    if s["host"] == "local":
        cmd, cwd = argv, cwd if cwd and os.path.isdir(cwd) else None
    else:  # comme launch() : cd côté distant puis claude, dans un shell de login
        line = (f"cd {shlex.quote(cwd)} 2>/dev/null; " if cwd else "") + f"exec {shlex.join(argv)}"
        cmd, cwd = ["ssh", *SSH_OPTS, s["host"], "bash -lc " + shlex.quote(line)], None
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120,
                       stdin=subprocess.DEVNULL)
    try:
        res = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(r.stderr.strip()[-300:] or f"claude a échoué (code {r.returncode})") from None
    text = str(res.get("result") or "")
    if res.get("is_error") or not text.startswith(RENAMED):
        raise RuntimeError(text[:300] or "réponse inattendue de claude")
    return text[len(RENAMED):].strip()


class RenamePopover(Gtk.Popover):
    """Saisie du nouveau nom ; on_rename(nom) reçoit "" pour « Claude choisit »."""

    def __init__(self, title, on_rename):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=300)
        self.set_child(box)
        self.entry = Gtk.Entry(text=title or "", placeholder_text="Nouveau nom")
        box.append(self.entry)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        auto = Gtk.Button(label="Nom choisi par Claude",
                          tooltip_text="Claude propose un nom d'après la conversation")
        ok = Gtk.Button(label="Renommer", css_classes=["suggested-action"])
        row.append(auto)
        row.append(ok)
        box.append(row)

        def done(pop, name):
            pop.popdown()
            on_rename(name)
        self.entry.connect("activate", weak_handler(
            self, lambda pop, e: e.get_text().strip() and done(pop, e.get_text())))
        ok.connect("clicked", weak_handler(
            self, lambda pop, _b: pop.entry.get_text().strip() and done(pop, pop.entry.get_text())))
        auto.connect("clicked", weak_handler(self, lambda pop, _b: done(pop, "")))
        self.entry.connect("changed", weak_handler(
            self, lambda pop, e: pop.ok.set_sensitive(bool(e.get_text().strip()))))
        self.ok = ok
        ok.set_sensitive(bool(self.entry.get_text().strip()))
        self.connect("map", lambda _p: self.entry.grab_focus())
