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

Le bouton du haut de la liste ouvre un menu : les hôtes dont on veut voir les
conversations (ce PC, hôtes SSH, cloud), les sources (Claude Code, Cursor, et
les conversations archivées, avec leur nombre) et « Ajouter un hôte SSH… ». Le
choix est gardé d'un lancement à l'autre. Avec plusieurs hôtes, leurs conversations sont mêlées, triées par
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

Écrire dans claude-hub à une conversation ouverte dans un terminal relié à
Remote Control envoie le message par l'API, comme claude.ai, avec l'en-tête
`anthropic-client-platform: web_claude_ai`. Le Claude du terminal le reçoit
comme tapé par l'utilisateur. Sans cet en-tête (ou avec `claude -p --cloud`), il
l'afficherait comme venant d'une autre session (« Another Claude session sent a
message ») et ne le prendrait pas pour une réponse de l'utilisateur, par exemple
pour accepter une permission.

Une question, un plan ou une autorisation en attente dans ce terminal se répond
aussi depuis claude-hub (voir « Mode plan, demandes de Claude et
sous-agents ») : la demande est lue dans l'API
(`external_metadata.pending_actions` de la session), la réponse part en
`control_response`, par le même chemin et avec le même en-tête.

## Accueil et nouvelle conversation

Sans conversation affichée (au lancement, ou avec le bouton « Nouvelle »), la
fenêtre montre l'accueil :

- **À traiter** : une carte par conversation qui attend quelque chose. Un plan à
  valider (claude lancé d'ici) se lance (« Lancer », en modifications acceptées)
  ou se renvoie en planification depuis la carte ; une question ou une
  autorisation, ou une conversation qui attend dans son terminal, s'ouvre d'un
  clic ; une réponse non lue montre le début de la réponse (lu sur l'hôte la
  première fois), avec « Ouvrir » et « Marquer comme lue » ;
- **En cours** : les conversations où Claude travaille, ici, dans un terminal ou
  dans Cursor.

Les archivées n'y figurent pas. La saisie de l'accueil démarre une nouvelle
conversation : l'hôte et le dossier se choisissent au-dessus du texte (le menu
du dossier propose les dossiers récents de l'hôte, un chemin à saisir, ou
« Parcourir… »), et « Démarrer » (ou Entrée) la crée avec ce premier message ;
elle apparaît alors en tête de la liste. **Ctrl+N** et le bouton « nouveau
document » de l'en-tête ouvrent l'accueil sur le dossier et l'hôte de la
conversation affichée (s'il existe encore) ; « Nouvelle » aussi.

Chaque conversation garde sa propre saisie en cours (texte et images) quand on
passe à une autre, marquée « brouillon » (crayon) dans la liste ; l'accueil garde
aussi la sienne. Les brouillons restent en mémoire : fermer claude-hub perd ceux
qui n'ont pas été envoyés.

## Liste des conversations

Les conversations sont regroupées par jour (« Aujourd'hui », « Hier »,
« Samedi 31 octobre »…), avec l'heure du dernier message à droite. L'icône de
gauche donne l'état : pastille bleue (réponse non lue : titre en gras, heure en
bleu), roue orange (Claude travaille : « en cours »), triangle rouge (plan,
question ou autorisation à valider : « à valider »), coche verte (terminé, la
session reste ouverte dans un terminal), crayon (brouillon). La roue est fixe :
une animation par ligne coûtait ~20 % de CPU avec le rendu cairo.

Les boutons « Toutes / À traiter / En cours », sous la recherche, filtrent la
liste par état ; les pastilles comptent les conversations à traiter (non lues et
demandes) et en cours. La conversation affichée reste visible quel que soit le
filtre. Au survol d'une ligne, l'heure laisse la place aux boutons tags, épingle
et archive. Le pied de liste donne le nombre de conversations et d'archivées.

