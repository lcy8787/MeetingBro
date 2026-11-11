import { useEffect, useState } from "react";
import { useI18n } from "../i18n";

export function AudioLevelMeter({ level = 0, running = true }: { level?: number; running?: boolean }) {
  const { language } = useI18n();
  const display = running ? Math.max(0, Math.min(1, level)) : 0;
  return <span className="mb-level" role="meter" aria-label={language === "zh-Hant" ? "輸入音量" : "Input level"}
    aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(display * 100)}>
    {Array.from({ length: 18 }, (_, index) => <i key={index} className={display > index / 18 ? "is-lit" : ""} />)}
  </span>;
}

export function InputLevelPreview({ source, micDevice, speakerName, active, level, running }: {
  source: string; micDevice: string; speakerName: string; active: boolean; level: number; running: boolean;
}) {
  const { language } = useI18n();
  const [preview, setPreview] = useState(0);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (active) return;
    setPreview(0); setError(false);
    const url = new URL("/ws/audio-level", window.meetingbro?.backendWs ?? "ws://127.0.0.1:8765");
    url.search = new URLSearchParams({ source, mic_device: micDevice, speaker_name: speakerName }).toString();
    const ws = new WebSocket(url);
    ws.onmessage = event => {
      try {
        const value = JSON.parse(event.data);
        if (value.error) setError(true);
        else setPreview(Number(value.level) || 0);
      } catch { setError(true); }
    };
    ws.onerror = () => setError(true);
    ws.onclose = () => setPreview(0);
    return () => { ws.onmessage = null; ws.onerror = null; ws.onclose = null; ws.close(); };
  }, [active, source, micDevice, speakerName]);
  return <span className="mb-level-preview"><AudioLevelMeter level={active ? level : preview} running={active ? running : !error} />
    {error && !active && <small role="status">{language === "zh-Hant" ? "未能讀取音訊，請檢查裝置權限" : "Check the audio device and permission"}</small>}
  </span>;
}
