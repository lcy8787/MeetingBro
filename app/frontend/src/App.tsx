import { t, useI18n } from "./i18n";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { ExportMeetingResponse, TranscriptSegment } from "./types";
import { useSessionSocket } from "./session/useSessionSocket";
import type { ExportMeetingInput } from "./session/useSessionSocket";
import { useBackendStatus } from "./system/useBackendStatus";
import { BackendGate } from "./components/BackendGate";
import { SettingsModal } from "./components/SettingsModal";
import { InputLevelPreview } from "./components/AudioLevelMeter";
import { RecorderView } from "./components/RecorderView";
import "./meetingWorkspace.css";
import { fetchAudioDevices } from "./system/backendApi";
import type { AudioDevices } from "./system/backendApi";

const VOCABULARY_STORAGE_KEY = "meetingbro.vocabulary";
const AUDIO_DEVICE_KEY = "meetingbro.audioDevice";
const LOOPBACK_DEVICE_KEY = "meetingbro.loopbackDevice";
const PREVIEW_HANDOFF_TOLERANCE_SECONDS = 0.35;
const FORMAL_SEGMENT_STAGGER_MS = 75;
const FORMAL_CELL_STAGGER_MS = 55;
const PREVIEW_ROW_STAGGER_MS = 45;
const PREVIEW_CELL_STAGGER_MS = 45;

type StaggerStyle = CSSProperties & {
  "--segment-delay"?: string;
  "--segment-text-delay"?: string;
  "--preview-status-delay"?: string;
  "--preview-text-delay"?: string;
};

function hasPreviewHandoff(segment: TranscriptSegment, previews: readonly TranscriptSegment[]): boolean {
  return previews.some((preview) => (
    preview.end_time <= segment.end_time + PREVIEW_HANDOFF_TOLERANCE_SECONDS &&
    preview.end_time >= segment.start_time - PREVIEW_HANDOFF_TOLERANCE_SECONDS
  ));
}


function formatRange(start: number, end: number): string {
  const fmt = (s: number) => {
    const m = Math.floor(s / 60).toString().padStart(2, "0");
    const sec = Math.floor(s % 60).toString().padStart(2, "0");
    return `${m}:${sec}`;
  };
  return `${fmt(start)} – ${fmt(end)}`;
}

