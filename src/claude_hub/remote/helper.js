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

function liveIds() {
  const out = {}, d = path.join(C, "sessions");
  for (const f of ls(d).filter(f => f.endsWith(".json"))) {
    try {
      const s = JSON.parse(fs.readFileSync(path.join(d, f), "utf8"));
      process.kill(Number(s.pid), 0);
      out[s.sessionId] = s.status || "idle";
    } catch (e) {}
  }
  return out;
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

function msgTime(line) {  // date (epoch) d'une ligne de message, null si elle n'est pas lisible
  const ts = (parse(line) || {}).timestamp;
  const m = typeof ts === "string" && /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,3}))?/.exec(ts);
  if (!m) return null;
  return Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000 + (m[7] ? +m[7].padEnd(3, "0") / 1000 : 0);
}

// Commande locale de claude (/rename…) : pas un message, ni une activité.
const isCommand = line => line.includes("<local-command-caveat>") || line.includes('"content":"<command-name>/');

// Ligne qui change l'ordre de la liste : message envoyé, réponse finie ou question
// posée. Les étapes d'une réponse (outils, réflexion, résultats) n'en font pas partie.
function isStop(line) {
  if (line.includes('"isMeta":true') || line.includes('"isSidechain":true')) return false;
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
  for (const line of lines(p)) {
    if (isCommand(line)) continue;
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
      const t = textOf((r.message || {}).content);
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

// Messages et appels d'outils d'une transcription ; renvoie [items, dernier modèle, son effort].
// todos : liste complétée par les appels de TodoWrite, TaskCreate et TaskUpdate de toute
// la transcription, et par les numéros attribués aux tâches créées.
function transcriptItems(p, sidechain = false, todos = null) {
  const items = [], creating = new Set();
  let model = null, effort = null;
  for (const line of lines(p)) {
    if (!line.includes('"type":"user"') && !line.includes('"type":"assistant"')
        && !line.includes('"queued_command"')) continue;
    const r = parse(line);
    if (!r || (r.isSidechain && !sidechain)) continue;
    if (r.type === "attachment") {  // message envoyé pendant une réponse, lu entre deux étapes
      const a = r.attachment || {};
      if (a.type === "queued_command" && (a.commandMode === undefined ? "prompt" : a.commandMode) === "prompt") {
        const t = textOf(a.prompt), imgs = imagesOf(a.prompt);
        if (imgs.length || !isNoise(t)) items.push({role: "user", text: isNoise(t) ? "" : t, images: imgs, queued: true});
      }
      continue;
    }
    const c = (r.message || {}).content;
    if (r.isMeta) {  // message relayé par Remote Control : on garde son texte
      const body = (r.origin || {}).kind === "peer" ? peerBody(textOf(c)) : null;
      if (body) items.push(peerItem(body));
      continue;
    }
    if (r.type === "user") {
      const ev = creating.size && todos ? createdTask(r, creating) : null;
      if (ev) todos.push(ev);
      const t = textOf(c), imgs = imagesOf(c);
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
    }
    for (const b of Array.isArray(c) ? c : []) {
      if (b.type === "text" && (b.text || "").trim()) {
        items.push({role: "assistant", text: b.text});
        if (r.uuid && !sidechain) items[items.length - 1].uuid = r.uuid;  // point de copie « jusqu'ici »
      }
      else if (b.type === "tool_use") {
        items.push({role: "tool", name: b.name, input: b.input, id: b.id === undefined ? null : b.id});
        if (todos && TODO_TOOLS.includes(b.name)) {
          todos.push(items[items.length - 1]);
          if (b.name === "TaskCreate") creating.add(b.id === undefined ? null : b.id);
        }
      }
    }
  }
  return [items, model, effort];
}

function history(id, limit) {
  const p = find(id);
  const todos = [];
  const [items, model, effort] = p ? transcriptItems(p, false, todos) : [[], null, null];
  return {size: p ? fs.statSync(p).size : 0, model, effort, total: items.length, items: applyBudget(items.slice(-limit)), todos,
    agents: p ? agentsList(p) : []};
}

// Transcription du sous-agent lancé par l'appel d'outil toolId (subagents/agent-*.jsonl).
function agent(id, toolId, limit) {
  const p = find(id);
  const walk = d => {
    for (const n of ls(d).sort()) {
      const f = path.join(d, n);
      if (isDir(f)) { const r = walk(f); if (r) return r; continue; }
      if (!n.endsWith(".meta.json")) continue;
      let meta;
      try { meta = JSON.parse(fs.readFileSync(f, "utf8")); } catch (e) { continue; }
      const jsonl = path.join(d, n.slice(0, -".meta.json".length) + ".jsonl");
      if (!meta || typeof meta !== "object" || Array.isArray(meta) || meta.toolUseId !== toolId) continue;
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
  const a = {id: name.startsWith("agent-") ? name.slice("agent-".length) : name,
    type: meta.agentType ?? null, description: meta.description ?? null,
    tool: meta.toolUseId ?? null, parent: meta.parentAgentId ?? null,
    depth: meta.spawnDepth || 1, background: meta.requestShape === "background",
    size: fs.statSync(p).size, start: null, end: null, steps: 0, done: false, interrupted: false, last: null};
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
      a.done = false;
      a.interrupted = textOf(content).startsWith("[Request interrupted by user");
      continue;
    }
    a.interrupted = false;
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
      if ((!line.includes('"type":"user"') && !line.includes('"type":"assistant"')) || line.includes('"tool_result"')) continue;
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

const a = process.argv.slice(2);
let out;
if (a[0] === "list") {
  const live = liveIds();
  const sessions = allFiles().map(summary).filter(s => s.msgs && !s.next)
    .map(s => Object.assign(s, {live: live[s.id] || null, cwd_exists: s.cwd ? isDir(s.cwd) : null}))
    .sort((x, y) => y.mtime - x.mtime);
  out = {home: os.homedir(), sessions};
} else if (a[0] === "stat") {
  const p = find(a[1]);
  let bridge = null;
  if (p) for (const line of lines(p))
    if (line.includes('"bridge-session"')) { const r = parse(line); if (r && r.bridgeSessionId) bridge = r.bridgeSessionId; }
  out = {size: p ? fs.statSync(p).size : 0, live: liveIds()[a[1]] || null, bridge};
} else if (a[0] === "search") {
  out = search(a[1], Number(a[2]));
} else if (a[0] === "exists") {
  out = Object.fromEntries(JSON.parse(a[1]).map(d => [d, isDir(d)]));
} else if (a[0] === "live") {
  out = liveIds();
} else if (a[0] === "history") {
  out = history(a[1], Number(a[2]));
} else if (a[0] === "agent") {
  out = agent(a[1], a[2], Number(a[3]));
} else if (a[0] === "agents") {
  const p = find(a[1]);
  out = {live: liveIds()[a[1]] || null, agents: p ? agentsList(p) : []};
} else if (a[0] === "result") {
  out = result(a[1], a[2]);
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
