"""Fenêtre principale."""
import base64
import json
import os
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .gtk import GLib, GObject, Gdk, Gio, Graphene, Gtk, Pango, in_thread
from .hosts import (
    CLOUD, DEFAULT_LABEL, EFFORTS, HISTORY_LIMIT, model_name, MODE_HELP, MODE_LABELS, MODELS, PERMISSION_MODES, SEARCH_HISTORY_LIMIT, SSH_OPTS,
    load_hosts, load_hosts_with, load_settings, remember_mode, run_helper, save_settings,
)
from .render import esc, image_widget, make_label, message_widget, texture_from
from .cursor import (
    check_dirs, cursor_agent, cursor_context, cursor_history, cursor_result, cursor_list, cursor_live, cursor_search,
    cursor_stat, highlight_words, norm_text, state_text,
)
from .cloud import (
    cloud_history, cloud_list, cursor_cloud_history, cursor_cloud_list, iso_ts, remote_connected,
    remote_send,
)
from .attachments import IMAGE_EXTS, prepare_image
from .dirpicker import RemoteDirDialog
from .tags import TagStore, dir_matches, dir_token, parse_query, tag_matches, toggle_token
from .tagui import TAG_ICON, TagPopover, filter_popover, tag_chips
from .viewer import FileViewer, ViewerWindow, follow_link, resolve
from .permissions import PLAN_REFUSAL, RequestCard, control_response, plan_allow, plan_deny
from .planview import PlanPanel
from .subagents import AGENT_TOOLS, SubagentBlock, with_open_button
from .agentsview import AGENT_ID, AgentsPanel, agent_state
from .toolcall import ToolCall
from .todos import TodoList, TodoPanel, created_id
from .rename import RenamePopover, can_rename, rename_session
from .commands import CommandPopover, fetch_commands, match_commands, typed_command


def host_icon(host):
    return {"local": "computer-symbolic", CLOUD: "claude-hub-cloud-symbolic"}.get(
        host, "network-server-symbolic")


def run_parallel(jobs):
    """{nom: fonction} -> {nom: (résultat, erreur)}, appels lancés en même temps
    (un hôte SSH lent ne retarde pas les autres)."""
    out = {}

    def run(name, fn):
        try:
            out[name] = (fn(), None)
        except Exception as e:  # noqa: BLE001
            out[name] = (None, e)
    threads = [threading.Thread(target=run, args=job, daemon=True) for job in jobs.items()]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return out


def row_meta(s, cwd, missing):
    """Ligne « date · dossier » sous le titre d'une conversation."""
    meta = f"{GLib.DateTime.new_from_unix_local(int(s.get('mtime', 0))).format('%d/%m %H:%M')}  ·  {cwd}"
    return "dossier supprimé  ·  " + meta if missing else meta


def short_host(host):
    """user@cerberus.stockly.tech -> cerberus (badge des lignes de la liste)."""
    return host.rsplit("@", 1)[-1].split(".")[0] or host


def host_kind(host):
    return "ce PC" if host == "local" else "cloud" if host == CLOUD else "SSH"


def host_factory(with_name=True):
    """Lignes d'une liste d'hôtes : icône du type d'hôte (local, SSH, cloud) + nom."""
    factory = Gtk.SignalListItemFactory()

    def setup(_f, item):
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        # Dans la liste déroulante, le nom n'est pas tronqué : c'est lui qui donne
        # sa largeur au menu (sinon celle, minuscule, du bouton à icône seule).
        box.append(Gtk.Label(xalign=0, visible=with_name))
        item.set_child(box)

    def bind(_f, item):
        host = item.get_item().get_string()
        icon = item.get_child().get_first_child()
        icon.set_from_icon_name(host_icon(host))
        icon.set_tooltip_text(host_kind(host) if with_name else f"Nouvelle conversation sur {host}")
        icon.get_next_sibling().set_text(host)
    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory


