"""Application GTK."""
from pathlib import Path

from .gtk import Gdk, Gio, GLib, Gtk
from .render import BREEZE_CSS, CSS, DARK_CSS, detect_dark, is_dark
from .window import Hub


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.paxal.ClaudeHub")
        # clic sur une notification « Claude a terminé »
        action = Gio.SimpleAction.new("open-session", GLib.VariantType.new("s"))
        action.connect("activate", self.on_open_session)
        self.add_action(action)

    def on_open_session(self, _action, sid):
        self.activate()
        self.get_active_window().open_notified(sid.get_string())

    def do_startup(self):
        """Une seule fois : chaque feuille de style ajoutée fait recalculer le style de tous
        les widgets, et l'application est réactivée à chaque clic sur une notification et à
        chaque relance de claude-hub."""
        Gtk.Application.do_startup(self)
        css = Gtk.CssProvider()
        theme = Gtk.Settings.get_default().get_property("gtk-theme-name") or ""
        text = CSS + (BREEZE_CSS if theme.lower().startswith("breeze") else "")
        if hasattr(css, "load_from_string"):
            css.load_from_string(text)
        else:
            css.load_from_data(text.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).parent / "icons"))  # icônes propres à claude-hub
        Gtk.Window.set_default_icon_name(self.get_application_id())  # installée par install.sh

    def do_activate(self):
        win = self.get_active_window() or Hub(self)
        win.present()
        detect_dark(win)
        if is_dark() and not getattr(self, "dark_css", None):  # teintes des maquettes, sur fond sombre
            self.dark_css = Gtk.CssProvider()
            if hasattr(self.dark_css, "load_from_string"):
                self.dark_css.load_from_string(DARK_CSS)
            else:
                self.dark_css.load_from_data(DARK_CSS.encode())
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.dark_css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)



def main():
    App().run(None)
