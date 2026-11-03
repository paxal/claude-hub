---
name: piloter-claude-hub
description: Lancer et piloter claude-hub hors écran pour vérifier un changement d'interface — instance séparée sur l'écran virtuel, config temporaire, faux claude qui rejoue un scénario (réponse longue, plan, autorisation, question, sous-agent, erreur), vraies captures, mesure du CPU. À utiliser pour tout test d'affichage ou de comportement de la fenêtre, une capture, ou « teste toi-même ».
---

# Piloter claude-hub hors écran

Trois outils dans `tests/`, à utiliser plutôt que de réécrire un script de pilotage
et un faux `claude` à chaque fois :

| Outil | Rôle |
|---|---|
| `tests/piloter.sh [options] script.py` | config temporaire (thème du bureau compris), faux claude dans le PATH, écran virtuel, vérification que `~/.config/claude-hub` n'a pas bougé, pas de `__pycache__` |
| `tests/pilote.py` | bibliothèque du script : scénario en générateur, attentes, actions, captures, vérifications |
| `tests/faux-claude` | faux `claude -p` qui parle le protocole du hub (stdin ouvert, demandes `can_use_tool`, titre, interruption) et rejoue un scénario |

## Démarrage

Écrire le script dans le scratchpad (pas dans le dépôt) :

```python
import pilote

def scenario(w):                              # w : la fenêtre (Hub de window.py)
    yield pilote.liste_chargee                # attendre une condition
    s = pilote.nouvelle()                     # nouvelle conversation dans PILOTE_DIR
    pilote.envoyer("Fais quelque chose")
    yield pilote.attendre(pilote.fin_reponse(s["id"]), 30)
    yield 1                                   # laisser GTK redessiner avant la capture
    pilote.capture("fin")
    pilote.verifier(any("les tests passent" in t for t in pilote.textes(w.msgs)), "réponse dans le fil")

pilote.lancer(scenario)
```

```bash
FAUX_CLAUDE_SCENARIO=long tests/piloter.sh --faux-home /chemin/du/scratchpad/essai.py
```

La sortie donne les ✓/✗, le chemin des captures (à ouvrir avec Read) et le code de
retour (1 si une vérification échoue, si une condition n'est jamais remplie ou si le
scénario lève une exception).

## Options de piloter.sh

- **(aucune)** : config vide (hôte local seul), vrai `~/.claude` en lecture (tes
  vraies conversations dans la liste), Cursor lu en lecture seule.
- `--faux-home` : HOME temporaire, liste vide. Le faux claude écrit alors une vraie
  transcription, que le hub relit à la fin de la réponse (historique, titre, coût,
  fichiers modifiés). **À préférer pour tout ce qui envoie des messages.**
- `--copie-config` : part d'une copie de la vraie config (hôtes SSH, tags, réglages).
  Les hôtes cochés sont alors interrogés pour de vrai (SSH, cloud).
- `--theme clair` (`Breeze:light`), `--theme adwaita`. Par défaut : Breeze sombre,
  comme le bureau. `GTK_THEME=Breeze` seul reste sombre.
- `--vrai-claude` : vrai `claude` (Haiku, dossier temporaire, conversation supprimée
  ensuite, comme le demande AGENTS.md).
