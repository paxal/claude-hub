"""Contexte et coût d'une conversation, affichés dans l'en-tête.

Le contexte est la taille du dernier message de Claude (jetons d'entrée, cache
compris, et de sortie), lue dans la transcription ou en direct dans le flux de
claude -p ; sa part de la fenêtre du modèle dit quand faire /compact. La fenêtre de
chaque modèle est apprise de claude (modelUsage.contextWindow, à la fin d'une
réponse lancée d'ici) ; avant, elle est supposée. Le coût est celui de la dernière
réponse lancée depuis Claude Hub (total_cost_usd de claude -p).
"""
import re

from .gtk import Gtk

COSTS_KEPT = 1000           # conversations dont on garde le coût de la dernière réponse
WARN, FULL = 0.6, 0.85      # parts du contexte : orange au-delà de WARN, rouge au-delà de FULL


def context_tokens(usage):
    """Taille du contexte après un message de Claude (cf. usage_tokens des scripts distants)."""
    if not isinstance(usage, dict):
        return 0
    return sum(usage[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                  "output_tokens") if type(usage.get(k)) is int)


def context_window(model, learned):
    """(taille de la fenêtre du modèle, vrai si elle vient de claude). Faute de mieux :
    1 M pour un modèle « [1m] » et pour les modèles 5 et suivants (Haiku 5.5 annonce
    1 000 000 et des conversations Opus 5.5 dépassent 400 000 jetons), 200 000 sinon."""
    if not model:
        return None, False
    if learned.get(model):
        return learned[model], True
    if model.endswith("[1m]"):
        return 1_000_000, False
    m = re.match(r"claude-[a-z]+-(\d+)", model)
    return (1_000_000 if m and int(m.group(1)) >= 5 else 200_000), False


def learn_windows(learned, model_usage):
    """Retient la fenêtre de chaque modèle d'un modelUsage ; vrai si quelque chose a changé."""
    changed = False
    for model, u in (model_usage or {}).items():
        n = u.get("contextWindow") if isinstance(u, dict) else None
        if isinstance(n, int) and n > 0 and learned.get(model) != n:
            learned[model] = n
            changed = True
    return changed


def remember_cost(costs, sid, cost):
    """Garde le coût de la dernière réponse de sid (les plus anciennes conversations partent d'abord)."""
    if not isinstance(cost, (int, float)) or cost <= 0:
        return False
    costs.pop(sid, None)
    costs[sid] = round(float(cost), 6)
    for old in list(costs)[:-COSTS_KEPT]:
        del costs[old]
    return True


def cost_text(usd):
    """0.1234 -> « 0,12 $ » ; les petits montants gardent un chiffre significatif."""
    if usd >= 0.1:
        text = f"{usd:.2f}"
    elif usd >= 0.001:
        text = f"{usd:.3f}"
    else:
        return "< 0,001 $"
    return text.replace(".", ",") + " $"


class UsageBox(Gtk.Box):
    """« · ▮▮▮▯ 62 % · 0,12 $ » dans l'en-tête ; caché quand on ne sait rien."""

    def __init__(self):
        super().__init__(spacing=8, visible=False)
        self.add_css_class("usage")
        self.lead = Gtk.Label(label="·")
        self.context = Gtk.Box(spacing=5)
        self.bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER)
        self.bar.add_css_class("context")
        for name in ("low", "high", "full"):  # repères par défaut (batterie : bas = alerte)
            self.bar.remove_offset_value(name)
        self.bar.add_offset_value("ok", WARN)
        self.bar.add_offset_value("warn", FULL)
        self.bar.add_offset_value("full", 1.0)
        self.percent = Gtk.Label(css_classes=["context-text"])
        self.context.append(self.bar)
        self.context.append(self.percent)
        self.mid = Gtk.Label(label="·")
        self.cost = Gtk.Label(css_classes=["cost"])
        for w in (self.lead, self.context, self.mid, self.cost):
            self.append(w)

    def show(self, context, learned, cost):
        """context : {"tokens", "model"} ou None ; learned : fenêtres apprises ; cost : $ ou None."""
        tokens = (context or {}).get("tokens")
        window, sure = context_window((context or {}).get("model"), learned)
        has_context = bool(tokens and window)
        if has_context:
            part = tokens / window
            self.bar.set_value(min(part, 1.0))
            self.percent.set_text(f"{round(part * 100)} %")
            level = "full" if part > FULL else "warn" if part > WARN else "ok"
            self.percent.set_css_classes(["context-text", level])
            tip = (f"Contexte : {tokens:,} jetons sur {window:,}".replace(",", " ")
                   + ("" if sure else " (taille de la fenêtre supposée)"))
            if part > WARN:
                tip += "\n/compact pour le réduire"
            self.context.set_tooltip_text(tip)
        self.context.set_visible(has_context)
        has_cost = isinstance(cost, (int, float)) and cost > 0
        if has_cost:
            self.cost.set_text(cost_text(cost))
            self.cost.set_tooltip_text(f"Coût de la dernière réponse lancée depuis Claude Hub : "
                                       f"{cost:.4f} $".replace(".", ","))
        self.cost.set_visible(has_cost)
        self.mid.set_visible(has_context and has_cost)
        self.set_visible(has_context or has_cost)
