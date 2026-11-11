# MeetingBro 本地 AI Recorder 遷移調查及計劃

調查日期：2026-11-10。依據本地工作目錄（包括尚未提交的既有修改），不是上游某個假設版本。本文是設計提案，不代表後續功能已完成。第一階段不改動現有執行流程。下列新增檔案、API 和設定均是**建議**，最終名稱及介面須在實作時確認。

## 1. 現有架構與資料流

```mermaid
flowchart TD
  mic[麥克風: sounddevice] --> audio[AudioSource / 16 kHz mono chunks]
  bh[macOS BlackHole 虛擬輸入] --> audio
  mixed[MixedAudioSource: 麥克風 + loopback] --> audio
  audio --> conditioning[音訊調整 / PreVadSegmenter / Silero 或 RMS fallback]
  conditioning --> accum[累積與切段 / 可設定 overlap]
  accum --> formal[正式 ASR: faster-whisper 或 whisper.cpp]
  audio --> recent[近期音訊 deque / preview window]
  recent --> preview[預覽: whisper-streaming / Qwen3 sherpa-onnx / Whisper]
  formal --> manager[SessionManager: pending / 合併 / 去重 / commit]
  preview --> manager
  manager --> db[(SQLite: meetings / segments / snapshots / notes / speakers)]
  manager --> ws[WebSocket /ws/session]
  manager --> summary[背景摘要: rolling / memory / board / final]
  summary --> llm[LLMSummarizer / OpenAI-compatible 或 heuristic fallback]
  summary --> db
  summary --> ws
  ws --> ui[React transcript / summary / board]
  db --> export[POST /meetings/{id}/export → Markdown + metadata JSON]
```

### 音訊擷取及 VAD

- `main.py:_build_audio_source` 按來源建立 `MicrophoneSource`、`SystemAudioLoopbackSource` 或混音來源。`audio/capture.py:MicrophoneSource.stream` 用 sounddevice 回呼擷取麥克風，平均聲道為 mono float32；回呼放進有上限的 queue，滿時會計算丟棄。`audio/loopback.py:SystemAudioLoopbackSource` 在 macOS 使用虛擬 loopback 輸入（例如 BlackHole）；需要依 README 設置 Multi-Output Device。系統音訊可由裝置原生取樣率轉為目標取樣率；`_resample_mono` 使用 polyphase resampling。預設目標 16 kHz、mono；實際設定受 profile 和裝置支援影響。
- `audio/mixed.py:MixedAudioSource` 混合兩個來源並提供增益與自動平衡。`audio/vad.py:SileroSpeechDetector.detect` 載入共用 Silero 模型並呼叫 `get_speech_timestamps`，失敗時退回 RMS gate；`PreVadSegmenter` 緩存語音、等待尾端靜音或長度上限再輸出。`SessionManager._run` 負責 conditioning、預 VAD、累積、靜音判斷、ASR 送出與近期音訊 deque。`SessionConfig` 預設 capture chunk 0.5 秒、累積 2 秒、formal overlap 0 秒；實際值可被 profile / 環境設定覆寫。預覽採重複窗口，與正式路徑的可選 overlap 不同。queue 滿或 ASR 太慢可能丟音，現有 watchdog 和統計可以觀察，但不能當成無損保證。

### ASR、字幕與 WebSocket

- `main.py:lifespan` 使用本地新增的 `asr/factory.py:build_formal_asr` 建立正式 Whisper：`FasterWhisperAdapter` 或 `WhisperCppAdapter`，視 engine/device 設定與硬體而定；模型由應用狀態共用。`ASRAdapter` 目前只定義同步 `transcribe`，沒有統一 lifecycle 或 streaming 合約。
- `main.py:_build_preview_asr` 建立預覽 adapter。`Qwen3ASRAdapter` 是 sherpa-onnx 的離線窗口解碼（`OfflineRecognizer.from_qwen3_asr`），並非 MLX；其模型與 recognizer 惰性載入。`StreamingWhisperAdapter` 透過正式 Whisper 的 word transcription 和 `streaming.py:StreamingTranscriber` 執行 LocalAgreement。它依賴底層 word-level 能力，不能假設可直接用於 Qwen MLX。
- `SessionManager._run` 將正式窗口交 `_transcribe_window`，近期窗口交 `_transcribe_fast_preview_window`；兩者各有 executor 和限流／過期處理。`_queue_pending_segment`、`_consume_pending_segment`、`_flush_silence_boundary_segments`、`_persist_and_emit_segment` 負責正式提交；Qwen 存在 orphan/draft 提升及 `_whisper_replaces_qwen_drafts` 刪除重疊草稿的特殊路徑，故不能只更換工廠便保證 Qwen 權威。`_emit_transcript_preview` 傳暫定內容，正式提交經 `transcript_segment`，草稿撤回經 `transcript_segment_removed`；`main.py:_forward_events` 經 `/ws/session` 推送。`useSessionSocket.ts` 分別處理預覽、正式片段、刪除和摘要。
- `schemas.py:TranscriptSegment` 有 id、meeting_id、時間、文字、語言、信心等，沒有 revision/status/sequence；`TranscriptPreviewPayload` 包裝 segment，而不是相同版本化狀態機。`Storage.insert_segment/list_segments` 持久化正式資料，預覽並非一般持久化的最終紀錄；但 Qwen draft promotion 是特例，遷移時要特別測試。`Storage.insert_snapshot/list_snapshots` 保存摘要及來源 ID。
- WebSocket 生命週期目前綁定 `SessionManager`：`session_ws` 建立及啟動 manager，斷線後 finally 呼叫 `stop(graceful=stop_requested)`；不能假設斷線後會恢復同一錄音 session。重連續錄需另行設計 session 擁有權、快照及重放，避免建立新會議或遺失狀態。

