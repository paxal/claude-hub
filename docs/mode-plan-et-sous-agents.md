# Mode plan, validation et sous-agents avec `claude -p`

Notes de recherche (6 octobre 2026, claude 2.1.288) : comment piloter le
mode plan depuis claude-hub, récupérer le plan en Markdown, le valider pour
lancer l'exécution (le « build ») et suivre les sous-agents. Tout ce qui suit a
été **vérifié par de vrais appels** (Haiku, dossier temporaire), sauf mention
contraire. Implémenté dans `permissions.py`, `subagents.py` et `window.py`
(voir « Dans claude-hub » plus bas).

## Sans le protocole (situation d'avant)

`Hub.launch` (`window.py`) lançait
`claude -p --input-format stream-json --output-format stream-json --verbose --permission-mode <mode>`,
écrivait le message sur stdin puis **fermait stdin**. En mode `plan` :

- les outils `ExitPlanMode`, `EnterPlanMode` et `AskUserQuestion` **ne sont pas
  proposés à Claude** (absents de `tools` dans l'événement `system/init`, et
  `ToolSearch select:ExitPlanMode` répond « No matching deferred tools
  found ») ;
- Claude écrit quand même son plan dans `~/.claude/plans/<slug>.md`, ne peut
  pas demander de validation, et s'arrête en disant que le plan est prêt ;
- le hub n'en affichait qu'une ligne d'outil `🔧 Write …`.

## La clé : `--permission-prompt-tool stdio`

Avec cette option (celle qu'utilise l'Agent SDK quand on lui donne un
`canUseTool`), les trois outils apparaissent et **toute demande de permission
passe par stdin/stdout** sous forme de messages de contrôle. Il faut donc
**garder stdin ouvert** pendant le tour, et le fermer à l'événement `result`
(sinon le processus attend le message suivant). Fonctionne aussi à travers
ssh, puisque c'est le même canal.

Pas besoin de la poignée de main `initialize` (testé avec et sans : même
résultat). `--permission-prompts host` est la valeur par défaut.

### Demande (stdout)

```json
{"type": "control_request", "request_id": "…",
 "request": {"subtype": "can_use_tool", "tool_name": "ExitPlanMode",
             "display_name": "ExitPlanMode",
             "input": {"plan": "# Plan : …\n…",
                       "planFilePath": "/home/…/.claude/plans/fais-un-plan-tr-s-snoopy-sparrow.md"},
             "tool_use_id": "toolu_…", "requires_user_interaction": true}}
```

Pour les autres outils (après validation, en mode `default`), la demande
porte aussi `description`, `decision_reason`, `decision_reason_type` et
`permission_suggestions`, par exemple
`[{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]` pour
un Write, ou `[{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "python3 hello.py"}], "behavior": "allow", "destination": "localSettings"}]`
pour un Bash.

### Réponse (stdin)

```json
{"type": "control_response",
 "response": {"subtype": "success", "request_id": "…",
              "response": {"behavior": "allow", "updatedInput": {…l'input reçu…},
                           "updatedPermissions": [{"type": "setMode", "mode": "acceptEdits",
                                                   "destination": "session"}]}}}
```

ou, pour refuser :

```json
{"type": "control_response",
 "response": {"subtype": "success", "request_id": "…",
              "response": {"behavior": "deny", "message": "Ajoute une étape : afficher aussi la date."}}}
```

Champs facultatifs vus dans le schéma : `interrupt` (deny : arrêter le tour),
`toolUseID`, `decisionClassification` (`user_temporary`, `user_permanent`,
`user_reject`).

> Attention : `destination: "localSettings"` (ou `userSettings`,
> `projectSettings`) ferait **écrire claude dans des fichiers de réglages**.
> Pour respecter le principe « ne jamais écrire dans les données des autres
> outils », n'utiliser que `destination: "session"`.

## Le cycle complet (vérifié)

1. Mode `plan` : Claude explore (lecture seule), écrit
   `~/.claude/plans/<slug>.md`, charge `ExitPlanMode` via `ToolSearch`, puis
   l'appelle avec `input.plan` (le Markdown complet) et `input.planFilePath`.
