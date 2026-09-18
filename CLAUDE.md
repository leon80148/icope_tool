# CLAUDE.md — icope_tool

ICOPE 小幫手（內部識別碼與 exe 名稱 `IcopeTool`）：PySide6 桌面程式（Windows），查國健署 hpdcs 的 ICOPE 年度紀錄、產生轉介衛教單。設計見 `docs/DESIGN.md`。公開在 github.com/leon80148/icope_tool（MIT）。

## 常用指令（repo 根目錄，PowerShell 或 Git Bash）

```
venv\Scripts\python -m icope_tool                       # 執行（--data-dir / --local-dir 可指定位置）
venv\Scripts\python -m pytest -q                        # 全部測試（約 90 秒，Qt offscreen）
venv\Scripts\python tools\ui_snapshots.py <out> [--size 1280x720] [--only r0]   # UI 截圖
venv\Scripts\python tools\guide_images.py [--only daily-3-print]                # 使用說明（F1）與 docs/GUIDE.md 的圖片
venv\Scripts\python tools\make_clinic_pack.py <清單.json> <PDF 資料夾> <輸出.zip>   # 院所自己的設定包
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1            # 打包
dist\IcopeTool\IcopeTool.exe --self-test <out>          # 打包後自我檢測
venv\Scripts\python toolselease_notes.py v<版本>          # 發佈前檢查：tag 與版本一致、CHANGELOG 有這一版
```

安裝依賴時 **ddddocr 一定要 `--no-deps`**：它的宣告會拉進非 headless 的 opencv-python，與 PySide6 衝突。

## 規則

- **這是公開的 repo**：追蹤的檔案、commit 訊息、截圖裡不出現院所名稱、真實帳號或任何真實個資；示範資料一律用「示範診所」與 `A123456789` 這類公認假值；commit 用 GitHub 的 noreply email（repo 層級已設定）。公開的歷史從 1.1.0 的單一 commit 開始；本機的 `backup/*` 與舊的 `feat/*` 分支含公開前的歷史，**不可 push**。
- 顯示名稱只在 `icope_tool/__init__.py` 的 `APP_DISPLAY_NAME` 定義；`APP_NAME`（`IcopeTool`）是 exe 與 `%LOCALAPPDATA%` 資料夾的名字，不能改。
- 發佈：版本號改 `__init__.py` 與 `pyproject.toml`、`CHANGELOG.md` 加一段（`tests/test_release.py` 檢查三者一致）→ 側邊欄會顯示版本號，重產含側邊欄的兩張說明圖（`guide_images.py --only setup-1-checklist`、`--only daily-1-tick-and-apply`）→ 推 main、CI 綠 → 想試跑就手動執行 release workflow（不發佈）→ 推 `v<版本>` tag。打包腳本跑在 Windows PowerShell 5.1，驗證它要用 `powershell.exe -File`，不是 pwsh 7。
- 外來的檔案都當成不可信任：設定包不採用包內檔名並限制大小；`Material.filename` 只接受單張資料夾內的 PDF 檔名（清單在共用資料夾，會被拿去開啟與刪除）。
- 國健署密碼不可出現在畫面、log、錯誤訊息、設定包；稽核只記 `pid_tag`（sha256 前 8 碼），不記身分證與姓名。
- 健保卡的出生日期只拿來在查詢結果旁核對年齡（`eligibility.age_in_year`，以年度計；`ui/messages.describe_age`）：和姓名一樣只存在記憶體（`HistoryEntry.birth_roc`），不進稽核、log 與任何檔案，而且只跟著讀卡當下的身分證走。年齡只是提醒，不改變國健署的判定與能按的按鈕。
- `data/`（含 `auth/`）永不入版控。
- 院所自己的設定包來源與成品放 `packs/`（git 忽略）：常有聯絡人姓名、手機與院所自製單張。`examples/` 會被打包發佈給所有使用者，只放公開資料。
- 使用說明的圖片（`icope_tool/resources/guide/`）只用 `tools/guide_images.py` 的示範資料產生，不拿真實資料夾截圖；改了畫面或文案就重跑並一起 commit。`help_dialog.py` 與 `docs/GUIDE.md` 共用同一批圖，是兩份各自的文字：改一邊要核對另一邊，連同圖說裡引用的數量。`tests/test_guide.py` 會檢查圖片引用，以及兩份說明引用的按鈕與訊息文字還在畫面上（改了畫面的字，要更新那份清單與兩份說明）；圖片清不清楚、編號有沒有蓋到字要自己逐張看。`docs/INSTALL.md` 會被複製進發佈資料夾，裡面不要放圖片連結。
- `QTextBrowser` 的 `line-height: 150%` 套到只有圖片的段落時，圖片下面會多出半張圖高的空白：圖片段落另外設 `line-height: 100%`（`help_dialog._figure`）。
- 登入失敗預設關閉：只有訊息含「驗證碼」才重試，其他失敗停用自動登入。改這段要保留 `test_hpdcs_client.py` 的帳號鎖定測試。
- `HpdcsClient`、`CredentialStore` 用建構子注入路徑與 provider，不讀環境變數、不用模組層單例。
- 共用資料夾：`DataStore` 只做單筆操作（重讀 → 套用 → 原子寫入）；UI 不整批覆寫清單。
- 背景工作用 `run_in_background`（`ui/workers.py`）或 `run_with_progress`（`ui/progress.py`）；`on_finished` 會在成功／失敗處理之前執行。
- **Python 循環回收只在主執行緒做**：`app.main` 呼叫 `keep_garbage_collection_on_main_thread`，測試由 `tests/conftest.py` 處理；
  任何地方不得 `gc.enable()`。背景執行緒若因回收刪除 Qt 物件，會拿著 Qt 訊號連線鎖等 GIL、與主執行緒互等而凍結
  （曾在「產生 PDF 時開啟對話框」重現）。背景工作函式與結果不要持有 Qt 物件的最後一個參照。
