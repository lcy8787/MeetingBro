const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const vm = require("node:vm");

async function harness(t, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "meetingbro-export-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const folder = path.join(root, "匯出 空格 # %");
  await fs.mkdir(folder);
  const files = options.files || ["transcript.md", "metadata.json", "meeting-report.html"];
  for (const file of files) await fs.writeFile(path.join(folder, file), "exported");
  const handlers = new Map();
  const calls = [];
  const frame = { url: "http://localhost:5173/" };
  const sender = { mainFrame: frame };
  const event = { sender, senderFrame: frame };
  const win = { webContents: sender, isDestroyed: () => false };
  const state = {
    data: { meeting_id: "meeting-1", export_dir: folder, files },
    status: 200, folder, root, frame, event, win, calls, openError: "",
  };
  const source = await fs.readFile(path.join(__dirname, "export-ipc.cjs"), "utf8");
  const mod = { exports: {} };
  vm.runInNewContext(source, {
    module: mod, __dirname, URL,
    require: name => name === "node:path" && options.path ? options.path
      : name === "node:fs/promises" && options.fs ? options.fs : require(name),
    fetch: async (url, request) => {
      calls.push({ kind: "fetch", url: String(url), request });
      await state.beforeResponse?.();
      return { ok: state.status >= 200 && state.status < 300, status: state.status, json: async () => structuredClone(state.data) };
    },
  });
  const controls = mod.exports.registerExportIpc({
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    shell: {
      showItemInFolder: target => { calls.push({ kind: "reveal", target }); if (state.throwShell) throw new Error("failed"); },
      openPath: async target => { calls.push({ kind: "open", target }); return state.openError; },
    },
    clipboard: { writeText: text => calls.push({ kind: "copy", text }) },
    getWindow: () => win, getBackendBase: () => state.backend || "http://127.0.0.1:8765",
    isDev: options.isDev ?? true,
  });
  return Object.assign(state, {
    invoke: (name, ...args) => handlers.get(`meetingbro:${name}`)(event, ...args),
    export: async input => (await handlers.get("meetingbro:export-meeting")(event, "meeting-1", input)).result,
    clear: controls.clear,
  });
}

test("batch exports open the actual returned folder and copy its exact full path", async t => {
  const h = await harness(t);
  const result = await h.export({ bilingual: true, target_language: "zh", export_dir: "/a/different/requested/path" });
  assert.equal(result.export_dir, h.folder);
  assert.ok(result.locationId);
  assert.equal((await h.invoke("reveal-export", result.locationId)).ok, true);
  assert.equal((await h.invoke("copy-export-path", result.locationId)).ok, true);
  assert.equal(h.calls.find(c => c.kind === "open").target, h.folder);
  assert.equal(h.calls.find(c => c.kind === "copy").text, h.folder);
  const request = h.calls.find(c => c.kind === "fetch");
  assert.equal(request.url, "http://127.0.0.1:8765/meetings/meeting-1/export?bilingual=true&target_language=zh");
  assert.equal(JSON.parse(request.request.body).export_dir, "/a/different/requested/path");
  assert.equal(request.request.redirect, "error");
});

test("a single file uses native showItemInFolder", async t => {
  const h = await harness(t, { files: ["report # 繁體.md"] });
  const result = await h.export();
  assert.equal((await h.invoke("reveal-export", result.locationId)).ok, true);
  assert.equal(h.calls.find(c => c.kind === "reveal").target, path.join(h.folder, h.data.files[0]));
  assert.equal(h.calls.some(c => c.kind === "open"), false);
});

test("raw paths, forged IDs, objects and capabilities from a previous window are rejected", async t => {
  const h = await harness(t);
  const result = await h.export();
  for (const id of [h.folder, "invented", { locationId: result.locationId }, null]) {
    assert.equal((await h.invoke("reveal-export", id)).ok, false);
    assert.equal((await h.invoke("copy-export-path", id)).ok, false);
  }
  h.clear();
  assert.equal((await h.invoke("reveal-export", result.locationId)).ok, false);
  assert.equal(h.calls.length, 1);
});

test("iframe, foreign window, navigated page and lookalike origins cannot call privileged IPC", async t => {
  const h = await harness(t);
  const result = await h.export();
  const originalSender = h.event.sender;
  for (const url of ["https://example.com/", "http://localhost.evil:5173/", "invalid-url", "http://localhost:5173/other.html", "file:///tmp/untrusted.html"]) {
    h.frame.url = url;
    await assert.rejects(h.export(), /Untrusted/);
    assert.equal((await h.invoke("reveal-export", result.locationId)).ok, false);
  }
  h.frame.url = "http://localhost:5173/";
  h.event.senderFrame = { url: h.frame.url };
  await assert.rejects(h.export(), /Untrusted/);
  h.event.senderFrame = h.frame;
  h.event.sender = { ...originalSender };
  assert.equal((await h.invoke("copy-export-path", result.locationId)).ok, false);
  assert.equal(h.calls.length, 1);
});

