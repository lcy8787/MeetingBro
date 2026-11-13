// Thin fetch wrappers over the backend's read-only/validation endpoints.

import type { ExportMeetingInput, ExportMeetingResponse, ExportRequestResult, FinalSummaryJob, Note, ReportRevision, SummarySnapshot, TranscriptSegment } from "../types";

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

const SUMMARY_ERRORS: Record<string, string> = {
  model_not_configured: "Configure a summary model in Settings before generating a summary.",
  model_authentication: "The summary model rejected the credentials. Check the model settings.",
  model_connection: "Could not connect to the summary model. Check the model service and try again.",
  model_rate_limited: "The summary model is rate limiting requests. Wait a moment, then retry.",
  provider_error: "The summary model service returned an error. Check the service and try again.",
  model_timeout: "The summary model request timed out. Please try again or check the model service.",
  ambiguous_response: "The model returned multiple JSON answers. Generate the summary again.",
  structured_output_unsupported: "The model service rejected the summary response format.",
  invalid_response: "The summary model service returned an unreadable response. Check the base URL and model ID.",
  output_truncated: "The model reached its output limit before finishing. No summary was published; automatic summaries are off.",
  empty_response: "The model returned no final answer. No summary was published; automatic summaries are off.",
  incomplete_response: "The model did not finish its answer. No summary was published; please generate again.",
  refusal: "The model declined to generate a summary. No summary was published.",
  report_call_budget: "The summary stopped at its safe model-call limit. No partial report was published.",
  invalid_report_speaker: "The model used an unidentified speaker. No summary was published; please generate again.",
  invalid_report_json: "The model returned invalid JSON. No summary was published; please generate again.",
  invalid_report_structure: "The model returned an invalid report structure. No summary was published; please generate again.",
  invalid_report_component_data: "The model placed data in an incompatible component. No summary was published; retry to generate again.",
  invalid_report_sources: "The model cited missing or unknown transcript sources. No summary was published; retry to generate again.",
  unsupported_report_claim: "The model included a quantity absent from its cited transcript. No summary was published; retry to generate again.",
  invalid_report_evidence: "The model's evidence does not match the cited transcript. No summary was published; retry to generate again.",
  summary_in_progress: "A summary is already being generated for this meeting.",
  no_transcript: "This meeting has no saved transcript to summarize.",
};

export class SummaryGenerationError extends Error {
  readonly needsSettings: boolean;
  readonly location: string | null;
  constructor(readonly code: string = "invalid_report", location?: unknown) {
    super(Object.hasOwn(SUMMARY_ERRORS, code) ? SUMMARY_ERRORS[code] : "The model could not generate a valid summary. Retry to generate again; automatic summaries are off.");
    this.name = "SummaryGenerationError";
    this.needsSettings = ["model_not_configured", "model_authentication", "model_connection", "model_timeout", "provider_error", "invalid_response"].includes(code);
    // Only display schema paths; never treat an arbitrary provider/model string as a location.
    const pathNames = new Set("parts sections blocks data nodes edges cells columns rows options source_segment_ids text title detail speaker_id evidence_quote executive_summary executive_summary_source_segment_ids id layout_type layout type label value unit series source target relation row column op block_index span emphasis children field".split(" "));
    this.location = typeof location === "string" && location.length <= 240
      && location.split(".").every(part => pathNames.has(part) || /^\d+$/.test(part))
      ? location : null;
  }
}

type Translate = (source: string, ...values: Array<string | number | undefined>) => string;
export function summaryFailureMessage(error: SummaryGenerationError, translate: Translate): string {
  const message = translate(error.message);
  return error.location ? `${message} ${translate("Location: {0}", error.location)}` : message;
}

export async function generateMeetingReport(id: string): Promise<ReportRevision> {
  let res: Response;
  try {
    res = await fetch(`${httpBase()}/meetings/${encodeURIComponent(id)}/reports/generate`, { method: "POST" });
  } catch {
    throw new SummaryGenerationError("model_connection");
  }
  if (!res.ok) {
    if (res.status === 503) throw new SummaryGenerationError("model_not_configured");
    if (res.status === 502) {
      const body = await res.json().catch(() => null);
      throw new SummaryGenerationError(typeof body?.detail?.code === "string" ? body.detail.code : undefined, body?.detail?.location);
    }
    if (res.status === 409) throw new SummaryGenerationError("summary_in_progress");
    if (res.status === 422) throw new SummaryGenerationError("no_transcript");
    throw new SummaryGenerationError();
  }
  return res.json();
}

export async function fetchMeetingNotes(id: string): Promise<Note[]> {
  const res = await fetch(`${httpBase()}/meetings/${id}/notes`);
  if (!res.ok) throw new Error(`notes ${res.status}`);
  return res.json();
}

export async function exportMeetingById(id: string): Promise<ExportMeetingResponse> {
  const { status, result } = await requestMeetingExport(id);
  if (!result) throw new Error(`export ${status}`);
  return result;
}

export async function requestMeetingExport(id: string, input: ExportMeetingInput = {}): Promise<ExportRequestResult> {
  if (window.meetingbro?.exportMeeting) return window.meetingbro.exportMeeting(id, input);
  const params = new URLSearchParams();
  if (input.bilingual) params.set("bilingual", "true");
  if (input.target_language) params.set("target_language", input.target_language);
  const res = await fetch(`${httpBase()}/meetings/${encodeURIComponent(id)}/export${params.size ? `?${params}` : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return { status: res.status, result: res.ok ? await res.json() : null };
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