class Hub(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Claude Hub", default_width=1150,
                         default_height=780)
        self.hosts = load_hosts()
        self.settings = load_settings()
        self.sessions = []       # conversations des hôtes cochés, la plus récente en tête
        self.host_sessions = {}  # hôte -> conversations lues au dernier chargement
        self.list_gens = {}      # hôte -> numéro du dernier chargement demandé
        self.loading = set()     # hôtes en cours de chargement
        self.list_errors = {}    # hôte -> erreurs du dernier chargement
        self.current = None      # dict de la session affichée
        self.size = 0            # taille du fichier à la dernière lecture
        self.streams = {}        # id de session -> (claude -p en cours, dict qui suit sa sortie)
        self.queues = {}         # id de session -> [(texte, images)] à envoyer après la réponse
        self.fork_at = None      # copie en préparation : {"sid", "at" (uuid), "widget", "before"}
        self.pending = {}        # id de session -> {request_id: demande can_use_tool sans réponse}
        self.agent_blocks = {}   # id de l'appel Agent -> bloc du sous-agent dans la vue affichée
        self.tool_calls = {}     # id d'un autre appel d'outil -> sa ligne dépliable
        self.remote_home = {}
        self.host_defaults = {}  # hôte -> {"model", "effort"} réglés dans son ~/.claude
        self.defaults_shown = None  # hôte dont les défauts sont affichés dans les listes
        self.state_labels = {}   # id de session -> étiquette d'état dans la liste
        self.rows = {}           # id de session -> ligne de la liste
        self.notified = {}       # id de session -> conversation à ouvrir depuis sa notification
        self.kept_unread = None  # conversation affichée marquée non lue à la main : le reste jusqu'à la quitter
        self.hub_done = {}       # id de session -> fin du dernier claude -p lancé d'ici
        self.content_hits = {}   # id -> {"role", "snippet"} trouvés dans les transcriptions
        self.drafts = {}         # id -> (texte, pièces jointes) saisis mais pas envoyés (en mémoire)
        self.unsent = {}         # id -> nouvelle conversation encore jamais envoyée
        self.search_gen = 0
        self.polling_states = False
        self.tag_store = TagStore()
        self.shown = 0           # conversations affichées par fill_list
        self.plans_opened = set()  # demandes de plan déjà ouvertes dans le volet (une seule ouverture auto)

        paned = Gtk.Paned(position=380)
        self.set_child(paned)
        paned.set_start_child(self.build_sidebar())
        # Volet de droite : plan de Claude et fichier cité dans la conversation, une page
        # chacun (caché tant qu'on n'en ouvre pas).
        self.chat_paned = Gtk.Paned(resize_end_child=False, shrink_start_child=False)
        self.chat_paned.set_start_child(self.build_chat())
        self.viewer = FileViewer(on_close=lambda _v: self.close_side("file"),
                                 on_detach=self.detach_viewer)
        self.plan_panel = PlanPanel(self.plan_run, self.plan_refuse, lambda: self.close_side("plan"))
        self.agents_panel = AgentsPanel(lambda: self.close_side("agents"))
        self.agents_polling = False
        self.side_stack = Gtk.Stack(vexpand=True)
        self.side_stack.add_titled(self.plan_panel, "plan", "Plan")
        self.side_stack.add_titled(self.viewer, "file", "Fichier")
        self.side_stack.add_titled(self.agents_panel, "agents", "Agents")
        self.side_switch = Gtk.StackSwitcher(stack=self.side_stack, halign=Gtk.Align.CENTER,
                                             margin_top=6)
        self.side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["side"])
        self.side.append(self.side_switch)
        self.side.append(self.side_stack)
        for w in (self.viewer, self.plan_panel, self.agents_panel, self.side):
            w.set_visible(False)
        self.chat_paned.set_end_child(self.side)
        paned.set_end_child(self.chat_paned)
        shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>n"),
                                                Gtk.CallbackAction.new(lambda *_a: self.new_here() or True)))
        self.add_controller(shortcuts)

        # revenir sur la fenêtre vaut lecture de la conversation affichée
        self.connect("notify::is-active", lambda w, _p: w.is_active() and w.current
                     and w.current["id"] != w.kept_unread and w.mark_read(w.current["id"]))
        self.hosts_changed()
        GLib.timeout_add_seconds(4, self.poll)
        GLib.timeout_add_seconds(3, self.poll_states)
        GLib.timeout_add_seconds(4, self.poll_agents)

    @property
    def proc(self):
        """Le claude -p lancé depuis le hub pour la conversation affichée, s'il tourne."""
        s = self.current
        return self.streams.get(s["id"], (None, None))[0] if s else None

    def is_streaming(self, s):
        """Vrai si ce dict reçoit en direct la sortie de son claude -p."""
        return self.streams.get(s["id"], (None, None))[1] is s

    # -- construction --
    def build_sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       margin_top=6, margin_start=6, margin_end=6, margin_bottom=6)
        row = Gtk.Box(spacing=4)
        # Hôtes listés : cases à cocher dans un popover, résumé (icônes + noms) sur le bouton.
        self.hosts_btn = Gtk.MenuButton(hexpand=True, tooltip_text="Hôtes dont les conversations sont listées")
        self.hosts_summary = Gtk.Box(spacing=4)
        self.hosts_btn.set_child(self.hosts_summary)
        self.hosts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6,
                                 margin_bottom=6, margin_start=6, margin_end=6)
        self.hosts_btn.set_popover(Gtk.Popover(child=self.hosts_box))
        self.build_host_checks()
        row.append(self.hosts_btn)

        add = Gtk.MenuButton(icon_name="list-add-symbolic", tooltip_text="Ajouter un hôte SSH")
        pop_box = Gtk.Box(spacing=4, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        self.host_entry = Gtk.Entry(placeholder_text="hôte ou user@hôte")
        self.host_entry.connect("activate", self.add_host)
        pop_box.append(self.host_entry)
        add.set_popover(Gtk.Popover(child=pop_box))
        row.append(add)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rafraîchir")
        refresh.connect("clicked", lambda _b: self.refresh_list())
        row.append(refresh)
        side.append(row)

        row = Gtk.Box(spacing=4)
        # Hôte de la nouvelle conversation : icône seule sur le bouton, noms dans la liste.
        self.new_hosts = [h for h in self.hosts if h != CLOUD]
        self.new_host_dd = Gtk.DropDown(model=Gtk.StringList.new(self.new_hosts),
                                        factory=host_factory(with_name=False),
                                        list_factory=host_factory())
        self.new_host_dd.connect("notify::selected", lambda *_: self.new_host_changed())
        self.new_host_dd.set_tooltip_text(f"Nouvelle conversation sur {self.new_host}")
        row.append(self.new_host_dd)
        self.dir_entry = Gtk.Entry(placeholder_text="dossier (défaut : ~)", hexpand=True)
        row.append(self.dir_entry)
        self.dir_btn = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Choisir le dossier")
        self.dir_btn.connect("clicked", self.pick_dir)
        row.append(self.dir_btn)
        self.new_btn = Gtk.Button(label="Nouvelle")
        self.new_btn.connect("clicked", self.new_session)
        row.append(self.new_btn)
        side.append(row)

        row = Gtk.Box(spacing=4)
        self.search = Gtk.SearchEntry(placeholder_text="Rechercher…  #tag  #dossier/  dir:~/chemin",
                                      hexpand=True, search_delay=350,
                                      tooltip_text="Texte : titres et contenu des conversations\n"
                                                   "#tag : conversations taguées, sur tous les hôtes\n"
                                                   "#dossier/ : tous les tags rangés sous dossier/\n"
                                                   "dir:~/chemin : conversations lancées dans ce dossier")
        self.search.connect("search-changed", self.on_search)
        row.append(self.search)
        for icon, tip, make in ((TAG_ICON, "Filtrer par tag (tous les hôtes)", self.tags_menu),
                                ("folder-symbolic", "Filtrer par dossier de travail", self.dirs_menu)):
            btn = Gtk.MenuButton(icon_name=icon, tooltip_text=tip)
            btn.set_create_popup_func(make)
            row.append(btn)
        side.append(row)

        row = Gtk.Box(spacing=10, margin_start=2)
        self.show_claude = Gtk.CheckButton(label="Claude", active=self.settings["claude"],
                                           tooltip_text="Récupérer les conversations Claude Code")
        self.show_cursor = Gtk.CheckButton(label="Cursor", active=self.settings["cursor"],
                                           tooltip_text="Récupérer les conversations Cursor")
        self.show_archived = Gtk.CheckButton(label="Archivées", active=self.settings["show_archived"],
                                             tooltip_text="Afficher aussi les conversations archivées")
        for key, btn in (("claude", self.show_claude), ("cursor", self.show_cursor)):
            btn.connect("toggled", self.on_source_toggled, key)
        self.show_archived.connect("toggled", self.on_archived_toggled)
        for btn in (self.show_claude, self.show_cursor, self.show_archived):
            row.append(btn)
        side.append(row)

        self.listbox = Gtk.ListBox()
        # plus récente en tête ; touch() fait remonter une ligne sans reconstruire la liste
        self.listbox.set_sort_func(lambda a, b: (b.session.get("mtime", 0) > a.session.get("mtime", 0))
                                   - (b.session.get("mtime", 0) < a.session.get("mtime", 0)))
        self.listbox.connect("row-activated", self.on_row)
        # clic droit sur une ligne : menu contextuel (actions par id de session)
        for name, handler in (("toggle-unread", self.toggle_unread), ("row-tags", self.row_tags),
                              ("row-rename", self.row_rename), ("row-archive", self.row_archive),
                              ("row-dir", self.row_dir)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            # après la fermeture du menu : un popover ouvert par-dessus lui serait refusé
            action.connect("activate", lambda _a, sid, handler=handler:
                           GLib.idle_add(lambda: handler(sid.get_string()) and False))
            self.add_action(action)
        menu_click = Gtk.GestureClick(button=3)
        menu_click.connect("pressed", self.on_row_menu)
        self.listbox.add_controller(menu_click)
        side.append(Gtk.ScrolledWindow(child=self.listbox, vexpand=True))
        self.list_status = Gtk.Label(xalign=0, css_classes=["sid"], ellipsize=Pango.EllipsizeMode.END)
        side.append(self.list_status)
        return side

    def build_chat(self):
        chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8, margin_start=12,
                       margin_end=12, margin_bottom=4)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.title.set_markup("<b>Choisis une conversation</b>")
        self.subtitle = Gtk.Label(xalign=0, css_classes=["sid"], selectable=True,
                                  ellipsize=Pango.EllipsizeMode.MIDDLE)
        top = Gtk.Box(spacing=8)
        top.append(self.title)
        self.activity = Gtk.Box(spacing=6, css_classes=["activity"], visible=False)
        self.spinner = Gtk.Spinner()
        self.activity_label = Gtk.Label()
        self.activity.append(self.spinner)
        self.activity.append(self.activity_label)
        top.append(self.activity)
        self.agents_btn = Gtk.Button(visible=False, css_classes=["flat"],
                                     tooltip_text="Sous-agents de cette conversation")
        self.agents_btn.connect("clicked", lambda _b: self.toggle_agents())
        top.append(self.agents_btn)
        self.web_btn = Gtk.Button(label="Ouvrir sur claude.ai ↗", visible=False, css_classes=["flat"],
                                  tooltip_text="Ouvrir cette session dans le navigateur")
        self.web_btn.connect("clicked", lambda _b: Gtk.UriLauncher.new(
            f"https://claude.ai/code/{self.current['id']}").launch(self, None, None))
        top.append(self.web_btn)
        self.new_here_btn = Gtk.Button(icon_name="document-new-symbolic", visible=False,
                                       css_classes=["flat"],
                                       tooltip_text="Nouvelle conversation dans ce dossier (Ctrl+N)")
        self.new_here_btn.connect("clicked", lambda _b: self.new_here())
        top.append(self.new_here_btn)
        self.tag_btn = Gtk.Button(icon_name=TAG_ICON, visible=False, css_classes=["flat"],
                                  tooltip_text="Tags de cette conversation")
        self.tag_btn.connect("clicked", lambda b: self.edit_tags(b, self.current, self.current["host"]))
        top.append(self.tag_btn)
        self.rename_btn = Gtk.Button(icon_name="document-edit-symbolic", visible=False,
                                     css_classes=["flat"], tooltip_text="Renommer la conversation")
        self.rename_btn.connect("clicked", self.ask_rename)
        top.append(self.rename_btn)
        head.append(top)
        head.append(self.subtitle)
        self.head_tags = Gtk.Box(spacing=4, visible=False, margin_top=2)
        head.append(self.head_tags)
        chat.append(head)

        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=8)
        self.scroll = Gtk.ScrolledWindow(child=self.msgs, vexpand=True)
        chat.append(self.scroll)
        # Collé en bas : la vue suit les changements de taille (message mis en page plus
        # tard, bandeau « Claude réfléchit » qui réduit la zone) tant qu'on n'a pas remonté.
        self.follow_end = self.follow_pending = False
        vadj = self.scroll.get_vadjustment()
        vadj.connect("changed", self.on_scroll_resized)
        vadj.connect("value-changed", self.on_scroll_moved)

        # Tâches de Claude (TodoWrite, TaskCreate…), tant qu'il en reste à faire.
        self.todo_panel = TodoPanel()
        chat.append(self.todo_panel)

        # Bandeau animé au-dessus de la saisie pendant que Claude travaille.
        self.thinking = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                     transition_duration=200)
        bar = Gtk.Box(spacing=8, css_classes=["thinking"], margin_start=8, margin_end=8)
        self.thinking_spinner = Gtk.Spinner()
        self.thinking_label = Gtk.Label(xalign=0)
        # Trois points toujours présents dont seule l'opacité change : la taille ne
        # varie jamais (changer le texte laissait des restes de l'ancien rendu).
        self.thinking_dot_labels = [Gtk.Label(label=".") for _ in range(3)]
        bar.append(self.thinking_spinner)
        self.thinking_text_box = Gtk.Box()
        self.thinking_text_box.append(self.thinking_label)
        for dot in self.thinking_dot_labels:
            self.thinking_text_box.append(dot)
        bar.append(self.thinking_text_box)
        self.thinking_bar = bar
        self.thinking.set_child(bar)
        chat.append(self.thinking)
        self.thinking_text, self.thinking_dots, self.thinking_timer = "", 0, 0

        # Copie de la conversation à partir d'un message (bouton au survol des messages).
        self.fork_bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6,
                                css_classes=["fork-bar"], visible=False)
        self.fork_bar.append(Gtk.Image(icon_name="claude-hub-fork-symbolic"))
        self.fork_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.fork_bar.append(self.fork_label)
        cancel = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                            tooltip_text="Annuler la copie", valign=Gtk.Align.CENTER)
        cancel.connect("clicked", lambda _b: self.cancel_fork())
        self.fork_bar.append(cancel)
        chat.append(self.fork_bar)

        # Messages en attente de la fin de la réponse en cours.
        self.queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=8,
                                 margin_end=8, margin_top=6, visible=False)
        chat.append(self.queue_box)

        self.attachments = []    # [(media_type, octets)]
        self.attach_box = Gtk.Box(spacing=6, margin_start=8, margin_top=6, visible=False,
                                  halign=Gtk.Align.START)
        chat.append(self.attach_box)

        bottom = Gtk.Box(spacing=6, margin_top=6, margin_start=8, margin_end=8, margin_bottom=8)
        clip = Gtk.Button(icon_name="mail-attachment-symbolic", valign=Gtk.Align.START,
                          tooltip_text="Joindre des images (ou Ctrl+V, ou glisser-déposer)")
        clip.connect("clicked", self.pick_images)
        bottom.append(clip)
        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, hexpand=True,
                                  top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        self.input.connect("paste-clipboard", self.on_paste)
        self.input.get_buffer().connect("changed", lambda _b: (self.draft_changed(),
                                                               self.update_commands()))
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, Gdk.Texture])
        drop.connect("drop", self.on_drop)
        chat.add_controller(drop)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.input.add_controller(keys)
        input_scroll = Gtk.ScrolledWindow(child=self.input, hexpand=True, min_content_height=70,
                                          max_content_height=220, propagate_natural_height=True)
        bottom.append(input_scroll)
        # Complétion des commandes « / » : liste par (hôte, dossier), demandée à claude.
        self.commands = {}  # (hôte, dossier) -> [commande] | "loading" | Exception
        self.cmd_popover = CommandPopover(input_scroll, self.pick_command)
        self.cmd_dismissed = False
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tuning = Gtk.Box(spacing=4)
        self.model_dd = self.choice_dd(MODELS, "model", "Modèle (--model)", self.model_label)
        self.effort_dd = self.choice_dd(EFFORTS, "effort", "Effort de raisonnement (--effort)")
        tuning.append(self.model_dd)
        tuning.append(self.effort_dd)
        side.append(tuning)
        self.mode_dd = Gtk.DropDown.new_from_strings([MODE_LABELS[m] for m in PERMISSION_MODES])
        self.mode_dd.set_tooltip_text(
            "Mode de permissions, retenu pour chaque conversation ; les demandes de Claude "
            "s'affichent dans le fil\n\n" + "\n".join(f"{MODE_LABELS[m]} : {MODE_HELP[m]}" for m in PERMISSION_MODES)
            + "\n\nMaj+Tab : bascule automatique / plan")
        self.mode_dd.set_selected(PERMISSION_MODES.index(self.settings["mode"]))
        self.syncing_mode = False  # vrai quand c'est la conversation qui règle la liste, pas l'utilisateur
        self.mode_dd.connect("notify::selected", lambda *_: self.push_mode())
        side.append(self.mode_dd)
        # Bouton à menu : « Envoyer » et ses variantes (tout de suite, en file).
        # hexpand=False : le bouton remplit la boîte sans élargir la colonne (sinon
        # elle prend autant de place que la saisie)
        send_box = Gtk.Box(css_classes=["linked"], hexpand=False)
        self.send_btn = Gtk.Button(label="Envoyer", css_classes=["suggested-action"], hexpand=True)
        self.send_btn.connect("clicked", lambda _b: self.send())
        send_box.append(self.send_btn)
        action = Gio.SimpleAction.new("send", GLib.VariantType.new("s"))
        action.connect("activate", lambda _a, mode: self.send(mode.get_string()))
        self.add_action(action)
        menu = Gio.Menu()
        for label, mode, accel in (("Envoyer", "auto", "Return"),
                                   ("Envoyer tout de suite", "now", "<Control>Return"),
                                   ("Mettre en file d'attente", "queue", "<Alt>Return")):
            item = Gio.MenuItem.new(label, f"win.send::{mode}")
            item.set_attribute_value("accel", GLib.Variant.new_string(accel))
            menu.append_item(item)
        # Bouton ordinaire plutôt qu'un Gtk.MenuButton : Breeze ne dessine pas sa flèche
        # et ne l'accole pas (bouton « toggle » imbriqué). Chevron des listes déroulantes.
        arrow = Gtk.Button(icon_name="go-down-symbolic", css_classes=["suggested-action"],
                           tooltip_text="Autres façons d'envoyer")
        self.send_menu = Gtk.PopoverMenu.new_from_model(menu)
        self.send_menu.set_parent(arrow)
        arrow.connect("clicked", lambda _b: self.send_menu.popup())
        send_box.append(arrow)
        side.append(send_box)
        self.stop_btn = Gtk.Button(label="Arrêter", css_classes=["destructive-action"], visible=False)
        self.stop_btn.connect("clicked", lambda _b: self.stop())
        side.append(self.stop_btn)
        bottom.append(side)
        chat.append(bottom)
        self.status = Gtk.Label(xalign=0, css_classes=["sid"], margin_start=10, margin_bottom=4,
                                ellipsize=Pango.EllipsizeMode.MIDDLE)
        chat.append(self.status)
        return chat

    def model_label(self, model):
        """« opus · Opus 5.5 » pour un alias, « Opus 5.5 » pour un identifiant complet."""
        version = self.settings["model_versions"].get(model)
        return f"{model} · {model_name(version)}" if version else model_name(model)

    def relabel_models(self):
        """Remet les versions à jour dans la liste des modèles (après une réponse de claude)."""
        sel = self.model_dd.get_selected()
        self.model_dd.get_model().splice(1, len(MODELS) - 1, [self.model_label(m) for m in MODELS[1:]])
        self.model_dd.set_selected(sel)
        self.defaults_shown = None
        self.show_defaults()

    def choice_dd(self, options, key, tooltip, label=str):
        """Liste déroulante dont le choix est mémorisé dans settings[key] (None = défaut)."""
        dd = Gtk.DropDown.new_from_strings([options[0]] + [label(o) for o in options[1:]])
        dd.set_tooltip_text(tooltip)
        if self.settings[key] in options[1:]:
            dd.set_selected(options.index(self.settings[key]))

        def changed(dd, _pspec):
            i = dd.get_selected()
            if i >= len(options):  # aucun choix, le temps qu'un libellé soit remplacé
                return
            self.settings[key] = options[i] if i > 0 else None
            save_settings(self.settings)
        dd.connect("notify::selected", changed)
        return dd

    def show_defaults(self):
        """Affiche dans le premier choix des listes le modèle et l'effort par défaut de l'hôte."""
        host = self.current["host"] if self.current else self.new_host
        if host == self.defaults_shown:
            return
        self.defaults_shown = host
        known = self.host_defaults.get(host)
        for dd, options, key, what in ((self.model_dd, MODELS, "model", "Modèle (--model)"),
                                       (self.effort_dd, EFFORTS, "effort",
                                        "Effort de raisonnement (--effort)")):
            value = known and known.get(key)
            if value and key == "model":
                value = self.model_label(value)
            sel = dd.get_selected()
            dd.get_model().splice(0, 1, [DEFAULT_LABEL[key].format(value) if value else options[0]])
            dd.set_selected(sel)
            if known is not None:
                what += f"\nDéfaut sur {host} : {value or 'non réglé, laissé à claude'}"
            dd.set_tooltip_text(what)
        if known is None and host != CLOUD:
            def done(res):
                self.host_defaults[host] = res
                if self.defaults_shown == host:
                    self.defaults_shown = None
                    self.show_defaults()
            in_thread(lambda: run_helper(host, "defaults"), done)

    # -- hôtes et liste --
    def shown_hosts(self):
        """Hôtes cochés, dans l'ordre de la configuration."""
        return [h for h in self.hosts if h in self.settings["hosts_shown"]]

    def hosts_label(self, hosts=None):
        hosts = self.shown_hosts() if hosts is None else hosts
        return ", ".join(hosts) or "aucun hôte"

    @property
    def new_host(self):
        """Hôte où « Nouvelle » crée une conversation."""
        return self.new_hosts[self.new_host_dd.get_selected()]

    def build_host_checks(self):
        """Cases des hôtes dans le popover, et résumé sur le bouton."""
        while (child := self.hosts_box.get_first_child()):
            self.hosts_box.remove(child)
        shown = self.settings["hosts_shown"]
        for h in self.hosts:
            check = Gtk.CheckButton(active=h in shown, tooltip_text=host_kind(h))
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(icon_name=host_icon(h)))
            box.append(Gtk.Label(label=h, xalign=0))
            check.set_child(box)
            check.connect("toggled", self.on_host_toggled, h)
            self.hosts_box.append(check)
        while (child := self.hosts_summary.get_first_child()):
            self.hosts_summary.remove(child)
        hosts = self.shown_hosts()
        for h in hosts:
            self.hosts_summary.append(Gtk.Image(icon_name=host_icon(h)))
        self.hosts_summary.append(Gtk.Label(label=self.hosts_label(hosts), xalign=0, hexpand=True,
                                            ellipsize=Pango.EllipsizeMode.END))

    def on_host_toggled(self, check, host):
        shown = [h for h in self.shown_hosts() if h != host]
        self.settings["hosts_shown"] = shown + [host] if check.get_active() else shown
        save_settings(self.settings)
        self.hosts_changed(added=[host] if check.get_active() else [])

    def hosts_changed(self, added=None):
        """Les hôtes cochés ont changé : on garde ce qui est déjà lu, on charge les
        nouveaux (tous si added est None)."""
        hosts = self.shown_hosts()
        self.settings["hosts_shown"] = hosts  # oublie les hôtes retirés de la configuration
        self.build_host_checks()
        for h in list(self.host_sessions):
            if h not in hosts:
                del self.host_sessions[h]
                self.list_errors.pop(h, None)
                self.loading.discard(h)
        if added is None and self.new_host not in hosts:  # au démarrage
            first = next((h for h in hosts if h in self.new_hosts), "local")
            self.new_host_dd.set_selected(self.new_hosts.index(first))
        self.content_hits = {}
        self.merge_sessions()
        self.fill_list()
        if not hosts:
            self.list_status.set_text("Aucun hôte coché : choisis-en dans la liste du haut.")
        self.refresh_list(added)
        self.show_defaults()

    def new_host_changed(self):
        self.new_host_dd.set_tooltip_text(f"Nouvelle conversation sur {self.new_host}")
        self.show_defaults()

    def add_host(self, entry):
        h = entry.get_text().strip()
        entry.set_text("")
        if not h or h in self.hosts:
            return
        self.hosts = load_hosts_with(self.hosts[1:-1] + [h])
        new_host = self.new_host
        self.new_hosts = [x for x in self.hosts if x != CLOUD]
        self.new_host_dd.set_model(Gtk.StringList.new(self.new_hosts))
        self.new_host_dd.set_selected(self.new_hosts.index(new_host))
        self.settings["hosts_shown"].append(h)
        save_settings(self.settings)
        self.hosts_changed(added=[h])

    def on_search(self, _entry):
        """Filtre immédiat sur les titres, puis recherche dans les transcriptions."""
        self.search_gen += 1
        tags_f, dirs_f, q = parse_query(self.search.get_text())
        gen, hosts = self.search_gen, [h for h in self.shown_hosts() if h != CLOUD]
        self.content_hits = {}
        self.fill_list()
        if len(q) < 3:
            if tags_f or dirs_f:
                return self.filter_status(tags_f, dirs_f)
            return self.list_status.set_text("")
        self.list_status.set_text("Recherche dans les conversations…")
        with_cursor, with_claude = self.show_cursor.get_active(), self.show_claude.get_active()

        def fetch():
            jobs = {h: (lambda h=h: run_helper(h, "search", q, 200)) for h in hosts if with_claude}
            if with_cursor:
                jobs["Cursor"] = lambda: cursor_search(q)
            hits, errors = {}, []
            for name, (res, err) in run_parallel(jobs).items():
                if err:
                    errors.append(f"{name} : {err}")
                else:
                    hits.update(res)
            return hits, errors

        def done(out):
            hits, errors = out
            if gen != self.search_gen:
                return  # requête dépassée
            show_archived = self.show_archived.get_active()
            known = {s["id"] for s in self.sessions if show_archived or not self.is_archived(s)}
            self.content_hits = {k: v for k, v in hits.items() if k in known}
            self.fill_list()
            n = self.shown  # avec les filtres #tag / dir:, et les titres qui correspondent
            self.list_status.set_text(" · ".join(errors)[:300] if errors else
                                      f"{n} conversation{'s' * (n > 1)} contien{'nen' * (n > 1)}t « {q} »")
        in_thread(fetch, done)

    def refresh_list(self, hosts=None):
        """Recharge la liste des hôtes donnés (par défaut tous les hôtes cochés) ;
        chacun s'affiche dès qu'il répond, sans attendre les plus lents."""
        hosts = [h for h in self.shown_hosts() if hosts is None or h in hosts]
        all_hosts = list(self.hosts)
        with_claude, with_cursor = self.show_claude.get_active(), self.show_cursor.get_active()

        def fetch(host):
            errors, res, cursor = [], {"home": None, "sessions": []}, []
            if host == CLOUD:
                if with_claude:
                    try:
                        res["sessions"] = cloud_list()
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"cloud : {e}")
                if with_cursor:
                    try:
                        cursor = cursor_cloud_list()
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"Cursor : {e}")
                return res, cursor, errors
            if with_claude:
                try:
                    res = run_helper(host, "list")
                except Exception as e:  # noqa: BLE001 — Cursor reste lisible hors ligne
                    errors.append(f"{host} : {e}")
            if with_cursor:
                try:
                    cursor = cursor_list(host, all_hosts)
                    check_dirs(host, cursor)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"Cursor ({host}) : {e}")
            return res, cursor, errors

        def done(host, gen, out):
            if gen != self.list_gens[host] or host not in self.shown_hosts():
                return  # chargement dépassé, ou hôte décoché entre-temps
            res, cursor, errors = out
            self.loading.discard(host)
            self.list_errors[host] = errors
            if res["home"]:
                self.remote_home[host] = res["home"]
            listed = res["sessions"] + cursor
            for s in listed:
                s["host"] = host
            self.host_sessions[host] = listed
            self.merge_sessions()
            self.tag_store.refresh(host, listed)
            self.fill_list()
            self.show_list_status()
            if not self.loading and self.search.get_text().strip():
                self.on_search(None)  # relance la recherche sur la nouvelle liste

        for host in hosts:
            gen = self.list_gens[host] = self.list_gens.get(host, 0) + 1
            self.loading.add(host)
            in_thread(lambda host=host: fetch(host), lambda out, host=host, gen=gen: done(host, gen, out))
        self.show_list_status()

    def merge_sessions(self):
        """self.sessions : conversations lues sur les hôtes cochés et nouvelles pas encore
        listées, la plus récente en tête."""
        busy = {s["id"] for s in self.sessions if s.get("was_busy")}
        sessions = []
        for host in self.shown_hosts():
            listed = self.host_sessions.get(host, [])
            sessions += listed + self.pending_new(host, listed)
        self.sessions = sorted(sessions, key=lambda s: -s["mtime"])
        for s in self.sessions:  # pour repérer la fin d'une réponse au prochain relevé
            s["was_busy"] = s["id"] in busy

    def show_list_status(self):
        """Hôtes encore en chargement, sinon erreurs, sinon le décompte."""
        errors = [e for h in self.shown_hosts() for e in self.list_errors.get(h, [])]
        if self.loading:
            loading = [h for h in self.shown_hosts() if h in self.loading]
            self.list_status.set_text(" · ".join([f"Chargement de {self.hosts_label(loading)}…"]
                                                 + errors)[:300])
        elif errors:
            self.list_status.set_text(" · ".join(errors)[:300])
        elif self.shown_hosts():
            self.update_list_status()

    def fill_list(self):
        self.state_labels = {}
        self.rows = {}
        while (child := self.listbox.get_first_child()):
            self.listbox.remove(child)
        tags_f, dirs_f, query = parse_query(self.search.get_text())
        q = norm_text(query)
        show_archived = self.show_archived.get_active()
        hosts = self.shown_hosts()
        entries = [(s["host"], s) for s in self.sessions]
        if tags_f:  # la vue par tag montre aussi les conversations taguées des autres hôtes
            seen = {s["id"] for s in self.sessions}
            entries += [(h, s) for h, s in self.tag_store.tagged()
                        if s["id"] not in seen and h in self.hosts]
            entries.sort(key=lambda e: -e[1].get("mtime", 0))
        self.shown = 0
        for host, s in entries:
            if not self.source_shown(s):
                continue
            archived = self.is_archived(s)
            if archived and not show_archived:
                continue
            tags = self.tag_store.tags(s["id"])
            if not all(tag_matches(tags, f) for f in tags_f):
                continue
            home = self.remote_home.get(host, "")
            if dirs_f and not any(dir_matches(s.get("cwd"), d, home) for d in dirs_f):
                continue
            label = s.get("title") or s.get("first") or ("Nouvelle conversation" if s.get("new")
                                                         else "(sans titre)")
            cwd = s.get("cwd") or "sans dossier"
            if home and cwd.startswith(home):
                cwd = "~" + cwd[len(home):]
            hit = self.content_hits.get(s["id"])
            if q and not hit and q not in norm_text(f"{label} {cwd} {s['id']} {' '.join(tags)}"):
                continue
            self.shown += 1
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4,
                          margin_start=4, margin_end=4, hexpand=True)
            box.append(Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["row-title"]))
            missing = s.get("cwd_exists") is False
            line = Gtk.Box(spacing=6)
            if len(hosts) > 1 or host not in hosts:
                other = Gtk.Box(spacing=3, css_classes=["badge", "host"], tooltip_text=f"Sur {host}")
                other.append(Gtk.Image(icon_name=host_icon(host), pixel_size=12))
                other.append(Gtk.Label(label=short_host(host), ellipsize=Pango.EllipsizeMode.END,
                                       max_width_chars=12))
                line.append(other)
            if s.get("source") == "cursor":
                line.append(Gtk.Label(label="Cursor", css_classes=["badge"]))
            elif s.get("source") == "claude-cloud":
                line.append(Gtk.Label(label="claude.ai", css_classes=["badge", "cloud"]))
            state = Gtk.Label(css_classes=["state"])
            line.append(state)
            meta = Gtk.Label(label=row_meta(s, cwd, missing), xalign=0, ellipsize=Pango.EllipsizeMode.END,
                             hexpand=True, css_classes=["sid"])
            line.append(meta)
            box.append(line)
            if hit:
                snip = Gtk.Label(xalign=0, wrap=True, lines=2, css_classes=["snippet"],
                                 ellipsize=Pango.EllipsizeMode.END)
                prefix = {"user": "Toi : ", "assistant": "Claude : "}.get(hit["role"], "")
                snip.set_markup(esc(prefix) + highlight_words(hit["snippet"], query))
                box.append(snip)
            tag_box = Gtk.Box(spacing=4, margin_top=2)
            tag_chips(tag_box, tags, self.filter_tag)
            box.append(tag_box)
            if host in hosts:
                self.state_labels[s["id"]] = state
            self.set_state(state, self.list_state(s["id"], s.get("live") if host in hosts else None))
            outer = Gtk.Box(spacing=2)
            outer.append(box)
            row = Gtk.ListBoxRow(child=outer)
            row.session, row.host, row.tag_box = s, host, tag_box
            row.meta, row.cwd_text, row.missing = meta, cwd, missing
            self.rows[s["id"]] = row
            self.listbox.append(row)
            if s["id"] in self.unsent:
                continue  # pas de tags ni d'archive : elle n'existe pas encore pour claude
            tag_btn = Gtk.Button(icon_name=TAG_ICON, tooltip_text="Tags", valign=Gtk.Align.CENTER,
                                 css_classes=["flat", "archive-btn"])
            tag_btn.connect("clicked", lambda b, s=s, host=host: self.edit_tags(b, s, host))
            outer.append(tag_btn)
            btn = Gtk.Button(icon_name="archive-extract-symbolic" if archived else "archive-insert-symbolic",
                             tooltip_text="Désarchiver" if archived else "Archiver",
                             css_classes=["flat", "archive-btn"], valign=Gtk.Align.CENTER,
                             margin_end=12)  # hors de la zone de la barre de défilement (overlay)
            outer.append(btn)
            if archived:
                row.add_css_class("archived")
            if s["id"] in self.settings["unread"]:
                row.add_css_class("unread")
            if missing:
                row.add_css_class("missing")
                row.set_tooltip_text(f"Le dossier {s['cwd']} n'existe plus sur {host}")
            btn.connect("clicked", lambda _b, row=row: self.toggle_archive(row))

    # -- tags --
    def edit_tags(self, button, s, host):
        known = [t for t, _n in self.tag_store.counts().most_common()]
        pop = TagPopover(self.tag_store.tags(s["id"]), known,
                         lambda tags: self.set_tags(s, host, tags))
        pop.set_parent(button)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def set_tags(self, s, host, tags):
        """Enregistre les tags et met à jour sur place les pastilles affichées."""
        tags = self.tag_store.set(s, host, tags)
        row = self.listbox.get_first_child()
        while row:
            if getattr(row, "session", None) and row.session["id"] == s["id"]:
                tag_chips(row.tag_box, tags, self.filter_tag)
            row = row.get_next_sibling()
        if self.current and self.current["id"] == s["id"]:
            tag_chips(self.head_tags, tags, self.filter_tag)
        return tags

    def apply_token(self, token, kind):
        self.search.set_text(toggle_token(self.search.get_text(), token, kind))
        self.search.set_position(-1)

    def filter_tag(self, tag):
        self.apply_token(f"#{tag}", "#")

    def tags_menu(self, btn):
        active = parse_query(self.search.get_text())[0]
        items = [(f"#{t}", f"#{t}", n) for t, n in sorted(self.tag_store.counts().items())]
        btn.set_popover(filter_popover(
            "Tags — sur tous les hôtes ; « / » regroupe comme un dossier", items,
            {f"#{t}" for t in active}, lambda tok: self.apply_token(tok, "#"),
            "Aucun tag pour l'instant : utilise le bouton étiquette d'une conversation."))

    def dirs_menu(self, btn):
        """Dossiers de travail des conversations de l'hôte, les plus utilisés d'abord."""
        counts, last = {}, {}
        for s in self.sessions:
            cwd = self.dir_of(s)
            if not cwd or not self.source_shown(s):
                continue
            counts[cwd] = counts.get(cwd, 0) + 1
            last[cwd] = max(last.get(cwd, 0), s.get("mtime", 0))
        dirs = sorted(counts, key=lambda d: (-counts[d], -last[d]))
        active = parse_query(self.search.get_text())[1]
        items = [(d, dir_token(d), counts[d]) for d in dirs]
        btn.set_popover(filter_popover(
            f"Dossiers de travail sur {self.hosts_label()}", items, {dir_token(d) for d in active},
            lambda tok: self.apply_token(tok, "dir:"),
            "Aucun dossier connu sur les hôtes cochés."))

    def filter_status(self, tags_f, dirs_f):
        n = self.shown
        what = " ".join([f"#{t}" for t in tags_f] + [dir_token(d) for d in dirs_f])
        where = " · tous les hôtes" if tags_f else f" sur {self.hosts_label()}"
        self.list_status.set_text(f"{n} conversation{'s' * (n > 1)} · {what}{where}")

    # -- filtres et archives --
    def source_shown(self, s):
        if s.get("source") == "cursor":
            return self.show_cursor.get_active()
        return self.show_claude.get_active()

    def is_archived(self, s):
        sid = s["id"]
        if sid in self.settings["unarchived"]:
            return False
        return (sid in self.settings["archived"] or bool(s.get("cursor_archived"))
                or bool(s.get("cloud_archived")))

    def toggle_archive(self, row):
        s, st = row.session, self.settings
        if self.is_archived(s):
            st["archived"].discard(s["id"])
            if s.get("cursor_archived") or s.get("cloud_archived"):
                st["unarchived"].add(s["id"])
        else:
            st["unarchived"].discard(s["id"])
            st["archived"].add(s["id"])
        save_settings(st)
        archived = self.is_archived(s)
        if archived and not self.show_archived.get_active():
            self.listbox.remove(row)  # sur place : la liste garde sa position
        else:
            btn = row.get_child().get_last_child()
            btn.set_icon_name("archive-extract-symbolic" if archived else "archive-insert-symbolic")
            btn.set_tooltip_text("Désarchiver" if archived else "Archiver")
            (row.add_css_class if archived else row.remove_css_class)("archived")
        self.update_list_status()

    def update_list_status(self):
        shown = [s for s in self.sessions if self.source_shown(s)]
        n_claude = sum(1 for s in shown if s.get("source") != "cursor")
        n_archived = sum(1 for s in shown if self.is_archived(s))
        parts = []
        if self.show_claude.get_active():
            parts.append(f"{n_claude} Claude")
        if self.show_cursor.get_active():
            parts.append(f"{len(shown) - n_claude} Cursor")
        text = (" + ".join(parts) or "Aucun outil coché") + f" sur {self.hosts_label()}"
        if n_archived:
            text += f" · {n_archived} archivée{'s' * (n_archived > 1)}"
        self.list_status.set_text(text)

    def on_source_toggled(self, btn, key):
        self.settings[key] = btn.get_active()
        save_settings(self.settings)
        self.fill_list()     # masque tout de suite ce qui est décoché
        self.refresh_list()  # puis ne récupère que les outils cochés

    def on_archived_toggled(self, btn):
        self.settings["show_archived"] = btn.get_active()
        save_settings(self.settings)
        self.fill_list()
        self.update_list_status()

    @staticmethod
    def set_state(label, status):
        text, css = state_text(status)
        label.set_text(text)
        label.set_visible(bool(text))
        label.set_css_classes(["state"] + ([css] if css else []))

    def poll_states(self):
        """États busy/idle des sessions des hôtes cochés, mis à jour sur place."""
        if self.polling_states:
            return True
        with_cursor, with_claude = self.show_cursor.get_active(), self.show_claude.get_active()
        self.state_ticks = getattr(self, "state_ticks", 0) + 1
        # API cloud : toutes les ~9 s seulement
        hosts = [h for h in self.shown_hosts() if h != CLOUD or not self.state_ticks % 3]
        if not hosts:
            return True
        self.polling_states = True

        def cloud_live():
            live = {}
            for c in cloud_list():
                live[c["id"]] = c["live"]
                live[c["id"] + ":updated"] = c["updated"]
            return live

        def fetch():
            jobs = {}
            if with_claude:
                jobs = {h: cloud_live if h == CLOUD else (lambda h=h: run_helper(h, "live"))
                        for h in hosts}
            if with_cursor and any(h != CLOUD for h in hosts):
                jobs["cursor"] = cursor_live
            return {k: res for k, (res, err) in run_parallel(jobs).items() if not err}

        def done(polled):
            self.polling_states = False
            shown = self.shown_hosts()
            live = {}
            for res in polled.values():
                live.update(res)

            def fresh(s):
                """Vrai si le relevé a réussi pour la source et l'hôte de s : une panne
                passagère ne doit pas faire croire qu'une réponse est finie."""
                if s.get("source") == "cursor" and not s.get("cursor_cloud"):
                    return "cursor" in polled
                return s.get("host") in polled

            for s in list(self.sessions):  # touch() retrie la liste
                if not fresh(s):
                    continue
                was, s["live"] = s.get("live"), live.get(s["id"])
                updated = iso_ts(live.get(s["id"] + ":updated"))
                if updated:  # session cloud : date de sa dernière activité, prise à l'envoi ou à l'arrêt
                    if s["live"] != "busy" or was != "busy":
                        self.touch(s["id"], updated)
                elif "busy" in (was, s["live"]) and was != s["live"] and s["id"] not in self.streams:
                    self.touch(s["id"])  # message envoyé ou réponse finie dans un terminal ou Cursor
                if s.get("was_busy") and s["live"] != "busy":  # fini dans un terminal ou Cursor
                    self.reply_done(s, waiting=s["live"] == "requires_action")
                # les claude -p lancés d'ici sont notifiés par on_finished (relevé fait avant leur fin compris)
                s["was_busy"] = (s["live"] == "busy" and s["id"] not in self.streams
                                 and time.monotonic() - self.hub_done.get(s["id"], -60) > 30)
                if s["id"] in self.state_labels:
                    # un claude -p lancé d'ici tourne aussi en arrière-plan
                    self.set_state(self.state_labels[s["id"]], self.list_state(s["id"], s["live"]))
            cur = self.current
            if cur and not cur.get("new") and cur["host"] in shown and fresh(cur):
                cur["live"] = live.get(cur["id"])
                updated = live.get(cur["id"] + ":updated")
                if cur.get("source") == "claude-cloud" and updated and updated != cur.get("updated"):
                    cur["updated"] = updated
                    self.load_history()  # la session cloud a avancé
                self.update_status()

        def fail(_e):
            self.polling_states = False
        in_thread(fetch, done, fail)
        return True

    # -- fin des réponses : notification et « nouvelle réponse » dans la liste --
    def list_state(self, sid, live):
        """État affiché dans la liste : en cours d'abord, puis réponse non lue."""
        if self.pending.get(sid):
            return "requires_action"
        if sid in self.streams or live == "busy":
            return "busy"
        if self.has_draft(sid):
            return "draft"
        return "unread" if sid in self.settings["unread"] and live != "requires_action" else live

    def touch(self, sid, when=None):
        """Un message vient de partir ou d'arriver : la conversation remonte dans la liste."""
        when = when or time.time()
        row = self.rows.get(sid)
        sessions = [s for s in self.sessions if s["id"] == sid]
        if row and row.session not in sessions:
            sessions.append(row.session)  # conversation d'un autre hôte (vue par tag)
        if not any(s.get("mtime", 0) < when for s in sessions):
            return
        for s in sessions:
            s["mtime"] = max(s.get("mtime", 0), when)
        self.sessions.sort(key=lambda s: -s["mtime"])
        if row:
            row.meta.set_text(row_meta(row.session, row.cwd_text, row.missing))
            row.changed()  # le tri de la liste la replace

    def reply_done(self, s, text=None, error=False, waiting=False):
        """Claude a fini de répondre dans s : notifie, sauf si on la regarde déjà."""
        sid = s["id"]
        if self.is_active() and self.current and self.current["id"] == sid:
            return
        self.settings["unread"].add(sid)
        save_settings(self.settings)
        self.show_unread(sid)
        title = s.get("title") or s.get("first") or "Conversation sans titre"
        if error:
            head = "Claude a échoué"
        elif waiting:
            head = "Claude attend une action"
        else:
            head = "Claude a terminé"
        n = Gio.Notification.new(f"{head} · {title[:80]}")
        body = " ".join((text or "").split())
        n.set_body((body[:200] + "…" if len(body) > 200 else body) or f"{s.get('host')} · {s.get('cwd') or ''}")
        n.set_priority(Gio.NotificationPriority.HIGH if error or waiting else Gio.NotificationPriority.NORMAL)
        n.set_default_action_and_target("app.open-session", GLib.Variant("s", sid))
        self.notified[sid] = {k: s.get(k) for k in ("id", "host", "title", "first", "cwd", "source", "model")}
        self.get_application().send_notification(f"reply-{sid}", n)

    def open_notified(self, sid):
        """Clic sur une notification : ouvre la conversation."""
        s = self.notified.get(sid)
        self.present()
        if s and not (self.current and self.current["id"] == sid):
            row = self.rows.get(sid)
            self.open(dict(row.session, host=row.host) if row else dict(s))
            if row:
                self.listbox.select_row(row)

    def show_unread(self, sid):
        row = self.rows.get(sid)
        if row:
            (row.add_css_class if sid in self.settings["unread"] else row.remove_css_class)("unread")
        if sid in self.state_labels:
            live = next((s.get("live") for s in self.sessions if s["id"] == sid), None)
            self.set_state(self.state_labels[sid], self.list_state(sid, live))

    def mark_read(self, sid):
        app = self.get_application()
        if app:
            app.withdraw_notification(f"reply-{sid}")
        if sid in self.settings["unread"]:
            self.settings["unread"].discard(sid)
            save_settings(self.settings)
            self.show_unread(sid)

    def on_row_menu(self, gesture, _n, x, y):
        row = self.listbox.get_row_at_y(int(y))
        if not getattr(row, "session", None) or row.session["id"] in self.unsent:
            return
        sid, s = row.session["id"], dict(row.session, host=row.host)
        row_dir = self.dir_of(s)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        # liste simple, sans sections : avec leurs séparateurs, GTK 4.20 donne au
        # menu une hauteur trop petite et coupe le dernier élément
        menu = Gio.Menu()
        for label, action in (
                ("Marquer comme lue" if sid in self.settings["unread"] else "Marquer comme non lue",
                 "toggle-unread"),
                ("Tags…", "row-tags"),
                *([("Ne plus filtrer sur ce dossier" if row_dir in parse_query(self.search.get_text())[1]
                    else "Filtrer sur ce dossier", "row-dir")] if row_dir else []),
                *([("Renommer…", "row-rename")] if can_rename(s) else []),
                ("Désarchiver" if self.is_archived(row.session) else "Archiver", "row-archive")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"win.{action}", GLib.Variant.new_string(sid))
            menu.append_item(item)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(self.listbox)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.set_has_arrow(False)
        pop.set_position(Gtk.PositionType.BOTTOM)
        pop.set_halign(Gtk.Align.START)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def toggle_unread(self, sid):
        """Marque à la main une conversation comme lue ou non lue."""
        if sid in self.settings["unread"]:
            if self.kept_unread == sid:
                self.kept_unread = None
            return self.mark_read(sid)
        self.settings["unread"].add(sid)
        save_settings(self.settings)
        self.show_unread(sid)
        if self.current and self.current["id"] == sid:
            self.kept_unread = sid  # sinon revenir sur la fenêtre la remarquerait lue

    def shown_row(self, sid):
        """Ligne de sid si elle est encore dans la liste (une archivée en est retirée)."""
        row = self.rows.get(sid)
        return row if row and row.get_parent() is self.listbox else None

    def row_tags(self, sid):
        if row := self.shown_row(sid):  # popover accroché au bouton de tags de la ligne
            self.edit_tags(row.get_child().get_last_child().get_prev_sibling(), row.session, row.host)

    def row_rename(self, sid):
        if row := self.shown_row(sid):
            s = self.current if self.current and self.current["id"] == sid else row.session
            self.ask_rename(row, s)

    def dir_of(self, s):
        """Dossier de travail de s tel qu'on l'écrit dans un filtre « dir: » (~ pour le home)."""
        cwd = s.get("cwd")
        if not cwd or ":" in cwd:
            return None
        home = self.remote_home.get(s["host"], "")
        if home and (cwd == home or cwd.startswith(home + "/")):
            cwd = "~" + cwd[len(home):]
        return cwd

    def row_dir(self, sid):
        if (row := self.shown_row(sid)) and (d := self.dir_of(dict(row.session, host=row.host))):
            self.apply_token(dir_token(d), "dir:")

    def row_archive(self, sid):
        if row := self.shown_row(sid):
            self.toggle_archive(row)

    # -- affichage d'une conversation --
    def on_row(self, _lb, row):
        sid = row.session["id"]
        if self.current and self.current["id"] == sid:
            return  # déjà affichée : garder la vue et la saisie telles quelles
        if sid in self.unsent:
            return self.open(self.unsent[sid])
        q = parse_query(self.search.get_text())[2]
        found = row.session["id"] in self.content_hits
        self.open(dict(row.session, host=row.host, search=q if found else None))

    def open(self, s):
        self.cancel_fork()
        self.stash_input()
        self.current = s
        self.kept_unread = None
        self.restore_input(s["id"])
        self.show_mode(s)
        self.mark_read(s["id"])
        if s["host"] in self.new_hosts:  # « Nouvelle » part de l'hôte de la conversation affichée
            self.new_host_dd.set_selected(self.new_hosts.index(s["host"]))
        self.show_defaults()
        self.web_btn.set_visible(s.get("source") == "claude-cloud")
        self.new_here_btn.set_visible(self.here_dir(s) is not None)
        self.tag_btn.set_visible(True)
        self.rename_btn.set_visible(can_rename(s))
        tag_chips(self.head_tags, self.tag_store.tags(s["id"]), self.filter_tag)
        self.size = -1
        if self.plan_panel.sid != s["id"]:
            self.close_side("plan")  # le plan affiché est celui d'une autre conversation
        if self.agents_panel.get_visible() and not self.has_agents_files(s):
            self.close_side("agents")
        self.show_agents(s)
        self.title.set_markup("<b>" + GLib.markup_escape_text(s["title"] or s["first"] or "Nouvelle conversation") + "</b>")
        self.show_subtitle(s)
        self.clear()
        if s.get("new"):
            self.notice("Nouvelle conversation : écris le premier message.")
        else:
            self.load_history()
        self.set_thinking(None)  # le bandeau d'une autre conversation ne doit pas rester
        self.todo_panel.show(s.get("todos"))
        self.update_send_btn()
        self.show_queue()
        self.update_status()
        self.input.grab_focus()

    def show_subtitle(self, s):
        """hôte · modèle et effort de la dernière réponse · dossier · id."""
        model = f"  ·  {model_name(s['model'])}" if s.get("model") else ""
        if s.get("effort"):
            model += f" ({s['effort']})" if model else f"  ·  effort {s['effort']}"
        self.subtitle.set_text(f"{s['host']}{model}  ·  {s['cwd']}  ·  {s['id']}")
        self.subtitle.set_tooltip_text(s.get("model"))

    def ask_rename(self, button, s=None):
        s = s or self.current
        pop = RenamePopover(s.get("title"), lambda name: self.rename(s, name))
        pop.set_parent(button)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def rename(self, s, name):
        """Renomme par /rename (claude écrit le titre) puis met la liste à jour."""
        if s is self.current:
            self.status.set_text("Claude choisit un nom…" if not name.strip() else "Renommage…")

        def done(title):
            self.set_title(s, title)
            if self.current and self.current["id"] == s["id"]:
                self.update_status()

        def fail(e):
            if self.current and self.current["id"] == s["id"]:
                self.update_status()
                self.error(f"Renommage impossible : {e}")
        in_thread(lambda: rename_session(s, name), done, fail)

    def set_title(self, s, title):
        """Nouveau titre de s : en-tête et liste."""
        s["title"] = title
        for other in self.sessions:
            if other["id"] == s["id"]:
                other["title"] = title
        if self.current and self.current["id"] == s["id"]:
            self.current["title"] = title
            self.title.set_markup("<b>" + esc(title) + "</b>")
        self.refresh_list([s["host"]])

    def update_send_btn(self):
        busy = bool(self.proc)
        self.send_btn.set_tooltip_text("Transmis à Claude, qui le lira après l'étape en cours"
                                       if busy else None)
        self.stop_btn.set_visible(busy)
        # /rename passe par un autre claude -p : pas pendant que celui du hub écrit
        self.rename_btn.set_sensitive(not busy)
        self.rename_btn.set_tooltip_text("Renommage possible une fois la réponse finie" if busy
                                         else "Renommer la conversation")

    def show_queue(self):
        """Affiche les messages en file de la conversation affichée."""
        while (child := self.queue_box.get_first_child()):
            self.queue_box.remove(child)
        queue = self.queues.get(self.current["id"], []) if self.current else []
        for i, (text, images) in enumerate(queue):
            row = Gtk.Box(spacing=6, css_classes=["queued"])
            row.append(Gtk.Image(icon_name="mail-send-symbolic", tooltip_text="En file"))
            summary = " ".join(text.split()) or "(image seule)"
            if images:
                summary += f"  ·  {len(images)} image{'s' if len(images) > 1 else ''}"
            row.append(Gtk.Label(label=summary, xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.END, tooltip_text=text or None))
            edit = Gtk.Button(icon_name="document-edit-symbolic", css_classes=["flat"],
                              tooltip_text="Remettre dans la saisie pour le modifier")
            edit.connect("clicked", lambda _b, i=i: self.unqueue(i, edit=True))
            row.append(edit)
            drop = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                              tooltip_text="Retirer de la file")
            drop.connect("clicked", lambda _b, i=i: self.unqueue(i))
            row.append(drop)
            self.queue_box.append(row)
        self.queue_box.set_visible(bool(queue))

    def unqueue(self, i, edit=False):
        queue = self.queues.get(self.current["id"], [])
        if i >= len(queue):
            return
        text, images = queue.pop(i)
        if not queue:
            del self.queues[self.current["id"]]
        if edit:
            buf = self.input.get_buffer()
            buf.insert(buf.get_start_iter(), text + ("\n" if buf.get_char_count() else ""))
            for img in images:
                self.attachments.append((img["type"], base64.b64decode(img["data"])))
            self.show_attachments()
            self.input.grab_focus()
        self.show_queue()

    def clear(self):
        while (child := self.msgs.get_first_child()):
            self.msgs.remove(child)
        self.agent_blocks = {}
        self.tool_calls = {}

    def load_history(self):
        s = self.current

        def done(res):
            if s is not self.current or self.is_streaming(s):
                return
            self.size = res["size"]
            if res.get("model"):
                s["model"], s["effort"] = res["model"], res.get("effort")
                self.show_subtitle(s)
            self.clear()
            if "agents" in res:
                s["agents"] = res["agents"]
                self.show_agents(s)
            if res["total"] > len(res["items"]):
                self.notice(f"… {res['total'] - len(res['items'])} éléments plus anciens masqués")
            target = None
            words = norm_text(s.get("search") or "").split()
            for item in res["items"]:
                w = self.item_widget(item, s)
                self.msgs.append(w)
                inp = item.get("input") if isinstance(item.get("input"), dict) else {}
                text = item.get("text") or " ".join(v for v in inp.values() if isinstance(v, str))
                if words and target is None and any(x in norm_text(text) for x in words):
                    target = w
            s["items"] = res["items"]
            # Le script distant rejoue toute la transcription ; Cursor et le cloud, l'historique lu.
            s["todos"] = (TodoList.from_events(res["todos"]) if "todos" in res
                          else TodoList.from_items(res["items"]))
            self.todo_panel.show(s["todos"])
            self.show_pending(s["id"])
            self.sync_plan_cards()
            if target is not None:
                s["search"] = None  # seulement au premier chargement
                target.add_css_class("hit")
                self.scroll_to(target)
            else:
                self.scroll_end()

        def fail(e):
            self.error(f"Lecture impossible : {e}")
        limit = SEARCH_HISTORY_LIMIT if s.get("search") else HISTORY_LIMIT
        if s.get("source") == "claude-cloud":
            fetch = lambda: cloud_history(s["id"], limit)  # noqa: E731
        elif s.get("cursor_cloud"):
            fetch = lambda: cursor_cloud_history(s.get("body"), limit)  # noqa: E731
        elif s.get("source") == "cursor":
            fetch = lambda: cursor_history(s["id"], limit)  # noqa: E731
        else:
            fetch = lambda: run_helper(s["host"], "history", s["id"], limit)  # noqa: E731
        in_thread(fetch, done, fail)

    def item_widget(self, item, s, live=False):
        """Widget d'un élément de l'historique (live : reçu dans le flux de claude -p).

        Un sous-agent se charge quand on le déplie, la sortie d'un outil aussi (sauf en
        direct, où elle arrive dans le flux).
        """
        agents = self.has_agents_files(s)  # bouton vers le volet des sous-agents
        if item.get("via") == "agent" and agents and item.get("from"):
            return with_open_button(message_widget(item), lambda: self.open_agent(item["from"]))
        if item.get("role") != "tool" or item.get("name") == "ExitPlanMode":
            return message_widget(item, self.fork_handler(s))
        tid = item.get("id")
        claude = bool(tid) and s.get("source") is None and s["host"] != CLOUD
        # Cursor : sorties et sous-agents lus dans sa base locale, même pour un hôte distant.
        cursor = bool(tid) and s.get("source") == "cursor" and not s.get("cursor_cloud")
        make = lambda it: self.item_widget(it, s, live)  # noqa: E731
        if item.get("name") in AGENT_TOOLS:
            loader = None
            if claude and not live:
                loader = lambda: run_helper(s["host"], "agent", s["id"], tid, HISTORY_LIMIT)  # noqa: E731
            elif cursor and item.get("agent_id"):
                loader = lambda: cursor_agent(item["agent_id"], HISTORY_LIMIT)  # noqa: E731
            elif item.get("agent"):  # cloud : reconstitué avec l'historique (cloud_agents)
                loader = lambda: item["agent"]  # noqa: E731
            block = SubagentBlock(item, loader, make,
                                  (lambda: self.open_agent(tid)) if agents and tid else None)
            if (item.get("agent") or {}).get("end"):
                block.finish(item["agent"]["end"])
            if tid:
                self.agent_blocks[tid] = block
            return block
        if not tid or not (claude or cursor or live):  # cloud : sortie inconnue
            return message_widget(item)
        loader = ((lambda: run_helper(s["host"], "result", s["id"], tid)) if claude  # noqa: E731
                  else (lambda: cursor_result(tid)) if cursor else None)
        call = self.tool_calls[tid] = ToolCall(item, loader)
        to = (item.get("input") or {}).get("to") if isinstance(item.get("input"), dict) else None
        if item.get("name") == "SendMessage" and agents and isinstance(to, str) and AGENT_ID.match(to):
            return with_open_button(call, lambda: self.open_agent(to))  # relance d'un sous-agent
        return call

    def poll(self):
        """Recharge la conversation si elle a été modifiée ailleurs (terminal…)."""
        s = self.current
        # Une conversation rouverte pendant que son claude -p tourne n'en reçoit plus la
        # sortie en direct : on la suit via son fichier, comme une session de terminal.
        if not s or s.get("new") or self.is_streaming(s) or s["host"] == CLOUD:
            return True  # le cloud est suivi par poll_states

        def done(res):
            if s is not self.current or self.is_streaming(s):
                return
            s["live"] = res["live"]
            self.update_status()
            if res["size"] != self.size and self.size != -1:
                self.load_history()
        if s.get("source") == "cursor":
            in_thread(lambda: cursor_stat(s["id"]), done)
        else:
            in_thread(lambda: run_helper(s["host"], "stat", s["id"]), done)
        return True

    def set_thinking(self, text):
        """Affiche (text) ou masque (None) le bandeau animé ; les points défilent."""
        if text:
            self.thinking_text = text.rstrip("…. ")
            if not self.thinking_timer:
                self.thinking_timer = GLib.timeout_add(400, self.tick_thinking)
            self.tick_thinking()
        elif self.thinking_timer:
            GLib.source_remove(self.thinking_timer)
            self.thinking_timer = 0
        self.thinking_spinner.set_spinning(bool(text))
        self.thinking.set_reveal_child(bool(text))

    def tick_thinking(self):
        self.thinking_dots = (self.thinking_dots + 1) % 4
        if self.thinking_label.get_text() != self.thinking_text:
            self.thinking_label.set_text(self.thinking_text)
        for i, dot in enumerate(self.thinking_dot_labels):
            dot.set_opacity(1 if i < self.thinking_dots else 0)
        self.thinking_bar.queue_draw()  # redessine tout le bandeau, pas seulement les points
        return True

    def update_activity(self):
        s = self.current
        if s and self.pending.get(s["id"]):
            status, text = "waiting", "⚠ Claude attend ta réponse"
        elif self.proc:
            status, text = "busy", "Claude travaille…"
            n = len(self.streams[s["id"]][1].get("tasks") or ())
            if n:
                text += f" · {n} sous-agent{'s' if n > 1 else ''}"
        elif s and s.get("live") == "busy":
            status = "busy"
            text = ("Cursor génère…" if s.get("source") == "cursor"
                    else "Claude travaille (dans un terminal)…")
        elif s and s.get("live") == "requires_action":
            status, text = "waiting", "⚠ Action requise"
        elif s and not s.get("new"):
            status, text = "idle", "✓ Terminé"
        else:
            status, text = None, ""
        self.activity.set_visible(bool(status))
        self.activity_label.set_text(text)
        self.activity.set_css_classes(["activity"] + ([status] if status else []))
        self.spinner.set_visible(status == "busy")
        self.spinner.set_spinning(status == "busy")
        if status != "busy":
            self.set_thinking(None)
        elif not self.proc:
            self.set_thinking(text)
        elif not self.thinking.get_reveal_child():
            self.set_thinking("Claude réfléchit")

    def update_status(self):
        s = self.current
        self.update_activity()
        if self.proc:
            return
        if s and s.get("cwd_exists") is False:
            self.status.set_text(f"⚠ Le dossier {s['cwd']} n'existe plus : Claude reprendra "
                                 "la conversation depuis le dossier personnel.")
        elif s and s.get("cursor_cloud"):
            self.status.set_text("Agent cloud Cursor (lecture seule) : réponds-y depuis Cursor.")
        elif s and s.get("source") == "claude-cloud":
            self.status.set_text("Session claude.ai : ton message est transmis à son Claude via "
                                 "Remote Control, s'il tourne encore.")
        elif s and s.get("source") == "cursor":
            self.status.set_text("Conversation Cursor (lecture seule) : ton message démarre une "
                                 "session Claude sur le même hôte, avec cette conversation en contexte.")
        elif s and s.get("live") and not s.get("new"):
            self.status.set_text("Ouverte dans un terminal : ton message lui sera transmis via "
                                 "Remote Control (sinon, Claude Hub te proposera une copie).")
        else:
            self.status.set_text("Entrée : envoyer · Maj+Entrée : nouvelle ligne")

    def notice(self, text, css="notice"):
        self.msgs.append(make_label(GLib.markup_escape_text(text), css))
        self.scroll_end()

    def error(self, text):
        self.notice(text, "error")

    def scroll_to(self, widget, tries=20):
        self.follow_end = False

        def go():
            ok, pt = widget.compute_point(self.msgs, Graphene.Point())
            adj = self.scroll.get_vadjustment()
            if not ok or widget.get_height() == 0 or adj.get_upper() <= pt.y:
                if tries > 0:  # pas encore placé
                    self.scroll_to(widget, tries - 1)
                return False
            adj.set_value(max(0, pt.y - 40))
            return False
        GLib.timeout_add(100, go)

    def scroll_end(self):
        self.follow_end = True
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def on_scroll_resized(self, adj):
        # "changed" arrive pendant la mise en page : y déplacer la vue décale la barre
        # sans déplacer le contenu (le dernier message restait caché). On attend la fin.
        if self.follow_end and not self.follow_pending:
            self.follow_pending = True
            GLib.idle_add(self.stick_end)

    def stick_end(self):
        self.follow_pending = False
        if self.follow_end:
            adj = self.scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def on_scroll_moved(self, adj):
        self.follow_end = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 2

    # -- images jointes --
    def add_image(self, raw):
        try:
            mime, data = prepare_image(raw)
        except GLib.Error as e:
            return self.status.set_text(f"Image illisible : {e.message}")
        self.attachments.append((mime, data))
        self.show_attachments()

    def show_attachments(self):
        while (child := self.attach_box.get_first_child()):
            self.attach_box.remove(child)
        for i, (_mime, data) in enumerate(self.attachments):
            pic = image_widget(texture_from(data), (120, 72))
            close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["circular", "osd"],
                               halign=Gtk.Align.END, valign=Gtk.Align.START,
                               tooltip_text="Retirer")
            close.connect("clicked", lambda _b, i=i: (self.attachments.pop(i), self.show_attachments()))
            ov = Gtk.Overlay(child=pic, halign=Gtk.Align.START, valign=Gtk.Align.START,
                             hexpand=False)
            ov.add_overlay(close)
            self.attach_box.append(ov)
        self.attach_box.set_visible(bool(self.attachments))
        self.draft_changed()

    def add_files(self, files):
        """Ajoute les fichiers images ; renvoie les chemins non-images."""
        others = []
        for f in files:
            name = (f.get_basename() or "").lower()
            if name.endswith(IMAGE_EXTS):
                try:
                    self.add_image(f.load_contents(None)[1])
                except GLib.Error as e:
                    self.status.set_text(f"{name} : {e.message}")
            else:
                others.append(f.get_path() or f.get_uri())
        return others

    def on_paste(self, view):
        clip = view.get_clipboard()
        formats = clip.get_formats()
        mimes = formats.get_mime_types() or []
        gtypes = formats.get_gtypes() or []
        # Autre appli : types MIME ; même appli : sous-type exact (GdkMemoryTexture…).
        has = lambda base, mime: (any(g.is_a(base) for g in gtypes)  # noqa: E731
                                  or any(m_.startswith(mime) for m_ in mimes))
        if has(Gdk.Texture, "image/"):
            view.stop_emission_by_name("paste-clipboard")
            clip.read_texture_async(None, lambda c, res: self.add_image(
                c.read_texture_finish(res).save_to_png_bytes().get_data()))
        elif has(Gdk.FileList, "text/uri-list"):
            view.stop_emission_by_name("paste-clipboard")

            def got(c, res):
                others = self.add_files(c.read_value_finish(res).get_files())
                if others:  # fichiers non-images : on colle leurs chemins
                    view.get_buffer().insert_at_cursor(" ".join(others))
            clip.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got)

    def on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gdk.Texture):
            self.add_image(value.save_to_png_bytes().get_data())
        else:
            others = self.add_files(value.get_files())
            if others:
                self.input.get_buffer().insert_at_cursor(" ".join(others))
        return True

    def pick_images(self, _btn):
        dialog = Gtk.FileDialog(title="Joindre des images")
        flt = Gtk.FileFilter(name="Images")
        flt.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dialog.set_filters(filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return  # annulé
            self.add_files([files.get_item(i) for i in range(files.get_n_items())])
        dialog.open_multiple(self, None, done)

    def pick_dir(self, _btn):
        host = self.new_host
        start = self.dir_entry.get_text().strip() or "~"
        if host != "local":
            return RemoteDirDialog(self, host, start,
                                   lambda path, home: self.set_dir(host, path, home)).present()
        dialog = Gtk.FileDialog(title="Dossier de la conversation")
        start = os.path.expanduser(start)
        if os.path.isdir(start):
            dialog.set_initial_folder(Gio.File.new_for_path(start))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return  # annulé
            if folder and folder.get_path():
                self.set_dir(host, folder.get_path(), str(Path.home()))
        dialog.select_folder(self, None, done)

    def set_dir(self, host, path, home):
        """Remplit le champ dossier (abrégé en ~) si l'hôte n'a pas changé entre-temps."""
        if host != self.new_host:
            return
        self.remote_home.setdefault(host, home)
        if path == home or path.startswith(home.rstrip("/") + "/"):
            path = "~" + path[len(home.rstrip("/")):]
        self.dir_entry.set_text(path)

    # -- envoi --
    def new_session(self, _btn):
        host = self.new_host
        cwd = self.dir_entry.get_text().strip() or "~"
        home = self.remote_home.get(host) or str(Path.home())
        if cwd.startswith("~"):
            cwd = home + cwd[1:]
        self.start_new(host, cwd)

    def here_dir(self, s):
        """Dossier où relancer une conversation à côté de s, ou None (cloud, dossier
        supprimé, dossier distant d'une conversation Cursor)."""
        cwd = s.get("cwd")
        if (not cwd or ":" in cwd or s["host"] == CLOUD or s.get("source") == "claude-cloud"
                or s.get("cwd_exists") is False):
            return None
        return cwd

    def new_here(self):
        """Nouvelle conversation dans le dossier (et sur l'hôte) de celle affichée ;
        sans conversation utilisable, comme le bouton « Nouvelle »."""
        cwd = self.here_dir(self.current) if self.current else None
        if cwd is None:
            return self.new_session(None)
        self.start_new(self.current["host"], cwd)

    def start_new(self, host, cwd):
        """Ouvre une nouvelle conversation, visible tout de suite en tête de la liste
        (elle disparaît si on la quitte sans rien y écrire)."""
        s = {"id": str(uuid.uuid4()), "cwd": cwd, "host": host, "new": True,
             "title": None, "first": None, "mtime": time.time()}
        self.unsent[s["id"]] = s
        self.open(s)  # avant fill_list : une nouvelle conversation vide quittée disparaît
        if host in self.shown_hosts():
            self.sessions.insert(0, s)
            self.fill_list()
            self.listbox.select_row(self.rows.get(s["id"]))

    # -- brouillons : saisie non envoyée, gardée par conversation (en mémoire seulement) --
    def input_text(self):
        buf = self.input.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def has_draft(self, sid):
        if self.current and self.current["id"] == sid:
            return bool(self.input_text().strip() or self.attachments)
        return sid in self.drafts

    def stash_input(self):
        """Garde la saisie de la conversation qu'on quitte ; une nouvelle conversation
        laissée vide disparaît de la liste."""
        s = self.current
        if not s:
            return
        text = self.input_text()
        if text.strip() or self.attachments:
            self.drafts[s["id"]] = (text, self.attachments)
            return
        self.drafts.pop(s["id"], None)
        if self.unsent.pop(s["id"], None):
            self.sessions = [x for x in self.sessions if x["id"] != s["id"]]
            row = self.rows.pop(s["id"], None)
            self.state_labels.pop(s["id"], None)
            if row:
                self.listbox.remove(row)

    def restore_input(self, sid):
        text, self.attachments = self.drafts.pop(sid, ("", []))
        self.input.get_buffer().set_text(text)
        self.show_attachments()

    def draft_changed(self):
        """La saisie a changé : badge « brouillon » de la conversation affichée."""
        if self.current and self.current["id"] in self.state_labels:
            self.refresh_state(self.current["id"])

    def pending_new(self, host, listed):
        """Nouvelles conversations de l'hôte pas encore dans la liste du helper :
        jamais envoyées, ou envoyées mais sans fichier pour l'instant."""
        known = {s["id"] for s in listed}
        new = list(self.unsent.values()) + [s for _p, s in self.streams.values() if s.get("new")]
        seen, out = set(), []
        for s in new:
            if s["host"] == host and s["id"] not in known and s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
        return out

    def command_key(self):
        """(hôte, dossier) où claude tournera pour la conversation affichée, ou None
        si on ne peut pas y taper de commande (cloud, Cursor en lecture seule)."""
        s = self.current
        if not s or s.get("cursor_cloud") or s["host"] == CLOUD or s.get("source") == "claude-cloud":
            return None
        cwd = s.get("cwd") if ":" not in (s.get("cwd") or "") else None
        return s["host"], cwd

    def update_commands(self):
        """Montre, filtre ou cache les suggestions selon le début de la saisie."""
        buf = self.input.get_buffer()
        query = typed_command(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
        key = self.command_key()
        if query is None or key is None:
            self.cmd_dismissed = False
            return self.cmd_popover.popdown()
        if self.cmd_dismissed:
            return
        known = self.commands.get(key)
        if known is None:
            self.commands[key] = "loading"

            def done(res, key=key):
                self.commands[key] = res
                self.update_commands()

            def failed(e, key=key):
                self.commands[key] = e
                self.update_commands()
                self.commands.pop(key, None)  # on réessaiera au prochain « / »
            in_thread(lambda: fetch_commands(*key), done, failed)
            known = "loading"
        if known == "loading":
            return self.cmd_popover.show_message("Chargement des commandes…")
        if isinstance(known, Exception):
            return self.cmd_popover.show_message(f"Commandes indisponibles : {known}")
        matches = match_commands(known, query)
        if not matches:
            return self.cmd_popover.popdown()
        self.cmd_popover.show_matches(matches)

    def pick_command(self, c):
        """Remplace la saisie par « /commande » (suivie d'une espace pour les arguments)."""
        self.cmd_popover.popdown()
        self.input.get_buffer().set_text(f"/{c['name']} ")
        self.input.grab_focus()

    def on_command_key(self, keyval, state):
        """Touches des suggestions de commandes ; True si la touche est prise."""
        pop = self.cmd_popover
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            pop.move(-1 if keyval == Gdk.KEY_Up else 1)
            return True
        if keyval == Gdk.KEY_Escape:
            self.cmd_dismissed = True
            pop.popdown()
            return True
        c = pop.selected()
        if c is None:
            return False
        if keyval == Gdk.KEY_Tab:
            self.pick_command(c)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            buf = self.input.get_buffer()
            if buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) != "/" + c["name"]:
                self.pick_command(c)
                return True
            pop.popdown()  # commande déjà complète : Entrée l'envoie
        return False

    def on_key(self, _ctl, keyval, _code, state):
        if self.cmd_popover.get_visible() and self.on_command_key(keyval, state):
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self.send("now" if state & Gdk.ModifierType.CONTROL_MASK
                      else "queue" if state & Gdk.ModifierType.ALT_MASK else "auto")
            return True
        if keyval == Gdk.KEY_ISO_Left_Tab:
            self.toggle_plan()
            return True
        if keyval == Gdk.KEY_Escape and self.proc:  # comme Échap dans claude
            self.stop()
            return True
        return False

    def toggle_plan(self):
        """Bascule entre les modes « auto » et « plan », comme Maj+Tab dans claude."""
        plan = PERMISSION_MODES[self.mode_dd.get_selected()] == "plan"
        self.mode_dd.set_selected(PERMISSION_MODES.index("auto" if plan else "plan"))

    def send(self, mode="auto"):
        """Envoie la saisie. Pendant une réponse, selon mode : « auto » la transmet à
        claude, qui la lit après l'étape en cours ; « now » interrompt la réponse et
        l'envoie aussitôt ; « queue » la garde en file jusqu'à la fin de la réponse."""
        s = self.current
        buf = self.input.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not s:
            return
        if self.fork_at and self.fork_at["sid"] == s["id"]:
            if (text or self.attachments) and not self.proc:
                self.launch_fork(s, text)
            return
        queue = self.queues.get(s["id"])
        if self.proc or queue:
            # Claude répond déjà (ou la file a été interrompue).
            if text or self.attachments:
                msg = (text, self.take_input())
                if self.proc and mode == "now":
                    # en tête de file, envoyé dès que claude s'est arrêté
                    self.queues.setdefault(s["id"], []).insert(0, msg)
                    self.streams[s["id"]][1]["then_send"] = True
                    self.stop()
                elif not (self.proc and mode == "auto" and self.inject(s, *msg)):
                    self.queues.setdefault(s["id"], []).append(msg)
            if not self.proc:
                self.send_queued(s)
            self.show_queue()
            return
        if not (text or self.attachments):
            return
        if s.get("cursor_cloud"):
            return self.status.set_text("Agent cloud Cursor en lecture seule : réponds-y depuis Cursor.")
        # Conversation déjà ouverte ailleurs (terminal) ou session claude.ai : on ne lance
        # pas un second Claude, on passe par Remote Control.
        if s.get("source") == "claude-cloud" or (s.get("source") is None and not s.get("new")
                                                 and s.get("live")):
            return self.send_via_remote(s, text)
        self.launch(s, text, self.take_input())

    def fork_handler(self, s):
        """Action « copie à partir d'ici » des messages de s : conversations Claude Code
        seulement (le cloud et Cursor n'ont pas de reprise à un point donné)."""
        if s.get("source") is None and s["host"] != CLOUD and not s.get("new"):
            return self.start_fork
        return None

    def start_fork(self, item, before, widget):
        """Prépare une copie de la conversation arrêtée au message de widget (ou juste
        avant, pour un message de l'utilisateur remis dans la saisie) : le prochain envoi
        la lance, l'original reste intact."""
        s = self.current
        if self.proc:
            return self.status.set_text("Copie possible une fois la réponse finie.")
        self.cancel_fork()
        while widget.get_parent() is not None and widget.get_parent() is not self.msgs:
            widget = widget.get_parent()
        if widget.get_parent() is None:
            return
        self.fork_at = {"sid": s["id"], "at": item["parent"] if before else item["uuid"],
                        "widget": widget, "before": before,
                        "text": item.get("text") if before else None}
        w = widget if before else widget.get_next_sibling()
        while w is not None:  # ce qui ne fera pas partie de la copie
            w.add_css_class("after-fork")
            w = w.get_next_sibling()
        if before and item.get("text"):
            self.input.get_buffer().set_text(item["text"])
        self.fork_label.set_text("Copie de la conversation " + (
            "juste avant ce message : modifie-le puis envoie." if before else
            "arrêtée à ce message : ton prochain message partira dans une nouvelle conversation."))
        self.fork_bar.set_visible(True)
        self.input.grab_focus()

    def cancel_fork(self):
        if self.fork_at:
            w = self.fork_at["widget"]
            while w is not None:
                w.remove_css_class("after-fork")
                w = w.get_next_sibling()
            buf = self.input.get_buffer()
            if self.fork_at["text"] and buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                                                     False) == self.fork_at["text"]:
                buf.set_text("")  # message remis dans la saisie, pas retouché
            self.fork_at = None
        self.fork_bar.set_visible(False)

    def launch_fork(self, s, text):
        """Lance la copie préparée par start_fork avec le premier message text."""
        f = self.fork_at
        w = f["widget"] if f["before"] else f["widget"].get_next_sibling()
        while w is not None:  # la vue montre la copie : sans la suite de l'original
            nxt = w.get_next_sibling()
            self.msgs.remove(w)
            w = nxt
        self.fork_at = None
        self.fork_bar.set_visible(False)
        copy = dict(s, live=None, forked=True, title=f"Copie de « {s.get('title') or s.get('first') or 'conversation'} »")
        self.current = copy
        self.title.set_markup("<b>" + esc(copy["title"]) + "</b>")
        self.launch(copy, text, self.take_input(), fork=True, at=f["at"])

    def send_queued(self, s):
        """Lance le premier message en file de s."""
        queue = self.queues.get(s["id"])
        if not queue:
            return
        text, images = queue.pop(0)
        if not queue:
            del self.queues[s["id"]]
        self.launch(s, text, images)
        if s is self.current:
            self.show_queue()

    def inject(self, s, text, images):
        """Transmet un message au claude -p qui répond : il le lira après l'étape en
        cours (comme un message tapé pendant une réponse dans le terminal). Faux si
        claude n'écoute plus son entrée (réponse finie ou interrompue)."""
        proc, st = self.streams[s["id"]]
        if proc.stdin.closed or st.get("interrupted") or not self.write_input(
                proc, self.user_message(text, images)):
            return False
        self.msgs.append(message_widget({"role": "user", "text": text, "images": images,
                                         "queued": True}))
        self.scroll_end()
        self.status.set_text("Message transmis : Claude le lira après l'étape en cours.")
        return True

    @staticmethod
    def user_message(text, images):
        """Ligne stream-json d'un message de l'utilisateur pour l'entrée de claude."""
        content = [{"type": "image", "source": {"type": "base64", "media_type": img["type"],
                                                 "data": img["data"]}} for img in images]
        if text:
            content.append({"type": "text", "text": text})
        return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"

    def take_input(self):
        """Vide la saisie et les pièces jointes ; renvoie les images encodées."""
        self.input.get_buffer().set_text("")
        images = [{"type": mime, "data": base64.b64encode(data).decode()}
                  for mime, data in self.attachments]
        self.attachments = []
        self.show_attachments()
        return images

    def show_user(self, text, images):
        self.msgs.append(message_widget({"role": "user", "text": text, "images": images}))
        self.scroll_end()

    def send_via_remote(self, s, text):
        self.send_btn.set_sensitive(False)
        self.status.set_text("Vérification de Remote Control…")

        def check():
            sid = s["id"] if s.get("source") == "claude-cloud" else None
            if sid is None:  # conversation locale/SSH : son identifiant Remote Control actuel
                sid = run_helper(s["host"], "stat", s["id"]).get("bridge")
            return sid, bool(sid) and remote_connected(sid)

        def checked(out):
            self.send_btn.set_sensitive(True)
            sid, connected = out
            if connected:
                return self.deliver_remote(s, sid, text)
            if s.get("source") == "claude-cloud":
                return self.status.set_text("Session déconnectée : son Claude ne tourne plus. "
                                            "Reprends-la depuis son terminal.")
            self.ask_fork(s, text)

        def failed(e):
            self.send_btn.set_sensitive(True)
            self.status.set_text(f"Remote Control indisponible ({e}).")
            self.ask_fork(s, text)
        in_thread(check, checked, failed)

    def deliver_remote(self, s, sid, text):
        images = self.take_input()
        self.show_user(text, images)
        self.touch(s["id"])
        s["live"] = "busy"  # le Claude du terminal va répondre : la vue suivra son fichier
        self.update_activity()
        self.status.set_text("Envoi à la session du terminal (Remote Control)…")

        def done(_):
            self.status.set_text("Message transmis au Claude du terminal : sa réponse s'affichera ici.")

        def fail(e):
            s["live"] = None
            self.update_activity()
            self.error(f"Envoi via Remote Control impossible : {e}")
        in_thread(lambda: remote_send(sid, text, images), done, fail)

    def ask_fork(self, s, text):
        dialog = Gtk.AlertDialog(
            message="Cette conversation est ouverte dans un terminal",
            detail="Elle n'est pas reliée à Remote Control : envoyer ici lancerait un second Claude "
                   "sur la même conversation, sans mémoire commune avec celui du terminal.\n\n"
                   "Pour lui écrire directement, tape /remote-control dans le terminal. "
                   "Sinon, tu peux continuer dans une copie indépendante de la conversation.",
            buttons=["Annuler", "Continuer dans une copie"], cancel_button=0, default_button=1)

        def chosen(d, res):
            try:
                if d.choose_finish(res) != 1:
                    return
            except GLib.Error:
                return
            copy = dict(s, live=None, forked=True, title=f"Copie de « {s.get('title') or s.get('first') or 'conversation'} »")
            self.current = copy
            self.title.set_markup("<b>" + esc(copy["title"]) + "</b>")
            self.launch(copy, text, self.take_input(), fork=True)
        dialog.choose(self, None, chosen)

    def launch(self, s, text, images, fork=False, at=None):
        """Lance `claude -p` (nouvelle conversation, reprise ou copie) et suit sa sortie ;
        at : uuid du dernier élément gardé de la transcription (copie à partir d'un message)."""
        shown = s is self.current
        if shown:
            self.show_user(text, images)
        prompt = text
        # Sans titre (ou suite d'une conversation Cursor) : claude en génère un, comme
        # l'extension VS Code ; en mode -p, il ne le fait pas de lui-même.
        about = None
        if s.get("source") == "cursor":
            about = f"{s['title'] or s['first'] or ''}\n{text}"
        elif not s.get("title") and not fork:
            about = s.get("first") or text
        if s.get("source") == "cursor":
            prompt = cursor_context(s["title"] or s["first"] or "", s.get("items", [])) + text
            cwd = s["cwd"] if ":" not in (s["cwd"] or "") else None
            s = self.current = {"id": str(uuid.uuid4()), "host": s["host"], "new": True,
                                "cwd": cwd or self.remote_home.get(s["host"]) or str(Path.home()),
                                "title": f"Suite de « {s['title'] or 'Cursor'} »", "first": None,
                                "mtime": time.time()}
            self.title.set_markup("<b>" + esc(s["title"]) + "</b>")
            self.show_subtitle(s)
            self.notice("Nouvelle session Claude créée avec la conversation Cursor en contexte.")

        argv = ["claude", "-p", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose",
                "--permission-mode", PERMISSION_MODES[self.mode_dd.get_selected()],
                # demandes de permission, plan et questions sur stdin/stdout (control_request)
                "--permission-prompt-tool", "stdio"]
        # Alias demandé (choisi ici ou réglé sur l'hôte) : claude dira au démarrage quelle version il désigne.
        s["alias"] = self.settings["model"] or (self.host_defaults.get(s["host"]) or {}).get("model")
        if self.settings["model"]:
            argv += ["--model", self.settings["model"]]
        if self.settings["effort"]:
            argv += ["--effort", self.settings["effort"]]
        # Effort de cette réponse (choisi ici ou réglé sur l'hôte), affiché dans l'en-tête.
        s["effort"] = self.settings["effort"] or (self.host_defaults.get(s["host"]) or {}).get("effort")
        if s is self.current:
            self.show_subtitle(s)
        argv += ["--session-id", s["id"]] if s.get("new") else ["--resume", s["id"]]
        if fork:
            argv.append("--fork-session")  # nouvelle conversation, l'originale reste intacte
        if at:
            argv += ["--resume-session-at", at]  # option non documentée de claude (mode -p)
        if s["host"] == "local":
            cmd, cwd = argv, s["cwd"] if s["cwd"] and os.path.isdir(s["cwd"]) else None
        else:
            # cd côté distant puis claude ; le prompt passe par stdin.
            line = f"cd {shlex.quote(s['cwd'])} 2>/dev/null; exec {shlex.join(argv)}"
            cmd, cwd = ["ssh", *SSH_OPTS, s["host"], "bash -lc " + shlex.quote(line)], None
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as e:
            return self.error(str(e)) if shown else None
        self.streams[s["id"]] = (proc, s)
        s["mode"] = argv[argv.index("--permission-mode") + 1]
        self.keep_mode(s["id"], s["mode"])
        self.unsent.pop(s["id"], None)  # envoyée : n'est plus un brouillon
        s["tasks"], s["idle"] = set(), False
        s.pop("interrupted", None)
        self.touch(s["id"])
        self.refresh_state(s["id"])  # « brouillon » -> « travaille »
        # stdin reste ouvert pour répondre aux demandes ; fermé à la fin de la réponse.
        self.write_input(proc, self.user_message(prompt, images))
        s.pop("titling", None)
        if about and about.strip():
            # persist : claude écrit lui-même l'ai-title dans la transcription.
            s["titling"] = f"title-{uuid.uuid4()}"
            self.write_input(proc, json.dumps({"type": "control_request", "request_id": s["titling"], "request": {
                "subtype": "generate_session_title", "description": about.strip()[:2000], "persist": True}}) + "\n")
        if shown:
            self.update_send_btn()
            self.status.set_text("Claude réfléchit…")
            self.update_activity()
        threading.Thread(target=self.read_stream, args=(proc, s), daemon=True).start()

    def read_stream(self, proc, s):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            GLib.idle_add(self.on_event, ev, s)
        err = proc.stderr.read()
        GLib.idle_add(self.on_finished, proc, s, proc.wait(), err)

    def on_event(self, ev, s):
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            if ev.get("model"):
                s["model"] = ev["model"]
                if s is self.current:
                    self.show_subtitle(s)
                versions = self.settings["model_versions"]
                if s.get("alias") in MODELS[1:] and versions.get(s["alias"]) != ev["model"]:
                    versions[s["alias"]] = ev["model"]
                    save_settings(self.settings)
                    self.relabel_models()
            if ev.get("session_id") and ev["session_id"] != s["id"]:  # copie (--fork-session)
                self.streams[ev["session_id"]] = self.streams.pop(s["id"])
                if s["id"] in self.queues:
                    self.queues[ev["session_id"]] = self.queues.pop(s["id"])
                s["id"] = ev["session_id"]
                self.keep_mode(s["id"], s.get("mode"))
                if s is self.current:
                    self.show_subtitle(s)
            return
        if t == "system" and ev.get("subtype") == "status" and ev.get("permissionMode"):
            s["mode"] = ev["permissionMode"]
            self.keep_mode(s["id"], s["mode"])
            if s is self.current:
                self.show_mode(s)
            return
        if t == "control_request":
            return self.on_request(ev, s)
        if t == "control_response":  # réponse à generate_session_title ou set_permission_mode
            resp = ev.get("response") or {}
            if s.get("titling") and resp.get("request_id") == s["titling"]:
                # erreur (claude ancien, panne…) : la première phrase reste le titre
                title = (resp.get("response") or {}).get("title") if resp.get("subtype") == "success" else None
                del s["titling"]
                if title:
                    self.set_title(s, title)
                return self.end_if_done(s)
            if resp.get("subtype") == "error":
                s.pop("mode", None)  # claude garde l'ancien mode : un nouveau choix sera renvoyé
                if s is self.current:
                    self.error(f"Changement de mode refusé : {resp.get('error')}")
            return
        if t == "control_cancel_request":  # demande retirée par claude (interruption…)
            self.pending.get(s["id"], {}).pop(ev.get("request_id"), None)
            self.request_done(s["id"], ev.get("request_id"), "Demande annulée.", ok=False)
            return self.refresh_state(s["id"])
        if t == "system" and ev.get("subtype", "").startswith("task_"):
            return self.on_task(ev, s)
        if t == "result":  # pour la notification de fin
            s["result"], s["result_error"] = ev.get("result"), bool(ev.get("is_error"))
            s["idle"] = True
            self.end_if_done(s)
            if s.get("titling"):  # un claude qui ne répondrait jamais ne doit pas rester ouvert
                rid = s["titling"]
                GLib.timeout_add_seconds(30, lambda: s.get("titling") == rid and (
                    s.pop("titling"), self.end_if_done(s)) and False)
            self.touch(s["id"])
        elif t == "assistant":  # étape d'une réponse : ne change pas l'ordre de la liste
            s["idle"] = False
            if (s.get("new") or s.get("forked")) and not s.get("listed"):
                s["listed"] = True
                self.refresh_list([s["host"]])  # nouvelle conversation (ou copie) : visible dès sa première réponse
        if self.track_todos(ev, s) and s is self.current:
            self.todo_panel.show(s["todos"])
        if s is not self.current:
            return
        if t == "user":  # sorties d'outils (de la conversation ou d'un sous-agent)
            content = ev.get("message", {}).get("content")
            for b in content if isinstance(content, list) else []:
                call = self.tool_calls.get(b.get("tool_use_id")) if isinstance(b, dict) else None
                if call and b.get("type") == "tool_result":
                    call.set_content(b.get("content"), bool(b.get("is_error")))
        parent = ev.get("parent_tool_use_id")
        if parent:  # message d'un sous-agent : dans son bloc
            block = self.agent_blocks.get(parent)
            for b in ev.get("message", {}).get("content", []) if t == "assistant" and block else []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    block.add({"role": "assistant", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    block.add({"role": "tool", "name": b.get("name"), "input": b.get("input"),
                               "id": b.get("id")})
            return
        if t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    item = {"role": "assistant", "text": b["text"]}
                    if ev.get("uuid"):
                        item["uuid"] = ev["uuid"]
                    self.msgs.append(message_widget(item, self.fork_handler(s)))
                    self.set_thinking("Claude réfléchit")
                elif b.get("type") == "tool_use":
                    item = {"role": "tool", "name": b.get("name"), "input": b.get("input"),
                            "id": b.get("id")}
                    # Sous-agent suivi en direct, sans relecture du fichier ; le plan
                    # s'affiche dans sa carte.
                    if b.get("name") != "ExitPlanMode":
                        self.msgs.append(self.item_widget(item, s, live=True))
                    self.status.set_text(f"Claude utilise {b.get('name')}…")
                    self.set_thinking(f"Claude utilise {b.get('name')}")
            self.scroll_end()
        elif t == "result":
            cost = ev.get("total_cost_usd")
            cost = f" · {cost:.3f} $" if cost else ""
            if s.get("interrupted"):  # error_during_execution attendu : pas une erreur
                return self.status.set_text(f"Interrompu{cost}")
            if ev.get("is_error"):
                self.error(str(ev.get("result") or ev.get("subtype")))
            self.status.set_text(f"Terminé{cost}")

    @staticmethod
    def track_todos(ev, s):
        """Met à jour les tâches de s d'après un événement du flux ; True si elles ont changé."""
        if ev.get("type") not in ("assistant", "user") or ev.get("parent_tool_use_id"):
            return False  # les tâches d'un sous-agent ne sont pas celles de la conversation
        todos, changed = s.setdefault("todos", TodoList()), False
        for b in ev.get("message", {}).get("content", []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                changed |= todos.use(b.get("name"), b.get("input"), b.get("id"))
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in todos.creating:
                result = ev.get("tool_use_result") or ev.get("toolUseResult")
                changed |= todos.created(b["tool_use_id"], created_id(result, b.get("content")))
        return changed

    def on_finished(self, proc, s, code, err):
        if self.streams.get(s["id"], (None,))[0] is not proc:
            return
        del self.streams[s["id"]]
        self.pending.pop(s["id"], None)
        self.refresh_state(s["id"])
        self.hub_done[s["id"]] = time.monotonic()
        shown = self.current is not None and self.current["id"] == s["id"]
        if shown:
            self.update_send_btn()
            self.update_activity()
        # Arrêté depuis le hub : claude sort en code 1 après une interruption, -15 si tué.
        stopped = code == -15 or bool(s.get("interrupted"))
        if stopped and s is self.current:
            self.status.set_text("Interrompu.")
        if code != 0 and not stopped and s is self.current:
            self.error(f"claude a échoué (code {code}) : {err.strip()[-800:]}")
        if s.pop("new", None) or s.pop("forked", None):
            self.refresh_list([s["host"]])  # la nouvelle conversation (ou la copie) apparaît dans la liste
        if shown:
            self.current.pop("new", None)
            self.rename_btn.set_visible(can_rename(self.current))
        # « Envoyer tout de suite » : le message en tête de file part après l'interruption.
        then_send = s.pop("then_send", False)
        if self.queues.get(s["id"]) and (code == 0 or then_send):
            # message suivant de la file, dans la vue qui suit la conversation
            return self.send_queued(self.current if shown else s)
        if not stopped:  # pas de notification après « Arrêter »
            self.reply_done(s, error=code != 0 or s.get("result_error"),
                            text=s.get("result") if code == 0 else err.strip()[-300:])
        if self.queues.get(s["id"]):
            if shown:
                n = len(self.queues[s["id"]])
                self.status.set_text(f"File en pause ({n} message{'s' if n > 1 else ''}) : "
                                     "« Envoyer » pour reprendre.")
        if shown:
            self.load_history()  # resynchronise avec le fichier

    # -- demandes de Claude (permissions, plan, questions) et sous-agents --
    @staticmethod
    def write_input(proc, line):
        """Écrit sur l'entrée de claude ; faux si le processus ne l'écoute plus."""
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def end_input(self, s):
        """Fin de la réponse : fermer stdin laisse claude terminer."""
        proc = self.streams.get(s["id"], (None,))[0]
        if proc and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass

    def end_if_done(self, s):
        """Ferme l'entrée de claude quand plus rien n'est attendu : réponse finie,
        sous-agents terminés, titre reçu."""
        if s.get("idle") and not s.get("tasks") and not s.get("titling"):
            self.end_input(s)

    def on_request(self, ev, s):
        rid, req = ev.get("request_id"), ev.get("request") or {}
        proc = self.streams.get(s["id"], (None,))[0]
        if req.get("subtype") != "can_use_tool":
            if proc:  # rien d'autre n'est déclaré : on le dit plutôt que de laisser claude attendre
                self.write_input(proc, json.dumps({"type": "control_response", "response": {
                    "subtype": "error", "request_id": rid,
                    "error": f"{req.get('subtype')} non pris en charge par Claude Hub"}}) + "\n")
            return
        self.pending.setdefault(s["id"], {})[rid] = req
        self.touch(s["id"])  # Claude s'arrête sur une question : la conversation remonte
        self.refresh_state(s["id"])
        if self.current and self.current["id"] == s["id"]:
            self.show_card(s["id"], rid, req)
            self.update_activity()
        what = {"ExitPlanMode": "Plan à valider", "AskUserQuestion": "Question de Claude"}.get(
            req.get("tool_name"), f"Autorisation demandée : {req.get('display_name') or req.get('tool_name')}")
        self.reply_done(s, text=what, waiting=True)

    def mode_for(self, s):
        """Mode d'une conversation : celui du claude en cours, sinon le dernier utilisé
        avec elle, sinon le dernier choisi."""
        live = self.streams.get(s["id"], (None, {}))[1].get("mode")
        return live or self.settings["modes"].get(s["id"]) or self.settings["mode"]

    def show_mode(self, s):
        mode = self.mode_for(s)
        if mode in PERMISSION_MODES and PERMISSION_MODES[self.mode_dd.get_selected()] != mode:
            self.syncing_mode = True
            self.mode_dd.set_selected(PERMISSION_MODES.index(mode))
            self.syncing_mode = False

    def keep_mode(self, sid, mode):
        if remember_mode(self.settings, sid, mode):
            save_settings(self.settings)

    def push_mode(self):
        """Mode choisi dans la liste : retenu pour la conversation, et transmis au claude
        en cours, sinon il ne s'appliquerait qu'au message suivant."""
        if self.syncing_mode or not self.current:
            return
        proc, s = self.streams.get(self.current["id"], (None, None))
        mode = PERMISSION_MODES[self.mode_dd.get_selected()]
        self.keep_mode(self.current["id"], mode)
        if not proc or proc.stdin.closed or s.get("mode") == mode:
            return
        s["mode"] = mode
        self.write_input(proc, json.dumps({"type": "control_request", "request_id": f"mode-{uuid.uuid4()}",
                                           "request": {"subtype": "set_permission_mode", "mode": mode}}) + "\n")

    def show_pending(self, sid):
        """Cartes des demandes en attente (après un rechargement de la vue)."""
        for rid, req in self.pending.get(sid, {}).items():
            self.show_card(sid, rid, req)

    def show_card(self, sid, rid, req):
        card = RequestCard(rid, req, lambda c, *a: self.answer(c.sid, c.request_id, *a),
                           self.take_feedback)
        card.sid = sid
        self.msgs.append(card)
        if card.tool == "ExitPlanMode" and rid not in self.plans_opened:
            self.open_plan(card.input.get("plan") or "", card.input.get("planFilePath"), rid)
        else:
            self.sync_plan_cards()
        self.scroll_end()

    def take_feedback(self):
        """Texte de la saisie, envoyé avec un refus (la saisie est alors vidée)."""
        buf = self.input.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if text:
            buf.set_text("")
        return text

    def answer(self, sid, rid, response, summary, mode=None):
        """Réponse à une demande, donnée depuis sa carte ou depuis le volet du plan."""
        proc = self.streams.get(sid, (None,))[0]
        if rid not in self.pending.get(sid, {}) or not proc or not self.write_input(
                proc, control_response(rid, response)):
            return self.request_done(sid, rid, "Demande expirée : Claude ne l'attend plus.", ok=False)
        del self.pending[sid][rid]
        self.request_done(sid, rid, summary, ok=response["behavior"] == "allow")
        if mode in PERMISSION_MODES and self.current and self.current["id"] == sid:
            self.streams[sid][1]["mode"] = mode  # déjà transmis avec la réponse
            self.mode_dd.set_selected(PERMISSION_MODES.index(mode))  # messages suivants dans ce mode
        self.refresh_state(sid)
        if self.current and self.current["id"] == sid:
            self.update_activity()
            self.set_thinking("Claude reprend")

    def request_done(self, sid, rid, summary, ok):
        """Demande close : sa carte dans le fil et le volet du plan affichent la décision."""
        if self.current and self.current["id"] == sid:
            child = self.msgs.get_first_child()
            while child:
                if isinstance(child, RequestCard) and child.request_id == rid:
                    child.done(summary, ok)
                child = child.get_next_sibling()
        if self.plan_panel.sid == sid and self.plan_panel.request_id == rid:
            self.plan_panel.done(summary, ok)

    def refresh_state(self, sid):
        """État de la conversation dans la liste (demande en attente, en cours…)."""
        if sid in self.state_labels:
            live = next((x.get("live") for x in self.sessions if x["id"] == sid), None)
            self.set_state(self.state_labels[sid], self.list_state(sid, live))
        if self.current and self.current["id"] == sid:
            self.update_activity()

    def on_task(self, ev, s):
        """Tâches de fond (sous-agents) : compteur, et état dans leur bloc."""
        sub, tid = ev.get("subtype"), ev.get("task_id")
        status = ev.get("status") or (ev.get("patch") or {}).get("status")
        if sub == "task_started":
            s["tasks"].add(tid)
            s["agents_stale"] = True  # nouveau sous-agent : relire la liste
        elif sub == "task_notification" or (sub == "task_updated" and status not in (None, "running")):
            s["tasks"].discard(tid)
            if s["idle"] and not s["tasks"]:
                # claude relance d'habitude un tour pour en rendre compte ; sinon, on termine.
                GLib.timeout_add_seconds(30, lambda: self.end_if_done(s) and False)
        if s is not self.current:
            return
        block = self.agent_blocks.get(ev.get("tool_use_id"))
        if block and sub == "task_progress":
            block.progress(ev)
        elif block and sub == "task_notification":
            block.finish(ev)
        self.update_activity()

    # -- sous-agents --
    @staticmethod
    def has_agents_files(s):
        """Conversation Claude Code d'un hôte (locale ou SSH) : ses sous-agents sont lisibles."""
        return bool(s) and s.get("source") is None and s["host"] != CLOUD and not s.get("new")

    def agents_alive(self, s):
        """La session tourne encore (terminal, ou claude -p lancé d'ici)."""
        return bool(s.get("live")) or s["id"] in self.streams

    def show_agents(self, s):
        """Bouton « 🤖 n » de l'en-tête, et le volet s'il montre cette conversation."""
        if s is not self.current:
            return
        agents = s.get("agents") or [] if self.has_agents_files(s) else []
        alive = self.agents_alive(s)
        running = sum(agent_state(a, alive) == "running" for a in agents)
        self.agents_btn.set_visible(bool(agents))
        self.agents_btn.set_label(f"🤖 {len(agents)}" + (f" · {running} en cours" if running else ""))
        if self.has_agents_files(s):
            host, sid = s["host"], s["id"]
            self.agents_panel.set_session(
                sid, lambda a: run_helper(host, "agent", sid, a["tool"], HISTORY_LIMIT),
                lambda it: self.item_widget(it, s))
            if self.agents_panel.get_visible():
                self.agents_panel.update(agents, alive)

    def toggle_agents(self):
        if self.agents_panel.get_visible() and self.side_stack.get_visible_child() is self.agents_panel:
            return self.close_side("agents")
        self.agents_panel.update((self.current or {}).get("agents"), self.agents_alive(self.current))
        self.show_side("agents")

    def open_agent(self, key):
        """Ouvre le volet sur un sous-agent (id de l'agent ou de l'appel Agent qui l'a lancé)."""
        s = self.current
        if not s:
            return
        self.agents_panel.update(s.get("agents"), self.agents_alive(s))
        self.show_side("agents")
        if not self.agents_panel.open_agent(key):
            s["agents_stale"] = True  # pas encore dans la liste (lancé depuis la dernière lecture)
            s["agents_want"] = key

    def poll_agents(self):
        """Relit la liste des sous-agents tant que l'un d'eux travaille (ou qu'un nouveau est lancé)."""
        s = self.current
        if not self.has_agents_files(s) or self.agents_polling:
            return True
        alive = self.agents_alive(s)
        if not (s.pop("agents_stale", False)
                or any(agent_state(a, alive) == "running" for a in s.get("agents") or [])):
            return True
        self.agents_polling = True

        def done(res):
            self.agents_polling = False
            s["agents"] = res.get("agents") or []
            self.show_agents(s)
            want = s.pop("agents_want", None)
            if want and s is self.current and self.agents_panel.get_visible():
                self.agents_panel.open_agent(want)

        def fail(_e):
            self.agents_polling = False
        in_thread(lambda: run_helper(s["host"], "agents", s["id"]), done, fail)
        return True

    # -- fichiers cités --
    def open_file_link(self, lbl, path, line, end):
        """Clic sur un chemin d'un message (appelé par render.on_link)."""
        follow_link(self.get_application(), lbl, path, line, end, self.open_cited_file)

    def open_cited_file(self, path, line, end, new_window=False):
        s = self.current
        if not s:
            return
        cwd = s.get("cwd") if ":" not in (s.get("cwd") or "") else None
        path = resolve(path, cwd)
        if new_window:
            return ViewerWindow(self.get_application(), s["host"], path, line, end).present()
        self.show_side("file")
        self.viewer.load(s["host"], path, line, end)

    def detach_viewer(self, v):
        ViewerWindow(self.get_application(), v.host, v.path, v.line, v.end).present()
        self.close_side("file")

    # -- volet de droite --
    def show_side(self, page):
        """Ouvre la page « plan », « file » ou « agents » du volet (à mi-largeur s'il était fermé)."""
        child = self.side_stack.get_child_by_name(page)
        child.set_visible(True)
        self.side_stack.set_visible_child(child)
        self.update_side()

    def close_side(self, page):
        """Ferme une page du volet ; le volet se cache quand toutes le sont."""
        self.side_stack.get_child_by_name(page).set_visible(False)
        if page == "plan":
            self.sync_plan_cards()
        self.update_side()

    def update_side(self):
        pages = [c for c in (self.plan_panel, self.viewer, self.agents_panel) if c.get_visible()]
        self.side_switch.set_visible(len(pages) > 1)
        if pages and not self.side.get_visible():
            self.side.set_visible(True)
            self.chat_paned.set_position(int(self.chat_paned.get_width() * 0.5))
        self.side.set_visible(bool(pages))

    # -- plan dans le volet --
    def open_plan(self, plan, path=None, request_id=None):
        """Montre un plan de la conversation affichée dans le volet.

        Sans request_id (bouton d'un plan du fil), c'est une bascule : le plan déjà
        affiché retourne dans le fil ; un plan qui attend encore sa validation
        retrouve sa demande, et donc ses actions.
        """
        if not self.current:
            return
        sid, panel = self.current["id"], self.plan_panel
        if request_id is None:
            if panel.get_visible() and panel.sid == sid and panel.plan == plan:
                return self.close_side("plan")
            request_id = next((rid for rid, req in self.pending.get(sid, {}).items()
                               if req.get("tool_name") == "ExitPlanMode"
                               and (req.get("input") or {}).get("plan") == plan), None)
        if request_id is not None:
            self.plans_opened.add(request_id)
        panel.show(sid, plan, path, request_id)
        self.show_side("plan")
        self.sync_plan_cards()

    def sync_plan_cards(self):
        """Replie, dans le fil, les plans affichés dans le volet ; déplie les autres."""
        shown = self.plan_panel.plan if self.plan_panel.get_visible() else None
        child = self.msgs.get_first_child()
        while child:
            if hasattr(child, "plan_body"):
                folded = child.plan_text == shown
                child.plan_body.set_visible(not folded)
                child.plan_side.set_tooltip_text("Remettre dans le fil" if folded
                                                 else "Ouvrir dans le volet")
            child = child.get_next_sibling()

    def plan_run(self, mode):
        p = self.plan_panel
        req = self.pending.get(p.sid, {}).get(p.request_id)
        if req is None:
            return self.request_done(p.sid, p.request_id, "Demande expirée : Claude ne l'attend plus.", ok=False)
        self.answer(p.sid, p.request_id, *plan_allow(req, mode))

    def plan_refuse(self):
        p = self.plan_panel
        self.answer(p.sid, p.request_id, *plan_deny(self.take_feedback() or PLAN_REFUSAL))

    def stop(self):
        """« Arrêter » (ou Échap) : demande d'interruption à claude, qui termine le tour
        proprement (outils et sous-agents arrêtés, « interrompu » noté dans la
        conversation). Un second clic, ou un claude qui n'écoute plus son entrée
        (stdin déjà fermé), tue le processus ; de même s'il ne s'arrête pas en 10 s."""
        proc = self.proc
        if not proc:
            return
        s = self.streams[self.current["id"]][1]
        first = not s.get("interrupted")
        s["interrupted"] = True
        if first and not proc.stdin.closed and self.write_input(proc, json.dumps({
                "type": "control_request", "request_id": f"interrupt-{uuid.uuid4()}",
                "request": {"subtype": "interrupt"}}) + "\n"):
            self.status.set_text("Interruption… (cliquer à nouveau pour forcer l'arrêt)")
            GLib.timeout_add_seconds(10, lambda: proc.poll() is None and proc.terminate() and False)
            return
        proc.terminate()
        self.status.set_text("Interrompu.")