function formatCreatedAt(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatLagSeconds(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  return `${Math.round(seconds)}s`;
}

function getDelayTone(seconds: number | null): "ok" | "warn" | "danger" | "unknown" {
  if (seconds == null) return "unknown";
  if (seconds < 1.2) return "ok";
  if (seconds < 2.8) return "warn";
  return "danger";
}

type AdviceSeverity = "ok" | "warn" | "danger";

interface SystemAdvice {
  severity: AdviceSeverity;
  title: string;
  detail: string;
  action?: string;
}

function getConfidenceTone(confidence: number): "stable" | "soft" | "uncertain" {
  if (confidence < 0.48) return "uncertain";
  if (confidence < 0.67) return "soft";
  return "stable";
}

function getConfidenceLabel(confidence: number): string | null {
  const tone = getConfidenceTone(confidence);
  if (tone === "uncertain") return "uncertain";
  if (tone === "soft") return t("low conf");
  return null;
}

function formatElapsedSeconds(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60).toString().padStart(2, "0");
  const s = Math.floor(total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function formatGain(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${value.toFixed(2)}x`;
}

function formatRtf(value: number | null | undefined): string {
  if (value == null) return "???";
  return `${value.toFixed(2)}x`;
}

function getGainTone(
  effectiveMicrophoneGain: number | null | undefined,
  baseMicrophoneGain: number | null | undefined,
  activeSource: string | null | undefined,
): "ok" | "warn" | "danger" | "unknown" {
  if (activeSource !== "mixed") return "unknown";
  if (effectiveMicrophoneGain == null || baseMicrophoneGain == null || baseMicrophoneGain <= 0) {
    return "unknown";
  }
  const boostRatio = effectiveMicrophoneGain / baseMicrophoneGain;
  if (boostRatio < 1.15) return "ok";
  if (boostRatio < 1.45) return "warn";
  return "danger";
}

function formatSourceLabel(source: string | null | undefined): string {
  switch (source) {
    case "mic":
      return t("Microphone");
    case "loopback":
    case "system":
      return t("System Audio");
    case "mixed":
      return t("System + Mic");
    case "sample":
      return t("Sample audio (demo)");
    default:
      return source ?? "—";
  }
}

function formatLanguageLabel(language: "zh" | "en" | "de" | null | undefined): string {
  switch (language) {
    case "zh":
      return t("Chinese");
    case "en":
      return t("English");
    case "de":
      return t("German");
    default:
      return t("Off");
  }
}

function formatRuntimeProfileLabel(profile: string | null | undefined): string {
  switch (profile) {
    case "summary_only":
      return t("Summary Only");
    case "performance":
    case "robust":
      return t("Performance");
    case "balanced":
      return t("Balanced");
    default:
      return profile ?? t("Balanced");
  }
}

function defaultExportFolderName(): string {
  const now = new Date();
  const pad = (value: number, width = 2) => value.toString().padStart(width, "0");
  const timestamp = [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
  ].join("-") + "_" + [
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("-") + `-${pad(now.getMilliseconds(), 3)}`;
  return `${timestamp}_meetingbro`;
}

function getMixedGainHint(
  tone: "ok" | "warn" | "danger" | "unknown",
  activeSource: string | null | undefined,
): string {
  if (activeSource !== "mixed") {
    return t("mixed diagnostics appear when source is System + Mic");
  }
  if (tone === "danger") {
    return "mic seems weak · consider raising MEETINGBRO_MIXED_MIC_GAIN";
  }
  if (tone === "warn") {
    return t("moderate mic compensation active · monitor voice clarity");
  }
  if (tone === "ok") {
    return t("mic and system levels look balanced");
  }
  return t("waiting for mixed-level diagnostics");
}

function isNearBottom(element: HTMLDivElement): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight < 120;
}

export default function App() {
  const { language: uiLanguage, setLanguage: setUiLanguage } = useI18n();
  const [source, setSource] = useState("mixed");
  const [speechLanguage, setSpeechLanguage] = useState("auto");
  const [summaryLanguage, setSummaryLanguage] = useState("zh");
  const [subtitleLanguage, setSubtitleLanguage] = useState("off");
  const [runtimeProfile, setRuntimeProfile] = useState("balanced");
  const [vocabulary, setVocabulary] = useState("");
  const [sessionEnabled, setSessionEnabled] = useState(false);
  const [lastExport, setLastExport] = useState<ExportMeetingResponse | null>(null);
  const [exportRoot, setExportRoot] = useState<string>("");
  const [exporting, setExporting] = useState(false);
  const [exportIntent, setExportIntent] = useState<"manual" | "restart" | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [bookmarkFeedback, setBookmarkFeedback] = useState<string | null>(null);
  const [bilingualExport, setBilingualExport] = useState(false);
  const [restartPromptOpen, setRestartPromptOpen] = useState(false);
  const [diagnosticsMode, setDiagnosticsMode] = useState<"simple" | "advanced">(
    () => (window.localStorage.getItem("meetingbro.diagnosticsMode") === "advanced" ? "advanced" : "simple"),
  );
  const [computeActivityHold, setComputeActivityHold] = useState({ cpu: false, gpu: false });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"recording" | "models" | "diagnostics">("recording");
  const [bookmarkLabel, setBookmarkLabel] = useState("");
  const [audioDevice, setAudioDevice] = useState("");
  const [loopbackDevice, setLoopbackDevice] = useState("");
  const [audioDevices, setAudioDevices] = useState<AudioDevices | null>(null);
  const [audioDeviceError, setAudioDeviceError] = useState(false);
  const refreshAudioDevices = useCallback(async () => {
    setAudioDeviceError(false);
    try { setAudioDevices(await fetchAudioDevices()); }
    catch { setAudioDeviceError(true); }
  }, []);
  const { connected, state, meetingId, sessionStartedAt, elapsedSeconds, sessionStats, audioLevel, segments, previewSegment, previewSegments, isExperimentalPreview, previewDisplayText, previewIsStreaming, departingPreviewSegments, latestByType, liveReport, finalJob, notes, lastError, lastErrorCode, saveBookmark, applyVocabulary, exportMeeting, pauseSession, resumeSession, stopSession } =
    useSessionSocket({ enabled: sessionEnabled, source, speechLanguage, summaryLanguage, subtitleLanguage, runtimeProfile, audioDevice, loopbackDevice });

  const backend = useBackendStatus();

  useEffect(() => {
    if (state === "ended") setSessionEnabled(false);
  }, [state]);

  // Load persisted device choices once, and fetch the device list when the
  // backend becomes ready.
  useEffect(() => {
    setAudioDevice(window.localStorage.getItem(AUDIO_DEVICE_KEY) ?? "");
    setLoopbackDevice(window.localStorage.getItem(LOOPBACK_DEVICE_KEY) ?? "");
  }, []);
  useEffect(() => {
    if (!backend.isReady) return;
    void refreshAudioDevices();
  }, [backend.isReady, refreshAudioDevices]);
  // First run: open Settings once so a new user lands on configuration instead
  // of guessing. Marking firstRunComplete touches no startup keys, so it won't
  // restart the backend.
  useEffect(() => {
    const bridge = window.meetingbro;
    if (!bridge?.getSettings || !backend.isReady) return;
    bridge.getSettings().then((s) => {
      if (!s.firstRunComplete) {
        setSettingsTab("models");
        setSettingsOpen(true);
        bridge.saveSettings?.({ firstRunComplete: true }).catch(() => {});
      }
    }).catch(() => {});
  }, [backend.isReady]);
  useEffect(() => {
    window.localStorage.setItem(AUDIO_DEVICE_KEY, audioDevice);
  }, [audioDevice]);
  useEffect(() => {
    window.localStorage.setItem(LOOPBACK_DEVICE_KEY, loopbackDevice);
  }, [loopbackDevice]);
  useEffect(() => {
    window.localStorage.setItem("meetingbro.diagnosticsMode", diagnosticsMode);
  }, [diagnosticsMode]);

  // Tell the Electron supervisor when a session is live so it won't restart the
  // backend (e.g. after a settings change) mid-recording.
  useEffect(() => {
    window.meetingbro?.setSessionActive?.(sessionEnabled);
  }, [sessionEnabled]);

  const rolling = latestByType.rolling_summary;
  const cumulative = latestByType.cumulative_meeting_summary;
  const hasRetainedMeetingData = !sessionEnabled && !!meetingId && (
    segments.length > 0 ||
    notes.length > 0 ||
    Object.keys(latestByType).length > 0
  );

  useEffect(() => {
    setVocabulary(window.localStorage.getItem(VOCABULARY_STORAGE_KEY) ?? "");
  }, []);

  useEffect(() => {
    if (!actionFeedback) {
      return;
    }
    const timer = window.setTimeout(() => setActionFeedback(null), 3200);
    return () => window.clearTimeout(timer);
  }, [actionFeedback]);

  useEffect(() => {
    if (!bookmarkFeedback) {
      return;
    }
    const timer = window.setTimeout(() => setBookmarkFeedback(null), 2200);
    return () => window.clearTimeout(timer);
  }, [bookmarkFeedback]);

  const visibleSegments = useMemo(() => segments.slice(-200), [segments, uiLanguage]);
  const visibleBookmarks = useMemo(() => {
    if (visibleSegments.length === 0) {
      return [];
    }
    const firstVisibleStart = visibleSegments[0].start_time;
    const lastVisibleEnd = visibleSegments[visibleSegments.length - 1].end_time;
    const latestTimelineSeconds = Math.max(lastVisibleEnd, elapsedSeconds || 0);
    return notes
      .filter((note) => note.source_type === "bookmark" && note.time_seconds != null)
      .filter((note) => (note.time_seconds ?? 0) >= firstVisibleStart && (note.time_seconds ?? 0) <= latestTimelineSeconds)
      .sort((left, right) => (left.time_seconds ?? 0) - (right.time_seconds ?? 0));
  }, [elapsedSeconds, notes, visibleSegments, uiLanguage]);
  const transcriptTimelineItems = useMemo(() => {
    const items: Array<
      | { type: "segment"; segment: typeof visibleSegments[number] }
      | { type: "bookmark"; note: typeof notes[number] }
    > = [];
    let bookmarkIndex = 0;

    for (const segment of visibleSegments) {
      while (
        bookmarkIndex < visibleBookmarks.length &&
        (visibleBookmarks[bookmarkIndex].time_seconds ?? 0) <= segment.end_time
      ) {
        items.push({ type: "bookmark", note: visibleBookmarks[bookmarkIndex] });
        bookmarkIndex += 1;
      }
      items.push({ type: "segment", segment });
    }

    while (bookmarkIndex < visibleBookmarks.length) {
      items.push({ type: "bookmark", note: visibleBookmarks[bookmarkIndex] });
      bookmarkIndex += 1;
    }

    return items;
  }, [notes, visibleBookmarks, visibleSegments, uiLanguage]);
  const latestSegment = segments.at(-1);
  const [newSegmentOrders, setNewSegmentOrders] = useState<Record<string, number>>({});
  const segmentAnimationKnownIdsRef = useRef<Set<string>>(new Set());
  const latestVisualSegment = previewSegment ?? latestSegment;
  const handoffPreviewSegments = useMemo(
    () => [...departingPreviewSegments, ...previewSegments],
    [departingPreviewSegments, previewSegments, uiLanguage],
  );
  const shouldShowPreviewStack = state === "running" || departingPreviewSegments.length > 0 || previewSegments.length > 0;
  const previewStackRevision = useMemo(
    () => handoffPreviewSegments.map((segment) => `${segment.id}:${segment.text}`).join("|"),
    [handoffPreviewSegments, uiLanguage],
  );
  const latestSegmentRevision = latestSegment ? `${latestSegment.id}:${latestSegment.text}` : "";
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const knownIds = segmentAnimationKnownIdsRef.current;

    if (segments.length === 0) {
      knownIds.clear();
      setNewSegmentOrders({});
      return;
    }

    const newlyAdded = segments.filter((segment) => !knownIds.has(segment.id));
    segments.forEach((segment) => knownIds.add(segment.id));

    if (newlyAdded.length === 0) {
      return;
    }

    const addedOrders = Object.fromEntries(
      newlyAdded.map((segment, index) => [segment.id, index]),
    ) as Record<string, number>;
    const addedIds = new Set(newlyAdded.map((segment) => segment.id));
    setNewSegmentOrders((prev) => ({ ...prev, ...addedOrders }));
    const timer = window.setTimeout(() => {
      setNewSegmentOrders((prev) => {
        const next = { ...prev };
        addedIds.forEach((id) => {
          delete next[id];
        });
        return next;
      });
    }, 1100);
    return () => window.clearTimeout(timer);
  }, [segments]);

  useEffect(() => {
    if (!sessionEnabled) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [sessionEnabled]);

  const audioClockSeconds = useMemo(() => {
    if (elapsedSeconds > 0) {
      return elapsedSeconds;
    }
    if (!sessionStartedAt) {
      return null;
    }
    const sessionStartMs = new Date(sessionStartedAt).getTime();
    if (Number.isNaN(sessionStartMs)) {
      return null;
    }
    return Math.max(0, (nowMs - sessionStartMs) / 1000);
  }, [elapsedSeconds, nowMs, sessionStartedAt, uiLanguage]);

  const committedLagSeconds = useMemo(() => {
    if (!latestSegment || state !== "running" || audioClockSeconds == null) {
      return null;
    }
    const committedAt = latestSegment.emitted_at_elapsed_seconds;
    if (committedAt != null) {
      return Math.max(0, committedAt - latestSegment.end_time);
    }
    return Math.max(0, audioClockSeconds - latestSegment.end_time);
  }, [audioClockSeconds, latestSegment, state, uiLanguage]);

  const liveGapSeconds = useMemo(() => {
    if (!latestVisualSegment || state !== "running" || audioClockSeconds == null) {
      return null;
    }
    return Math.max(0, audioClockSeconds - latestVisualSegment.end_time);
  }, [audioClockSeconds, latestVisualSegment, state, uiLanguage]);

  const transcriptLagSeconds = liveGapSeconds ?? committedLagSeconds;

  const visibleTranscriptSeconds = latestSegment?.end_time ?? null;
  const backpressureAgeSeconds = useMemo(() => {
    const lastBackpressure = sessionStats?.last_backpressure_elapsed_seconds;
    if (lastBackpressure == null || elapsedSeconds <= 0) {
      return null;
    }
    return Math.max(0, elapsedSeconds - lastBackpressure);
  }, [elapsedSeconds, sessionStats?.last_backpressure_elapsed_seconds, uiLanguage]);
  const retryTotal = sessionStats?.retry_windows_total ?? 0;
  const retryImproved = sessionStats?.retry_windows_improved ?? 0;
  const retryUnchanged = sessionStats?.retry_windows_unchanged ?? 0;
  const retryDiverged = sessionStats?.retry_windows_diverged ?? 0;
  const asrRealtimeFactor = sessionStats?.asr_realtime_factor ?? null;
  const asrWallSeconds = sessionStats?.asr_last_wall_seconds ?? null;
  const asrAudioSeconds = sessionStats?.asr_last_audio_seconds ?? null;
  const asrSafeguardActive = sessionStats?.asr_safeguard_active ?? false;
  const asrSafeguardEvents = sessionStats?.asr_safeguard_events ?? 0;
  const resourcePressureLevel = sessionStats?.resource_pressure_level ?? "normal";
  const resourceGovernorPolicy = sessionStats?.resource_governor_policy ?? "balanced";
  const resourceGovernorReason = sessionStats?.resource_governor_reason ?? null;
  const resourceGovernorSkips = sessionStats?.resource_governor_skips ?? 0;
  const activeRuntimeProfile = sessionEnabled ? sessionStats?.runtime_profile ?? runtimeProfile : runtimeProfile;
  const summaryOnlyMode = activeRuntimeProfile === "summary_only";
  const activeHardwareProfile = sessionStats?.hardware_profile ?? "auto";
  const activeHardwareSummary = sessionStats?.hardware_summary ?? t("detecting local CPU/GPU profile");
  const computeCpuActive = sessionStats?.compute_cpu_active ?? false;
  const computeCpuAvailable = sessionStats?.compute_cpu_available ?? true;
  const computeCpuConfigured = sessionStats?.compute_cpu_configured ?? false;
  const computeGpuActive = sessionStats?.compute_gpu_active ?? false;
  const computeGpuAvailable = sessionStats?.compute_gpu_available ?? false;
  const computeGpuConfigured = sessionStats?.compute_gpu_configured ?? false;
  const computeActivityLabel = sessionStats?.compute_activity_label ?? "idle";
  const cpuChipTitle = computeCpuActive
    ? computeActivityLabel
    : computeCpuConfigured
      ? t("CPU is configured for MeetingBro and waiting for Qwen/CPU work")
      : computeCpuAvailable
        ? t("CPU is available, but no CPU ASR task is currently assigned")
        : t("CPU is not available");
  const gpuChipTitle = computeGpuActive
    ? computeActivityLabel
    : computeGpuConfigured
      ? t("GPU is configured for MeetingBro and waiting for Qwen3 work")
      : computeGpuAvailable
        ? t("GPU is available, but MeetingBro is currently configured to use CPU")
        : t("GPU is not available to the current Python ASR runtime");
  const displayCpuActive = computeCpuActive || computeActivityHold.cpu;
  const displayGpuActive = computeGpuActive || computeActivityHold.gpu;
  const activeChunkSeconds = sessionStats?.audio_chunk_seconds ?? null;
  const activeAccumSeconds = sessionStats?.asr_accumulation_seconds ?? null;
  const languageLockEnabled = sessionStats?.language_lock_enabled ?? false;
  const weakRescueAttempts = sessionStats?.weak_rescue_attempts ?? 0;
  const weakRescueEmitted = sessionStats?.weak_rescue_emitted ?? 0;
  const weakRescueBufferSeconds = sessionStats?.weak_rescue_buffer_seconds ?? 0;
  const translationPendingCount = sessionStats?.translation_pending_count ?? 0;
  const summaryPendingCount = sessionStats?.summary_pending_count ?? 0;
  const translationTrimTotal = sessionStats?.translation_backlog_trim_total ?? 0;
  const audioDropTotal = sessionStats?.audio_drop_total ?? 0;
  const audioInputBacklogSeconds = sessionStats?.audio_input_backlog_seconds ?? 0;
  const audioInputQueueDropTotal = sessionStats?.audio_input_queue_drop_total ?? 0;
  const audioQueueWaitSeconds = sessionStats?.audio_queue_wait_seconds ?? null;
  const eventQueueDepth = sessionStats?.event_queue_depth ?? 0;
  const fastPreviewEnabled = sessionStats?.fast_preview_enabled ?? false;
  const fastPreviewAttempts = sessionStats?.fast_preview_attempts ?? 0;
  const fastPreviewEmitted = sessionStats?.fast_preview_emitted ?? 0;
  const fastPreviewSkipped = sessionStats?.fast_preview_skipped ?? 0;
  const fastPreviewRtf = sessionStats?.fast_preview_realtime_factor ?? null;
  const fastPreviewScheduleDelay = sessionStats?.fast_preview_schedule_delay_seconds ?? null;
  const snapshotConcatWall = sessionStats?.snapshot_concat_wall_seconds ?? null;
  const previewContinuedDuringFormal = sessionStats?.preview_continued_during_formal ?? 0;
  const previewStaleSuppressed = sessionStats?.preview_stale_suppressed ?? 0;
  const previewAlignmentCompared = sessionStats?.preview_alignment_compared ?? 0;
  const previewAlignmentSimilarityAvg = sessionStats?.preview_alignment_similarity_avg ?? null;
  const previewAlignmentSimilarityLast = sessionStats?.preview_alignment_similarity_last ?? null;
  const previewUnconfirmedAfterFormal = sessionStats?.preview_unconfirmed_after_formal ?? 0;
  const previewUnconfirmedLastText = sessionStats?.preview_unconfirmed_last_text ?? null;
  const qwenTargetedRetryAttempts = sessionStats?.qwen_targeted_retry_attempts ?? 0;
  const qwenTargetedRetryRecovered = sessionStats?.qwen_targeted_retry_recovered ?? 0;
  const qwenTargetedRetryFailed = sessionStats?.qwen_targeted_retry_failed ?? 0;
  const qwenTargetedRetrySkipped = sessionStats?.qwen_targeted_retry_skipped ?? 0;
  const qwenTargetedRetryLastReason = sessionStats?.qwen_targeted_retry_last_reason ?? null;
  const delayTone = getDelayTone(transcriptLagSeconds);
  const activeSource = sessionStats?.source ?? source;
  const mixedMicGain = sessionStats?.mixed_microphone_gain ?? null;
  const mixedSystemGain = sessionStats?.mixed_system_gain ?? null;
  const mixedEffectiveMicGain = sessionStats?.mixed_effective_microphone_gain ?? null;
  const mixedAutoBalanceEnabled = sessionStats?.mixed_auto_balance_enabled ?? null;
  const mixedGainTone = getGainTone(mixedEffectiveMicGain, mixedMicGain, activeSource);
  const mixedGainRatio =
    activeSource === "mixed"
      ? t("mic {0} vs system {1}", formatGain(mixedEffectiveMicGain), formatGain(mixedSystemGain))
      : "—";
  const mixedGainHint = getMixedGainHint(mixedGainTone, activeSource);
  const activeSubtitleLanguage: "zh" | "en" | "de" | null = summaryOnlyMode || subtitleLanguage === "off"
    ? null
    : ((sessionStats?.live_translation_language ?? subtitleLanguage) as "zh" | "en" | "de");
  const visibleSubtitleRevision = useMemo(() => {
    if (activeSubtitleLanguage == null) {
      return 0;
    }
    return visibleSegments.reduce((revision, segment, index) => {
      const text = segment.translations[activeSubtitleLanguage]?.trim();
      if (!text) {
        return revision;
      }
      return revision + text.length + index + 1;
    }, 0);
  }, [activeSubtitleLanguage, visibleSegments, uiLanguage]);
  const recentSpeechLanguages = useMemo(() => {
    const languages = new Set<string>();
    for (const segment of visibleSegments.slice(-24)) {
      if (segment.original_language !== "unknown") {
        languages.add(segment.original_language);
      }
    }
    return languages;
  }, [visibleSegments, uiLanguage]);
  const latestSubtitleText = activeSubtitleLanguage && latestSegment
    ? latestSegment.translations[activeSubtitleLanguage]
    : null;
  const subtitleStatus = activeSubtitleLanguage == null
    ? "off"
    : !latestSegment
      ? "waiting"
      : latestSegment.original_language === activeSubtitleLanguage
        ? t("same language")
        : latestSubtitleText && latestSubtitleText.trim()
          ? "translated"
          : "translating";
  const subtitleStatusDetail = activeSubtitleLanguage == null
    ? t("subtitles disabled")
    : !latestSegment
      ? t("target {0} · waiting for transcript", formatLanguageLabel(activeSubtitleLanguage))
      : latestSegment.original_language === activeSubtitleLanguage
        ? t("latest segment already in {0}", formatLanguageLabel(activeSubtitleLanguage))
        : latestSubtitleText && latestSubtitleText.trim()
          ? t("latest subtitle ready in {0}", formatLanguageLabel(activeSubtitleLanguage))
          : t("target {0} · translation pending", formatLanguageLabel(activeSubtitleLanguage));

  const systemAdvice = useMemo<SystemAdvice[]>(() => {
    if (!sessionEnabled || state !== "running") {
      return [{
        severity: "ok",
        title: t("Ready to monitor"),
        detail: t("Start a session to receive live stability, accuracy, and latency advice."),
      }];
    }

    const advice: SystemAdvice[] = [];
    const realtimeGap = transcriptLagSeconds ?? 0;
    const rtf = asrRealtimeFactor ?? 0;
    const asrBusy = lastError?.includes("ASR has been processing") ?? false;

    if (asrBusy) {
      advice.push({
        severity: "warn",
        title: t("ASR is currently busy"),
        detail: lastError ?? t("The backend is still processing the current audio window."),
        action: t("Use Summary Only mode, switch to a smaller Qwen3 model, or turn subtitles off if lag continues."),
      });
    } else if (asrSafeguardActive || rtf >= 1.0 || backpressureAgeSeconds != null) {
      advice.push({
        severity: asrSafeguardActive || rtf >= 1.2 ? "danger" : "warn",
        title: t("ASR is close to falling behind"),
        detail: asrSafeguardActive
          ? (sessionStats?.asr_safeguard_reason ?? t("The backend activated realtime protection."))
          : t("Last ASR realtime factor is {0}.", formatRtf(asrRealtimeFactor)),
        action: t("Use Summary Only mode, switch to a smaller Qwen3 model, or turn subtitles off if lag continues."),
      });
    } else if (realtimeGap >= 4.0 && activeRuntimeProfile !== "summary_only") {
      advice.push({
        severity: "warn",
        title: t("Transcript is lagging the audio"),
        detail: t("Latest visible transcript is {0} behind the audio clock.", formatLagSeconds(realtimeGap)),
        action: t("Try Summary Only mode if you can trade live captions for lower device load."),
      });
    }

    if (weakRescueAttempts >= 2 || weakRescueBufferSeconds >= 2.0) {
      advice.push({
        severity: weakRescueEmitted > 0 ? "warn" : "ok",
        title: t("Quiet speech detected"),
        detail: t("Weak voice rescue attempted {0} time(s), emitted {1}.", weakRescueAttempts, weakRescueEmitted),
        action: t("Increase speaker volume, move closer to the microphone, or use Performance mode."),
      });
    }

    if (mixedGainTone === "warn" || mixedGainTone === "danger") {
      advice.push({
        severity: mixedGainTone,
        title: t("Mixed audio balance may be uneven"),
        detail: mixedGainHint,
        action: t("Adjust microphone/system volume or keep Mixed mode auto-balance enabled."),
      });
    }

    if (translationPendingCount >= 4 || translationTrimTotal > 0) {
      advice.push({
        severity: translationTrimTotal > 0 ? "warn" : "ok",
        title: t("Subtitle translation is busy"),
        detail: t("Pending translations {0}, trims {1}.", translationPendingCount, translationTrimTotal),
        action: t("If the machine is struggling, turn subtitles off or use Summary Only mode."),
      });
    }

    if (audioDropTotal > 0) {
      advice.push({
        severity: audioDropTotal >= 5 ? "danger" : "warn",
        title: t("Audio chunks were dropped"),
        detail: t("{0} capture chunk(s) have been dropped.", audioDropTotal),
        action: t("Close other audio-heavy apps or switch source mode if drops continue."),
      });
    }

    if (audioInputBacklogSeconds >= 2.0 || audioInputQueueDropTotal > 0) {
      advice.push({
        severity: audioInputBacklogSeconds >= 5.0 || audioInputQueueDropTotal > 0 ? "warn" : "ok",
        title: t("Audio input is buffered"),
        detail: t("Backlog {0}, queue drops {1}.", formatLagSeconds(audioInputBacklogSeconds), audioInputQueueDropTotal),
        action: t("Use Summary Only mode or a smaller Qwen3 model until backlog stays near 0s."),
      });
    }

    if (fastPreviewEnabled && fastPreviewAttempts > 0 && fastPreviewEmitted === 0) {
      advice.push({
        severity: "warn",
        title: t("Fast preview has not emitted text yet"),
        detail: t("Preview attempts {0}, skipped {1}.", fastPreviewAttempts, fastPreviewSkipped),
        action: t("This can happen on silence or if ASR is busy; if speech is present, try a smaller preview model later."),
      });
    }

    if (speechLanguage !== "auto" && recentSpeechLanguages.size >= 2) {
      advice.push({
        severity: "warn",
        title: t("Multiple speech languages detected"),
        detail: t("Recent transcript contains {0}.", Array.from(recentSpeechLanguages).join(", ")),
        action: t("Set Speech to Auto if the meeting is switching languages."),
      });
    }

    if (advice.length === 0) {
      advice.push({
        severity: "ok",
        title: t("Realtime path looks healthy"),
        detail: t("No stability, latency, or accuracy warnings are active."),
        action: activeRuntimeProfile === "balanced"
          ? t("Keep Balanced mode unless you need lower load or stronger accuracy bias.")
          : t("Current mode: {0}.", formatRuntimeProfileLabel(activeRuntimeProfile)),
      });
    }

    return advice.slice(0, 4);
  }, [
    activeRuntimeProfile,
    asrRealtimeFactor,
    asrSafeguardActive,
    audioDropTotal,
    audioInputBacklogSeconds,
    audioInputQueueDropTotal,
    backpressureAgeSeconds,
    fastPreviewAttempts,
    fastPreviewEmitted,
    fastPreviewEnabled,
    fastPreviewSkipped,
    languageLockEnabled,
    lastError,
    mixedGainHint,
    mixedGainTone,
    recentSpeechLanguages,
    sessionEnabled,
    sessionStats?.asr_safeguard_reason,
    speechLanguage,
    state,
    transcriptLagSeconds,
    translationPendingCount,
    translationTrimTotal,
    weakRescueAttempts,
    weakRescueBufferSeconds,
    weakRescueEmitted,
    uiLanguage,
  ]);
  const systemAdviceTone = systemAdvice.some((item) => item.severity === "danger")
    ? "danger"
    : systemAdvice.some((item) => item.severity === "warn")
      ? "warn"
      : "ok";

  const transcriptBodyRef = useRef<HTMLDivElement | null>(null);
  const transcriptBottomRef = useRef<HTMLDivElement>(null);
  const transcriptAutoFollowRef = useRef(true);
  const transcriptProgrammaticScrollRef = useRef(false);
  const transcriptFollowFrameRef = useRef<number | null>(null);
  const [transcriptAutoFollow, setTranscriptAutoFollow] = useState(true);

  const scrollTranscriptToBottom = (behavior: ScrollBehavior = "auto") => {
    if (transcriptFollowFrameRef.current !== null) {
      window.cancelAnimationFrame(transcriptFollowFrameRef.current);
    }
    transcriptFollowFrameRef.current = window.requestAnimationFrame(() => {
      transcriptFollowFrameRef.current = null;
      const body = transcriptBodyRef.current;
      if (!body) return;
      transcriptProgrammaticScrollRef.current = true;
      if (behavior === "smooth") {
        body.scrollTo({ top: body.scrollHeight, behavior });
      } else {
        body.scrollTop = body.scrollHeight;
      }
      window.setTimeout(() => {
        const latestBody = transcriptBodyRef.current;
        if (latestBody && transcriptAutoFollowRef.current) {
          latestBody.scrollTop = latestBody.scrollHeight;
        }
        transcriptProgrammaticScrollRef.current = false;
      }, behavior === "smooth" ? 420 : 60);
    });
  };

  useLayoutEffect(() => {
    if (transcriptAutoFollowRef.current) {
      scrollTranscriptToBottom("auto");
    }
  }, [
    visibleSegments.length,
    latestSegmentRevision,
    visibleSubtitleRevision,
    previewSegment?.id,
    previewStackRevision,
    previewSegments.length,
    departingPreviewSegments.length,
    latestSegment?.id,
    latestSubtitleText,
    activeSubtitleLanguage,
  ]);

  useEffect(() => {
    return () => {
      if (transcriptFollowFrameRef.current !== null) {
        window.cancelAnimationFrame(transcriptFollowFrameRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!computeCpuActive && !computeGpuActive) {
      return;
    }
    setComputeActivityHold((prev) => ({
      cpu: prev.cpu || computeCpuActive,
      gpu: prev.gpu || computeGpuActive,
    }));
    const timer = window.setTimeout(() => {
      setComputeActivityHold({ cpu: false, gpu: false });
    }, 1400);
    return () => window.clearTimeout(timer);
  }, [computeCpuActive, computeGpuActive]);

  const currentExportInput = (): ExportMeetingInput => ({
    source: activeSource,
    runtime_profile: activeRuntimeProfile,
    summary_language: summaryLanguage,
    subtitle_language: summaryOnlyMode ? "off" : subtitleLanguage,
    export_dir: exportRoot.trim() || undefined,
    bilingual: bilingualExport,
    target_language: bilingualExport ? ((summaryLanguage === "zh" || summaryLanguage === "en" || summaryLanguage === "de") ? summaryLanguage : undefined) : undefined,
  });

  const handleExportMeeting = async () => {
    setExportIntent("manual");
    setActionFeedback(null);
    setExporting(true);
    try {
      const result = await exportMeeting(currentExportInput());
      if (result) {
        setLastExport(result);
        setActionFeedback(`Export ready in ${result.export_dir}`);
      }
    } catch (error) {
      setActionFeedback(t("Export failed: {0}", (error as Error).message));
    } finally {
      setExporting(false);
      setExportIntent(null);
    }
  };

  const handleChooseExportFolder = async () => {
    const chooser = window.meetingbro?.selectExportDirectory;
    if (!chooser) {
      return;
    }
    const selected = await chooser(defaultExportFolderName());
    if (selected) {
      setExportRoot(selected);
    }
  };

  const handleRuntimeProfileChange = (value: string) => {
    setRuntimeProfile(value);
    if (value === "summary_only") {
      setSubtitleLanguage("off");
    }
  };

  const handleSaveVocabulary = () => {
    window.localStorage.setItem(VOCABULARY_STORAGE_KEY, vocabulary);
    applyVocabulary(vocabulary);
    setActionFeedback(t("Terms saved"));
  };

  const handleStartSession = () => {
    if (hasRetainedMeetingData) {
      setRestartPromptOpen(true);
      return;
    }
    setLastExport(null);
    setSessionEnabled(true);
  };

  const handleTrySample = () => {
    // One-click demo: replay the bundled clip so a first-time user / tester sees
    // the full pipeline without configuring any audio device.
    setSource("sample");
    if (hasRetainedMeetingData) {
      setRestartPromptOpen(true);
      return;
    }
    setLastExport(null);
    setSessionEnabled(true);
  };

  const handleRestartWithoutSave = () => {
    setRestartPromptOpen(false);
    setLastExport(null);
    setActionFeedback(null);
    setSessionEnabled(true);
  };

  const handleSaveAndRestart = async () => {
    setExportIntent("restart");
    setActionFeedback(null);
    setExporting(true);
    try {
      const result = await exportMeeting(currentExportInput());
      if (!result) {
        return;
      }
      setLastExport(result);
      setActionFeedback(`Saved to ${result.export_dir}. Starting a new meeting…`);
      setRestartPromptOpen(false);
      setSessionEnabled(true);
    } catch (error) {
      setActionFeedback(t("Export failed: {0}", (error as Error).message));
    } finally {
      setExporting(false);
      setExportIntent(null);
    }
  };

  const handleBookmark = async () => {
    const label = bookmarkLabel;
    if (!connected || state !== "running") return;
    setBookmarkLabel("");
    await saveBookmark(label);
    setBookmarkFeedback(
      label.trim()
        ? `Bookmark saved at ${formatElapsedSeconds(audioClockSeconds ?? elapsedSeconds)} · ${label.trim()}`
        : `Bookmark saved at ${formatElapsedSeconds(audioClockSeconds ?? elapsedSeconds)}`,
    );
  };

  const vocabularyHint = sessionEnabled
    ? t("Add keywords for better recognition. Save anytime, even during a meeting.")
    : t("Add keywords for better recognition. Save anytime before or during a meeting.");
  const transcriptEmptyMessage = !sessionEnabled
    ? t("session stopped — press Start to begin capture")
    : state === "starting"
      ? t("connecting to the live session…")
      : state === "paused"
        ? t("session paused — press Resume to continue capture")
      : activeSource === "loopback" || activeSource === "system"
        ? t("listening to system audio only — play meeting audio or switch to System + Microphone")
        : activeSource === "mic"
          ? t("listening to microphone — speak to start the transcript")
          : t("listening to system audio + microphone — speak or play meeting audio to start the transcript");

  const openSettings = (tab: typeof settingsTab = "recording") => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  };
  const bindTranscriptBody = useCallback((element: HTMLDivElement | null) => {
    transcriptBodyRef.current = element;
    if (element && transcriptAutoFollowRef.current) element.scrollTop = element.scrollHeight;
  }, []);
  const recordingControls = <div className="recording-preferences">
    <section className="settings-section">
      <h3>{t("Recording and languages")}</h3>
          <label className="settings-field">
            <span>{t("Interface")}</span>
            <select aria-label={t("UI language")} value={uiLanguage} onChange={(e) => setUiLanguage(e.target.value === "en" ? "en" : "zh-Hant")}>
              <option value="zh-Hant">繁體中文</option>
              <option value="en">English</option>
            </select>
          </label>

            <label className="settings-field">
              <span>{t("Capture")} <InputLevelPreview source={source} micDevice={audioDevice} speakerName={loopbackDevice} active={sessionEnabled} running={state === "running"} level={audioLevel} /></span>
              <select aria-label={t("Capture")} value={source} onChange={(e) => setSource(e.target.value)}>
                <option value="mic">{t("Mic")}</option>
                <option value="loopback">{t("System audio")}</option>
                <option value="mixed">{t("System + Mic")}</option>
                <option value="sample">{t("Sample audio (demo)")}</option>
              </select>
            </label>
            {(source === "mic" || source === "mixed") && (
              <label className="settings-field">
                <span>{t("Mic device")}</span>
                <select value={audioDevice} onChange={(e) => setAudioDevice(e.target.value)}>
                  <option value="">{t("Default")}</option>
                  {audioDevice && !audioDevices?.mics.some(device => device.id === audioDevice) && <option value={audioDevice}>{t("Unavailable device ({0})", audioDevice)}</option>}
                  {audioDevices?.mics.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </label>
            )}
            {(source === "loopback" || source === "system" || source === "mixed") && (
              <label className="settings-field">
                <span>{t("Output device")}</span>
                <select value={loopbackDevice} onChange={(e) => setLoopbackDevice(e.target.value)}>
                  <option value="">{t("Default")}</option>
                  {loopbackDevice && !audioDevices?.loopbacks.some(device => device.id === loopbackDevice) && <option value={loopbackDevice}>{t("Unavailable device ({0})", loopbackDevice)}</option>}
                  {audioDevices?.loopbacks.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </label>
            )}
            <button type="button" className="settings-btn" onClick={() => void refreshAudioDevices()}>{t("Refresh audio devices")}</button>
            {audioDeviceError && <p className="settings-hint" role="status">{t("Could not load audio devices.")}</p>}
            <label className="settings-field">
              <span>{t("Mode")}</span>
              <select value={runtimeProfile} onChange={(e) => handleRuntimeProfileChange(e.target.value)}>
                <option value="balanced">{t("Balanced")}</option>
                <option value="performance">{t("Performance")}</option>
                <option value="summary_only">{t("Summary only")}</option>
              </select>
            </label>
            <label className="settings-field">
              <span>{t("Transcription language")}</span>
              <select value={speechLanguage} onChange={(e) => setSpeechLanguage(e.target.value)}>
                <option value="auto">{t("Auto")}</option>
                <option value="en">{t("English")}</option>
                <option value="zh">{t("Chinese")}</option>
                <option value="de">{t("Deutsch")}</option>
              </select>
            </label>
            <label className="settings-field">
              <span>{t("Summary language")}</span>
              <select value={summaryLanguage} onChange={(e) => setSummaryLanguage(e.target.value)}>
                <option value="en">{t("English")}</option>
                <option value="zh">{t("Traditional Chinese")}</option>
                <option value="de">{t("Deutsch")}</option>
              </select>
            </label>
            <label className="settings-field">
              <span>{t("Translation language")}</span>
              <select value={summaryOnlyMode ? "off" : subtitleLanguage} onChange={(e) => setSubtitleLanguage(e.target.value)} disabled={summaryOnlyMode}>
                <option value="off">{t("Off")}</option>
                <option value="en">{t("English")}</option>
                <option value="zh">{t("Chinese")}</option>
                <option value="de">{t("Deutsch")}</option>
              </select>
            </label>
    </section>
    <section className="settings-section">
      <h3>{t("Names & Terms")}</h3><p className="settings-hint">{vocabularyHint}</p>
      <label className="settings-field"><span>{t("Names, acronyms, or project terms")}</span>
        <textarea value={vocabulary} onChange={e => setVocabulary(e.target.value)} rows={3} placeholder="MeetingBro, Qwen3" />
      </label>
      <button type="button" onClick={handleSaveVocabulary}>{t("Save Terms")}</button>
    </section>
    <section className="settings-section">
      <h3>{t("Export meeting files")}</h3>
      <label className="settings-field"><span>{t("Export folder")}</span>
        <input value={exportRoot} onChange={e => setExportRoot(e.target.value)} placeholder={t("Default: project exports/<timestamp> folder")} />
      </label>
      {window.meetingbro?.selectExportDirectory && <button type="button" onClick={handleChooseExportFolder}>{t("Browse…")}</button>}
      <label className="settings-field"><span><input type="checkbox" checked={bilingualExport} onChange={e => setBilingualExport(e.target.checked)} /> {t("Bilingual transcript (original + translation)")}</span></label>
      {lastExport && <p className="settings-hint">{t("Latest export:")} {lastExport.export_dir}</p>}
    </section>
    <section className="settings-section">
      <button type="button" onClick={() => { setSettingsOpen(false); handleTrySample(); }} disabled={!backend.isReady || sessionEnabled}>{t("▶ Try sample")}</button>
    </section>
  </div>;
  const diagnostics = <div className="recorder-diagnostics">
    <div className="diagnostics-compute">
      <span className={`health-badge health-${systemAdviceTone}`}>{t(systemAdviceTone === "ok" ? "Healthy" : "Check needed")}</span>
      <span className={`compute-chip ${displayCpuActive ? "active" : "idle"}`} title={cpuChipTitle}>CPU</span>
      <span className={`compute-chip ${displayGpuActive ? "active" : "idle"}`} title={gpuChipTitle}>GPU</span>
    </div>
            <div className="diagnostics-body">
              <div className={`diagnostic-card advice-card advice-${systemAdviceTone}`}>
                <span className="diagnostic-label">{t("System Advice")}</span>
                <div className="advice-list">
                  {systemAdvice.map((item) => (
                    <div key={`${item.title}-${item.detail}`} className={`advice-item advice-${item.severity}`}>
                      <strong>{item.title}</strong>
                      <span>{item.detail}</span>
                      {item.action && <em>{item.action}</em>}
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="diagnostics-mode-toggle"
                  onClick={() => setDiagnosticsMode((m) => (m === "simple" ? "advanced" : "simple"))}
                >
                  {diagnosticsMode === "simple" ? t("Show advanced details") : t("Hide advanced details")}
                </button>
              </div>
              {diagnosticsMode === "advanced" && (<>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Active Source")}</span>
                <strong className="diagnostic-value">{formatSourceLabel(activeSource)}</strong>
                <span className="diagnostic-note">
                  {activeSource === "mixed"
                    ? t("capturing system audio and microphone")
                    : activeSource === "loopback" || activeSource === "system"
                      ? t("capturing system output only")
                      : t("capturing microphone only")}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Runtime Mode")}</span>
                <strong className="diagnostic-value">{formatRuntimeProfileLabel(activeRuntimeProfile)}</strong>
                <span className="diagnostic-note">
                  {t("chunk")}{formatLagSeconds(activeChunkSeconds)} {" "}{t("· ASR window")}{" "}{formatLagSeconds(activeAccumSeconds)} {" "}{t("· lock")}{" "}{languageLockEnabled ? "on" : "off"}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Hardware Profile")}</span>
                <strong className="diagnostic-value">{activeHardwareProfile}</strong>
                <span className="diagnostic-note">{activeHardwareSummary}</span>
                {computeGpuAvailable && !computeGpuConfigured && (
                  <span className="diagnostic-note diagnostic-hint">{t("GPU available but not assigned to ASR")}</span>
                )}
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Compute Activity")}</span>
                <strong className="diagnostic-value">{computeCpuActive || computeGpuActive ? "active" : "idle"}</strong>
                <span className="diagnostic-note">{computeActivityLabel}</span>
              </div>
              <div className={`diagnostic-card delay-card delay-${resourcePressureLevel === "critical" ? "danger" : resourcePressureLevel === "pressure" ? "warn" : "ok"}`}>
                <span className="diagnostic-label">{t("Resource Governor")}</span>
                <strong className="diagnostic-value">{resourcePressureLevel}</strong>
                <span className="diagnostic-note">
                  {resourceGovernorPolicy} ? skips {resourceGovernorSkips}
                  {resourceGovernorReason ? ` ? ${resourceGovernorReason}` : ""}
                </span>
              </div>
              <div className={`diagnostic-card gain-card gain-${mixedGainTone}`}>
                <span className="diagnostic-label">{t("Mixed Gain")}</span>
                <strong className="diagnostic-value">{mixedGainRatio}</strong>
                <span className="diagnostic-note">
                  {activeSource === "mixed"
                    ? t("mic effective {0} · base {1} · auto {2}", formatGain(mixedEffectiveMicGain), formatGain(mixedMicGain), mixedAutoBalanceEnabled ? "on" : "off")
                    : mixedGainHint}
                </span>
                {activeSource === "mixed" && (
                  <span className="diagnostic-note diagnostic-hint">{mixedGainHint}</span>
                )}
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Subtitles")}</span>
                <strong className="diagnostic-value">{subtitleStatus}</strong>
                <span className="diagnostic-note">{subtitleStatusDetail}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Audio Clock")}</span>
                <strong className="diagnostic-value">{formatElapsedSeconds(audioClockSeconds)}</strong>
                <span className="diagnostic-note">{elapsedSeconds > 0 ? t("backend progress") : t("frontend estimate")}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Committed Transcript")}</span>
                <strong className="diagnostic-value">{formatElapsedSeconds(visibleTranscriptSeconds)}</strong>
                <span className="diagnostic-note">{latestSegment ? t("last committed · {0}", formatRange(latestSegment.start_time, latestSegment.end_time)) : t("waiting for committed text")}</span>
              </div>
              <div className={`diagnostic-card delay-card delay-${delayTone}`}>
                <span className="diagnostic-label">{t("Realtime Gap")}</span>
                <strong className="diagnostic-value">{formatLagSeconds(transcriptLagSeconds)}</strong>
                <span className="diagnostic-note">{t("current audio clock minus latest visible transcript")}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Commit Delay")}</span>
                <strong className="diagnostic-value">{formatLagSeconds(committedLagSeconds)}</strong>
                <span className="diagnostic-note">
                  {committedLagSeconds != null
                    ? t("reference only · commit time minus segment end")
                    : t("waiting for committed text")}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Retry Summary")}</span>
                <strong className="diagnostic-value">{retryTotal}</strong>
                <span className="diagnostic-note">{t("improved")}{" "}{retryImproved} {" "}{t("· unchanged")}{" "}{retryUnchanged} {" "}{t("· diverged")}{" "}{retryDiverged}</span>
              </div>
              <div className={`diagnostic-card delay-card delay-${asrSafeguardActive ? "danger" : (asrRealtimeFactor != null && asrRealtimeFactor > 0.8 ? "warn" : "ok")}`}>
                <span className="diagnostic-label">{t("ASR Realtime")}</span>
                <strong className="diagnostic-value">{formatRtf(asrRealtimeFactor)}</strong>
                <span className="diagnostic-note">
                  {t("audio")}{formatLagSeconds(asrAudioSeconds)} · ASR {formatLagSeconds(asrWallSeconds)}
                </span>
              </div>
              <div className={`diagnostic-card delay-card delay-${fastPreviewRtf != null && fastPreviewRtf > 0.8 ? "warn" : "ok"}`}>
                <span className="diagnostic-label">{t("Fast Preview")}</span>
                <strong className="diagnostic-value">{fastPreviewEnabled ? formatRtf(fastPreviewRtf) : "off"}</strong>
                <span className="diagnostic-note">
                  {t("emitted")}{fastPreviewEmitted} {" "}{t("· skipped")}{" "}{fastPreviewSkipped}{sessionStats?.fast_preview_skip_reason ? ` · ${sessionStats.fast_preview_skip_reason}` : ""}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Realtime Internals")}</span>
                <strong className="diagnostic-value">q {eventQueueDepth}</strong>
                <span className="diagnostic-note">
                  {t("audio wait")}{formatLagSeconds(audioQueueWaitSeconds)} {" "}{t("· preview wake")}{" "}{formatLagSeconds(fastPreviewScheduleDelay)} {" "}{t("· concat")}{" "}{formatLagSeconds(snapshotConcatWall)}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Preview Continued")}</span>
                <strong className="diagnostic-value">{previewContinuedDuringFormal}</strong>
                <span className="diagnostic-note">{t("Qwen preview updates while formal text is pending")}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Preview Stale Suppressed")}</span>
                <strong className="diagnostic-value">{previewStaleSuppressed}</strong>
                <span className="diagnostic-note">{t("previews dropped (covered by formal)")}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Preview Alignment")}</span>
                <strong className="diagnostic-value">
                  {previewAlignmentCompared > 0
                    ? t("{0} avg", (previewAlignmentSimilarityAvg ?? 0).toFixed(2))
                    : "—"}
                </strong>
                <span className="diagnostic-note">
                  {t("compared")}{previewAlignmentCompared} {" "}{t("· last")}{" "}{previewAlignmentSimilarityLast != null ? previewAlignmentSimilarityLast.toFixed(2) : "—"}
                </span>
              </div>
              <div className={`diagnostic-card delay-card delay-${previewUnconfirmedAfterFormal > 0 ? "warn" : "ok"}`}>
                <span className="diagnostic-label">{t("Preview Unconfirmed")}</span>
                <strong className="diagnostic-value">{previewUnconfirmedAfterFormal}</strong>
                <span className="diagnostic-note">
                  {previewUnconfirmedLastText ? t("last: {0}", previewUnconfirmedLastText) : t("no preview has been passed by formal without overlap")}
                </span>
              </div>
              <div className={`diagnostic-card delay-card delay-${qwenTargetedRetryFailed > 0 ? "warn" : "ok"}`}>
                <span className="diagnostic-label">{t("Qwen Recovery")}</span>
                <strong className="diagnostic-value">{qwenTargetedRetryRecovered}</strong>
                <span className="diagnostic-note">
                  {t("attempts")}{qwenTargetedRetryAttempts} {" "}{t("· failed")}{" "}{qwenTargetedRetryFailed} {" "}{t("· skipped")}{" "}{qwenTargetedRetrySkipped}
                  {qwenTargetedRetryLastReason ? ` · ${qwenTargetedRetryLastReason}` : ""}
                </span>
              </div>
              <div className={`diagnostic-card delay-card delay-${asrSafeguardActive ? "danger" : "ok"}`}>
                <span className="diagnostic-label">{t("Realtime Safeguard")}</span>
                <strong className="diagnostic-value">{asrSafeguardActive ? "active" : "clear"}</strong>
                <span className="diagnostic-note">
                  {asrSafeguardActive
                    ? (sessionStats?.asr_safeguard_reason ?? t("protecting realtime path"))
                    : t("events {0}", asrSafeguardEvents)}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Weak Voice Rescue")}</span>
                <strong className="diagnostic-value">{weakRescueAttempts}</strong>
                <span className="diagnostic-note">
                  {t("emitted")}{weakRescueEmitted} {" "}{t("· buffer")}{" "}{formatLagSeconds(weakRescueBufferSeconds)}
                </span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Background Queue")}</span>
                <strong className="diagnostic-value">{translationPendingCount + summaryPendingCount}</strong>
                <span className="diagnostic-note">
                  {t("translations")}{translationPendingCount} {" "}{t("· summaries")}{" "}{summaryPendingCount} {" "}{t("· trims")}{" "}{translationTrimTotal}
                </span>
              </div>
              <div className={`diagnostic-card delay-card delay-${audioInputBacklogSeconds >= 5 ? "warn" : "ok"}`}>
                <span className="diagnostic-label">{t("Audio Backlog")}</span>
                <strong className="diagnostic-value">{formatLagSeconds(audioInputBacklogSeconds)}</strong>
                <span className="diagnostic-note">{t("input queue drops")}{" "}{audioInputQueueDropTotal}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Audio Drops")}</span>
                <strong className="diagnostic-value">{audioDropTotal}</strong>
                <span className="diagnostic-note">{t("capture queue dropped chunks")}</span>
              </div>
              <div className="diagnostic-card">
                <span className="diagnostic-label">{t("Backpressure")}</span>
                <strong className="diagnostic-value">{backpressureAgeSeconds != null ? "recent" : "clear"}</strong>
                <span className="diagnostic-note">
                  {backpressureAgeSeconds != null
                    ? t("last seen {0} ago at {1}", formatLagSeconds(backpressureAgeSeconds), formatElapsedSeconds(sessionStats?.last_backpressure_elapsed_seconds))
                    : t("no recent slow-ASR flush")}
                </span>
              </div>
              </>)}
            </div>
  </div>;
  const liveTranscript = <div className="live-caption-sheet">
    <form className="bookmark-form" onSubmit={e => { e.preventDefault(); void handleBookmark(); }}>
      <input value={bookmarkLabel} onChange={e => setBookmarkLabel(e.target.value)} placeholder={t("Bookmark label (optional)")} aria-label={t("Bookmark label (optional)")} />
      <button type="submit" disabled={!connected || state !== "running"}>{t("Bookmark")}</button>
    </form>
        {summaryOnlyMode ? (
          <section className="panel transcript summary-only-panel">
            <header>
              <div>
                <h2>
                  {t("Summary Only")}{state === "running" && <span className="live-dot" aria-label="recording" />}
                </h2>
                <p className="subtitle">{t("capturing in background · no live captions")}</p>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div className="range">{segments.length} {" "}{t("segments")}</div>
              </div>
            </header>
            <div className="panel-body summary-only-body">
              <div className="summary-only-banner">
                <strong>{t("Realtime captions are intentionally hidden in this mode.")}</strong>
                <p>
                  {t("MeetingBro is still listening and collecting transcript segments, then updating Summary, Clean Notes, and Board on their own cadence.")}</p>
              </div>
              <div className="summary-only-stats">
                <div className="summary-only-stat">
                  <span>{t("Captured")}</span>
                  <strong>{segments.length} {" "}{t("segments")}</strong>
                </div>
                <div className="summary-only-stat">
                  <span>{t("Elapsed")}</span>
                  <strong>{formatElapsedSeconds(audioClockSeconds ?? elapsedSeconds)}</strong>
                </div>
                <div className="summary-only-stat">
                  <span>{t("Latest summary")}</span>
                  <strong>{rolling?.created_at ? formatCreatedAt(rolling.created_at) : "waiting"}</strong>
                </div>
              </div>
              <p className="muted summary-only-tip">
                {t("Tip: use the Refresh button in Summary or Clean Notes if you want an immediate update.")}</p>
            </div>
          </section>
        ) : <section className="panel transcript">
          <header>
            <div>
              <h2>
                {t("Live Captions")}{state === "running" && <span className="live-dot" aria-label="recording" />}
              </h2>
              <p className="subtitle">{t("confirmed · timestamped")}</p>
              <p className="transcript-lag-meta">
                <span className={`delay-pill delay-${delayTone}`}>{formatLagSeconds(transcriptLagSeconds)} {" "}{t("behind")}</span>
                {committedLagSeconds != null ? ` · commit ${formatLagSeconds(committedLagSeconds)}` : ""}
                {previewSegment ? t(" · previewing") : ""}
              </p>
              {bookmarkFeedback && <p className="bookmark-feedback">{t(bookmarkFeedback)}</p>}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="range">{segments.length} {" "}{t("segments")}</div>
            </div>
          </header>
          <div
            ref={bindTranscriptBody}
            className="panel-body transcript-body"
            onScroll={(e) => {
              if (transcriptProgrammaticScrollRef.current) {
                return;
              }
              const follow = isNearBottom(e.currentTarget);
              transcriptAutoFollowRef.current = follow;
              setTranscriptAutoFollow(follow);
            }}
          >
            {visibleSegments.length === 0 && (
              <div className="muted">{transcriptEmptyMessage}</div>
            )}
            {transcriptTimelineItems.map((item) => {
              if (item.type === "bookmark") {
                return (
                  <div key={item.note.id} className="segment bookmark-marker">
                    <span className="ts">{t("bookmark ·")}{" "}{formatElapsedSeconds(item.note.time_seconds)}</span>
                    <div className="segment-text">
                      <span>{item.note.content.trim() || t("Important moment")}</span>
                    </div>
                  </div>
                );
              }

              const s = item.segment;
              const confidenceTone = getConfidenceTone(s.confidence);
              const confidenceLabel = getConfidenceLabel(s.confidence);
              const qualityStyle: CSSProperties | undefined = s.quality === "low"
                ? { opacity: 0.55, fontStyle: "italic" }
                : s.quality === "uncertain"
                  ? { opacity: 0.7 }
                  : undefined;
              const subtitleText = activeSubtitleLanguage ? s.translations[activeSubtitleLanguage] : null;
              const showSubtitlePlaceholder =
                activeSubtitleLanguage != null &&
                s.original_language !== activeSubtitleLanguage &&
                !(subtitleText && subtitleText.trim());
              const segmentOrder = newSegmentOrders[s.id];
              const isNewSegment = segmentOrder !== undefined;
              const segmentDelayMs = isNewSegment ? segmentOrder * FORMAL_SEGMENT_STAGGER_MS : 0;
              const segmentStyle: StaggerStyle | undefined = isNewSegment
                ? {
                  ...qualityStyle,
                  "--segment-delay": `${segmentDelayMs}ms`,
                  "--segment-text-delay": `${segmentDelayMs + FORMAL_CELL_STAGGER_MS}ms`,
                }
                : qualityStyle;
              const segmentClassName = [
                "segment",
                isNewSegment ? "segment-new" : "",
                isNewSegment && hasPreviewHandoff(s, handoffPreviewSegments) ? "segment-promoted" : "",
                confidenceTone !== "stable" ? `segment-${confidenceTone}` : "",
              ].filter(Boolean).join(" ");
              return (
                <div key={s.id} className={segmentClassName} style={segmentStyle}>
                  <span className="ts">
                    {formatCreatedAt(s.created_at)}
                    {confidenceLabel && <em className={`confidence-flag confidence-${confidenceTone}`}>{confidenceLabel}</em>}
                  </span>
                  <div className="segment-text">
                    <span>{s.text}</span>
                    {subtitleText && subtitleText.trim() && subtitleText.trim() !== s.text.trim() && (
                      <span className="segment-subtitle">{subtitleText}</span>
                    )}
                    {showSubtitlePlaceholder && (
                      <span className="segment-subtitle segment-subtitle-pending" aria-label={t("translating subtitle")}>
                        <span className="subtitle-loading-dot" />
                        <span className="subtitle-loading-dot" />
                        <span className="subtitle-loading-dot" />
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
            {shouldShowPreviewStack && (
              <div className="preview-stack" aria-live="polite" aria-label={t("Live preview transcript")}>
                {departingPreviewSegments.map((segment, index) => {
                  const previewDelayMs = index * PREVIEW_ROW_STAGGER_MS;
                  const previewStyle: StaggerStyle = {
                    "--preview-status-delay": `${previewDelayMs}ms`,
                    "--preview-text-delay": `${previewDelayMs + PREVIEW_CELL_STAGGER_MS}ms`,
                  };
                  return (
                  <div key={`leaving-${segment.id}`} className="segment preview-segment preview-age-0 preview-leaving" style={previewStyle} aria-hidden="true">
                    <span className="preview-status">
                      <span className="preview-hearing">
                        <span className="preview-pulse-dot" aria-hidden="true" />
                        {t("Listening")}</span>
                    </span>
                    <span className="preview-segment-text">{segment.text}</span>
                  </div>
                  );
                })}
                {previewSegments.length > 0 && previewSegments.map((segment, index) => {
                  const isNewest = index === previewSegments.length - 1;
                  const age = previewSegments.length - index - 1;
                  const displayText = isNewest
                    ? previewDisplayText ?? (previewIsStreaming ? "" : segment.text)
                    : segment.text;
                  const showCursor = isNewest && !previewIsStreaming && previewDisplayText !== null;
                  const previewDelayMs = (departingPreviewSegments.length + index) * PREVIEW_ROW_STAGGER_MS;
                  const previewStyle: StaggerStyle = {
                    "--preview-status-delay": `${previewDelayMs}ms`,
                    "--preview-text-delay": `${previewDelayMs + PREVIEW_CELL_STAGGER_MS}ms`,
                  };
                  return (
                    <div key={segment.id} className={`segment preview-segment preview-age-${Math.min(age, 4)}`} style={previewStyle}>
                      <span className="preview-status">
                        <span className="preview-hearing">
                          <span className="preview-pulse-dot" aria-hidden="true" />
                          {t("Listening")}</span>
                        {isExperimentalPreview && (
                          <span
                            className="preview-experimental-badge"
                            title={t("Qwen3 fast preview · text is live and may still change")}
                          >
                            {t("Qwen · Live")}</span>
                        )}
                      </span>
                      <span className="preview-segment-text">
                        {displayText}
                        {showCursor && <span className="preview-cursor" aria-hidden="true" />}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            <div ref={transcriptBottomRef} className="transcript-bottom-anchor" />
            {!transcriptAutoFollow && (
              <button
                type="button"
                className="jump-latest-btn"
                onClick={() => {
                  transcriptAutoFollowRef.current = true;
                  setTranscriptAutoFollow(true);
                  scrollTranscriptToBottom("smooth");
                }}
              >
                {t("Jump to latest")}</button>
            )}
          </div>
        </section>}


  </div>;
  return <div className="app recorder-app">
    {!backend.isReady && <BackendGate state={backend.state} detail={backend.detail} logTail={backend.logTail} onRetry={backend.retry} onOpenLog={backend.openLog} />}
    <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} sessionActive={sessionEnabled} initialTab={settingsTab} recordingControls={recordingControls} diagnostics={diagnostics} />
    <RecorderView ready={backend.isReady} active={sessionEnabled} state={state} meetingId={meetingId} elapsed={elapsedSeconds} audioLevel={audioLevel}
      segments={segments} summary={latestByType.final_summary ?? cumulative ?? rolling} report={liveReport} finalJob={finalJob}
      error={lastError} errorCode={lastErrorCode} feedback={actionFeedback ?? bookmarkFeedback}
      summaryOnly={summaryOnlyMode} liveTranscript={liveTranscript}
      liveExtras={null}
      onStart={handleStartSession} onPause={pauseSession} onResume={resumeSession}
      onStop={() => { stopSession(); setSessionEnabled(false); }}
      onExport={handleExportMeeting} onSettings={() => openSettings()} onModelSettings={() => openSettings("models")} onDiagnostics={() => openSettings("diagnostics")} />
      {restartPromptOpen && (
        <div className="confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="restart-dialog-title">
          <div className="confirm-dialog">
            <h3 id="restart-dialog-title">{t("Start a new meeting?")}</h3>
            <p>
              {t("Starting again will clear the current transcript, summaries, bookmarks, and notes from the screen.")}</p>
            <p className="muted">
              {t("Save this meeting first if you want to keep an export copy before opening a new session.")}</p>
            <div className="confirm-actions">
              <button type="button" className="secondary-action-btn" onClick={() => setRestartPromptOpen(false)} disabled={exporting}>
                {t("Cancel")}</button>
              <button type="button" className="danger-action-btn" onClick={handleRestartWithoutSave} disabled={exporting}>
                {t("Start Without Saving")}</button>
              <button type="button" className={`primary-export-btn ${exporting && exportIntent === "restart" ? "button-busy" : ""}`.trim()} onClick={handleSaveAndRestart} disabled={exporting} aria-busy={exporting && exportIntent === "restart"}>
                {exporting ? t("Saving…") : t("Save and Start New")}
              </button>
            </div>
            {(exporting || actionFeedback) && (
              <p className={`confirm-status ${exporting ? "is-busy" : "is-success"}`}>
                {exporting ? t("Saving the current meeting export before opening a new session…") : (actionFeedback ? t(actionFeedback) : null)}
              </p>
            )}
          </div>
        </div>
      )}
  </div>;
}
