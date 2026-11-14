// User-level settings store for the desktop app.
//
// Electron is the single owner of these settings. They live in a per-user JSON
// file (outside the repo, survives reinstalls) and are injected into the Python
// backend as MEETINGBRO_* environment variables when it is spawned. The backend
// already reads those env vars at startup and process-env wins over any .env,
// so no backend persistence is needed — this file is the single source of truth
// for GUI users. (Terminal users who run scripts/start.* keep using .env.)

const { app } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const SETTINGS_FILE = "meetingbro-settings.json";

function settingsPath() {
  return path.join(app.getPath("userData"), SETTINGS_FILE);
}

function defaultSettings() {
  return {
    firstRunComplete: false,
    backendPort: 8765,
    llm: { apiKey: "", baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:3b" },
    liveSummariesEnabled: false,
    finalSummaryEnabled: false,
    llmResourceMode: "balanced", // realtime | balanced | post_recording
    qwenModelSize: "0.6B",
    speechModel: "qwen3",

  };
}

// Shallow+one-level merge so missing keys fall back to defaults without losing
// nested fields the user did set.
function withDefaults(raw) {
  const d = defaultSettings();
  if (!raw || typeof raw !== "object") return d;
  const { whisperEngine, whisperSize, whisperDevice, previewBackend, ...current } = raw;
  return {
    ...d,
    ...current,
    speechModel: current.speechModel === "cantonese-hk-gguf" ? "cantonese-hk-gguf" : "qwen3",
    qwenModelSize: ["0.6B", "1.7B"].includes(current.qwenModelSize) ? current.qwenModelSize : "0.6B",
    llm: { ...d.llm, ...(raw.llm || {}) },
  };
}

function readSettings() {
  try {
    const text = fs.readFileSync(settingsPath(), "utf-8");
    return withDefaults(JSON.parse(text));
  } catch (err) {
    if (err && err.code !== "ENOENT") {
      console.warn("[settings] failed to read settings, using defaults:", err.message);
    }
    return defaultSettings();
  }
}

function writeSettings(partial) {
  const next = withDefaults({ ...readSettings(), ...(partial || {}) });
  if (partial && partial.llm) next.llm = { ...readSettings().llm, ...partial.llm };
  const dir = app.getPath("userData");
  fs.mkdirSync(dir, { recursive: true });
  const target = settingsPath();
  const tmp = `${target}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(next, null, 2), "utf-8");
  fs.renameSync(tmp, target); // atomic replace
  return next;
}

// Settings that only take effect at backend startup (changing them requires a
// backend restart). Changing anything else (audio source, languages, runtime
// profile, vocabulary) is applied live over the WebSocket and never restarts.
const STARTUP_KEYS = ["backendPort", "llm", "liveSummariesEnabled", "finalSummaryEnabled", "llmResourceMode", "qwenModelSize", "speechModel"];

function startupSignature(settings) {
  const s = withDefaults(settings);
  return JSON.stringify({
    backendPort: s.backendPort,
    llm: s.llm,
    liveSummariesEnabled: s.liveSummariesEnabled,
    finalSummaryEnabled: s.finalSummaryEnabled,
    llmResourceMode: s.llmResourceMode,
    qwenModelSize: s.qwenModelSize,
    speechModel: s.speechModel,
  });
}

// Map settings -> MEETINGBRO_* env vars passed to the spawned backend.
// Empty values are omitted so they don't clobber a value the user set in .env
// (relevant only for the terminal path; the GUI path always passes the JSON).
function settingsToEnv(settings) {
  const s = withDefaults(settings);
  const env = {};
  env.MEETINGBRO_PORT = String(s.backendPort || 8765);
  env.MEETINGBRO_LIVE_SUMMARIES_ENABLED = s.liveSummariesEnabled ? "true" : "false";
  env.MEETINGBRO_FINAL_SUMMARY_ENABLED = s.finalSummaryEnabled ? "true" : "false";
  env.MEETINGBRO_LLM_RESOURCE_MODE = ["realtime", "balanced", "post_recording"].includes(s.llmResourceMode) ? s.llmResourceMode : "balanced";

  if (s.llm && s.llm.apiKey) env.MEETINGBRO_LLM_API_KEY = s.llm.apiKey;
  if (s.llm && s.llm.baseUrl) env.MEETINGBRO_LLM_BASE_URL = s.llm.baseUrl;
  if (s.llm && s.llm.model) env.MEETINGBRO_LLM_MODEL = s.llm.model;

  env.MEETINGBRO_ASR_MODEL = s.speechModel;
  env.MEETINGBRO_QWEN3_MODEL_SIZE = s.qwenModelSize;
  return env;
}

module.exports = {
  settingsPath,
  defaultSettings,
  readSettings,
  writeSettings,
  settingsToEnv,
  startupSignature,
  STARTUP_KEYS,
};