2. Le hub reçoit le `can_use_tool` d'`ExitPlanMode` → **carte « Plan »**.
3. **Refuser** (`deny` + message) : Claude reçoit le message comme résultat de
   l'outil, réécrit le plan et rappelle `ExitPlanMode` (nouvelle demande, même
   fichier). Le refus apparaît dans `permission_denials` de l'événement
   `result`.
4. **Valider** (`allow`) : claude émet `{"type":"system","subtype":"status","permissionMode":…}`
   et l'outil répond « User has approved your plan. You can now start coding… ».
   Claude enchaîne l'exécution **dans le même tour** : c'est le « build ».
   - sans `updatedPermissions`, le mode retombe à **`default`** : chaque Write et
     chaque Bash refont une demande `can_use_tool` ;
   - avec `updatedPermissions: [{"type":"setMode","mode":"acceptEdits","destination":"session"}]`,
     le mode passe à `acceptEdits` : les Write passent seuls, les Bash non
     reconnus demandent encore.
   Autre moyen de changer de mode en cours de route (vérifié avec Haiku) :
   `{"type":"control_request","request_id":"…","request":{"subtype":"set_permission_mode","mode":"acceptEdits"}}`
   envoyé sur stdin. claude répond par une `control_response` (`success`, puis
   un `system`/`status` avec le nouveau `permissionMode`) ; les outils suivants
   suivent le nouveau mode, mais une demande déjà en attente reste à trancher.
   Un mode indisponible donne une erreur, par ex. `auto` avec Haiku :
   `{"subtype":"error","error":"Cannot set permission mode to auto: auto mode unavailable for this model","error_code":"auto_mode_model"}`.
   Le hub envoie cette demande dès qu'on change le mode pendant une réponse.

Conséquence pour le hub : une fois ce canal en place, il faut aussi une carte
« Autoriser / Refuser » pour les demandes ordinaires (Write, Bash…), sinon le
build bloque en mode `default`. Avec stdin fermé, ces demandes sont refusées
d'office, comme aujourd'hui.

`AskUserQuestion` passe par le même `can_use_tool`. Réponse vérifiée :
`allow` avec `updatedInput` = l'input reçu plus
`"answers": {"<texte de la question>": "<réponse>"}` (plusieurs choix séparés
par des virgules, ou texte libre) ; Claude reçoit « The user answered: … ».

## Récupérer le plan (Markdown)

En direct : `request.input.plan` de la demande `ExitPlanMode` (le chemin est
dans `input.planFilePath`). Le bloc `tool_use` `ExitPlanMode` du flux porte le
même `input`.

Dans le `.jsonl` de la conversation (rechargement, hôtes distants) :

