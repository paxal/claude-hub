const fs = require("fs"), path = require("path"), os = require("os");
const C = path.join(os.homedir(), ".claude");
const ls = d => { try { return fs.readdirSync(d); } catch (e) { return []; } };
const lines = p => fs.readFileSync(p, "utf8").split("\n");
const parse = l => { try { return JSON.parse(l); } catch (e) { return null; } };

function textOf(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.filter(b => b && b.type === "text").map(b => b.text || "").join("\n");
  return "";
}
function imagesOf(c) {
  const out = [];
  for (const b of Array.isArray(c) ? c : []) {
    if (!b || b.type !== "image") continue;
    const src = b.source || {};
    if (src.type === "base64" && src.data) out.push({type: src.media_type || "image/png", data: src.data});
    else out.push({omitted: true});
  }
  return out;
}
function applyBudget(items, budget = 8 * 1024 * 1024) {
  for (const it of items.slice().reverse())
    for (const img of it.images || []) {
      const size = (img.data || "").length;
      if (size > budget) { delete img.data; img.omitted = true; }
      budget -= size;
    }
  return items;
}
function peerBody(t) {
  t = t.trim();
  const pre = "Another Claude session sent a message:";
  if (!t.startsWith(pre)) return null;
  return t.slice(pre.length).split("\n\nThis came from another Claude session")[0].trim() || null;
}
const AGENT_MSG = /^<agent-message from="([^"]*)">/, HANDBACK = "[Subagent hand-back]",
  REPORT_START = "The report follows:";
function dedent(t) {  // retire l'indentation commune (le harnais indente chaque ligne d'un rapport)
  const ls = t.split("\n").map(l => l.trim() ? l : "");
  const ns = ls.filter(l => l).map(l => l.length - l.replace(/^ +/, "").length);
  const n = ns.length ? Math.min(...ns) : 0;
  return ls.map(l => l.slice(n)).join("\n").trim();
}
// Élément du fil pour un message relayé : message ou rapport d'un agent, sinon envoi Remote Control.
function peerItem(body) {
  const m = AGENT_MSG.exec(body);
  if (!m) return {role: "user", text: body, via: "remote"};
  let t = body.slice(m[0].length).trim();
  if (t.endsWith("</agent-message>")) t = t.slice(0, -"</agent-message>".length);
  const report = t.startsWith(HANDBACK);
  if (report && t.includes(REPORT_START)) t = t.slice(t.indexOf(REPORT_START) + REPORT_START.length);
  else if (report) t = t.slice(HANDBACK.length);
  return {role: "user", text: dedent(t), via: "agent", from: m[1], report};
}
const isNoise = t => { t = t.trim(); return !t || t.startsWith("<") || t.startsWith("Caveat:"); };

function liveSessions() {  // fichiers ~/.claude/sessions/<pid>.json des claude en cours, par id
  const out = {}, d = path.join(C, "sessions");
  for (const f of ls(d).filter(f => f.endsWith(".json"))) {
    try {
      const s = JSON.parse(fs.readFileSync(path.join(d, f), "utf8"));
      process.kill(Number(s.pid), 0);
      out[s.sessionId] = s;
    } catch (e) {}
  }
  return out;
}
function liveIds() {
  const out = {};
  for (const [sid, s] of Object.entries(liveSessions())) out[sid] = s.status || "idle";
  return out;
}
// Vrai pour un claude lancé sans terminal (claude -p, celui de Claude Hub).
const headless = s => !!s && s.entrypoint === "sdk-cli";
const msOf = v => typeof v === "number" ? v : null;  // date (ms) du fichier de session
const textOrNull = v => typeof v === "string" && v ? v : null;  // texte non vide du fichier de session

// Ligne « user » de la conversation principale (pas d'un sous-agent), null sinon.
function userRecord(line) {
  const r = parse(line);
  return r && typeof r === "object" && r.type === "user" && !r.isSidechain ? r : null;
}
const isDict = v => !!v && typeof v === "object" && !Array.isArray(v);
// Shell ou monitor lancé en arrière-plan, ou arrêté, d'après une ligne : ["start", id,
// appel d'outil, commande, "shell" ou "monitor"], ["stop", id] ou null ; et la dernière
// commande « ! » tapée. Shell : run_in_background, délai dépassé, Ctrl+B
// (backgroundTaskId), ou commande « ! » renvoyée en arrière-plan. Monitor : résultat de
// l'outil Monitor (taskId et timeoutMs). Arrêt : résultat de TaskStop (ou de l'ancien
// KillShell), qui n'écrit pas de <task-notification>.
function shellEvent(line, lastInput) {
  if (!(line.includes('"backgroundTaskId"') || line.includes("<bash-") || line.includes("Successfully")
        || line.includes('"timeoutMs"')))
    return [null, lastInput];
  const r = userRecord(line);
  if (!r) return [null, lastInput];
  const res = r.toolUseResult, content = (r.message || {}).content;
  const shell = isDict(res) && res.backgroundTaskId;
  if (shell || (isDict(res) && res.taskId && "timeoutMs" in res)) {
    const b = (Array.isArray(content) ? content : []).find(b => isDict(b) && b.type === "tool_result");
    const tool = b ? b.tool_use_id || null : null;
    return [shell ? ["start", String(res.backgroundTaskId), tool, null, "shell"]
                  : ["start", String(res.taskId), tool, null, "monitor"], lastInput];
  }
  if (isDict(res) && String(res.message || "").startsWith("Successfully")) {
    const tid = res.task_id || res.shell_id;
    return [tid ? ["stop", String(tid)] : null, lastInput];
  }
  const t = textOf(content);
  let m = /<bash-input>([\s\S]*?)<\/bash-input>/.exec(t);
  if (m) lastInput = m[1].trim();
  m = /<bash-stdout>Command was manually backgrounded by user with ID: (\w+)/.exec(t);
  return [m ? ["start", m[1], null, lastInput, "shell"] : null, lastInput];
}
const EVENT_LIMIT = 500;  // caractères gardés du dernier événement d'un monitor
const TURN_LINES = 20;  // dernières lignes « user » ou « assistant » gardées pour trouver la fin de la réponse
// Texte d'un morceau de chaîne JSON pris tel quel dans une ligne (échappements décodés).
function jsonText(raw) {
  try { return JSON.parse('"' + raw + '"'); } catch (e) { return raw; }
}
// Tâches des <task-notification> d'une ligne : [[id, événement]], événement null quand la
// notification dit la tâche finie. Un monitor en envoie une à chaque événement (<event>,
// sans <status>) ; il finit sur un <status>, ou sur un message de Claude Code entre
// crochets (« [Monitor expired …] », « [Monitor stopped] »…).
function notified(line) {
  const out = [];
  for (let part of line.split("<task-notification>").slice(1)) {
    part = part.split("</task-notification>")[0];
    const m = /<task-id>([^<]+)<\/task-id>/.exec(part);
    if (!m) continue;
    const ev = part.includes("<status>") ? null : /<event>([\s\S]*?)<\/event>/.exec(part);
    const text = ev ? jsonText(ev[1]) : null;
    out.push([m[1], text === null || text.startsWith("[Monitor ") ? null : text]);
  }
  return out;
}