Le nombre de conversations à traiter (réponses non lues, plans, questions et
autorisations en attente, sur les hôtes cochés, hors archives) s'affiche aussi
sur l'icône de Claude Hub dans la barre des tâches. KDE le lit dans le signal D-Bus
`com.canonical.Unity.LauncherEntry` (celui des docks d'Unity) ; la pastille
disparaît à zéro et quand on ferme la fenêtre.

## Épingles et vues enregistrées

Une conversation **épinglée** (bouton épingle au survol, clic droit ou menu ⋮ :
« Épingler ») reste en tête de la liste, sous « Épinglées », quelle que soit sa
date. Les filtres et la recherche s'y appliquent comme au reste.

Une **vue** est une recherche gardée (`#stockly`, `dir:"~/chansons scout"`,
`bazel #stockly/ops`…). Quand la recherche n'est pas déjà une vue, « Enregistrer
la vue » apparaît sous elle ; les vues s'y affichent ensuite en pastilles. Un clic
applique la recherche, un second l'enlève ; le clic droit propose de supprimer la
vue.

## Clavier

| Touches | Action |
|---|---|
| **Ctrl+K** | sélecteur rapide : taper quelques lettres d'une conversation, ↑/↓, Entrée |
| **Alt+↑ / Alt+↓** | conversation précédente / suivante de la liste (filtre compris) |
| **Alt+Maj+↑ / Alt+Maj+↓** | conversation à traiter précédente / suivante (non lue ou demande), en bouclant |
| **Ctrl+N** | nouvelle conversation (accueil) dans le dossier de celle affichée |
| **Maj+Tab** | mode automatique ⇄ plan (dans la saisie) |
| **Échap** | arrêter la réponse (dans la saisie) |
| **Entrée**, **Ctrl+Entrée**, **Alt+Entrée** | envoyer, tout de suite, en file (voir plus bas) |

Le sélecteur (Ctrl+K) cherche de façon approximative : les lettres de chaque mot
doivent apparaître dans l'ordre, pas forcément à la suite (« sepjr » trouve
« Séparateurs de jour »), dans le titre, le dossier, l'hôte ou les tags, sans
tenir compte des accents. Les débuts de mots et les lettres consécutives comptent
davantage, le titre plus que le reste ; les lettres trouvées sont en gras. Sans
texte, il montre les épinglées puis les plus récentes. Il porte sur les
conversations de la liste (hôtes et sources cochés).

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
réponse (« Opus 5.5 (medium) », lus dans la transcription), le remplissage du
contexte et le coût de la dernière réponse, le dossier, l'id abrégé (complet en
info-bulle) et les tags.

Le **contexte** est la taille du dernier message de Claude (jetons d'entrée, cache
compris, et de sortie), lue dans la transcription puis suivie en direct pendant une
réponse lancée d'ici ; la jauge en donne la part de la fenêtre du modèle, en
orange au-delà de 60 %, en rouge au-delà de 85 % : le moment de faire `/compact`.
L'info-bulle donne les nombres de jetons. La fenêtre de chaque modèle est apprise
de claude à la fin d'une réponse lancée d'ici (`modelUsage.contextWindow`) ; avant,
elle est supposée (1 M pour les modèles 5 et les modèles « [1m] », 200 000 sinon),
et l'info-bulle le dit. Après un `/compact`, la jauge disparaît jusqu'au message
suivant. Le **coût** est celui de la dernière réponse lancée depuis Claude Hub
(`total_cost_usd` de `claude -p`), gardé d'un lancement à l'autre ; les
transcriptions n'enregistrent pas le coût de chaque réponse, il manque donc pour
celles faites dans un terminal. Ni l'un ni l'autre pour Cursor et le cloud. À droite du titre, une pastille dit
si Claude travaille ou attend une réponse. Le menu ⋮ de l'en-tête reprend les
actions du clic droit sur la liste (lue ou non lue, filtrer sur le dossier,
archiver), plus « Copier l'identifiant » et, pour le cloud, « Ouvrir sur
claude.ai ».

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