- bloc `tool_use` `name == "ExitPlanMode"`, `input.plan` ;
- la ligne `user` du résultat a `toolUseResult = {"plan", "isAgent", "filePath"}` ;
- **décision** (vérifié, Haiku, claude 2.1.3xx : un refus puis une validation) :
  le `tool_result` d'un plan refusé a `is_error: true` et pour contenu la
  remarque telle quelle (`toolUseResult` : `"Error: <remarque>"`,
  `toolDenialKind`) ; celui d'un plan validé commence par « User has approved
  your plan. You can now start coding… » et recopie le plan. Le mode choisi
  pour l'exécution n'est écrit nulle part : les lignes `user` des résultats
  n'ont pas de `permissionMode` (seul le message tapé en a un). Chaque
  résultat porte `permissionDecision` (`{"decision": "accept"|"reject",
  "source": "user_reject"|"user_temporary"|"config", …}`). La commande
  `history` des scripts distants en tire `decision` sur l'élément du plan :
  `{"ok", "text"}` (la remarque d'un refus), ou `{"ok": false, "interrupted":
  true}` pour un résultat `[Request interrupted by user…]` ;
- **demande perdue** (vu le 3 novembre 2026, claude 2.1.316) : si l'entrée de
  claude se ferme pendant sa réponse (claude-hub fermé ou relancé, ssh coupé),
  claude continue, mais sa demande échoue. Le `tool_result` est en erreur :
  « Tool permission request failed: AbortError: Stream closed » (entrée déjà
  fermée quand la demande part), ou « …AbortError: Tool permission stream
  closed before response received » (fermée pendant qu'elle attend).
  claude termine alors son tour sans validation. claude-hub l'affiche
  « ⚠ Demande perdue », pas « ✗ Refusé » ;
- **message reçu pendant une demande** (même session) : un message écrit sur
  l'entrée pendant qu'une demande `can_use_tool` attend est mis en file
  (`queue-operation` `enqueue` dans la transcription, sans son texte). Il n'est
  lu, et écrit, qu'après la réponse à la demande ;
- pièces jointes `attachment.type` = `plan_mode` (entrée) et
  `plan_mode_exit` (sortie) ;
- chaque message `user` porte `permissionMode` (`"plan"`, …) ;
- claude cherche aussi un champ `planContent` sur les lignes `user` et une
  pièce jointe `plan_file_reference` (`planFilePath`, `planContent`) : vu dans
  le binaire, pas dans nos essais.

En dernier recours, le fichier `~/.claude/plans/<slug>.md` (lecture seule).
Le nom est un slug aléatoire, pas l'id de session : passer par
`planFilePath`.

## Sous-agents

**En direct (stream-json)** :

- le lancement est un `tool_use` `Agent` (`input`: `description`,
  `subagent_type`, `prompt`) ;
- `{"type":"system","subtype":"task_started","task_id":…,"tool_use_id":…,"description":…,"subagent_type":…,"is_backgrounded":true,"spawn_depth":1,"prompt":…}` ;
- les messages du sous-agent (`assistant` et `user`) arrivent dans le même flux
  avec **`parent_tool_use_id` = id du `tool_use` Agent** (les messages du fil
  principal ont `parent_tool_use_id: null`) ;
- `task_progress` (`usage.total_tokens`, `tool_uses`, `duration_ms`,
  `last_tool_name`, `description` du type « Running … »), puis
  `task_updated` (`patch.status: "completed"`) et `task_notification`
  (`status`, `summary`, `output_file`, `usage`) ;
- `background_tasks_changed` signale le changement de la liste.

Les sous-agents tournent **en arrière-plan** : le tour principal peut émettre
son `result` avant la fin du sous-agent. claude attend alors la fin, puis
relance un tour (nouvel `init` et nouveau `result`) **même si stdin a été
fermé**. Le hub doit donc continuer à lire le flux jusqu'à la sortie du
processus, sans considérer le premier `result` comme la fin.

**Sur disque** :

```
~/.claude/projects/<projet>/<session>/subagents/agent-<agentId>.jsonl
~/.claude/projects/<projet>/<session>/subagents/agent-<agentId>.meta.json
```

- `meta.json` : `{"agentType":"Explore","description":…,"toolUseId":"toolu_…","spawnDepth":1,"requestShape":"background","requestNonInteractive":true}`
  → c'est le lien avec le `tool_use` Agent de la conversation ;
- chaque ligne du `.jsonl` porte `isSidechain: true`, `agentId`, `sessionId` ;
- le `tool_result` de l'outil Agent contient l'`agentId` (texte « Async agent
  launched… agentId: … ») et `toolUseResult` (`isAsync`, `status`, `agentId`,
  `outputFile`, …) ;
- des sous-dossiers sont possibles sous `subagents/` (sous-agents imbriqués,
  d'après le binaire) ;
- sortie temporaire : `/tmp/claude-<uid>/<projet>/<session>/tasks/<agentId>.output`.

## Workflows en arrière-plan (observé, claude 2.1.304)

L'outil Workflow lance des agents en série ou en parallèle, en arrière-plan. Le
tour de Claude se termine aussitôt, mais le fichier de session
(`~/.claude/sessions/<pid>.json`) reste à `"status":"busy"` jusqu'à la fin du
workflow ; `statusUpdatedAt` (ms) garde l'heure du passage à `busy`.

- **Lancement** : ligne `user` (`tool_result` de l'outil) avec
  `toolUseResult: {"status":"async_launched","taskId":"wm8uxc1as","taskType":"local_workflow","workflowName":"review","runId":"wf_f820461f-80e","summary":…,"transcriptDir":…,"scriptPath":…}`.
- **Fin du tour** : `{"type":"system","subtype":"turn_duration",…,"pendingWorkflowCount":1}`.
  Une commande locale tapée ensuite (`/workflows`) écrit des lignes `user`
  (`<command-name>`, `<local-command-stdout>`) sans relancer de tour.
- **Journal** : `<session>/subagents/workflows/<runId>/journal.jsonl`, une ligne
  `{"type":"launched"}`, puis par agent `{"type":"started","key":…,"agentId":…,"label":"r5 · pass1 · General Rust 1/2","phase":"▸ review_style #5"}`
  et `{"type":"result","key":…,"agentId":…,"result":…}`. Les agents eux-mêmes
  sont à côté (`agent-<agentId>.jsonl` et `.meta.json`, avec `workflowPhase`).
  Un agent à schéma finit par un appel `StructuredOutput` : pas de réponse texte
  finale, mais le résultat de cet outil porte `"toolEndsTurn":true` (le résultat
  d'un appel parallèle peut s'écrire après lui). La commande `agents` le compte
  comme fini. Le « 151/152 » du terminal compte les `result` sur les `started`. **Aucune
  ligne ne marque la fin** : un workflow interrompu ressemble à un workflow fini.
- **Fin** : une `<task-notification>` avec `<task-id>` = `taskId` (lignes
  `queue-operation` puis `user` ou `attachment` `queued_command`), qui relance un
  tour de Claude.

Dans claude-hub, la commande `stat` des scripts distants renvoie les workflows
lancés depuis le démarrage du processus claude (`startedAt` ; les plus anciens
sont morts avec leur processus) et pas encore notifiés, leur avancement, et
`waiting` (Claude a fini son tour : aucune ligne `assistant`, résultat d'outil ou
message qui lance un tour depuis le dernier `turn_duration`).

## Shells en arrière-plan (observé, claude 2.1.304 à 2.1.316)

Le « 1 shell » du terminal. Le fichier de session (`~/.claude/sessions/<pid>.json`)
ne dit pas lesquels tournent ; à la fin du tour, il passe à `shell` au lieu de
`idle` tant qu'un shell ou un monitor (tâche `local_bash`) tourne (vu sur une
session de terminal, claude 2.1.316 ; même code en 2.1.308).

- **Lancement**, dans la transcription (lignes `user` de la conversation principale) :
  - outil Bash avec `run_in_background`, commande passée en arrière-plan après son
    délai (`timedOutAfterMs`) ou par Ctrl+B (`backgroundedByUser`) : `tool_result`
    avec `toolUseResult.backgroundTaskId` ; la description et la commande sont dans
    le `tool_use` correspondant ;
  - commande « ! » du terminal renvoyée en arrière-plan : message
    `<bash-stdout>Command was manually backgrounded by user with ID: <id>.…`, la
    commande étant dans le `<bash-input>` qui précède.
- **Fin** : `<task-notification>` avec ce `<task-id>` (`completed`, `failed`,
  `stopped`), écrite dès la fin (`queue-operation`), même si Claude ne travaille
  pas. Un claude relancé écrit `stopped` pour les shells de la session précédente.
- **Arrêt par Claude** (`TaskStop`, autrefois `KillShell`) : pas de notification,
  seulement le résultat de l'outil, `toolUseResult: {"message":"Successfully stopped
  task: …","task_id":…,"task_type":"local_bash",…}`.
- **Arrêt depuis l'interface du terminal** : aucune trace (la notification d'un
  claude relancé le dit : « stopped via the UI … leave no transcript marker »).
