"""Pilotage de claude-hub pour les tests d'interface, à lancer avec tests/piloter.sh.

Un scénario est un générateur qui reçoit la fenêtre (Hub) et cède la main avec `yield` :

    import pilote

    def scenario(w):
        yield pilote.liste_chargee              # attendre une condition (fonction → vrai)
        s = pilote.conversation(titre="Kernel")  # une conversation de la liste
        pilote.ouvrir(s)                         # comme un clic sur sa ligne
        yield 1.5                                # laisser 1,5 s à l'affichage
        pilote.capture("ouverte")                # vraie capture du compositeur
        pilote.verifier("Kernel" in w.title.get_text(), "titre de l'en-tête")

    pilote.lancer(scenario)

`yield` accepte :
- un nombre : attendre autant de secondes ;
- une fonction sans argument : attendre qu'elle renvoie vrai (20 s au plus) ;
- pilote.attendre(fonction, secondes) : idem avec un autre délai.
Une condition jamais remplie, une exception ou le délai total dépassé arrêtent le
scénario en échec. `lancer` quitte avec le code 1 si une vérification a échoué.

Le pilotage passe par les fonctions de la fenêtre : les vrais clics, le survol, le
clavier et les dialogues natifs ne se simulent pas.
"""
import json
import os
import pwd
import subprocess
import sys
import tempfile
import time
import traceback

from claude_hub.app import App
from claude_hub.gtk import Gio, GLib, Gtk

app = None
notifications = []   # id des notifications envoyées par le hub (elles ne partent pas)
_bilan = {"ok": 0, "echecs": 0}
_creees = set()      # conversations créées par le test : les seules où envoyer()
_vrai_home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)  # même avec --faux-home


def dossier():
    """Dossier temporaire du test (créé par piloter.sh) : projets, fichiers de travail."""
    d = os.environ.get("PILOTE_DIR") or tempfile.mkdtemp(prefix="pilote-")
    os.environ["PILOTE_DIR"] = d
    return d


def fenetre():
    """La fenêtre principale (Hub)."""
    if app is None:
        return None
    return app.get_active_window() or next(iter(app.get_windows()), None)


# -- attentes --
class attendre:
    """`yield pilote.attendre(condition, secondes)` : condition() vraie avant le délai."""

    def __init__(self, condition, secondes=20):
        self.condition, self.secondes = condition, secondes


def liste_chargee():
    """Condition : chaque hôte coché a répondu au moins une fois."""
    w = fenetre()
    return bool(w) and not w.loading and all(h in w.host_sessions for h in w.shown_hosts())


def fin_reponse(sid=None):
    """Condition : plus de `claude -p` en cours pour cette conversation (l'affichée par défaut)."""
    def fini():
        w = fenetre()
        s = sid or (w.current or {}).get("id")
        return s is not None and s not in w.streams
    return fini


def demande_en_attente(sid=None):
    """Condition : Claude attend une réponse (autorisation, plan, question)."""
    def attend():
        w = fenetre()
        return bool(w.pending.get(sid or (w.current or {}).get("id")))
    return attend


def cpu(secondes=5):
    """`pct = yield from pilote.cpu(5)` : CPU du processus (en % d'un cœur) pendant ce
    temps. À mesurer quelques secondes après la dernière action : la remise en page
    qui suit un changement compterait sinon."""
    t0, c0 = time.monotonic(), sum(os.times()[:2])
    yield secondes
    pct = 100 * (sum(os.times()[:2]) - c0) / (time.monotonic() - t0)
    print(f"cpu : {pct:.1f} % sur {secondes} s", flush=True)
    return pct


