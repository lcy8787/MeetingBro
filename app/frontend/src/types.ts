export type LanguageCode = "zh" | "en" | "de";

export type SummaryType =
  | "meeting_memory"
  | "rolling_summary"
  | "cumulative_meeting_summary"
  | "time_window_summary"
  | "speaker_summary"
  | "final_summary"
  | "refined_transcript"
  | "chapter_list"
  | "action_item_list";

export interface TranscriptSegment {
  id: string;
  meeting_id: string;
  start_time: number;
  end_time: number;
  text: string;
  original_language: LanguageCode | "unknown";
  speaker_id: string | null;
  confidence: number;
  quality?: "ok" | "uncertain" | "low";
  translations: Partial<Record<LanguageCode, string>>;
  created_at: string;
  emitted_at_elapsed_seconds?: number | null;
  status?: "partial" | "final";
  revision?: number;
  sequence?: number | null;
}

export interface SummarySnapshot {
  id: string;
  meeting_id: string;
  summary_type: SummaryType;
  time_start: number;
  time_end: number;
  language: LanguageCode;
  content: string;
  generation_method?: "unknown" | "extractive" | "llm";
  source_segment_ids: string[];
  is_latest: boolean;
  translations: Partial<Record<LanguageCode, string>>;
  created_at: string;
}

export interface ReportBlock {
  type: "paragraph" | "bullet" | "quote" | "action" | "concept" | "step" | "timeline" | "comparison" | "argument" | "statistic" | "conclusion";
  title: string;
  text: string;
  detail: string;
  options?: { label: string; text: string; source_segment_ids: string[] }[];
  source_segment_ids: string[];
}

export interface ReportSection {
  id: string;
  title: string;
  layout_type: "text" | "concept" | "step_flow" | "timeline" | "comparison" | "argument" | "statistic" | "conclusion";
  blocks: ReportBlock[];
}

export interface ReportDocument {
  title: string;
  executive_summary: string;
  executive_summary_source_segment_ids: string[];
  takeaways: ReportBlock[];
  sections: ReportSection[];
  generation_method: "extractive" | "llm" | "mixed";
  warnings: string[];
}

export interface ReportRevision {
  report_id: string;
  meeting_id: string;
  report_type: string;
  revision: number;
  document: ReportDocument;
  created_at: string;
}

export interface FinalSummaryJob {
  id: string;
  meeting_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  language: LanguageCode;
  attempts: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Note {
  id: string;
  meeting_id: string;
  content: string;
  source_type: string | null;
  source_id: string | null;
  time_seconds?: number | null;
  created_at: string;
}

export interface ExportMeetingResponse {
  meeting_id: string;
  export_dir: string;
  files: string[];
}

export type SessionState = "starting" | "running" | "paused" | "ended";

export interface SessionStatePayload {
  state: SessionState;
  meeting_id: string;
  elapsed_seconds: number;
  source: string;
  runtime_profile: string;
  hardware_profile?: string | null;
  hardware_summary?: string | null;
  compute_cpu_active?: boolean;
  compute_cpu_available?: boolean;
  compute_cpu_configured?: boolean;
  compute_gpu_active?: boolean;
  compute_gpu_available?: boolean;
  compute_gpu_configured?: boolean;
  compute_activity_label?: string | null;
  audio_chunk_seconds: number | null;
  asr_accumulation_seconds: number | null;
  language_lock_enabled: boolean;
  live_translation_language: LanguageCode | null;
  retry_windows_total: number;
  retry_windows_improved: number;
  retry_windows_unchanged: number;
  retry_windows_diverged: number;
  last_backpressure_elapsed_seconds: number | null;
  asr_last_audio_seconds: number | null;
  asr_last_wall_seconds: number | null;
  asr_realtime_factor: number | null;
  asr_safeguard_active: boolean;
  asr_safeguard_reason: string | null;
  asr_safeguard_events: number;
  resource_pressure_level?: string;
  resource_governor_policy?: string;
  resource_governor_reason?: string | null;
  resource_governor_skips?: number;
  weak_rescue_attempts: number;
  weak_rescue_emitted: number;
  weak_rescue_buffer_seconds: number;
  summary_pending_count: number;
  inference_memory_free_percent?: number | null;
  inference_defer_reason?: string | null;
  translation_pending_count: number;
  translation_backlog_trim_total: number;
  audio_drop_total: number;
  audio_input_backlog_seconds: number;
  audio_input_queue_drop_total: number;
  audio_queue_wait_seconds?: number | null;
  event_queue_depth?: number;
  fast_preview_enabled: boolean;
  fast_preview_attempts: number;
  fast_preview_emitted: number;
  fast_preview_skipped: number;
  fast_preview_skip_reason?: string | null;
  fast_preview_last_audio_seconds: number | null;
  fast_preview_last_wall_seconds: number | null;
  fast_preview_realtime_factor: number | null;
  fast_preview_schedule_delay_seconds?: number | null;
  snapshot_concat_wall_seconds?: number | null;
  preview_continued_during_formal?: number;
  preview_stale_suppressed?: number;
  preview_alignment_compared?: number;
  preview_alignment_similarity_avg?: number | null;
  preview_alignment_similarity_last?: number | null;
  preview_unconfirmed_after_formal?: number;
  preview_unconfirmed_last_text?: string | null;
  qwen_targeted_retry_attempts?: number;
  qwen_targeted_retry_recovered?: number;
  qwen_targeted_retry_failed?: number;
  qwen_targeted_retry_skipped?: number;
  qwen_targeted_retry_last_reason?: string | null;
  mixed_microphone_gain: number | null;
  mixed_system_gain: number | null;
  mixed_effective_microphone_gain: number | null;
  mixed_auto_balance_enabled: boolean | null;
}

export type SessionEvent =
  | { type: "audio_level"; payload: { level: number; db: number } }
  | { type: "session_sync"; payload: { state: SessionStatePayload; segments: TranscriptSegment[]; snapshots: SummarySnapshot[]; notes: Note[]; report: ReportRevision | null; preview: TranscriptSegment | null } }
  | { type: "transcript_segment"; payload: TranscriptSegment }
  | { type: "transcript_segment_removed"; payload: { segment_id: string } }
  | { type: "transcript_translation"; payload: { segment_id: string; language: LanguageCode; text: string } }
  | { type: "transcript_preview"; payload: { segment: TranscriptSegment | null; preview_backend?: string; preview_is_experimental?: boolean; preview_quality_note?: string | null } }
  | { type: "summary_snapshot"; payload: SummarySnapshot }
  | { type: "report_revision"; payload: ReportRevision }
  | { type: "final_summary_job"; payload: FinalSummaryJob }
  | { type: "note_saved"; payload: Note }
  | { type: "session_state"; payload: SessionStatePayload }
  | { type: "error"; payload: { code: string; message: string } };