// Une passe sur la conversation p : dernière session Remote Control (cse_…), workflows
// lancés en arrière-plan depuis started (ms, démarrage du processus claude ; un workflow
// plus ancien est mort avec son processus) et pas encore notifiés comme finis, lignes
// écrites depuis la fin du dernier tour de Claude (null si aucun tour n'est fini), et
// shells et monitors lancés en arrière-plan depuis started, ni notifiés comme finis ni
// arrêtés ({id, kind : shell ou monitor, tool : appel d'outil, command : commande « ! »,
// start : epoch, event : dernier événement d'un monitor}). Aussi : sous-agents lancés en
// arrière-plan depuis started et pas encore notifiés comme finis (ids), et si la réponse de
// Claude est finie (answered : son dernier message clôt le tour).
function scanMain(p, started) {
  let bridge = null, tail = null, lastInput = null, turns = [];
  const launched = new Map(), ended = new Set(), shells = [], agents = [];
  const events = new Map();  // monitor -> son dernier événement
  for (const line of lines(p)) {
    if (!line) continue;
    if (line.includes('"bridge-session"')) { const r = parse(line); if (r && r.bridgeSessionId) bridge = r.bridgeSessionId; }
    let ev;
    [ev, lastInput] = shellEvent(line, lastInput);
    if (ev && ev[0] === "stop") ended.add(ev[1]);
    else if (ev) {
      const t = msgTime(line);
      if (started === null || t === null || t * 1000 >= started)
        shells.push({id: ev[1], kind: ev[4], tool: ev[2], command: ev[3], start: t});
    }
    if (line.includes('"local_workflow"')) {
      const r = parse(line), res = r && r.toolUseResult;
      if (r && r.type === "user" && !r.isSidechain && res && typeof res === "object" && !Array.isArray(res)
          && res.taskType === "local_workflow" && res.status === "async_launched" && res.taskId) {
        const t = msgTime(line);
        if (started === null || t === null || t * 1000 >= started) launched.set(String(res.taskId), res);
      }
    }
    if (line.includes('"async_launched"') && line.includes('"agentId"')) {
      const r = parse(line), res = r && r.toolUseResult;
      if (r && r.type === "user" && !r.isSidechain && isDict(res) && res.status === "async_launched"
          && typeof res.agentId === "string" && res.agentId) {
        const t = msgTime(line);
        if (started === null || t === null || t * 1000 >= started) agents.push(res.agentId);
      }
    }
    if (line.includes('"type":"assistant"') || line.includes('"type":"user"')) {
      turns.push(line);
      if (turns.length > 2 * TURN_LINES) turns = turns.slice(-TURN_LINES);
    }
    if (line.includes("<task-notification>"))
      for (const [tid, event] of notified(line)) {
        if (event === null) ended.add(tid);
        else events.set(tid, Array.from(event).slice(0, EVENT_LIMIT).join(""));
      }
    if (line.includes('"subtype":"turn_duration"')) tail = [];
    else if (tail !== null) tail.push(line);
  }
  for (const sh of shells) if (sh.kind === "monitor") sh.event = events.has(sh.id) ? events.get(sh.id) : null;
  const last = turns.slice(-TURN_LINES).reverse().find(startsTurn);
  return {bridge, pending: [...launched].filter(([tid]) => !ended.has(tid)).map(([, w]) => w), tail,
          shells: shells.filter(sh => !ended.has(sh.id)), agents: agents.filter(a => !ended.has(a)),
          answered: last !== undefined && endsTurn(last)};
}
// Vrai si la ligne est un message de Claude qui clôt son tour (pas un appel d'outil).
function endsTurn(line) {
  const r = parse(line);
  if (!isDict(r) || r.type !== "assistant" || r.isSidechain) return false;
  const stop = isDict(r.message) ? r.message.stop_reason : null;
  return typeof stop === "string" && stop !== "" && stop !== "tool_use";
}
// Ids des tâches dont la sortie (…/tasks/<id>.output) est ouverte par un descendant du
// processus claude pid : ses commandes Bash qui tournent, au premier plan comme en
// arrière-plan. null si /proc ne permet pas de le savoir (macOS…).
function taskOutputs(pid) {
  const root = Number(pid);
  if (!Number.isInteger(root)) return null;
  const out = new Set(), todo = [root], seen = new Set();
  while (todo.length && seen.size < 1000) {
    const p = todo.pop();
    if (seen.has(p)) continue;
    seen.add(p);
    try {
      for (const t of fs.readdirSync(`/proc/${p}/task`))
        for (const c of fs.readFileSync(`/proc/${p}/task/${t}/children`, "utf8").split(/\s+/))
          if (c) todo.push(Number(c));
    } catch (e) {
      if (p === root) return null;  // pas de /proc, ou noyau sans la liste des fils
    }
    for (const fd of p !== root ? ["1", "2"] : []) {
      let link;
      try { link = fs.readlinkSync(`/proc/${p}/fd/${fd}`); } catch (e) { continue; }
      const name = path.basename(link);
      if (path.basename(path.dirname(link)) === "tasks" && name.endsWith(".output"))
        out.add(name.slice(0, -".output".length));
    }
  }
  return out;
}
// Description et commande des appels Bash ou Monitor qui ont lancé les shells et les
// monitors (seconde passe, faite seulement quand l'un d'eux tourne).
function shellInputs(p, shells) {
  const want = new Map(shells.filter(sh => sh.tool).map(sh => [sh.tool, sh]));
  if (!want.size) return;
  for (const line of lines(p)) {
    if (!line.includes('"tool_use"') || ![...want.keys()].some(t => line.includes(t))) continue;
    const r = parse(line), content = r && isDict(r) && isDict(r.message) ? r.message.content : null;
    for (const b of Array.isArray(content) ? content : []) {
      if (!isDict(b) || b.type !== "tool_use" || !want.has(b.id)) continue;
      const inp = isDict(b.input) ? b.input : {}, sh = want.get(b.id);
      sh.description = typeof inp.description === "string" ? inp.description : "";
      if (typeof inp.command === "string") sh.command = inp.command;
    }
  }
}
// Shells et monitors en arrière-plan qui tournent encore. Shell : sa sortie est encore
// ouverte (un shell arrêté depuis le terminal ne laisse aucune trace dans la transcription),
// ou tous si /proc ne le dit pas. Monitor : claude lit sa sortie et l'écrit lui-même, mais
// sa fin est notifiée ; et la session (status) ne repasse à « idle » qu'une fois ses shells
// et monitors finis (« shell » d'ici là). {id, kind, description, command, start}, plus
// event pour un monitor.
function runningShells(p, shells, pid, status) {
  const alive = shells.some(sh => sh.kind === "shell") ? taskOutputs(pid) : null;
  shells = shells.filter(sh => sh.kind === "monitor" ? status !== "idle" : alive === null || alive.has(sh.id));
  shellInputs(p, shells);
  return shells.map(sh => Object.assign(
    {id: sh.id, kind: sh.kind, description: sh.description || "", command: sh.command || "", start: sh.start},
    sh.kind === "monitor" ? {event: sh.event} : {}));
}
// Vrai si la ligne montre Claude au travail : sa réponse, un résultat d'outil, ou un
// message qui lance un tour (pas une commande locale comme /workflows).
function startsTurn(line) {
  const r = parse(line);
  if (!r || typeof r !== "object" || r.isSidechain) return false;
  if (r.type === "assistant") return true;
  if (r.type !== "user" || r.isMeta || r.isCompactSummary) return false;
  const content = (r.message || {}).content;
  if (Array.isArray(content) && content.some(b => b && b.type === "tool_result")) return true;
  const t = textOf(content).trimStart();
  return !!t && !t.startsWith("<command-") && !t.startsWith("<local-command-");
}
// Avancement d'un workflow d'après son journal (une ligne « started » puis « result »
// par agent) : nom, agents lancés et finis, et ceux qui tournent (libellé, phase).
function workflowProgress(w, p) {
  let d = w.transcriptDir;
  if (typeof d !== "string" || !d) d = path.join(p.slice(0, -".jsonl".length), "subagents", "workflows", String(w.runId));
  const started = new Map(), done = new Set();
  let text = "";
  try { text = fs.readFileSync(path.join(d, "journal.jsonl"), "utf8"); } catch (e) {}
  for (const line of text.split("\n")) {
    const r = parse(line);
    if (!r || typeof r !== "object") continue;
    if (r.type === "started") started.set(String(r.agentId || r.key), r);
    else if (r.type === "result") done.add(String(r.agentId || r.key));
  }
  const running = [...started].filter(([k]) => !done.has(k)).map(([, r]) => ({
    label: typeof r.label === "string" ? r.label : "", phase: typeof r.phase === "string" ? r.phase : ""}));
  return {name: typeof w.workflowName === "string" ? w.workflowName : "", total: started.size,
          done: started.size - running.length, running};
}