- **Processus** : chaque commande Bash, au premier plan comme en arrière-plan, est
  un `bash -c` fils direct du processus claude, sa sortie (fd 1 et 2) ouverte sur
  `/tmp/claude-<uid>/<projet>/<session>/tasks/<id>.output`.

Dans claude-hub, la commande `stat` renvoie `shells` : les shells lancés depuis le
démarrage du processus claude, ni notifiés ni arrêtés, et (sous Linux) dont la
sortie est encore ouverte par un descendant du processus (`/proc/<pid>/task/*/children`,
`/proc/<fils>/fd/1`) ; sans `/proc`, la transcription seule.

Avec `claude -p` (stream-json), le flux le dit directement :
`{"type":"system","subtype":"background_tasks_changed","tasks":[{"task_id":…,"task_type":"local_bash","description":…}]}`
à chaque changement (liste vide à la fin), plus `task_started`
(`task_type: "local_bash"`, `is_backgrounded`), `task_updated` et
`task_notification`, comme pour un sous-agent. claude-hub garde l'entrée de claude
ouverte jusqu'à la notification (la tâche compte dans `tasks`), puis claude relance
un tour pour en rendre compte.

## Monitors (observé, claude 2.1.316)

Le « 1 monitor » du terminal : l'outil `Monitor` lance une commande et transmet
chaque ligne de sa sortie à Claude, comme un événement.

