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
