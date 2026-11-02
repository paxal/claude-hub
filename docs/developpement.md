# Développement

← [README](../README.md)

Consignes pour les agents (et ce qu'on a appris en développant) :
[`AGENTS.md`](../AGENTS.md). Protocole de contrôle de `claude -p` (mode plan,
permissions, sous-agents), essais à l'appui :
[`mode-plan-et-sous-agents.md`](mode-plan-et-sous-agents.md).

## Tests

```sh
python3 -m unittest discover -s tests
```

Les tests d'interface tournent hors écran, sur une KWin virtuelle :
`tests/ecran-virtuel.sh commande…` (captures avec `spectacle -b -n -f -o …`).

Pour piloter l'appli elle-même : `tests/piloter.sh script.py` lance une instance
séparée sur l'écran virtuel, avec une config temporaire et un faux `claude`
(`tests/faux-claude`) qui rejoue un scénario (réponse longue, plan, autorisation,
question, sous-agent, erreur). Le script s'écrit avec `tests/pilote.py`. Mode
d'emploi dans `.claude/skills/piloter-claude-hub/SKILL.md`.

## Organisation du code

| Module | Rôle |
|---|---|
| `gtk.py` | initialisation de GTK 4, `in_thread`, modificateur des raccourcis (Ctrl, ou Cmd sous macOS) |
| `hosts.py` | configuration, hôtes local/SSH, exécution des scripts distants |
| `remote/helper.py`, `remote/helper.js` | scripts envoyés aux hôtes (`python3 -` ou `node -`) pour lire `~/.claude` ; bibliothèque standard uniquement |
| `cursor.py` | conversations Cursor (base `state.vscdb`, Linux ou macOS ; recherche) |
| `cloud.py` | sessions claude.ai/code et agents cloud Cursor ; jeton lu dans `~/.claude` ou le trousseau macOS |
| `render.py` | Markdown en ligne, coloration, images, chemins cliquables, cartes des messages, ligne d'un appel d'outil, CSS (et teintes du thème sombre) |
| `widgets.py` | widgets communs : colonne de largeur limitée (`Clamp`), pastilles renvoyées à la ligne (`WrapBox`), bouton à choix (modèle, effort, mode, hôte), pastille d'hôte, texte indicatif de la saisie |
| `listrow.py` | lignes de la liste (icône d'état, heure, boutons du survol), séparateurs de jour |
| `switcher.py` | sélecteur rapide Ctrl+K et sa recherche approximative |
| `views.py` | vues enregistrées (pastilles sous la recherche) |
| `changes.py` | fichiers modifiés : diffs (claude ou recalculés), regroupement par fichier, volet |
| `usage.py` | contexte rempli (jauge) et coût de la dernière réponse, dans l'en-tête |
| `launcher.py` | nombre de conversations à traiter sur l'icône de la barre des tâches (D-Bus) |
| `home.py` | accueil (à traiter, en cours) et choix du dossier d'une nouvelle conversation |
| `mdview.py` | Markdown d'un message dans un seul `TextView`, pour que la sélection traverse paragraphes, listes et code (tableaux copiés en Markdown) |
| `attachments.py` | conversion des images jointes |
| `viewer.py` | volet et fenêtre d'affichage des fichiers cités |
| `permissions.py` | cartes des demandes de Claude (plan, questions, autorisations) et réponses |
| `planview.py` | plan de Claude dans le volet latéral, avec ses actions |
| `subagents.py` | blocs des sous-agents (suivi en direct, transcription à la demande) |
| `agentsview.py` | volet « Sous-agents » : arbre des sous-agents, état et transcription suivie |
| `toolcall.py` | appels d'outils dépliables sur leur sortie (en direct, ou lue à la demande), suites repliées en « N étapes » |
| `todos.py` | liste de tâches de Claude (TodoWrite, TaskCreate, TaskUpdate) et son volet |
| `dirpicker.py` | sélecteur de dossier sur un hôte SSH |
| `tags.py` | stockage des tags, analyse de la recherche (`#tag`, `dir:`) |
| `tagui.py` | pastilles, éditeur de tags, menus de filtre |
| `commands.py` | complétion des commandes « / » (liste demandée à `claude`, filtrage, suggestions) |
| `shell.py` | commandes « ! » : lancement sur l'hôte, bloc du fil, texte joint au message suivant ; textes de la pastille des shells en arrière-plan |
| `rename.py` | renommage par `/rename` (commande sur l'hôte, popover) |
| `window.py` | fenêtre principale |
| `app.py` | application GTK et point d'entrée |
