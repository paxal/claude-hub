"""Nombre de conversations à traiter sur l'icône de la barre des tâches.

Le gestionnaire de tâches de KDE (comme les docks d'Unity, Dash to Dock…) lit le
signal D-Bus com.canonical.Unity.LauncherEntry.Update : l'application y donne son
fichier .desktop et un compteur. Rien n'est à installer ; sans gestionnaire qui écoute,
le signal se perd.
"""
from .gtk import Gio, GLib

DESKTOP_URI = "application://org.paxal.ClaudeHub.desktop"
OBJECT_PATH = "/org/paxal/ClaudeHub/LauncherEntry"


def launcher_props(count):
    """Propriétés du signal pour count conversations (pastille cachée à zéro)."""
    return {"count": GLib.Variant("x", count), "count-visible": GLib.Variant("b", count > 0)}


class Launcher:
    """Envoie le compteur quand il change."""

    def __init__(self):
        self.count = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None  # pas de bus de session : pas de pastille

    def set_count(self, count):
        if count == self.count or self.bus is None:
            return
        self.count = count
        try:
            self.bus.emit_signal(None, OBJECT_PATH, "com.canonical.Unity.LauncherEntry", "Update",
                                 GLib.Variant("(sa{sv})", (DESKTOP_URI, launcher_props(count))))
        except GLib.Error:
            pass
