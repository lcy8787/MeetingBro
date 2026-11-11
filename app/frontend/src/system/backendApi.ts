// Thin fetch wrappers over the backend's read-only/validation endpoints.

import type { ExportMeetingResponse, FinalSummaryJob, Note, ReportRevision, SummarySnapshot, TranscriptSegment } from "../types";

function httpBase(): string {
  return window.meetingbro?.backendHttp ?? "http://127.0.0.1:8765";
}

export interface AudioDevice {
  id: string;
  name: string;
  default?: boolean;
}

export interface AudioDevices {
  mics: AudioDevice[];
  loopbacks: AudioDevice[];
  system_default: AudioDevice | null;
}

export interface BackendConfig {
  asr_engine: string;
  metal_available: boolean;
  asr_size: string;
  asr_device: string;
  asr_compute_type: string;
  preview_backend: string;
  port: number;
  cuda_available: boolean;
  llm_configured: boolean;
  llm_key_masked: string | null;
  llm_model: string | null;
  llm_base_url: string | null;
  hardware: {
    label: string;
    summary: string | null;
    recommended_asr_size: string;
    recommended_asr_device: string;
    recommended_runtime_profile: string;
  };
}

export interface TestLlmResult {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  error?: string;
}

export async function fetchAudioDevices(): Promise<AudioDevices> {
  const res = await fetch(`${httpBase()}/audio/devices`);
  if (!res.ok) throw new Error(`audio/devices ${res.status}`);
  return res.json();
}

export async function fetchConfig(): Promise<BackendConfig> {
  const res = await fetch(`${httpBase()}/config`);
  if (!res.ok) throw new Error(`config ${res.status}`);
  return res.json();
}

export interface MeetingListItem {
  id: string;
  started_at: string;
  ended_at: string | null;
  preferred_summary_language: string;
  segment_count: number;
  title: string | null;
}

export async function fetchMeetings(limit = 100): Promise<MeetingListItem[]> {
  const res = await fetch(`${httpBase()}/meetings?limit=${limit}`);
  if (!res.ok) throw new Error(`meetings ${res.status}`);
  return res.json();
}

export async function fetchMeetingTranscript(id: string): Promise<TranscriptSegment[]> {
  const res = await fetch(`${httpBase()}/meetings/${id}/transcript`);
  if (!res.ok) throw new Error(`transcript ${res.status}`);
  return res.json();
}

export async function fetchMeetingSummaries(id: string): Promise<SummarySnapshot[]> {
  const res = await fetch(`${httpBase()}/meetings/${id}/summaries`);
  if (!res.ok) throw new Error(`summaries ${res.status}`);
  return res.json();
}

export async function fetchMeetingReports(id: string): Promise<ReportRevision[]> {
  const res = await fetch(`${httpBase()}/meetings/${encodeURIComponent(id)}/reports`);
  if (!res.ok) throw new Error(`reports ${res.status}`);
  return res.json();
}

export async function fetchFinalSummaryJob(id: string): Promise<FinalSummaryJob | null> {
  const res = await fetch(`${httpBase()}/meetings/${encodeURIComponent(id)}/final-summary-job`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`final summary job ${res.status}`);
  return res.json();
}

export async function retryFinalSummaryJob(id: string): Promise<FinalSummaryJob> {
  const res = await fetch(`${httpBase()}/meetings/${encodeURIComponent(id)}/final-summary-job/retry`, { method: "POST" });
  if (!res.ok) throw new Error(`retry final summary ${res.status}`);
  return res.json();
}

export async function generateMeetingReport(id: string): Promise<ReportRevision> {
  const res = await fetch(`${httpBase()}/meetings/${encodeURIComponent(id)}/reports/generate`, { method: "POST" });
  if (!res.ok) {
    if (res.status === 503) throw new Error("Configure a summary model in Settings before generating a summary.");
    if (res.status === 502) {
      const body = await res.json().catch(() => null);
      if (body?.detail?.code === "output_truncated") throw new Error("The model reached its output limit before finishing. No summary was published; automatic summaries are off.");
      if (body?.detail?.code === "empty_response") throw new Error("The model returned no final answer. No summary was published; automatic summaries are off.");
      throw new Error("The model could not generate a valid summary. Check the model settings and try again.");
    }
    if (res.status === 409) throw new Error("A summary is already being generated for this meeting.");
    throw new Error(`report generation ${res.status}`);
  }
  return res.json();
}

export async function fetchMeetingNotes(id: string): Promise<Note[]> {
  const res = await fetch(`${httpBase()}/meetings/${id}/notes`);
  if (!res.ok) throw new Error(`notes ${res.status}`);
  return res.json();
}

export async function exportMeetingById(id: string): Promise<ExportMeetingResponse> {
  const res = await fetch(`${httpBase()}/meetings/${id}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`export ${res.status}`);
  return res.json();
}

export async function deleteMeeting(id: string): Promise<void> {
  const res = await fetch(`${httpBase()}/meetings/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`delete ${res.status}`);
}

export async function testLlm(input: { api_key: string; base_url?: string; model?: string }): Promise<TestLlmResult> {
  const res = await fetch(`${httpBase()}/config/test-llm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
  return res.json();
}

export async function fetchOllamaModels(): Promise<{ok: boolean; models: string[]; error?: string}> {
  const res = await fetch(`${httpBase()}/config/ollama-models`);
  if (!res.ok) throw new Error(`Ollama models HTTP ${res.status}`);
  return res.json();
}