function allFiles() {
  const res = [], P = path.join(C, "projects");
  for (const d of ls(P))
    for (const f of ls(path.join(P, d)))
      if (f.endsWith(".jsonl")) res.push(path.join(P, d, f));
  return res;
}
const isDir = d => { try { return fs.statSync(d).isDirectory(); } catch (e) { return false; } };
const find = id => allFiles().find(p => path.basename(p) === id + ".jsonl");

function recordTime(r) {  // date (epoch) d'un enregistrement de la transcription, null si elle n'est pas lisible
  const ts = (r || {}).timestamp;
  const m = typeof ts === "string" && /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,3}))?/.exec(ts);
  if (!m) return null;
  return Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000 + (m[7] ? +m[7].padEnd(3, "0") / 1000 : 0);
}
function msgTime(line) { return recordTime(parse(line)); }  // date (epoch) d'une ligne de message, null si elle n'est pas lisible
// Heure (epoch, à la seconde) des éléments items[start:], tirés d'un même enregistrement.
function stamp(items, start, t) {
  if (t !== null) for (const it of items.slice(start)) it.ts = Math.floor(t);
}

// Commande slash telle que tapée (« /nom arguments ») d'après le message que claude
// enregistre pour elle (<command-name>, <command-args>…) ; null si t n'en est pas une.
function commandText(t) {
  const m = t.trimStart().startsWith("<command-") ? /<command-name>(\/[^<]*)<\/command-name>/.exec(t) : null;
  if (!m) return null;
  const a = /<command-args>([\s\S]*?)<\/command-args>/.exec(t);
  return (m[1] + " " + (a ? a[1] : "")).split(/\s+/).filter(Boolean).join(" ");
}
// Commandes « ! » d'un message : mode bash du terminal (commande puis sortie, en deux
// messages) ou envoyées par claude-hub (avant le texte). Ajoutées à items ({role:
// "bash", command, stdout, stderr}) ; renvoie le reste du texte.
function bashItems(t, items) {
  if (!t.includes("<bash-")) return t;
  return t.replace(/<(bash-input|bash-stdout|bash-stderr)>([\s\S]*?)<\/\1>/g, (_m, tag, body) => {
    if (tag === "bash-input") {
      items.push({role: "bash", command: body.trim()});
      return "";
    }
    const key = tag === "bash-stdout" ? "stdout" : "stderr";
    const last = items[items.length - 1];
    if (!(last && last.role === "bash" && !(key in last))) items.push({role: "bash", command: ""});  // sortie sans sa commande
    items[items.length - 1][key] = body;
    return "";
  }).trim();
}
// Avertissement que claude écrit juste avant une commande locale (/rename, /copy…).
const isCaveat = line => line.includes("<local-command-caveat>") && line.includes('"type":"user"');
// Commande locale de claude (/rename…), avertissement compris : pas un message, ni une
// activité. caveat : le message précédent était l'avertissement. Une commande qui en est
// dépourvue (skill, /plan suivi d'une demande) part à Claude : c'est un message.
const isCommand = (line, caveat) => isCaveat(line) || (caveat && line.includes('"content":"<command-name>/'));

// Ligne qui change l'ordre de la liste : message envoyé, réponse finie ou question
// posée. Les étapes d'une réponse (outils, réflexion, résultats) n'en font pas partie.
function isStop(line) {
  if (line.includes('"isMeta":true') || line.includes('"isSidechain":true')
      || line.includes('"isCompactSummary":true')) return false;  // résumé écrit par claude en compactant
  if (line.includes('"type":"user"')) return !line.includes('"type":"tool_result"');
  if (line.includes('"type":"assistant"'))
    return !line.includes('"stop_reason":"tool_use"') || line.includes('"name":"AskUserQuestion"')
      || line.includes('"name":"ExitPlanMode"');
  return false;
}

