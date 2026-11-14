import { t, useI18n } from "../i18n";
import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import "../recorderSettings.css";
import type { AppSettings } from "../meetingbro-bridge";
import { fetchConfig, testLlm, fetchOllamaModels } from "../system/backendApi";
import type { BackendConfig, TestLlmResult } from "../system/backendApi";

type SettingsTab = "recording" | "models" | "diagnostics";

interface SettingsModalProps {
  open: boolean;
  onClose: () => void;
  sessionActive: boolean;
  recordingControls?: ReactNode;
  diagnostics?: ReactNode;
  initialTab?: SettingsTab;
}

const PROVIDER_PRESETS: Record<string, { baseUrl: string; model: string }> = {
  OpenAI: { baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  Ollama: { baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:3b" },
  OpenRouter: { baseUrl: "https://openrouter.ai/api/v1", model: "openrouter/auto" },
  LongCat: { baseUrl: "https://api.longcat.chat/openai", model: "LongCat-Flash-Chat" },
};

export function SettingsModal({ open, ...props }: SettingsModalProps) {
  return open ? <SettingsDialog {...props} /> : null;
}

function SettingsDialog({ onClose, sessionActive, recordingControls, diagnostics, initialTab }: Omit<SettingsModalProps, "open">) {
  const { language } = useI18n();
  const chinese = language === "zh-Hant";
  const sections: { id: SettingsTab; label: string }[] = [];
  if (recordingControls) sections.push({ id: "recording", label: chinese ? "錄音" : "Recording" });
  sections.push({ id: "models", label: chinese ? "模型" : "Models" });
  if (diagnostics) sections.push({ id: "diagnostics", label: chinese ? "診斷" : "Diagnostics" });
  const [tab, setTab] = useState<SettingsTab>(() => sections.find(section => section.id === initialTab)?.id ?? sections[0].id);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const id = useId();
  const [loadError, setLoadError] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
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
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement;
    dialog.showModal();
    dialog.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')?.focus();
    return () => {
      dialog.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  useEffect(() => {
    let ignore = false;
    setLoadError("");
    bridge?.getSettings?.()
      .then(value => { if (!ignore) setSettings(value); })
      .catch((err: Error) => { if (!ignore) setLoadError(err.message); });
    fetchConfig().then(value => { if (!ignore) setConfig(value); }).catch(() => {});
    return () => { ignore = true; };
  }, [bridge, loadAttempt]);

  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    switch (event.key) {
      case "ArrowLeft": next = (index - 1 + sections.length) % sections.length; break;
      case "ArrowRight": next = (index + 1) % sections.length; break;
      case "Home": next = 0; break;
      case "End": next = sections.length - 1; break;
      default: return;
    }
    event.preventDefault();
    setTab(sections[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }

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


  return (
    <dialog
      ref={dialogRef}
      className="recorder-settings"
      aria-labelledby={`${id}-title`}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
      }}
    >
      <header className="recorder-settings__header">
        <h2 id={`${id}-title`}>{t("Settings")}</h2>
        <button type="button" className="recorder-settings__close" onClick={onClose} aria-label={t("Close")}>×</button>
      </header>
      <div className="recorder-settings__tabs" role="tablist" aria-label={t("Settings")}>
        {sections.map((section, index) => (
          <button
            key={section.id}
            type="button"
            role="tab"
            id={`${id}-${section.id}-tab`}
            aria-selected={section.id === tab}
            aria-controls={`${id}-${section.id}-panel`}
            tabIndex={section.id === tab ? 0 : -1}
            onClick={() => setTab(section.id)}
            onKeyDown={event => navigateTabs(event, index)}
          >
            {section.label}
          </button>
        ))}
      </div>
      <div key={tab} role="tabpanel" id={`${id}-${tab}-panel`} aria-labelledby={`${id}-${tab}-tab`} tabIndex={0} className="recorder-settings__panel">
        {tab === "recording" && recordingControls}
        {tab === "diagnostics" && diagnostics}
        {tab === "models" && !hasBridge && (
          <p className="settings-hint">{t("In-app settings are available in the desktop app. In a browser, configure the backend via its .env file.")}</p>
        )}
        {tab === "models" && hasBridge && !settings && (
          loadError ? <p role="alert">{loadError} <button type="button" className="settings-btn" onClick={() => setLoadAttempt(n => n + 1)}>{t("Retry")}</button></p> : <p role="status">{t("Loading…")}</p>
        )}
        {tab === "models" && settings && (
          <>
            <section className="settings-section">
              <h3>{t("AI summaries (optional)")}</h3>
              <p className="settings-hint">{chinese ? "選擇 OpenRouter，填入 API Key 及模型 ID，即可產生互動 HTML 總結。使用雲端供應商時，逐字稿會傳送至該供應商。" : "Choose OpenRouter and enter an API key and model ID for interactive HTML summaries. Cloud providers receive the transcript."}</p>
              <p className="settings-hint">{t("Choose an installed local Ollama model for private summaries. Other providers require their own settings.")}</p>
              <p className="settings-hint">{t("Use Generate summary or the automatic summary switch on the summary page. Without a successful model result, only the transcript is available.")}</p>
              <label className="settings-field">
                <span>{t("Inference resource mode")}</span>
                <select value={settings.llmResourceMode === "realtime" ? "realtime" : "balanced"} onChange={(e) => update({ llmResourceMode: e.target.value as AppSettings["llmResourceMode"] })}>
                  <option value="realtime">{t("Realtime priority")}</option>
                  <option value="balanced">{t("Balanced")}</option>
                </select>
              </label>
              <label className="settings-field">
                <span>{t("Provider preset")}</span>
                <select onChange={(e) => applyProvider(e.target.value)} value={Object.entries(PROVIDER_PRESETS).find(([, preset]) => preset.baseUrl === settings.llm.baseUrl)?.[0] ?? ""}>
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
                    setOllamaStatus(result.ok ? (chinese ? `已找到 ${result.models.length} 個模型` : `${result.models.length} models found`) : `Ollama: ${result.error}`);
                  } catch (err) { setOllamaStatus(`Ollama: ${(err as Error).message}`); }
                }}>{chinese ? "重新整理本機 Ollama 模型" : "Refresh local Ollama models"}</button>
                {ollamaModels.length > 0 && <select aria-label={chinese ? "Ollama 模型" : "Ollama model"} value={ollamaModels.includes(settings.llm.model) ? settings.llm.model : ""} onChange={(e) => updateLlm({baseUrl: "http://127.0.0.1:11434/v1", model: e.target.value})}>
                  <option value="" disabled>{chinese ? "選擇已安裝的模型" : "Select installed model"}</option>
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
                <span>{chinese ? "語音模型" : "Speech model"}</span>
                <select value={settings.speechModel} onChange={e => update({speechModel: e.target.value as AppSettings["speechModel"]})}>
                  <option value="qwen3">Qwen3-ASR</option>
                  <option value="cantonese-hk-gguf">香港粵語 1.7B · GGUF Q4_K_M</option>
                </select>
              </label>
              {settings.speechModel === "cantonese-hk-gguf" && <p className="settings-hint">自動辨識粵語／英語。先執行 scripts/install_qwen3_gguf.sh 或 Windows 的 .ps1 安裝腳本。</p>}
              <label className="settings-field">
                <span>Qwen3-ASR</span>
                <select disabled={settings.speechModel === "cantonese-hk-gguf"} value={settings.qwenModelSize} onChange={e => update({ qwenModelSize: e.target.value as AppSettings["qwenModelSize"] })}>
                  <option value="0.6B">0.6B · {chinese ? "輕量快速" : "Lightweight"}</option>
                  <option value="1.7B">1.7B · {chinese ? "高準確度" : "Higher accuracy"}</option>
                </select>
              </label>
              <p className="settings-hint">{chinese ? "即時字幕及正式逐字稿共用所選 Qwen3 模型。" : "Live captions and final transcripts share the selected Qwen3 model."}</p>
              <p className="settings-hint">{chinese ? "模型須先安裝於本機；儲存後重新啟動後端套用。" : "Install the model locally first; saving restarts the backend to apply it."}</p>
              <p className="settings-hint">{t("Active:")} {config?.asr_engine ?? "Qwen3"} · {config?.asr_size ?? "0.6B"}</p>
            </section>
          </>
        )}
      </div>
      {tab === "models" && hasBridge && (
        <footer className="recorder-settings__footer">
          <div role="status">
            {saveNote && <span className="settings-save-note">{t(saveNote)}</span>}
            {sessionActive && <span className="settings-hint">{t("Model/key changes apply after the current session ends.")}</span>}
          </div>
          <button type="button" className="settings-btn settings-btn--primary" onClick={onSave} disabled={saving || !settings}>
            {saving ? t("Saving…") : t("Save & apply")}
          </button>
        </footer>
      )}
    </dialog>
  );
}
