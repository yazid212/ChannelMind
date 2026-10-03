import test from "node:test";
import assert from "node:assert/strict";

test("completed exports download once from the background even without a dashboard", async () => {
  const data = {
    config: { api: "https://api.example.com", app: "https://app.example.com" },
    "session:https://api.example.com": {
      workspace: { token: "test-capability", expires_at: new Date(Date.now() + 60000).toISOString() },
      runId: "test-run",
    },
  };
  let alarmListener;
  const downloads = [];
  const listener = { addListener() {} };
  globalThis.chrome = {
    action: { onClicked: listener }, tabs: {},
    runtime: { id: "a".repeat(32), onInstalled: listener, onStartup: listener, onMessage: listener },
    alarms: { create: async () => {}, onAlarm: { addListener(fn) { alarmListener = fn; } } },
    storage: { local: {
      get: async (key) => ({ [key]: data[key] }),
      set: async (values) => Object.assign(data, values),
    } },
    downloads: { download: async (options) => { downloads.push(options); return 7; } },
  };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({
    id: "test-run", status: "finished", total: 10, channel: { id: "UC" + "a".repeat(22) },
  }) });
  await import("../background.js");
  alarmListener({ name: "channelmind-export" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].url, "https://api.example.com/api/v1/exports/test-run/download?format=zip");
  assert.equal(downloads[0].headers[0].name, "Authorization");
  assert.equal(downloads[0].saveAs, false);
  alarmListener({ name: "channelmind-export" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(downloads.length, 1);
});

test("paused temporary export resumes when its retry time arrives", async () => {
  const data = {
    config: { api: "https://api.example.com" },
    "session:https://api.example.com": {
      workspace: { token: "test-capability", expires_at: new Date(Date.now() + 60000).toISOString() },
      runId: "partial-run",
    },
  };
  let alarmListener;
  const listener = { addListener() {} };
  globalThis.chrome = {
    action: { onClicked: listener }, tabs: {},
    runtime: { id: "a".repeat(32), onInstalled: listener, onStartup: listener, onMessage: listener },
    alarms: { create: async () => {}, onAlarm: { addListener(fn) { alarmListener = fn; } } },
    storage: { local: { get: async (key) => ({ [key]: data[key] }), set: async () => {} } },
    downloads: { download: async () => { throw new Error("A partial job must not download automatically"); } },
  };
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method || "GET" });
    return { ok: true, json: async () => ({
      id: "partial-run", status: "paused", error_code: "temporary", retry_after: Date.now() / 1000 - 1,
    }) };
  };
  await import("../background.js?retry-test");
  alarmListener({ name: "channelmind-export" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(requests.map((item) => item.method), ["GET", "POST"]);
  assert.match(requests[1].url, /\/exports\/partial-run\/resume$/);
});