function summary(p) {
  const st = fs.statSync(p);
  const s = {id: path.basename(p, ".jsonl"), mtime: st.mtimeMs / 1000, size: st.size,
             cwd: null, title: null, first: null, next: null, msgs: 0, bridge: null};
  let last = null;  // dernier arrêt (isStop) : sa date sert au tri, pas celle du fichier
  let caveat = false;  // message précédent : avertissement d'une commande locale
  for (const line of lines(p)) {
    if (line.includes('"type":"user"')) {
      const skip = isCommand(line, caveat);
      caveat = isCaveat(line);
      if (skip) continue;
    }
    if (isStop(line)) last = line;
    if (line.includes('"type":"user"')) {
      s.msgs++;
      if (s.first && s.cwd) continue;
    } else if (!['"ai-title"', '"agent-name"', '"custom-title"', '"continued-in"', '"bridge-session"']
                 .some(k => line.includes(k))) {
      continue;
    }
    const r = parse(line);
    if (!r) continue;
    if (r.type === "user") {
      s.cwd = s.cwd || r.cwd || null;
      let t = textOf((r.message || {}).content);
      t = bashItems(commandText(t) || t, []);
      if (!s.first && !r.isMeta && !isNoise(t)) s.first = t.split(/\s+/).join(" ").trim().slice(0, 200);
    } else if (r.type === "custom-title") s.title = r.customTitle || s.title;
    else if (r.type === "agent-name") s.title = r.agentName || s.title;
    else if (r.type === "ai-title" && !s.title) s.title = r.aiTitle;
    else if (r.type === "continued-in") s.next = r.continuedInSessionId;
    else if (r.type === "bridge-session") s.bridge = r.bridgeSessionId || s.bridge;
  }
  // une session restée ouverte dans un terminal réécrit ses métadonnées en fin de
  // fichier sans nouveau message : la date du fichier ne suffit pas.
  s.mtime = (last && msgTime(last)) || s.mtime;
  return s;
}

const TODO_TOOLS = ["TodoWrite", "TaskCreate", "TaskUpdate"];

// Numéro attribué par TaskCreate, d'après le résultat de l'outil ; null sinon.
function createdTask(r, creating) {
  const c = (r.message || {}).content;
  for (const b of Array.isArray(c) ? c : []) {
    if (!b || typeof b !== "object" || b.type !== "tool_result" || !creating.has(b.tool_use_id)) continue;
    const res = r.toolUseResult;
    const task = res && typeof res === "object" && !Array.isArray(res) ? res.task : null;
    const text = typeof b.content === "string" ? b.content : (Array.isArray(b.content) ? b.content : [])
      .filter(x => x && typeof x === "object").map(x => String(x.text || "")).join(" ");
    const m = /Task #(\w+) created/.exec(text);
    const tid = task && typeof task === "object" && task.id !== undefined && task.id !== null ? String(task.id)
      : (m ? m[1] : null);
    return {created: b.tool_use_id, task: tid};
  }
  return null;
}

const PLAN_REMARK = 2000;  // caractères gardés de la remarque jointe au refus d'un plan

// Décision prise sur un plan (ExitPlanMode), d'après le résultat de l'outil : validé (ok),
// refusé avec la remarque transmise à Claude (text), ou demande interrompue. Notée sur
// l'élément du plan (plans : id de l'appel -> élément). Le mode d'exécution choisi en
// validant n'est pas dans la transcription.
function planDecision(c, plans) {
  for (const b of Array.isArray(c) ? c : []) {
    if (!b || typeof b !== "object" || b.type !== "tool_result" || typeof b.tool_use_id !== "string"
        || !plans.has(b.tool_use_id)) continue;
    const ok = !b.is_error;
    const text = ok ? "" : resultText(b.content);
    plans.get(b.tool_use_id).decision = text.startsWith("[Request interrupted by user")
      ? {ok: false, text: "", interrupted: true} : {ok, text: cut(text, PLAN_REMARK)};
  }
}

// Messages et appels d'outils d'une transcription ; renvoie [items, dernier modèle, son effort].
// todos : liste complétée par les appels de TodoWrite, TaskCreate et TaskUpdate de toute
// la transcription, et par les numéros attribués aux tâches créées.
// Taille du contexte après un message de Claude : jetons d'entrée (cache compris) et de sortie.
function usageTokens(u) {
  if (!u || typeof u !== "object" || Array.isArray(u)) return 0;
  return ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"]
    .reduce((n, k) => n + (Number.isInteger(u[k]) ? u[k] : 0), 0);
}

// Message tapé par l'utilisateur : ni résultat d'outil, ni texte ajouté par claude, ni
// mention d'une interruption.
function isPrompt(r) {
  if (r.type !== "user" || r.isMeta || r.isCompactSummary) return false;
  const c = (r.message || {}).content;
  if (Array.isArray(c) && c.some(b => isObj(b) && b.type === "tool_result")) return false;
  return !textOf(c).startsWith("[Request interrupted");
}

// uuid des enregistrements abandonnés de la transcription p. Un message modifié
// (claude-hub lance `claude -p --resume <id> --resume-session-at <uuid>`) ou un retour
// en arrière du terminal ajoute un message de l'utilisateur rattaché (parentUuid) à un
// enregistrement qui a déjà une suite : claude ne garde que la branche la plus récente,
// ce qui descendait déjà de ce point est abandonné. Les appels d'outils en parallèle
// forment aussi des branches (l'appel suivant et le résultat du premier ont le même
// parent) : seul un message tapé marque un retour.
function abandoned(p) {
  const kids = new Map(), dropped = new Set();
  for (const line of lines(p)) {
    if (!line.includes('"uuid"')) continue;
    const r = parse(line);
    const uid = isObj(r) && !r.isSidechain ? r.uuid : null;
    const up = uid ? r.parentUuid || r.logicalParentUuid : null;  // après /compact : le dernier message d'avant
    if (!up) continue;
    if (kids.has(up) && isPrompt(r)) {
      const todo = [...kids.get(up)];
      while (todo.length) {
        const u = todo.pop();
        if (!dropped.has(u)) {
          dropped.add(u);
          todo.push(...(kids.get(u) || []));
        }
      }
    }
    if (!kids.has(up)) kids.set(up, []);
    kids.get(up).push(uid);
  }
  return dropped;
}

