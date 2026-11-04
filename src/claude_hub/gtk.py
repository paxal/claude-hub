"""Initialisation de GTK 4 et utilitaires liés à la boucle GTK."""
import gc
import os
import sys
import threading
import traceback

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


def collect_in_main_loop():
    """Ramasse-miettes de Python lancé depuis la boucle GTK, jamais depuis un thread.

    Python le lance dans le thread qui alloue quand un seuil est franchi, souvent un
    thread de fond (in_thread, lecture du flux de claude -p). Un widget qu'il libère y est
    détruit (dispose) pendant que la boucle GTK peut s'en servir : SIGSEGV vu dans la
    validation d'un MdView dont dispose venait de retirer le tampon. Or un widget sorti de
    la fenêtre qui s'est branché sur lui-même (self.connect(…, self.methode)) n'est libéré
    que par le ramasse-miettes. On coupe donc le déclenchement automatique, et on le refait
    ici avec les mêmes seuils : au plus une collecte par seconde, la génération la plus
    haute dont le compteur a atteint son seuil, une fois celui de la génération 0 atteint."""
    gc.disable()
    GLib.timeout_add_seconds(1, collect_if_due)


def collect_if_due():
    """Collecte due d'après les seuils de Python (voir collect_in_main_loop)."""
    counts, thresholds = gc.get_count(), gc.get_threshold()
    if thresholds[0] and counts[0] >= thresholds[0]:
        gc.collect(max(g for g in range(len(counts)) if counts[g] >= thresholds[g]))
    return True


def start_thread(target, *args):
    """Lance target(*args) dans un thread (daemon) et renvoie celui-ci. target et args ne
    sont lâchés que dans la boucle GTK : si le thread gardait la dernière référence à un
    widget (une de ses méthodes), le widget serait détruit hors de la boucle GTK (voir
    collect_in_main_loop). Une exception de target est affichée et lâchée dans le thread,
    avant que la boucle ne lâche target (qui la retient encore)."""
    thread = threading.Thread(target=_run_job, args=([target, args],), daemon=True)
    thread.start()
    return thread


def _run_job(job):
    try:
        job[0](*job[1])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    GLib.idle_add(_release, job)


def _release(job):
    job.clear()
    return False


def in_thread(fn, on_done, on_error=None):
    """Lance fn() dans un thread et renvoie le résultat (ou l'exception) dans la boucle
    GTK, où sont aussi lâchés fn, on_done et on_error (voir start_thread)."""
    threading.Thread(target=_in_thread_work, args=([fn, on_done, on_error],), daemon=True).start()


def _in_thread_work(job):
    try:
        job.append((True, job[0]()))
    except Exception as e:  # noqa: BLE001
        job.append((False, e))
    GLib.idle_add(_in_thread_done, job)


def _in_thread_done(job):
    _fn, on_done, on_error, (ok, res) = job
    job.clear()
    if ok:
        on_done(res)
    elif on_error:
        on_error(res)
    return False