def orphelins():
    """Widgets encore en vie hors de toute fenêtre : ceux qu'un cycle retient après leur
    retrait (voir widgets.weak_handler). Le contenu des sections repliées affichées n'en
    fait pas partie (GTK le détache tant qu'elles sont fermées). Le nombre doit rester
    stable quand on refait le fil ou la liste (changer de conversation, relire
    l'historique, taper dans la recherche)."""
    import gc
    replie, a_voir = set(), list(app.get_windows())
    while a_voir:
        for x in descendants(a_voir.pop()):
            if isinstance(x, Gtk.Expander) and not x.get_expanded() and x.get_child() is not None:
                replie.add(id(x.get_child()))
                a_voir.append(x.get_child())
    gc.collect()
    return [x for x in gc.get_objects() if isinstance(x, Gtk.Widget) and x.get_parent() is None
            and not isinstance(x, Gtk.Root) and id(x) not in replie]


# -- actions --
def conversation(sid=None, titre=None, hote=None, condition=None):
    """Première conversation de la liste qui correspond (titre : morceau du titre ou du
    premier message, sans tenir compte de la casse) ; None sinon."""
    for s in fenetre().sessions:
        if sid and s["id"] != sid:
            continue
        if hote and s.get("host") != hote:
            continue
        if titre and titre.lower() not in " ".join(filter(None, (s.get("title"), s.get("first")))).lower():
            continue
        if condition and not condition(s):
            continue
        return s
    return None


def ouvrir(s):
    """Ouvre une conversation (dict de la liste ou id) comme un clic sur sa ligne."""
    w = fenetre()
    sid = s if isinstance(s, str) else s["id"]
    row = w.rows.get(sid)
    if row is not None:
        w.on_row(None, row)
    else:
        w.open(dict(s))


def nouvelle(dossier_conv=None, hote="local"):
    """Nouvelle conversation (bouton « Nouvelle ») ; renvoie son dict."""
    s = fenetre().start_new(hote, dossier_conv or dossier())
    _creees.add(s["id"])
    return s


def ecrire(texte):
    """Remplace le texte de la zone de saisie."""
    fenetre().input.get_buffer().set_text(texte)


def envoyer(texte=None, mode="auto", force=False):
    """Envoie la saisie (après l'avoir remplacée par texte s'il est donné) ; mode :
    « auto », « now » (interrompt la réponse) ou « queue » (file d'attente).

    Refuse d'écrire à une vraie conversation de l'utilisateur : ouverte dans un
    terminal, le hub transmettrait le texte au vrai Claude (Remote Control). Seules
    les conversations créées par le test (nouvelle) ou celles d'un --faux-home le
    sont ; force=True pour passer outre en connaissance de cause."""
    s = fenetre().current
    faux_home = os.path.realpath(os.environ.get("HOME", "")) != _vrai_home
    if s and not (s.get("new") or s["id"] in _creees or faux_home or force):
        raise RuntimeError(f"envoi refusé : {s['id']} est une vraie conversation (pilote.nouvelle, "
                           "ou --faux-home)")
    if texte is not None:
        ecrire(texte)
    fenetre().send(mode)


def capture(nom):
    """Vraie capture du compositeur (spectacle) ; renvoie son chemin. Laisser d'abord
    GTK redessiner (`yield 0.5`) après un changement."""
    d = os.environ.get("PILOTE_CAPTURES") or os.path.join(dossier(), "captures")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, nom if nom.endswith(".png") else nom + ".png")
    subprocess.run(["spectacle", "-b", "-n", "-f", "-o", path], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"capture : {path}" if os.path.exists(path) else f"capture ratée : {path}", flush=True)
    return path


# -- lecture de l'interface --
def descendants(widget):
    """Tous les widgets sous widget (lui compris), en profondeur."""
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from descendants(child)
        child = child.get_next_sibling()


def chercher(widget=None, type=None, css=None, texte=None, visibles=True):
    """Widgets sous widget (la fenêtre par défaut) d'un type, d'une classe CSS ou dont
    le texte contient `texte`."""
    found = []
    for w in descendants(widget or fenetre()):
        if visibles and not w.is_drawable():
            continue
        if type is not None and not isinstance(w, type):
            continue
        if css is not None and not w.has_css_class(css):
            continue
        if texte is not None and texte not in texte_de(w):
            continue
        found.append(w)
    return found


