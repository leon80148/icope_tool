# hpdcs.hpa.gov.tw ICOPE 查詢整合 — 協定側錄文件

國健署「成人預防保健暨慢性疾病防治資訊系統」(hpdcs.hpa.gov.tw) 的登入與
「新版長者功能評估—個案身分證檢核」查詢協定。Phase 0 實地側錄（2026-08，以一家基層診所自己的帳號操作）。

> **不含任何帳號、密碼、身分證、姓名**。所有 URL query 與個資已去識別化為 `...`。

---

## 一、登入（JSON API，非 postback）

登入頁 `GET /index.aspx` 的登入 modal 由「服務登入」按鈕叫出。登入不走 ASP.NET
postback，而是 jQuery `Login()` 函式送出 AJAX：

- **`POST /Login.ashx`**，`Content-Type: application/x-www-form-urlencoded`，欄位：
  - `sAcc` = 帳號
  - `sPwd` = 密碼
  - `sValidateCode` = 驗證碼（**5 碼純數字**，彩色＋雜訊線）
- 驗證碼圖：`GET /ValidateCode.aspx?t=...`（同 session；`refreshIMG()` 換新圖）
  - 格式為 **GIF**（`Content-Type: image/Gif`，非 PNG），約 8KB，彩色數字＋雜訊線
  - **OCR 實測（ddddocr 1.6.1）：12/12 = 100% 命中**（一次性容器抓真圖辨識，人工對照全對）
- 回應：JSON `{ Result, Msg }`
  - `Result === "1"` → 成功（前端 `location.href = "/Default.aspx"`）
  - `Result === "0"` → 失敗，`Msg` 為訊息文字（`refreshIMG()` 換驗證碼）
- 登入**不需要** `__VIEWSTATE` / `__EVENTVALIDATION` / CSRF

### 後端登入流程
1. `GET /index.aspx` → 取得 session cookie（ASP.NET session 為 HttpOnly）＋ F5 WAF cookie `TS*`
2. `GET /ValidateCode.aspx` → 驗證碼 PNG（同 session）
3. OCR（ddddocr）辨識 5 碼 → `sValidateCode`
4. `POST /Login.ashx` {sAcc, sPwd, sValidateCode} → `{Result, Msg}`
   - `Result=="1"` → 成功
   - `Result=="0"` → 依 `Msg` 分類：**含「驗證碼」→ 驗證碼錯（可重試）；否則視為帳密/其他錯誤 → 停止並停用自動登入（default-closed，防鎖帳號）**
5. **⚠️ 登入成功後必須先 `GET /Default.aspx`** 建立 session context（模擬瀏覽器登入成功的
   `location.href=/Default.aspx`）。**否則直接進查詢頁會被伺服器 302 導回 `/Default.aspx` 首頁、
   拿不到查詢表單**（實測踩雷點；`_establish_context`）。查詢頁 GET 若無 `#ContentPlaceHolder1_TBPID`
   即視為 context 未建立/過期，自動重登重試。
6. session 逾時 **30 分鐘**（無操作倒數自動登出；任何請求重置）

> **驗證碼錯 Msg 已實證**（假帳號＋4 碼錯驗證碼隔離探測）：
> `{"Result":"0","Msg":"驗證碼錯誤！<br/>"}` — Msg 含「驗證碼」，default-closed 前提成立。
> （回應 header 是 `text/html` 但 body 為合法 JSON，`resp.json()` 可解析。）
> 帳密錯 Msg 刻意未觸發（避免真帳號鎖定）；分類邏輯：Msg 含「驗證碼」→驗證碼錯（重試）；
> 其餘一律視為帳密/其他錯 →停止＋停用自動登入。

---

## 二、ICOPE 個案身分證檢核（標準 ASP.NET postback）

登入後：左側選單「長者功能評估」→「新增ICOPE評估資料」→「個案身分證檢核」。

### 查詢頁 URL（依年度計畫）
`/EardlyFunction_V2/EFA_{PLAN}/EF2_CheckIDExist.aspx`（"Eardly" 為官方拼字）