### 摘要及停止流程

- `SessionManager._maybe_emit_rolling/_emit_rolling` 按 interval、窗口與新片段更新 rolling；`_emit_memory` 增量壓縮會議記憶；`_emit_cumulative` 使用記憶或先前 board 加近期上下文。預設 `SessionConfig` 分別為 60 秒 rolling、180 秒窗口、120 秒 memory、180 秒 cumulative；profile／設定可能覆寫。`_build_and_emit_snapshot` 經 summary executor 呼叫 `Summarizer`，保存 `SummarySnapshot` 並發出 `summary_snapshot`；前端 hook 收取並由 `App.tsx` 顯示。
- 明確停止時 `_run` 會處理尾段並呼叫 `_emit_final`，後者呼叫 `finalize_meeting`，失敗時退回一般 final summary；`stop` 等待 session task 及背景任務，但有逾時／取消機制，因此現時不能保證長時間推理一定完成。最終摘要使用 `_summary_context_segments` 和壓縮記憶；`LLMSummarizer.finalize_meeting` 亦有文字截斷，並非保證完整逐字稿的分層摘要。應保留原文並補上可恢復的分層處理。
- `summarization/llm.py:LLMSummarizer` 使用 `llm/openai_compatible.py:OpenAICompatibleClient` 或其他 provider，普通摘要失敗退回 `HeuristicSummarizer` 並可發錯誤事件。Ollama 目前透過 OpenAI-compatible `/v1/chat/completions`；`from_env` 要求非空 API key（文件用佔位值），不是專用 Ollama 管理 adapter。Settings 已支援 base URL、model 和測試連線，尚須核實及實作模型清單和獨立開關。

### 匯出與儲存

- `POST /meetings/{meeting_id}/export` 呼叫 `exporter.py:export_meeting`，從 `Storage.get_meeting/list_segments/list_snapshots/list_notes` 讀取資料；寫 `transcript.md`、`summary.md`、`metadata.json`，有 refined snapshot 時另寫 `clean_conversation.md`。JSON 是 metadata，並非完整逐字稿 JSON；未見獨立 TXT 或完整 JSON 匯出。保留現有輸出，未來如需 TXT/完整 JSON 須另行加入，而非宣稱已存在。
- `App.tsx` 的 Export Meeting 與 `useSessionSocket.ts:exportMeeting` 呼叫現有 API；`HistoryModal.tsx` 亦涉及歷史會議。預設資料庫為專案 `data/meetingbro.db`，可由 `MEETINGBRO_DB` 覆寫；預設匯出根目錄為 `exports`，可設定。`storage/db.py` 使用 SQLite WAL、現有 `CREATE TABLE IF NOT EXISTS` 和少量 `ALTER TABLE`；尚無通用版本化 migration 框架。

## 2. 預計修改範圍（已有檔案）

