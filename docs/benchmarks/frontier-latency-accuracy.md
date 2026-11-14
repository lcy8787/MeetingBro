# MeetingBro 延遲 × 準確率前沿 (latency–accuracy frontier)

> 由 `scripts/benchmark_latency_accuracy_frontier.py` 生成。在**真實 AMI 會議片段**上
> 掃描分段旋鈕,對每個配置在**相同音訊**上分別測字幕延遲(即時回放)與 WER(離線評分),
> 給出可優化的 Pareto 前沿,而非兩個孤立數字。`★` 標記 Pareto 最優點。

## 配置

- fixture: `ami_en2002a` (en, AMI EN2002a [300-420s], CC BY 4.0)
- Whisper: `small` / `cpu` / `int8` / beam `3`
- runtime profile: `balanced` (formal lane only, fast_preview disabled), forced_language=`en`
- governor: ASR safeguard DISABLED (knob isolated);  runs/point: 3 (latency samples pooled across runs)
- swept: pre_vad_max_segment_seconds=8,4  asr_accumulation_seconds=2.0,1.0
- label: frontier-2026-06 hardened: safeguard off, 2x2, 3-run pooled

## 前沿點

| | pre_vad_max (s) | accum (s) | latency p50 (s) | p50 range | p95 (s) | WER | seg(acc/lat) | asr_rtf |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| • | 8 (baseline) | 2 | 3.83 | 3.75–3.87 | 9.29 | 0.582 | 66/66 | 0.45 |
| ★ | 8 | 1 | 3.76 | 3.68–3.80 | 9.29 | 0.582 | 66/66 | 0.45 |
|  | 4 | 2 | 3.98 | 3.88–4.09 | 6.98 | 0.579 | 53/53 | 0.45 |
| ★ | 4 | 1 | 3.97 | 3.91–3.98 | 6.98 | 0.579 | 53/53 | 0.47 |

## 解讀

- `★` = Pareto 最優(沒有任何其它點同時在延遲和 WER 上都不差且至少一項更好)。
- 沿 `pre_vad_max_segment_seconds` 下降,延遲應下降;若 WER 不升或僅微升,即為**免費的延遲收益**,
  說明當前 8.0s 上限對真實會議語音偏保守。若 WER 明顯惡化,則該點是 tradeoff 的拐點。
- 這是真實會議語音上的相對比較;絕對 WER 受 AMI 遠場單麥(SDM)與重疊說話影響,看趨勢而非絕對值。