// Pour la conversation principale, les branches abandonnées (message modifié) sont
// ignorées : seule compte celle que claude reprendra.
// usage : objet dont "context" reçoit la taille du contexte au dernier message de Claude
// ({tokens, model}), null après une compaction.
function transcriptItems(p, sidechain = false, todos = null, usage = null) {
  const dropped = sidechain ? new Set() : abandoned(p);
  const items = [], creating = new Set();
  const plans = new Map();  // id de l'appel ExitPlanMode -> élément du plan, qui recevra la décision
  let model = null, effort = null;
  let caveat = false;  // message précédent : avertissement d'une commande locale (/rename…)
  let mark = 0, when = null;  // éléments de l'enregistrement en cours (à dater), sa date
  for (const line of lines(p)) {
    if (!line.includes('"type":"user"') && !line.includes('"type":"assistant"')
        && !line.includes('"queued_command"')) continue;
    const r = parse(line);
    if (!r) continue;
    stamp(items, mark, when);
    mark = items.length;
    when = recordTime(r);
    if ((r.isSidechain && !sidechain) || (dropped.size && dropped.has(r.uuid))) continue;
    if (r.type === "attachment") {  // message envoyé pendant une réponse, lu entre deux étapes
      const a = r.attachment || {};
      if (a.type === "queued_command" && (a.commandMode === undefined ? "prompt" : a.commandMode) === "prompt") {
        const t = bashItems(textOf(a.prompt), items), imgs = imagesOf(a.prompt);
        if (imgs.length || !isNoise(t)) items.push({role: "user", text: isNoise(t) ? "" : t, images: imgs, queued: true});
      }
      continue;
    }
    const c = (r.message || {}).content;
    if (r.isCompactSummary) {  // conversation compactée : claude repart de ce résumé
      items.push({role: "compact", text: textOf(c)});
      if (usage) usage.context = null;
      continue;
    }
    let local = false;
    if (r.type === "user") {
      local = caveat;
      caveat = !!r.isMeta && textOf(c).includes("<local-command-caveat>");
    }
    if (r.isMeta) {  // message relayé par Remote Control : on garde son texte
      const body = (r.origin || {}).kind === "peer" ? peerBody(textOf(c)) : null;
      if (body) items.push(peerItem(body));
      continue;
    }
    if (r.type === "user") {
      if (plans.size) planDecision(c, plans);
      const ev = creating.size && todos ? createdTask(r, creating) : null;
      if (ev) todos.push(ev);
      let t = textOf(c);
      if (!local) t = commandText(t) || t;  // commande tapée (skill, /plan …) : affichée comme dans le terminal
      t = bashItems(t, items);  // commandes « ! », avant le texte du message
      const imgs = imagesOf(c);
      if (imgs.length || !isNoise(t)) {
        items.push({role: "user", text: isNoise(t) ? "" : t, images: imgs});
        if (r.parentUuid && !sidechain) items[items.length - 1].parent = r.parentUuid;  // point de copie « avant ce message »
      }
      continue;
    }
    const m = (r.message || {}).model;
    if (m && m !== "<synthetic>") {  // dernier modèle qui a répondu, et son effort
      model = m;
      effort = r.effort === undefined ? null : r.effort;
      const n = usage ? usageTokens((r.message || {}).usage) : 0;
      if (n) usage.context = {tokens: n, model: m};
    }
    for (const b of Array.isArray(c) ? c : []) {
      if (b.type === "text" && (b.text || "").trim()) {
        items.push({role: "assistant", text: b.text});
        if (r.uuid && !sidechain) items[items.length - 1].uuid = r.uuid;  // point de copie « jusqu'ici »
      }
      else if (b.type === "tool_use") {
        items.push({role: "tool", name: b.name, input: b.input, id: b.id === undefined ? null : b.id});
        if (b.name === "ExitPlanMode" && typeof b.id === "string") plans.set(b.id, items[items.length - 1]);
        if (todos && TODO_TOOLS.includes(b.name)) {
          todos.push(items[items.length - 1]);
          if (b.name === "TaskCreate") creating.add(b.id === undefined ? null : b.id);
        }
      }
    }
  }
  stamp(items, mark, when);
  return [items, model, effort];
}

function history(id, limit) {
  const p = find(id);
  const todos = [], usage = {context: null};
  const [items, model, effort] = p ? transcriptItems(p, false, todos, usage) : [[], null, null];
  return {size: p ? fs.statSync(p).size : 0, model, effort, total: items.length, items: applyBudget(items.slice(-limit)), todos,
    agents: p ? agentsList(p) : [], workflow_runs: workflowRuns(p), context: usage.context};
}

// Transcription du sous-agent lancé par l'appel d'outil key (subagents/agent-*.jsonl), ou
// dont l'id est key : les agents d'un workflow n'ont pas d'appel d'outil (pas de toolUseId).
function agent(id, key, limit) {
  const p = find(id);
  const walk = d => {
    for (const n of ls(d).sort()) {
      const f = path.join(d, n);
      if (isDir(f)) { const r = walk(f); if (r) return r; continue; }
      if (!n.endsWith(".meta.json")) continue;
      let meta;
      try { meta = JSON.parse(fs.readFileSync(f, "utf8")); } catch (e) { continue; }
      const jsonl = path.join(d, n.slice(0, -".meta.json".length) + ".jsonl");
      if (!meta || typeof meta !== "object" || Array.isArray(meta)
          || (meta.toolUseId !== key && n !== "agent-" + key + ".meta.json")) continue;
      try { if (!fs.statSync(jsonl).isFile()) continue; } catch (e) { continue; }
      const [items, model] = transcriptItems(jsonl, true);
      return {found: true, meta, model, total: items.length, items: applyBudget(items.slice(-limit))};
    }
    return null;
  };
  return (p && walk(p.slice(0, -".jsonl".length) + "/subagents")) || {found: false, meta: null, total: 0, items: []};
}

// Début d'un texte, en caractères (comme le découpage de Python, emojis compris).
const cut = (s, n) => Array.from(s).slice(0, n).join("");

// Entrée d'un appel d'outil réduite à ses textes courts (aperçu de la dernière étape).
const shortInput = inp => inp && typeof inp === "object" && !Array.isArray(inp)
  ? Object.fromEntries(Object.entries(inp).filter(([, v]) => typeof v === "string").map(([k, v]) => [k, cut(v, 300)]))
  : {};

