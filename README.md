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

## Hôtes affichés

Le bouton du haut de la liste coche les hôtes dont on veut voir les
conversations (ce PC, hôtes SSH, cloud) ; le choix est gardé d'un lancement à
l'autre. Avec plusieurs hôtes, leurs conversations sont mêlées, triées par
dernier message, et chaque ligne porte un badge à son hôte. Chaque hôte
s'affiche dès qu'il répond : un hôte lent ou injoignable ne retarde pas les
autres, et son erreur s'affiche sous la liste. La recherche dans le contenu, le
filtre `dir:` et le suivi des états (en cours, terminé…) portent sur tous les
hôtes cochés.

## Remote Control

Le Remote Control de Claude Code (piloter depuis claude.ai/code une session
ouverte dans un terminal) ne s'active pas depuis claude-hub : il tourne dans le
processus `claude` interactif lui-même, qui seul peut l'allumer (`/remote-control`
ou `claude --remote-control`). Rien ne permet de l'activer de l'extérieur sur une
session déjà ouverte, par exemple dans un terminal SSH sur un autre poste.
Relancer la conversation avec `claude --resume <id> --remote-control` ferait
tourner deux processus sur la même conversation.

Pour l'avoir sur toutes les sessions, l'activer par défaut sur chaque hôte où
tourne `claude` (option « Enable Remote Control for all sessions » de `/config`) :

```json
// ~/.claude/settings.json de l'hôte (cerberus, rpi4…), pas celui de ce PC
{ "remoteControlAtStartup": true }
```

- Seuls les réglages utilisateur comptent : dans les `.claude/settings*.json`
  d'un dépôt, la clé est ignorée.
- `claude` doit y être connecté avec un compte claude.ai, pas une clé d'API.
- Une politique d'organisation peut l'interdire.

claude-hub n'écrit pas ce réglage (il ne modifie jamais `~/.claude`) : c'est à
faire soi-même. Ces sessions devraient alors apparaître aussi dans l'hôte
cloud (non vérifié).

## Nouvelle conversation

Le bouton « Nouvelle » ouvre une conversation sur l'hôte choisi à sa gauche
(par défaut celui de la conversation affichée), dans le dossier saisi à côté
(ou `~`). Le bouton « nouveau document » de l'en-tête, ou **Ctrl+N**, en ouvre une
dans le dossier et sur l'hôte de la conversation affichée. Il n'apparaît pas
pour le cloud, ni quand le dossier n'existe plus.

La nouvelle conversation apparaît tout de suite en tête de la liste. Si on la
quitte sans rien y écrire, elle disparaît ; sinon elle reste marquée
« ✎ brouillon ».

Chaque conversation garde sa propre saisie en cours (texte et images) quand on
passe à une autre, avec le même marqueur « ✎ brouillon » dans la liste. Les
brouillons restent en mémoire : fermer claude-hub perd ceux qui n'ont pas été
envoyés, y compris les nouvelles conversations.

## Renommer une conversation

