"""Demandes de Claude pendant une réponse : validation du plan, questions, permissions.

claude -p est lancé avec --permission-prompt-tool stdio : chaque demande arrive sur
sa sortie en control_request « can_use_tool » et attend une control_response sur
son entrée (voir docs/mode-plan-et-sous-agents.md). Ce module construit les cartes
affichées dans le fil ; la fenêtre se charge d'envoyer les réponses.
"""
import json

from .gtk import Gdk, Gtk, Pango
from .hosts import MODE_LABELS
from .render import code_block, esc, file_href, lang_for, make_label, plan_widget, show_plan_decision
from .widgets import WrapBox, weak_handler

# Modes proposés pour exécuter un plan validé (le premier est le choix par défaut).
EXEC_MODES = [(m, MODE_LABELS[m]) for m in ("acceptEdits", "auto", "default", "bypassPermissions")]
CODE_LINES = 40  # lignes montrées du contenu d'un Write ou d'un Edit


def allow(request, updated_input=None, permissions=None):
    resp = {"behavior": "allow",
            "updatedInput": updated_input if updated_input is not None else request.get("input") or {}}
    if permissions:
        resp["updatedPermissions"] = permissions
    return resp


def deny(message):
    return {"behavior": "deny", "message": message}


def control_response(request_id, response):
    """Ligne à écrire sur l'entrée de claude pour répondre à une demande."""
    return json.dumps({"type": "control_response", "response": {
        "subtype": "success", "request_id": request_id, "response": response}}) + "\n"


def session_only(suggestions):
    """Suggestions de claude ramenées à la session : il ne doit jamais écrire
    dans ses fichiers de réglages à cause de nous."""
    return [dict(s, destination="session") for s in suggestions or []
            if isinstance(s, dict) and s.get("type") in ("setMode", "addRules")]


def suggestion_text(suggestions):
    parts = []
    for s in suggestions:
        if s.get("type") == "setMode":
            parts.append(f"passer en mode {s.get('mode')}")
        else:
            rules = ", ".join(f"{r.get('toolName')}({r.get('ruleContent')})" if r.get("ruleContent")
                              else str(r.get("toolName")) for r in s.get("rules") or [])
            parts.append(f"autoriser {rules}")
    return " ; ".join(parts)


def plan_allow(request, mode):
    """Validation d'un plan : (réponse, résumé, mode) ; claude passe dans `mode`."""
    label = MODE_LABELS.get(mode, mode)
    perms = [{"type": "setMode", "mode": mode, "destination": "session"}]
    return allow(request, permissions=perms), f"✓ Plan validé : exécution ({label.lower()})", mode


def plan_deny(message):
    """Refus d'un plan : (réponse, résumé) ; `message` est transmis à Claude."""
    return deny(message), f"✗ Plan refusé : {message}"


PLAN_REFUSAL = "Continue à planifier : le plan n'est pas encore validé."


def decision_of(response):
    """Décision lue dans une réponse au plan (render.show_plan_decision) : validé, avec le
    mode d'exécution transmis, ou refusé, avec la remarque."""
    if response.get("behavior") == "allow":
        mode = next((p.get("mode") for p in response.get("updatedPermissions") or []
                     if isinstance(p, dict) and p.get("type") == "setMode"), None)
        return {"ok": True, "mode": mode, "text": ""}
    return {"ok": False, "text": response.get("message") or ""}


def mode_row():
    """Choix du mode d'exécution d'un plan, sur sa ligne : (ligne, liste déroulante)."""
    dd = Gtk.DropDown.new_from_strings([label for _m, label in EXEC_MODES])
    dd.set_tooltip_text("Mode de permissions pour l'exécution du plan")
    row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
    row.append(Gtk.Label(label="Exécution :", css_classes=["sid"]))
    row.append(dd)
    return row, dd


def clip(text, n=CODE_LINES):
    lines = str(text).split("\n")
    return "\n".join(lines[:n]) + (f"\n… ({len(lines) - n} lignes de plus)" if len(lines) > n else "")