// État d'un sous-agent d'après sa transcription : début, dernière étape, fini ou non.
// Fini : son dernier message est une réponse de Claude sans appel d'outil (il a rendu
// la main) ; interrompu : c'est la marque d'interruption de claude ; sinon il attend un
// outil ou la suite du modèle.
function agentSummary(p, meta) {
  const name = path.basename(p).slice(0, -".jsonl".length);
  const d = path.dirname(p);  // agent d'un workflow : subagents/workflows/<run>/
  const a = {id: name.startsWith("agent-") ? name.slice("agent-".length) : name,
    type: meta.agentType ?? null, description: meta.description ?? null,
    tool: meta.toolUseId ?? null, parent: meta.parentAgentId ?? null,
    depth: meta.spawnDepth || 1, background: meta.requestShape === "background",
    workflow: path.basename(path.dirname(d)) === "workflows" ? path.basename(d) : null,
    phase: typeof meta.workflowPhase === "string" ? meta.workflowPhase : null,
    size: fs.statSync(p).size, start: null, end: null, steps: 0, done: false, interrupted: false, last: null};
  let ended = false;  // un outil a terminé l'agent (le résultat d'un appel parallèle peut suivre)
  for (const line of lines(p)) {
    if (!line.includes('"type":"user"') && !line.includes('"type":"assistant"')) continue;
    const r = parse(line);
    if (!r || (r.type !== "user" && r.type !== "assistant") || r.isMeta) continue;
    const t = msgTime(line);
    a.start = a.start || t;
    a.end = t || a.end;
    const content = (r.message || {}).content;
    const blocks = Array.isArray(content) ? content.filter(b => b && typeof b === "object" && !Array.isArray(b)) : [];
    if (r.type === "user") {
      ended = ended || r.toolEndsTurn === true;
      a.done = ended;
      a.interrupted = textOf(content).startsWith("[Request interrupted by user");
      continue;
    }
    ended = a.interrupted = false;
    const tools = blocks.filter(b => b.type === "tool_use");
    const texts = blocks.filter(b => b.type === "text" && (b.text || "").trim()).map(b => b.text);
    a.steps += tools.length;
    if (tools.length) {
      const last = tools[tools.length - 1];
      a.last = {role: "tool", name: last.name ?? null, input: shortInput(last.input)};
    } else if (texts.length) a.last = {role: "assistant", text: cut(texts[texts.length - 1], 300)};
    a.done = !tools.length && (texts.length > 0 || blocks.length > 0);
  }
  return a;
}