Dans le fil et la liste, une commande envoyée à Claude (skill, `/plan` suivi
d'une demande) apparaît telle qu'on l'a tapée (`/nom arguments`) : elle sert de
titre tant que claude n'en a pas généré. Les commandes locales du terminal
(`/copy`, `/rename`…, précédées d'un avertissement dans la transcription) ne
sont ni un message ni une activité.

## Commandes « ! »

Comme dans le terminal, un message qui commence par `!` est une commande shell
(le cadre de la saisie passe en rose) : `! ./run_tintes2.sh` la lance avec
Entrée, dans le dossier de la conversation et sur son hôte (`bash -lc`, par SSH
pour un hôte distant). `claude -p` ne connaît pas ce mode : c'est claude-hub qui
lance la commande.

- Sa sortie s'affiche dans le fil au fil de l'eau, avec son code de retour ;
  au-delà d'une quinzaine de lignes, elle défile dans un cadre de hauteur fixe.
  Le bouton ■ l'arrête, × la retire (elle ne sera pas envoyée).
- Claude ne répond pas tout de suite : la commande et sa sortie partent avec le
  message suivant, dans les balises que le terminal enregistre
  (`<bash-input>`, `<bash-stdout>`, `<bash-stderr>`). **Entrée** sur une saisie
  vide les envoie seules : Claude reprend avec le résultat. Un code de retour
  non nul (`Exit code 2`) ou un arrêt est ajouté aux erreurs.
- Claude reçoit au plus 30 000 caractères de chaque sortie (début et fin), comme
  dans le terminal.
- Tant qu'elles ne sont pas envoyées, les commandes n'existent que dans
  claude-hub (la conversation est marquée « brouillon ») : fermer claude-hub,
  ou reprendre la conversation dans un terminal avant d'écrire, les perd.
- La commande n'a pas de terminal : une commande qui attend une saisie (mot de
  passe…) échoue, comme dans Claude Code.
- Pas de commande « ! » pour une conversation claude.ai ou Cursor, ni pour une
  conversation ouverte dans un terminal (Remote Control) : la taper là-bas.
- Sur un hôte SSH, ■ coupe la connexion ; la commande distante peut continuer
  tant qu'elle n'écrit rien.

Les commandes `!` tapées dans un terminal s'affichent aussi dans le fil.

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

Chaque appel d'outil (icône, nom et détail : `Bash  ls -la`, Read, Grep…) se
déplie d'un clic sur sa sortie (précédée du diff pour une modification de
fichier, voir « Fichiers modifiés »), en rouge si l'outil a échoué, avec un bouton pour
la copier. Une modification (Edit, MultiEdit, Write) indique ses lignes ajoutées
et retirées (« +24 −3 ») ; en direct, une roue marque l'outil en cours, puis une
coche (ou une croix rouge s'il a échoué). Trois appels consécutifs ou plus sont
repliés sous « N étapes », suivi de leur résumé (« Read window.py · Grep
set_sort_func… ») ; en direct, la suite en cours reste dépliée jusqu'au message
suivant de Claude, et une recherche déplie la suite qui contient le résultat. La ligne
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

## Fichiers modifiés

Déplier un Edit, un MultiEdit ou un Write dans le fil montre son diff, avant la
sortie de l'outil (calculé d'après l'entrée de l'outil, au premier dépliage).

