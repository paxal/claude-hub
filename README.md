# claude-hub

Interface graphique (GTK 4) pour les sessions Claude Code.

Liste les conversations de ce PC, d'hôtes SSH et du cloud (claude.ai/code,
agents Cursor), les affiche sous forme de chat et les continue avec
`claude -p --resume`. Les conversations restent les fichiers habituels de
`~/.claude` : on peut les reprendre dans un terminal (`claude --resume`) et
inversement ; la vue se met à jour toute seule.

La liste est triée par dernier message (le tien ou celui de Claude), pas par
date du fichier : un `claude` resté ouvert dans un terminal réécrit ses
métadonnées sans rien dire de nouveau. Une conversation remonte en tête dès
qu'on lui envoie un message, puis quand Claude s'arrête (réponse finie,
question, plan ou autorisation à valider), qu'elle tourne ici, dans un
terminal, dans Cursor ou sur claude.ai. Les étapes d'une réponse (commandes,
lectures de fichiers…) ne changent pas l'ordre.

## Prérequis

- Python ≥ 3.10, GTK 4 et PyGObject (paquets système)
- `pygments` (facultatif) pour la coloration syntaxique
- `claude` dans le PATH, en local comme sur les hôtes distants
- hôtes distants : accès SSH par clé, et `python3` ou `node`

### macOS (non essayé)

Le code prévoit macOS, sans y avoir été lancé : `brew install gtk4 pygobject3`,
puis `./bin/claude-hub` depuis un terminal (`install.sh` et l'entrée de menu
sont propres à Linux ; pas encore de paquet `.app`). Différences prises en
compte :

- le jeton de Claude Code est lu dans le trousseau (service
  `Claude Code-credentials`, avec `security`) quand `~/.claude/.credentials.json`
  n'existe pas ;
- la base de Cursor est cherchée dans `~/Library/Application Support/Cursor/` ;
- les raccourcis Ctrl+… deviennent Cmd+… (accélérateurs `<Primary>`).

Sans D-Bus, la pastille de l'icône (`launcher.py`) ne s'affiche pas. Un Mac
utilisé comme hôte SSH reçoit ses commandes par `bash -lc` : `claude` doit être
dans le PATH de `~/.bash_profile`, pas seulement dans celui de `~/.zprofile`.

## Lancement et installation

```sh
./bin/claude-hub          # depuis le dépôt, sans installation
python3 -m claude_hub     # avec src/ dans PYTHONPATH
./install.sh              # lien ~/.bin/claude-hub -> bin/claude-hub, entrée de menu, icône
```

`install.sh` accepte `BINDIR=...` pour choisir un autre répertoire. Le lien
pointe sur le dépôt : une modification du code est prise en compte au prochain
lancement.

L'entrée de menu s'appelle `org.paxal.ClaudeHub.desktop`, comme l'identifiant
de l'application : sous Wayland, c'est par ce nom que le compositeur retrouve
l'icône de la fenêtre (`data/org.paxal.ClaudeHub.svg`, indiquée en chemin
absolu). Sans installation, la fenêtre n'a pas d'icône.

## Documentation

- [Trouver et organiser ses conversations](docs/liste.md) : hôtes affichés,
  accueil et nouvelle conversation, liste et états, épingles et vues
  enregistrées, tags et filtres, clavier.
- [Dans une conversation](docs/conversation.md) : en-tête (renommage,
  contexte, coût), copie à partir d'un message, commandes « / » et « ! »,
  sorties des outils, conversations Cursor, fichiers cités et modifiés, tâches
  de Claude, écrire pendant une réponse, l'arrêter, fin des réponses.
- [Mode plan, sous-agents et Remote Control](docs/plan-et-remote-control.md) :
  mode de permissions, plans, questions et autorisations, sous-agents, Remote
  Control.
- [Développement](docs/developpement.md) : tests, organisation du code.

## Configuration

Dans `~/.config/claude-hub/` :

- `hosts` : un hôte SSH par ligne (`local` désigne ce PC) ; modifiable aussi
  avec « Ajouter un hôte SSH… » du menu des hôtes ;
- `settings.json` : préférences de l'interface (hôtes et sources affichés, archives,
  réponses non lues, épingles, vues enregistrées,
  modèle et effort choisis pour `claude -p`, mode de permissions de chaque conversation, version désignée par chaque alias
  de modèle, apprise à chaque lancement de claude, fenêtre de contexte de chaque
  modèle, coût de la dernière réponse lancée d'ici) ;
- `tags.json` : tags par conversation, avec son hôte et un résumé (titre,
  dossier, date) pour l'afficher sans interroger l'hôte. Propre à ce PC : à
  synchroniser soi-même pour le partager.
