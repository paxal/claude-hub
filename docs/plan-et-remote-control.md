# Mode plan, sous-agents et Remote Control

← [README](../README.md)

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
  saisie comme remarque. Tant qu'un plan attend, un message envoyé avec « Envoyer »
  (Entrée, sans pièce jointe) devient cette remarque : claude ne lirait un message
  ordinaire qu'une fois le plan tranché. La saisie l'annonce (« Remarque sur le
  plan : Claude continue à planifier »). Un plan à valider s'ouvre de lui-même dans le volet de
  droite, avec les mêmes actions ; dans le fil, sa carte est alors repliée
  (titre et boutons). Le bouton « volet » de chaque plan, y compris ceux de
  l'historique, l'y ouvre ou l'y referme. Fermé à la main, un plan ne se rouvre
  pas tout seul. Il appartient à sa conversation : il disparaît quand on en
  change et revient quand on y retourne, avec ses actions s'il attend encore sa
  validation. Quand un plan et un fichier sont ouverts, deux onglets « Plan » /
  « Fichier » les séparent.
  Une fois tranché, le plan reste dans le fil, suivi de son exécution : ses
  boutons laissent la place à une pastille « ✓ Validé · modifications
  acceptées » (le mode choisi), « ✗ Refusé » avec la remarque transmise à
  Claude, ou « ⏹ Interrompu ». Dans l'historique, la pastille dit seulement
  « ✓ Validé » : la conversation ne garde pas le mode choisi. « ⚠ Demande
  perdue » : claude n'a pas pu demander la validation, parce que claude-hub a
  été fermé ou relancé pendant sa réponse (« Stream closed »). Un plan que
  Claude repropose tel quel (même texte) est réduit à son en-tête, avec la
  pastille « Identique au plan suivant » et une flèche pour le déplier ; un
  plan révisé reste déplié. Une conversation rouverte pendant qu'un plan
  attend ne le montre qu'une fois : sa carte remplace l'élément relu dans la
  transcription ;
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
le terminal retire la carte au relevé suivant.

Sans Remote Control, on y répond dans le terminal. Une carte à la fin du fil le
rappelle (« Claude attend ta réponse dans son terminal », en précisant s'il
s'agit d'une autorisation), et la question est dépliée dans le fil si elle y
est. Souvent, elle n'y est pas encore : Claude Code ne l'écrit dans la
conversation qu'une fois répondue (en mode auto, ou quand la fin d'une tâche de
fond l'a relancé). Elle n'est alors visible que dans le terminal.

Chaque sous-agent (outil Agent) a son bloc dépliable : type, description,
avancement et jetons en direct, puis sa consigne et son travail. Dans
l'historique, sa transcription est lue à l'ouverture du bloc.

Tant que des sous-agents travaillent, chacun a sa ligne dans la liste d'activité,
au-dessus de la saisie, sous celle de Claude : sa description, puis sur la même
ligne sa dernière étape. Comme dans le terminal, l'étape est la description que
Claude a donnée à l'appel (« Dump the WEMYY access point fixture fields ») ; à
défaut, l'appel lui-même en police fixe (« Grep set_sort_func »). Les agents sont
rangés par heure de début, le plus ancien en premier (l'ordre ne change pas à
chaque étape), cinq au plus puis « … et 2 autres sous-agents ». L'info-bulle
donne son type et l'appel tel quel (la commande complète) ; le bouton › ouvre
l'agent dans le volet **Sous-agents**. Un workflow a une seule ligne, sous les
sous-agents : son nom, une barre d'avancement, ses agents finis sur lancés et
ceux qui tournent (« review ▬▬▬ 11/13 · review_style : r1 · pass1 »), et depuis
quand il tourne ; ses agents ne sont pas listés un par un. Quand Claude a fini
son tour mais attend ses sous-agents ou ses workflows, sa ligne le dit
(« Claude attend un workflow et 10 sous-agents »). Pour une réponse lancée
d'ici, les lignes suivent le flux de claude en direct ; sinon, la liste relue
sur l'hôte toutes les 4 s. Conversations Claude Code seulement (ce PC, SSH),
pas le cloud ni Cursor.

