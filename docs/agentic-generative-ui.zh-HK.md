# Agentic Generative UI

`generate_report()` 每批向模型請求一次帶來源的內容關係。`reports/relations.py` 定義精簡提示詞及內容格式，本機依關係選擇元件、驗證證據並產生版面。舊版區塊 JSON 及已儲存的報告仍可讀取。

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

## 每批一次與部分摘要

逐字稿按既有字數上限分批。模型回傳明確立場、事件、流程、條件、待辦或量測值，應用程式負責轉成可閱讀的元件。一般摘要使用 JSON object 模式，每批只發送一次 completion POST。請求逾時、格式不支援或輸出截斷會結束該批。

程式逐項核對引用。元件格式或來源有誤時，仍可儲存其餘通過驗證的項目，並顯示受影響的章節。整批失敗時繼續處理後續批次。`coverage=partial` 與 `missing_parts` 記錄失敗批次、起訖秒數及錯誤代碼。時間以涉及的逐字稿片段為準。桌面、HTML 與報告文字顯示缺漏，所有批次都失敗時保留上次儲存的版本。

## 來源及安全邊界

所有新視覺資料項目都必須引用同一會議的逐字稿，並包含在父區塊的來源內；兩個儲存入口都檢查新欄位，避免跨會議引用。圖表值必須是來源中可直接找到的有限數值及單位，不接受換算、估計、NaN、無限大或推導百分比。同一數據點的逐字證據不得夾帶另一項數值。折線須提供至少三個依原文次序排列的觀察及完整次序證據；沒有足夠依據時應選文字或表格。講者引文保留原話且講者 ID 必須與來源 metadata 一致；決策和行動要求原文明確承諾的證據。

JSON 額外欄位、任意樣式、事件處理器、HTML 執行節點及未知 DSL 操作都會被拒絕。文字只經 React 文字節點或 HTML escape 輸出；匯出 HTML 的 CSP 僅允許雜湊匹配的內建互動腳本。模型不能提供自己的 JavaScript。

這些檢查能阻止缺失來源、數值捏造、單位不符及多種錯配，但不能在程式層面證明所有自然語言的語義蘊含。模型仍須遵守不推斷因果、共識、比較結果或講者觀點的規則；讀者可直接核對逐字證據。中文口述數字若無原文阿拉伯數字，使用 KeyMetrics／文字保留原文，不自行轉成圖表數值。

## 驗證

```sh
PYTHONPATH=app/backend app/backend/.venv/bin/python -m pytest tests/test_relation_reports.py tests/test_agentic_reports.py tests/test_report_generation.py tests/test_semantic_reports.py tests/test_report_storage.py tests/test_standalone_report_interactions.py -q
npm --prefix app/frontend run build
```

測試涵蓋單次 HTTP 請求、逐項來源驗證、截斷或多份回答、部分摘要儲存與離線 HTML 匯出。

## 長錄音生成修復

報告的硬性結構上限與模型 JSON Schema 一致：每批最多 8 章、每章最多 8 個區塊。約 12 個區塊是編輯上的精簡目標，超過這個目標不會使整份有效報告失敗；仍逐項驗證所有內容與引用。

`reports/quantities.py` 以精確值核對阿拉伯數字、中文數字及英文口述數字，識別 `30 percent`／`30%` 等字面等值形式，以及「數以萬計」等明確概數。不把概數變成精確統計，不作四捨五入、單位換算或把列舉數字相加。中文「一度懸而未解」、「每一個」及與英文不定冠詞對應的「一個」按語法處理，與溫度、百分比及明確數量分開。

一般敘述若混有引用無法支持的數值，會略去含該數值的整句；數值不受支持的敘述標題亦會略去。其餘文字必須重新通過來源驗證，並在 `design_review` 及可見提示留下記錄。不補寫替代數字或猜測其他來源，也不把無效圖表、決策或引文靜默改成有效資料。整個區塊沒有可保留的有效正文時，仍會失敗。

`max_model_calls` 可在請求前確認預算足以涵蓋所有批次。

這次驗證以受影響的實際長錄音進行，限定總共三次 OpenRouter 請求。後續修正、完整生成驗證及產生摘要端點測試均重播已保存的回應，沒有額外模型呼叫；完整端點回傳 200，並成功儲存摘要版本及在桌面程式顯示。
