# MeetingBro ASR 基線報告 (baseline)

> 由 `scripts/benchmark_accuracy.py` 生成。僅正式通道(formal Whisper),已禁用預覽(preview)與摘要,
> 確保結果可重現。後續優化 issue (#2-#9) 須引用此基線給出前/後對比。

## 配置

- Whisper model size: `small`
- device: `cpu`  compute type: `int8`  beam size: `3`
- runtime profile: `balanced`  (formal lane only, fast_preview disabled)
- manifest: `data\benchmark\manifest.json`
- label: baseline small/cpu/int8 (balanced profile)

## 準確率 (WER / CER)

| fixture | lang | metric | error_rate | kw recall | vocab | asr_rtf | dur (s) | seg |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| zh_fast1 | zh | cer | 0.500 | — | off | 0.47 | 11.4 | 7 |
| zh_raokouling | zh | cer | 0.586 | — | off | 0.37 | 20.8 | 7 |
| zh_qiqiu1 | zh | cer | 0.789 | — | off | 0.00 | 51.0 | 8 |
| en_f1_noise | en | wer | 0.290 | — | off | 0.97 | 19.0 | 6 |
| en_rap1 | en | wer | 0.952 | — | off | 0.76 | 29.0 | 1 |
| de_de | de | wer | 0.000 | — | off | 0.22 | 6.7 | 2 |
| mixed_codeswitch | mixed | wer | 0.786 | — | off | 0.49 | 6.2 | 4 |

### 按語言聚合

| lang | metric | mean | n |
| --- | --- | --- | --- |
| de | wer | 0.000 | 1 |
| en | wer | 0.621 | 2 |
| mixed | wer | 0.786 | 1 |
| zh | cer | 0.625 | 3 |

## 端到端字幕延遲 (formal lane)

源文件: `sample_en.wav` (循環鋪到 ≥60s),即時回放。延遲 = 音訊結束 → 字幕發出。

| p50 (s) | p95 (s) | max (s) | segments | asr_rtf |
| --- | --- | --- | --- | --- |
| 8.04 | 13.81 | 16.24 | 10 | 0.36 |

## 結果解讀 (重要)

- **這些 fixture 是對抗性壓力樣本**(背景噪音 / 配樂說唱 / 歌唱 / 繞口令 / 多語 code-switch),
  **不代表乾淨會議語音**。絕對錯誤率偏高是預期的;唯一的乾淨樣本 `de_de` (WER 0.14) 才是
  乾淨語音的現實參考。本基線的價值在於**相對比較**(優化前/後),而非絕對達標線。
- 離線評分關閉了音訊輸入隊列上限(`audio_input_queue_max_seconds`),否則非即時回放會衝垮 8s
  隊列、丟掉長片段的開頭,污染準確率。生產即時路徑不受影響。
- 正式通道字幕延遲為多秒級:連續無停頓語音會讓 pre-VAD 把片段累積到上限才發出。**亞秒級**
  即時字幕由預覽(preview)通道提供,需單獨度量(見 #6/#13)。`sample_en.wav` 為循環鋪長。

## 說明

- WER 用於 Latin 語言(en/de + code-switch),CER 用於 CJK(zh);見 `scripts/asr_metrics.py`。
- 此基線為**正式通道**質量;預覽(preview)延遲與質量另行度量(見 #6/#13)。
- ground truth 提交於 `data/benchmark/manifest.json`;音訊隨 Qwen3 模型下載,gitignored。
- 重現: `python scripts/benchmark_accuracy.py`(MeetingBro conda 環境)。
