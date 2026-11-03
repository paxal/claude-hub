"""Conversation dont Claude Hub ne peut pas joindre le Claude : pastille de l'en-tête
(HeadChip, avec son explication) et carte du fil quand Claude attend une réponse dans son
terminal (WaitCard).

Terminal sans Remote Control : Claude Hub ne peut ni écrire au Claude du terminal ni
répondre à ses demandes. Et Claude Code n'écrit souvent la question en attente dans la
transcription qu'une fois répondue (en mode auto, ou dans un tour relancé par la fin d'une
tâche de fond) : elle n'est alors visible que dans le terminal. « Remote Control » est lu
par la commande « stat » des scripts distants (bridgeSessionId du fichier de session de
claude).

Lancée avant le redémarrage : le claude -p qu'un Claude Hub précédent avait lancé tourne
encore, mais plus personne ne lit sa sortie ni n'écrit sur son entrée (Hub.orphan).
"""
from .gtk import Gtk

WARNING_ICON = "dialog-warning-symbolic"
ORPHAN_ICON = "view-refresh-symbolic"
PERMISSION = "permission prompt"  # waitingFor de claude pour une autorisation


def where(host):
    return "ce PC" if host == "local" else host


def help_texts(host):
    """(titre, paragraphes) de l'explication de la pastille « Sans Remote Control »."""
    return "Sans Remote Control", [
        "Ce Claude tourne dans un terminal sans être relié à Remote Control. Depuis Claude Hub, "
        "on ne peut donc pas :",
        "• lui envoyer un message (Claude Hub propose de continuer dans une copie) ;\n"
        "• répondre à ses questions, plans et autorisations ;\n"
        "• voir sa question avant d'y avoir répondu : Claude Code ne l'écrit souvent dans la "
        "conversation qu'après (en mode auto, ou quand la fin d'une tâche de fond l'a relancé).",
        "Pour cette session : tape /remote-control dans son terminal. Pas pendant qu'une "
        "question y est affichée, car elle prend la saisie : réponds-y d'abord.",
        f"Pour les suivantes : ajoute \"remoteControlAtStartup\": true à ~/.claude/settings.json "
        f"sur {where(host)}.",
    ]


def orphan_texts():
    """(titre, paragraphes) de l'explication de la pastille « Lancée avant le redémarrage »."""
    return "Lancée avant le redémarrage", [
        "Claude Hub a lancé ce Claude avant de redémarrer. Il continue sa réponse, qui "
        "s'affiche ici au fur et à mesure, mais Claude Hub n'est plus relié à lui : il ne "
        "peut ni lui écrire, ni répondre à ses questions, plans et autorisations.",
        "Dès qu'il a fini, Claude Hub l'arrête et la conversation reprend normalement. Un "
        "message écrit d'ici là attend dans la file et part à ce moment-là.",
        "S'il reste bloqué sur une question ou une autorisation, arrête-le avec le bouton "
        "d'arrêt au-dessus de la saisie : ce qu'il a déjà écrit est gardé.",
    ]


def wait_texts(waiting_for, linked, shown):
    """(titre, texte, conseil ou None) de la carte du fil d'une conversation dont le Claude
    attend une réponse dans son terminal, sans carte à laquelle répondre d'ici.
    waiting_for : waitingFor de claude ; linked : reliée à Remote Control ; shown : la
    question est dépliée dans le fil."""
    permission = waiting_for == PERMISSION
    text = "Il demande une autorisation." if permission else "Il pose une question ou présente un plan."
    if not permission and not shown:
        text += (" Claude Code ne l'écrit souvent dans la conversation qu'une fois répondue : "
                 "pour l'instant, elle n'est visible que dans le terminal.")
    if linked:
        return "Claude attend ta réponse dans son terminal", text + " Réponds-y dans le terminal.", None
    return ("Claude attend ta réponse dans son terminal",
            text + " Sans Remote Control, Claude Hub ne peut pas y répondre.",
            "Pour répondre d'ici les prochaines fois : réponds dans le terminal, puis tapes-y "
            "/remote-control.")


def text_label(text, css=None):
    label = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=60)
    if css:
        label.add_css_class(css)
    return label


CHIP_CLASSES = ("rc-chip", "orphan-chip")


class HeadChip(Gtk.MenuButton):
    """Pastille de l'en-tête, « Sans Remote Control » (show_for) ou « Lancée avant le
    redémarrage » (show_orphan) ; un clic ouvre son explication."""

    def __init__(self):
        super().__init__(visible=False, valign=Gtk.Align.CENTER)
        self.add_css_class("flat")
        inner = Gtk.Box(spacing=4)
        self.icon = Gtk.Image(pixel_size=12)
        self.label = Gtk.Label()
        inner.append(self.icon)
        inner.append(self.label)
        self.set_child(inner)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8,
                           margin_bottom=8, margin_start=10, margin_end=10)
        self.set_popover(Gtk.Popover(child=self.box))
        self.key = None  # pastille affichée : ("rc", hôte) ou ("orphan",)

    def show_for(self, host):
        """« Sans Remote Control », avec l'explication propre à host (où mettre le réglage)."""
        self.show_chip(("rc", host), WARNING_ICON, "rc-chip",
                       "Ce Claude tourne dans un terminal sans Remote Control : Claude Hub ne "
                       "peut ni lui écrire ni répondre à ses demandes", *help_texts(host))

    def show_orphan(self):
        """« Lancée avant le redémarrage » : le claude -p d'un Claude Hub précédent tourne encore."""
        self.show_chip(("orphan",), ORPHAN_ICON, "orphan-chip",
                       "Claude Hub a lancé ce Claude avant de redémarrer et n'est plus relié à "
                       "lui : ton message partira quand il aura fini", *orphan_texts())

    def show_chip(self, key, icon, css, tooltip, title, paragraphs):
        if key != self.key:
            self.key = key
            self.icon.set_from_icon_name(icon)
            self.label.set_label(title)
            self.set_tooltip_text(tooltip)
            for c in CHIP_CLASSES:
                (self.add_css_class if c == css else self.remove_css_class)(c)
            while (child := self.box.get_first_child()):
                self.box.remove(child)
            self.box.append(text_label(title, "card-title"))
            for p in paragraphs:
                self.box.append(text_label(p))
        self.set_visible(True)


class WaitCard(Gtk.Box):
    """Carte du fil : Claude attend une réponse dans son terminal (voir wait_texts)."""

    def __init__(self, waiting_for, linked, shown):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["card", "wait-card"])
        title, text, hint = wait_texts(waiting_for, linked, shown)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Image(icon_name=WARNING_ICON, css_classes=["wait-icon"]))
        row.append(text_label(title, "card-title"))
        self.append(row)
        self.append(text_label(text))
        if hint:
            self.append(text_label(hint, "card-hint"))