# Réponse envoyée par « En discuter » quand la saisie est vide.
QUESTION_CHAT = ("L'utilisateur préfère en discuter avant de répondre : demande-lui ce qu'il "
                 "veut préciser, puis repose les questions si besoin.")


def parse_questions(inp):
    """Questions d'un AskUserQuestion : [{text, header, multi, options: [(libellé,
    description)]}], sans les entrées mal formées."""
    out = []
    for q in inp.get("questions") if isinstance(inp.get("questions"), list) else []:
        if not isinstance(q, dict):
            continue
        options = []
        for opt in q.get("options") if isinstance(q.get("options"), list) else []:
            opt = opt if isinstance(opt, dict) else {"label": str(opt)}
            options.append((str(opt.get("label") or ""), str(opt.get("description") or "")))
        out.append({"text": str(q.get("question") or ""), "header": str(q.get("header") or ""),
                    "multi": bool(q.get("multiSelect")), "options": options})
    return out


def answer_text(picked, other):
    """Réponse à une question, telle que Claude l'attend : les choix cochés, puis la réponse
    libre, séparés par des virgules ; vide sans réponse."""
    return ", ".join(list(picked) + ([other.strip()] if other.strip() else []))


def tab_label(q, answered, n):
    return f"{'☑' if answered else '☐'} {q['header'] or f'Question {n}'}"


def option_number(n):
    """Numéro d'un choix (touche du clavier qui le choisit)."""
    return Gtk.Label(label=f"{n}.", css_classes=["qnum"], xalign=1, width_chars=2,
                     valign=Gtk.Align.START)


def wrapped(text, css):
    """Texte d'un choix : pas sélectionnable (il prendrait le clic du bouton)."""
    return Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                     css_classes=[css])


def fixed_label(markup, css):
    """Texte d'une page de la carte, pas sélectionnable : GTK y donnerait la main à
    l'étiquette en changeant d'onglet, et tout son texte apparaîtrait sélectionné."""
    label = make_label(markup, css)
    label.set_selectable(False)
    return label


def key_digit(ctl, keyval, code):
    """Chiffre de 1 à 9 d'une touche, en AZERTY aussi (le chiffre y est sur Maj) ; 0 sinon."""
    char = chr(Gdk.keyval_to_unicode(keyval) or 0)
    if not ("1" <= char <= "9"):
        event = ctl.get_current_event()
        layout = event.get_layout() if isinstance(event, Gdk.KeyEvent) else 0
        ok, shifted, *_ = ctl.get_widget().get_display().translate_key(
            code, Gdk.ModifierType.SHIFT_MASK, layout)
        char = chr(Gdk.keyval_to_unicode(shifted) or 0) if ok else ""
    return int(char) if "1" <= char <= "9" else 0