// Sous-agents de la conversation p (subagents/, y compris imbriqués), du plus ancien au plus récent.
function agentsList(p) {
  const res = [];
  const walk = d => {
    for (const n of ls(d).sort()) {
      const f = path.join(d, n);
      if (isDir(f)) { walk(f); continue; }
      if (!n.endsWith(".meta.json")) continue;
      const jsonl = path.join(d, n.slice(0, -".meta.json".length) + ".jsonl");
      try {
        const meta = JSON.parse(fs.readFileSync(f, "utf8"));
        if (meta && typeof meta === "object" && !Array.isArray(meta) && fs.statSync(jsonl).isFile())
          res.push(agentSummary(jsonl, meta));
      } catch (e) {}
    }
  };
  if (p) walk(p.slice(0, -".jsonl".length) + "/subagents");
  return res.sort((x, y) => (x.start || 0) - (y.start || 0) || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
}

// Workflows lancés par la conversation p, dans l'ordre : run (dossier de ses agents sous
// subagents/workflows/), nom, résumé, appel d'outil qui l'a lancé et date. Lus dans le
// résultat de l'outil Workflow ; la transcription n'est relue que si un workflow a laissé
// des agents.
function workflowRuns(p) {
  if (!p || !isDir(p.slice(0, -".jsonl".length) + "/subagents/workflows")) return [];
  const runs = new Map();
  for (const line of lines(p)) {
    if (!line.includes('"local_workflow"')) continue;
    const r = parse(line);
    const res = r && typeof r === "object" && !Array.isArray(r) ? r.toolUseResult : null;
    if (!res || typeof res !== "object" || Array.isArray(res) || r.type !== "user" || r.isSidechain
        || res.taskType !== "local_workflow" || !textOrNull(res.runId) || runs.has(res.runId)) continue;
    const content = (r.message || {}).content;
    const tools = Array.isArray(content)
      ? content.filter(b => b && typeof b === "object" && b.type === "tool_result").map(b => b.tool_use_id) : [];
    runs.set(res.runId, {id: res.runId, name: textOrNull(res.workflowName), summary: textOrNull(res.summary),
      tool: tools.length ? textOrNull(tools[0]) : null, start: recordTime(r)});
  }
  return [...runs.values()];
}

const RESULT_LIMIT = 100000;  // caractères de sortie renvoyés au plus

// Texte d'un bloc tool_result (chaîne, ou liste de blocs texte et image).
function resultText(c) {
  if (typeof c === "string") return c;
  const parts = [];
  for (const b of Array.isArray(c) ? c : []) {
    if (b && typeof b === "object" && b.type === "text") parts.push(String(b.text || ""));
    else if (b && typeof b === "object" && b.type === "image") parts.push("[image]");
  }
  return parts.join("\n");
}

function findResult(p, toolId) {
  const key = '"tool_use_id":' + JSON.stringify(toolId);
  for (const line of lines(p)) {
    if (!line.includes(key) || !line.includes('"type":"user"')) continue;
    const c = ((parse(line) || {}).message || {}).content;
    for (const b of Array.isArray(c) ? c : [])
      if (b && typeof b === "object" && b.type === "tool_result" && b.tool_use_id === toolId) return b;
  }
  return null;
}

// Sortie de l'appel d'outil toolId, dans la conversation ou un de ses sous-agents.
function result(id, toolId) {
  const p = find(id);
  const paths = p ? [p] : [];
  const walk = d => {
    for (const n of ls(d).sort()) {
      const f = path.join(d, n);
      if (isDir(f)) walk(f);
      else if (n.endsWith(".jsonl")) paths.push(f);
    }
  };
  if (p) walk(p.slice(0, -".jsonl".length) + "/subagents");
  for (const f of paths) {
    const b = findResult(f, toolId);
    if (!b) continue;
    const chars = Array.from(resultText(b.content));  // longueur en caractères, comme python
    return {found: true, error: !!b.is_error, length: chars.length, text: chars.slice(0, RESULT_LIMIT).join("")};
  }
  return {found: false};
}

const EDIT_TOOLS = ["Edit", "MultiEdit", "Write", "NotebookEdit"];
const EDIT_LIMIT = 200000;  // caractères gardés de chaque texte d'une modification
const EDIT_KEYS = ["file_path", "notebook_path", "old_string", "new_string", "replace_all", "content",
                   "new_source", "edit_mode", "cell_id"];
const isObj = v => !!v && typeof v === "object" && !Array.isArray(v);

// Entrée d'une modification réduite à ce qui sert à son diff (textes tronqués).
function editInput(inp) {
  const trim = v => typeof v === "string" ? cut(v, EDIT_LIMIT) : v;
  const out = {};
  for (const k of EDIT_KEYS) if (k in inp) out[k] = trim(inp[k]);
  if (Array.isArray(inp.edits))
    out.edits = inp.edits.filter(isObj).map(e => {
      const o = {};
      for (const k of ["old_string", "new_string"]) if (k in e) o[k] = trim(e[k]);
      return o;
    });
  return out;
}

// Modifications de fichiers d'une transcription (Edit, MultiEdit, Write, NotebookEdit) :
// fichier, issue (ok, error, pending) et diff calculé par claude (structuredPatch).
function fileEdits(p, agentId = null) {
  const out = [], byId = new Map();
  for (const line of lines(p)) {
    const toolUse = line.includes('"type":"assistant"') && line.includes('"tool_use"');
    if (!toolUse && !(byId.size && line.includes('"tool_result"'))) continue;
    const r = parse(line);
    if (!isObj(r) || (r.isSidechain && agentId === null)) continue;
    const c = (r.message || {}).content;
    for (const b of Array.isArray(c) ? c : []) {
      if (!isObj(b)) continue;
      if (r.type === "assistant" && b.type === "tool_use" && EDIT_TOOLS.includes(b.name)) {
        const inp = isObj(b.input) ? b.input : {};
        const e = {id: b.id === undefined ? null : b.id, tool: b.name,
                   path: inp.file_path || inp.notebook_path || null,
                   time: msgTime(line), agent: agentId, status: "pending",
                   input: editInput(inp), patch: null, created: false};
        out.push(e);
        if (typeof b.id === "string") byId.set(b.id, e);
      } else if (r.type === "user" && b.type === "tool_result" && byId.has(b.tool_use_id)) {
        const e = byId.get(b.tool_use_id);
        e.status = b.is_error ? "error" : "ok";
        const res = r.toolUseResult;
        if (isObj(res)) {
          if (Array.isArray(res.structuredPatch) && res.structuredPatch.length) e.patch = res.structuredPatch;
          e.created = res.type === "create";
        }
      }
    }
  }
  return out;
}

// Fichiers modifiés par la conversation et ses sous-agents, dans l'ordre des modifications.
function edits(id) {
  const p = find(id);
  const paths = [];
  const walk = d => {
    for (const n of ls(d)) {
      const f = path.join(d, n);
      if (isDir(f)) walk(f);
      else if (n.endsWith(".jsonl")) paths.push(f);
    }
  };
  if (p) walk(p.slice(0, -".jsonl".length) + "/subagents");
  let out = p ? fileEdits(p) : [];
  for (const f of paths.sort()) {
    const name = path.basename(f).slice(0, -".jsonl".length);
    out = out.concat(fileEdits(f, name.startsWith("agent-") ? name.slice("agent-".length) : name));
  }
  out.sort((x, y) => (x.time || 0) - (y.time || 0));
  return {size: p ? fs.statSync(p).size : 0, edits: out};
}

const norm = t => t.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");

function snippet(text, word, width = 70) {
  const flat = text.split(/\s+/).join(" ").trim();
  const i = norm(flat).indexOf(word);
  if (i < 0) return flat.slice(0, 2 * width);
  const start = Math.max(0, i - width), end = i + word.length + width;
  return (start ? "…" : "") + flat.slice(start, end) + (end < flat.length ? "…" : "");
}

function search(query, limit) {
  const words = norm(query).split(/\s+/).filter(Boolean);
  let files = allFiles();
  if (!words.length || !files.length) return {};
  try {  // pré-filtre rapide sur le mot le plus long
    const longest = query.split(/\s+/).reduce((x, y) => (y.length > x.length ? y : x), "");
    const r = require("child_process").spawnSync("grep", ["-l", "-i", "-F", "--", longest, ...files],
                                                  {encoding: "utf8", maxBuffer: 1 << 26});
    if (!r.error) files = r.stdout.split("\n").filter(Boolean);
    files.sort((x, y) => fs.statSync(y).mtimeMs - fs.statSync(x).mtimeMs);  // récentes d'abord
  } catch (e) {}
  const res = {};
  for (const f of files) {
    const found = new Set();
    let first = null;
    for (const line of lines(f)) {
      if ((!line.includes('"type":"user"') && !line.includes('"type":"assistant"')) || line.includes('"tool_result"')
          || line.includes('"isCompactSummary":true')) continue;
      const low = norm(line);
      if (!words.some(w => low.includes(w))) continue;
      const r = parse(line);
      if (!r) continue;
      const c = (r.message || {}).content;
      const texts = [textOf(c)];
      for (const b of Array.isArray(c) ? c : [])
        if (b && b.type === "tool_use" && b.input && typeof b.input === "object")
          texts.push(Object.values(b.input).filter(v => typeof v === "string").join(" "));
      for (const t of texts) {
        const nt = norm(t), hit = words.filter(w => nt.includes(w));
        hit.forEach(w => found.add(w));
        if (hit.length && !first && !(r.type === "user" && isNoise(t)))
          first = {role: r.type === "user" ? "user" : "assistant", snippet: snippet(t, hit[0])};
      }
    }
    if (first && words.every(w => found.has(w))) {
      res[path.basename(f, ".jsonl")] = first;
      if (Object.keys(res).length >= limit) break;
    }
  }
  return res;
}

// Notes de claude-hub : ~/.config/claude-hub/notes/<id>.md (pas dans ~/.claude).
const NOTE_EXCERPT = 1000, NOTE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const notesDir = () => path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"),
                                 "claude-hub", "notes");
// Début de chaque note rangée sur cet hôte, par id de conversation.
function notesIndex() {
  const d = notesDir(), out = {};
  let names;
  try { names = fs.readdirSync(d).sort(); } catch (e) { return out; }
  for (const n of names) {
    if (n.startsWith(".") || !n.endsWith(".md")) continue;
    let t;
    try { t = fs.readFileSync(path.join(d, n), "utf8"); } catch (e) { continue; }
    if (t.trim()) out[n.slice(0, -3)] = t.length <= NOTE_EXCERPT ? t : Array.from(t).slice(0, NOTE_EXCERPT).join("");
  }
  return out;
}
// Texte de la note d'une conversation (null s'il n'y en a pas).
function note(id) {
  if (!NOTE_ID.test(id || "")) return {error: "identifiant invalide"};
  try { return {text: fs.readFileSync(path.join(notesDir(), id + ".md"), "utf8")}; }
  catch (e) { return e.code === "ENOENT" ? {text: null} : {error: "lecture refusée"}; }
}