| PLAN 段 | 計畫 | 說明 |
|---------|------|------|
| `EFA_114` | 114 年度 | 去年（2025），非「今年」範圍 |
| `EFA_115` | 115 年度 | **今年（2026）** |
| `EFA_Pilot_115` | 115 年度**試辦計畫** | **今年（2026）** |

（以 115 為例。程式依今天的民國年推導 `EFA_{年}`／`EFA_Pilot_{年}`，命名規則只寫在 `services/hpdcs/plans.py` 的 `PlanCode`。）

> **「今年是否做過」需同時查 `EFA_115` 與 `EFA_Pilot_115` 兩頁**；任一頁「已被登錄」即算今年已做。
> （側錄時該診所同時參與這兩個計畫；實際要查哪些計畫在「設定 › 國健署帳號」選擇。）

### 表單機制
- `form1` POST 到自己（同頁），method post
- 身分證輸入：`ctl00$ContentPlaceHolder1$TBPID`（id `ContentPlaceHolder1_TBPID`，maxlen 10）
- 檢查鈕（submit）：`ctl00$ContentPlaceHolder1$BtnCheck`（id `ContentPlaceHolder1_BtnCheck`，value「檢查」）
- 必帶 hidden：`__EVENTTARGET __EVENTARGUMENT __VIEWSTATE __VIEWSTATEGENERATOR`
  `__VIEWSTATEENCRYPTED __EVENTVALIDATION ctl00$HFcsrf ctl00$ContentPlaceHolder1$HFPID`
  `ctl00$ContentPlaceHolder1$HFOrgCode ...`（後端做法：GET 後抓**所有** `input[type=hidden]` 一併回送）

> **查詢頁 GET 回來沒有表單、但仍是登入中的首頁**，有兩種可能：context 沒建立（一.5），或這個計畫的頁面不存在
> （新年度尚未開放、代碼命名改了）。client 用一個階梯分辨（HTTP 404 直接判定不存在；其他計畫有回答就視為不存在；
> 否則 GET 一次 Default.aspx 重建 context 再試；看到登入頁才沿用共用 cookie、最後才登入一次），
> 找不到的計畫以 `unavailable` 回報，全部找不到才丟 `PlanUnavailable`。國健署對不存在頁面的實際回應**尚未側錄**，見第四節。

### 後端查詢流程（單一年度頁）
1. `GET .../EFA_{PLAN}/EF2_CheckIDExist.aspx` → bs4 解析所有 hidden input
2. `POST` 同頁：所有 hidden ＋ `ctl00$ContentPlaceHolder1$TBPID`=身分證 ＋ `ctl00$ContentPlaceHolder1$BtnCheck`=檢查
3. 解析回應 HTML 的 `#ContentPlaceHolder1_Lmsg1`

### 結果解析（實地側錄，三頁結構一致）
結果渲染到兩個固定 span：
- `#ContentPlaceHolder1_Lmsg1` — 主訊息（**解析目標**）
- `#ContentPlaceHolder1_Lmsg2` — 原住民狀態（`原住民：✕` 非原民 / 推測 `○` 原民）

| status | `#ContentPlaceHolder1_Lmsg1` 內容 | 計入今年已做? |
|--------|-----------------------------------|:---:|
| `done` | `今年可以繼續評估：X，該身分證已被登錄！[ICOPE評估表]` | ✓ |
| `done_other` | `今年無法繼續評估：X，該身分證已被其它計畫登錄！` | ✓ |
| `can_assess` | `今年可以繼續評估：O，可以繼續評估！` | ✗ |
| `blocked` | `今年無法繼續評估：X，<其他原因>！`（如資格不符） | ✗ |