- `--garder` : garde le dossier temporaire (sinon gardé seulement s'il y a des captures).
- `LARGEUR`/`HAUTEUR` : taille de l'écran (1280×800).

## Faux claude

`FAUX_CLAUDE_SCENARIO` : `simple` (défaut), `long` (texte, code, tableau, outils,
TodoWrite, Edit), `plan`, `build` (plan puis exécution : tâches, Edit, Write, autorisation
Bash, réponse finale), `autorisation`, `question`, `sous-agent`, `erreur`, `lent`
(10 étapes d'une seconde : ligne de Claude, « Arrêter », file d'attente), `shell` (Bash
laissé en arrière-plan 6 s : ligne « Shell » au-dessus de la saisie), ou le chemin d'un
JSON avec ses propres étapes (format en tête de `tests/faux-claude`, monitor compris).
La liste d'activité se lit avec `w.activity_panel.labels()` (« libellé · détail »).
`FAUX_CLAUDE_VITESSE=0` supprime les pauses, `2` les double.

Ce que le hub lui a envoyé : `pilote.journal_claude()` (un `{"argv", "cwd"}` par
lancement, un `{"recu": …}` par ligne reçue : messages, réponses aux demandes,
requêtes de contrôle).

## pilote.py en bref

- `yield 1.5` : attendre ; `yield condition` : attendre qu'elle soit vraie (20 s) ;
  `yield pilote.attendre(condition, 60)`.
- Conditions : `liste_chargee`, `fin_reponse(sid)`, `demande_en_attente(sid)`.
- Actions : `conversation(titre=…, sid=…, hote=…, condition=…)`, `ouvrir(s)` (comme
  un clic sur la ligne), `nouvelle(dossier)`, `ecrire(texte)`,
  `envoyer(texte, mode="auto"|"now"|"queue")`.
- Lecture : `chercher(type=Gtk.Button, texte="Autoriser", css=…)`, `textes(widget)`,
  `texte_de(widget)`, `descendants(widget)`, `defilement()`, `notifications`.
- `capture(nom)`, `verifier(condition, message)`, `pct = yield from pilote.cpu(5)`,
  `orphelins()` (widgets retenus hors de la fenêtre).
- Un contrôleur (survol d'une ligne, clic droit sur une vue, clic sur une image) se
  trouve avec `widget.observe_controllers()` (liste à parcourir par `get_item(i)`)
  et se déclenche en émettant son signal : `motion.emit("enter", 1.0, 1.0)`,
  `clic.emit("pressed", 1, 2.0, 2.0)`. Ça teste le gestionnaire, pas le geste.
- Un bouton se « clique » avec `bouton.emit("clicked")` : la carte d'autorisation
  (« Autoriser », « Pour la session », « Refuser ») et celle du plan (« Lancer »,
  « Continuer à planifier », remarque prise dans la saisie) se testent ainsi.

## Recettes

- **Vraie conversation, en lecture** : `pilote.ouvrir(pilote.conversation(titre="…"))`,
  sans option. `envoyer` refuse d'écrire à une vraie conversation : ouverte dans un
  terminal, le hub transmettrait le texte au vrai Claude par Remote Control.
- **CPU au repos** : `yield 4` après la dernière action (la remise en page compte
  sinon), puis `yield from pilote.cpu(5)`. Repère : 1–2 % au repos avec une
  conversation ouverte. Sur l'écran virtuel, le moteur gl est logiciel
  (llvmpipe) : comparer deux versions entre elles plutôt que de lire un chiffre
  absolu ; `GSK_RENDERER=cairo tests/piloter.sh …` pour l'autre moteur.
- **Fuite de widgets** (après un changement du fil, de la liste, d'une carte ou
  d'un popover) : `len(pilote.orphelins())` avant et après avoir refait plusieurs
  fois l'affichage (`ouvrir` d'autres conversations, `w.load_history(full=True)` qui reconstruit le fil,
  `w.search.set_text(…)`). Le nombre doit rester stable ; sinon, chercher qui retient
  ces widgets avec `gc.get_referrers` (voir `widgets.weak_handler`).
- **Défilement** : `pilote.defilement()["en_bas"]` ; mesurer plusieurs fois pendant
  la réponse avec le scénario `long`.
- **Conversation suivie comme dans un terminal** (`--faux-home`) : après une
  réponse, ajouter des lignes à la transcription
  (`~/.claude/projects/*/<id>.jsonl`, enregistrements `user`/`assistant` avec
  `uuid` et `timestamp`) ; le hub la relit dans les 4 s. Le fil est mis à jour sans
  être reconstruit : garder `[t for _, t in w.feed]` avant et vérifier avec `is`
  que ces widgets sont toujours là, puis comparer `pilote.textes(w.msgs)` avec
  celui d'un `w.load_history(full=True)`. Relâcher ces listes avant `orphelins()`.
- **Rendu sur de vraies conversations** (après un changement de `render.py` ou
  `mdview.py`) : boucler sur `w.sessions`, `ouvrir`, `yield 1`, et vérifier
  qu'aucune exception ni avertissement GTK n'apparaît dans la sortie.

## Limites

- Pas de vrais clics, survol, glisser-déposer, raccourcis clavier ni dialogues
  natifs : appeler la fonction qu'ils déclenchent, et le dire dans le compte rendu.
- `spectacle` capture ce que le compositeur affiche : laisser GTK redessiner
  (`yield 0.5`) après un changement. S'il ne répond pas en 15 s (vu le 3 novembre
  2026 sur l'écran virtuel), `capture` se rabat sur un rendu GTK de la fenêtre, et
  le dit : bon pour la mise en page, pas pour les défauts de rafraîchissement.
- Si piloter.sh signale que `~/.config/claude-hub` a changé, c'est en général
  l'instance ouverte de l'utilisateur (le message le dit) ; vérifier que les id du
  test n'y sont pas.
