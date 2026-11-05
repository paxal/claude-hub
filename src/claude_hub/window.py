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

from .gtk import PRIMARY_KEY, PRIMARY_MASK, GLib, GObject, Gdk, Gio, Graphene, Gtk, Pango, in_thread
from .hosts import (
    CLOUD, DEFAULT_LABEL, EFFORTS, HISTORY_LIMIT, model_name, MODE_HELP, MODE_LABELS, MODELS, PERMISSION_MODES, SEARCH_HISTORY_LIMIT, SSH_OPTS,
    load_hosts, load_hosts_with, load_settings, remember_mode, run_helper, save_settings,
)
from .render import (esc, fold_plan, image_widget, make_label, message_widget, plan_roles, texture_from,
                     tool_detail)
from .cursor import (
    check_dirs, cursor_agent, cursor_context, cursor_history, cursor_result, cursor_list, cursor_live, cursor_search,
    cursor_stat, highlight_words, norm_text,
)
from .cloud import (
    cloud_history, cloud_list, cursor_cloud_history, cursor_cloud_list, iso_ts, remote_connected,
    remote_pending, remote_respond, remote_send,
)
from .attachments import IMAGE_EXTS, prepare_image
from .dirpicker import RemoteDirDialog
from .tags import TagStore, dir_matches, dir_token, parse_query, tag_matches, toggle_token
from .tagui import TAG_ICON, TagPopover, filter_popover, tag_chips
from .viewer import FileViewer, ViewerWindow, follow_link, resolve
from .remotecontrol import PERMISSION, HeadChip, WaitCard
from .permissions import EXEC_MODES, PLAN_REFUSAL, RequestCard, control_response, plan_allow, plan_deny
from .planview import PlanPanel
from .subagents import AGENT_TOOLS, SubagentBlock, with_open_button
from .agentsview import (AGENT_ID, AgentsPanel, agent_state, agent_workflows, outside_workflows,
                         running_agents)
from .toolcall import ToolCall, ToolRun
from .feedtime import Timed, day_separator, last_day, unwrap
from .feedsync import changed_from, unread
from .todos import TodoList, TodoPanel, created_id
from .activity import (MAX_HEIGHT as ACTIVITY_MAX_HEIGHT, ActivityPanel, agent_specs, claude_spec,
                       queued_spec, request_spec, shell_spec, waiting_for, workflow_spec)
from .rename import RenamePopover, can_rename, rename_session
from .shell import ShellBlock, mark_monitor, monitor_started, shell_argv, shell_command, waiting_text
from .commands import CommandPopover, fetch_commands, match_commands, typed_command
from .widgets import (
    ChoiceButton, Clamp, count_badge, host_chip, host_icon, host_kind, menu_popover, section_title, set_count,
    short_host, themed_icon, with_placeholder,
)
from .listrow import ACTION_STATES, NOTE_ICON, SessionRow, day_label, local_date, status_kind
from .home import DirButton, HomePage, plan_digest
from .changes import EDIT_TOOLS, ChangesPanel, edits_from_items, group_files
from .notes import excerpt, host_index, note_preview, note_snippet, store_of, valid_id, where_text
from .notesview import NotesPanel
from .switcher import Switcher
from .launcher import Launcher
from .views import ViewsBar, add_view, remove_view
from .usage import UsageBox, context_tokens, cost_text, learn_windows, remember_cost

HOME = ""  # clé du brouillon de la saisie de l'accueil (nouvelle conversation)
CHAT_HINT = "Écrire à Claude…   « / » pour les commandes"
PLAN_HINT = "Remarque sur le plan : Claude continue à planifier"  # quand un plan attend
HOME_HINT = "Que doit faire Claude ?   « / » pour les commandes"
SIDE_PAGES = ("plan", "file", "agents", "changes", "notes")  # pages du volet de droite
# Conversations Claude lues par hôte à la fois (les dernières écrites d'abord) ; la suite se
# lit en faisant défiler la liste. Sur cerberus, lire les 351 transcriptions prenait 10 s.
LIST_PAGE = 30
NOT_READ = object()  # list_more d'un hôte dont la liste n'a pas encore été lue


def title_of(s):
    return s.get("title") or s.get("first") or ("Nouvelle conversation" if s.get("new") else "(sans titre)")


def is_step(item):
    """Élément qui fait partie d'une suite d'étapes du fil : appel d'outil ordinaire (pas un
    sous-agent, ni un plan, ni un message relayé par un agent)."""
    return (bool(item) and item.get("role") == "tool" and item.get("name") not in AGENT_TOOLS
            and item.get("name") != "ExitPlanMode" and item.get("via") != "agent")


def last_reply(items):
    """Texte de la dernière réponse de Claude dans un historique."""
    return next((it["text"] for it in reversed(items or [])
                 if it.get("role") == "assistant" and (it.get("text") or "").strip()), None)


def first_page_merge(old, page, more, prev):
    """(conversations Claude d'un hôte, suite de sa liste) après relecture de la première page
    (page, et sa suite more : [ms, id] du fichier d'où reprendre, None si tout y tient). Ce
    qui a été écrit depuis est dans la page ; celles des pages suivantes déjà lues (old)
    restent. prev : suite d'avant, None si tout était lu, NOT_READ si rien ne l'était."""
    if more is None:
        return page, None
    ids = {s["id"] for s in page}
    kept = [s for s in old if "file_ms" in s and s["id"] not in ids and [s["file_ms"], s["id"]] < more]
    if prev is NOT_READ:
        return page + kept, more
    return page + kept, None if prev is None else min(prev, more)


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