| 檔案 | 現有職責／具體修改入口 | 擬修改原因 |
|---|---|---|
| `app/backend/meetingbro/asr/base.py` | `ASRAdapter.transcribe`, `ASRSegment` | 增加相容的初始化、reset、shutdown 與分段能力；不迫使現有 adapter 失效。 |
| `app/backend/meetingbro/asr/factory.py` | `build_formal_asr` 選 Whisper engine | 增加正式引擎選擇及明確 fallback；保留本地 whisper.cpp 修改。 |
| `app/backend/meetingbro/asr/qwen3_asr_adapter.py` | sherpa-onnx Qwen 預覽 | 保留原選項；不要冒充 MLX。對照其過濾與語言處理，避免將 preview 專用假設照搬 formal。 |
| `app/backend/meetingbro/asr/streaming.py`, `streaming_whisper_adapter.py` | LocalAgreement 和 Whisper word preview | 檢視可抽出的通用穩定文字邏輯；若 MLX 無 word timestamps，不應依賴此介面。 |
| `app/backend/meetingbro/main.py` | `lifespan`, `_build_preview_asr`, `session_ws`, config/export endpoints | 建立與顯示正式 engine，管理 session lifecycle、重連、Ollama 和報告路由；保留既有路由。 |
| `app/backend/meetingbro/session/manager.py` | `_run`, `_transcribe_window`, `_whisper_replaces_qwen_drafts`, `_persist_and_emit_segment`, `stop`, `_emit_final`, 摘要及 governor | 將正式／暫定權威及版本化明確化，處理停止排空、積壓、重連及摘要排程；避免 Qwen 正式結果被 Whisper 草稿交接覆蓋。 |
| `app/backend/meetingbro/audio/capture.py`, `audio/vad.py`, `audio/mixed.py`, `audio/loopback.py` | 擷取、混音、VAD、buffer | 優先沿用；只在實測暴露資料遺失、尾段或 backpressure 問題時針對性修改。 |
| `app/backend/meetingbro/schemas.py` | `TranscriptSegment`, `TranscriptPreviewPayload`, `SummarySnapshot` 等 | 增加向後相容的字幕版本與驗證過的報告 schema；既有快照仍可讀。 |
| `app/backend/meetingbro/storage/db.py` | `Storage`、SCHEMA、segment/snapshot CRUD | 增加具版本的 migration、報告 revision CRUD；保護舊資料與順序。 |
| `app/backend/meetingbro/summarization/llm.py`, `summarization/base.py`, `summarization/heuristic.py`, `llm/openai_compatible.py` | 摘要介面、provider 與 fallback | 改善增量與長文分層、結構化輸出及錯誤狀態；支援本地 Ollama 無需假 key，同時保留其他 provider。 |
| `app/backend/meetingbro/exporter.py` | `export_meeting` 和 Markdown writers | 保留原輸出，擴展安全的離線 HTML；是否提供 TXT／完整 JSON 另外實作。 |
| `app/frontend/src/session/useSessionSocket.ts`, `types.ts` | WS reducer、型別、export API | 版本判斷、重連同步、報告事件與型別，兼容現有事件。 |
| `app/frontend/src/App.tsx`, `App.css` | transcript、summary、board、export UI | 增加報告視圖、匯出入口與狀態，保留捲動及既有 UI。 |
| `app/frontend/src/components/SettingsModal.tsx`, `HistoryModal.tsx` | 設定和歷史會議 | 正式 ASR、Ollama 模型／狀態和歷史報告入口。 |
| `app/frontend/electron/settings-store.cjs`, `backend.cjs`, `preload.cjs`, `main.cjs`, `src/meetingbro-bridge.d.ts` | 持久設定、後端環境、Electron bridge | 依實際需要傳遞新設定；保持安全邊界及舊設定相容。 |
| `.env.example`, `app/backend/pyproject.toml`, `app/frontend/package.json` | 設定範例及依賴 | 清楚標示可選 MLX 和 Ollama 設定；依選定 bundle 工具調整依賴。 |
| `README.md`, `docs/architecture.md`, `docs/llm-providers.md` | 操作與架構文件 | 在功能確實完成及測試後更新安裝、限制、回退與隱私說明。 |
| `tests/test_preview_backend_selection.py`, `tests/test_streaming_transcriber.py`, `tests/test_streaming_whisper_adapter.py`, `tests/test_realtime_lifecycle.py`, `tests/test_locale_and_auto_engine.py` | 現有 preview、串流、生命週期與語言測試 | 延伸相容及回歸案例；保留 `test_faster_whisper_words.py`、`test_whisper_cpp_adapter.py`。 |

可能新增：MLX adapter、報告 schema／renderer 元件、獨立 HTML bundle、版本化 migration 和相關測試。這些是待設計的新檔案，不是聲稱現有檔案。實際改動以各階段 diff 為準。

## 3. 相容性、設定與資料設計提案