- **Lancement** : `tool_result` avec `toolUseResult: {"taskId":…,"timeoutMs":1800000,"persistent":false}`
  (`timeoutMs` à 0 pour un monitor `persistent`) ; la description et la commande
  sont dans le `tool_use` (`name: "Monitor"`).
- **Événements** : une `<task-notification>` par événement, avec `<task-id>`,
  `<summary>Monitor event: "<description>"</summary>` et `<event>…</event>`, mais
  **sans `<status>`** : le monitor tourne toujours. Écrite aussitôt
  (`queue-operation` `enqueue`), puis dans la conversation.
- **Fin** : `<task-notification>` avec `<status>` (`completed` quand la commande
  se termine, `failed`…), ou événement de Claude Code entre crochets :
  `[Monitor expired after 30m with 3 events delivered. …]` à l'échéance (écrit
  dès l'échéance), `[Monitor timed out …]`, `[Monitor stopped]`,
  `[Monitor stopped — too much output …]`. Arrêt par Claude : résultat de
  `TaskStop`, comme pour un shell (`task_type: "local_bash"`).
- **Processus** : `bash -c` fils du processus claude, comme un shell, mais sa
  sortie est un socket lu par claude, qui écrit lui-même `tasks/<id>.output` :
  `/proc` ne permet pas de le relier à sa tâche.

La commande `stat` renvoie les monitors dans `shells`, avec `"kind": "monitor"`
(`"shell"` pour les autres) et `event` (le dernier événement) : ceux lancés depuis
le démarrage du processus claude, pas encore finis d'après la transcription, et
seulement si la session n'est pas `idle`.

Avec `claude -p`, le flux ne les distingue pas des shells : `task_started` et
`background_tasks_changed` disent `task_type: "local_bash"`, avec la description du
monitor. claude-hub les reconnaît à l'appel de l'outil `Monitor` (`tool_use_id` de
`task_started`) ou à son résultat (`taskId` et `timeoutMs`). Les événements
arrivent comme des messages ; `task_notification` n'est émis qu'à la vraie fin.

## Demandes d'un terminal relié à Remote Control (vérifié, claude 2.1.312)

Un `claude` interactif relié à Remote Control publie chaque demande
`can_use_tool` dans les événements de sa session (`type: control_request`,
même forme qu'avec `claude -p`) et dans les métadonnées de la session :

```
GET /v1/code/sessions/cse_…  →  response_shape.external_metadata.pending_actions
[{"tool_name": "AskUserQuestion", "display_tool_name": "Question",
  "action_description": "Review scope", "input": {…l'input complet…},
  "tool_use_id": "toolu_…", "request_id": "",
  "suppressed_request_id": "6cd1dcec-…"}]
```

- Pour une question ou un plan, `request_id` est vide et l'identifiant est dans
  `suppressed_request_id` : il sert aux notifications mobiles, où l'on ne peut
  pas répondre d'un bouton (le client de claude prend l'un ou l'autre). Les
  `permission_suggestions` sont dans `requires_action_details_list`.
- `tool_name` en `dialog:…` : boîte de dialogue du terminal, pas un outil.
- **Réponse** (celle que claude.ai envoie) : un événement posté sur
  `/v1/code/sessions/cse_…/events`, avec l'en-tête
  `anthropic-client-platform: web_claude_ai` :
  `{"type":"control_response","response":{"subtype":"success","request_id":"6cd1dcec-…","response":{"behavior":"allow","updatedInput":{…,"answers":{"<question>":"<réponse>"}}}}}`.
  Le terminal ferme sa boîte de dialogue (`control_cancel_request` dans les
  événements) et reprend : `tool_result` « User answered Claude's questions… »
  (« Your questions have been answered… » selon la version).
- Essai : vrai `claude --model haiku --remote-control` dans un pseudo-terminal,
  question posée, réponse « Blue » cliquée dans claude-hub : reçue, Claude a
  répondu « BLUE ». Le `claude` de test ne doit pas hériter des variables
  `CLAUDE*` d'une session Claude Code (sinon pas de transcription ni de fichier
  `~/.claude/sessions/<pid>.json`).