test("production permits only the packaged index document", async t => {
  const h = await harness(t, { isDev: false });
  const { pathToFileURL } = require("node:url");
  h.frame.url = pathToFileURL(path.join(__dirname, "..", "dist", "index.html")).href + "#summary";
  assert.ok((await h.export()).locationId);
  h.frame.url = "file:///tmp/index.html";
  await assert.rejects(h.export(), /Untrusted/);
});

test("invalid backend paths never grant a location, but export remains successful without a retry", async t => {
  const h = await harness(t);
  const good = structuredClone(h.data);
  for (const patch of [
    { export_dir: "relative/folder" }, { export_dir: "file:///tmp/report" },
    { files: ["../outside.md"] }, { files: ["nested/file.md"] },
    { files: ["C:\\outside.md"] }, { files: ["stream:secret"] },
    { files: [] }, { files: ["missing.md"] }, { meeting_id: "other-meeting" },
  ]) {
    h.data = { ...good, ...patch, locationId: "forged-backend-id" };
    const result = await h.export();
    assert.equal(result.locationId, undefined);
    assert.equal(result.export_dir, h.data.export_dir);
  }
  assert.equal(h.calls.length, 9);
});

test("deleted and replaced files cannot be revealed or copied", async t => {
  const h = await harness(t);
  const result = await h.export();
  const file = path.join(h.folder, h.data.files[0]);
  await fs.rename(file, file + ".old");
  assert.equal((await h.invoke("reveal-export", result.locationId)).ok, false);
  await fs.writeFile(file, "replacement");
  assert.equal((await h.invoke("copy-export-path", result.locationId)).ok, false);
  assert.equal(h.calls.length, 1);
});

test("symlinks and directories masquerading as files are rejected", async t => {
  const h = await harness(t, { files: ["report.html"] });
  const file = path.join(h.folder, "report.html");
  await fs.unlink(file);
  await fs.symlink(path.join(h.root, "outside"), file);
  assert.equal((await h.export()).locationId, undefined);
  await fs.unlink(file);
  await fs.mkdir(file);
  assert.equal((await h.export()).locationId, undefined);
});

test("changing a parent symlink after export invalidates the capability", async t => {
  const h = await harness(t);
  const link = path.join(h.root, "parent");
  await fs.symlink(h.folder, link);
  h.data.export_dir = link;
  // A directly symlinked export folder is rejected before a capability is issued.
  assert.equal((await h.export()).locationId, undefined);
  h.data.export_dir = h.folder;
  const result = await h.export();
  await fs.rename(h.folder, h.folder + "-old");
  await fs.symlink(h.folder + "-old", h.folder);
  assert.equal((await h.invoke("reveal-export", result.locationId)).ok, false);
});

test("native failures are reported without changing export success", async t => {
  const h = await harness(t);
  const result = await h.export();
  h.openError = "No file manager available";
  assert.equal((await h.invoke("reveal-export", result.locationId)).ok, false);
  assert.equal((await h.invoke("copy-export-path", result.locationId)).ok, true);
  assert.equal(h.calls.filter(c => c.kind === "fetch").length, 1);
});

test("HTTP errors, invalid options and remote backend URLs grant no capabilities", async t => {
  const h = await harness(t);
  h.status = 500;
  const response = await h.invoke("export-meeting", "meeting-1", {});
  assert.equal(response.status, 500);
  assert.equal(response.result, null);
  await assert.rejects(h.invoke("export-meeting", "../settings", {}), /Invalid/);
  await assert.rejects(h.export({ unexpected: "option" }), /Invalid/);
  await assert.rejects(h.export({ bilingual: "true" }), /Invalid/);
  await assert.rejects(h.export({ target_language: "file://" }), /Invalid/);
  h.backend = "https://example.com";
  await assert.rejects(h.export(), /Invalid backend/);
  assert.equal(h.calls.length, 1);
});

test("navigation while an export is in flight never grants a capability to the new document", async t => {
  const h = await harness(t);
  h.beforeResponse = () => h.clear();
  const result = await h.export();
  assert.equal(result.locationId, undefined);
  assert.equal(result.export_dir, h.folder);
});

for (const folder of ["C:\\Users\\Tester\\匯出 空格", "\\\\server\\share\\會議 exports"]) {
  test(`Windows paths are handled natively without URL conversion: ${folder}`, async t => {
    const fakeFs = {
      lstat: async target => ({
        isSymbolicLink: () => false, isDirectory: () => target === folder,
        isFile: () => target !== folder, dev: 1, ino: target === folder ? 1 : 2, birthtimeMs: 123,
      }),
      realpath: async target => target,
    };
    const h = await harness(t, { path: path.win32, fs: fakeFs });
    h.data = { meeting_id: "meeting-1", export_dir: folder, files: ["report # 繁體.html"] };
    const result = await h.export();
    assert.ok(result.locationId);
    assert.equal((await h.invoke("reveal-export", result.locationId)).ok, true);
    assert.equal(h.calls.find(c => c.kind === "reveal").target, path.win32.join(folder, h.data.files[0]));
    assert.equal((await h.invoke("copy-export-path", result.locationId)).ok, true);
    assert.equal(h.calls.find(c => c.kind === "copy").text, folder);
  });
}
