// Shape of the `window.meetingbro` bridge exposed by electron/preload.cjs.
// Single source of truth so the session hook and UI components agree.
import type { ExportMeetingInput, ExportRequestResult } from "./types";

export type BackendState =
  | "idle"
  | "locating"
  | "starting"
  | "downloading-model"
  | "ready"
  | "failed"
  | "stopped";

export interface BackendInfo {
  state: BackendState;
  detail: string | null;
  httpBase: string;
  wsBase: string;
  logTail: string[];
}

export interface LlmSettings {
  apiKey: string;
  baseUrl: string;
  model: string;
}

export interface AppSettings {
  firstRunComplete: boolean;
  backendPort: number;
  llm: LlmSettings;
  liveSummariesEnabled: boolean;
  finalSummaryEnabled: boolean;
  llmResourceMode: "realtime" | "balanced" | "post_recording";
  qwenModelSize: "0.6B" | "1.7B";

}

export interface SaveSettingsResult {
  settings: AppSettings;
  startupChanged: boolean;
  restarted: boolean;
  requiresManualRestart?: boolean;
  backend: BackendInfo;
}

export interface MeetingBroBridge {
  backendHttp: string;
  backendWs: string;
  selectExportDirectory?: (suggestedName?: string) => Promise<string | null>;
  exportMeeting?: (meetingId: string, input: ExportMeetingInput) => Promise<ExportRequestResult>;
  revealExport?: (id: string) => Promise<{ ok: boolean }>;
  copyExportPath?: (id: string) => Promise<{ ok: boolean }>;

  getBackendInfo: () => Promise<BackendInfo>;
  onBackendStatus: (cb: (info: BackendInfo) => void) => () => void;
  retryBackend: () => Promise<BackendInfo>;
  openBackendLog: () => Promise<string>;
  setSessionActive: (active: boolean) => void;

  getSettings: () => Promise<AppSettings>;
  saveSettings: (partial: Partial<AppSettings>) => Promise<SaveSettingsResult>;
}

declare global {
  interface Window {
    meetingbro?: MeetingBroBridge;
  }
}
