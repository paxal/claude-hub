"""Demandes de Claude pendant une réponse : validation du plan, questions, permissions.

claude -p est lancé avec --permission-prompt-tool stdio : chaque demande arrive sur
sa sortie en control_request « can_use_tool » et attend une control_response sur
son entrée (voir docs/mode-plan-et-sous-agents.md). Ce module construit les cartes
affichées dans le fil ; la fenêtre se charge d'envoyer les réponses.
"""
import json

from .gtk import Gtk, Pango
from .hosts import MODE_LABELS
from .render import code_block, esc, file_href, lang_for, make_label, plan_widget, show_plan_decision
from .widgets import weak_handler

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
        elif self.tool == "AskUserQuestion" and isinstance(self.input.get("questions"), list):
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
        self.set_css_classes(["card", "done", "ok" if ok else "refused"])
        self.append(Gtk.Label(label=summary, xalign=0, wrap=True, ellipsize=Pango.EllipsizeMode.END,
                              lines=2))

    def send(self, response, summary, mode=None):
        self.respond(self, response, summary, mode)

    # -- plan --
    def build_plan(self):
        plan_widget(self.input.get("plan") or "", self.input.get("planFilePath"), self)
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
    def build_questions(self):
        self.add_css_class("question")
        self.header("❓ Claude te pose une question")
        self.answers = []  # (question, [boutons], champ libre)
        for q in self.input["questions"]:
            if not isinstance(q, dict):
                continue
            text = str(q.get("question") or "")
            head = f"<b>{esc(str(q['header']))}</b> · " if q.get("header") else ""
            self.append(make_label(head + esc(text), "card-question"))
            group, btns = None, []
            for opt in q.get("options") or []:
                opt = opt if isinstance(opt, dict) else {"label": str(opt)}
                label = str(opt.get("label") or "")
                if q.get("multiSelect"):
                    b = Gtk.CheckButton(label=label)
                else:
                    b = Gtk.CheckButton(label=label, group=group)
                    group = group or b
                b.set_tooltip_text(opt.get("description") or None)
                b.answer = label
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                box.append(b)
                if opt.get("description"):
                    box.append(Gtk.Label(label=str(opt["description"]), xalign=0, wrap=True,
                                         css_classes=["sid"], margin_start=28))
                self.append(box)
                btns.append(b)
            other = Gtk.Entry(placeholder_text="Autre réponse…")
            other.connect("activate", weak_handler(self, lambda card, _e: card.answer()))
            self.append(other)
            self.answers.append((text, btns, other))
        self.buttons(("Ne pas répondre", "Refuser la question : le texte de la saisie est "
                      "transmis à Claude", self.question_refuse, None),
                     ("Répondre", None, self.answer, "suggested-action"))

    def answer(self):
        answers = {}
        for text, btns, other in self.answers:
            chosen = [b.answer for b in btns if b.get_active()]
            if other.get_text().strip():
                chosen.append(other.get_text().strip())
            if not chosen:
                return other.grab_focus()  # toutes les questions doivent avoir une réponse
            answers[text] = ", ".join(chosen)
        inp = dict(self.input, answers=answers)
        self.send(allow(self.request, inp), "✓ Réponse : " + " · ".join(answers.values()))

    def question_refuse(self):
        msg = self.reason("L'utilisateur n'a pas répondu à la question.")
        self.send(deny(msg), f"✗ Question refusée : {msg}")

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