Le bouton « sous-agents » de l'en-tête (icône d'arbre et nombre ; conversations
Claude Code, locales ou SSH)
ouvre le volet **Sous-agents** : l'arbre de tous les sous-agents de la
conversation, y compris ceux lancés par un sous-agent, avec leur état (en
cours, terminé, interrompu, en attente de ses propres sous-agents), leur
dernière étape et leur durée, par heure de début. Ce qui tourne vient en tête,
dans une section « En cours » (un agent avec ceux qu'il a lancés, un workflow
avec tous ses agents), le reste dessous, sous « Terminés ». Les agents d'un workflow forment un groupe, à la
place de son premier agent : un en-tête avec le nom et le résumé du workflow, ses
agents finis sur le total et sa durée, puis ses agents rangés par phase
(« REVIEW · 3 », « VERIFY · 1/3 » tant qu'une phase tourne), sans leur type
(« workflow-subagent »). Un workflow en cours est déplié, un workflow fini replié ;
un clic sur l'en-tête le replie ou le déplie. Un clic sur un agent affiche sa
transcription, qui se complète tant qu'il travaille ; la liste est relue toutes
les 4 s tant qu'un agent tourne, même si la conversation est ouverte dans un
terminal. Le bouton
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
dans [`mode-plan-et-sous-agents.md`](mode-plan-et-sous-agents.md).

## Remote Control

Le Remote Control de Claude Code (piloter depuis claude.ai/code une session
ouverte dans un terminal) ne s'active pas depuis claude-hub : il tourne dans le
processus `claude` interactif lui-même, qui seul peut l'allumer (`/remote-control`
ou `claude --remote-control`). Rien ne permet de l'activer de l'extérieur sur une
session déjà ouverte, par exemple dans un terminal SSH sur un autre poste.
Relancer la conversation avec `claude --resume <id> --remote-control` ferait
tourner deux processus sur la même conversation. `/remote-control` ne se tape pas
pendant qu'une question est affichée dans le terminal (elle prend la saisie) :
y répondre d'abord.

Une conversation ouverte dans un terminal **sans** Remote Control porte la
pastille « Sans Remote Control » dans son en-tête, à côté de l'hôte. Depuis
claude-hub, on ne peut alors ni lui écrire (claude-hub propose une copie), ni
répondre à ses demandes, ni toujours voir sa question avant d'y avoir répondu.
Un clic sur la pastille l'explique, avec la marche à suivre pour cette session
et pour les suivantes. Le texte sous la saisie le rappelle. La pastille
disparaît dès que la session est reliée (relevé toutes les 4 s).

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
aussi depuis claude-hub (voir [Mode plan, demandes de Claude et
sous-agents](#mode-plan-demandes-de-claude-et-sous-agents)) : la demande est lue dans l'API
(`external_metadata.pending_actions` de la session), la réponse part en
`control_response`, par le même chemin et avec le même en-tête.

### Conversations lancées avant un redémarrage

Les conversations lancées depuis claude-hub tournent en `claude -p`, sans
terminal : Remote Control ne s'y applique pas (`--remote-control` et
`remoteControlAtStartup` ne valent que pour un `claude` interactif), et
claude-hub n'en a pas besoin, puisqu'il parle directement à ce `claude`. Mais
si claude-hub se ferme (ou plante) pendant une réponse, ce `claude` continue
sans lui : le claude-hub relancé ne peut plus lui écrire ni répondre à ses
demandes. Sur un hôte SSH, il ne s'arrête même pas une fois sa réponse finie.

claude-hub retient donc les conversations qu'il lance, jusqu'à la fin de leur
`claude` (`launched` dans `settings.json`). Après un redémarrage, celles dont le
`claude` tourne encore portent la pastille grise « Lancée avant le
redémarrage » (un clic l'explique), et la ligne « Claude travaille (lancé avant
le redémarrage) » au-dessus de la saisie, avec un bouton pour l'arrêter. Sa
réponse s'affiche au fur et à mesure, comme celle d'un terminal.

- Dès que sa réponse est finie et qu'il n'attend plus rien en arrière-plan
  (shell, monitor, workflow, sous-agent), claude-hub l'arrête : la conversation
  redevient normale, sans rien perdre.
- Un message écrit d'ici là attend dans la file et part à ce moment-là (une
  reprise de la conversation, pas une copie). « Envoyer tout de suite » arrête
  l'ancien `claude` aussitôt.
- S'il reste bloqué sur une question ou une autorisation (personne ne peut plus
  y répondre), le bouton d'arrêt l'arrête : ce qu'il a déjà écrit est gardé.

Seul un `claude` sans terminal lancé par claude-hub est arrêté ainsi, jamais
celui d'un terminal.