Le bouton « fichiers modifiés » de l'en-tête (icône de document et nombre de
fichiers, absent tant que rien n'a changé) ouvre le volet **Fichiers modifiés** :
tout ce que la conversation a modifié, sous-agents compris, regroupé par fichier
et trié par chemin, pour relire avant de committer. Chaque fichier donne ses lignes
ajoutées et retirées, « nouveau » s'il a été créé, et le nombre de modifications ;
déplié, le diff de chacune (avec son heure, et le sous-agent qui l'a faite). Le
chemin ouvre le fichier dans le volet, à la première ligne modifiée. « Copier »
copie le diff de toutes les modifications. Les modifications qui ont échoué ne
figurent pas.

Pour une conversation Claude Code (ce PC, SSH), la liste vient de toute la
transcription et de celles des sous-agents (commande `edits` des scripts
distants), avec le diff calculé par claude (numéros de ligne compris). Elle est
relue quand une modification apparaît dans le fil, et pendant une réponse lancée
d'ici, dès qu'une modification aboutit. Pour le cloud, elle est tirée de
l'historique affiché ; pour Cursor, dont les outils ne donnent que le fichier, le
diff est lu dans sa base au dépliage. Les diffs affichés sont coupés après 400
lignes ; « Copier » en haut du volet les copie entiers.

## Mode plan, demandes de Claude et sous-agents

Le mode de permissions (dans la barre de la saisie, après le modèle et l'effort,
en bleu en mode plan ; **Maj+Tab** bascule entre « Automatique » et « Plan ») est
passé à `claude -p`. Il est retenu
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

Une conversation qui attend une réponse est marquée « à valider » (triangle
rouge) dans la liste, figure dans « À traiter », et déclenche une notification.
Une session de terminal qui attend une réponse (état `waiting` de claude) est
traitée de même. Les décisions ne sont jamais
enregistrées dans les réglages de claude.

Si cette session de terminal est reliée à Remote Control (ou si c'est une
session claude.ai), ses demandes s'affichent avec les mêmes cartes, et on y
répond d'ici comme sur claude.ai : le Claude du terminal reprend avec la
réponse, et sa boîte de dialogue se ferme. Une réponse donnée entre-temps dans
le terminal retire la carte au relevé suivant. Sans Remote Control, la question
est seulement dépliée dans le fil, en lecture seule : on y répond dans le
terminal.

Chaque sous-agent (outil Agent) a son bloc dépliable : type, description,
avancement et jetons en direct, puis sa consigne et son travail. Dans
l'historique, sa transcription est lue à l'ouverture du bloc.

Tant que des sous-agents travaillent, le bandeau d'activité de la saisie porte
une pastille « 🤖 3 sous-agents », suivie de l'étape de celui qui a agi en dernier
(« Review fix › Bash cargo test… ») quand Claude lui-même n'a rien de plus précis
à dire : pendant un appel Agent, quand il attend ses sous-agents (« Claude attend
ses sous-agents »), ou quand la conversation tourne dans un terminal (sauf
workflow en cours, dont l'avancement reste affiché). L'info-bulle de la pastille
liste les sous-agents au travail et leur dernière étape ; un clic ouvre le volet
**Sous-agents**. Pour une réponse lancée d'ici, la pastille et l'étape suivent le
flux de claude en direct ; sinon, la liste relue sur l'hôte toutes les 4 s.
Conversations Claude Code seulement (ce PC, SSH), pas le cloud ni Cursor.

Le bouton « sous-agents » de l'en-tête (icône d'arbre et nombre ; conversations
Claude Code, locales ou SSH)
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
le bloc montre leurs étapes (« Bash  description », sans détail), leur état
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
saisie : « Tâches 1/3 · » suivi de la tâche en cours ; un clic déplie chaque
tâche (✓ terminée, ▸ en cours, ○ à faire). Le volet disparaît quand toutes les
tâches sont terminées. Dans le fil, ces appels sont résumés
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
  au-dessus de la saisie (« En file ») ; chaque message peut y être modifié ou
  retiré.

Sans réponse en cours, les trois envoient simplement le message.

## Arrêter une réponse

Pendant une réponse, le haut de la zone de saisie montre l'étape en cours
(« Claude utilise Bash » et sa commande, « Claude réfléchit »), sa durée, et
« Arrêter ». « Arrêter », ou **Échap** dans la zone de saisie, interrompt Claude
comme Échap dans le terminal. Le texte déjà écrit est gardé, l'outil en cours et les
demandes en attente sont abandonnés, et la conversation porte la mention
« ⏹ Interrompu ». Si claude ne s'arrête pas, un second clic (ou 10 s
d'attente) tue le processus.

Pour une conversation ouverte dans un terminal, le bandeau dit « Claude travaille
(dans un terminal) », avec la durée depuis que claude s'est mis au travail (d'après
son fichier `~/.claude/sessions/<pid>.json`, pas depuis l'ouverture dans le hub).
Si Claude a fini son tour mais attend un workflow lancé en arrière-plan (outil
Workflow, par exemple `/review`), la pastille et le bandeau disent « Claude attend
un workflow », suivi de l'avancement lu dans le journal du workflow : son nom, les
agents finis sur les agents lancés, et celui qui tourne avec sa phase
(« review · 151/152 agents · review_style #5 : r5 · pass1 · General Rust 1/2 »).

## Fin des réponses

Quand Claude a fini de répondre (un `claude -p` lancé depuis le hub, ou une
session d'un terminal ou de Cursor qui passe de « travaille » à « terminé »),
claude-hub envoie une notification du bureau, sauf si la conversation est déjà
affichée dans la fenêtre active. Un clic sur la notification ouvre la
conversation.

Dans la liste, ces conversations restent marquées d'une pastille bleue (titre
en gras, heure en bleu) tant qu'on ne les a pas ouvertes ; elles figurent aussi
dans « À traiter ». Un clic droit sur une
conversation de la liste ouvre un menu : marquer comme non lue (ou lue), tags,
filtrer sur son dossier de travail (`dir:`), renommer, archiver. Si c'est
la conversation affichée, elle reste non lue jusqu'à ce qu'on en ouvre une autre
puis qu'on y revienne.

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
| `gtk.py` | initialisation de GTK 4, `in_thread` |
| `hosts.py` | configuration, hôtes local/SSH, exécution des scripts distants |
| `remote/helper.py`, `remote/helper.js` | scripts envoyés aux hôtes (`python3 -` ou `node -`) pour lire `~/.claude` ; bibliothèque standard uniquement |
| `cursor.py` | conversations Cursor (base `state.vscdb`, recherche) |
| `cloud.py` | sessions claude.ai/code et agents cloud Cursor |
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
| `shell.py` | commandes « ! » : lancement sur l'hôte, bloc du fil, texte joint au message suivant |
| `rename.py` | renommage par `/rename` (commande sur l'hôte, popover) |
| `window.py` | fenêtre principale |
| `app.py` | application GTK et point d'entrée |