Le titre d'une conversation est celui que Claude Code lui donne (`/rename`, ou
le titre généré dans le terminal) ; à défaut, son premier message. En mode
`-p`, claude ne génère pas de titre de lui-même : quand claude-hub envoie un
message à une conversation sans titre, il le demande au même processus, comme
l'extension VS Code (requête de contrôle `generate_session_title` avec
`persist`, qui ajoute l'`ai-title` à la transcription). Les anciennes
conversations sans titre en reçoivent donc un au message suivant. Le bouton
crayon de l'en-tête la renomme : claude-hub lance `claude -p --resume <id>
"/rename <nom>"` sur l'hôte, c'est donc `claude` qui écrit le titre, visible
aussi dans `claude --resume`. « Nom choisi par Claude » envoie `/rename` sans
argument. Pas de tour du modèle avec un nom donné. Le bouton est grisé pendant
une réponse lancée depuis claude-hub, et absent pour Cursor et le cloud.

Sous le titre, l'en-tête indique l'hôte, le modèle et l'effort de la dernière
réponse (« Opus 5.5 (medium) », lus dans la transcription), le dossier et l'id.

## Copier une conversation à partir d'un message

Au survol d'un message, le bouton « branche » (à côté de « copier le
Markdown ») prépare une copie de la conversation :

- sur un message de Claude, la copie s'arrête à ce message ;
- sur un message de l'utilisateur, elle s'arrête juste avant, et le message est
  remis dans la saisie pour être modifié.

La suite de l'original est grisée, et un bandeau au-dessus de la saisie rappelle
la copie en cours (la croix l'annule). Le message suivant lance la copie avec
`claude -p --resume <id> --fork-session --resume-session-at <uuid>`, une option
non documentée de claude. L'original reste intact. Ce bouton n'existe que pour
les conversations Claude Code (ce PC, SSH), pas pour celles du cloud ni de
Cursor. On ne peut pas copier pendant une réponse.

## Commandes « / »

Un message qui commence par `/` ouvre la liste des commandes de Claude Code
(intégrées, skills, plugins, commandes du projet), filtrée au fil de la frappe
sur le début du nom ou d'un alias, puis sur le reste du nom. **↑/↓** choisit,
**Tab** ou **Entrée** complète (`/compact `), **Échap** ferme la liste ; une
commande déjà complète part avec Entrée. La liste disparaît dès qu'on tape une
espace (arguments).

La liste vient de `claude` lui-même, sur l'hôte et dans le dossier de la
conversation (les skills et commandes du projet en dépendent) : requête de
contrôle `initialize` du SDK, sans tour du modèle ni conversation enregistrée
(`--no-session-persistence`). Elle est demandée au premier `/` puis gardée en
mémoire par hôte et dossier. Pas de complétion pour le cloud.

## Tags et filtres

Le bouton étiquette d'une conversation (dans la liste ou dans l'en-tête) ajoute
ou retire des tags. Un tag avec des « / » se range comme dans un dossier :
`stockly/bazel`, `stockly/ops`…

Dans la recherche, on peut combiner :

- `#stockly` : conversations taguées `stockly` ou `stockly/…`, **sur tous les
  hôtes** (celles d'un autre hôte portent un badge à son nom) ;
- `#stockly/` : seulement ce qui est rangé sous `stockly/` ;
- `dir:~/devel/gauth` : conversations lancées dans ce dossier ou en dessous
  (`dir:"~/mes docs"` si le chemin contient des espaces) ;
- du texte libre : titres, puis contenu des conversations de l'hôte affiché.

Les boutons étiquette et dossier à droite de la recherche (listes des tags et
des dossiers de travail) et les pastilles des conversations remplissent ces
filtres ; un second clic les retire.

## Sorties des outils

Chaque appel d'outil (`🔧 Bash  ls -la`, Read, Grep…) se déplie d'un clic sur
sa sortie, en rouge si l'outil a échoué, avec un bouton pour la copier. La ligne
de l'outil est tronquée (texte complet en info-bulle) ; pour Bash, une longue
commande est aussi affichée en entier dans la partie dépliée. En direct, la
sortie arrive avec la réponse de Claude ; dans l'historique, elle est lue sur
l'hôte au premier dépliage (commande `result` des scripts distants), pour garder
l'historique léger. Elle est limitée à 100 000 caractères. Les sessions cloud
n'ont pas de sorties.

## Conversations Cursor

Les outils de Cursor prennent le nom de leur équivalent chez Claude
(`read_file_v2` → Read, `edit_file_v2` → Edit, `run_terminal_command_v2` → Bash,
`ripgrep_raw_search` → Grep, `todo_write` → TodoWrite, `task_v2` → Agent,
`create_plan` → plan…) : chemins cliquables, tâches, sous-agents et plans
s'affichent comme pour Claude. Les outils MCP gardent leur nom.

Les sorties sont lues dans la base de Cursor, en lecture seule, même quand
l'espace de travail est sur un hôte SSH. Pour une modification de fichier, c'est
le diff entre les versions que Cursor garde. Cursor ne garde pas tout : ni le
contenu lu par la plupart des Read, ni les résultats de Grep. La conversation
d'un sous-agent est lue à l'ouverture de son bloc.

## Fichiers cités

Les chemins cités dans les messages (`/chemin/plan.md`, `~/.bashrc`,
`src/app.py:42`, `` `README.md` ``, liens Markdown vers un fichier) et ceux des
outils Read, Write et Edit sont cliquables. Le fichier s'ouvre dans un volet à
droite du chat : Markdown mis en forme, code coloré (d'après l'extension, ou la
ligne `#!` d'un script) avec défilement jusqu'à la ligne citée, qui est
surlignée, et images (PNG, JPEG, GIF, SVG, BMP, TIFF… ; WebP et AVIF passent par
ImageMagick si GTK ne les lit pas) ajustées au volet, un clic les montrant en
taille réelle. **Maj+clic** l'ouvre dans une fenêtre à part
(le bouton « fenêtre » du volet aussi).

Le fichier est lu sur l'hôte de la conversation, SSH compris. Les chemins
relatifs partent du dossier de la session, ou du fichier Markdown affiché pour
les liens qu'il contient. Le texte est limité à 1 Mo et les images à 8 Mo.
« Ouvrir avec l'application par défaut » n'est proposé que pour les fichiers
locaux. Les sessions cloud n'ont pas de fichiers accessibles.

## Mode plan, demandes de Claude et sous-agents

Le mode de permissions (sous les boutons de modèle et d'effort ; **Maj+Tab**
bascule entre « Automatique » et « Plan ») est passé à `claude -p`. Il est retenu
pour chaque conversation : on la retrouve dans le dernier mode utilisé, même après
un redémarrage. Une nouvelle conversation prend le dernier mode choisi. Les demandes de Claude
s'affichent dans le fil sous forme de cartes :

- **plan** (mode `plan`) : le plan est rendu en Markdown, avec le lien vers son
  fichier. « Lancer » le valide et Claude passe à l'exécution dans le mode
  choisi au-dessus (modifications acceptées par défaut), qui devient celui des
  messages suivants ; « Continuer à planifier » le refuse, avec le texte de la
  saisie comme remarque. Un plan à valider s'ouvre de lui-même dans le volet de
  droite, avec les mêmes actions ; dans le fil, sa carte est alors repliée
  (titre et boutons). Le bouton « volet » de chaque plan, y compris ceux de
  l'historique, l'y ouvre ou l'y referme. Fermé à la main, un plan ne se rouvre
  pas tout seul ; il se ferme quand on change de conversation. Quand un plan et
  un fichier sont ouverts, deux onglets « Plan » / « Fichier » les séparent ;
- **question** : options à cocher, ou réponse libre ;
- **autorisation** d'un outil (mode `default` surtout) : commande ou contenu
  du fichier, puis « Autoriser », « Pour la session » (la règle proposée par
  claude, pour la réponse en cours seulement) ou « Refuser » (la saisie sert de
  consigne).

Une conversation qui attend une réponse est marquée « ⚠ action requise » dans
la liste et déclenche une notification. Les décisions ne sont jamais
enregistrées dans les réglages de claude.

Chaque sous-agent (outil Agent) a son bloc dépliable : type, description,
avancement et jetons en direct, puis sa consigne et son travail. Dans
l'historique, sa transcription est lue à l'ouverture du bloc. Le bandeau
indique le nombre de sous-agents actifs.

Le bouton « 🤖 n » de l'en-tête (conversations Claude Code, locales ou SSH)
ouvre le volet **Sous-agents** : l'arbre de tous les sous-agents de la
conversation, y compris ceux lancés par un sous-agent, avec leur état (en
cours, terminé, interrompu, en attente de ses propres sous-agents), leur
dernière étape et leur durée. Un clic affiche la transcription de l'agent, qui
se complète tant qu'il travaille ; la liste est relue toutes les 4 s tant qu'un
agent tourne, même si la conversation est ouverte dans un terminal. Le bouton
› d'un bloc de sous-agent, d'un `SendMessage` qui relance un agent ou d'un
rapport d'agent ouvre directement l'agent concerné. Non fait pour le cloud et
Cursor.

Pour les sessions cloud, l'API ne transmet pas les messages des sous-agents :
le bloc montre leurs étapes (« 🔧 Bash  description », sans détail), leur état
final, les jetons et leur rapport.

Le rapport d'un sous-agent en arrière-plan revient dans la session comme un
message d'une autre session (`<agent-message from="…">[Subagent hand-back]…`).
Il s'affiche replié (« ↩ Rapport du sous-agent », avec sa première ligne en
aperçu), sans l'enveloppe ni l'avertissement ajoutés par Claude Code. Les autres
messages d'agents s'affichent de la même façon (« ✉ Message de l'agent »).
Seuls les messages envoyés depuis Claude Hub gardent la mention « via Remote
Control ».

Le fonctionnement du protocole (`--permission-prompt-tool stdio`) est décrit
dans `docs/mode-plan-et-sous-agents.md`.

## Tâches de Claude

Quand Claude découpe son travail en tâches (outils `TaskCreate` et `TaskUpdate`,
ou `TodoWrite` des versions plus anciennes), la liste s'affiche au-dessus de la
saisie : « Tâches 1/3 · » suivi de la tâche en cours, puis chaque tâche (✓
terminée, ▸ en cours, ○ à faire). Le volet se replie d'un clic et disparaît
quand toutes les tâches sont terminées. Dans le fil, ces appels sont résumés
(« #2 → terminée »).

La liste est reconstituée sur toute la transcription, même quand l'historique
affiché est tronqué, puis suivie en direct pendant une réponse. Les tâches des
sous-agents n'y figurent pas.

## Écrire pendant une réponse

Le bouton « Envoyer » a un menu avec trois façons d'envoyer un message pendant
que Claude répond :

- **Envoyer** (**Entrée**) : le message est transmis tout de suite au `claude
  -p` en cours, qui le lit après l'étape en cours (comme un message tapé
  pendant une réponse dans le terminal). Il est marqué « ↪ envoyé pendant la
  réponse » ; claude l'enregistre comme une pièce jointe `queued_command`, que
  les helpers relisent (les notifications internes `task-notification` sont
  ignorées). Si claude n'écoute plus (réponse en train de finir ou
  interrompue), le message passe en file.
- **Envoyer tout de suite** (**Ctrl+Entrée**) : interrompt la réponse, puis
  envoie le message dès que claude s'est arrêté.
- **Mettre en file d'attente** (**Alt+Entrée**) : le message attend la fin de
  la réponse et part dans un nouveau `claude -p --resume`. La file s'affiche
  au-dessus de la saisie ; chaque message peut y être modifié ou retiré.

Sans réponse en cours, les trois envoient simplement le message.

## Arrêter une réponse

« Arrêter », ou **Échap** dans la zone de saisie, interrompt Claude comme Échap
dans le terminal. Le texte déjà écrit est gardé, l'outil en cours et les
demandes en attente sont abandonnés, et la conversation porte la mention
« ⏹ Interrompu ». Si claude ne s'arrête pas, un second clic (ou 10 s
d'attente) tue le processus.

## Fin des réponses

Quand Claude a fini de répondre (un `claude -p` lancé depuis le hub, ou une
session d'un terminal ou de Cursor qui passe de « travaille » à « terminé »),
claude-hub envoie une notification du bureau, sauf si la conversation est déjà
affichée dans la fenêtre active. Un clic sur la notification ouvre la
conversation.

Dans la liste, ces conversations restent marquées « ● nouvelle réponse » (titre
en gras, liseré bleu) tant qu'on ne les a pas ouvertes. Un clic droit sur une
conversation de la liste ouvre un menu : marquer comme non lue (ou lue), tags,
filtrer sur son dossier de travail (`dir:`), renommer, archiver. Si c'est
la conversation affichée, elle reste non lue jusqu'à ce qu'on en ouvre une autre
puis qu'on y revienne.

## Configuration

Dans `~/.config/claude-hub/` :

- `hosts` : un hôte SSH par ligne (`local` désigne ce PC) ; modifiable aussi
  avec le bouton « + » de la fenêtre ;
- `settings.json` : préférences de l'interface (hôtes et sources affichés, archives,
  réponses non lues,
  modèle et effort choisis pour `claude -p`, mode de permissions de chaque conversation, version désignée par chaque alias
  de modèle, apprise à chaque lancement de claude) ;
- `tags.json` : tags par conversation, avec son hôte et un résumé (titre,
  dossier, date) pour l'afficher sans interroger l'hôte. Propre à ce PC : à
  synchroniser soi-même pour le partager.

## Tests

```sh
python3 -m unittest discover -s tests
```

Les tests d'interface tournent hors écran, sur une KWin virtuelle :
`tests/ecran-virtuel.sh commande…` (captures avec `spectacle -b -n -f -o …`).

## Organisation du code

| Module | Rôle |
|---|---|
| `gtk.py` | initialisation de GTK 4, `in_thread` |
| `hosts.py` | configuration, hôtes local/SSH, exécution des scripts distants |
| `remote/helper.py`, `remote/helper.js` | scripts envoyés aux hôtes (`python3 -` ou `node -`) pour lire `~/.claude` ; bibliothèque standard uniquement |
| `cursor.py` | conversations Cursor (base `state.vscdb`, recherche) |
| `cloud.py` | sessions claude.ai/code et agents cloud Cursor |
| `render.py` | Markdown en ligne, coloration, images, chemins cliquables, cartes des messages, CSS |
| `mdview.py` | Markdown d'un message dans un seul `TextView`, pour que la sélection traverse paragraphes, listes et code (tableaux copiés en Markdown) |
| `attachments.py` | conversion des images jointes |
| `viewer.py` | volet et fenêtre d'affichage des fichiers cités |
| `permissions.py` | cartes des demandes de Claude (plan, questions, autorisations) et réponses |
| `planview.py` | plan de Claude dans le volet latéral, avec ses actions |
| `subagents.py` | blocs des sous-agents (suivi en direct, transcription à la demande) |
| `agentsview.py` | volet « Sous-agents » : arbre des sous-agents, état et transcription suivie |
| `toolcall.py` | appels d'outils dépliables sur leur sortie (en direct, ou lue à la demande) |
| `todos.py` | liste de tâches de Claude (TodoWrite, TaskCreate, TaskUpdate) et son volet |
| `dirpicker.py` | sélecteur de dossier sur un hôte SSH |
| `tags.py` | stockage des tags, analyse de la recherche (`#tag`, `dir:`) |
| `tagui.py` | pastilles, éditeur de tags, menus de filtre |
| `commands.py` | complétion des commandes « / » (liste demandée à `claude`, filtrage, suggestions) |
| `rename.py` | renommage par `/rename` (commande sur l'hôte, popover) |
| `window.py` | fenêtre principale |
| `app.py` | application GTK et point d'entrée |
