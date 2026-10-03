import { channelHint, playlistHint, serverOrigin } from "./core.mjs";

chrome.action.onClicked.addListener(async (tab) => {
  const hint = channelHint(tab.url || "");
  const playlist = playlistHint(tab.url || "");
  await chrome.tabs.create({
    url: chrome.runtime.getURL("export.html") +
      (playlist ? "#playlist=" + encodeURIComponent(playlist) : hint ? "#channel=" + encodeURIComponent(hint) : ""),
  });
});

let checking = false;
async function checkExport() {
  if (checking) return;
  checking = true;
  try {
    const settings = await chrome.storage.local.get("config");
    const api = serverOrigin(settings.config?.api || "http://127.0.0.1:8000");
    const key = "session:" + api;
    const session = (await chrome.storage.local.get(key))[key];
    if (!session?.runId || !session.workspace || Date.parse(session.workspace.expires_at) <= Date.now()) return;
    const downloadKey = "download:" + session.runId;
    if ((await chrome.storage.local.get(downloadKey))[downloadKey]) return;
    const headers = { Authorization: `Bearer ${session.workspace.token}` };
    const response = await fetch(`${api}/api/v1/exports/${session.runId}`, {
      headers, credentials: "omit", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return;
    const run = await response.json();
    if (run.status === "interrupted" ||
        (run.status === "paused" && run.error_code === "temporary" &&
         run.retry_after && Date.now() >= run.retry_after * 1000)) {
      await fetch(`${api}/api/v1/exports/${run.id}/resume`, {
        method: "POST", headers, credentials: "omit", redirect: "error",
        signal: AbortSignal.timeout(15000),
      });
      return;
    }
    if (run.status !== "finished" || !run.total) return;
    const downloadId = await chrome.downloads.download({
      url: `${api}/api/v1/exports/${run.id}/download?format=zip`,
      headers: [{ name: "Authorization", value: headers.Authorization }],
      filename: `ChannelMind-${run.channel.id}.zip`, saveAs: false, conflictAction: "uniquify",
    });
    await chrome.storage.local.set({ [downloadKey]: downloadId });
  } catch { /* Offline servers are retried by the next alarm; errors appear in the dashboard. */ }
  finally { checking = false; }
}

async function ensureAlarm() {
  await chrome.alarms.create("channelmind-export", { periodInMinutes: 1 });
}
chrome.runtime.onInstalled.addListener(ensureAlarm);
chrome.runtime.onStartup.addListener(ensureAlarm);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "channelmind-export") void checkExport();
});
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "check-export") return;
  ensureAlarm().then(checkExport).then(() => sendResponse({ ok: true }));
  return true;
});