class RequestCard(Gtk.Box):
    """Carte d'une demande can_use_tool.

    respond(card, response, summary, mode=None) envoie la réponse (mode : mode
    d'exécution choisi en validant un plan) ; feedback() renvoie le texte de la
    saisie, qui sert de remarque en cas de refus.
    """

    def __init__(self, request_id, request, respond, feedback):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card"])
        self.request_id, self.request = request_id, request
        self.respond, self.feedback = respond, feedback
        self.tool = request.get("tool_name") or "?"
        self.input = request.get("input") if isinstance(request.get("input"), dict) else {}
        if self.tool == "ExitPlanMode":
            self.build_plan()
        elif self.tool == "AskUserQuestion" and parse_questions(self.input):
            self.build_questions()
        else:
            self.build_tool()

    # -- éléments communs --
    def header(self, text, extra=None):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=text, xalign=0, hexpand=True, css_classes=["card-title"],
                             wrap=True))
        if extra:
            row.append(extra)
        self.append(row)

    def buttons(self, *specs):
        """specs : (libellé, infobulle, action, css), action étant une méthode de la carte ;
        renvoie la rangée."""
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        for label, tip, action, css in specs:
            b = Gtk.Button(label=label, tooltip_text=tip, css_classes=[css] if css else [])
            b.connect("clicked", weak_handler(self, lambda card, _b, f=action.__func__: f(card)))
            row.append(b)
        self.append(row)
        return row

    def reason(self, default):
        text = self.feedback()
        return text or default

    def done(self, summary, ok=True, response=None):
        """Remplace la carte par une ligne qui résume la décision (response : la réponse
        envoyée à Claude, s'il y en a une). Un plan reste dans le fil, comme dans
        l'historique : ses actions laissent la place à la décision."""
        if self.tool == "ExitPlanMode" and hasattr(self, "plan_head"):
            return self.plan_done(summary, ok, response)
        while (child := self.get_first_child()):
            self.remove(child)
        if hasattr(self, "keys"):  # questions : plus de choix au clavier, ni de widgets retenus
            self.remove_controller(self.keys)
            del self.keys
            self.qs, self.tabs, self.stack, self.recap = [], [], None, None
            self.send_button = self.recap_hint = None
        self.set_css_classes(["card", "done", "ok" if ok else "refused"])
        self.append(Gtk.Label(label=summary, xalign=0, wrap=True, ellipsize=Pango.EllipsizeMode.END,
                              lines=2))

    def send(self, response, summary, mode=None):
        self.respond(self, response, summary, mode)

    # -- plan --
    def build_plan(self):
        plan_widget(self.input.get("plan") or "", self.input.get("planFilePath"), self,
                    plan_id=self.request.get("tool_use_id"))
        row = self.buttons(
            ("Continuer à planifier", "Refuser le plan : le texte de la saisie est transmis "
             "à Claude comme remarque", self.plan_refuse, None),
            ("Lancer", "Valider le plan : Claude commence l'exécution", self.plan_run,
             "suggested-action"))
        modes, self.mode_dd = mode_row()
        # Sur sa propre ligne : à côté des boutons, la carte serait trop large quand le
        # plan est ouvert dans le volet.
        self.insert_child_after(modes, row.get_prev_sibling())

    def plan_done(self, summary, ok, response):
        if getattr(self, "decided", False):
            return
        self.decided = True
        child = self.plan_body.get_next_sibling()
        while child:  # choix du mode et boutons
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        if response is not None:
            decision = decision_of(response)
        else:  # demande expirée ou annulée : pas de réponse partie d'ici
            decision = {"ok": ok, "label": summary.rstrip(".").split(" : ")[0], "text": ""}
        decision["tip"] = summary
        show_plan_decision(self, decision)

    def plan_run(self):
        self.send(*plan_allow(self.request, EXEC_MODES[self.mode_dd.get_selected()][0]))

    def plan_refuse(self):
        self.send(*plan_deny(self.reason(PLAN_REFUSAL)))

    # -- questions --
    # Comme dans le terminal de Claude Code : un onglet par question, coché une fois
    # répondue, puis « Envoyer », qui récapitule les réponses (s'il y a plusieurs
    # questions). Choix numérotés (touches 1 à 9, ← → pour changer de question), réponse
    # libre en dernier ; « En discuter » laisse les questions sans réponse.
    def build_questions(self):
        self.add_css_class("ask")  # pas « question » : Breeze en fait une barre d'info orange
        self.qs = parse_questions(self.input)
        single = len(self.qs) == 1
        bar = WrapBox(spacing=4)
        bar.add_css_class("qtabs")
        bar.append(Gtk.Label(label="❓", tooltip_text="Claude te pose une question",
                             valign=Gtk.Align.CENTER))
        self.stack = Gtk.Stack()  # même hauteur d'un onglet à l'autre : rien ne bouge sous la souris
        self.tabs = []
        for i, q in enumerate(self.qs):
            q["picked"] = []
            self.add_tab(bar, tab_label(q, False, i + 1), q["text"] or None)
            self.stack.add_child(self.question_page(i, q, single))
        self.recap = None
        if not single:
            self.add_tab(bar, "✔ Envoyer", "Relire les réponses et les envoyer à Claude")
            self.stack.add_child(self.recap_page())
        self.append(bar)
        self.append(self.stack)
        self.append(Gtk.Separator())
        chat = self.buttons(("En discuter", "Ne pas répondre : Claude reçoit le texte de la saisie, "
                             "ou, saisie vide, la demande d'en discuter", self.question_refuse,
                             "flat"))
        chat.set_halign(Gtk.Align.START)
        self.keys = Gtk.EventControllerKey()
        self.keys.connect("key-pressed", weak_handler(self, RequestCard.on_question_key))
        self.add_controller(self.keys)
        self.refresh_answers()
        self.tabs[0].set_active(True)

    def add_tab(self, bar, label, tip):
        tab = Gtk.ToggleButton(label=label, tooltip_text=tip)
        tab.add_css_class("flat")
        if self.tabs:
            tab.set_group(self.tabs[0])
        n = len(self.tabs)
        tab.connect("toggled", weak_handler(self, lambda card, t: t.get_active() and card.on_tab(n)))
        bar.append(tab)
        self.tabs.append(tab)

    def question_page(self, i, q, single):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["qpage"])
        page.append(fixed_label(f"<b>{esc(q['text'])}</b>", "card-question"))
        q["buttons"] = []
        for n, (label, desc) in enumerate(q["options"], 1):
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            text.append(wrapped(label, "qlabel"))
            if desc:
                text.append(wrapped(desc, "sid"))
            row = Gtk.Box(spacing=8)
            row.append(option_number(n))
            row.append(text)
            if q["multi"]:
                b = Gtk.CheckButton(child=row)
                b.connect("toggled", weak_handler(self, lambda card, _b, i=i: card.on_multi(i)))
            else:
                b = Gtk.Button(child=row)
                b.add_css_class("flat")
                b.mark = Gtk.Label(label="✓", css_classes=["qmark"], valign=Gtk.Align.CENTER,
                                   visible=False)
                row.append(b.mark)
                b.connect("clicked", weak_handler(
                    self, lambda card, _b, i=i, label=label: card.choose(i, label)))
            b.add_css_class("qopt")
            b.answer = label
            page.append(b)
            q["buttons"].append(b)
        row = Gtk.Box(spacing=8, css_classes=["qother"])
        row.append(option_number(len(q["options"]) + 1))
        q["other"] = Gtk.Entry(hexpand=True, placeholder_text="Autre réponse… (Entrée pour valider)")
        q["other"].connect("changed", weak_handler(self, lambda card, _e, i=i: card.on_other(i)))
        q["other"].connect("activate", weak_handler(self, lambda card, _e, i=i: card.next_question(i)))
        row.append(q["other"])
        page.append(row)
        if q["multi"]:  # plusieurs choix : rien ne part au clic
            row = Gtk.Box(halign=Gtk.Align.END, margin_top=4)
            b = Gtk.Button(label="Envoyer" if single else "Suivant", css_classes=["suggested-action"])
            b.connect("clicked", weak_handler(self, lambda card, _b, i=i: card.next_question(i)))
            row.append(b)
            page.append(row)
        return page

    def recap_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["qpage"])
        self.recap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.append(self.recap)
        row = Gtk.Box(halign=Gtk.Align.END, spacing=8)
        self.recap_hint = Gtk.Label(label="Il reste des questions sans réponse.", css_classes=["sid"])
        self.send_button = Gtk.Button(label="Envoyer", css_classes=["suggested-action"])
        self.send_button.connect("clicked", weak_handler(self, lambda card, _b: card.answer()))
        row.append(self.recap_hint)
        row.append(self.send_button)
        page.append(row)
        return page

    def answer_of(self, i):
        q = self.qs[i]
        return answer_text(q["picked"], q["other"].get_text())

    def refresh_answers(self):
        """Onglets cochés, choix marqués et récapitulatif, d'après les réponses données."""
        missing = False
        for i, q in enumerate(self.qs):
            ans = self.answer_of(i)
            missing |= not ans
            self.tabs[i].set_label(tab_label(q, bool(ans), i + 1))
            (self.tabs[i].add_css_class if ans else self.tabs[i].remove_css_class)("answered")
            for b in q["buttons"] if not q["multi"] else []:
                b.mark.set_visible(b.answer in q["picked"])
                (b.add_css_class if b.answer in q["picked"] else b.remove_css_class)("chosen")
        if self.recap is None:
            return
        while (child := self.recap.get_first_child()):
            self.recap.remove(child)
        for i, q in enumerate(self.qs):
            ans = self.answer_of(i)
            head = f"<b>{esc(q['header'])}</b> · " if q["header"] else ""
            self.recap.append(fixed_label(head + esc(q["text"]), "card-question"))
            reply = fixed_label("→ " + esc(ans) if ans else "<i>pas de réponse</i>", "qanswer")
            if not ans:
                reply.add_css_class("missing")
            self.recap.append(reply)
        self.send_button.set_sensitive(not missing)
        self.recap_hint.set_visible(missing)

    def current_tab(self):
        return next((i for i, t in enumerate(self.tabs) if t.get_active()), 0)

    def show_tab(self, i):
        i = max(0, min(i, len(self.tabs) - 1))
        if self.tabs[i].get_active():
            self.on_tab(i)
        else:
            self.tabs[i].set_active(True)  # → on_tab

    def on_tab(self, i):
        """Onglet choisi : sa page ; si l'on était dans la carte (clic, clavier), le premier
        choix de la page prend la main, pour continuer au clavier."""
        root = self.get_root()
        focus = root.get_focus() if root else None
        inside = focus is not None and (focus is self or focus.is_ancestor(self))
        old, page = self.stack.get_visible_child(), self.stack.get_pages().get_item(i).get_child()
        if old is not None and old is not page:
            # Fond de la page différent de celui de la précédente (de très peu) : GTK repeint
            # alors toute la page. Sinon il ne repeint que les textes, et le haut des lettres
            # de la page précédente reste à l'écran.
            (page.remove_css_class if old.has_css_class("alt") else page.add_css_class)("alt")
        self.stack.set_visible_child(page)
        if not inside:
            return
        if i == len(self.qs):
            target = self.send_button
        else:
            q = self.qs[i]
            target = next((b for b in q["buttons"] if b.answer in q["picked"]),
                          q["other"] if q["other"].get_text().strip() or not q["buttons"]
                          else q["buttons"][0])
        target.grab_focus()

    def choose(self, i, label):
        """Choix unique cliqué : la question est répondue, on passe à la suivante."""
        q = self.qs[i]
        q["picked"] = [label]
        q["other"].set_text("")  # l'un ou l'autre, comme dans le terminal
        self.refresh_answers()
        self.advance(i)

    def on_multi(self, i):
        q = self.qs[i]
        q["picked"] = [b.answer for b in q["buttons"] if b.get_active()]
        self.refresh_answers()

    def on_other(self, i):
        q = self.qs[i]
        if not q["multi"] and q["other"].get_text().strip():
            q["picked"] = []
        self.refresh_answers()

    def next_question(self, i):
        """Entrée dans la réponse libre, ou « Suivant » : la suivante, si celle-ci a une réponse."""
        if not self.answer_of(i):
            return self.qs[i]["other"].grab_focus()
        self.advance(i)

    def advance(self, i):
        """Question i répondue : une question seule part aussitôt ; sinon la suivante, puis
        le récapitulatif."""
        if len(self.qs) == 1:
            return self.answer()
        self.show_tab(i + 1)

    def on_question_key(self, ctl, keyval, code, state):
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
                    | Gdk.ModifierType.SUPER_MASK):
            return False
        root = self.get_root()
        if isinstance(root.get_focus() if root else None, Gtk.Editable):
            return False  # réponse libre en cours de saisie : les touches sont à elle
        i = self.current_tab()
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.show_tab(i + (1 if keyval == Gdk.KEY_Right else -1))
            return True
        n = key_digit(ctl, keyval, code)
        if not n or i >= len(self.qs):
            return False
        q = self.qs[i]
        if n <= len(q["buttons"]):
            b = q["buttons"][n - 1]
            if q["multi"]:
                b.set_active(not b.get_active())
                b.grab_focus()
            else:
                self.choose(i, b.answer)
            return True
        if n == len(q["buttons"]) + 1:
            q["other"].grab_focus()
            return True
        return False

    def answer(self):
        answers = {}
        for i, q in enumerate(self.qs):
            ans = self.answer_of(i)
            if not ans:
                return self.show_tab(i)  # toutes les questions doivent avoir une réponse
            answers[q["text"]] = ans
        inp = dict(self.input, answers=answers)
        self.send(allow(self.request, inp), "✓ Réponse : " + " · ".join(answers.values()))

    def question_refuse(self):
        msg = self.reason(QUESTION_CHAT)
        self.send(deny(msg), f"✗ Sans réponse : {msg}")

    # -- autres outils --
    def build_tool(self):
        name = self.request.get("display_name") or self.tool
        self.header(f"🔐 Claude demande à utiliser {name}")
        desc = self.request.get("description")
        if desc and desc != self.input.get("command"):
            self.append(Gtk.Label(label=str(desc), xalign=0, wrap=True, css_classes=["sid"]))
        self.append(self.tool_preview())
        if self.request.get("decision_reason"):
            self.append(Gtk.Label(label=str(self.request["decision_reason"]), xalign=0, wrap=True,
                                  css_classes=["sid"]))
        self.suggestions = session_only(self.request.get("permission_suggestions"))
        specs = [("Refuser", "Le texte de la saisie est transmis à Claude comme consigne",
                  self.tool_refuse, None)]
        if self.suggestions:
            specs.append(("Pour la session", "Autoriser et " + suggestion_text(self.suggestions)
                          + " (jusqu'à la fin de cette réponse)", self.tool_session, None))
        specs.append(("Autoriser", None, self.tool_allow, "suggested-action"))
        self.buttons(*specs)

    def tool_preview(self):
        inp = self.input
        path = inp.get("file_path") or inp.get("notebook_path")
        if self.tool == "Bash" and inp.get("command"):
            return code_block(str(inp["command"]), "bash")
        if path and "content" in inp:
            return self.with_path(path, code_block(clip(inp["content"]), lang_for(str(path))))
        if path and ("old_string" in inp or "edits" in inp):
            edits = inp.get("edits") if isinstance(inp.get("edits"), list) else [inp]
            diff = []
            for e in edits:
                e = e if isinstance(e, dict) else {}
                diff += ["- " + x for x in str(e.get("old_string", "")).split("\n")]
                diff += ["+ " + x for x in str(e.get("new_string", "")).split("\n")]
            return self.with_path(path, code_block(clip("\n".join(diff)), "diff"))
        return code_block(clip(json.dumps(inp, ensure_ascii=False, indent=2)), "json")

    @staticmethod
    def with_path(path, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(make_label(f'<a href="{esc(file_href(str(path)))}">{esc(str(path))}</a>', "sid"))
        box.append(widget)
        return box

    def tool_allow(self):
        self.send(allow(self.request), f"✓ {self.tool} autorisé")

    def tool_session(self):
        self.send(allow(self.request, permissions=self.suggestions),
                  f"✓ {self.tool} autorisé ({suggestion_text(self.suggestions)})")

    def tool_refuse(self):
        msg = self.reason("Refusé par l'utilisateur.")
        self.send(deny(msg), f"✗ {self.tool} refusé : {msg}", None)
