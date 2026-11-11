# 第二、三階段實作進度

## 已加入

- 桌面 Settings 可選 `Qwen3-ASR 0.6B (MLX)`，亦可設定 `MEETINGBRO_WHISPER_ENGINE=qwen3-mlx`。Apple Silicon 上 auto 會在套件和模型目錄存在時優先選用 Qwen；否則選 Whisper，亦可明確指定 Whisper。預設本機模型目錄為專案 `models/Qwen3-ASR-0.6B-4bit`，可用 `MEETINGBRO_QWEN3_MLX_MODEL` 指定。缺少套件、模型或記憶體時顯示載入錯誤，切回 Whisper 可恢復。
- 透過 `mlx-qwen3-asr` 0.4.4 的 `Session` 重用模型及 tokenizer。選此引擎時停用跨引擎預覽；同引擎 partial 重用正式模型及單一 ASR worker，預覽使用穩定 ID 與遞增 revision。暫停、來源切換或正式提交後會丟棄過時推理結果；partial 不會經這條共享預覽路徑提升為正式字幕。
- Ollama 設定 `http://127.0.0.1:11434/v1`、已安裝模型名稱及空 API key 可連線；新桌面設定及範例預設本機 Ollama，模型名稱只是候選。即時與最終摘要可獨立開關。Settings 可刷新預設本機 Ollama 模型列表、選擇模型及測試連線；其他 provider 和 heuristic fallback 保留。
- 最終摘要讀取 SQLite 中完整正式逐字稿；長逐字稿按組更新會議記憶，再交原有 finalization。此做法仍可能在壓縮時丟失資訊，必須實測。

## 安裝及啟用

在 Apple Silicon 上，先安裝 MeetingBro backend，再執行 `./scripts/install_qwen3_mlx.sh` 以安裝套件及下載模型。預設模型使用 [moona3k 0.6B 量化模型](https://huggingface.co/moona3k/mlx-qwen3-asr-0.6b-4bit)，需要 mlx-qwen3-asr 0.4.3 或更新版本；目前鎖定 0.4.4。重啟 backend 後 Auto 可選擇本機模型，亦可在 Settings 明確選用 Qwen3 MLX。套件和模型均為 Apache-2.0；本次依要求沒有執行載入或辨識測試。

啟動 Ollama 並自行安裝所需模型，在 Settings 選 Ollama、刷新本機模型列表、選模型並測試，然後儲存；外部啟動的 backend 需要手動重啟。preset 的模型名稱只是例子，不代表已安裝或已證明適合 8GB。

## 尚未完成或驗證

- 本機已安裝 mlx-qwen3-asr 0.4.4、MLX 0.32.4，並下載 moona3k 的 0.6B 量化模型。依要求尚未進行模型載入或辨識測試；粵語辨識、模型相容性、4-bit 與 fp16 比較及 M2 8GB 記憶體／延遲仍未驗證。Ollama 尚未安裝。
- Qwen 同引擎 partial 已有 ID、revision、status 及 sequence；正式結果完整覆蓋預覽窗口時沿用 ID 並遞增 revision，其他切段仍會建立新的 ID。stable prefix、所有切段情況下的 ID 對應與 ASR 排隊壓力保證仍未實作。短暫 WebSocket 斷線可在 45 秒內使用該次錄音的隨機會話令牌重連同一錄音，並以快照及事件序號同步；超時會停止錄音，後端程序重啟不能續錄。此機制已用模擬連線測試，尚未完成真實裝置及瀏覽器驗收。暫停會先排空已排隊音訊和 VAD／ASR 緩衝；恢復後重新開始緩衝。此行為已用模擬音訊及 VAD 測試，實際裝置與慢推理情況仍需驗證。一般停止會排空已排隊音訊；現有 stop 仍有逾時限制，不能保證慢推理時尾段和 final summary 必然完成。Qwen 的 confidence 是相容現有 schema 的固定值，並非模型信心分數。
- 即時摘要、翻譯、最終摘要及手動報告生成共用單一推理位置；在 ASR 忙碌、音訊積壓或系統記憶體低於門檻時延後新工作。可選即時優先、平衡及錄音後摘要；已開始的推理或一批次內的多次模型呼叫不會被中斷，記憶體讀數不可用時亦無法依記憶體門檻保護。這些策略尚未完成 M2 8GB 的實機量測。長會議摘要仍未證明完整覆蓋。最終摘要已有持久工作佇列、重啟恢復和失敗重試，仍需真實 Ollama 測試。現有 rolling、memory、board 和 heuristic fallback 仍沿用。15 分鐘錄音驗收未做。
- 已有初步報告資料、即時報告及離線 HTML；完整結構化生成、互動、真實瀏覽器和三類會議驗收仍未完成。

本次以 mock 測模型 session 重用、Ollama 設定及資源排程，並執行現有 backend 測試與前端 build；這些測試不能代替實機驗收。
