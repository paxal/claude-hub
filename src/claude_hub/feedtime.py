"""Heure des éléments du fil et séparateurs de jour.

Comme dans une messagerie, chaque message, étape ou bloc du fil porte son heure
(« 11:08 ») dans une colonne à droite, de même largeur pour tous ; la date complète
est en info-bulle. Quand le jour change, un séparateur « Aujourd'hui », « Hier »,
« Samedi 31 octobre » s'insère dans le fil, comme dans la liste des conversations.
"""
import time

from .gtk import Gtk
from .listrow import day_label, local_date, time_text


def time_label(ts):
    """Heure d'un élément du fil, sa date complète en info-bulle."""
    full = f"{day_label(ts)}, {time.strftime('%H:%M:%S', time.localtime(ts))}"
    return Gtk.Label(label=time_text(ts), xalign=1, valign=Gtk.Align.START, tooltip_text=full,
                     css_classes=["msg-time"])


class Timed(Gtk.Box):
    """Élément du fil (content) suivi de son heure ; day : son jour (séparateurs)."""

    def __init__(self, content, ts):
        super().__init__(spacing=12, css_classes=["timed"])
        self.content, self.day = content, local_date(ts)
        content.set_hexpand(True)
        self.append(content)
        label = time_label(ts)
        if content.has_css_class("msg") or content.has_css_class("bubble"):
            label.add_css_class("on-bubble")  # en face de la première ligne du message
        self.append(label)


def unwrap(widget):
    """Élément du fil sans son heure."""
    return widget.content if isinstance(widget, Timed) else widget


def day_separator(ts):
    """Séparateur « —— AUJOURD'HUI —— » avant le premier élément d'un jour."""
    box = Gtk.Box(spacing=10, css_classes=["feed-day"])
    box.day = local_date(ts)
    box.append(Gtk.Separator(hexpand=True, valign=Gtk.Align.CENTER))
    box.append(Gtk.Label(label=day_label(ts).upper(), css_classes=["day-label"]))
    box.append(Gtk.Separator(hexpand=True, valign=Gtk.Align.CENTER))
    return box


def last_day(box):
    """Jour du dernier élément daté de box (le fil), None s'il n'y en a pas."""
    child = box.get_last_child()
    while child is not None:
        day = getattr(child, "day", None)
        if day is not None:
            return day
        child = child.get_prev_sibling()
    return None
