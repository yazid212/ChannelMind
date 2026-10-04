import { authCode, hostPermission, progressLabel, serverOrigin } from "./core.mjs";

const el = (id) => document.getElementById(id);
const initialHint = new URLSearchParams(location.hash.slice(1)).get("channel") || "";
const initialPlaylist = new URLSearchParams(location.hash.slice(1)).get("playlist") || "";
const defaults = { api: "http://127.0.0.1:8000", app: "http://localhost:3000" };
let config = defaults;
let workspace = null;
let run = null;
let connected = false;
let busy = false;
let polling = false;
let openWhenReady = false;
let loadedIssues = [];
let loadedIssueKey = "";

function showError(error) {
  el("error").textContent = error ? error.message || String(error) : "";
  el("error").hidden = !error;
}

function reasonLabel(reason) {
  return reason.replace(/([a-z])([A-Z])/g, "$1 $2").replaceAll("_", " ");
}

async function request(path, { method = "GET", body, binary = false } = {}) {
  const response = await fetch(config.api + "/api/v1" + path, {
    method,
    headers: { "Content-Type": "application/json", ...(workspace ? { Authorization: `Bearer ${workspace.token}` } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(binary ? 120000 : 45000),
    credentials: "omit", redirect: "error", cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return binary ? response.blob() : response.json();
}

function sessionKey() { return "session:" + config.api; }
async function saveSession() {
  await chrome.storage.local.set({ [sessionKey()]: { workspace, runId: run?.id || null } });
}

async function ensureWorkspace() {
  if (workspace && new Date(workspace.expires_at).getTime() > Date.now() + 60000) return;
  workspace = null;
  run = null;
  workspace = await request("/workspaces", { method: "POST" });
  await saveSession();
}

async function apiPermission() {
  const permission = { origins: [hostPermission(config.api)] };
  if (!await chrome.permissions.request(permission)) throw new Error("API access was not granted.");
}

function render() {
  const working = run && ["queued", "running", "indexing"].includes(run.status);
  el("start-public").disabled = busy || !el("public-channel").value.trim() || Boolean(run && !["finished", "cancelled"].includes(run.status));
  el("start-playlist").disabled = busy || !el("public-playlist").value.trim() || Boolean(run && !["finished", "cancelled"].includes(run.status));
  el("resume-upload").disabled = busy || !el("partial-file").files.length || Boolean(run && !["finished", "cancelled"].includes(run.status));
  el("start").disabled = busy || !connected || !el("channel").value || Boolean(run && !["finished", "cancelled"].includes(run.status));
  el("connect").disabled = busy;
  el("connect").textContent = connected ? "Reconnect YouTube" : "Connect YouTube";
  el("disconnect").hidden = !connected;
  el("disconnect").disabled = busy || working;
  el("channel").disabled = !connected || working;
  el("empty").hidden = Boolean(run);
  el("job").hidden = !run;
  if (!run) return;
  el("state").textContent = run.status;
  el("job-title").textContent = run.channel.title;
  el("message").textContent = run.message;
  el("counts").textContent = progressLabel(run);
  el("reason-counts").textContent = (run.coverage?.reasons || [])
    .map(({ reason, count }) => `${reasonLabel(reason)}: ${count}`).join(" · ");
  const pending = run.total - run.processed;
  el("partial").hidden = !pending || working;
  el("partial").textContent = pending
    ? `This export is incomplete: ${pending} videos have not been checked. Resume the export to continue from video ${run.processed + 1}.`
    : "";
  el("expiry").textContent = "Saved until " + new Date(run.expires_at).toLocaleString();
  el("progress").value = run.total ? 100 * run.processed / run.total : 0;
  el("resume").hidden = !["paused", "cancelled", "interrupted"].includes(run.status);
  el("resume").disabled = busy;
  el("cancel").hidden = !working;
  el("downloads").hidden = working || run.total === 0;
  el("open-app").hidden = working || run.exported === 0;
  el("open-app").disabled = busy;
  el("open-app").textContent = run.indexed ? "Open ChannelMind →" : "Use in ChannelMind →";
  const issueKey = `${run.id}:${run.processed}:${run.skipped}`;
  if (issueKey !== loadedIssueKey) { loadedIssues = []; loadedIssueKey = issueKey; }
  const visibleIssues = loadedIssues.length ? loadedIssues : (run.issues || []);
  el("issues").hidden = !run.issue_total;
  el("more-issues").hidden = !run.issue_total || visibleIssues.length >= run.issue_total;
  el("more-issues").disabled = busy;
  el("issue-list").replaceChildren(...visibleIssues.map((issue) => {
    const li = document.createElement("li");
    li.textContent = `${issue.title} — ${issue.outcome}: ${reasonLabel(issue.reason)}`;
    return li;
  }));
}

el("more-issues").addEventListener("click", () => action(async () => {
  const offset = loadedIssues.length;
  const page = await request(`/exports/${run.id}/issues?offset=${offset}&limit=50`);
  loadedIssues = [...loadedIssues, ...page.items];
}));

async function action(callback) {
  if (busy) return;
  busy = true;
  showError(null);
  render();
  try { await callback(); }
  catch (error) { showError(error); }
  finally { busy = false; render(); }
}

async function loadChannels() {
  const data = await request("/youtube/channels");
  connected = true;
  el("channel").replaceChildren(...data.channels.map((channel) => {
    const option = document.createElement("option");
    option.value = channel.id;
    option.textContent = channel.title;
    if ([channel.id.toLowerCase(), channel.handle.toLowerCase()].includes(initialHint.toLowerCase())) option.selected = true;
    return option;
  }));
  el("connection").textContent = data.channels.length
    ? "Connected. Select the channel you want to export."
    : "No owned channel was returned. Reconnect and choose the correct YouTube / Brand Account.";
  render();
}

el("connect").addEventListener("click", () => action(async () => {
  await apiPermission();
  await ensureWorkspace();
  const authorization = await request("/youtube/authorize", {
    method: "POST", body: { extension_id: chrome.runtime.id },
  });
  const returned = await chrome.identity.launchWebAuthFlow({ url: authorization.authorization_url, interactive: true });
  if (!returned) throw new Error("Google authorization was cancelled.");
  const code = authCode(returned, chrome.identity.getRedirectURL("oauth2"), authorization.state);
  await request("/youtube/complete", { method: "POST", body: { code, state: authorization.state } });
  await loadChannels();
}));

el("disconnect").addEventListener("click", () => action(async () => {
  await request("/youtube/connection", { method: "DELETE" });
  connected = false;
  el("connection").textContent = "YouTube disconnected. Existing exports remain available until they expire.";
  el("channel").replaceChildren();
}));

el("start").addEventListener("click", () => action(async () => {
  run = await request("/exports", { method: "POST", body: {
    channel_id: el("channel").value, language: el("language").value,
  } });
  await saveSession();
  void chrome.runtime.sendMessage({ type: "check-export" });
}));

el("public-channel").addEventListener("input", render);
el("start-public").addEventListener("click", () => action(async () => {
  await apiPermission();
  await ensureWorkspace();
  run = await request("/public-exports", { method: "POST", body: {
    channel: el("public-channel").value.trim(), language: el("language").value,
  } });
  await saveSession();
  void chrome.runtime.sendMessage({ type: "check-export" });
}));

el("public-playlist").addEventListener("input", render);
el("start-playlist").addEventListener("click", () => action(async () => {
  await apiPermission();
  await ensureWorkspace();
  run = await request("/public-playlist-exports", { method: "POST", body: {
    playlist: el("public-playlist").value.trim(), language: el("language").value,
  } });
  await saveSession();
  void chrome.runtime.sendMessage({ type: "check-export" });
}));

el("partial-file").addEventListener("change", render);
el("resume-upload").addEventListener("click", () => action(async () => {
  const file = el("partial-file").files[0];
  if (!file || file.size > 60 * 1024 * 1024) throw new Error("Select a partial ChannelMind export under 60 MB.");
  await apiPermission();
  await ensureWorkspace();
  const response = await fetch(config.api + "/api/v1/public-exports/resume-upload", {
    method: "POST", headers: { "Content-Type": "application/octet-stream", Authorization: `Bearer ${workspace.token}` },
    body: file, credentials: "omit", redirect: "error", signal: AbortSignal.timeout(120000),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `Upload failed (${response.status}).`);
  }
  run = await response.json();
  await saveSession();
  void chrome.runtime.sendMessage({ type: "check-export" });
}));

el("resume").addEventListener("click", () => action(async () => {
  run = await request(`/exports/${run.id}/resume`, { method: "POST" });
}));
el("cancel").addEventListener("click", () => action(async () => {
  await request(`/exports/${run.id}/cancel`, { method: "POST" });
  await poll();
}));

for (const button of document.querySelectorAll("[data-format]")) {
  button.addEventListener("click", () => action(async () => {
    const format = button.dataset.format;
    const blob = await request(`/exports/${run.id}/download?format=${format}`, { binary: true });
    const url = URL.createObjectURL(blob);
    try {
      await chrome.downloads.download({ url, filename: `ChannelMind-${run.channel.id}.${format}`, saveAs: true });
      // Give the download manager time to consume the Blob URL.
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) { URL.revokeObjectURL(url); throw error; }
  }));
}

async function openApp() {
  const { code } = await request(`/exports/${run.id}/handoff`, { method: "POST" });
  await chrome.tabs.create({ url: config.app + "/#channelmind=" + encodeURIComponent(code) });
  openWhenReady = false;
}
el("open-app").addEventListener("click", () => action(async () => {
  if (run.indexed) return openApp();
  run = await request(`/exports/${run.id}/index`, { method: "POST" });
  openWhenReady = true;
}));

el("save-settings").addEventListener("click", () => action(async () => {
  const next = { api: serverOrigin(el("api-origin").value), app: serverOrigin(el("app-origin").value) };
  const granted = await chrome.permissions.request({ origins: [hostPermission(next.api)] });
  if (!granted) throw new Error("API access was not granted.");
  config = next;
  await chrome.storage.local.set({ config });
  workspace = null; run = null; connected = false;
  await loadSession();
  el("connection").textContent = "Settings saved. Connect YouTube to export your channel.";
}));

async function poll() {
  if (!run || polling) return;
  polling = true;
  try {
    run = await request(`/exports/${run.id}`);
    render();
    if (run.status === "finished") void chrome.runtime.sendMessage({ type: "check-export" });
    if (run.indexed && openWhenReady) await openApp();
  } catch (error) {
    showError(error);
    if ([401, 404].includes(error.status) || !run.channel) {
      run = null;
      await saveSession();
      render();
    }
  } finally { polling = false; }
}

async function loadSession() {
  const saved = (await chrome.storage.local.get(sessionKey()))[sessionKey()];
  if (saved?.workspace && new Date(saved.workspace.expires_at).getTime() > Date.now()) {
    workspace = saved.workspace;
    if (saved.runId) { run = { id: saved.runId }; await poll(); }
    try { await loadChannels(); } catch (error) {
      connected = false;
      if (error.status !== 401) showError(error);
    }
  }
}

async function init() {
  // Content scripts (none are used) do not need access to the workspace token.
  await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
  config = (await chrome.storage.local.get("config")).config || defaults;
  config = { api: serverOrigin(config.api), app: serverOrigin(config.app) };
  el("api-origin").value = config.api;
  el("app-origin").value = config.app;
  el("public-channel").value = initialHint;
  el("public-playlist").value = initialPlaylist;
  el("redirect").textContent = chrome.identity.getRedirectURL("oauth2");
  el("extension-id").textContent = "Extension ID: " + chrome.runtime.id;
  await loadSession();
  render();
  setInterval(() => { if (run && ["queued", "running", "indexing"].includes(run.status)) void poll(); }, 3000);
}
init().catch(showError);
