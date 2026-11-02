"""Initialisation de GTK 4 et utilitaires liés à la boucle GTK."""
import os
import sys
import threading

# Les moteurs GPU de GTK 4.20 (gl, vulkan) laissent des restes de l'ancien texte
# quand une étiquette change (accents, points) ; cairo redessine proprement.
os.environ.setdefault("GSK_RENDERER", "cairo")

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