const a = process.argv.slice(2);
let out;
if (a[0] === "list") {
  const live = liveIds();
  const sessions = allFiles().map(summary).filter(s => s.msgs && !s.next)
    .map(s => Object.assign(s, {live: live[s.id] || null, cwd_exists: s.cwd ? isDir(s.cwd) : null}))
    .sort((x, y) => y.mtime - x.mtime);
  out = {home: os.homedir(), sessions, notes: notesIndex()};
} else if (a[0] === "note") {
  out = note(a[1]);
} else if (a[0] === "notes") {
  out = notesIndex();
} else if (a[0] === "stat") {
  const p = find(a[1]), sess = liveSessions()[a[1]] || null;
  const scan = p ? scanMain(p, msOf((sess || {}).startedAt))
    : {bridge: null, pending: [], tail: null, shells: [], agents: [], answered: false};
  const workflows = sess ? scan.pending.map(w => workflowProgress(w, p)) : [];
  // claude en cours : sa session Remote Control est notée dans son fichier de session
  // (la transcription garde celles des lancements précédents)
  const bridge = sess ? textOrNull(sess.bridgeSessionId) : scan.bridge;
  const shells = sess ? runningShells(p, scan.shells, sess.pid, sess.status) : [];
  out = {size: p ? fs.statSync(p).size : 0, live: sess ? sess.status || "idle" : null, bridge,
         // ce qu'attend claude : « input needed » (question, plan), « permission prompt »
         waiting_for: sess ? textOrNull(sess.waitingFor) : null,
         since: sess ? msOf(sess.statusUpdatedAt) : null, workflows,
         // shells et monitors en arrière-plan (« 1 shell », « 1 monitor » du terminal)
         shells,
         // Claude a fini son tour et attend la fin des workflows
         waiting: workflows.length > 0 && scan.tail !== null && !scan.tail.some(startsTurn),
         // claude sans terminal (claude -p) : son fichier de session reste à « busy » une
         // fois la réponse finie
         headless: headless(sess),
         // … dont la réponse est finie et qui n'attend plus rien en arrière-plan : l'arrêter
         // ne perd rien
         finished: headless(sess) && scan.answered && !workflows.length && !shells.length
           && !scan.agents.length};
} else if (a[0] === "stop") {
  // Arrête le claude sans terminal (claude -p) de la session, que Claude Hub avait lancé
  // avant de redémarrer ; jamais celui d'un terminal. Attend sa fin (5 s au plus).
  const sess = liveSessions()[a[1]] || null;
  const pid = headless(sess) ? Number(sess.pid) : 0;
  let stopped = false;
  if (Number.isInteger(pid) && pid > 1) {  // 0 ou -1 viseraient tout un groupe de processus
    const pause = new Int32Array(new SharedArrayBuffer(4));
    try {
      process.kill(pid, "SIGTERM");
      for (let i = 0; i < 50; i++) {
        Atomics.wait(pause, 0, 0, 100);
        process.kill(pid, 0);
      }
    } catch (e) { stopped = true; }
  }
  out = {stopped};
} else if (a[0] === "search") {
  out = search(a[1], Number(a[2]));
} else if (a[0] === "exists") {
  out = Object.fromEntries(JSON.parse(a[1]).map(d => [d, isDir(d)]));
} else if (a[0] === "live") {
  // état de chaque session en cours, et sous « <id>:since » la date (ms) où elle y est passée
  out = {};
  for (const [sid, s] of Object.entries(liveSessions())) {
    out[sid] = s.status || "idle";
    if (msOf(s.statusUpdatedAt) !== null) out[sid + ":since"] = s.statusUpdatedAt;
  }
} else if (a[0] === "history") {
  out = history(a[1], Number(a[2]));
} else if (a[0] === "agent") {
  out = agent(a[1], a[2], Number(a[3]));
} else if (a[0] === "agents") {
  const p = find(a[1]);
  out = {live: liveIds()[a[1]] || null, agents: p ? agentsList(p) : [], workflow_runs: workflowRuns(p)};
} else if (a[0] === "result") {
  out = result(a[1], a[2]);
} else if (a[0] === "edits") {
  out = edits(a[1]);
} else if (a[0] === "defaults") {
  // Modèle et effort que prendrait claude sans --model ni --effort (null si non réglés).
  let st = {};
  try { st = JSON.parse(fs.readFileSync(path.join(C, "settings.json"), "utf8")); } catch (e) {}
  st = st && typeof st === "object" && !Array.isArray(st) ? st : {};
  const env = st.env && typeof st.env === "object" && !Array.isArray(st.env) ? st.env : {};
  const pick = (v, k) => process.env[v] || env[v] || st[k] || null;
  out = {model: pick("ANTHROPIC_MODEL", "model"), effort: pick("CLAUDE_CODE_EFFORT_LEVEL", "effortLevel")};
} else if (a[0] === "dirs") {
  const p = path.resolve((a[1] || "~").replace(/^~(?=$|\/)/, os.homedir()));
  out = {path: p, home: os.homedir(), dirs: []};
  try {
    out.dirs = fs.readdirSync(p).filter(n => isDir(path.join(p, n)))
      .sort((x, y) => (x[0] === ".") - (y[0] === ".") || x.toLowerCase().localeCompare(y.toLowerCase()));
  } catch (e) { out.error = e.message; }
} else if (a[0] === "read") {
  // Fichier cité dans une conversation : texte (tronqué) ou image en base64.
  const IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                       ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
                       ".bmp": "image/bmp", ".ico": "image/x-icon", ".tif": "image/tiff",
                       ".tiff": "image/tiff", ".avif": "image/avif", ".jxl": "image/jxl"};
  const READ_LIMIT = 1000000, IMAGE_LIMIT = 8 * 1024 * 1024;
  const p = path.resolve((a[1] || "").replace(/^~(?=$|\/)/, os.homedir()));
  out = {path: p};
  try {
    const st = fs.statSync(p);
    if (st.isDirectory()) out.error = "c'est un dossier";
    else if (!st.isFile()) out.error = "pas un fichier ordinaire";
    else {
      out.size = st.size;
      const kind = IMAGE_TYPES[path.extname(p).toLowerCase()];
      if (kind && st.size > IMAGE_LIMIT) out.error = "image trop lourde";
      else if (kind) { out.image = fs.readFileSync(p).toString("base64"); out.type = kind; }
      else {
        const fd = fs.openSync(p, "r"), buf = Buffer.alloc(Math.min(st.size, READ_LIMIT));
        let data;
        try { data = buf.subarray(0, fs.readSync(fd, buf, 0, buf.length, 0)); } finally { fs.closeSync(fd); }
        if (data.includes(0)) out.error = "fichier binaire";
        else {
          out.truncated = st.size > data.length;
          if (out.truncated && data.includes(10)) data = data.subarray(0, data.lastIndexOf(10) + 1);
          out.text = data.toString("utf8");
        }
      }
    }
  } catch (e) {
    out.error = e.code === "EACCES" || e.code === "EPERM" ? "lecture refusée" : "introuvable";
  }
}
process.stdout.write(JSON.stringify(out));