class Hub(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Claude Hub", default_width=1280,
                         default_height=820)
        self.hosts = load_hosts()
        self.settings = load_settings()
        self.sessions = []       # conversations des hôtes cochés, la plus récente en tête
        self.host_sessions = {}  # hôte -> conversations lues au dernier chargement
        self.list_gens = {}      # hôte -> numéro du dernier chargement demandé
        self.loading = set()     # hôtes en cours de chargement
        self.list_errors = {}    # hôte -> erreurs du dernier chargement
        # hôte -> suite de sa liste Claude ([ms, id] d'où lire la page suivante), None si
        # tout est lu ; absent tant qu'elle n'a pas été lue
        self.list_more = {}
        self.more_loading = set()  # hôtes dont la page suivante est en cours de lecture
        self.more_failed = set()   # hôtes dont la page suivante n'a pas pu être lue (jusqu'au rafraîchissement)
        self.more_check = 0        # schedule_more_check en attente
        self.current = None      # dict de la session affichée
        self.size = 0            # taille du fichier à la dernière lecture
        # Éléments de l'historique affichés dans le fil : [(élément, enfant du fil qui le
        # contient)], le premier à l'index feed_start de la transcription (feedsync.py).
        self.feed, self.feed_start = [], 0
        self.feed_of = None      # conversation (dict) dont le fil montre l'historique
        self.history_gen = self.history_shown = 0  # lectures demandées, dernière affichée
        self.streams = {}        # id de session -> (claude -p en cours, dict qui suit sa sortie)
        self.queues = {}         # id de session -> [(texte, images)] à envoyer après la réponse
        self.shells = {}         # id de session -> [ShellBlock] commandes « ! » pas encore envoyées
        # id de session -> [(élément, bulle)] messages transmis pendant la réponse, pas encore
        # lus par claude (absents de sa transcription : remis à la fin du fil relu)
        self.injected = {}
        # copie (ou message modifié : "edit") en préparation :
        # {"sid", "at" (uuid), "widget", "before", "text", "edit"}
        self.fork_at = None
        self.pending = {}        # id de session -> {request_id: demande can_use_tool sans réponse}
        # (celles d'un terminal ou de claude.ai portent « remote » : l'id Remote Control)
        self.remote_answered = set()  # demandes répondues d'ici, encore publiées un moment par l'API
        self.wait_card = None    # carte « Claude attend ta réponse dans son terminal » du fil
        self.agent_blocks = {}   # id de l'appel Agent -> bloc du sous-agent dans la vue affichée
        self.tool_calls = {}     # id d'un autre appel d'outil -> sa ligne dépliable
        self.remote_home = {}
        self.host_defaults = {}  # hôte -> {"model", "effort"} réglés dans son ~/.claude
        self.defaults_shown = None  # hôte dont les défauts sont affichés dans les listes
        self.state_rows = {}     # id de session -> ligne de la liste, si son hôte est coché
        self.rows = {}           # id de session -> ligne de la liste
        self.states_pending = False  # compteurs, filtre et accueil à mettre à jour
        self.segment = "all"     # filtre de la liste : all, todo (à traiter), busy (en cours)
        self.excerpts = {}       # id -> (date, début de la dernière réponse) pour l'accueil
        self.excerpt_loading = set()
        self.syncing_new = False  # vrai quand le programme règle l'hôte d'une nouvelle conversation
        self.notified = {}       # id de session -> conversation à ouvrir depuis sa notification
        self.kept_unread = None  # conversation affichée marquée non lue à la main : le reste jusqu'à la quitter
        self.hub_done = {}       # id de session -> fin du dernier claude -p lancé d'ici
        self.orphan_checks = set()  # orphelins (claude -p d'avant un redémarrage) en cours de relevé
        self.held = set()        # sessions de terminal dont la fin attend leurs shells et monitors
        self.asked = set()       # sessions de terminal à qui l'on a écrit pendant cette attente
        self.content_hits = {}   # id -> {"role", "snippet"} trouvés dans les transcriptions
        self.drafts = {}         # id -> (texte, pièces jointes) saisis mais pas envoyés (en mémoire)
        self.suggestions = {}    # id -> (prochain message prédit par claude, heure de réception)
        self.unsent = {}         # id -> nouvelle conversation encore jamais envoyée
        self.search_gen = 0
        self.polling_states = False
        self.tag_store = TagStore()
        self.note_index = {}     # hôte où sont rangées les notes -> {id: début de la note}
        self.shown = 0           # conversations affichées par fill_list
        self.plans_opened = set()  # demandes de plan déjà ouvertes dans le volet (une seule ouverture auto)
        self.sides = {}          # id -> volet laissé ouvert dans cette conversation (side_state)
        self.launcher = Launcher()  # nombre de conversations à traiter sur l'icône de la barre des tâches
        self.switcher = None     # sélecteur Ctrl+K, créé à la première ouverture
        self.views_pending = False

        paned = Gtk.Paned(position=340, shrink_start_child=False)
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
        self.changes_panel = ChangesPanel(lambda: self.close_side("changes"), self.edit_time)
        self.notes_panel = NotesPanel(lambda: self.close_side("notes"), self.note_saved, self.note_failed)
        self.side_stack = Gtk.Stack(vexpand=True)
        self.side_stack.add_titled(self.plan_panel, "plan", "Plan")
        self.side_stack.add_titled(self.viewer, "file", "Fichier")
        self.side_stack.add_titled(self.agents_panel, "agents", "Agents")
        self.side_stack.add_titled(self.changes_panel, "changes", "Modifications")
        self.side_stack.add_titled(self.notes_panel, "notes", "Notes")
        self.side_switch = Gtk.StackSwitcher(stack=self.side_stack, halign=Gtk.Align.CENTER,
                                             margin_top=6)
        self.side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["side"])
        self.side.append(self.side_switch)
        self.side.append(self.side_stack)
        for page in SIDE_PAGES:
            self.set_side_page(page, False)
        self.side.set_visible(False)
        self.chat_paned.set_end_child(self.side)
        paned.set_end_child(self.chat_paned)
        shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for accel, action in (("<Primary>n", self.new_here),
                              ("<Primary>k", self.show_switcher),
                              ("<Alt>Up", lambda: self.step_row(-1)),
                              ("<Alt>Down", lambda: self.step_row(1)),
                              ("<Alt><Shift>Up", lambda: self.step_row(-1, todo=True)),
                              ("<Alt><Shift>Down", lambda: self.step_row(1, todo=True))):
            shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                                    Gtk.CallbackAction.new(lambda *_a, f=action: f() or True)))
        self.add_controller(shortcuts)
        # fermer la fenêtre retire la pastille de l'icône, et enregistre la note en cours
        # (claude-hub attend la fin de l'enregistrement pour quitter)
        self.connect("close-request", lambda _w: self.launcher.set_count(0) or self.close_notes() or False)

        # revenir sur la fenêtre vaut lecture de la conversation affichée
        self.connect("notify::is-active", lambda w, _p: w.is_active() and w.current
                     and w.current["id"] != w.kept_unread and w.mark_read(w.current["id"]))
        self.hosts_changed()
        self.go_home()
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
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sidebar-list"])
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_start=10,
                      margin_end=10, margin_bottom=8)
        row = Gtk.Box(spacing=6)
        # Hôtes et sources listés : menu à cases, résumé (icônes + noms) sur le bouton.
        self.hosts_btn = Gtk.MenuButton(hexpand=True, tooltip_text="Hôtes et sources affichés")
        self.hosts_summary = Gtk.Box(spacing=4)
        self.hosts_btn.set_child(self.hosts_summary)
        self.hosts_btn.set_popover(self.build_hosts_menu())
        self.build_host_checks()
        row.append(self.hosts_btn)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rafraîchir")
        refresh.connect("clicked", lambda _b: self.refresh_list())
        row.append(refresh)
        self.new_btn = Gtk.Button(tooltip_text=f"Nouvelle conversation ({PRIMARY_KEY}N)")
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Image(icon_name="claude-hub-compose-symbolic"))
        inner.append(Gtk.Label(label="Nouvelle"))
        self.new_btn.set_child(inner)
        self.new_btn.connect("clicked", lambda _b: self.new_here())
        row.append(self.new_btn)
        top.append(row)

        row = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Rechercher…  #tag  dir:~/chemin",
                                      hexpand=True, search_delay=350,
                                      tooltip_text="Texte : titres et contenu des conversations\n"
                                                   "#tag : conversations taguées, sur tous les hôtes\n"
                                                   "#dossier/ : tous les tags rangés sous dossier/\n"
                                                   "dir:~/chemin : conversations lancées dans ce dossier")
        self.search.connect("search-changed", self.on_search)
        self.search.connect("changed", lambda _e: self.update_views())
        row.append(self.search)
        for icon, tip, make in ((TAG_ICON, "Filtrer par tag (tous les hôtes)", self.tags_menu),
                                ("folder-symbolic", "Filtrer par dossier de travail", self.dirs_menu)):
            btn = Gtk.MenuButton(icon_name=icon, tooltip_text=tip)
            btn.set_create_popup_func(make)
            row.append(btn)
        top.append(row)
        # Vues enregistrées : recherches gardées en pastilles.
        self.views_bar = ViewsBar(self.apply_view, self.save_view, self.remove_view)
        top.append(self.views_bar)
        self.update_views()

        # Filtre par état : toutes, à traiter (non lues, demandes), en cours.
        segs = Gtk.Box(css_classes=["linked", "segments"], homogeneous=True)
        self.segments, self.seg_counts, group = {}, {}, None
        for key, label, tip in (("all", "Toutes", "Toutes les conversations"),
                                ("todo", "À traiter", "Réponses non lues, plans, questions et "
                                                      "autorisations en attente\n"
                                                      "Alt+Maj+↓ : la suivante, Alt+Maj+↑ : la précédente"),
                                ("busy", "En cours", "Conversations où Claude travaille")):
            btn = Gtk.ToggleButton(active=key == "all", tooltip_text=tip, group=group)
            group = group or btn
            inner = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            inner.append(Gtk.Label(label=label))
            if key != "all":
                self.seg_counts[key] = count_badge(key)
                inner.append(self.seg_counts[key])
            btn.set_child(inner)
            btn.connect("toggled", self.on_segment, key)
            self.segments[key] = btn
            segs.append(btn)
        top.append(segs)
        side.append(top)

        self.listbox = Gtk.ListBox(css_classes=["sessions"])
        # épinglées, puis plus récente en tête ; touch() fait remonter une ligne sans reconstruire la liste
        self.listbox.set_sort_func(lambda a, b: (b.pinned - a.pinned) or (
            (b.session.get("mtime", 0) > a.session.get("mtime", 0))
            - (b.session.get("mtime", 0) < a.session.get("mtime", 0))))
        self.listbox.set_header_func(self.day_header)
        self.listbox.set_filter_func(self.row_visible)
        self.listbox.connect("row-activated", self.on_row)
        self.list_day = local_date(time.time())
        # clic droit sur une ligne : menu contextuel (actions par id de session)
        for name, handler in (("toggle-unread", self.toggle_unread), ("row-tags", self.row_tags),
                              ("row-rename", self.row_rename), ("row-archive", self.row_archive),
                              ("row-dir", self.row_dir), ("copy-id", self.copy_id),
                              ("open-web", self.open_web), ("row-pin", self.toggle_pin),
                              ("row-notes", self.row_notes)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            # après la fermeture du menu : un popover ouvert par-dessus lui serait refusé
            action.connect("activate", lambda _a, sid, handler=handler:
                           GLib.idle_add(lambda: handler(sid.get_string()) and False))
            self.add_action(action)
        menu_click = Gtk.GestureClick(button=3)
        menu_click.connect("pressed", self.on_row_menu)
        self.listbox.add_controller(menu_click)
        # sous la liste, pendant la lecture de la page suivante (load_more)
        self.more_row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, visible=False, css_classes=["list-more"])
        self.more_row.append(Gtk.Image(icon_name="claude-hub-busy-symbolic"))
        self.more_row.append(Gtk.Label(label="Chargement des conversations plus anciennes…"))
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.append(self.listbox)
        list_box.append(self.more_row)
        self.list_scroll = Gtk.ScrolledWindow(child=list_box, vexpand=True,
                                              hscrollbar_policy=Gtk.PolicyType.NEVER)
        adj = self.list_scroll.get_vadjustment()
        self.listbox.set_adjustment(adj)  # Page préc./suiv. : la liste n'est plus l'enfant direct du défilement
        # bas de la liste en vue (défilement, liste plus courte, fenêtre agrandie) : page suivante
        for signal in ("changed", "value-changed"):
            adj.connect(signal, lambda _a: self.maybe_load_more())
        side.append(self.list_scroll)
        footer = Gtk.Box(css_classes=["list-footer"])
        self.list_status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.list_status.connect("notify::label", lambda lbl, _p: lbl.set_tooltip_text(lbl.get_label() or None))
        footer.append(self.list_status)
        side.append(footer)
        return side

    def build_hosts_menu(self):
        """Menu du bouton des hôtes : hôtes affichés, sources (Claude, Cursor, archives),
        ajout d'un hôte SSH."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, width_request=300)
        box.append(section_title("Hôtes"))
        self.hosts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.hosts_box)
        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        box.append(section_title("Sources"))
        self.show_claude = Gtk.CheckButton(label="Claude Code", active=self.settings["claude"],
                                           tooltip_text="Récupérer les conversations Claude Code",
                                           css_classes=["menu-row"])
        self.show_cursor = Gtk.CheckButton(label="Cursor", active=self.settings["cursor"],
                                           tooltip_text="Récupérer les conversations Cursor",
                                           css_classes=["menu-row"])
        self.show_archived = Gtk.CheckButton(active=self.settings["show_archived"],
                                             tooltip_text="Afficher aussi les conversations archivées",
                                             css_classes=["menu-row"])
        line = Gtk.Box(spacing=8)
        line.append(Gtk.Label(label="Archivées", xalign=0, hexpand=True))
        self.archived_count = Gtk.Label(css_classes=["menu-kind"])
        line.append(self.archived_count)
        self.show_archived.set_child(line)
        for key, btn in (("claude", self.show_claude), ("cursor", self.show_cursor)):
            btn.connect("toggled", self.on_source_toggled, key)
        self.show_archived.connect("toggled", self.on_archived_toggled)
        for btn in (self.show_claude, self.show_cursor, self.show_archived):
            box.append(btn)
        box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        add = Gtk.Button(css_classes=["flat"])
        inner = Gtk.Box(spacing=10)
        inner.append(Gtk.Image(icon_name="list-add-symbolic"))
        inner.append(Gtk.Label(label="Ajouter un hôte SSH…"))
        add.set_child(inner)
        self.host_entry = Gtk.Entry(placeholder_text="hôte ou user@hôte, puis Entrée", visible=False)
        self.host_entry.connect("activate", self.add_host)
        add.connect("clicked", lambda _b: (self.host_entry.set_visible(True), self.host_entry.grab_focus()))
        box.append(add)
        box.append(self.host_entry)
        pop = menu_popover(box)
        pop.connect("closed", lambda _p: self.host_entry.set_visible(False))
        return pop

    def build_chat(self):
        chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Accueil (aucune conversation affichée) ou conversation : en-tête et fil.
        self.pages = Gtk.Stack(vexpand=True)
        self.home = HomePage(self)
        self.pages.add_named(self.home, "home")
        conv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["chat-head"])
        top = Gtk.Box(spacing=10)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["chat-title"])
        top.append(self.title)
        # (ce qui tourne et ce qui attend : au-dessus de la saisie, voir activity.py)
        top.append(Gtk.Box(hexpand=True))
        buttons = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.agents_btn = Gtk.Button(visible=False, css_classes=["flat"])
        inner = Gtk.Box(spacing=5)
        inner.append(Gtk.Image(icon_name="claude-hub-agents-symbolic"))
        self.agents_count = Gtk.Label()
        inner.append(self.agents_count)
        self.agents_btn.set_child(inner)
        self.agents_btn.connect("clicked", lambda _b: self.toggle_agents())
        buttons.append(self.agents_btn)
        self.changes_btn = Gtk.Button(visible=False, css_classes=["flat"])
        inner = Gtk.Box(spacing=5)
        inner.append(Gtk.Image(icon_name="claude-hub-changes-symbolic"))
        self.changes_count = Gtk.Label()
        inner.append(self.changes_count)
        self.changes_btn.set_child(inner)
        self.changes_btn.connect("clicked", lambda _b: self.toggle_changes())
        buttons.append(self.changes_btn)
        self.edits_gen = 0       # dernière lecture des modifications demandée
        self.edits_timer = 0     # relecture prévue pendant une réponse
        self.new_here_btn = Gtk.Button(icon_name="document-new-symbolic", visible=False,
                                       css_classes=["flat"],
                                       tooltip_text=f"Nouvelle conversation dans ce dossier ({PRIMARY_KEY}N)")
        self.new_here_btn.connect("clicked", lambda _b: self.new_here())
        buttons.append(self.new_here_btn)
        self.note_btn = Gtk.Button(icon_name=NOTE_ICON, visible=False, css_classes=["flat"])
        self.note_btn.connect("clicked", lambda _b: self.toggle_notes())
        buttons.append(self.note_btn)
        self.tag_btn = Gtk.Button(icon_name=TAG_ICON, visible=False, css_classes=["flat"],
                                  tooltip_text="Tags de cette conversation")
        self.tag_btn.connect("clicked", lambda b: self.edit_tags(b, self.current, self.current["host"]))
        buttons.append(self.tag_btn)
        self.rename_btn = Gtk.Button(icon_name="document-edit-symbolic", visible=False,
                                     css_classes=["flat"], tooltip_text="Renommer la conversation")
        self.rename_btn.connect("clicked", self.ask_rename)
        buttons.append(self.rename_btn)
        self.more_btn = Gtk.MenuButton(visible=False, tooltip_text="Autres actions : lue ou non lue, "
                                                                   "archiver, copier l'identifiant…")
        self.more_btn.add_css_class("flat")
        self.more_btn.set_child(themed_icon("overflow-menu-symbolic", "view-more-symbolic"))
        self.more_btn.set_create_popup_func(self.head_menu)
        buttons.append(self.more_btn)
        top.append(buttons)
        head.append(top)
        # hôte · modèle et effort de la dernière réponse · dossier · id · tags
        self.head_meta = Gtk.Box(spacing=8, css_classes=["head-meta"])
        self.head_host = Gtk.Box()
        # terminal sans Remote Control, claude -p d'avant un redémarrage (remotecontrol.py)
        self.head_chip = HeadChip()
        self.head_model = Gtk.Label()
        self.head_cwd = Gtk.Label(css_classes=["mono"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                                  selectable=True)
        self.head_id = Gtk.Label(css_classes=["mono"])
        self.head_tags = Gtk.Box(spacing=4, visible=False)
        self.head_usage = UsageBox()  # contexte rempli, coût de la dernière réponse
        self.head_seps = [Gtk.Label(label="·") for _ in range(2)]
        for w in (self.head_host, self.head_chip, self.head_model, self.head_usage, self.head_seps[0],
                  self.head_cwd, self.head_seps[1], self.head_id, self.head_tags):
            self.head_meta.append(w)
        head.append(self.head_meta)
        conv.append(head)

        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=14,
                            margin_bottom=8, margin_start=12, margin_end=12, css_classes=["msgs"])
        self.scroll = Gtk.ScrolledWindow(child=Clamp(self.msgs, 804, hexpand=True), vexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        conv.append(self.scroll)
        self.pages.add_named(conv, "chat")
        chat.append(self.pages)
        # Collé en bas : la vue suit les changements de taille (message mis en page plus
        # tard, bandeau d'activité qui réduit la zone) tant qu'on n'a pas remonté.
        self.follow_end = self.follow_pending = False
        vadj = self.scroll.get_vadjustment()
        vadj.connect("changed", self.on_scroll_resized)
        vadj.connect("value-changed", self.on_scroll_moved)

        # Sous le fil, dans la même colonne : copie en préparation, puis la saisie, avec
        # au-dessus du texte ce qui tourne et ce qui attend.
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        chat.append(Clamp(bottom, 780, hexpand=True, margin_start=20, margin_end=20, margin_bottom=14))

        # Copie de la conversation à partir d'un message, ou message modifié (boutons au
        # survol des messages). L'avertissement : une réponse en cours sera arrêtée.
        self.fork_bar = Gtk.Box(spacing=6, css_classes=["fork-bar"], visible=False)
        self.fork_icon = Gtk.Image(icon_name="claude-hub-fork-symbolic")
        self.fork_bar.append(self.fork_icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.fork_warn = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.fork_warn.add_css_class("fork-warn")
        texts.append(self.fork_warn)
        self.fork_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        texts.append(self.fork_label)
        self.fork_bar.append(texts)
        self.fork_cancel = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                                      tooltip_text="Annuler la copie", valign=Gtk.Align.CENTER)
        self.fork_cancel.connect("clicked", lambda _b: self.cancel_fork())
        self.fork_bar.append(self.fork_cancel)
        bottom.append(self.fork_bar)

        # Un seul cadre : la liste de ce qui tourne et de ce qui attend (Claude,
        # sous-agents, shells et monitors, tâches, messages en file), puis la saisie (main).
        self.composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["composer"],
                                overflow=Gtk.Overflow.HIDDEN)
        bottom.append(self.composer)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.todo_panel = TodoPanel()  # tâches de Claude (TodoWrite, TaskCreate…)
        self.activity_panel = ActivityPanel(self.todo_panel)
        self.activity_side = Gtk.ScrolledWindow(
            child=self.activity_panel, visible=False, css_classes=["activity-side"],
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
            max_content_height=ACTIVITY_MAX_HEIGHT)
        self.composer.append(self.activity_side)
        self.composer.append(main)

        # Accueil : hôte et dossier de la nouvelle conversation.
        self.new_line = Gtk.Box(spacing=6, css_classes=["composer-new"], visible=False)
        self.new_hosts = [h for h in self.hosts if h != CLOUD]
        self.new_host_btn = ChoiceButton(
            self.new_hosts, 0, icons=lambda i: host_icon(self.new_hosts[i]),
            short=lambda _i, h: short_host(h), tooltip="Hôte de la nouvelle conversation",
            on_change=lambda _i: self.new_host_changed())
        self.dir_btn = DirButton(self.recent_dirs, lambda: self.pick_dir(None))
        for w in (Gtk.Label(label="Nouvelle conversation sur"), self.new_host_btn, Gtk.Label(label="dans"),
                  self.dir_btn):
            self.new_line.append(w)
        main.append(self.new_line)

        self.attachments = []    # [(media_type, octets)]
        self.attach_box = Gtk.Box(spacing=6, margin_start=10, margin_top=8, visible=False,
                                  halign=Gtk.Align.START)
        main.append(self.attach_box)

        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, hexpand=True, top_margin=10,
                                  bottom_margin=4, left_margin=12, right_margin=12)
        self.input.add_css_class("composer-input")  # en plus de « view »
        self.input.connect("paste-clipboard", self.on_paste)
        self.input.get_buffer().connect("changed", lambda _b: (self.draft_changed(),
                                                               self.update_commands(),
                                                               self.update_shell_mode()))
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, Gdk.Texture])
        drop.connect("drop", self.on_drop)
        chat.add_controller(drop)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.input.add_controller(keys)
        input_scroll = Gtk.ScrolledWindow(child=self.input, hexpand=True, min_content_height=44,
                                          max_content_height=220, propagate_natural_height=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER)
        # Saisie vide après une réponse : le message que Claude prédit, Tab pour le reprendre.
        self.suggest_key = Gtk.Label(label="Tab", css_classes=["keycap"], valign=Gtk.Align.CENTER,
                                     visible=False)
        field, self.placeholder = with_placeholder(input_scroll, self.input, CHAT_HINT,
                                                   self.suggest_key)
        main.append(field)
        # Complétion des commandes « / » : liste par (hôte, dossier), demandée à claude.
        self.commands = {}  # (hôte, dossier) -> [commande] | "loading" | Exception
        self.cmd_popover = CommandPopover(input_scroll, self.pick_command)
        self.cmd_dismissed = False

        bar = Gtk.Box(spacing=6, css_classes=["composer-bar"])
        clip = Gtk.Button(icon_name="mail-attachment-symbolic", css_classes=["flat"],
                          tooltip_text=f"Joindre des images (ou {PRIMARY_KEY}V, ou glisser-déposer)")
        clip.connect("clicked", self.pick_images)
        bar.append(clip)
        self.model_dd = self.choice_dd(MODELS, "model", "Modèle (--model)", self.model_label,
                                       self.model_short)
        self.effort_dd = self.choice_dd(EFFORTS, "effort", "Effort de raisonnement (--effort)",
                                        short=self.effort_short)
        bar.append(self.model_dd)
        bar.append(self.effort_dd)
        self.mode_dd = ChoiceButton(
            [MODE_LABELS[m] for m in PERMISSION_MODES], PERMISSION_MODES.index(self.settings["mode"]),
            helps=[MODE_HELP[m] for m in PERMISSION_MODES], on_change=lambda _i: self.on_mode_changed(),
            tooltip="Mode de permissions, retenu pour chaque conversation ; les demandes de Claude "
                    "s'affichent dans le fil\nMaj+Tab : bascule automatique / plan")
        self.mode_dd.list.add_css_class("modes")
        self.syncing_mode = False  # vrai quand c'est la conversation qui règle le mode, pas l'utilisateur
        self.paint_mode()
        bar.append(self.mode_dd)
        self.status = Gtk.Label(xalign=1, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                css_classes=["hint"])
        self.status.connect("notify::label", lambda lbl, _p: lbl.set_tooltip_text(lbl.get_label() or None))
        bar.append(self.status)
        # Bouton à menu : « Envoyer » et ses variantes (tout de suite, en file).
        self.send_box = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
        self.send_btn = Gtk.Button(label="Envoyer", css_classes=["suggested-action"])
        self.send_btn.connect("clicked", lambda _b: self.send())
        self.send_box.append(self.send_btn)
        action = Gio.SimpleAction.new("send", GLib.VariantType.new("s"))
        action.connect("activate", lambda _a, mode: self.send(mode.get_string()))
        self.add_action(action)
        menu = Gio.Menu()
        for label, mode, accel in (("Envoyer", "auto", "Return"),
                                   ("Envoyer tout de suite", "now", "<Primary>Return"),
                                   ("Mettre en file d'attente", "queue", "<Alt>Return")):
            item = Gio.MenuItem.new(label, f"win.send::{mode}")
            item.set_attribute_value("accel", GLib.Variant.new_string(accel))
            menu.append_item(item)
        # Bouton ordinaire plutôt qu'un Gtk.MenuButton : Breeze ne dessine pas sa flèche
        # et ne l'accole pas (bouton « toggle » imbriqué). Chevron des listes déroulantes.
        arrow = Gtk.Button(icon_name="pan-down-symbolic", css_classes=["suggested-action"],
                           tooltip_text="Autres façons d'envoyer")
        self.send_menu = Gtk.PopoverMenu.new_from_model(menu)
        self.send_menu.set_parent(arrow)
        arrow.connect("clicked", lambda _b: self.send_menu.popup())
        self.send_box.append(arrow)
        bar.append(self.send_box)
        self.start_btn = Gtk.Button(label="Démarrer", css_classes=["suggested-action"], visible=False,
                                    valign=Gtk.Align.CENTER, tooltip_text="Démarrer la conversation (Entrée)")
        self.start_btn.connect("clicked", lambda _b: self.start_from_home())
        bar.append(self.start_btn)
        main.append(bar)
        return chat

    def model_label(self, model):
        """« opus · Opus 5.5 » pour un alias, « Opus 5.5 » pour un identifiant complet."""
        version = self.settings["model_versions"].get(model)
        return f"{model} · {model_name(version)}" if version else model_name(model)

    def model_display(self, model):
        """Nom court d'un modèle (alias ou identifiant) : « Opus 5.5 »."""
        version = self.settings["model_versions"].get(model)
        return model_name(version) if version else (model.capitalize() if model in MODELS
                                                     else model_name(model))

    def model_short(self, i, _label):
        """Texte du bouton du modèle : son nom, ou celui du défaut de l'hôte."""
        if i:
            return self.model_display(MODELS[i])
        value = (self.host_defaults.get(self.defaults_shown) or {}).get("model")
        return self.model_display(value) if value else "Modèle par défaut"

    def effort_short(self, i, _label):
        if i:
            return EFFORTS[i]
        value = (self.host_defaults.get(self.defaults_shown) or {}).get("effort")
        return value or "Effort par défaut"

    def relabel_models(self):
        """Remet les versions à jour dans la liste des modèles (après une réponse de claude)."""
        for i, m in enumerate(MODELS[1:], 1):
            self.model_dd.set_item_label(i, self.model_label(m))
        self.defaults_shown = None
        self.show_defaults()

    def choice_dd(self, options, key, tooltip, label=str, short=None):
        """Bouton à choix dont le choix est mémorisé dans settings[key] (None = défaut)."""
        selected = options.index(self.settings[key]) if self.settings[key] in options[1:] else 0

        def changed(i):
            self.settings[key] = options[i] if i > 0 else None
            save_settings(self.settings)
        return ChoiceButton([options[0]] + [label(o) for o in options[1:]], selected, tooltip=tooltip,
                            short=short, on_change=changed)

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
            dd.set_item_label(0, DEFAULT_LABEL[key].format(value) if value else options[0])
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
        """Hôte d'une nouvelle conversation (choisi sur l'accueil)."""
        return self.new_hosts[self.new_host_btn.get_selected()]

    def build_host_checks(self):
        """Cases des hôtes dans le menu, et résumé sur le bouton."""
        while (child := self.hosts_box.get_first_child()):
            self.hosts_box.remove(child)
        shown = self.settings["hosts_shown"]
        for h in self.hosts:
            check = Gtk.CheckButton(active=h in shown, css_classes=["menu-row"])
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(icon_name=host_icon(h)))
            box.append(Gtk.Label(label=h, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            box.append(Gtk.Label(label=host_kind(h), css_classes=["menu-kind"]))
            check.set_child(box)
            check.connect("toggled", self.on_host_toggled, h)
            self.hosts_box.append(check)
        while (child := self.hosts_summary.get_first_child()):
            self.hosts_summary.remove(child)
        hosts = self.shown_hosts()
        icons = Gtk.Box(spacing=4, css_classes=["dim"])
        for icon in dict.fromkeys(host_icon(h) for h in hosts):
            icons.append(Gtk.Image(icon_name=icon))
        self.hosts_summary.append(icons)
        self.hosts_summary.append(Gtk.Label(label=", ".join(short_host(h) for h in hosts) or "aucun hôte",
                                            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        self.hosts_summary.append(Gtk.Image(icon_name="pan-down-symbolic", css_classes=["chevron"]))

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
                self.list_more.pop(h, None)
                self.more_failed.discard(h)
                self.loading.discard(h)
        if added is None and self.new_host not in hosts:  # au démarrage
            first = next((h for h in hosts if h in self.new_hosts), "local")
            self.new_host_btn.set_selected(self.new_hosts.index(first))
        self.content_hits = {}
        self.merge_sessions()
        self.fill_list()
        if not hosts:
            self.list_status.set_text("Aucun hôte coché : choisis-en dans la liste du haut.")
        self.refresh_list(added)
        self.show_defaults()

    def new_host_changed(self):
        if not self.syncing_new:  # hôte choisi à la main : le dossier d'un autre hôte n'a plus de sens
            self.dir_btn.set_value("~")
        self.show_defaults()

    def add_host(self, entry):
        h = entry.get_text().strip()
        entry.set_text("")
        entry.set_visible(False)
        if not h or h in self.hosts:
            return
        self.hosts = load_hosts_with(self.hosts[1:-1] + [h])
        new_host = self.new_host
        self.new_hosts = [x for x in self.hosts if x != CLOUD]
        self.new_host_btn.set_labels(self.new_hosts, self.new_hosts.index(new_host))
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
        loaded = {s["id"] for s in self.sessions}

        def search_host(h):
            """Résultats de la recherche sur h, et les conversations trouvées pas encore lues
            (plus bas que les pages chargées)."""
            hits = run_helper(h, "search", q, 200)
            missing = [sid for sid in hits if sid not in loaded]
            found = (run_helper(h, "list", json.dumps({"count": 0, "ids": missing}))["sessions"]
                     if missing else [])
            return hits, found

        def fetch():
            jobs = {h: (lambda h=h: search_host(h)) for h in hosts if with_claude}
            if with_cursor:
                jobs["Cursor"] = lambda: (cursor_search(q), [])
            hits, errors, found = {}, [], {}
            for name, (res, err) in run_parallel(jobs).items():
                if err:
                    errors.append(f"{name} : {err}")
                else:
                    hits.update(res[0])
                    found[name] = res[1]
            return hits, errors, found

        def done(out):
            hits, errors, found = out
            if gen != self.search_gen:
                return  # requête dépassée
            shown = self.shown_hosts()
            for h, sessions in found.items():
                if sessions and h in shown:
                    self.add_sessions(h, sessions)
            if any(found.values()):
                self.merge_sessions()
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
        # première page ; les épinglées en plus, où qu'elles soient (en tête de liste)
        first_page = json.dumps({"count": LIST_PAGE, "ids": sorted(self.settings["pinned"])})

        def notes(host, listed):
            """Index des notes de l'hôte : donné par `list`, sinon demandé à part (pas
            après un `list` qui a échoué : l'hôte ne répond pas)."""
            if store_of(host) != "local" and with_claude and listed is None:
                return None
            try:
                return host_index(host, listed)
            except Exception:  # noqa: BLE001 — sans index, pas d'icône ; le volet relit la note
                return None

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
                return res, cursor, errors, notes(host, None)
            listed = None
            if with_claude:
                try:
                    res = listed = run_helper(host, "list", first_page)
                except Exception as e:  # noqa: BLE001 — Cursor reste lisible hors ligne
                    errors.append(f"{host} : {e}")
            if with_cursor:
                try:
                    cursor = cursor_list(host, all_hosts)
                    check_dirs(host, cursor)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"Cursor ({host}) : {e}")
            return res, cursor, errors, notes(host, listed)

        def done(host, gen, out):
            if gen != self.list_gens[host] or host not in self.shown_hosts():
                return  # chargement dépassé, ou hôte décoché entre-temps
            res, cursor, errors, notes_found = out
            if notes_found is not None:
                self.note_index[store_of(host)] = notes_found
                if self.current and store_of(self.current["host"]) == store_of(host):
                    self.show_note_btn(self.current)
            self.loading.discard(host)
            self.list_errors[host] = errors
            if res["home"]:
                self.remote_home[host] = res["home"]
            page = res["sessions"]
            if "more" in res:  # liste Claude de l'hôte : les pages suivantes déjà lues restent
                page, self.list_more[host] = first_page_merge(
                    self.host_sessions.get(host, []), page, res["more"], self.list_more.get(host, NOT_READ))
                self.more_failed.discard(host)
            else:  # cloud, Claude décoché, ou hôte injoignable
                self.list_more.pop(host, None)
            listed = page + cursor
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

    def add_sessions(self, host, sessions):
        """Ajoute à la liste de l'hôte des conversations lues à part (page suivante, trouvées
        par une recherche) ; remplace celles qui y étaient déjà."""
        ids = {s["id"] for s in sessions}
        for s in sessions:
            s["host"] = host
        self.host_sessions[host] = [s for s in self.host_sessions.get(host, []) if s["id"] not in ids] + sessions
        self.tag_store.refresh(host, sessions)

    def more_hosts(self):
        """Hôtes cochés dont la liste Claude a encore des pages à lire (hors échec de lecture)."""
        return [h for h in self.shown_hosts() if self.list_more.get(h) is not None and h not in self.more_failed]

    def list_edge(self):
        """Date (s) au-dessus de laquelle la liste est complète : la plus récente des suites
        à lire ; None si tout est lu."""
        hosts = self.more_hosts()
        return max(self.list_more[h][0] for h in hosts) / 1000 if hosts else None

    def below_edge(self, s, edge):
        """s attend sa page : sans filtre, la liste s'arrête à edge (list_edge), là où elle cesse
        d'être complète ; plus bas manquent des conversations Claude pas encore lues, et le reste
        (Cursor, cloud) viendra avec elles. Les épinglées, celles qui tournent et celle affichée
        restent."""
        sid = s["id"]
        return (edge is not None and s.get("mtime", 0) < edge and not s.get("live")
                and sid not in self.settings["pinned"] and sid not in self.unsent and sid not in self.streams
                and not (self.current and self.current["id"] == sid))

    def maybe_load_more(self):
        """Moins d'un écran sous le bas de la liste : lit la page suivante de l'hôte qui arrête
        la liste (list_edge). Pas dans « À traiter » ni « En cours » (des conversations
        récentes), ni dans la vue par tag (elle montre déjà toutes les conversations taguées)."""
        adj = self.list_scroll.get_vadjustment()
        hosts = self.more_hosts()
        if (not hosts or self.segment != "all" or parse_query(self.search.get_text())[0]
                or not adj.get_page_size()
                or adj.get_upper() - adj.get_value() - adj.get_page_size() > adj.get_page_size()):
            return
        host = max(hosts, key=lambda h: self.list_more[h])
        if host not in self.loading and host not in self.more_loading:
            self.load_more(host)

    def load_more(self, host):
        """Lit la page suivante de la liste Claude de l'hôte."""
        after = self.list_more[host]
        self.more_loading.add(host)
        self.update_more_row()

        def done(res):
            self.more_loading.discard(host)
            self.update_more_row()
            if host not in self.shown_hosts() or self.list_more.get(host) != after:
                self.maybe_load_more()  # liste relue ou décochée entre-temps : reprendre d'où elle en est
                return
            self.add_sessions(host, res["sessions"])
            self.list_more[host] = res["more"]
            self.merge_sessions()
            self.fill_list()
            self.show_list_status()

        def fail(e):
            self.more_loading.discard(host)
            self.more_failed.add(host)  # pas de nouvel essai avant le prochain rafraîchissement
            self.list_errors.setdefault(host, []).append(f"{host} : {e}")
            self.update_more_row()
            self.fill_list()  # l'hôte n'arrête plus la liste (list_edge) : la suite des autres s'affiche
            self.show_list_status()

        in_thread(lambda: run_helper(host, "list", json.dumps({"count": LIST_PAGE, "after": after})),
                  done, fail)

    def schedule_more_check(self):
        """maybe_load_more une fois la liste remise en page (liste remplie, filtre changé) : le
        signal « changed » du défilement ne vient pas si elle tient dans la fenêtre avant comme
        après (beaucoup d'archivées, filtre)."""
        if not self.more_check:
            self.more_check = GLib.timeout_add(200, self.run_more_check)

    def run_more_check(self):
        self.more_check = 0
        self.maybe_load_more()
        return False

    def update_more_row(self):
        """« Chargement des conversations plus anciennes… » sous la liste, le temps de lire la
        page suivante."""
        self.more_row.set_visible(any(h in self.more_loading for h in self.shown_hosts()))

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
        self.state_rows = {}
        self.rows = {}
        self.listbox.remove_all()
        tags_f, dirs_f, query = parse_query(self.search.get_text())
        q = norm_text(query)
        show_archived = self.show_archived.get_active()
        hosts = self.shown_hosts()
        current = self.current["id"] if self.current else None
        entries = [(s["host"], s) for s in self.sessions]
        if tags_f:  # la vue par tag montre aussi les conversations taguées des autres hôtes
            seen = {s["id"] for s in self.sessions}
            entries += [(h, s) for h, s in self.tag_store.tagged()
                        if s["id"] not in seen and h in self.hosts]
            entries.sort(key=lambda e: -e[1].get("mtime", 0))
        edge = None if tags_f or dirs_f or q else self.list_edge()
        self.shown = 0
        for host, s in entries:
            if not self.source_shown(s):
                continue
            archived = self.is_archived(s)
            if archived and not show_archived:
                continue
            if self.below_edge(s, edge):
                continue
            tags = self.tag_store.tags(s["id"])
            if not all(tag_matches(tags, f) for f in tags_f):
                continue
            home = self.remote_home.get(host, "")
            if dirs_f and not any(dir_matches(s.get("cwd"), d, home) for d in dirs_f):
                continue
            label = title_of(s)
            cwd = s.get("cwd") or "sans dossier"
            if home and cwd.startswith(home):
                cwd = "~" + cwd[len(home):]
            hit = self.content_hits.get(s["id"])
            note, note_hit = self.note_text(host, s["id"]), None
            if q and not hit and q not in norm_text(f"{label} {cwd} {s['id']} {' '.join(tags)}"):
                note_hit = note_snippet(note, q) if note else None
                if not note_hit:
                    continue
            self.shown += 1
            snippet = None
            if hit:
                prefix = {"user": "Toi : ", "assistant": "Claude : "}.get(hit["role"], "")
                snippet = esc(prefix) + highlight_words(" ".join(hit["snippet"].split()), query)
            elif note_hit:
                snippet = esc("Note : ") + highlight_words(note_hit, query)
            source = {"cursor": ["Cursor"], "claude-cloud": ["claude.ai", "cloud"]}.get(s.get("source"))
            unsent = s["id"] in self.unsent  # pas de tags ni d'archive : elle n'existe pas encore pour claude
            missing = s.get("cwd_exists") is False
            row = SessionRow(
                s, host, label, cwd, show_host=len(hosts) > 1 or host not in hosts, tags=tags,
                on_tag=self.filter_tag, missing=missing, archived=archived, source=source,
                snippet=snippet, on_archive=None if unsent else lambda r: self.toggle_archive(r.session),
                on_tags=None if unsent else lambda r: self.edit_tags(r, r.session, r.host),
                pinned=s["id"] in self.settings["pinned"],
                on_pin=None if unsent else lambda r: self.toggle_pin(r.session["id"]),
                note=note_preview(note) if note else None)
            if missing:
                row.set_tooltip_text(f"Le dossier {s['cwd']} n'existe plus sur {host}")
            if host in hosts:
                self.state_rows[s["id"]] = row
            row.paint(self.list_state(s["id"], s.get("live") if host in hosts else None))
            self.rows[s["id"]] = row
            self.listbox.append(row)
            if s["id"] == current:
                self.listbox.select_row(row)
        self.schedule_states()
        self.schedule_more_check()

    def day_header(self, row, before):
        """Séparateur « Épinglées » en tête, puis « Aujourd'hui », « Hier », « Samedi 31 octobre »
        quand le jour change."""
        ts = row.session.get("mtime", 0)
        if row.pinned:
            if before is not None and before.pinned:
                return row.set_header(None)
            text = "ÉPINGLÉES"
        elif (before is not None and not before.pinned
              and local_date(before.session.get("mtime", 0)) == local_date(ts)):
            return row.set_header(None)
        else:
            text = day_label(ts).upper()
        header = row.get_header()
        if header is None or header.get_label() != text:
            row.set_header(Gtk.Label(label=text, xalign=0, css_classes=["day-header"]))

    def row_visible(self, row):
        """Filtre « À traiter » / « En cours » ; la conversation affichée reste visible."""
        if self.segment == "all" or (self.current and row.session["id"] == self.current["id"]):
            return True
        return row.kind in (("unread", "action") if self.segment == "todo" else ("busy",))

    def on_segment(self, btn, key):
        if btn.get_active() and self.segment != key:
            self.segment = key
            self.listbox.invalidate_filter()
            self.schedule_more_check()

    def paint_row(self, row, status):
        if row.status != status:
            row.paint(status)
            self.schedule_states()

    def repaint(self, sid):
        """Met à jour l'état de sid dans la liste (et l'accueil)."""
        row = self.rows.get(sid)
        if row is None:
            return self.schedule_states()
        live = (next((x.get("live") for x in self.sessions if x["id"] == sid), None)
                if sid in self.state_rows else None)
        self.paint_row(row, self.list_state(sid, live))

    def schedule_states(self):
        """Un état a changé : compteurs des filtres, filtre de la liste et accueil, une fois
        pour toutes les lignes repeintes."""
        if not self.states_pending:
            self.states_pending = True
            GLib.idle_add(self.states_changed)

    def states_changed(self):
        self.states_pending = False
        rows = [r for r in self.rows.values() if r.get_parent() is self.listbox]
        set_count(self.seg_counts["todo"], sum(r.kind in ("unread", "action") for r in rows))
        set_count(self.seg_counts["busy"], sum(r.kind == "busy" for r in rows))
        if self.segment != "all":
            self.listbox.invalidate_filter()
        self.update_home()
        self.launcher.set_count(sum(kind in ("action", "unread") for _s, kind in self.attention()))
        return False

    def attention(self):
        """[(conversation, état)] de celles qui demandent l'attention ou travaillent, sur les
        hôtes cochés, hors archives (accueil, compteur de l'icône)."""
        out, shown = [], self.shown_hosts()
        for s in self.sessions:
            if (s["host"] not in shown or not self.source_shown(s) or self.is_archived(s)
                    or s["id"] in self.unsent):
                continue
            kind = status_kind(self.list_state(s["id"], s.get("live")))
            if kind in ("busy", "action", "unread"):
                out.append((s, kind))
        return out

    # -- épingles et vues enregistrées --
    def toggle_pin(self, sid):
        """Épingle la conversation en tête de liste, ou l'en retire."""
        pinned = self.settings["pinned"]
        (pinned.discard if sid in pinned else pinned.add)(sid)
        save_settings(self.settings)
        row = self.rows.get(sid)
        if row:
            row.set_pinned(sid in pinned)
            self.listbox.invalidate_sort()
            self.listbox.invalidate_headers()
            self.reveal_row(row)

    def update_views(self):
        """Pastilles des vues (après la frappe : un clic sur une pastille change la recherche)."""
        if not self.views_pending:
            self.views_pending = True

            def later():
                self.views_pending = False
                self.views_bar.update(self.settings["views"], self.search.get_text())
                return False
            GLib.idle_add(later)

    def apply_view(self, query):
        self.search.set_text(query)
        self.search.set_position(-1)

    def save_view(self, query):
        self.settings["views"] = add_view(self.settings["views"], query)
        save_settings(self.settings)
        self.update_views()

    def remove_view(self, query):
        self.settings["views"] = remove_view(self.settings["views"], query)
        save_settings(self.settings)
        self.update_views()

    # -- navigation au clavier --
    def list_rows(self):
        """Lignes de la liste dans l'ordre affiché (filtrées comprises)."""
        out, i = [], 0
        while (row := self.listbox.get_row_at_index(i)) is not None:
            out.append(row)
            i += 1
        return out

    def step_row(self, delta, todo=False):
        """Alt+↑/↓ : conversation précédente ou suivante de la liste ; todo (Alt+Maj+↑/↓) : la
        précédente ou la suivante à traiter (réponse non lue, demande), en bouclant."""
        rows = self.list_rows()
        sid = self.current["id"] if self.current else None
        at = next((i for i, r in enumerate(rows) if r.session["id"] == sid), None)
        if todo:  # dans toute la liste, quel que soit le filtre : la conversation ouverte y reste
            if at is None:
                order = rows if delta > 0 else rows[::-1]
            elif delta > 0:
                order = rows[at + 1:] + rows[:at]
            else:
                order = rows[:at][::-1] + rows[at + 1:][::-1]
            target = next((r for r in order if r.kind in ("unread", "action")), None)
            if target is None:
                self.list_status.set_text("Rien d'autre à traiter.")
                GLib.timeout_add_seconds(3, lambda: self.show_list_status())
                return
        else:
            visible = [r for r in rows if r.get_child_visible()]
            i = next((k for k, r in enumerate(visible) if r.session["id"] == sid), None)
            if i is None:
                i = -1 if delta > 0 else len(visible)
            if not 0 <= i + delta < len(visible):
                return
            target = visible[i + delta]
        self.listbox.select_row(target)
        self.on_row(self.listbox, target)
        self.reveal_row(target)

    def reveal_row(self, row):
        """Fait défiler la liste jusqu'à row (sans y mettre le focus, qui reste à la saisie)."""
        def go():
            ok, rect = row.compute_bounds(self.listbox)
            adj = self.list_scroll.get_vadjustment()
            if ok:
                top, bottom = rect.get_y() - 30, rect.get_y() + rect.get_height()  # 30 : séparateur
                if top < adj.get_value():
                    adj.set_value(max(0, top))
                elif bottom > adj.get_value() + adj.get_page_size():
                    adj.set_value(bottom - adj.get_page_size())
            return False
        GLib.idle_add(go)

    def show_switcher(self):
        """Ctrl+K : sélecteur rapide des conversations."""
        if self.switcher is None:
            self.switcher = Switcher(self.switcher_entries, self.open_sid)
            self.switcher.set_parent(self.chat_paned)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = self.chat_paned.get_width() // 2, 40, 1, 1
        self.switcher.set_pointing_to(rect)
        self.switcher.present()

    def switcher_entries(self):
        """Conversations proposées par Ctrl+K : celles de la liste (hôtes et sources cochés)."""
        out, several = [], len(self.shown_hosts()) > 1
        show_archived = self.show_archived.get_active()
        for s in self.sessions:
            if not self.source_shown(s) or (self.is_archived(s) and not show_archived):
                continue
            sid = s["id"]
            out.append({"sid": sid, "title": title_of(s), "folder": self.dir_of(s) or s.get("cwd") or "",
                        "host": s["host"], "tags": self.tag_store.tags(sid), "mtime": s.get("mtime", 0),
                        "pinned": sid in self.settings["pinned"], "show_host": several,
                        "status": self.list_state(sid, s.get("live"))})
        return out

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
        row = self.rows.get(s["id"])
        if row:
            tag_chips(row.tag_box, tags, self.filter_tag)
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

    def toggle_archive(self, s):
        st = self.settings
        if self.is_archived(s):
            st["archived"].discard(s["id"])
            if s.get("cursor_archived") or s.get("cloud_archived"):
                st["unarchived"].add(s["id"])
        else:
            st["unarchived"].discard(s["id"])
            st["archived"].add(s["id"])
        save_settings(st)
        archived = self.is_archived(s)
        row = self.shown_row(s["id"])
        if row and archived and not self.show_archived.get_active():
            self.listbox.remove(row)  # sur place : la liste garde sa position
        elif row:
            row.set_archived(archived)
        self.update_list_status()
        self.schedule_states()

    def update_list_status(self):
        """« 408 conversations · 79 archivées » (« 30 conversations chargées » tant que la suite
        reste à lire) ; le nombre d'archivées aussi dans le menu des hôtes."""
        edge = self.list_edge()  # comme la liste : sans celles qui attendent leur page
        shown = [s for s in self.sessions if self.source_shown(s) and s["id"] not in self.unsent
                 and not self.below_edge(s, edge)]
        n_archived = sum(1 for s in shown if self.is_archived(s))
        n = len(shown) - (0 if self.show_archived.get_active() else n_archived)
        self.archived_count.set_text(str(n_archived) if n_archived else "")
        if not (self.show_claude.get_active() or self.show_cursor.get_active()):
            return self.list_status.set_text("Aucune source cochée : Claude Code ou Cursor, dans le menu des hôtes.")
        text = f"{n} conversation{'s' * (n > 1)}"
        if edge is not None:
            text += f" chargée{'s' * (n > 1)}"
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
            cur_was = (self.current or {}).get("live")  # la boucle peut changer le même dict
            live = {}
            for res in polled.values():
                live.update(res)

            def fresh(s):
                """Vrai si le relevé a réussi pour la source et l'hôte de s : une panne
                passagère ne doit pas faire croire qu'une réponse est finie."""
                if s.get("source") == "cursor" and not s.get("cursor_cloud"):
                    return "cursor" in polled
                return s.get("host") in polled

            edge, entered = self.list_edge(), False
            for s in list(self.sessions):  # touch() retrie la liste
                if not fresh(s):
                    continue
                held = edge is not None and s["id"] not in self.rows and self.below_edge(s, edge)
                was, s["live"] = s.get("live"), live.get(s["id"])
                # sous le bas de la liste (list_edge), elle attendait sa page : un claude la reprend
                entered = entered or (held and not self.below_edge(s, edge))
                self.set_live_since(s, live.get(s["id"] + ":since"))
                if s["live"] not in ACTION_STATES and s["id"] not in self.streams:
                    self.drop_remote_requests(s)
                updated = iso_ts(live.get(s["id"] + ":updated"))
                if updated:  # session cloud : date de sa dernière activité, prise à l'envoi ou à l'arrêt
                    if s["live"] != "busy" or was != "busy":
                        self.touch(s["id"], updated)
                elif "busy" in (was, s["live"]) and was != s["live"] and s["id"] not in self.streams:
                    self.touch(s["id"])  # message envoyé ou réponse finie dans un terminal ou Cursor
                self.follow_turn(s)  # fini dans un terminal ou Cursor : notification
                # les claude -p lancés d'ici sont notifiés par on_finished (relevé fait avant leur fin compris)
                s["was_busy"] = (s["live"] == "busy" and s["id"] not in self.streams
                                 and time.monotonic() - self.hub_done.get(s["id"], -60) > 30)
                if s["id"] in self.state_rows:
                    # un claude -p lancé d'ici tourne aussi en arrière-plan
                    self.paint_row(self.state_rows[s["id"]], self.list_state(s["id"], s["live"]))
            if entered:
                self.fill_list()
            self.follow_orphans(live, polled)
            today = local_date(time.time())
            if today != self.list_day:  # minuit : « Aujourd'hui » devient « Hier »
                self.list_day = today
                self.listbox.invalidate_headers()
            self.schedule_states()  # accueil : états des conversations des autres hôtes (vue par tag)
            cur = self.current
            if cur and not cur.get("new") and cur["host"] in shown and fresh(cur):
                cur["live"] = live.get(cur["id"])
                self.set_live_since(cur, live.get(cur["id"] + ":since"))
                updated = live.get(cur["id"] + ":updated")
                if cur.get("source") == "claude-cloud" and updated and updated != cur.get("updated"):
                    cur["updated"] = updated
                    self.load_history()  # la session cloud a avancé (relit aussi ses demandes)
                else:
                    self.follow_requests(cur, cur_was)
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
        return "unread" if sid in self.settings["unread"] and live not in ACTION_STATES else live

    def touch(self, sid, when=None):
        """Un message vient de partir ou d'arriver : la conversation remonte dans la liste."""
        when = when or time.time()
        row = self.rows.get(sid)
        sessions = [s for s in self.sessions if s["id"] == sid]
        if row and row.session not in sessions:
            sessions.append(row.session)  # conversation d'un autre hôte (vue par tag)
        if not any(s.get("mtime", 0) < when for s in sessions):
            return
        edge = self.list_edge() if row is None else None
        waiting = edge is not None and any(self.below_edge(s, edge) for s in sessions)
        for s in sessions:
            s["mtime"] = max(s.get("mtime", 0), when)
        self.sessions.sort(key=lambda s: -s["mtime"])
        if row:
            row.update_time()
            row.changed()  # le tri de la liste la replace
        elif waiting:
            self.fill_list()  # elle attendait sa page sous le bas de la liste : elle y entre

    # -- claude -p lancés d'ici avant un redémarrage (orphelins) --
    def mark_launched(self, sid, host):
        """Retient le claude -p lancé d'ici pour sid, jusqu'à sa fin (on_finished)."""
        if self.settings["launched"].get(sid) != host:
            self.settings["launched"][sid] = host
            save_settings(self.settings)

    def unmark_launched(self, sid):
        if self.settings["launched"].pop(sid, None) is not None:
            save_settings(self.settings)

    def orphan(self, s):
        """Vrai si s a encore le claude -p qu'un Claude Hub précédent avait lancé (fermé ou
        planté depuis) : il finit sa réponse, mais plus personne ne lit sa sortie ni n'écrit
        sur son entrée. Par SSH, il ne s'arrête même pas une fois la réponse finie (la fin de
        son entrée ne lui parvient pas) : follow_orphans s'en charge."""
        return (bool(s) and s.get("source") is None and s["id"] in self.settings["launched"]
                and s["id"] not in self.streams and bool(s.get("live")))

    def follow_orphans(self, live, polled):
        """Après un relevé des états : oublie les claude -p lancés d'ici qui se sont arrêtés,
        et arrête ceux d'avant un redémarrage dont la réponse est finie (check_orphan)."""
        for sid, host in list(self.settings["launched"].items()):
            if sid in self.streams or host not in polled:
                continue
            if live.get(sid) is None:
                self.orphan_ended(sid)
            else:
                self.check_orphan(sid, host)

    def check_orphan(self, sid, host):
        """Relit l'orphelin sid ; s'il a fini sa réponse et n'attend plus rien en arrière-plan,
        l'arrête (rien n'est perdu) : la conversation redevient normale."""
        if sid in self.orphan_checks:
            return
        self.orphan_checks.add(sid)

        def work():
            res = run_helper(host, "stat", sid)
            if res.get("finished"):
                res["stopped"] = run_helper(host, "stop", sid).get("stopped")
            return res

        def done(res):
            self.orphan_checks.discard(sid)
            if sid in self.streams:
                return
            if res.get("live") and not res.get("headless"):
                self.unmark_launched(sid)  # reprise dans un terminal : ce n'est plus le nôtre
            elif res.get("stopped") or not res.get("live"):
                self.orphan_ended(sid)

        def fail(_e):
            self.orphan_checks.discard(sid)
        in_thread(work, done, fail)

    def stop_orphan(self, s):
        """« Arrêter » sur la ligne de l'orphelin : arrêté même au milieu de sa réponse (ce
        qu'il a déjà écrit reste dans la conversation)."""
        sid, host = s["id"], s["host"]
        self.status.set_text("Arrêt du Claude lancé avant le redémarrage…")

        def work():
            if run_helper(host, "stop", sid).get("stopped"):
                return True
            return not run_helper(host, "stat", sid).get("live")  # déjà arrêté

        def done(stopped):
            shown = self.current is not None and self.current["id"] == sid
            if stopped:
                self.orphan_ended(sid)
                if shown and not self.proc:
                    self.status.set_text("Claude arrêté.")
            elif shown:
                self.status.set_text("Ce Claude ne s'est pas arrêté.")

        def fail(e):
            if self.current is not None and self.current["id"] == sid:
                self.status.set_text(f"Arrêt impossible : {e}")
        in_thread(work, done, fail)

    def orphan_ended(self, sid):
        """L'orphelin de sid s'est arrêté : la conversation redevient normale, et le premier
        message en file part (send_queued)."""
        if sid in self.streams:  # un nouveau claude -p d'ici a déjà pris le relais
            return
        self.unmark_launched(sid)
        found =[s for s in self.sessions if s["id"] == sid]
        cur = self.current if self.current is not None and self.current["id"] == sid else None
        for s in found + ([cur] if cur is not None else []):
            s["live"] = None
        self.refresh_state(sid)
        if cur is not None:
            self.update_status()
        target = cur if cur is not None else found[0] if found else None
        if target is not None and self.queues.get(sid):
            self.send_queued(target)

    def follow_turn(self, s):
        """Fin d'un tour dans un terminal ou Cursor, d'après l'état relevé (s["live"]) et le
        précédent (s["was_busy"]). Tant que des shells ou des monitors tournent (session
        « shell »), Claude n'a pas fini : chaque événement d'un monitor relance un tour, qui
        ne doit pas notifier. La notification attend le repos (« idle »), sauf si on a écrit
        à la session d'ici pendant qu'elle attendait : sa réponse est notifiée."""
        sid, live = s["id"], s["live"]
        if s.get("was_busy") and live != "busy":
            asked = sid in self.asked
            self.asked.discard(sid)
            if live == "shell":
                self.held.add(sid)  # la fin des shells et monitors sera notifiée
                if not asked:
                    return
            elif live not in ACTION_STATES:
                self.held.discard(sid)
            self.reply_done(s, waiting=live in ACTION_STATES, replied=live == "shell")
        elif sid in self.held and live in ("idle", None):
            # shells et monitors finis ; le dernier tour, court, a pu échapper au relevé
            self.held.discard(sid)
            if live:  # claude quitté depuis le terminal : rien à signaler
                self.reply_done(s)
        if live in ("idle", None):
            self.asked.discard(sid)

    def reply_done(self, s, text=None, error=False, waiting=False, replied=False):
        """Claude a fini de répondre dans s : notifie, sauf si on la regarde déjà.
        replied : il a répondu, mais des tâches de fond tournent encore."""
        sid = s["id"]
        if text and not error and not waiting:  # aperçu de la carte « Nouvelle réponse » de l'accueil
            self.excerpts[sid] = (time.time(), text)
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
        elif replied:
            head = "Claude a répondu"
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
        self.repaint(sid)

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
                ("Désépingler" if sid in self.settings["pinned"] else "Épingler", "row-pin"),
                ("Tags…", "row-tags"),
                ("Note…" if self.note_text(row.host, sid) else "Ajouter une note…", "row-notes"),
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
        if row := self.shown_row(sid):  # popover accroché à la ligne
            self.edit_tags(row, row.session, row.host)

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

    def session_for(self, sid):
        """Conversation sid : celle affichée, sinon celle de sa ligne dans la liste."""
        if self.current and self.current["id"] == sid:
            return self.current
        row = self.shown_row(sid)
        return dict(row.session, host=row.host) if row else None

    def row_dir(self, sid):
        if (s := self.session_for(sid)) and (d := self.dir_of(s)):
            self.apply_token(dir_token(d), "dir:")

    def row_archive(self, sid):
        row = self.shown_row(sid)
        s = row.session if row else self.session_for(sid)
        if s:
            self.toggle_archive(s)

    def copy_id(self, sid):
        self.get_clipboard().set(sid)
        self.status.set_text(f"Identifiant copié : {sid}")

    def open_web(self, sid):
        Gtk.UriLauncher.new(f"https://claude.ai/code/{sid}").launch(self, None, None)

    def head_menu(self, btn):
        """Menu ⋮ de l'en-tête : actions sur la conversation affichée (liste simple, cf. on_row_menu)."""
        s = self.current
        if not s:
            return
        sid, items = s["id"], []
        if sid not in self.unsent:
            items.append(("Marquer comme lue" if sid in self.settings["unread"] else "Marquer comme non lue",
                          "toggle-unread"))
            items.append(("Désépingler" if sid in self.settings["pinned"] else "Épingler", "row-pin"))
        if d := self.dir_of(s):
            items.append(("Ne plus filtrer sur ce dossier" if d in parse_query(self.search.get_text())[1]
                          else "Filtrer sur ce dossier", "row-dir"))
        if sid not in self.unsent and not s.get("new"):
            items.append(("Désarchiver" if self.is_archived(s) else "Archiver", "row-archive"))
        items.append(("Copier l'identifiant", "copy-id"))
        if s.get("source") == "claude-cloud":
            items.append(("Ouvrir sur claude.ai", "open-web"))
        menu = Gio.Menu()
        for label, action in items:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"win.{action}", GLib.Variant.new_string(sid))
            menu.append_item(item)
        btn.set_menu_model(menu)

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
        self.stash_side()
        self.current = s
        self.kept_unread = None
        self.set_page("chat")
        self.restore_input(s["id"])
        self.show_mode(s)
        self.mark_read(s["id"])
        self.show_defaults()
        self.new_here_btn.set_visible(self.here_dir(s) is not None)
        self.tag_btn.set_visible(True)
        self.more_btn.set_visible(True)
        self.rename_btn.set_visible(can_rename(s))
        self.show_note_btn(s)
        tag_chips(self.head_tags, self.tag_store.tags(s["id"]), self.filter_tag)
        self.size = -1
        agent = self.restore_side(s)  # le volet tel que cette conversation l'a laissé
        if agent:
            s["agents_want"] = agent  # rouvert par show_agents, dès la liste lue s'il le faut
        self.show_agents(s)
        self.edits_gen += 1  # une lecture en cours concerne la conversation qu'on quitte
        self.changes_btn.set_visible(False)
        self.title.set_text(title_of(s))
        self.show_subtitle(s)
        self.clear()
        if s.get("new"):
            if not s.pop("starting", False):
                self.notice("Nouvelle conversation : écris le premier message.")
            self.show_shells(s)
        else:
            self.load_history()
        self.activity_panel.show([])  # les lignes d'une autre conversation ne doivent pas rester
        self.show_todos(s.get("todos"))
        self.update_send_btn()
        self.show_queue()
        self.update_status()
        row = self.rows.get(s["id"])
        if row is not None and self.listbox.get_selected_row() is not row:
            self.listbox.select_row(row)
        if self.segment != "all":
            self.listbox.invalidate_filter()  # la conversation affichée reste dans la liste filtrée
        self.input.grab_focus()

    def set_page(self, page):
        """« home » (accueil, saisie d'une nouvelle conversation) ou « chat »."""
        home = page == "home"
        self.pages.set_visible_child_name(page)
        self.new_line.set_visible(home)
        self.start_btn.set_visible(home)
        self.send_box.set_visible(not home)
        self.show_placeholder()
        (self.new_btn.add_css_class if home else self.new_btn.remove_css_class)("active")

    def go_home(self, host=None, cwd=None):
        """Accueil : ce qui attend une action, ce qui tourne, et la saisie d'une nouvelle
        conversation (sur host, dans cwd, s'ils sont donnés)."""
        self.cancel_fork()
        self.stash_input()
        self.stash_side()
        self.current = None
        self.kept_unread = None
        self.listbox.unselect_all()
        if self.segment != "all":
            self.listbox.invalidate_filter()
        for page in SIDE_PAGES:
            if self.side_stack.get_child_by_name(page).get_visible():
                self.close_side(page)
        if host in self.new_hosts:
            self.syncing_new = True
            self.new_host_btn.set_selected(self.new_hosts.index(host))
            self.syncing_new = False
            self.dir_btn.set_value(cwd or "~")
        self.set_page("home")
        self.restore_input(HOME)
        self.show_mode(None)
        self.show_defaults()
        self.activity_panel.show([])
        self.show_todos(None)
        self.show_queue()
        self.update_send_btn()
        self.update_status()
        self.update_home()
        self.input.grab_focus()

    def update_home(self):
        """Contenu de l'accueil : conversations à traiter, puis en cours."""
        if self.current is not None:
            return
        todo, busy = [], []
        for s, kind in self.attention():
            entry = {"sid": s["id"], "host": s["host"], "title": title_of(s),
                     "cwd": self.dir_of(s) or s.get("cwd") or "", "ts": s.get("mtime", 0)}
            if kind == "busy":
                entry["text"] = ("Claude travaille…" if s["id"] in self.streams
                                 else "Cursor génère…" if s.get("source") == "cursor"
                                 else "Claude travaille (dans un terminal)…")
                busy.append(entry)
            elif kind == "action":
                todo.append(dict(entry, **self.request_entry(s)))
            else:
                todo.append(dict(entry, kind="unread", text=self.excerpt(s)))
        self.home.update(todo, busy)

    def request_entry(self, s):
        """Carte d'une conversation qui attend : la demande en attente (claude lancé d'ici),
        sinon une action à faire là où tourne claude (terminal, claude.ai)."""
        reqs = self.pending.get(s["id"])
        if not reqs:
            if s.get("source") == "claude-cloud":
                return {"kind": "action", "text": "Claude attend une réponse sur claude.ai."}
            if s.get("waiting_for") == PERMISSION:  # lu par « stat » (conversation affichée)
                return {"kind": "action", "text": "Claude demande une autorisation dans son terminal."}
            return {"kind": "action", "text": "Claude attend une réponse dans son terminal."}
        rid, req = next(iter(reqs.items()))
        tool, inp = req.get("tool_name"), req.get("input") or {}
        if tool == "ExitPlanMode":
            return {"kind": "plan", "rid": rid, "plan": plan_digest(inp.get("plan"))}
        if tool == "AskUserQuestion":
            qs = [q for q in inp.get("questions") or [] if isinstance(q, dict)]
            return {"kind": "question", "text": str(qs[0].get("question") or "") if qs else ""}
        return {"kind": "permission", "text": f"{req.get('display_name') or tool} · {tool_detail(inp)}"}

    def excerpt(self, s):
        """Début de la dernière réponse de s, lu sur l'hôte la première fois (None en attendant)."""
        sid, mtime = s["id"], s.get("mtime", 0)
        known = self.excerpts.get(sid)
        if (known is None or known[0] < mtime) and sid not in self.excerpt_loading:
            self.excerpt_loading.add(sid)

            def done(res):
                self.excerpt_loading.discard(sid)
                self.excerpts[sid] = (mtime, last_reply(res.get("items")))
                self.schedule_states()

            def fail(_e):
                self.excerpt_loading.discard(sid)
                self.excerpts[sid] = (mtime, None)
            in_thread(lambda: self.fetch_history(s, 8), done, fail)
        return known[1] if known else None

    def open_sid(self, sid):
        """Ouvre une conversation depuis l'accueil."""
        row = self.rows.get(sid)
        if row is not None:
            self.listbox.select_row(row)
            return self.on_row(self.listbox, row)
        s = next((x for x in self.sessions if x["id"] == sid), None)
        if s:
            self.open(dict(s))

    def plan_run_sid(self, sid, rid):
        """« Lancer » sur la carte d'un plan de l'accueil : exécution en modifications acceptées."""
        req = self.pending.get(sid, {}).get(rid)
        if req is None:
            return self.schedule_states()
        self.answer(sid, rid, *plan_allow(req, EXEC_MODES[0][0]))

    def plan_refuse_sid(self, sid, rid):
        if rid in self.pending.get(sid, {}):
            self.answer(sid, rid, *plan_deny(PLAN_REFUSAL))

    def show_subtitle(self, s):
        """hôte · modèle et effort de la dernière réponse · dossier · id."""
        while (child := self.head_host.get_first_child()):
            self.head_host.remove(child)
        self.head_host.append(host_chip(s["host"]))
        model = model_name(s["model"]) if s.get("model") else ""
        if s.get("effort"):
            model = f"{model} ({s['effort']})" if model else f"effort {s['effort']}"
        self.head_model.set_text(model)
        self.head_model.set_tooltip_text(s.get("model"))
        self.head_model.set_visible(bool(model))
        self.show_usage(s)
        cwd = self.dir_of(s) or s.get("cwd") or ""
        self.head_cwd.set_text(cwd)
        self.head_cwd.set_tooltip_text(s.get("cwd"))
        self.head_seps[0].set_visible(bool((model or self.head_usage.get_visible()) and cwd))
        sid = s["id"]
        self.head_id.set_text(f"{sid[:4]}…{sid[-4:]}" if len(sid) > 12 else sid)
        self.head_id.set_tooltip_text(f"Identifiant : {sid} (copie : menu ⋮)")

    def show_usage(self, s):
        """Contexte rempli et coût de la dernière réponse, dans l'en-tête (si s est affichée)."""
        if s is self.current or (self.current and self.current["id"] == s["id"]):
            self.head_usage.show(s.get("context"), self.settings["context_windows"],
                                 self.settings["costs"].get(s["id"]))
            self.head_usage.lead.set_visible(self.head_model.get_visible())

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
        self.show_fork_warning()
        self.update_activity()  # « Arrêter » sur la ligne de Claude ; aide de la saisie
        # /rename passe par un autre claude -p : pas pendant que celui du hub écrit
        self.rename_btn.set_sensitive(not busy)
        self.rename_btn.set_tooltip_text("Renommage possible une fois la réponse finie" if busy
                                         else "Renommer la conversation")

    def show_queue(self):
        """Messages en file de la conversation affichée, dans la liste d'activité."""
        queue = self.queues.get(self.current["id"], []) if self.current else []
        self.activity_panel.show(queued=[
            queued_spec(i, text, images, edit=lambda i=i: self.unqueue(i, edit=True),
                        remove=lambda i=i: self.unqueue(i))
            for i, (text, images) in enumerate(queue)])
        self.show_activity_side()

    def show_todos(self, todos):
        """Tâches de Claude (TodoList, ou None), dans la liste d'activité."""
        self.activity_panel.show_todos(todos)
        self.show_activity_side()

    def show_activity_side(self):
        """La liste d'activité n'apparaît au-dessus de la saisie que si elle a des lignes."""
        shown = self.activity_panel.has_content()
        if self.activity_side.get_visible() != shown:
            self.activity_side.set_visible(shown)

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
        self.feed, self.feed_start = [], 0
        self.wait_card = None

    def open_question(self, items):
        """Conversation qui attend une réponse dans son terminal, sans Remote Control : déplie
        la question de Claude (AskUserQuestion) si c'est son dernier appel, pour la lire sans
        chercher (on y répond dans le terminal). Faux si elle n'est pas dans le fil : en mode
        auto, ou dans un tour relancé par la fin d'une tâche de fond, Claude Code ne l'écrit
        qu'une fois répondue (docs/mode-plan-et-sous-agents.md)."""
        item = items[-1] if items else {}
        call = self.tool_calls.get(item.get("id")) if item.get("name") == "AskUserQuestion" else None
        if call is None or call.get_root() is not self:
            return False
        run = call.get_ancestor(ToolRun.__gtype__)
        if run is not None:
            run.reveal()  # repliée en « N étapes »
        call.expander.set_expanded(True)
        return True

    def note_stat(self, s, res):
        """Remote Control et attente du Claude de s (terminal), relus par la commande « stat »."""
        if res.get("live"):
            s["linked"], s["waiting_for"] = bool(res.get("bridge")), res.get("waiting_for")
        else:
            s.pop("linked", None)
            s.pop("waiting_for", None)

    def without_remote_control(self, s):
        """s tourne dans un terminal sans être reliée à Remote Control (selon le dernier
        « stat ») : Claude Hub ne peut ni lui écrire ni répondre à ses demandes."""
        return (bool(s) and s.get("source") is None and s["host"] != CLOUD and not s.get("new")
                and not self.is_streaming(s) and bool(s.get("live")) and s.get("linked") is False
                and not self.orphan(s))

    def wait_in_terminal(self, s, shown):
        """Carte « Claude attend ta réponse dans son terminal » à la fin du fil, quand s attend
        sans carte à laquelle répondre d'ici (shown : sa question est dépliée dans le fil) ;
        refaite d'après son état, ou retirée."""
        had = self.drop_wait_card()
        if (s is not self.current or s.get("source") is not None or s["host"] == CLOUD
                or s.get("live") not in ACTION_STATES or self.pending.get(s["id"])
                or self.orphan(s)):  # pas de terminal où répondre
            return
        self.wait_card = WaitCard(s.get("waiting_for"), s.get("linked") is not False, shown)
        self.append_item(self.wait_card)
        if not had:
            self.scroll_end()

    def drop_wait_card(self):
        """Retire la carte « Claude attend ta réponse dans son terminal » ; vrai s'il y en avait une."""
        card, self.wait_card = self.wait_card, None
        if card is not None and card.get_parent() is self.msgs:
            self.msgs.remove(card)
        return card is not None

    @staticmethod
    def can_answer_remote(s):
        """s peut avoir des demandes auxquelles répondre par l'API : conversation d'un
        terminal (si son Claude est relié à Remote Control) ou de claude.ai."""
        return s.get("source") == "claude-cloud" or (
            s.get("source") is None and s["host"] != CLOUD and not s.get("new"))

    def load_requests(self, s, items):
        """Conversation qui attend (terminal, claude.ai) : ses demandes en attente, lues dans
        l'API Remote Control, en cartes auxquelles on répond d'ici. Sinon (pas de Remote
        Control, API indisponible), la question est dépliée dans le fil si elle y est, et une
        carte dit d'aller répondre dans le terminal."""
        if not self.can_answer_remote(s):
            return self.open_question(items)
        sid = s["id"]

        def fetch():
            res = None if s.get("source") == "claude-cloud" else run_helper(s["host"], "stat", sid)
            bridge = sid if res is None else res.get("bridge")
            try:
                reqs = [(rid, dict(req, remote=bridge)) for rid, req in remote_pending(bridge)
                        ] if bridge else []
            except Exception:  # noqa: BLE001 — API indisponible : les cartes déjà là restent
                reqs = None
            return res, reqs

        def done(out):
            res, reqs = out
            if s is not self.current or self.is_streaming(s):
                return
            if res is not None:
                self.note_stat(s, res)
            if reqs is not None:
                self.show_remote_requests(s, reqs)
            if not self.pending.get(sid):
                self.wait_in_terminal(s, self.open_question(items))
            self.update_status()

        def fail(_e):  # script distant injoignable : la question reste lisible dans le fil
            if s is self.current:
                self.wait_in_terminal(s, self.open_question(items))
        in_thread(fetch, done, fail)

    def show_remote_requests(self, s, reqs):
        """Demandes en attente de s lues dans l'API : celles qui n'y sont plus (répondues
        dans le terminal) sont retirées du fil, les nouvelles y sont ajoutées."""
        sid = s["id"]
        mine = self.pending.setdefault(sid, {})
        fresh = {rid: req for rid, req in reqs if rid not in self.remote_answered}
        for rid in [r for r, req in mine.items() if req.get("remote") and r not in fresh]:
            del mine[rid]
            self.drop_cards(sid, rid)
        for rid, req in fresh.items():
            if rid not in mine:
                mine[rid] = req
                if s is self.current:
                    self.show_card(sid, rid, req)
        if not mine:
            self.pending.pop(sid, None)
        self.refresh_state(sid)

    def follow_requests(self, s, was):
        """État de la conversation affichée relevé (was : le précédent) : ses demandes lues
        dans l'API sont oubliées quand elle n'attend plus (réponse donnée dans le terminal),
        relues quand elle se met à attendre sans que son fichier change."""
        if self.is_streaming(s) or self.size == -1:
            return
        if s.get("live") not in ACTION_STATES:
            self.drop_remote_requests(s)
        elif was not in ACTION_STATES:
            self.load_requests(s, s.get("items") or [])

    def drop_remote_requests(self, s):
        """s n'attend plus rien (selon son hôte ou claude.ai) : oublie ses demandes lues
        dans l'API, et retire leurs cartes (et celle qui disait de répondre dans le terminal)."""
        if s is self.current:
            self.drop_wait_card()
        mine = self.pending.get(s["id"])
        if mine and any(req.get("remote") for req in mine.values()):
            self.show_remote_requests(s, [])

    def load_history(self, full=False):
        """Relit l'historique de la conversation affichée. Le fil n'est reconstruit qu'à
        l'ouverture (ou si full) ; ensuite, seuls les éléments nouveaux ou changés y
        entrent (update_feed), sans toucher au reste ni au défilement."""
        s = self.current
        self.history_gen += 1
        gen = self.history_gen

        def done(res):
            # une lecture partie avant la dernière affichée ne revient pas en arrière
            if s is not self.current or self.is_streaming(s) or gen < self.history_shown:
                return
            self.history_shown = gen
            self.size = res["size"]
            if "context" in res:
                s["context"] = res["context"]
            if res.get("model"):
                s["model"], s["effort"] = res["model"], res.get("effort")
            self.show_subtitle(s)
            if "agents" in res:
                s["agents"] = res["agents"]
                s["workflow_runs"] = res.get("workflow_runs") or []  # nom et résumé des workflows
                self.show_agents(s)
            self.forget_read(s["id"], res["items"])
            self.drop_stale_suggestion(s, res["items"])
            if full or not self.update_feed(s, res["items"], res["total"] - len(res["items"])):
                self.rebuild_feed(s, res)
            s["items"] = res["items"]
            self.refresh_edits(s, res["items"])
            # Le script distant rejoue toute la transcription ; Cursor et le cloud, l'historique lu.
            s["todos"] = (TodoList.from_events(res["todos"]) if "todos" in res
                          else TodoList.from_items(res["items"]))
            self.show_todos(s["todos"])
            if s.get("live") in ACTION_STATES:
                self.load_requests(s, res["items"])
            else:
                self.drop_remote_requests(s)
            self.sync_plan_cards()

        def fail(e):
            self.error(f"Lecture impossible : {e}")
        limit = SEARCH_HISTORY_LIMIT if s.get("search") else HISTORY_LIMIT
        in_thread(lambda: self.fetch_history(s, limit), done, fail)

    @staticmethod
    def fetch_history(s, limit):
        """Historique de s (dans un thread) : ses `limit` derniers éléments."""
        if s.get("source") == "claude-cloud":
            return cloud_history(s["id"], limit)
        if s.get("cursor_cloud"):
            return cursor_cloud_history(s.get("body"), limit)
        if s.get("source") == "cursor":
            return cursor_history(s["id"], limit)
        return run_helper(s["host"], "history", s["id"], limit)

    def rebuild_feed(self, s, res):
        """Fil reconstruit d'après l'historique res (ouverture de la conversation), puis
        défilé jusqu'au passage cherché, sinon jusqu'à la fin."""
        self.clear()
        items = res["items"]
        if res["total"] > len(items):
            self.notice(f"… {res['total'] - len(items)} éléments plus anciens masqués")
        self.feed_of, self.feed_start = s, res["total"] - len(items)
        target = target_run = None
        words = norm_text(s.get("search") or "").split()
        for item in items:
            w = self.item_widget(item, s)
            run = self.add_entry(w, item)
            inp = item.get("input") if isinstance(item.get("input"), dict) else {}
            text = item.get("text") or " ".join(v for v in inp.values() if isinstance(v, str))
            if words and target is None and any(x in norm_text(text) for x in words):
                target, target_run = w, run
        self.end_feed(s)
        if target_run is not None:
            target_run.reveal()
        self.show_pending(s["id"])
        self.show_injected(s)
        if target is not None:
            s["search"] = None  # seulement au premier chargement
            target.add_css_class("hit")
            self.scroll_to(target)
        else:
            self.scroll_end()

    def update_feed(self, s, items, start):
        """Fil mis à jour d'après une nouvelle lecture de l'historique (items, le premier à
        l'index start de la transcription), sans le reconstruire : les éléments inchangés
        restent tels quels (dépliés, sélection, défilement) ; ce qui suit le dernier d'entre
        eux (éléments changés, réponse reçue en direct, avis) laisse la place aux éléments
        relus. Les cartes des demandes encore en attente (et celle qui dit de répondre dans
        le terminal) et les commandes « ! » pas encore envoyées restent à la fin. Faux s'il
        faut tout reconstruire."""
        if self.feed_of is not s or not self.feed or self.feed[-1][1].get_parent() is not self.msgs:
            return False  # fil d'une autre conversation (copie, suite d'une conversation Cursor)
        at = changed_from([it for it, _ in self.feed], self.feed_start, items, start)
        if at is None:
            return False
        k, new = at - self.feed_start, items[at - start:]
        if k == len(self.feed) and not new:
            self.end_feed(s)
            return True
        top = self.feed[min(k, len(self.feed) - 1)][1]
        run = [i for i, (_, t) in enumerate(self.feed) if t is top]
        # Suite d'étapes refaite entière : un de ses appels a changé, elle a reçu des étapes
        # du flux de claude -p, ou d'autres étapes la prolongent alors qu'elle est fermée.
        if isinstance(top, ToolRun) and (k < len(self.feed) or len(top.items) != len(run)
                                         or (not top.open and is_step(new[0]))):
            k = run[0]
        first = top if k < len(self.feed) else top.get_next_sibling()
        redo = [it for it, _ in self.feed[k:at - self.feed_start]] + new
        pending, cards = self.pending.get(s["id"]) or {}, []
        while first is not None:
            nxt = first.get_next_sibling()
            self.msgs.remove(first)
            if (isinstance(first, RequestCard) and first.request_id in pending) or first is self.wait_card:
                cards.append(first)
            first = nxt
        while (last := self.msgs.get_last_child()) is not None and last.has_css_class("feed-day"):
            self.msgs.remove(last)  # le jour des éléments retirés : remis s'il le faut
        del self.feed[k:]
        ids = {it.get("id") for it, _ in self.feed}  # appels retirés (et ceux du flux) oubliés
        self.tool_calls = {t: c for t, c in self.tool_calls.items() if t in ids}
        self.agent_blocks = {t: b for t, b in self.agent_blocks.items() if t in ids}
        for item in redo:
            self.add_entry(self.item_widget(item, s), item)
        self.end_feed(s)
        for card in cards:
            self.msgs.append(card)
        self.show_injected(s)
        return True

    def add_entry(self, widget, item):
        """Élément de l'historique ajouté au fil (append_item), retenu dans self.feed."""
        run = self.append_item(widget, item)
        self.feed.append((item, self.msgs.get_last_child()))
        return run

    def end_feed(self, s):
        """Fin du fil après une lecture de l'historique : dernière suite d'étapes repliée si
        Claude ne travaille plus, commandes « ! » pas encore envoyées remises à la fin."""
        last = self.msgs.get_last_child()
        if isinstance(last, ToolRun) and s.get("live") != "busy":
            last.close()  # étapes en cours (terminal) : laissées dépliées
        self.show_shells(s)

    def append_item(self, widget, item=None):
        """Ajoute un élément au fil, avec son heure à droite (« ts » de item), précédé
        d'un séparateur quand le jour change. Les appels d'outils consécutifs forment une
        suite (ToolRun), repliée en « N étapes » quand un autre élément la ferme ; renvoie
        la suite qui reçoit widget, ou None."""
        last = self.msgs.get_last_child()
        ts = (item or {}).get("ts")
        if ts and local_date(ts) != last_day(self.msgs):
            if isinstance(last, ToolRun):
                last.close()
            last = day_separator(ts)
            self.msgs.append(last)
        if is_step(item):
            if not (isinstance(last, ToolRun) and last.open):
                last = ToolRun()
                self.msgs.append(last)
            last.add(item, widget)
            self.grey_after_cut()
            return last
        if isinstance(last, ToolRun):
            last.close()
        self.msgs.append(Timed(widget, ts) if ts else widget)
        self.grey_after_cut()
        return None

    def feed_child(self, widget):
        """Élément du fil (enfant de self.msgs) qui contient widget, None s'il n'y est pas."""
        while widget is not None and widget.get_parent() is not self.msgs:
            widget = widget.get_parent()
        return widget

    def item_widget(self, item, s, live=False):
        """Widget d'un élément de l'historique (live : reçu dans le flux de claude -p).

        Un sous-agent se charge quand on le déplie, la sortie d'un outil aussi (sauf en
        direct, où elle arrive dans le flux).
        """
        if item.get("role") == "bash":  # commande « ! » (terminal ou claude-hub)
            return ShellBlock.from_item(item)
        agents = self.has_agents_files(s)  # bouton vers le volet des sous-agents
        if item.get("via") == "agent" and agents and item.get("from"):
            return with_open_button(message_widget(item), lambda: self.open_agent(item["from"]))
        if item.get("role") != "tool" or item.get("name") == "ExitPlanMode":
            return message_widget(item, self.fork_handler(s), self.edit_handler(s))
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
        call = self.tool_calls[tid] = ToolCall(item, loader, live=live)
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
            was, s["live"] = s.get("live"), res["live"]
            self.set_live_since(s, res.get("since"))
            s["workflows"] = res.get("workflows") or []  # workflows en arrière-plan pas encore finis
            s["workflows_waiting"] = bool(res.get("waiting"))  # Claude a fini son tour et les attend
            s["shells"] = res.get("shells") or []  # shells en arrière-plan qui tournent
            if s.get("source") is None:
                self.note_stat(s, res)
            self.update_status()
            if res["size"] != self.size and self.size != -1:
                self.load_history()  # relit aussi les demandes en attente
            else:
                self.follow_requests(s, was)
        if s.get("source") == "cursor":
            in_thread(lambda: cursor_stat(s["id"]), done)
        else:
            in_thread(lambda: run_helper(s["host"], "stat", s["id"]), done)
        return True

    def running_agents(self, s):
        """Sous-agents de s qui travaillent, le plus récemment actif en premier : ceux de la
        liste relue sur l'hôte, plus ceux que le flux de son claude -p vient d'annoncer."""
        if not s:
            return []
        files = s.get("agents") or [] if self.has_agents_files(s) else []
        known = {a.get("tool") for a in files}
        stream = self.streams.get(s["id"], (None, None))[1] or {}
        live = [a for a in (stream.get("live_agents") or {}).values() if a["tool"] not in known]
        return running_agents(files + live, self.agents_alive(s))

    def workflows(self, s):
        """Workflows de s en arrière-plan : relus sur l'hôte (commande « stat ») pour une
        session de terminal ; pour un claude -p lancé d'ici, dont « stat » n'est pas relu,
        déduits de la liste des sous-agents."""
        if not s:
            return []
        if s["id"] in self.streams:
            files = s.get("agents") or [] if self.has_agents_files(s) else []
            return agent_workflows(files, True, s.get("workflow_runs") or [])
        return s.get("workflows") or []

    def background_shells(self, s):
        """Shells et monitors en arrière-plan de s qui tournent : annoncés par le flux de son
        claude -p, sinon relus sur l'hôte (session de terminal, commande « stat »)."""
        if not s:
            return []
        stream = self.streams.get(s["id"], (None, None))[1]
        if stream is not None:
            return stream.get("bg_shells") or []
        return s.get("shells") or []

    def step(self, s, text, detail=None):
        """Étape en cours de la réponse de s (claude -p lancé d'ici), avec son heure de début,
        affichée au-dessus de la saisie si s est la conversation affichée."""
        if (s.get("step") or (None, None))[:2] != (text, detail):
            s["step"] = (text, detail, time.monotonic())
        if self.current and self.current["id"] == s["id"]:
            self.update_activity()

    def show_hint(self):
        """Aide de la saisie et infobulle d'« Envoyer », selon ce que deviendra le message :
        remarque sur le plan qui attend, ou message que claude lira plus tard."""
        s = self.current
        plan = s is not None and self.pending_plan(s) is not None
        self.show_placeholder()
        self.send_btn.set_tooltip_text(
            "Envoyé comme remarque sur le plan : Claude continue à planifier" if plan
            else None if not self.proc
            else "Transmis à Claude, qui le lira une fois la demande tranchée" if self.pending.get(s["id"])
            else "Transmis à Claude, qui le lira après l'étape en cours")

    def shown_suggestion(self):
        """Message prédit par claude pour la conversation affichée, s'il tient lieu d'aide
        dans la saisie vide : ni sur l'accueil, ni quand un plan attend, ni pendant que
        Claude répond (la prédiction suit la fin de la réponse)."""
        s = self.current
        if s is None or self.pages.get_visible_child_name() != "chat" or self.pending_plan(s):
            return None
        stream = self.streams.get(s["id"], (None, {}))[1]
        if stream and not stream.get("idle"):
            return None
        return (self.suggestions.get(s["id"]) or (None,))[0]

    def show_placeholder(self):
        """Aide de la saisie vide : accueil, remarque sur le plan qui attend, message
        prédit par claude (avec la touche Tab qui le reprend) ou aide habituelle."""
        tip = self.shown_suggestion()
        if self.pages.get_visible_child_name() == "home":
            text = HOME_HINT
        elif self.current is not None and self.pending_plan(self.current):
            text = PLAN_HINT
        else:
            text = " ".join(tip.split()) if tip else CHAT_HINT  # sur une ligne
        self.placeholder.set_text(text)
        self.suggest_key.set_visible(tip is not None)

    def accept_suggestion(self):
        """Tab dans la saisie vide : y met le message prédit par claude, à modifier ou à
        envoyer. Faux s'il n'y en a pas (Tab garde alors son effet habituel)."""
        tip = self.shown_suggestion()
        buf = self.input.get_buffer()
        if tip is None or buf.get_char_count() or self.attachments:
            return False
        buf.set_text(tip)
        buf.place_cursor(buf.get_end_iter())
        return True

    def got_suggestion(self, s, text):
        """Message prédit par claude après sa réponse (prompt_suggestion, --prompt-suggestions)."""
        text = (text or "").strip()
        if not text:
            return
        self.suggestions[s["id"]] = (text, time.time())
        if self.current and self.current["id"] == s["id"]:
            self.show_placeholder()

    def drop_suggestion(self, sid):
        """Prédiction dépassée : un message est parti, ou la conversation a continué ailleurs."""
        if self.suggestions.pop(sid, None) and self.current and self.current["id"] == sid:
            self.show_placeholder()

    def drop_stale_suggestion(self, s, items):
        """Historique relu : un message écrit après la prédiction (dans le terminal, par
        exemple) la rend caduque."""
        since = (self.suggestions.get(s["id"]) or (None, None))[1]
        if since is not None and any(it.get("role") in ("user", "bash") and (it.get("ts") or 0) > since
                                     for it in items):
            self.drop_suggestion(s["id"])

    def update_activity(self):
        """Ce qui tourne et ce qui attend, au-dessus de la saisie (activity.py) : Claude au
        travail (ou la demande qui attend une réponse), ses sous-agents, ses shells et
        monitors en arrière-plan. Les tâches et la file ont leurs propres mises à jour."""
        self.show_hint()
        s = self.current
        stream = self.streams.get(s["id"], (None, None))[1] if s else None
        flows = self.workflows(s)
        # les agents d'un workflow sont résumés par sa ligne
        agents = outside_workflows(self.running_agents(s), (s or {}).get("agents"))
        rows, busy = [], False
        if s and self.pending.get(s["id"]):
            rows.append(request_spec(self.request_entry(s), jump=self.show_request))
        elif self.proc:
            busy = True
            step, detail, since = stream.get("step") or ("Claude réfléchit", None, None)
            if stream.get("idle"):  # tour fini : claude attend ses sous-agents, shells ou monitors
                shells = self.background_shells(s)
                if flows or agents:
                    step, detail = waiting_for(len(flows), len(agents)), None
                elif shells:
                    step, detail = waiting_text(shells), None
            rows.append(claude_spec(step, detail, since, stop=self.stop))
        elif s and s.get("live") == "busy":  # dans un terminal ou Cursor (et ses workflows)
            busy = True
            orphan = self.orphan(s)  # claude -p lancé avant le redémarrage : on peut l'arrêter
            n = len(flows) if s.get("workflows_waiting") else 0
            text = ("Cursor génère" if s.get("source") == "cursor"
                    else waiting_for(n, len(agents)) if n
                    else "Claude travaille (lancé avant le redémarrage)" if orphan
                    else "Claude travaille (dans un terminal)")
            rows.append(claude_spec(text, None, self.busy_start(s),
                                    stop=(lambda: self.stop_orphan(s)) if orphan else None,
                                    stop_tip="Arrêter ce Claude (ce qu'il a déjà écrit est gardé)"))
        elif s and s.get("live") in ACTION_STATES:  # on y répond là où tourne claude
            rows.append(request_spec(self.request_entry(s)))
        if s and not busy:
            s.pop("busy_since", None)
        if busy:
            rows += agent_specs(agents, indent=True,
                                open_agent=lambda a: self.open_agent(a.get("id") or a.get("tool")),
                                open_panel=self.show_agents_panel)
            rows += [workflow_spec(w, indent=True, open_panel=self.show_agents_panel) for w in flows]
        rows += [shell_spec(sh) for sh in self.background_shells(s)]
        self.activity_panel.show(rows)
        self.show_activity_side()

    def show_request(self):
        """« Aller à la demande » : le fil défile jusqu'à la carte qui attend une réponse."""
        pending = self.pending.get(self.current["id"]) if self.current else None
        child = self.msgs.get_last_child()
        while child is not None:
            if isinstance(child, RequestCard) and pending and child.request_id in pending:
                return self.scroll_to(child)
            child = child.get_prev_sibling()
        self.scroll_end()

    def set_live_since(self, s, since):
        """Garde la date (ms, horloge de l'hôte) où la session de s est passée à « busy »,
        d'après son fichier ~/.claude/sessions ; oublie ses workflows quand elle a fini, et
        ses shells quand son claude est arrêté (ils meurent avec lui)."""
        if s.get("live") == "busy" and type(since) in (int, float) and since > 0:
            s["live_since"] = since
        else:
            s.pop("live_since", None)
        if s.get("live") != "busy":
            s.pop("workflows", None)
            s.pop("workflows_waiting", None)
        if not s.get("live"):
            s.pop("shells", None)

    def busy_start(self, s):
        """Début (time.monotonic) du travail de s dans un terminal : celui que note claude,
        sinon le moment où le hub l'a vu travailler."""
        since = s.get("live_since")
        if since:
            return time.monotonic() - max(0.0, time.time() - since / 1000)
        return s.setdefault("busy_since", time.monotonic())

    def update_status(self):
        s = self.current
        self.update_activity()
        if self.orphan(s):
            self.head_chip.show_orphan()
        elif self.without_remote_control(s):
            self.head_chip.show_for(s["host"])
        else:
            self.head_chip.set_visible(False)
        if self.proc:
            return
        if s is None:
            self.status.set_text("")
        elif self.composer.has_css_class("shell-mode"):
            self.status.set_text(self.shell_hint(s))
        elif s.get("cwd_exists") is False:
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
        elif self.orphan(s):
            self.status.set_text("Lancée avant le redémarrage : ton message partira quand "
                                 "son Claude aura fini.")
        elif s and s.get("live") and not s.get("new") and s.get("linked") is False:
            self.status.set_text("Ouverte dans un terminal sans Remote Control : Claude Hub te "
                                 "proposera une copie (voir « Sans Remote Control » en haut).")
        elif s and s.get("live") and not s.get("new"):
            self.status.set_text("Ouverte dans un terminal : ton message lui sera transmis via "
                                 "Remote Control (sinon, Claude Hub te proposera une copie).")
        elif self.shells.get(s["id"]):
            self.status.set_text("Entrée : envoyer, avec la sortie des commandes « ! »")
        else:
            self.status.set_text("Entrée : envoyer · Maj+Entrée : nouvelle ligne")

    def notice(self, text, css="notice"):
        self.append_item(make_label(GLib.markup_escape_text(text), css))
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
        start = self.dir_btn.value.strip() or "~"
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
        self.dir_btn.set_value(path)

    def recent_dirs(self):
        """Dossiers des conversations récentes de l'hôte d'une nouvelle conversation."""
        host, seen = self.new_host, []
        for s in self.sessions:  # les plus récentes d'abord
            d = self.dir_of(s) if s.get("host") == host and s.get("cwd_exists") is not False else None
            if d and d not in seen:
                seen.append(d)
            if len(seen) >= 12:
                break
        if "~" not in seen:
            seen.append("~")
        return f"Récents {'sur ce PC' if host == 'local' else 'sur ' + short_host(host)}", seen

    # -- envoi --
    def start_from_home(self):
        """« Démarrer » sur l'accueil : crée la conversation (hôte et dossier choisis) avec
        le message saisi."""
        text = self.input_text().strip()
        if not (text or self.attachments):
            return
        if shell_command(text) is not None:
            return self.status.set_text("Une commande « ! » se lance dans une conversation : "
                                        "démarre-la d'abord.")
        host, cwd = self.new_host, self.new_cwd()
        images = self.take_input()
        self.launch(self.start_new(host, cwd, quiet=True), text, images)

    def new_cwd(self):
        """Dossier choisi pour une nouvelle conversation, « ~ » remplacé par le dossier personnel."""
        cwd = self.dir_btn.value.strip() or "~"
        if cwd.startswith("~"):
            cwd = (self.remote_home.get(self.new_host) or str(Path.home())) + cwd[1:]
        return cwd

    def here_dir(self, s):
        """Dossier où relancer une conversation à côté de s, ou None (cloud, dossier
        supprimé, dossier distant d'une conversation Cursor)."""
        cwd = s.get("cwd")
        if (not cwd or ":" in cwd or s["host"] == CLOUD or s.get("source") == "claude-cloud"
                or s.get("cwd_exists") is False):
            return None
        return cwd

    def new_here(self):
        """Accueil, prêt pour une nouvelle conversation dans le dossier (et sur l'hôte) de
        celle affichée, s'il existe encore."""
        s = self.current
        cwd = self.here_dir(s) if s else None
        if cwd:
            self.go_home(s["host"], self.dir_of(dict(s, cwd=cwd)) or cwd)
        else:
            self.go_home()

    def start_new(self, host, cwd, quiet=False):
        """Ouvre une nouvelle conversation, visible tout de suite en tête de la liste
        (elle disparaît si on la quitte sans rien y écrire) ; quiet : son premier message
        part aussitôt (accueil), pas d'invitation à l'écrire."""
        s = {"id": str(uuid.uuid4()), "cwd": cwd, "host": host, "new": True,
             "title": None, "first": None, "mtime": time.time(), "starting": quiet}
        self.unsent[s["id"]] = s
        self.open(s)  # avant fill_list : une nouvelle conversation vide quittée disparaît
        if host in self.shown_hosts():
            self.sessions.insert(0, s)
            self.fill_list()
            self.listbox.select_row(self.rows.get(s["id"]))
        return s

    # -- brouillons : saisie non envoyée, gardée par conversation (en mémoire seulement) --
    def input_text(self):
        buf = self.input.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def has_draft(self, sid):
        if self.shells.get(sid):  # commandes « ! » en attente du prochain message
            return True
        if self.current is not None and self.current["id"] == sid:
            return bool(self.input_text().strip() or self.attachments)
        return sid in self.drafts

    def stash_input(self):
        """Garde la saisie de la conversation qu'on quitte ; une nouvelle conversation
        laissée vide disparaît de la liste."""
        s = self.current
        sid = s["id"] if s else HOME
        text = self.input_text()
        if text.strip() or self.attachments or self.shells.get(sid):
            self.drafts[sid] = (text, self.attachments)
            return
        self.drafts.pop(sid, None)
        if s and self.unsent.pop(sid, None):
            self.sessions = [x for x in self.sessions if x["id"] != s["id"]]
            row = self.rows.pop(s["id"], None)
            self.state_rows.pop(s["id"], None)
            if row:
                self.listbox.remove(row)
                self.schedule_states()

    def restore_input(self, sid):
        text, self.attachments = self.drafts.pop(sid, ("", []))
        self.input.get_buffer().set_text(text)
        self.show_attachments()

    def draft_changed(self):
        """La saisie a changé : marque « brouillon » de la conversation affichée."""
        if self.current and self.current["id"] in self.state_rows:
            self.repaint(self.current["id"])

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
        if s is None:  # accueil : là où la nouvelle conversation démarrera
            return self.new_host, self.new_cwd()
        if s.get("cursor_cloud") or s["host"] == CLOUD or s.get("source") == "claude-cloud":
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
            self.send("now" if state & PRIMARY_MASK
                      else "queue" if state & Gdk.ModifierType.ALT_MASK else "auto")
            return True
        if keyval == Gdk.KEY_ISO_Left_Tab:
            self.toggle_plan()
            return True
        if keyval == Gdk.KEY_Tab and not state & (PRIMARY_MASK | Gdk.ModifierType.ALT_MASK) \
                and self.accept_suggestion():
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
            return self.start_from_home()
        command = shell_command(text)
        if command is not None:
            return self.run_shell(s, command)
        ready = bool(self.ready_shells(s["id"]))  # commandes « ! » finies : partent avec le message
        if self.fork_at and self.fork_at["sid"] == s["id"]:
            if self.fork_at.get("edit"):
                if text or self.attachments:
                    self.launch_edit(s, text)
            elif (text or self.attachments or ready) and not self.proc:
                self.launch_fork(s, text)
            return
        # claude -p lancé avant le redémarrage : on ne peut plus lui écrire, et en lancer un
        # second mêlerait leurs réponses. Le message attend en file qu'il ait fini (il est
        # alors arrêté, et orphan_ended envoie la file) ; « tout de suite » l'arrête aussitôt.
        if self.orphan(s):
            if text or self.attachments:
                msg = (text, self.take_input())
                if mode == "now":
                    self.queues.setdefault(s["id"], []).insert(0, msg)
                    self.stop_orphan(s)
                else:
                    self.queues.setdefault(s["id"], []).append(msg)
                    self.status.set_text("En file : ton message partira quand le Claude lancé "
                                         "avant le redémarrage aura fini.")
                self.show_queue()
            return
        # Un plan attend : claude ne lirait le message qu'une fois le plan tranché. « Envoyer »
        # en fait donc la remarque de « Continuer à planifier », et Claude y répond aussitôt
        # (« tout de suite » et « en file » gardent leur sens : interrompre, attendre).
        rid = self.pending_plan(s)
        if rid and mode == "auto" and text and not self.attachments:
            mine = self.pending[s["id"]]
            remote = mine[rid].get("remote")  # terminal relié à Remote Control : réponse par l'API
            buf.set_text("")
            self.answer(s["id"], rid, *plan_deny(text))
            if remote:
                return
            if rid in mine:  # claude ne l'attend plus : le message n'est pas perdu
                del mine[rid]
                buf.set_text(text)
                self.refresh_state(s["id"])
                return self.status.set_text("Demande expirée : ton message est resté dans la saisie.")
            return self.status.set_text("Remarque transmise : Claude continue à planifier.")
        queue = self.queues.get(s["id"])
        if self.proc or queue:
            # Claude répond déjà (ou la file a été interrompue). Entrée seule avec des
            # commandes « ! » finies : elles partent seules (file en pause : avec le premier en file).
            if text or self.attachments or (ready and self.proc):
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
        if not (text or self.attachments or ready):
            if self.shells.get(s["id"]):
                self.status.set_text("La commande tourne encore : sa sortie partira une fois finie.")
            return
        if s.get("cursor_cloud"):
            return self.status.set_text("Agent cloud Cursor en lecture seule : réponds-y depuis Cursor.")
        # Conversation déjà ouverte ailleurs (terminal) ou session claude.ai : on ne lance
        # pas un second Claude, on passe par Remote Control.
        if s.get("source") == "claude-cloud" or (s.get("source") is None and not s.get("new")
                                                 and s.get("live")):
            if not (text or self.attachments):  # Remote Control ne transmet que le texte tapé
                return self.status.set_text("Conversation ouverte dans un terminal : les commandes "
                                            "« ! » ne lui sont pas transmises.")
            return self.send_via_remote(s, text)
        self.launch(s, text, self.take_input())

    def pending_plan(self, s):
        """Demande de validation d'un plan qui attend une réponse dans s (id), sinon None."""
        return next((rid for rid, req in (self.pending.get(s["id"]) or {}).items()
                     if req.get("tool_name") == "ExitPlanMode"), None)

    def fork_handler(self, s):
        """Action « copie à partir d'ici » des messages de s : conversations Claude Code
        seulement (le cloud et Cursor n'ont pas de reprise à un point donné)."""
        if s.get("source") is None and s["host"] != CLOUD and not s.get("new"):
            return self.start_fork
        return None

    def edit_handler(self, s):
        """Action « modifier ce message » des messages de s : les mêmes conversations que
        la copie (claude y reprend la conversation à un point donné)."""
        return self.start_edit if self.fork_handler(s) else None

    def start_fork(self, item, before, widget):
        """Prépare une copie de la conversation arrêtée au message de widget (ou juste
        avant, pour un message de l'utilisateur remis dans la saisie) : le prochain envoi
        la lance, l'original reste intact."""
        s = self.current
        if self.proc:
            return self.status.set_text("Copie possible une fois la réponse finie.")
        self.cancel_fork()
        widget = self.feed_child(widget)
        if widget is None:
            return
        self.fork_at = {"sid": s["id"], "at": item["parent"] if before else item["uuid"],
                        "widget": widget, "before": before,
                        "text": item.get("text") if before else None}
        self.grey_after_cut(widget if before else widget.get_next_sibling())
        if before and item.get("text"):
            self.input.get_buffer().set_text(item["text"])
        self.fork_icon.set_from_icon_name("claude-hub-fork-symbolic")
        self.fork_cancel.set_tooltip_text("Annuler la copie")
        self.fork_label.set_text("Copie de la conversation " + (
            "juste avant ce message : modifie-le puis envoie." if before else
            "arrêtée à ce message : ton prochain message partira dans une nouvelle conversation."))
        self.show_fork_warning()
        self.fork_bar.set_visible(True)
        self.input.grab_focus()

    def in_terminal(self, s):
        """Vrai si un claude tourne pour s ailleurs que dans claude-hub (terminal) : l'état
        relevé le dit, et ce n'est pas le claude -p d'ici, en cours ou fini depuis moins de
        30 s (le relevé suivant ne l'a peut-être pas encore vu s'arrêter)."""
        return (bool(s.get("live")) and s["id"] not in self.streams
                and time.monotonic() - self.hub_done.get(s["id"], -60) > 30)

    def start_edit(self, item, widget):
        """Prépare la modification d'un message de l'utilisateur : son texte et ses images
        reviennent dans la saisie, lui et la suite sont grisés. L'envoi (launch_edit)
        relance claude sur la même conversation, arrêtée juste avant ce message : pas de
        copie, la suite est abandonnée. Une réponse en cours n'est arrêtée qu'à l'envoi."""
        s = self.current
        if self.orphan(s):
            return self.status.set_text("Modification possible une fois fini le Claude lancé "
                                        "avant le redémarrage.")
        if self.in_terminal(s):  # ce claude-là garderait l'ancienne suite en mémoire
            return self.status.set_text("Conversation ouverte dans un terminal : modifie le "
                                        "message là-bas (Échap deux fois).")
        self.cancel_fork()
        widget = self.feed_child(widget)
        if widget is None:
            return
        self.fork_at = {"sid": s["id"], "at": item["parent"], "widget": widget, "before": True,
                        "text": item.get("text"), "edit": True}
        self.grey_after_cut(widget)
        self.input.get_buffer().set_text(item.get("text") or "")
        # les images trop anciennes ne sont plus dans l'historique lu (budget des scripts distants)
        images = [img for img in item.get("images") or [] if img.get("data")]
        self.attachments = [(img["type"], base64.b64decode(img["data"])) for img in images]
        self.show_attachments()
        missing = len(item.get("images") or []) - len(images)
        self.fork_icon.set_from_icon_name("document-edit-symbolic")
        self.fork_cancel.set_tooltip_text("Annuler la modification")
        self.fork_label.set_text(
            "Modifie le message puis envoie : Claude repartira de là, dans cette conversation. "
            "La suite grisée sera abandonnée (les fichiers modifiés restent tels quels)."
            + (" Image trop ancienne, non reprise." if missing == 1 else
               f" {missing} images trop anciennes, non reprises." if missing else ""))
        self.show_fork_warning()
        self.fork_bar.set_visible(True)
        self.input.grab_focus()

    def show_fork_warning(self):
        """Message modifié pendant une réponse : le bandeau prévient que l'envoi arrêtera Claude."""
        on = bool(self.fork_at and self.fork_at.get("edit") and self.proc)
        self.fork_warn.set_text("Claude est en train de répondre : l'envoi l'arrêtera." if on else "")
        self.fork_warn.set_visible(on)
        (self.fork_bar.add_css_class if on else self.fork_bar.remove_css_class)("warn")

    def grey_after_cut(self, start=None):
        """Grise ce qui ne fera pas partie de la suite préparée (copie, message modifié) :
        tout à partir de start, ou ce qui vient d'être ajouté au fil. Les commandes « ! » pas
        encore envoyées partiront avec le message : elles restent telles quelles. Si le fil a
        été refait sans le message choisi, la préparation est annulée."""
        f = self.fork_at
        if not f or self.current is None or f["sid"] != self.current["id"]:
            return
        if f["widget"].get_parent() is not self.msgs:
            self.cancel_fork()
            return self.status.set_text("Fil rechargé : " + (
                "modification" if f.get("edit") else "copie") + " annulée.")
        keep = [self.feed_child(b) for b in self.shells.get(f["sid"], [])]
        if start is None:  # du dernier élément jusqu'au premier déjà grisé (ou au message choisi)
            w, step = self.msgs.get_last_child(), Gtk.Widget.get_prev_sibling
        else:
            w, step = start, Gtk.Widget.get_next_sibling
        while w is not None and (start is not None or not (
                w is f["widget"] or w.has_css_class("after-fork"))):
            if not any(w is k for k in keep):
                w.add_css_class("after-fork")
            w = step(w)

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
                if self.fork_at.get("edit"):  # ses images aussi
                    self.attachments = []
                    self.show_attachments()
            self.fork_at = None
        self.fork_bar.set_visible(False)

    def cut_feed(self, widget):
        """Retire du fil widget et tout ce qui le suit (message modifié et suite abandonnée),
        sauf les commandes « ! » pas encore envoyées, remises à la fin ; self.feed suit."""
        w = widget if widget.get_parent() is self.msgs else None
        while w is not None:
            nxt = w.get_next_sibling()
            self.msgs.remove(w)
            w = nxt
        while (last := self.msgs.get_last_child()) is not None and last.has_css_class("feed-day"):
            self.msgs.remove(last)  # le jour des éléments retirés
        k = next((i for i, (_, t) in enumerate(self.feed) if t.get_parent() is not self.msgs),
                 len(self.feed))
        del self.feed[k:]
        ids = {it.get("id") for it, _ in self.feed}  # appels retirés oubliés
        self.tool_calls = {t: c for t, c in self.tool_calls.items() if t in ids}
        self.agent_blocks = {t: b for t, b in self.agent_blocks.items() if t in ids}
        if self.current:
            self.show_shells(self.current)

    def launch_edit(self, s, text):
        """Envoie le message modifié préparé par start_edit : claude reprend la même
        conversation (`--resume-session-at`, sans `--fork-session`) arrêtée juste avant le
        message d'origine, et ce qui suivait est abandonné. Une réponse en cours est d'abord
        arrêtée ; le message part quand claude s'est arrêté (on_finished)."""
        f = self.fork_at
        self.fork_at = None
        self.fork_bar.set_visible(False)
        self.cut_feed(f["widget"])
        images = self.take_input()
        st = self.streams.get(s["id"], (None, None))[1]
        if st is not None:
            st["then_edit"] = (text, images, f["at"])
            return self.stop()
        self.launch(s, text, images, at=f["at"])

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
        if not (text or images or self.ready_shells(s["id"])):  # commandes « ! » retirées depuis
            return self.send_queued(s)
        self.launch(s, text, images)
        if s is self.current:
            self.show_queue()

    def inject(self, s, text, images):
        """Transmet un message au claude -p qui répond : il le lira après l'étape en
        cours (comme un message tapé pendant une réponse dans le terminal). Faux si
        claude n'écoute plus son entrée (réponse finie ou interrompue)."""
        proc, st = self.streams[s["id"]]
        shells = self.ready_shells(s["id"])
        if proc.stdin.closed or st.get("interrupted") or not self.write_input(
                proc, self.user_message(text, images, shells)):
            return False
        self.shells_sent(s["id"], shells)
        if st.get("tasks"):  # claude attend ses tâches de fond : sa réponse sera notifiée
            st["asked"] = True
        if text or images:
            item = {"role": "user", "text": text, "images": images, "queued": True, "ts": time.time()}
            bubble = message_widget(item)
            self.append_item(bubble, item)
            self.injected.setdefault(s["id"], []).append((item, bubble))
        self.scroll_end()
        self.status.set_text("Message transmis : Claude le lira une fois la demande tranchée."
                             if self.pending.get(s["id"]) else
                             "Message transmis : Claude le lira après l'étape en cours.")
        return True

    @staticmethod
    def user_message(text, images, shells=()):
        """Ligne stream-json d'un message de l'utilisateur pour l'entrée de claude, précédé
        des commandes « ! » shells (ShellBlock), comme le terminal les joint au message."""
        content = [b for sh in shells for b in sh.blocks()]
        content += [{"type": "image", "source": {"type": "base64", "media_type": img["type"],
                                                  "data": img["data"]}} for img in images]
        if text:
            content.append({"type": "text", "text": text})
        return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"

    def take_input(self):
        """Vide la saisie et les pièces jointes (message envoyé : la prédiction de claude
        est dépassée) ; renvoie les images encodées."""
        if self.current is not None:
            self.drop_suggestion(self.current["id"])
        self.input.get_buffer().set_text("")
        images = [{"type": mime, "data": base64.b64encode(data).decode()}
                  for mime, data in self.attachments]
        self.attachments = []
        self.show_attachments()
        return images

    # -- commandes « ! » : lancées ici, jointes au message suivant (shell.py) --
    def update_shell_mode(self):
        """Saisie « ! … » dans une conversation : cadre de la saisie en couleur, et où la
        commande sera lancée."""
        on = self.current is not None and shell_command(self.input_text()) is not None
        if on == self.composer.has_css_class("shell-mode"):
            return
        (self.composer.add_css_class if on else self.composer.remove_css_class)("shell-mode")
        if on:
            self.status.set_text(self.shell_hint(self.current))
        elif not self.proc:
            self.update_status()

    def shell_hint(self, s):
        """Saisie « ! … » : comment lancer la commande (dans le dossier de la conversation,
        affiché dans l'en-tête), ou pourquoi elle ne le sera pas."""
        where = "" if s["host"] == "local" else f" sur {s['host']}"
        return self.shell_refusal(s) or f"Commande shell · Entrée : la lancer{where}"

    def shell_refusal(self, s):
        """Pourquoi une commande « ! » ne peut pas être lancée pour s, ou None."""
        if s["host"] == CLOUD or s.get("source") == "claude-cloud":
            return "Conversation claude.ai : pas de machine où lancer la commande."
        if s.get("source") == "cursor" or s.get("cursor_cloud"):
            return "Conversation Cursor : les commandes « ! » n'y sont pas prises en charge."
        if self.orphan(s):
            return "Claude lancé avant le redémarrage : lance la commande une fois qu'il a fini."
        if (s.get("source") is None and not s.get("new") and s.get("live")
                and not self.is_streaming(s) and not self.queues.get(s["id"])):
            return "Conversation ouverte dans un terminal : tape la commande là-bas."
        cwd = s.get("cwd")
        if not cwd:
            return "Dossier de la conversation inconnu : commande non lancée."
        if s["host"] == "local" and not os.path.isdir(cwd):
            return f"Le dossier {cwd} n'existe plus : commande non lancée."
        return None

    def run_shell(self, s, command):
        """« ! commande » : lancée dans le dossier de s, sur son hôte ; sa sortie part avec
        le message suivant (Entrée seule : tout de suite)."""
        if not command:
            return
        why = self.shell_refusal(s)
        if why:
            return self.status.set_text(why)
        self.input.get_buffer().set_text("")  # les images jointes restent pour le message
        block = ShellBlock(command, done=False)
        block.ts = time.time()  # son heure, remise avec lui dans le fil rechargé (show_shells)
        self.append_item(block, {"role": "bash", "ts": block.ts})
        self.scroll_end()
        argv, cwd = shell_argv(s["host"], s["cwd"], command)
        try:
            block.start(argv, cwd, lambda b: self.shell_done(s, b))
        except OSError as e:
            return block.fail(f"Lancement impossible : {e}")
        block.set_pending(lambda b: self.drop_shell(s, b))
        self.shells.setdefault(s["id"], []).append(block)
        self.refresh_state(s["id"])  # « brouillon » dans la liste
        self.status.set_text("Commande lancée : sa sortie partira avec ton prochain message.")

    def shell_done(self, s, block):
        if s is self.current and not block.sent and block in self.shells.get(s["id"], []):
            code = "arrêtée" if block.stopped else f"code {block.code}" if block.code else "terminée"
            self.status.set_text(f"Commande {code} : sa sortie partira avec ton prochain message "
                                 "(Entrée seule : tout de suite).")

    def ready_shells(self, sid):
        """Commandes « ! » finies de la conversation, pas encore envoyées à Claude."""
        return [b for b in self.shells.get(sid, []) if b.done]

    def shells_sent(self, sid, shells):
        """shells sont partis avec un message : plus en attente."""
        if not shells:
            return
        for b in shells:
            b.mark_sent()
        left = [b for b in self.shells.get(sid, []) if b not in shells]
        if left:
            self.shells[sid] = left
        else:
            self.shells.pop(sid, None)
        self.refresh_state(sid)

    def drop_shell(self, s, block):
        """Bouton « retirer » d'une commande en attente : arrêtée, hors du fil, pas envoyée."""
        block.stop()
        left = [b for b in self.shells.get(s["id"], []) if b is not block]
        if left:
            self.shells[s["id"]] = left
        else:
            self.shells.pop(s["id"], None)
        top = self.feed_child(block)
        if top is not None:
            self.msgs.remove(top)
        self.refresh_state(s["id"])

    def show_shells(self, s):
        """Remet à la fin du fil (rechargé) les commandes « ! » de s pas encore envoyées."""
        for b in self.shells.get(s["id"], []):
            if self.feed_child(b) is not None:
                continue
            if b.get_parent() is not None:  # l'ancien conteneur de son heure
                b.get_parent().remove(b)
            ts = getattr(b, "ts", None)
            self.msgs.append(Timed(b, ts) if ts else b)

    def show_injected(self, s):
        """Remet à la fin du fil (relu) les messages transmis à claude pendant sa réponse
        qu'il n'a pas encore lus : ils ne sont pas encore dans sa transcription."""
        for item, bubble in self.injected.get(s["id"], []):
            if self.feed_child(bubble) is not None:
                continue
            if bubble.get_parent() is not None:  # l'ancien conteneur de son heure
                bubble.get_parent().remove(bubble)
            self.msgs.append(Timed(bubble, item["ts"]))

    def forget_read(self, sid, items):
        """Oublie les messages transmis que claude a lus : ils sont dans l'historique items."""
        sent = self.injected.get(sid)
        if not sent:
            return
        left = {id(item) for item in unread([item for item, _ in sent], items)}
        self.injected[sid] = [(item, bubble) for item, bubble in sent if id(item) in left]
        if not self.injected[sid]:
            del self.injected[sid]

    def show_user(self, text, images):
        if text or images:  # pas de bulle vide pour des commandes « ! » envoyées seules
            item = {"role": "user", "text": text, "images": images, "ts": time.time()}
            self.append_item(message_widget(item), item)
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
        if s.get("live") == "shell" or self.background_shells(s):
            self.asked.add(s["id"])  # elle attend ses shells ou monitors : sa réponse sera notifiée
        s["live"] = "busy"  # le Claude du terminal va répondre : la vue suivra son fichier
        self.update_activity()
        self.status.set_text("Envoi à la session du terminal (Remote Control)…")

        def done(_):
            self.status.set_text("Message transmis au Claude du terminal : sa réponse s'affichera ici.")

        def fail(e):
            self.asked.discard(s["id"])
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
                "--permission-prompt-tool", "stdio",
                # message suivant prédit (prompt_suggestion), comme le terminal le propose
                "--prompt-suggestions"]
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
        if not fork:  # une copie a son propre id, connu à son démarrage (on_event)
            self.mark_launched(s["id"], s["host"])
        self.drop_suggestion(s["id"])  # nouvelle réponse : claude en prédira une autre
        s["mode"] = argv[argv.index("--permission-mode") + 1]
        self.keep_mode(s["id"], s["mode"])
        self.unsent.pop(s["id"], None)  # envoyée : n'est plus un brouillon
        s["tasks"], s["idle"] = set(), False
        for key in ("bg_shells", "monitors", "monitor_calls", "asked"):
            s.pop(key, None)
        s["step"] = ("Claude réfléchit", None, time.monotonic())
        s.pop("interrupted", None)
        self.touch(s["id"])
        self.refresh_state(s["id"])  # « brouillon » -> « travaille »
        # stdin reste ouvert pour répondre aux demandes ; fermé à la fin de la réponse.
        shells = self.ready_shells(s["id"])
        if self.write_input(proc, self.user_message(prompt, images, shells)):
            self.shells_sent(s["id"], shells)
        s.pop("titling", None)
        if about and about.strip():
            # persist : claude écrit lui-même l'ai-title dans la transcription.
            s["titling"] = f"title-{uuid.uuid4()}"
            self.write_input(proc, json.dumps({"type": "control_request", "request_id": s["titling"], "request": {
                "subtype": "generate_session_title", "description": about.strip()[:2000], "persist": True}}) + "\n")
        if shown:
            self.update_send_btn()
            self.status.set_text("Entrée : lu après l'étape en cours")
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
                if s["id"] in self.shells:
                    self.shells[ev["session_id"]] = self.shells.pop(s["id"])
                if s["id"] in self.sides:
                    self.sides[ev["session_id"]] = self.sides.pop(s["id"])
                s["id"] = ev["session_id"]
                self.mark_launched(s["id"], s["host"])
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
        if t == "prompt_suggestion":  # après le résultat, avant que claude se termine
            return self.got_suggestion(s, ev.get("suggestion"))
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
        if t == "system" and ev.get("subtype") == "background_tasks_changed":
            return self.on_background_tasks(ev, s)
        if t == "result":  # pour la notification de fin
            s["result"], s["result_error"] = ev.get("result"), bool(ev.get("is_error"))
            # coût de la réponse, et fenêtre de contexte des modèles (pour la jauge de l'en-tête)
            learned = learn_windows(self.settings["context_windows"], ev.get("modelUsage"))
            if remember_cost(self.settings["costs"], s["id"], ev.get("total_cost_usd")) or learned:
                save_settings(self.settings)
            self.show_usage(s)
            s["idle"] = True
            if s.pop("asked", False) and s.get("tasks"):
                # réponse à un message écrit pendant l'attente des tâches de fond ; sans elles,
                # claude se termine et on_finished notifie
                self.reply_done(s, text=s["result"], error=s["result_error"], replied=True)
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
        if t == "assistant":  # modifications de fichiers (sous-agents compris) : relues à leur résultat
            for b in ev.get("message", {}).get("content", []):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
                    s.setdefault("edit_ids", set()).add(b.get("id"))
                elif isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Monitor":
                    s.setdefault("monitor_calls", set()).add(b.get("id"))  # pour la pastille
        if t == "user":  # monitor lancé : la pastille ne le compte pas comme un shell
            tid = monitor_started(ev.get("tool_use_result") or ev.get("toolUseResult"))
            if tid:
                mark_monitor(s, tid)
                if self.current and self.current["id"] == s["id"]:
                    self.update_activity()
        if t == "user" and s.get("edit_ids"):
            content = ev.get("message", {}).get("content")
            if any(isinstance(b, dict) and b.get("tool_use_id") in s["edit_ids"]
                   for b in content if isinstance(content, list)):
                self.schedule_edits(s)
        if t == "assistant" and not ev.get("parent_tool_use_id"):
            tokens = context_tokens(ev.get("message", {}).get("usage"))
            if tokens:  # contexte après ce message, en direct
                s["context"] = {"tokens": tokens, "model": ev["message"].get("model") or s.get("model")}
                self.show_usage(s)
        if t == "assistant" and not ev.get("parent_tool_use_id"):  # étape en cours (bandeau)
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    self.step(s, "Claude réfléchit")
                elif b.get("type") == "tool_use":
                    self.step(s, f"Claude utilise {b.get('name')}", tool_detail(b.get("input")))
        if self.track_todos(ev, s) and s is self.current:
            self.show_todos(s["todos"])
        if s is not self.current or s.get("then_edit"):  # réponse abandonnée : plus affichée
            return
        if t == "user":  # sorties d'outils (de la conversation ou d'un sous-agent)
            content = ev.get("message", {}).get("content")
            for b in content if isinstance(content, list) else []:
                call = self.tool_calls.get(b.get("tool_use_id")) if isinstance(b, dict) else None
                if call and b.get("type") == "tool_result":
                    call.set_content(b.get("content"), bool(b.get("is_error")))
        parent = ev.get("parent_tool_use_id")
        if parent:  # message d'un sous-agent : dans son bloc
            if t == "assistant":
                self.agent_step(s, parent, ev)
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
                    item = {"role": "assistant", "text": b["text"], "ts": time.time()}
                    if ev.get("uuid"):
                        item["uuid"] = ev["uuid"]
                    self.append_item(message_widget(item, self.fork_handler(s)), item)
                elif b.get("type") == "tool_use":
                    item = {"role": "tool", "name": b.get("name"), "input": b.get("input"),
                            "id": b.get("id"), "ts": time.time()}
                    # Sous-agent suivi en direct, sans relecture du fichier ; le plan
                    # s'affiche dans sa carte.
                    if b.get("name") != "ExitPlanMode":
                        self.append_item(self.item_widget(item, s, live=True), item)
            self.scroll_end()
        elif t == "result":
            if s.get("tasks"):  # tour fini, claude attend ses tâches de fond : le bandeau le dit
                self.update_activity()
            cost = ev.get("total_cost_usd")
            cost = f" · {cost_text(cost)}" if isinstance(cost, (int, float)) and cost > 0 else ""
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
        self.unmark_launched(s["id"])
        self.pending.pop(s["id"], None)
        self.injected.pop(s["id"], None)  # lus, ou perdus avec claude : la relecture le dira
        s.pop("bg_shells", None)  # morts avec claude
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
            self.show_note_btn(self.current)
        edit = s.pop("then_edit", None)
        if edit:  # message modifié pendant la réponse : il part maintenant que claude s'est arrêté
            text, images, at = edit
            return self.launch(self.current if shown else s, text, images, at=at)
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
        avec elle, sinon le dernier choisi (celui d'une nouvelle conversation si s est None)."""
        if s is None:
            return self.settings["mode"]
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

    def on_mode_changed(self):
        self.paint_mode()
        self.push_mode()

    def paint_mode(self):
        """Bouton du mode en évidence (bleu) en mode plan."""
        plan = PERMISSION_MODES[self.mode_dd.get_selected()] == "plan"
        (self.mode_dd.add_css_class if plan else self.mode_dd.remove_css_class)("plan")

    def push_mode(self):
        """Mode choisi : retenu pour la conversation, et transmis au claude en cours, sinon
        il ne s'appliquerait qu'au message suivant ; sur l'accueil, celui des nouvelles."""
        if self.syncing_mode:
            return
        if not self.current:
            self.settings["mode"] = PERMISSION_MODES[self.mode_dd.get_selected()]
            return save_settings(self.settings)
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
        self.append_item(card)
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
        req = self.pending.get(sid, {}).get(rid)
        if req is not None and req.get("remote"):
            return self.answer_remote(sid, rid, req["remote"], response, summary)
        proc = self.streams.get(sid, (None,))[0]
        if rid not in self.pending.get(sid, {}) or not proc or not self.write_input(
                proc, control_response(rid, response)):
            return self.request_done(sid, rid, "Demande expirée : Claude ne l'attend plus.", ok=False)
        del self.pending[sid][rid]
        self.request_done(sid, rid, summary, ok=response["behavior"] == "allow", response=response)
        if mode in PERMISSION_MODES and self.current and self.current["id"] == sid:
            self.streams[sid][1]["mode"] = mode  # déjà transmis avec la réponse
            self.mode_dd.set_selected(PERMISSION_MODES.index(mode))  # messages suivants dans ce mode
        self.refresh_state(sid)
        if sid in self.streams:
            self.step(self.streams[sid][1], "Claude reprend")

    def answer_remote(self, sid, rid, bridge, response, summary):
        """Réponse à une demande du Claude d'un terminal (ou de claude.ai), postée comme
        claude.ai la poste ; la carte reste grisée jusqu'à la réponse de l'API."""
        for card in self.cards(sid, rid):
            card.set_sensitive(False)
        self.status.set_text("Envoi de la réponse (Remote Control)…")

        def done(_):
            self.remote_answered.add(rid)
            self.pending.get(sid, {}).pop(rid, None)
            if not self.pending.get(sid):
                self.pending.pop(sid, None)
            self.request_done(sid, rid, summary, ok=response["behavior"] == "allow", response=response)
            if self.current and self.current["id"] == sid:
                self.current["live"] = "busy"  # Claude reprend : la vue suivra son fichier
                self.status.set_text("Réponse transmise : Claude reprend.")
            self.refresh_state(sid)

        def fail(e):
            for card in self.cards(sid, rid):
                card.set_sensitive(True)
            if self.current and self.current["id"] == sid:
                self.error(f"Réponse via Remote Control impossible : {e}")
        in_thread(lambda: remote_respond(bridge, rid, response), done, fail)

    def cards(self, sid, rid):
        """Cartes de la demande rid dans le fil (si sid est la conversation affichée)."""
        if not (self.current and self.current["id"] == sid):
            return []
        found, child = [], self.msgs.get_first_child()
        while child:
            if isinstance(child, RequestCard) and child.request_id == rid:
                found.append(child)
            child = child.get_next_sibling()
        return found

    def drop_cards(self, sid, rid):
        """Demande close ailleurs (dans le terminal) : sa carte quitte le fil."""
        for card in self.cards(sid, rid):
            self.msgs.remove(card)
        if self.plan_panel.sid == sid and self.plan_panel.request_id == rid:
            self.plan_panel.done("Réponse donnée dans le terminal.", True)

    def request_done(self, sid, rid, summary, ok, response=None):
        """Demande close : sa carte dans le fil et le volet du plan affichent la décision
        (response : la réponse envoyée à Claude, s'il y en a une)."""
        for card in self.cards(sid, rid):
            card.done(summary, ok, response)
        if self.plan_panel.sid == sid and self.plan_panel.request_id == rid:
            self.plan_panel.done(summary, ok)

    def refresh_state(self, sid):
        """État de la conversation dans la liste (demande en attente, en cours…)."""
        self.repaint(sid)
        if self.current and self.current["id"] == sid:
            self.update_activity()

    def on_task(self, ev, s):
        """Tâches de fond (sous-agents) : compteur, et état dans leur bloc."""
        sub, tid = ev.get("subtype"), ev.get("task_id")
        status = ev.get("status") or (ev.get("patch") or {}).get("status")
        tool = ev.get("tool_use_id")
        live = s.setdefault("live_agents", {})  # id de l'appel Agent -> sous-agent vu dans le flux
        if sub == "task_started":
            s["tasks"].add(tid)
            if ev.get("task_type") == "local_bash" and tool in (s.get("monitor_calls") or ()):
                mark_monitor(s, str(tid))
            if ev.get("task_type") != "local_bash":  # un shell ou un monitor n'est pas un sous-agent
                s["agents_stale"] = True  # nouveau sous-agent : relire la liste
                # pour le bandeau, avant que la liste relue sur l'hôte le connaisse (un workflow
                # a sa propre ligne, d'après ses agents)
                if tool and ev.get("task_type") != "local_workflow":
                    live[tool] = {"id": tid, "tool": tool, "type": ev.get("subagent_type"),
                                  "description": ev.get("description"), "start": time.time(),
                                  "end": time.time(), "last": None}
        elif sub == "task_notification" or (sub == "task_updated" and status not in (None, "running")):
            s["tasks"].discard(tid)
            live.pop(tool, None)
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

    def on_background_tasks(self, ev, s):
        """Liste des tâches en arrière-plan que claude -p tient à jour : ses shells et ses
        monitors (task_type « local_bash » pour les deux) alimentent la pastille de l'en-tête."""
        known = {sh["id"]: sh for sh in s.get("bg_shells") or ()}
        shells = []
        for task in ev.get("tasks") or ():
            if isinstance(task, dict) and task.get("task_type") == "local_bash" and task.get("task_id"):
                tid = str(task["task_id"])
                kind = "monitor" if tid in (s.get("monitors") or ()) else "shell"
                shells.append(known.get(tid) or {"id": tid, "kind": kind, "start": time.time(),
                                                 "description": str(task.get("description") or ""),
                                                 "command": ""})
        s["bg_shells"] = shells
        if self.current and self.current["id"] == s["id"]:
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
        """Bouton « 🤖 n » de l'en-tête, et le volet s'il montre cette conversation (sur
        l'agent demandé, s["agents_want"], s'il est maintenant dans la liste)."""
        if s is not self.current:
            return
        agents = s.get("agents") or [] if self.has_agents_files(s) else []
        alive = self.agents_alive(s)
        running = sum(agent_state(a, alive) == "running" for a in agents)
        self.agents_btn.set_visible(bool(agents))
        # ceux qui travaillent sont listés au-dessus de la saisie : ici, le total
        self.agents_count.set_text(f"{len(agents)}")
        self.agents_btn.set_tooltip_text(f"Sous-agents de cette conversation ({len(agents)}"
                                         + (f", dont {running} au travail)" if running else ")"))
        self.update_activity()  # lignes des sous-agents au-dessus de la saisie
        if self.has_agents_files(s):
            host, sid = s["host"], s["id"]
            self.agents_panel.set_session(
                # un agent de workflow n'a pas d'appel d'outil : son id le désigne
                sid, lambda a: run_helper(host, "agent", sid, a.get("tool") or a["id"], HISTORY_LIMIT),
                lambda it: self.item_widget(it, s))
            if self.agents_panel.get_visible():
                self.agents_panel.update(agents, alive, s.get("workflow_runs") or [])
                if s.get("agents_want") and self.agents_panel.open_agent(s["agents_want"]):
                    del s["agents_want"]

    def agent_step(self, s, tool_id, ev):
        """Message d'un sous-agent reçu en direct : sa dernière étape, pour le bandeau, sans
        attendre la relecture de la liste sur l'hôte."""
        found = [a for a in s.get("agents") or () if a.get("tool") == tool_id]
        found += [a for t, a in (s.get("live_agents") or {}).items() if t == tool_id]
        content = ev.get("message", {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if found and isinstance(content, list) else []
        tools = [b for b in blocks if b.get("type") == "tool_use"]
        texts = [b["text"] for b in blocks if b.get("type") == "text" and (b.get("text") or "").strip()]
        if tools:
            last = {"role": "tool", "name": tools[-1].get("name"), "input": tools[-1].get("input")}
        elif texts:
            last = {"role": "assistant", "text": texts[-1][:300]}
        else:
            return
        for a in found:
            a["last"], a["end"] = last, time.time()
        self.update_activity()

    def toggle_agents(self):
        if self.agents_panel.get_visible() and self.side_stack.get_visible_child() is self.agents_panel:
            return self.close_side("agents")
        self.show_agents_panel()

    def show_agents_panel(self):
        s = self.current or {}
        self.agents_panel.update(s.get("agents"), self.agents_alive(self.current),
                                 s.get("workflow_runs") or [])
        self.show_side("agents")

    def open_agent(self, key):
        """Ouvre le volet sur un sous-agent (id de l'agent ou de l'appel Agent qui l'a lancé)."""
        s = self.current
        if not s:
            return
        self.agents_panel.update(s.get("agents"), self.agents_alive(s), s.get("workflow_runs") or [])
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
            s["workflow_runs"] = res.get("workflow_runs") or []
            self.show_agents(s)  # ouvre l'agent demandé s'il est maintenant dans la liste
            s.pop("agents_want", None)  # sinon, on n'insiste pas

        def fail(_e):
            self.agents_polling = False
        in_thread(lambda: run_helper(s["host"], "agents", s["id"]), done, fail)
        return True

    # -- fichiers modifiés --
    @staticmethod
    def edit_time(ts):
        """Heure d'une modification (avec le jour si ce n'est pas aujourd'hui)."""
        same_day = local_date(ts) == local_date(time.time())
        return time.strftime("%H:%M" if same_day else "%d/%m %H:%M", time.localtime(ts))

    def edits_source(self, s):
        """D'où viennent les modifications de s : « helper » (Claude Code de ce PC ou SSH :
        toute la transcription et les sous-agents), « items » (cloud, Cursor : l'historique
        affiché), ou None (conversation pas encore envoyée)."""
        if not s or s["id"] in self.unsent:
            return None
        if s.get("source") is None and s["host"] != CLOUD:
            return "helper"
        return "items"

    def refresh_edits(self, s, items):
        """Après une lecture de l'historique : relit les modifications si une nouvelle est
        apparue, si l'une attendait son résultat, ou la première fois."""
        source = self.edits_source(s)
        if source != "helper":
            return self.load_edits(s)
        last = next((it.get("id") for it in reversed(items) if it.get("role") == "tool"
                     and it.get("name") in EDIT_TOOLS), None)
        pending = any(e.get("status") == "pending" for e in s.get("edits") or [])
        if "edits" not in s or last != s.get("edits_last") or pending:
            s["edits_last"] = last
            self.load_edits(s)

    def schedule_edits(self, s):
        """Une modification vient d'aboutir pendant une réponse : relecture groupée."""
        if self.edits_timer or not self.current or self.current["id"] != s["id"]:
            return

        def later():
            self.edits_timer = 0
            if self.current and self.current["id"] == s["id"]:
                self.load_edits(self.current)
            return False
        self.edits_timer = GLib.timeout_add(1500, later)

    def load_edits(self, s):
        """Lit les fichiers modifiés de s : bouton de l'en-tête et volet."""
        source = self.edits_source(s)
        if source != "helper":
            s["edits"] = edits_from_items(s.get("items")) if source else []
            return self.show_changes(s)
        self.edits_gen += 1
        gen, host, sid = self.edits_gen, s["host"], s["id"]

        def done(res):
            cur = self.current
            if gen != self.edits_gen or not cur or cur["id"] != sid:
                return
            s["edits"] = cur["edits"] = res.get("edits") or []
            self.show_changes(cur)
        in_thread(lambda: run_helper(host, "edits", sid), done, lambda _e: None)

    def changes_args(self, s):
        """(dossier de la conversation, lecteur du diff d'une modification sans diff) pour le volet."""
        cwd = s.get("cwd") if ":" not in (s.get("cwd") or "") else None
        loader = None
        if s.get("source") == "cursor" and not s.get("cursor_cloud"):
            loader = lambda e: cursor_result(e["id"]).get("text")  # noqa: E731 — diff des versions
        return cwd, loader

    def show_changes(self, s):
        """Bouton « fichiers modifiés » de l'en-tête (nombre de fichiers), et le volet s'il est ouvert."""
        if not self.current or self.current["id"] != s["id"]:
            return
        files = group_files(s.get("edits"))
        n = len(files)
        added, removed = sum(f["added"] for f in files), sum(f["removed"] for f in files)
        self.changes_btn.set_visible(bool(n))
        self.changes_count.set_text(str(n))
        tip = f"Fichiers modifiés par cette conversation : {n}"
        if added or removed:
            tip += f" (+{added} −{removed})"
        self.changes_btn.set_tooltip_text(tip + "\nPour les relire avant de committer")
        if self.changes_panel.get_visible():
            self.changes_panel.show(s["id"], s.get("edits") or [], *self.changes_args(s))

    def toggle_changes(self):
        if (self.changes_panel.get_visible()
                and self.side_stack.get_visible_child() is self.changes_panel):
            return self.close_side("changes")
        s = self.current
        if s:
            self.changes_panel.show(s["id"], s.get("edits") or [], *self.changes_args(s))
            self.show_side("changes")

    # -- notes --
    def note_text(self, host, sid):
        """Début de la note de sid (index lu avec la liste, tenu à jour à l'enregistrement)."""
        return self.note_index.get(store_of(host), {}).get(sid)

    def can_note(self, s):
        """Une note n'est possible qu'une fois la conversation créée par claude."""
        return bool(s) and s["id"] not in self.unsent and not s.get("new") and valid_id(s["id"])

    def show_note_btn(self, s):
        """Bouton « note » de l'en-tête : en couleur quand la conversation en a une."""
        ok = self.can_note(s)
        self.note_btn.set_visible(ok)
        if not ok:
            return
        text = self.note_text(s["host"], s["id"])
        (self.note_btn.add_css_class if text else self.note_btn.remove_css_class)("has-note")
        self.note_btn.set_tooltip_text(f"Note :\n{note_preview(text)}" if text else
                                       f"Ajouter une note (Markdown, rangée {where_text(s['host'])})")

    def toggle_notes(self):
        if self.notes_panel.get_visible() and self.side_stack.get_visible_child() is self.notes_panel:
            return self.close_side("notes")
        self.show_notes()

    def show_notes(self):
        s = self.current
        if self.can_note(s):
            self.notes_panel.show(s["host"], s["id"], focus=True)
            self.show_side("notes")

    def row_notes(self, sid):
        """« Note… » du clic droit : ouvre la conversation et sa note."""
        if not self.current or self.current["id"] != sid:
            self.open_sid(sid)
        if self.current and self.current["id"] == sid:
            self.show_notes()

    def note_saved(self, host, sid, text):
        index = self.note_index.setdefault(store_of(host), {})
        if text.strip():
            index[sid] = excerpt(text)
        else:
            index.pop(sid, None)
        row = self.rows.get(sid)
        if row is not None and row.host == host:
            row.set_note(note_preview(text) if text.strip() else None)
        if self.current and self.current["id"] == sid:
            self.show_note_btn(self.current)

    def note_failed(self, host, sid, e):
        """Échec d'un enregistrement : le volet le dit pour la note affichée, sinon la barre d'état."""
        if (host, sid) != (self.notes_panel.host, self.notes_panel.sid):
            self.status.set_text(f"Note non enregistrée {where_text(host)} : {e}")

    def close_notes(self):
        """Fermeture de la fenêtre : enregistre la note en cours et attend la fin (15 s au plus)."""
        self.notes_panel.flush()
        self.notes_panel.io.wait(15)

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
    def set_side_page(self, page, visible):
        """Montre ou cache une page du volet et son onglet : GTK garde l'onglet d'une page
        dont seul le widget est caché."""
        child = self.side_stack.get_child_by_name(page)
        child.set_visible(visible)
        self.side_stack.get_page(child).set_visible(visible)
        return child

    def show_side(self, page):
        """Ouvre une page du volet (SIDE_PAGES), à mi-largeur s'il était fermé."""
        self.side_stack.set_visible_child(self.set_side_page(page, True))
        self.update_side()

    def close_side(self, page):
        """Ferme une page du volet ; le volet se cache quand toutes le sont."""
        self.set_side_page(page, False)
        if page == "plan":
            self.sync_plan_cards()
        elif page == "notes":
            self.notes_panel.forget()  # enregistrée ; relue à la prochaine ouverture
        self.update_side()

    def update_side(self, position=None):
        """Volet montré s'il a une page ouverte ; s'il était fermé, le séparateur va à position
        (en pixels, celle qu'avait la conversation), ou au milieu."""
        pages = [c for c in (self.plan_panel, self.viewer, self.agents_panel, self.changes_panel,
                             self.notes_panel) if c.get_visible()]
        self.side_switch.set_visible(len(pages) > 1)
        if pages and not self.side.get_visible():
            self.side.set_visible(True)
            self.chat_paned.set_position(position or int(self.chat_paned.get_width() * 0.5))
        self.side.set_visible(bool(pages))

    def side_state(self):
        """Volet de la conversation affichée : pages ouvertes, page montrée, contenu et
        largeur, pour le retrouver en y revenant (None : fermé)."""
        pages = [p for p in SIDE_PAGES if self.side_stack.get_child_by_name(p).get_visible()]
        if not pages:
            return None
        v, p = self.viewer, self.plan_panel
        return {"pages": pages, "shown": self.side_stack.get_visible_child_name(),
                "position": self.chat_paned.get_position(),
                "file": (v.host, v.path, v.line, v.end),
                "plan": (p.plan, p.path, p.request_id, p.decision),
                "agent": self.agents_panel.selected}

    def stash_side(self):
        """On quitte la conversation affichée : son volet est retenu pour son retour."""
        if not self.current:
            return
        state = self.side_state()
        if state:
            self.sides[self.current["id"]] = state
        else:
            self.sides.pop(self.current["id"], None)

    def restore_side(self, s):
        """Rouvre le volet tel que s l'a laissé (fermé si elle n'en avait pas). Ce que le
        volet montre déjà n'est pas relu : après un passage par une conversation sans
        volet, on retrouve le fichier ou le plan tel quel. Renvoie l'agent à rouvrir."""
        st = self.sides.get(s["id"]) or {}
        pages = set(st.get("pages", ()))
        if not self.has_agents_files(s):
            pages.discard("agents")
        if not self.can_note(s):
            pages.discard("notes")
        for page in SIDE_PAGES:
            if page not in pages and self.side_stack.get_child_by_name(page).get_visible():
                self.close_side(page)
        if "plan" in pages:
            plan, path, rid, decision = st["plan"]
            if rid not in self.pending.get(s["id"], {}):
                rid = None  # demande close entre-temps : plus d'actions
            p = self.plan_panel
            if (p.sid, p.plan, p.request_id) != (s["id"], plan, rid):
                p.show(s["id"], plan, path, rid)
                if decision and rid is None:
                    p.done(*decision)
        v = self.viewer
        if "file" in pages and st["file"] != (v.host, v.path, v.line, v.end):
            v.load(*st["file"])
        if "notes" in pages:
            self.notes_panel.show(s["host"], s["id"])
        if "changes" in pages:  # liste remplie à la lecture des modifications (load_edits)
            self.changes_panel.show(s["id"], [], loading=bool(self.edits_source(s)))
        for page in pages:
            self.set_side_page(page, True)
        if pages:
            shown = st["shown"] if st["shown"] in pages else next(p for p in SIDE_PAGES if p in pages)
            self.side_stack.set_visible_child_name(shown)
        self.update_side(st.get("position"))
        return st.get("agent") if "agents" in pages else None

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
        """Plans du fil (render.plan_roles) : replie ceux que le volet affiche et ceux qu'un
        plan identique suit, et masque l'élément de l'historique d'un plan que sa carte
        montre déjà ; les autres restent dépliés."""
        shown = self.plan_panel.plan if self.plan_panel.get_visible() else None
        plans, child = [], self.msgs.get_first_child()
        while child:
            w = unwrap(child)  # plan de l'historique : avec son heure
            if hasattr(w, "plan_body"):
                plans.append((child, w))
            child = child.get_next_sibling()
        roles = plan_roles([(w.plan_text, w.plan_id, isinstance(w, RequestCard), w.plan_decided)
                            for _c, w in plans])
        for (child, w), (hidden, same) in zip(plans, roles):
            child.set_visible(not hidden)
            fold_plan(w, panel=w.plan_text == shown, same=same)

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
