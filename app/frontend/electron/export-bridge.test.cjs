const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function loadApi(bridge, fetch) {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "system", "backendApi.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, window: { meetingbro: bridge }, fetch, URLSearchParams });
  return exports;
}

test("both frontend export entry points use the trusted IPC response without an extra HTTP export", async () => {
  const calls = [];
  const result = { meeting_id: "id", export_dir: "/actual/path", files: ["report.html"], locationId: "native-capability" };
  const api = loadApi({ exportMeeting: async (...args) => { calls.push(args); return { status: 200, result }; } }, () => assert.fail("unexpected browser fetch"));
  const input = { bilingual: true, target_language: "zh", export_dir: "/requested/path" };
  assert.equal((await api.requestMeetingExport("id", input)).result, result);
  assert.equal(await api.exportMeetingById("id"), result);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][1], input);
});

test("desktop IPC errors never trigger a duplicate browser export", async () => {
  const api = loadApi({ exportMeeting: async () => { throw new Error("backend unavailable"); } }, () => assert.fail("unsafe retry"));
  await assert.rejects(api.requestMeetingExport("id"), /backend unavailable/);
});

test("HTTP failure retains the existing history error and current-session status", async () => {
  const api = loadApi({ exportMeeting: async () => ({ status: 503, result: null }) }, () => assert.fail("unexpected fetch"));
  assert.equal((await api.requestMeetingExport("id")).status, 503);
  await assert.rejects(api.exportMeetingById("id"), /export 503/);
});

test("browser mode preserves the original POST, body, query and returned path", async () => {
  const calls = [];
  const result = { meeting_id: "id", export_dir: "/returned/path", files: ["transcript.md", "metadata.json"] };
  const api = loadApi(undefined, async (url, request) => {
    calls.push({ url, request });
    return { status: 200, ok: true, json: async () => result };
  });
  const input = { bilingual: true, target_language: "de", export_dir: "/requested/path" };
  assert.equal((await api.requestMeetingExport("id", input)).result, result);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/meetings/id/export?bilingual=true&target_language=de");
  assert.equal(calls[0].request.method, "POST");
  assert.equal(calls[0].request.body, JSON.stringify(input));
});

test("preload exposes only named export operations and forwards no raw filesystem API", async () => {
  const calls = [];
  let bridge;
  const source = fs.readFileSync(path.join(__dirname, "preload.cjs"), "utf8");
  vm.runInNewContext(source, { require: name => {
    assert.equal(name, "electron");
    return {
      contextBridge: { exposeInMainWorld: (key, value) => { assert.equal(key, "meetingbro"); bridge = value; } },
      ipcRenderer: {
        sendSync: () => ({ httpBase: "http://127.0.0.1:8765", wsBase: "ws://127.0.0.1:8765" }),
        invoke: async (...args) => { calls.push(args); return { ok: true }; },
      },
    };
  } });
  await bridge.exportMeeting("id", {});
  await bridge.revealExport("capability");
  await bridge.copyExportPath("capability");
  assert.equal(calls[0][0], "meetingbro:export-meeting");
  assert.equal(calls[1][0], "meetingbro:reveal-export");
  assert.equal(calls[2][0], "meetingbro:copy-export-path");
  for (const key of ["invoke", "send", "shell", "fs", "openPath", "showItemInFolder"]) assert.equal(bridge[key], undefined);
});