**判定規則（順序，越具體越前）**：
1. Lmsg1 含「**該身分證已被登錄**」→ `done`（本計畫已登錄）
2. 含「**已被其它計畫登錄**」→ `done_other`（今年已在另一計畫做；注意「已被登錄」不是其子字串，因中間卡「其它計畫」，故須獨立判斷）
3. 含「**可以繼續評估**」（是「可以」非「無法」）→ `can_assess`（可做）
4. 含「**無法繼續評估**」其他原因 → `blocked`（不可做、非已做）
5. Lmsg1 為空 → 疑似 session 中途失效 → `SessionExpired`（自癒重登）
6. 皆不符 → `LayoutChanged`（default-closed，回報而非猜測）

`done_this_year = 任一計畫 done 或 done_other`。
⚠️ **語意注意**：曾見「115 正式=可評估、115 試辦=已被其它計畫登錄」的組合（該身分證在某計畫已登錄，
但正式 115 仍顯示可評估）。目前採「安全預設」：只要任一計畫顯示已登錄（含其它計畫）即視為今年已做，
避免重複篩檢。若診所要的語意不同（例如只認正式計畫），調整 `IcopeResult.done_this_year` 即可。

「[ICOPE評估表]」連結 → `EF2_Case.aspx?...`（評估表頁，query 含 case id）。
**評估日期在該頁內**——目前需求（是否做過／能否做）由 Lmsg1 即可回答，日期為次要，先不抓。

---

## 三、後端整合摘要（給 hpdcs_client.py）

- 「今年是否做過」= 對 `EFA_115` 與 `EFA_Pilot_115` 各查一次，任一「已被登錄」即 `done_this_year=True`，
  並回報命中的計畫（115 正式／115 試辦）
- session 重用：登入一次（30 分內）可連續查詢；逾時→自動重登一次
- 憑證：存在資料存放位置的 `auth/`（由程式注入路徑，不讀環境變數）；驗證碼 ddddocr 自動辨識，多次失敗→把驗證碼圖交給使用者人工輸入
- cookie：requests.Session 自動管理（HttpOnly session + F5 `TS*`）

---

## 四、每年 1 月的檢查清單（新年度）

1. **程式不用改**：計畫代碼由 `plans.py` 的 `PlanCode` 依民國年推導，1 月 1 日自動變成新年度；設定頁的標籤也會跟著換。
   還沒有側錄過的是：國健署何時開放新年度的頁面、不存在的頁面長什麼樣。
2. **1 月第一個上班日**：用瀏覽器登入 hpdcs，左側選單「長者功能評估」→「新增ICOPE評估資料」→「個案身分證檢核」，
   記下實際的 URL（`EFA_116`？`EFA_Pilot_116`？還有別的計畫？）。把選單那段 HTML 去識別化存成 `tests/fixtures/hpdcs/menu.html`
   （將來要做「自動偵測可用計畫」就靠它）。
3. **在程式裡查一位長者**：正常就結束。若顯示「國健署系統找不到這些計畫的查詢頁」或某個計畫是「找不到查詢頁」：
   用瀏覽器開該 URL，按 F12 → Network 記下 **HTTP 狀態碼、是否 302 與導向目標（query 是否含 `aspxerrorpath=`）、
   最終頁的 `<title>`、頁面是否含 `#username`／`#IMGValidate`／`#ContentPlaceHolder1_TBPID`**；
   去識別化存成 `tests/fixtures/hpdcs/plan_missing_<mode>.html`，對照 `client._fetch_check_page` 的分類與
   `tests/test_hpdcs_client.py` 的 `FakeSession(unknown_plans=...)` 三種模式（404／導回首頁／導回登入頁），不符就修分類。
   特別是「導回登入頁」：目前分不出缺頁與 session 失效，最多重登一次就放棄。
4. **命名改了**（例如不再叫 `EFA_`）：先在「設定 › 國健署帳號 › 進階：自訂計畫代碼」填新代碼讓診所能查，再改 `PlanCode._TEMPLATES` 發新版。
5. **去年的頁面 1 月仍在**（`EFA_115` 在 2027 年還能開），不能拿它當「今年」；`done_this_year` 只看今年度的計畫。