1. **模型與平台**：先在目標環境查證 `mlx-qwen3-asr` 已安裝版本、公開 API、模型檔及授權；量化版本、語言與提示支援均須實測。MLX 不可用、記憶體不足或載入失敗時提供明確錯誤及使用者可選的 Whisper 回退。保留 Windows/Linux 原有路徑；不要假設所有 Whisper adapter 均有 word API。Cantonese 用實際模型支援的參數或 auto／Chinese，不自行發明語言碼。
2. **字幕**：提議新增穩定 segment ID、單調 revision、partial/final 狀態、sequence；保留現有 `meeting_id` 和必要時間欄位，舊事件可在過渡期映射。明確定義同 ID 的版本排序、final 不可被舊 partial 覆蓋、重疊去重、pause/resume、stop flush、排隊丟棄策略；Qwen 正式模式關閉 Whisper 覆蓋與不適用的 cross-engine 草稿流程。遷移需檢查現存 draft 記錄，避免誤刪。
3. **資料庫**：提議以版本化 migration 加新增 report document/revision 表，記錄 report ID、meeting ID、類型、revision、驗證後 JSON 和時間；加唯一約束並保留舊表。先以舊資料庫副本測試升級、失敗回滾和重開。不要將 partial 當正式摘要來源。
4. **報告資料**：以 Pydantic 和對應 TypeScript 定義 title、executive summary、takeaways、sections、具類型 content blocks、來源 segment IDs 等；缺值降級，圖表需有逐字稿證據。Schema 驗證只能檢查形狀和引用，不能單獨保證語義真實，須補來源檢查及人工驗收。LLM 不輸出可直接注入的 HTML、CSS 或 JS。
5. **建議設定（名稱待實作確認）**：formal ASR backend/model/quantization、語言與資源模式；Ollama base URL/model、live/final 開關與摘要語言；避免把建議名稱當作現在已支援的環境變數。現有 `MEETINGBRO_WHISPER_ENGINE`、`MEETINGBRO_PREVIEW_ASR_BACKEND`、`MEETINGBRO_LLM_*` 和 Electron 儲存設定應維持相容。預設模型及 8 GB 可行性須依實測決定。
6. **安全及離線**：匯出使用受控元件與文字轉義、安全 JSON 序列化及內嵌本地資源；不嵌入密鑰、外部 CDN 或本機路徑。離線及 script injection 需實測。報告增量更新應保留組件 key 和閱讀位置。
7. **資源與失敗**：沿用現有獨立 executor、pressure governor 和診斷，加入有界佇列、過時工作合併及 ASR 優先；現有 governor 不等同完整記憶體壓力協調。Ollama 不可用不得阻塞轉錄；停止後的 final job 應能表達排隊、失敗及重試，避免超時被誤報完成。

## 4. 分階段實作與驗證

| 階段 | 工作及驗證 |
|---|---|
| 1 調查 | 核對呼叫鏈、模型、現有設定、資料路徑和已修改工作樹；交付本文。 |
| 2 Qwen3 整合 | 驗證 MLX 套件／授權及量化模型；新增 adapter、選擇與回退；使用 mock 測生命周期及現有 Whisper 回歸，再於 Apple Silicon 實測載入和記憶體。 |
| 3 字幕管線 | 穩定 ID/revision、去重、停止尾段、pause/resume、重連和積壓；以受控音訊及亂序事件測試，確認 Qwen formal 不受 Whisper 覆蓋。 |
| 4 Ollama 摘要 | 沿用 snapshot、memory 和事件；測連線、模型選擇、斷線 fallback、final job 和完整逐字稿分層。 |
| 5 結構化 schema | 驗證不完整或惡意 JSON、來源引用、無證據數字與舊資料升級；新增 revision migration 測試。 |
| 6 報告 renderer | 實作多類可重用元件、無障礙、手機、列印和 presentation mode；驗證三類會議且不虛構數據。 |
| 7 即時整合 | 測增量 revision、重連後同步、錯誤與生成中狀態、捲動位置和歷史會議。 |
| 8 離線 HTML | 保留 Markdown 匯出；測單檔離線、注入防護、章節／timeline／複製／presentation、Safari、Chrome 和手機尺寸。 |
| 9 資源協調 | 測三種模式下的佇列深度、延遲、記憶體、丟音和降級，尤其 M2 8 GB；依量測調整。 |
| 10 端到端與文件 | 以粵語、普通話、英文、混合語言及噪音錄音比較 CER/WER 和延遲；至少 15 分鐘測摘要，記錄硬體／模型／參數和未達標項目；更新文件與回退指引。 |

每階段先跑相應自動化測試與既有回歸，再記錄手動及硬體測試限制。不得以 mock 測試代替模型、音訊裝置或瀏覽器實測。現有工作樹已有大量未提交變更，實作和任何 commit 必須避免把不屬於該階段的變更混入。

## 5. 第一階段驗證與未決事項

本階段以靜態程式碼搜尋及閱讀為主，沒有下載模型、啟動錄音或執行 M2 8 GB 效能測試。尚待確認 MLX 套件的實際版本/API/模型授權、Ollama 可用模型與資源需求、重連協定細節，以及使用者提及的兩張視覺參考圖（本次附件只有文字）。這些問題不影響現有架構調查，但後續對應階段必須先驗證。本文不代表後續階段已實作或驗收。