- 回傳結果要綁定發出時的快照（`QueryRequest`、PDF 的 `_version/_session`、單張列印的 `_Job`），不要在回呼裡讀畫面上可能已改變的狀態。
- **院所預設只在使用者按「帶入院所預設」時加入**，勾項目本身不加任何內容；`pinned`（排序）、`domains`（建議清單）、`include_by_default`（帶入）是三個概念，不要互相代替。
- `ReferralState` 新增欄位時，掃過 `set_domain`、`set_material`、`prune`、`reset`、`_bind_patient`、`has_selections／is_empty`（第 2 輪 U01 的教訓）；能設計成「只描述目前勾選的項目」就不必讓整體判斷知道它。畫面同步勾選狀態用重建列，不要呼叫 `set_material`（那代表使用者自己選的）。
- 「只印衛教單張」不讀寫轉介草稿。輸出前一律在背景重讀來源比對內容（長度與修改時間相同不算沒變）；`_busy` 由整段流程的結尾解除，不是 `on_finished`。
- 各項衛教重點的字數／行數判斷只用 `models.normalize_note`／`note_problem`；`Settings.sheet` 不進設定包。
- PDF 分頁：放得進一頁才用 `ensure_space` 要求整塊同頁（`usable_height()` 已扣續頁頁首）。分頁類改動要同時看文字抽取、座標邊界與 `tools/ui_snapshots.py --only p1` 的渲染圖。

## UI 慣例與踩過的坑

- 樣式寫在 `ui/theme.py` 全域 QSS，用 `set_prop(widget, "role", ...)` 切換；不要對個別元件 `setStyleSheet`（大量清單會很慢）。
- `QPushButton` 的通用 `min-height` 會壓扁組合式按鈕，所以只套在 `QPushButton[kind]`；組合式按鈕各自設 objectName 與高度。
- QSS 設了 `color` 後 placeholder 會變成文字色 50% 透明，要另外設 `placeholder-text-color`。
- 自動換行的 `QLabel` 加入版面時不要給對齊旗標（會用預設寬度估高度而被截斷）；需要靠上時對外層卡片用 `AlignTop`。
- 焦點框用 `C["focus"]`（近黑色），不要和選取狀態同色；自繪列（`ClickableRow`）外圈留 3px 畫焦點框。
- 可能超過螢幕高度的區塊用 `widgets.scroll_area` 包起來；主要動作放在捲動區外。
- 輸入欄位用 `field_label(text, buddy=widget)` 建立標題，會自動設定無障礙名稱；`test_every_input_has_an_accessible_name` 會掃描。

## 測試

- UI 測試（`tests/test_ui_flows.py`）有 autouse fixture 攔截 `QMessageBox`；新增會跳對話框的流程時，用 `dialogs.asked` 斷言、`dialogs.answer` 控制回答。真的跳出模態對話框會讓無頭測試卡住。
- 假的國健署 client（`FakeHpdcs`）的 `query_icope` 要接受 `cancel` 參數。
- 無法在本機自動驗證：真實國健署查詢與實體印表機。

## UI 審查流程

`docs/ui-review/RUBRIC.md`（Nielsen 10 原則×10 分、嚴重度 0–4、WCAG 2.2 AA 門檻）。每輪截圖放 `docs/ui-review/round-NN/`（git 忽略），分數與共識問題清單記在 `SCORES.md`。
