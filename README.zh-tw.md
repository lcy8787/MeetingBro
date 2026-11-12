<div align="right">
  <a href="README.md">English</a> | 繁體中文
</div>

# MeetingBro

**開源、以本機處理為優先的 AI 會議助理，提供即時轉錄、翻譯與會議摘要。**

MeetingBro 能擷取會議音訊，即時將語音轉成文字、翻譯不同語言的內容，並產生會議摘要，無須安裝會議平台外掛或整合平台 API。

它直接從電腦擷取音訊，因此適用於 Zoom、Teams、Google Meet、BBB 等各種會議平台。

支援平台：Windows、macOS 與 Linux。

[![授權：MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/armpro24-blip/MeetingBro/actions/workflows/ci.yml/badge.svg)](https://github.com/armpro24-blip/MeetingBro/actions/workflows/ci.yml)

---

<video src="https://github.com/user-attachments/assets/54111a42-2e80-416f-9ee5-1402a68e3cd7" autoplay loop muted playsinline width="100%"></video>

---

## MeetingBro 能做什麼？

| 功能 | 說明 |
|---|---|
| **即時轉錄** | 使用在本機執行的 Qwen3-ASR，即時將語音轉成文字 |
| **即時字幕** | 可選擇顯示中文、英文與德文的翻譯字幕 |
| **即時翻譯** | 自動進行中文、英文與德文之間的翻譯 |
| **滾動摘要** | 透過 AI 整理近期會議內容，並可自動更新；更新時機請參閱[運作原理](#運作原理) |
| **會議看板** | 持續整理會議主題、決議、待辦事項與待釐清問題 |
| **筆記匯出** | 將完整逐字稿、摘要與筆記匯出為 Markdown；成功產生摘要後，也可匯出互動式 HTML 報告 |
| **會議歷史紀錄** | 從應用程式內的歷史紀錄（🕘）重新檢視、匯出或刪除過往會議 |
| **快速即時字幕** | 可啟用串流預覽，約 1.5 秒即可顯示字幕，無須等待完整片段或額外安裝模型 |
| **系統音訊擷取** | 擷取各種會議平台的音訊；Windows 原生支援，macOS／Linux 需設定音訊回送 |
| **麥克風擷取** | 支援 Windows、macOS 與 Linux，適用於實體會議 |
| **本機或雲端語言模型** | 使用 OpenAI、Groq、Ollama 或其他相容 OpenAI API 的服務產生摘要 |

> **轉錄功能不需要 API 金鑰。** 未設定摘要模型時，仍可顯示與匯出逐字稿，但不會產生 AI 摘要或 HTML 報告。

---

## 適合誰使用？

- 希望自動整理線上課程筆記的學生
- 需要參加外語會議的工作者
- 需要多語言訪談或會議逐字稿的研究人員
- 重視隱私、希望優先在本機處理資料的使用者
- 希望擴充或開發本機會議助理的開發者

---

## 安裝前須知

**請先閱讀以下說明，再開始安裝。**

- 安裝時需要在終端機輸入少量指令，下方提供逐步操作說明。
- **Windows 的音訊擷取設定最簡單。** MeetingBro 可直接擷取系統音訊，也就是喇叭播放的聲音，因此適用於各種會議軟體。
- **macOS 與 Linux** 均支援麥克風擷取。若要擷取線上會議的系統音訊，macOS 需設定虛擬音訊回送裝置及「多重輸出裝置」；Linux 則需設定 PulseAudio／PipeWire 音訊回送。
- **錄音前請先安裝 Qwen3-ASR 模型。** 0.6B 的資源需求較低，1.7B 則著重辨識準確度；即時預覽與最終逐字稿共用所選模型。
- **提供三種執行模式**：`Summary only`（僅摘要）適合效能較低的裝置；`Balanced`（均衡）適合多數電腦；`Performance`（高效能）適合能投入更多運算資源的裝置。
- **語音辨識語言與執行模式分開設定。** 多語言會議可選擇 `Speech = Auto`；若會議主要使用單一語言，可指定該語言以改善辨識效果。
- **即時字幕使用已選取的 Qwen 模型。** 不需要另外安裝預覽模型，也不會自動改用 Whisper 或其他辨識引擎。
- **LLM API 金鑰為選用項目。** 語音轉錄不需要金鑰；AI 摘要與翻譯則需要設定可用的語言模型服務。使用本機 Ollama 時，不需要雲端 API 金鑰。

---

## 快速開始

建議先將專案下載至本機，開啟專案根目錄，再使用附帶的安裝與啟動腳本。若要自行安裝，請參閱[逐步安裝](#逐步安裝)。

**Windows（PowerShell）：**

```powershell
.\scripts\install.ps1
.\scripts\start.ps1
```

**macOS／Linux：**

```bash
chmod +x scripts/install.sh scripts/start.sh
./scripts/install.sh
./scripts/start.sh
```

安裝與啟動腳本會完成以下工作：

- 尋找 Python 3.12 以上版本並檢查 npm 是否可用；建議安裝 Node.js 20 以上版本。
- 在 `app/backend/.venv` 建立 Python 虛擬環境。
- 安裝後端及平台對應的語音辨識相依套件：Apple Silicon 使用 `.[mlx]`，其他平台使用 `.[qwen]`。
- 若尚未建立後端設定檔，將 `.env.example` 複製為 `app/backend/.env`。
- 執行 `npm install` 安裝前端相依套件。
- 啟動前端開發伺服器與 Electron 桌面應用程式，並由桌面應用程式管理後端。

**安裝完成後，仍需先安裝所選的語音模型，才能開始轉錄。** Apple Silicon 的安裝方式如下，其他平台請參閱[步驟 5](#步驟-5安裝語音模型)。

在終端機按 `Ctrl+C` 可停止啟動腳本所管理的服務。

### Apple Silicon 的 Qwen3-ASR 設定

完成後端安裝後，從專案根目錄執行以下其中一個指令：

```bash
# 0.6B：資源需求較低
./scripts/install_qwen3_mlx.sh 0.6B

# 1.7B：著重辨識準確度
./scripts/install_qwen3_mlx.sh 1.7B
```

腳本會將相容的 `moona3k/mlx-qwen3-asr-0.6b-4bit`（約 538 MB）或 `moona3k/mlx-qwen3-asr-1.7b-4bit`（約 1.2 GB）下載至各自的本機目錄。你可以只安裝需要的版本。

在 **Settings → Models → Qwen3-ASR** 選擇已安裝的 **0.6B** 或 **1.7B**，然後儲存。即時預覽與最終轉錄都會使用所選模型。Apple Silicon 使用 MLX，其他平台則使用本機 `qwen-asr` 後端；模型載入失敗時，不會在未告知的情況下切換到其他引擎。

若要指定自訂模型目錄，Apple Silicon 可設定 `MEETINGBRO_QWEN3_MLX_MODEL`，其他平台則設定 `MEETINGBRO_QWEN3_MODEL`。指定的路徑會優先於依模型大小決定的預設目錄。請在啟動前確認模型檔案已完整下載。

> **連接埠說明：** 後端與前端預設使用 **8765** 連接埠。一般使用者可保留預設設定；若自行調整後端連接埠，請同步確認前端連線設定。

### 在應用程式內完成設定

桌面應用程式提供圖形介面，日常設定不需要編輯 `.env`：

- **自動管理後端**：Electron 會啟動 Python 後端，並在退出時關閉它。啟動及載入語音模型期間，會顯示「啟動中／準備語音模型中」畫面；若失敗，則顯示錯誤並提供 **Retry**（重試）與 **View log**（檢視紀錄）。若已有後端執行中，應用程式會直接連線。
- **應用程式設定（頂端 ⚙）**：設定 **LLM API 金鑰、Base URL、模型**，並使用 **Test connection**（測試連線）確認連線；也可選擇 **Qwen3-ASR 模型大小（0.6B／1.7B）**。設定會依使用者儲存，並在沒有錄音進行時自動重新啟動後端套用。
- **音訊裝置選擇與音量表**：錄音前可選擇麥克風或系統輸出裝置，並確認即時輸入音量。主錄音畫面也會顯示輸入音量。
- **錯誤提示**：摘要產生失敗時，例如 API 金鑰遭拒，介面會提供設定入口，且不會發布替代報告。

> 應用程式內的後端啟動器使用 `app/backend/.venv`，此環境**必須使用 Python 3.12 以上版本**。若原本的虛擬環境使用較舊版本建立，請重新建立；也可以自行啟動後端，再讓桌面應用程式連線。

### Linux 音訊與桌面環境設定

```bash
# 音訊相依套件
sudo apt install portaudio19-dev libportaudio2
```

部分 Linux 系統可能需要設定 Electron 沙箱權限：

```bash
sudo chown root:root app/frontend/node_modules/electron/dist/chrome-sandbox
sudo chmod 4755 app/frontend/node_modules/electron/dist/chrome-sandbox
```

線上會議的系統音訊擷取需另行設定 PulseAudio／PipeWire 音訊回送，詳見[平台支援說明](docs/platform-support.md)。

### macOS 音訊設定

```bash
brew install portaudio
brew install --cask blackhole-2ch
```

開啟 **Audio MIDI Setup（音訊 MIDI 設定）**，建立 **Multi-Output Device（多重輸出裝置）**：

1. 按一下 `+`，選擇建立多重輸出裝置。
2. 勾選 `BlackHole 2ch`。
3. 勾選你的喇叭或耳機。
4. 在「系統設定 → 聲音 → 輸出」選擇剛建立的多重輸出裝置。
5. 在 MeetingBro 選擇 **System Audio**（系統音訊）／`loopback`。

若要確認 macOS 是否能辨識虛擬輸入裝置，可在已安裝後端套件的 Python 環境執行：

```bash
python scripts/list_audio_devices.py
```

清單中應出現類似 `BlackHole 2ch` 且 `max_in > 0` 的裝置。

會議結束後，若不再需要擷取系統音訊，可將 macOS 的聲音輸出切換回一般喇叭或耳機。

### 選擇適合的執行模式

請依電腦效能選擇執行模式：

| 模式 | 適用情境 | 運作方式 |
|---|---|---|
| **Summary only（僅摘要）** | 效能較低或較舊的裝置 | 隱藏即時逐字稿，減少即時運算，將處理資源集中於摘要與最終筆記 |
| **Balanced（均衡）** | 多數使用者 | 在反應速度、轉錄品質、字幕與摘要之間取得平衡 |
| **Performance（高效能）** | 較強的 CPU／GPU，且以英文會議為主 | 採用更積極、著重品質的即時設定，需要較多可用運算資源 |

建議從以下方式開始：

- 裝置負荷過高或逐字稿明顯落後時，選擇 **Summary only**。
- 一般電腦可先使用 **Balanced**。
- 效能充足且希望加強即時處理時，可嘗試 **Performance**。

語言設定與執行模式相互獨立：

- 多語言會議：選擇 **Speech = Auto**。
- 主要使用單一語言的會議：選擇 **Speech = English／Chinese／German**。

---

## 逐步安裝

### 環境需求

| 工具 | 版本 | 取得方式 |
|---|---|---|
| **Python** | 3.12 以上 | [python.org](https://www.python.org/downloads/) 或透過 Conda 安裝 |
| **Conda** | 較新的版本即可；僅使用下方 Conda 安裝流程時需要 | [docs.conda.io](https://docs.conda.io/en/latest/miniconda.html)，建議使用 Miniconda |
| **Node.js** | 建議 20 以上 | [nodejs.org](https://nodejs.org/) |
| **Git** | 任意近期版本 | [git-scm.com](https://git-scm.com/) |

### 步驟 1：複製專案

```bash
git clone https://github.com/armpro24-blip/MeetingBro.git
cd MeetingBro
```

### 步驟 2：建立 Python 環境

```bash
conda create -n MeetingBro python=3.12 -y
conda activate MeetingBro
```

之後每次開啟新的終端機操作後端，都需要先執行 `conda activate MeetingBro`。

### 步驟 3：安裝後端

進入後端目錄：

```bash
cd app/backend
```

依平台選擇其中一個安裝指令：

```bash
# Apple Silicon
pip install -e ".[mlx]"

# Windows／Linux／Intel Mac
pip install -e ".[qwen]"
```

**Windows 使用者**需額外安裝 WASAPI 音訊回送套件：

```bash
pip install "soundcard>=0.4"
```

這能讓 MeetingBro 擷取 Zoom、Teams 等應用程式的音訊，無須安裝平台外掛。

回到專案根目錄：

```bash
cd ../..
```

### 步驟 4：設定環境變數

將設定範本複製到後端目錄；若檔案已存在，請直接編輯現有檔案：

```bash
# macOS／Linux
cp .env.example app/backend/.env
```

```powershell
# Windows（PowerShell）
Copy-Item .env.example app/backend/.env
```

```bat
:: Windows（命令提示字元）
copy .env.example app\backend\.env
```

使用文字編輯器開啟 `app/backend/.env`。完成語音模型安裝後，可先使用預設設定進行轉錄；若需要 AI 摘要，請依下方的 [LLM 設定](#llm-設定ai-摘要)加入模型服務資訊。也可稍後在桌面應用程式內設定。

### 步驟 5：安裝語音模型

**Apple Silicon：** 若已使用快速開始的安裝腳本建立 `app/backend/.venv`，可直接執行前述的 `install_qwen3_mlx.sh`。若使用本節的 Conda 環境手動安裝，請在專案根目錄下載所需版本：

```bash
# 選擇 0.6B 或 1.7B，無須兩者都安裝
hf download moona3k/mlx-qwen3-asr-0.6b-4bit --local-dir models/Qwen3-ASR-0.6B-4bit
hf download moona3k/mlx-qwen3-asr-1.7b-4bit --local-dir models/Qwen3-ASR-1.7B-4bit
```

**Windows／Linux／Intel Mac：** 在專案根目錄下載所需的本機模型：

```bash
# 選擇 0.6B 或 1.7B，無須兩者都安裝
hf download Qwen/Qwen3-ASR-0.6B --local-dir models/Qwen3-ASR-0.6B
hf download Qwen/Qwen3-ASR-1.7B --local-dir models/Qwen3-ASR-1.7B
```

在應用程式設定中選擇已安裝的大小，或在以終端機啟動時設定 `MEETINGBRO_QWEN3_MODEL_SIZE=0.6B` 或 `1.7B`；僅接受這兩種大小。

### 步驟 6：安裝前端

```bash
cd app/frontend
npm install
cd ../..
```

---

## LLM 設定（AI 摘要）

MeetingBro 可選擇使用雲端或本機大型語言模型（LLM），提供以下功能：

- AI 會議摘要，包括滾動摘要與會議看板
- 中文、英文與德文之間的翻譯

**未設定摘要模型時：** 轉錄功能仍可正常使用，但不會發布摘要或 HTML 報告。使用本機 Ollama 則可在不提供雲端 API 金鑰的情況下產生摘要。

最簡單的設定方式是開啟應用程式內的 **Settings（⚙）**，貼上金鑰、選擇服務商預設值，再按 **Test connection**。你也可以在 `app/backend/.env` 設定以下三個變數：

```env
MEETINGBRO_LLM_API_KEY=your_api_key_here
MEETINGBRO_LLM_BASE_URL=https://api.openai.com/v1
MEETINGBRO_LLM_MODEL=gpt-4o-mini
```

支援的服務商包括 **OpenAI、Groq、Mistral AI、OpenRouter、Together AI**，以及完全在本機執行的 **Ollama**。

各服務商的完整設定方式，請參閱 [LLM 服務商指南](docs/llm-providers.md)。

> **隱私說明：** 只有文字逐字稿會傳送至你設定的 LLM 服務商；音訊擷取與 Qwen3-ASR 語音辨識均在本機進行。若語音模型與 Ollama 等語言模型皆在本機執行，會議內容就不需要傳送至雲端模型服務。

---

## 啟動 MeetingBro

使用快速開始的腳本時，桌面應用程式會自動管理後端。若要手動啟動，請先啟動後端，再啟動前端。

### 後端

從專案根目錄執行：

```bash
conda activate MeetingBro
cd app/backend
meetingbro-backend
```

正常啟動後，應看到類似以下的輸出：

```text
INFO:     Started server process
INFO:     Uvicorn running on http://127.0.0.1:8765
```

### 前端

開啟新的終端機，進入專案根目錄後執行：

```bash
cd app/frontend
npm run dev
```

Electron 視窗會自動開啟。也可透過瀏覽器存取 `http://localhost:5173`。

---

## 首次執行檢查清單

> **想先確認是否能正常運作？** 按一下開始按鈕旁的 **▶ Try sample**（試用範例），即可使用內建示範音訊測試「轉錄 → 摘要」流程，無須設定麥克風或音訊回送。摘要部分仍需設定可用的語言模型服務。

開始錄音前，請確認：

- [ ] 後端已啟動，且語音模型已準備完成
- [ ] Electron 視窗已開啟
- [ ] 已在介面選擇正確的音訊裝置
- [ ] 線上會議已選擇 **System Audio**；macOS 需 BlackHole 與多重輸出裝置，Linux 需音訊回送監聽裝置
- [ ] 實體會議已選擇麥克風

按一下 **Start Session**（開始會議），說幾句話，即可確認逐字稿面板是否出現文字。

第一次正式開會前，請先選擇適合裝置的模式：

- **Summary only**：較重視最終筆記，希望減少即時轉錄負荷。
- **Balanced**：希望使用標準設定。
- **Performance**：裝置效能充足，希望使用更積極的即時處理設定。

首次使用 Qwen3 模型時，載入可能需要 10–30 秒，實際時間依裝置與模型大小而異；後續轉錄通常會更快。

---

## 運作原理

MeetingBro 從電腦擷取音訊，再透過本機處理流程進行辨識：

```text
音訊來源（麥克風或系統音訊）
    ↓ 語音活動偵測（VAD）
    ↓ Qwen3-ASR 0.6B 或 1.7B（即時預覽與最終轉錄共用）
    ↓ 語者分離（選用）
    ↓ 翻譯（選用，透過 LLM）
    ↓ 摘要（選用，透過 LLM）
    ↓ 即時介面（逐字稿與摘要）
    ↓ 匯出（可獨立開啟的互動式 HTML，以及 Markdown／JSON）
```

Qwen3-ASR 是唯一的語音辨識引擎。錄音結束後，已設定的摘要服務會產生經驗證的結構化報告，再呈現為互動式 HTML 摘要。以 OpenRouter 為例，前往 **Settings → Models → OpenRouter**，輸入 API 金鑰與模型 ID，按 **Test connection**，再按 **Save & apply**（儲存並套用）。雲端服務會收到文字逐字稿，語音辨識仍在本機執行；未設定服務商時，不會呼叫雲端模型。

摘要支援主題導覽、可展開說明的比較與概念按鈕、依序排列的流程、可勾選的待辦事項，以及可展開的逐字稿來源。這些功能也能在匯出的獨立 HTML 中使用。勾選狀態僅保留於目前檢視期間，不會同步至其他待辦服務。

你可以在既有紀錄的摘要頁面左上方按 **Generate summary**（產生摘要），重新產生報告。**自動摘要預設關閉**；開啟 **Automatically generate summaries** 後，首次成功產生摘要前每 5 分鐘檢查一次，之後則每 10 分鐘在逐字稿有更新時產生摘要。

**停止錄音後，系統一律會在逐字稿儲存完成時排入一次最終摘要工作**，不受定期摘要開關影響。模型請求可能產生服務商費用；請求失敗時，會關閉定期摘要。

尚未成功產生模型摘要時，匯出內容僅包含 `transcript.md`、`metadata.json`，以及使用者自行撰寫的筆記（如有）；舊版離線摘錄不會顯示。成功產生摘要後，才會加入 `summary.md` 與 `meeting-report.html`。

在歷史紀錄項目上按右鍵即可刪除紀錄；刪除前會顯示無法復原的確認提示。已匯出的檔案是獨立副本，不受歷史紀錄刪除影響。

音訊擷取提供兩種模式：

- **線上模式（Windows／macOS／Linux）**：擷取系統音訊；macOS 需 BlackHole 等虛擬音訊回送裝置與多重輸出裝置。
- **離線模式（所有平台）**：擷取麥克風音訊，適合實體會議。此處的「離線」指音訊來源；是否使用雲端摘要，取決於 LLM 設定。

完整技術說明請參閱[架構文件](docs/architecture.md)。

---

## 疑難排解

常見問題與處理方式請參閱[疑難排解指南](docs/troubleshooting.md)。

可先在已安裝後端套件的 Python 環境中，從專案根目錄進行以下檢查：

1. 執行 `python scripts/dep_check.py`，確認相依套件已正確安裝。
2. 執行 `python scripts/list_audio_devices.py`，查看可用的音訊裝置。
3. 手動啟動時，確認後端已啟動，再開啟前端。

---

## 平台支援

| 平台 | 麥克風 | 系統音訊 | 說明 |
|---|---|---|---|
| Windows 10／11 | ✅ | ✅ | 完整支援 |
| macOS | ✅ | ⚠️ | 系統音訊需 BlackHole 或同類工具，以及多重輸出裝置 |
| Linux | ✅ | ⚠️ | 透過 PulseAudio／PipeWire 音訊回送擷取系統音訊，需手動設定 |

各平台的詳細步驟請參閱[平台支援說明](docs/platform-support.md)。

---

## 常見問題

請參閱[常見問題集](docs/faq.md)。

---

## 參與貢獻

開發環境設定、測試與 Pull Request 提交流程，請參閱[貢獻指南](CONTRIBUTING.md)。

---

## 文件索引

| 文件 | 內容 |
|---|---|
| [README.zh-tw.md](README.zh-tw.md) | 本文件：繁體中文專案介紹與快速開始 |
| [README.md](README.md) | 英文版專案介紹與快速開始 |
| [docs/llm-providers.md](docs/llm-providers.md) | LLM 服務商與 API 金鑰設定 |
| [docs/platform-support.md](docs/platform-support.md) | 各作業系統的音訊設定 |
| [docs/architecture.md](docs/architecture.md) | 技術架構與元件概覽 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 常見問題與解決方式 |
| [docs/faq.md](docs/faq.md) | 常見問題集 |
| [docs/roadmap.md](docs/roadmap.md) | 開發藍圖 |
| [docs/optimization-2026-06.md](docs/optimization-2026-06.md) | 2026 年 6 月準確度與延遲最佳化報告 |
| [docs/benchmarks/baseline-2026-06.md](docs/benchmarks/baseline-2026-06.md) | 可重現的 WER／CER 與延遲基準 |
| [docs/benchmarks/frontier-latency-accuracy-notes.md](docs/benchmarks/frontier-latency-accuracy-notes.md) | 延遲與準確度的取捨，以及正式轉錄流程的延遲分析 |
| [docs/benchmarks/streaming-whisper-2026-06.md](docs/benchmarks/streaming-whisper-2026-06.md) | 串流 Whisper 字幕流程的歷史效能評測 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 開發環境與貢獻指南 |
| [docs/product-principles.md](docs/product-principles.md) | 產品與工程原則 |

---

## 建議的 GitHub 主題標籤

可在 GitHub 儲存庫加入以下主題標籤，方便其他使用者找到專案：

`meeting-assistant` `speech-to-text` `live-transcription` `transcription` `ai-summary` `live-translation` `qwen3-asr` `local-ai` `offline-first` `privacy` `electron` `fastapi` `python` `multilingual` `open-source`

---

## 授權

本專案採用 MIT 授權，詳見 [LICENSE](LICENSE)。
