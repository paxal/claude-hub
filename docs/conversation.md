# Dans une conversation

← [README](../README.md)

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
info-bulle) et les tags. Le bouton « note » ouvre la [note](#notes) de la
conversation.

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

## Heure des messages

Comme dans une messagerie, chaque élément du fil (message, étape, sous-agent,
commande « ! ») porte son heure à droite, dans une colonne de même largeur pour
tous ; la date complète est en info-bulle. Une suite repliée en « N étapes »
porte l'heure de la première, et chacune la sienne une fois dépliée. Quand le
jour change, un séparateur « Aujourd'hui », « Hier », « Samedi 31 octobre »
s'insère dans le fil, comme dans la liste.

L'heure vient de la transcription (`timestamp` de chaque ligne, à la seconde),
de l'API pour le cloud et du `createdAt` des bulles pour Cursor ; pendant une
réponse lancée d'ici, c'est l'heure de réception, remplacée par celle de la
transcription au rechargement suivant. Les agents cloud Cursor n'en ont pas.

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

## Notes

Le bouton « note » de l'en-tête (feuille au coin plié, en bleu quand la
conversation a une note) ouvre le volet **Notes** : un texte en Markdown attaché à
la conversation (ce qui reste à faire, une décision, un lien…). Une conversation
sans note s'ouvre directement en édition ; sinon le volet montre l'aperçu
(Markdown mis en forme, chemins cliquables comme dans le fil, relatifs au dossier
de la conversation) et le crayon passe au texte brut. « Note… » du clic droit sur
la liste ouvre la conversation et sa note. Le volet suit la conversation affichée.

La note s'enregistre toute seule une seconde après la dernière frappe, et aussitôt
quand on revient à l'aperçu, qu'on change de conversation, qu'on ferme le volet ou
claude-hub ; le pied du volet dit « Enregistrée à 14:02 ». Une note vidée est
supprimée. Elle est relue à chaque ouverture du volet.

La note est rangée **sur l'hôte de la conversation**, dans
`~/.config/claude-hub/notes/<id>.md` (sous `$XDG_CONFIG_HOME` s'il est défini) :
celle d'une conversation de cerberus est sur cerberus, et un autre PC qui affiche
cet hôte la retrouve. Le cloud n'a pas d'hôte où écrire : les notes de ses
conversations restent sur ce PC. Rien n'est écrit dans `~/.claude` : Claude ne lit
pas les notes.

- Si l'hôte ne répond pas, le volet le dit et ne permet pas d'écrire (on
  écraserait la note qu'on n'a pas pu lire).
- Deux claude-hub qui modifient la même note en même temps : le dernier
  enregistrement l'emporte.
- Pas de note pour une nouvelle conversation tant que Claude n'y a pas répondu.

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

## Suivi du fil

La conversation affichée est relue quand sa transcription change : toutes les 4 s
pour une conversation d'un terminal ou de Cursor, quand l'API annonce du nouveau
pour le cloud, et à la fin d'une réponse lancée d'ici. Le fil n'est pas reconstruit
pour autant : seuls les éléments nouveaux, ou ceux qui ont changé (sortie d'une
commande « ! » du terminal, sous-agent du cloud qui avance), y entrent. Le reste
garde son état (appels dépliés, sélection), et la vue ne bouge pas si l'on est
remonté lire plus haut ; en bas du fil, elle suit les nouveaux éléments. À la fin
d'une réponse lancée d'ici, ce qui a été reçu en direct est remplacé par les
éléments de la transcription (heure, point de copie). Le fil n'est reconstruit en
entier qu'à l'ouverture de la conversation (avec ses 300 derniers éléments, une
conversation longue y passe une à deux secondes).

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

## Shells en arrière-plan

Quand Claude laisse tourner une commande en arrière-plan (Bash avec
`run_in_background`, commande passée en arrière-plan après son délai ou par
Ctrl+B, commande « ! » renvoyée en arrière-plan dans le terminal), l'en-tête
montre la pastille « 1 shell en cours », comme le « 1 shell » du terminal, même
quand Claude a fini son tour. Son info-bulle donne, pour chaque shell, sa
description, depuis quand il tourne et la première ligne de sa commande. La
pastille disparaît quand le shell se termine, quand Claude l'arrête, ou quand
claude est quitté (ses shells meurent avec lui). Pour une conversation lancée
depuis le hub, le bandeau dit aussi « Claude attend un shell » une fois le tour
fini.

Sous Linux, claude-hub vérifie que le shell tourne encore (sa sortie est encore
ouverte par un processus lancé par claude) : un shell arrêté depuis le terminal
ne laisse aucune trace dans la conversation. Ailleurs (macOS), la pastille reste
alors affichée jusqu'à l'arrêt de claude.

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
note, filtrer sur son dossier de travail (`dir:`), renommer, archiver. Si c'est
la conversation affichée, elle reste non lue jusqu'à ce qu'on en ouvre une autre
puis qu'on y revienne.
