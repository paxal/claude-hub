"""Initialisation de GTK 4 et utilitaires liés à la boucle GTK."""
import os
import sys
import threading

# Moteur gl : avec cairo, GTK 4.22 sous Wayland (KWin) repeint toute la fenêtre à
# chaque image, et une seule roue qui tourne coûte 80 à 90 % d'un cœur (13 % en gl).
# GSK_RENDERER=cairo pour revenir à cairo si des restes de texte réapparaissent
# (points blancs au-dessus d'une étiquette qui change, vus avec GTK 4.20).
os.environ.setdefault("GSK_RENDERER", "gl")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

# Modificateur des raccourcis (« <Primary> » des accélérateurs) : Cmd sous macOS, Ctrl ailleurs.
MACOS = sys.platform == "darwin"
PRIMARY_MASK = Gdk.ModifierType.META_MASK if MACOS else Gdk.ModifierType.CONTROL_MASK
PRIMARY_KEY = "⌘" if MACOS else "Ctrl+"  # pour les infobulles : f"{PRIMARY_KEY}N"


def in_thread(fn, on_done, on_error=None):
    """Lance fn() dans un thread et renvoie le résultat dans la boucle GTK."""
    def work():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            if on_error:
                GLib.idle_add(on_error, e)
            return
        GLib.idle_add(on_done, res)
    threading.Thread(target=work, daemon=True).start()
