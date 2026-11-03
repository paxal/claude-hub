# Développement

← [README](../README.md)

Consignes pour les agents (et ce qu'on a appris en développant) :
[`AGENTS.md`](../AGENTS.md). Protocole de contrôle de `claude -p` (mode plan,
permissions, sous-agents), essais à l'appui :
[`mode-plan-et-sous-agents.md`](mode-plan-et-sous-agents.md).

## Tests

```sh
make                        # tous les tests unitaires
make test T=test_tags.py    # un seul fichier (motif accepté : T='test_t*.py')
make test V=1               # nom de chaque test
make clean                  # supprime __pycache__, build/, *.egg-info
```

`make test` revient à `python3 -m unittest discover -s tests`, sans écrire de
`__pycache__`. Les tests qui comparent `helper.py` et `helper.js` demandent
`node`.

GitHub Actions lance les mêmes tests à chaque push sur `master` et à chaque pull
request (`.github/workflows/tests.yml`), dans un conteneur Ubuntu 26.04 avec
GTK 4, PyGObject et node pris dans les paquets de la distribution. Les tests
d'interface (écran virtuel, `piloter.sh`) n'y tournent pas.

Les tests d'interface tournent hors écran, sur une KWin virtuelle :
`tests/ecran-virtuel.sh commande…` (captures avec `spectacle -b -n -f -o …`).

Pour piloter l'appli elle-même : `tests/piloter.sh script.py` lance une instance
séparée sur l'écran virtuel, avec une config temporaire et un faux `claude`
(`tests/faux-claude`) qui rejoue un scénario (réponse longue, plan, autorisation,
question, sous-agent, erreur). Le script s'écrit avec `tests/pilote.py`. Mode
d'emploi dans `.claude/skills/piloter-claude-hub/SKILL.md`.

## Branches et pull requests

Les changements de code se font sur une branche, dans un worktree rangé dans
`.claude/worktrees/` (ignoré par git), puis passent par une pull request : la CI
y lance les tests, et le merge se fait en squash (un commit par changement sur
`master`), après quoi GitHub supprime la branche. Les retouches de doc seules
restent sur `master`. Marche à suivre pour les agents :
`.claude/skills/branche-et-pr/SKILL.md`.

## Organisation du code

| Module | Rôle |
|---|---|
| `gtk.py` | initialisation de GTK 4, `in_thread`, modificateur des raccourcis (Ctrl, ou Cmd sous macOS) |
| `hosts.py` | configuration, hôtes local/SSH, exécution des scripts distants |
| `remote/helper.py`, `remote/helper.js` | scripts envoyés aux hôtes (`python3 -` ou `node -`) pour lire `~/.claude` et les notes ; bibliothèque standard uniquement |
| `cursor.py` | conversations Cursor (base `state.vscdb`, Linux ou macOS ; recherche) |
| `cloud.py` | sessions claude.ai/code et agents cloud Cursor ; jeton lu dans `~/.claude` ou le trousseau macOS |
| `render.py` | Markdown en ligne, coloration, images, chemins cliquables, cartes des messages, ligne d'un appel d'outil, CSS (et teintes du thème sombre) |
| `widgets.py` | widgets communs : colonne de largeur limitée (`Clamp`, qui peut s'élargir à droite pour la liste d'activité), pastilles renvoyées à la ligne (`WrapBox`), bouton à choix (modèle, effort, mode, hôte), pastille d'hôte, texte indicatif de la saisie, gestionnaire de signal qui ne retient pas son widget (`weak_handler`) |
| `listrow.py` | lignes de la liste (icône d'état, heure, boutons du survol), séparateurs de jour |
| `feedtime.py` | heure des éléments du fil (colonne à droite) et séparateurs de jour du fil |
| `feedsync.py` | relecture de l'historique : premier élément du fil à refaire, pour n'ajouter que le nouveau au lieu de tout reconstruire |
| `switcher.py` | sélecteur rapide Ctrl+K et sa recherche approximative |
| `views.py` | vues enregistrées (pastilles sous la recherche) |
| `changes.py` | fichiers modifiés : diffs (claude ou recalculés), regroupement par fichier, volet |
| `usage.py` | contexte rempli (jauge) et coût de la dernière réponse, dans l'en-tête |
| `launcher.py` | nombre de conversations à traiter sur l'icône de la barre des tâches (D-Bus) |
| `home.py` | accueil (à traiter, en cours) et choix du dossier d'une nouvelle conversation |
| `mdview.py` | Markdown d'un message dans un seul `TextView`, pour que la sélection traverse paragraphes, listes et code (tableaux copiés en Markdown), menu du clic droit sur un lien |
| `attachments.py` | conversion des images jointes |
| `viewer.py` | volet et fenêtre d'affichage des fichiers cités |
| `permissions.py` | cartes des demandes de Claude (plan, questions, autorisations) et réponses |
| `planview.py` | plan de Claude dans le volet latéral, avec ses actions |
| `subagents.py` | blocs des sous-agents (suivi en direct, transcription à la demande) |
| `agentsview.py` | volet « Sous-agents » : arbre des sous-agents, état et transcription suivie |
| `toolcall.py` | appels d'outils dépliables sur leur sortie (en direct, ou lue à la demande), suites repliées en « N étapes » |
| `todos.py` | liste de tâches de Claude (TodoWrite, TaskCreate, TaskUpdate) et son volet repliable, dans la liste d'activité |
| `activity.py` | ce qui tourne et ce qui attend, à côté de la saisie : Claude (étape, durée, « Arrêter ») ou la demande en attente, sous-agents, shells et monitors, tâches, messages en file ; lignes mises à jour sur place d'après leur clé ; disposition à droite de la saisie ou au-dessus (`ActivitySplit`) |
| `dirpicker.py` | sélecteur de dossier sur un hôte SSH |
| `tags.py` | stockage des tags, analyse de la recherche (`#tag`, `dir:`) |
| `notes.py` | notes Markdown : lecture et écriture sur l'hôte de la conversation (ce PC pour le cloud), index, file d'enregistrement, extraits pour la recherche |
| `notesview.py` | volet « Notes » : aperçu, édition, enregistrement automatique |
| `tagui.py` | pastilles, éditeur de tags, menus de filtre |
| `commands.py` | complétion des commandes « / » (liste demandée à `claude`, filtrage, suggestions) |
| `shell.py` | commandes « ! » : lancement sur l'hôte, bloc du fil, texte joint au message suivant ; décompte et info-bulle des shells et monitors en arrière-plan |
| `rename.py` | renommage par `/rename` (commande sur l'hôte, popover) |
| `window.py` | fenêtre principale |
| `app.py` | application GTK et point d'entrée |
