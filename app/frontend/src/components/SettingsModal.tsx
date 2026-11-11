import { t, useI18n } from "../i18n";
import { useEffect, useState } from "react";
import type { AppSettings } from "../meetingbro-bridge";
import { fetchConfig, testLlm, fetchOllamaModels } from "../system/backendApi";
import type { BackendConfig, TestLlmResult } from "../system/backendApi";

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  sessionActive: boolean;
}

const PROVIDER_PRESETS: Record<string, { baseUrl: string; model: string }> = {
  OpenAI: { baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  Ollama: { baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:3b" },
  LongCat: { baseUrl: "https://api.longcat.chat/openai", model: "LongCat-Flash-Chat" },
};

const WHISPER_SIZES = ["auto", "tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"];
// Large tiers are slow on CPU; surface a GPU-recommended hint when no CUDA is available.
const GPU_RECOMMENDED_SIZES = new Set(["large-v2", "large-v3", "large-v3-turbo"]);

export function SettingsModal({ open, onClose, sessionActive }: SettingsModalProps) {
  useI18n();
  const bridge = typeof window !== "undefined" ? window.meetingbro : undefined;
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [config, setConfig] = useState<BackendConfig | null>(null);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaStatus, setOllamaStatus] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestLlmResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setSaveNote(null);
    setTestResult(null);
    bridge?.getSettings?.().then(setSettings).catch(() => {});
    fetchConfig().then(setConfig).catch(() => setConfig(null));
  }, [open, bridge]);

  if (!open) return null;

  const hasBridge = Boolean(bridge?.saveSettings);

  const update = (patch: Partial<AppSettings>) => setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
  const updateLlm = (patch: Partial<AppSettings["llm"]>) =>
    setSettings((prev) => (prev ? { ...prev, llm: { ...prev.llm, ...patch } } : prev));

  const applyProvider = (name: string) => {
    const preset = PROVIDER_PRESETS[name];
    if (preset) updateLlm({ baseUrl: preset.baseUrl, model: preset.model });
  };

  const onTest = async () => {
    if (!settings) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testLlm({ api_key: settings.llm.apiKey, base_url: settings.llm.baseUrl, model: settings.llm.model });
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, error: (err as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const onSave = async () => {
    if (!settings || !bridge?.saveSettings) return;
    setSaving(true);
    setSaveNote(null);
    try {
      const res = await bridge.saveSettings(settings);
      if (res.requiresManualRestart) setSaveNote("Saved. This backend was started externally; restart that backend to apply changes.");
      else if (res.restarted) setSaveNote("Saved. Restarting the backend to apply changes…");
      else if (res.startupChanged && sessionActive) setSaveNote("Saved. Changes apply after the current session ends and the backend restarts.");
      else setSaveNote("Saved.");
    } catch (err) {
      setSaveNote(`Save failed: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const recommended = config?.hardware.recommended_whisper_size;
  const cudaAvailable = Boolean(config?.cuda_available);
  const nativeEngine = settings?.whisperEngine === "whisper-cpp" || (settings?.whisperEngine === "auto" && config?.metal_available);
  const qwenEngine = settings?.whisperEngine === "qwen3-mlx" || (settings?.whisperEngine === "auto" && config?.whisper_engine === "qwen3-mlx");
  const showGpuSizeHint = Boolean(settings && GPU_RECOMMENDED_SIZES.has(settings.whisperSize) && !cudaAvailable && !nativeEngine);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={t("Settings")} onClick={onClose}>
      <div className="modal modal--settings" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>{t("Settings")}</h2>
          <button type="button" className="modal__close" onClick={onClose} aria-label={t("Close")}>×</button>
        </div>

        {!hasBridge && (
          <p className="modal__notice">{t("In-app settings are available in the desktop app. In a browser, configure the backend via its .env file.")}</p>
        )}

        {settings && (
          <div className="modal__body">
            <section className="settings-section">
              <h3>{t("AI summaries (optional)")}</h3>
              <p className="settings-hint">{t("Choose an installed local Ollama model for private summaries. Other providers require their own settings.")}</p>
              <p className="settings-hint">{t("Offline extracts preserve the original language; Chinese text is converted to Traditional Chinese.")}</p>
              <label className="settings-field">
                <span><input type="checkbox" checked={settings.liveSummariesEnabled} onChange={(e) => update({ liveSummariesEnabled: e.target.checked })} /> {t("Generate summaries during recording")}</span>
              </label>
              <label className="settings-field">
                <span><input type="checkbox" checked={settings.finalSummaryEnabled} onChange={(e) => update({ finalSummaryEnabled: e.target.checked })} /> {t("Generate final summary after recording")}</span>
              </label>
              <p className="settings-hint">{t("Turn off live summaries to reserve resources for captions. Manual summary requests remain available.")}</p>
              <label className="settings-field">
                <span>{t("Inference resource mode")}</span>
                <select value={settings.llmResourceMode} onChange={(e) => update({ llmResourceMode: e.target.value as AppSettings["llmResourceMode"] })}>
                  <option value="realtime">{t("Realtime priority")}</option>
                  <option value="balanced">{t("Balanced")}</option>
                  <option value="post_recording">{t("Post-recording summaries")}</option>
                </select>
              </label>
              {settings.llmResourceMode === "post_recording" && <p className="settings-hint">{t("Automatic live summaries are paused in this mode; final summary remains separately configurable.")}</p>}
              <label className="settings-field">
                <span>{t("Provider preset")}</span>
                <select onChange={(e) => applyProvider(e.target.value)} defaultValue="">
                  <option value="" disabled>{t("Choose…")}</option>
                  {Object.keys(PROVIDER_PRESETS).map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
              <label className="settings-field">
                <span>{t("API key")}</span>
                <input type="password" value={settings.llm.apiKey} placeholder={config?.llm_key_masked ?? "sk-…"} onChange={(e) => updateLlm({ apiKey: e.target.value })} />
              </label>
              <label className="settings-field">
                <span>{t("Base URL")}</span>
                <input type="text" value={settings.llm.baseUrl} onChange={(e) => updateLlm({ baseUrl: e.target.value })} />
              </label>
              <label className="settings-field">
                <span>{t("Model")}</span>
                <input type="text" value={settings.llm.model} onChange={(e) => updateLlm({ model: e.target.value })} />
              </label>
              <div className="settings-actions-inline">
                <button type="button" className="settings-btn" onClick={async () => {
                  try {
                    const result = await fetchOllamaModels();
                    setOllamaModels(result.models);
                    setOllamaStatus(result.ok ? `Ollama: ${result.models.length} models` : `Ollama: ${result.error}`);
                  } catch (err) { setOllamaStatus(`Ollama: ${(err as Error).message}`); }
                }}>Refresh local Ollama models</button>
                {ollamaModels.length > 0 && <select aria-label="Ollama model" value={ollamaModels.includes(settings.llm.model) ? settings.llm.model : ""} onChange={(e) => updateLlm({baseUrl: "http://127.0.0.1:11434/v1", model: e.target.value})}>
                  <option value="" disabled>Select installed model</option>
                  {ollamaModels.map((model) => <option key={model} value={model}>{model}</option>)}
                </select>}
                {ollamaStatus && <span>{ollamaStatus}</span>}
              </div>
              <div className="settings-actions-inline">
                <button type="button" className="settings-btn" onClick={onTest} disabled={testing || (!settings.llm.apiKey && !/^http:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(settings.llm.baseUrl))}>
                  {testing ? t("Testing…") : t("Test connection")}
                </button>
                {testResult && (
                  <span className={`settings-test ${testResult.ok ? "is-ok" : "is-error"}`}>
                    {testResult.ok ? t("✓ Connected ({0} ms)", testResult.latency_ms) : `✗ ${testResult.error}`}
                  </span>
                )}
              </div>
            </section>

            <section className="settings-section">
              <h3>{t("Speech model")}</h3>
              <label className="settings-field">
                <span>{t("Recognition engine")}</span>
                <select value={settings.whisperEngine ?? "auto"} onChange={(e) => update({ whisperEngine: e.target.value as AppSettings["whisperEngine"], whisperDevice: "auto" })}>
                  <option value="auto">{t("Auto")}</option>
                  <option value="qwen3-mlx">Qwen3-ASR 0.6B (MLX)</option>
                  <option value="faster-whisper">{t("faster-whisper (CPU / CUDA)")}</option>
                  <option value="whisper-cpp">{t("whisper.cpp (Apple Metal / CPU)")}</option>
                </select>
              </label>
              <p className="settings-hint">{t("Active:")}{" "}{config?.whisper_engine ?? "faster-whisper"} · {config?.whisper_device ?? "loading"}{t(". Models must already be installed locally; changing these settings does not approve a download.")}</p>
              {qwenEngine && <p className="settings-hint">{t("Qwen3 MLX uses a local model and shares it between preview and final captions. Whisper settings apply only when a Whisper engine is selected.")}</p>}
              <label className="settings-field">
                <span>{t("Model size")}</span>
                <select disabled={qwenEngine} value={settings.whisperSize} onChange={(e) => update({ whisperSize: e.target.value })}>
                  {WHISPER_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size === "auto" && recommended ? t("Auto (recommended: {0})", recommended) : size}
                    </option>
                  ))}
                </select>
              </label>
              {showGpuSizeHint && (
                <p className="settings-hint">
                  {settings?.whisperSize === "large-v3-turbo"
                    ? t("large-v3-turbo is fastest among the large models but still GPU-recommended — on CPU it runs well below real time.")
                    : t("Large models are GPU-recommended — on CPU they run well below real time. Try small/medium, or large-v3-turbo if you need top accuracy.")}
                </p>
              )}
              <label className="settings-field">
                <span>{t("Compute device")}</span>
                <select disabled={qwenEngine} value={settings.whisperDevice} onChange={(e) => update({ whisperDevice: e.target.value })}>
                  <option value="auto">{t("Auto")}</option>
                  <option value="cpu">CPU</option>
                  {!nativeEngine && config?.cuda_available && <option value="cuda">{t("GPU (CUDA)")}</option>}
                  {nativeEngine && config?.metal_available && <option value="metal">{t("Apple GPU (Metal)")}</option>}
                </select>
              </label>
              <label className="settings-field">
                <span>{t("Live caption preview")}</span>
                <select disabled={qwenEngine} value={settings.previewBackend} onChange={(e) => update({ previewBackend: e.target.value as AppSettings["previewBackend"] })}>
                  <option value="qwen3">{t("Qwen3-ASR (separate local model)")}</option>
                  <option value="whisper-streaming">{t("Whisper streaming (reuse speech model)")}</option>
                  <option value="shared">{t("Off (formal transcript only)")}</option>
                </select>
              </label>
            </section>
          </div>
        )}

        <div className="modal__footer">
          {saveNote && <span className="settings-save-note">{t(saveNote)}</span>}
          {sessionActive && <span className="settings-hint">{t("Model/key changes apply after the current session ends.")}</span>}
          <button type="button" className="settings-btn" onClick={onClose}>{t("Close")}</button>
          <button type="button" className="settings-btn settings-btn--primary" onClick={onSave} disabled={!hasBridge || saving || !settings}>
            {saving ? t("Saving…") : t("Save & apply")}
          </button>
        </div>
      </div>
    </div>
  );
}