Dans claude-hub : `remote_pending` et `remote_respond` (`cloud.py`) ; la fenêtre
les lit quand la conversation affichée passe en attente (`load_requests`), et
les range dans `Hub.pending` avec `remote` (l'id Remote Control) pour que les
cartes, le volet du plan et l'accueil fonctionnent comme pour `claude -p`.

## Terminal sans Remote Control (vérifié, claude 2.1.317)

**La question en attente n'est souvent pas dans la transcription.** Claude Code
n'écrit le message qui contient l'appel (texte qui le précède compris) qu'une
fois la question répondue, avec son heure d'origine. Essais avec un vrai
`claude --model haiku` dans un pseudo-terminal, question `AskUserQuestion` :

| Cas | Appel écrit pendant l'attente ? |
|---|---|
| mode `default`, tour lancé par l'utilisateur | oui |
| mode `auto` | non |
| tour relancé par la fin d'une tâche de fond (`<task-notification>`) | non |

Même constat sur une vraie session (mode auto, relancée par la fin d'un
`gh run watch`) : question posée à 10:22:10, écrite à 10:40:07, au moment de la
réponse. Si le processus est tué avant, elle n'est jamais écrite. Déplier la
question dans le fil (`open_question`) ne marche donc que dans le premier cas ;
sinon, la fenêtre ajoute à la fin du fil une carte qui dit d'aller répondre dans
le terminal (`WaitCard`, `remotecontrol.py`).

**Ce que dit `~/.claude/sessions/<pid>.json`** (aussi vu sur cerberus, 2.1.316) :

- `bridgeSessionId` (`session_…`) : présent tant que ce processus est relié à
  Remote Control, absent sinon. La transcription, elle, garde les entrées
  `bridge-session` (`cse_…`, réécrites à chaque tour) des lancements précédents :
  la commande `stat` des scripts distants prend donc le fichier de session pour
  un `claude` en cours, et la transcription seulement pour une session arrêtée.
- `waitingFor` (avec `status: "waiting"`) : `"input needed"` pour une question
  (et sans doute un plan), `"permission prompt"` pour une autorisation. `stat` le
  renvoie dans `waiting_for`.

**Relier une session en cours** : `/remote-control` tapé dans le terminal écrit
l'entrée `bridge-session` et le `bridgeSessionId`, et une question posée ensuite
est lue par l'API (`pending_actions`). Mais pas pendant qu'une question est
affichée : sa boîte de dialogue prend la saisie (« /remote-control » puis
Entrée choisit la première réponse). Il faut d'abord y répondre.

Dans claude-hub : la pastille « Sans Remote Control » de l'en-tête
(`RcChip`) s'affiche pour un `claude` en cours sans `bridgeSessionId`
(`Hub.without_remote_control`, d'après le dernier `stat`) ; un clic ouvre
l'explication. Les sessions de test reliées à Remote Control sont archivées sur
claude.ai à la fermeture de leur `claude`.

## Ce que fait l'extension VS Code (doc officielle)

Pour s'en inspirer (voir aussi la conversation « regarde l'extension claude
pour vscode ») :

- mode Plan : le plan s'ouvre en document Markdown, avec commentaires en ligne
  avant validation ; `/plan`, `/plan open` ;
- à la reprise d'une conversation terminée en mode plan, le mode plan est
  restauré ;
- sous-agents : lignes de progression sous le groupe d'outils qui les a lancés ;
  compteur « 2 agents » sous la saisie (point d'état : actif ou en attente de
  permission) ; « carte des agents » en arbre (statut, durée, jetons,
  transcription en lecture seule, arrêt) ;
- « Run in background » pour un sous-agent ou une commande longue.

## Dans claude-hub

- `launch` ajoute `--permission-prompt-tool stdio` et garde stdin ouvert ;
  `end_input` le ferme au `result`, ou plus tard si des sous-agents tournent
  encore (30 s après le dernier si claude ne relance pas de tour).
- `on_request` range la demande dans `Hub.pending` (par id de session, comme
  `Hub.streams`) ; la carte (`permissions.RequestCard`) est réaffichée si la
  vue est rechargée, et `respond` écrit la `control_response`. Une demande
  dont le processus est fini devient « Demande expirée ».
- Une autre `control_request` reçoit une réponse `error` plutôt que de laisser
  claude attendre ; `control_cancel_request` retire la carte.
- Les messages à `parent_tool_use_id` vont dans le `SubagentBlock` de l'appel
  Agent ; `task_started`/`task_progress`/`task_notification` tiennent le
  compte et l'état. Dans l'historique, les éléments `tool` portent leur `id`
  et le bloc appelle `helper agent <session> <tool_use_id> <limite>`.
- Volet « Sous-agents » (`agentsview.py`) : `helper agents <session>` lit tous
  les `.meta.json` sous `subagents/` (le `parentAgentId` donne l'arbre ; les
  imbriqués sont rangés à plat, avec `spawnDepth` 2) et résume chaque
  transcription. Fini : dernier message de Claude sans `tool_use` ;
  interrompu : dernier message `[Request interrupted by user…]` ; sinon en
  cours. Le statut `live` de la session ne suffit pas (une session pilotée
  par le SDK peut ne pas figurer dans `~/.claude/sessions`) : un agent non
  fini sans nouvelle étape depuis 10 min dans une session arrêtée est dit
  « sans nouvelles ». Les `<task-notification>` (dans la transcription du
  parent, en `queue-operation`) ne sont pas utilisées.
- Le mode `default` a été ajouté à la liste des modes, maintenant que les
  demandes ont une réponse.
- « Arrêter » (ou Échap dans la saisie) envoie une interruption (voir
  ci-dessous) ; un second clic, ou 10 s sans effet, tue le processus.
- Non fait : les messages en file relancent un `claude -p` au lieu de passer
  par le stdin ouvert.

## Interruption (vérifié, Haiku)

```json
{"type": "control_request", "request_id": "…", "request": {"subtype": "interrupt"}}
```

claude répond tout de suite
`{"type":"control_response","response":{"subtype":"success","request_id":"…","response":{"still_queued":[]}}}`,
arrête le tour (texte en cours, outil en cours ou demande de permission) et
émet un `result` `{"subtype":"error_during_execution","is_error":true}` avec
`terminal_reason` `aborted_streaming` (texte) ou `aborted_tools` (outil). Le
processus sort en **code 1** : le hub ne le traite pas comme une erreur.

- Une demande `can_use_tool` en attente est retirée par un
  `control_cancel_request` (la carte passe à « Demande annulée »).
- Un Bash lancé en arrière-plan est arrêté aussi (`task_updated` puis
  `task_notification`).
- La conversation garde ce qui a été écrit, suivi d'un message `user`
  `[Request interrupted by user]` (ou `… for tool use]`, après un
  `tool_result` de refus) : le hub l'affiche « ⏹ Interrompu ». C'est la même
  trace qu'Échap dans le terminal.

## Refaire les essais

Script minimal (Haiku, dossier temporaire ; supprimer ensuite
`~/.claude/projects/-tmp-…` et le plan créé dans `~/.claude/plans/`) :

```python
import json, subprocess, uuid
argv = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
        "--verbose", "--permission-mode", "plan", "--model", "haiku",
        "--session-id", str(uuid.uuid4()), "--permission-prompt-tool", "stdio"]
p = subprocess.Popen(argv, cwd="/tmp/essai", stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def send(o):
    p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()
send({"type": "user", "message": {"role": "user", "content": "Plan court pour créer hello.py, puis ExitPlanMode."}})
for line in p.stdout:
    e = json.loads(line)
    if e.get("type") == "control_request" and e["request"].get("subtype") == "can_use_tool":
        r = e["request"]
        send({"type": "control_response", "response": {"subtype": "success", "request_id": e["request_id"],
              "response": {"behavior": "allow", "updatedInput": r["input"]}}})
    if e.get("type") == "result":
        p.stdin.close()
p.wait()
```
