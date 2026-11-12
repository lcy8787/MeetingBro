const fs = require("node:fs/promises");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { pathToFileURL } = require("node:url");

// Paths are accepted only from the fixed backend export endpoint, never from a
// renderer's reveal/copy request. Capabilities belong to one window lifetime.
function registerExportIpc({ ipcMain, shell, clipboard, getWindow, getBackendBase, isDev }) {
  const exports = new Map();
  let generation = 0;
  const indexUrl = pathToFileURL(path.join(__dirname, "..", "dist", "index.html")).href;

  function assertSender(event) {
    const win = getWindow();
    if (!win || win.isDestroyed() || event.sender !== win.webContents ||
        !event.senderFrame || event.senderFrame !== win.webContents.mainFrame) {
      throw new Error("Untrusted export IPC sender");
    }
    let url;
    try { url = new URL(event.senderFrame.url); }
    catch { throw new Error("Untrusted export IPC origin"); }
    url.search = "";
    url.hash = "";
    const trusted = isDev
      ? url.origin === "http://localhost:5173" && url.pathname === "/"
      : url.href === indexUrl;
    if (!trusted) throw new Error("Untrusted export IPC origin");
  }

  async function snapshot(target, directory) {
    const stat = await fs.lstat(target);
    if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile())) {
      throw new Error("Invalid export target");
    }
    return { path: target, real: await fs.realpath(target), dev: stat.dev, ino: stat.ino, birth: stat.birthtimeMs, directory };
  }

  async function remember(result, event, startedGeneration) {
    if (typeof result.export_dir !== "string" || !path.isAbsolute(result.export_dir) || result.export_dir.includes("\0") ||
        !Array.isArray(result.files) || !result.files.length || result.files.length > 100) {
      throw new Error("Invalid export paths");
    }
    const folder = await snapshot(result.export_dir, true);
    const files = [];
    for (const name of result.files) {
      if (typeof name !== "string" || !name || name === "." || name === ".." || /[\\/\0:]/.test(name)) {
        throw new Error("Invalid export filename");
      }
      const file = await snapshot(path.join(result.export_dir, name), false);
      if (path.dirname(file.real) !== folder.real) throw new Error("Export escaped its folder");
      files.push(file);
    }
    const id = randomUUID();
    assertSender(event);
    if (startedGeneration !== generation) throw new Error("Export window changed");
    if (exports.size >= 256) exports.delete(exports.keys().next().value);
    exports.set(id, { owner: event.sender, folder, files, displayPath: result.export_dir });
    return id;
  }

  async function lookup(event, id) {
    assertSender(event);
    const record = typeof id === "string" && exports.get(id);
    if (!record || record.owner !== event.sender) throw new Error("Unknown export");
    for (const saved of [record.folder, ...record.files]) {
      const current = await snapshot(saved.path, saved.directory);
      if (current.real !== saved.real || current.dev !== saved.dev || current.ino !== saved.ino || current.birth !== saved.birth) {
        throw new Error("Export has moved or changed");
      }
    }
    assertSender(event);
    if (exports.get(id) !== record) throw new Error("Export window changed");
    return record;
  }

  ipcMain.handle("meetingbro:export-meeting", async (event, meetingId, input = {}) => {
    assertSender(event);
    const startedGeneration = generation;
    if (typeof meetingId !== "string" || !/^[a-zA-Z0-9_-]{1,200}$/.test(meetingId) ||
        !input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid export request");
    const stringKeys = ["source", "runtime_profile", "summary_language", "subtitle_language", "export_root", "export_dir", "target_language"];
    for (const [key, value] of Object.entries(input)) {
      if (value === undefined) continue;
      if (key === "bilingual" ? typeof value !== "boolean" : !stringKeys.includes(key) || typeof value !== "string" || value.length > 32768) {
        throw new Error("Invalid export option");
      }
    }
    if (input.target_language !== undefined && !["zh", "en", "de"].includes(input.target_language)) throw new Error("Invalid export language");
    const base = new URL(getBackendBase());
    if (base.protocol !== "http:" || base.hostname !== "127.0.0.1" || base.username || base.password) throw new Error("Invalid backend origin");
    const url = new URL(`/meetings/${encodeURIComponent(meetingId)}/export`, base);
    if (input.bilingual) url.searchParams.set("bilingual", "true");
    if (input.target_language) url.searchParams.set("target_language", input.target_language);
    const response = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input), redirect: "error",
    });
    if (!response.ok) return { status: response.status, result: null };
    const result = await response.json();
    // Failure of the optional desktop action must not turn a successful export
    // into a failed export (or cause a second export request).
    delete result.locationId;
    try {
      if (result.meeting_id !== meetingId) throw new Error("Unexpected export meeting");
      result.locationId = await remember(result, event, startedGeneration);
    } catch { /* Keep the backend's full path and successful response. */ }
    return { status: response.status, result };
  });

  ipcMain.handle("meetingbro:reveal-export", async (event, id) => {
    try {
      const record = await lookup(event, id);
      if (record.files.length === 1) {
        shell.showItemInFolder(record.files[0].path);
      } else {
        // Multiple exported files share this exact validated output directory.
        const error = await shell.openPath(record.folder.path);
        if (error) return { ok: false };
      }
      return { ok: true };
    } catch { return { ok: false }; }
  });

  ipcMain.handle("meetingbro:copy-export-path", async (event, id) => {
    try {
      const record = await lookup(event, id);
      clipboard.writeText(record.displayPath);
      return { ok: true };
    } catch { return { ok: false }; }
  });

  return { clear: () => { generation += 1; exports.clear(); } };
}

module.exports = { registerExportIpc };