def texte_de(widget):
    """Texte d'un label, d'un bouton ou d'une zone de texte ("" sinon)."""
    if isinstance(widget, Gtk.Label):
        return widget.get_text()
    if isinstance(widget, Gtk.TextView):
        buf = widget.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    if isinstance(widget, Gtk.Button) and widget.get_label():
        return widget.get_label()
    if isinstance(widget, Gtk.Editable):
        return widget.get_text()
    return ""


def textes(widget=None):
    """Textes visibles sous widget (la fenêtre par défaut), dans l'ordre."""
    return [t for t in (texte_de(w) for w in descendants(widget or fenetre()) if w.is_drawable()) if t]


def defilement():
    """Position du fil des messages : value, upper, page et en_bas."""
    adj = fenetre().scroll.get_vadjustment()
    v, up, page = adj.get_value(), adj.get_upper(), adj.get_page_size()
    return {"value": round(v), "upper": round(up), "page": round(page), "en_bas": v >= up - page - 1}


def journal_claude():
    """Ce que le faux claude a noté : {"argv", "cwd"} par lancement, {"recu"} par ligne
    reçue du hub (messages, réponses aux demandes, requêtes de contrôle)."""
    path = os.environ.get("FAUX_CLAUDE_JOURNAL")
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# -- vérifications et lancement --
def verifier(condition, message):
    """Note une vérification : ✓ ou ✗ dans la sortie, échec du test à la fin si ✗."""
    _bilan["ok" if condition else "echecs"] += 1
    print(f"{'✓' if condition else '✗'} {message}", flush=True)
    return bool(condition)


def lancer(scenario, delai_max=120):
    """Lance une instance séparée de claude-hub et y joue scenario(fenêtre) ; quitte
    l'appli à la fin du scénario, et le processus avec le code 1 en cas d'échec."""
    global app
    app = App()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)  # pas l'instance ouverte chez l'utilisateur
    # Pas de vraie notification : on les garde pour les vérifier.
    app.send_notification = lambda nid, n: notifications.append(nid)
    app.withdraw_notification = lambda nid: None
    debut = time.monotonic()
    etat = {"gen": None, "jusqua": 0.0, "attente": None}

    def echec(message):
        _bilan["echecs"] += 1
        print(f"✗ {message}", flush=True)
        app.quit()
        return False

    def tick():
        if time.monotonic() - debut > delai_max:
            return echec(f"délai total dépassé ({delai_max} s)")
        w = fenetre()
        if w is None:
            return True
        if etat["gen"] is None:
            etat["gen"] = scenario(w)
        try:
            if etat["attente"] is not None:
                cond, limite, quoi = etat["attente"]
                if not cond():
                    if time.monotonic() > limite:
                        return echec(f"condition jamais remplie : {quoi}")
                    return True
                etat["attente"] = None
            if time.monotonic() < etat["jusqua"]:
                return True
            step = next(etat["gen"])
        except StopIteration:
            app.quit()
            return False
        except Exception:  # noqa: BLE001 — l'erreur du scénario est le résultat du test
            traceback.print_exc()
            return echec("exception dans le scénario")
        if isinstance(step, (int, float)):
            etat["jusqua"] = time.monotonic() + step
        elif isinstance(step, attendre):
            etat["attente"] = (step.condition, time.monotonic() + step.secondes,
                               getattr(step.condition, "__name__", repr(step.condition)))
        elif callable(step):
            etat["attente"] = (step, time.monotonic() + 20, getattr(step, "__name__", repr(step)))
        return True

    GLib.timeout_add(100, tick)
    app.run(None)
    print(f"pilote : {_bilan['ok']} vérification(s) réussie(s), {_bilan['echecs']} échec(s)", flush=True)
    sys.exit(1 if _bilan["echecs"] else 0)
