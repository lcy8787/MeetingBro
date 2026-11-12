# Agentic Generative UI

現有 `generate_report()` 現在交由 Visual Design Agent 編排。模型 API、報告生成入口、版本儲存、逐字稿及深色主題維持原有流程；本次不涉及 PDF。

## 元件與資料契約

`report_contracts.py` 定義 28 個公開元件名稱及 KnowledgeGraph、ArgumentMap、DecisionTree、RiskMatrix、Heatmap、SankeyDiagram、InteractiveChart，亦接受原有 14 個語義名稱。`reports/components.py` 提供元件用途、資料欄位及模型用 JSON Schema。React 與獨立 HTML 以相同資料契約渲染，並共用 `reports/design.css`；不載入圖表 CDN 或執行模型程式碼。

- 一般元件使用 `text`、`detail`、有來源的 `options`。
- 比較／規格／數據表使用 `columns`、`rows`；每行有獨立來源。
- 數值圖使用 `data`：`label`、原文數值字串 `value`、`unit`、`series`、`evidence_quote`、`source_segment_ids`。
- 關係圖使用 `nodes`、`edges`。每條邊包含端點、`relation`、原文關係標籤、逐字證據及來源。Sankey 邊另有數值和單位。
- 矩陣使用 `cells`：原文 `row`、`column`、`value`、`unit`、逐字證據及來源。未提及的格子顯示 `—`，不補零。

圖表可選取資料點／節點查看來源，數據系列可切換；另備可展開的完整數據／關係及來源。Sankey 使用按數值比例計算的帶狀連接，中間節點的已述流入／流出必須守恆；不生成未提及的分配或總計標籤。風險矩陣只展示原文明確提供的分類，不替會議計算風險分數。

## Layout DSL

每章可附 `layout`，引用同章 `blocks` 的零起始索引。相同資料可組成不同展示區塊，毋須新增前端元件。例如：

```json
{
  "op": "grid",
  "columns": 2,
  "children": [
    {"op": "block", "block_index": 1, "span": 8, "emphasis": "strong"},
    {"op": "stack", "span": 4, "children": [
      {"op": "block", "block_index": 0},
      {"op": "block", "block_index": 2, "emphasis": "quiet"}
    ]}
  ]
}
```

允許 `stack`、12 軌 `grid`、2–4 欄 `split`、`tabs`、`accordion` 和 `block`。每個節點的 `span` 為 1–12、強調程度為 `quiet/normal/strong`。每個區塊必須剛好出現一次；深度不超過 3、總節點不超過 40；禁止互動容器互相巢狀及在其中再放 TabGroup／Accordion。窄畫面自動改為單欄，寬圖表只在自己的區域內捲動。

省略佈局時，Layout Composer 按元件語義、資訊長度和層級編排。跨逐字稿批次合併／去重後會重新編排，避免區塊索引漂移。極長的合併主題超出 DSL 預算時，使用既有順序 renderer 保留全部內容。

以上例子省略了本地資料模型的預設欄位；傳送至支援 strict schema 的模型時，由完整 JSON Schema 要求所有欄位。

## Agent 工具循環

沿用現有 `client.chat(system, user, response_schema)`，以 `tool_request` 表達受控工具呼叫，不更改供應商 API 或要求原生 function calling：

| 工具 | 用途 |
| --- | --- |
| `list_components` | 按 `query` 查詢元件與資料欄位 |
| `compose_layout` | 為候選報告補上佈局並驗證引用 |
| `validate_structure` | 驗證 JSON、資料型態、引用、數值和視覺化證據 |
| `review_design` | 審查及修正安全可修的版面問題 |
| `revise_section` | 以單一替代章節修改最後有效候選報告的 `section_index` |

工具參數固定為 `{name, query, section_index}`；工具結果會連同原始逐字稿供下一回合使用，不累積無界限歷史。每個逐字稿批次最多 5 次模型回合，只有模型明確請求工具才會追加呼叫。最後須輸出完整報告及 `tool_request: null`。最終輸出無效會失敗，不隱藏付費重試，也不把來源逐字稿冒充生成報告。

Design Review 會檢查重複卡片／文字、巢狀深度、資訊密度、狹窄的密集區塊、圖表適用性及過多主要強調。可安全修正的寬度、過深佈局與強調層級會直接修正；需語義判斷的項目供 Agent 修改。結果保存在 `document.design_review`。

## 來源及安全邊界

所有新視覺資料項目都必須引用同一會議的逐字稿，並包含在父區塊的來源內；兩個儲存入口都檢查新欄位，避免跨會議引用。圖表值必須是來源中可直接找到的有限數值及單位，不接受換算、估計、NaN、無限大或推導百分比。同一數據點的逐字證據不得夾帶另一項數值。折線須提供至少三個依原文次序排列的觀察及完整次序證據；沒有足夠依據時應選文字或表格。講者引文保留原話且講者 ID 必須與來源 metadata 一致；決策和行動要求原文明確承諾的證據。

JSON 額外欄位、任意樣式、事件處理器、HTML 執行節點及未知 DSL 操作都會被拒絕。文字只經 React 文字節點或 HTML escape 輸出；匯出 HTML 的 CSP 僅允許雜湊匹配的內建互動腳本。模型不能提供自己的 JavaScript。

這些檢查能阻止缺失來源、數值捏造、單位不符及多種錯配，但不能在程式層面證明所有自然語言的語義蘊含。模型仍須遵守不推斷因果、共識、比較結果或講者觀點的規則；讀者可直接核對逐字證據。中文口述數字若無原文阿拉伯數字，使用 KeyMetrics／文字保留原文，不自行轉成圖表數值。

## 驗證

```sh
PYTHONPATH=app/backend app/backend/.venv/bin/python -m pytest tests/test_agentic_reports.py tests/test_report_generation.py tests/test_semantic_reports.py tests/test_report_storage.py tests/test_standalone_report_interactions.py -q
npm --prefix app/frontend run build
```

新增測試包含工具預算、查詢／編排／審查／替換章節、DSL 及程式碼注入防護、圖表與圖譜證據、講者錯配、跨會議引用、HTML escape 和完整複製內容。瀏覽器檢查使用清楚標示的合成逐字稿 fixture，不改動實際會議資料。
