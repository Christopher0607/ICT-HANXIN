# LTF Sweep — TradingView

`strategies/ltf_sweep.py` 移植到 Pine Script v6。
15 分鐘流動性池 → 1 分鐘掃針 → 1 分鐘 CHoCH → FVG 限價進場 → 固定 1:1。

---

## ⚠ 接 prop firm 之前先讀這一段

**Topstep 在 Live Funded Account 禁止透過 ProjectX API 自動交易，而且禁用 VPS / VPN / 遠端伺服器。**
Apex 的來源互相矛盾，最清楚的說法同樣是「評估可以、funded 禁止」。

也就是說：**你能自動化的是評估階段，賺錢那一段要手動。**

我查到的都是二手評測站（不少帶聯盟行銷），2026 年內政策改過好幾次。
**付錢或上線之前，直接跟客服要書面確認**，Combine 和 Funded 分開問。
本專案的 `bridge/` 預設 `--dry-run`，就是為了讓你在拿到答覆前也能先跑通。

---

## 先讀這三件事

**一、這套策略十年半累計是虧的。**
NQ 2016–2026 淨損益 −$38,787，2016–2022 有六年虧損，2023 之後才轉正。
把 10.7 年切成所有的三個月視窗，**中位數獲利因子只有 0.89、只有 37% 的視窗獲利**，
而當前視窗落在第 **99** 百分位。這既可能是 2023 後市場真的變了，
也可能只是十年半裡最好的那一段而我們正在看它。
資料本身分不出來 —— 只有 2026-08-28 之後的新 K 棒能分。
接自動化在統計上還太早。

**二、TradingView 的資料不是回測用的資料。**
研究用的是 Databento GLBX.MDP3，TV 是另一個數據源，時段處理和成交模型也不同。
Strategy Tester 的數字**不會**和回測吻合，而且兩邊都不是「真相」。
TV 當即時訊號引擎用，不要當驗證工具。

**三、把停利拉遠沒有用。**
1:1.5 和 1:2 都測過，都更差；0.5 到 3.0 整條曲線單調下降。
這個模型的優勢在命中率，不在讓獲利奔跑。

---

## 兩份腳本，用哪一份

| | `ltf_sweep_strategy.pine` | `ltf_sweep_indicator.pine` |
|---|---|---|
| 用途 | 看 Strategy Tester、驗證邏輯 | **接自動化** |
| 下單 | `strategy.entry/exit`（走 broker emulator） | 不下單，只發 `alert()` |
| 損益報表 | 有 | 無 |
| 成交判定 | TradingView 的模擬器決定 | 腳本自己判定，可控 |

邏輯核心兩份**逐字元相同**（232 行），只有結尾的下單/發警報不同。
先用 strategy 版確認訊號長得對，再換 indicator 版接自動化。

## 腳本刻意維持純 ASCII

兩個 `.pine` 檔案裡**沒有任何非 ASCII 字元** —— 沒有框線 `-`、沒有破折號、
沒有間隔點。這不是風格潔癖，是因為這些檔案唯一的交付路徑是**複製貼上進
Pine Editor**，而那條路徑會把 UTF-8 搞壞。

實際踩過的坑：標題字串裡一個破折號 `—`（UTF-8 `E2 80 94`）被當成 cp1252 讀時
變成 `â€"`，最後那個字元是右彎引號，**把字串常值提前關閉**，
於是整串參數解析錯亂，而 TradingView 回報的錯誤位置在四行之外的 `grpS` ——
完全看不出真正的病灶在哪。

`tests/test_tradingview_assets.py` 會擋下任何非 ASCII 字元。改腳本時請維持純 ASCII。

## 手機貼上請用 `_compact` 版

實測過一次：17 KB 的檔案在手機剪貼簿被截斷，編輯器裡最後一行停在
`table.cell(info, 1, 6, "$" + st`，然後報 `Missing closing parenthesis` ——
錯誤指向最後一行，看不出是傳輸問題。

所以每份腳本都有一個精簡版，**程式碼逐行相同**，只是拿掉了說明性註解：

| 檔案 | 大小 | 用途 |
|---|---|---|
| `ltf_sweep_strategy.pine` | 27.6 KB | 完整版，桌機用、看註解 |
| `ltf_sweep_strategy_compact.pine` | 18.2 KB | **手機貼這個** |
| `ltf_sweep_indicator.pine` | 26.0 KB | 完整版 |
| `ltf_sweep_indicator_compact.pine` | 18.1 KB | **手機貼這個** |

精簡版由 `scripts/make_compact_pine.py` 產生：拿掉說明性註解，
把換行的續行折回單行（Pine 自己的規則是「續行的縮排不是 4 的倍數」，
所以折得回去），並拿掉 input 的 `tooltip`（那也是說明文字，執行時沒人讀）。
`tests/test_tradingview_assets.py` 會驗證精簡版和本體**是同一支程式**
（正規化後逐 token 相同）。改了本體忘記重新產生，測試就會失敗。

**尺寸變大了。** 加上 prop firm 守衛層之後精簡版從 14.6 KB 長到 18.2 KB，
超過了當初被手機剪貼簿截斷過的 16.8 KB。我把測試的上限從 16 KB 調到 19 KB，
這是刻意的取捨而不是偷偷放寬：把攸關資金安全的程式碼壓縮去遷就一個
近似值不划算，而且**貼壞是會叫的** —— 當初那次是編譯錯誤
（`Missing closing parenthesis`），不是靜悄悄跑一支半截的腳本。
真正的防線是下面那句「檢查最後一行」，不是那個數字。

**貼完務必檢查最後一行是不是完整的** `text_color = color.white, text_size = size.tiny)`。

## 安裝

1. TradingView → Pine Editor → 貼上 → Save → Add to chart
2. 圖表設成 **MNQ1!、1 分鐘**（**必須是 1 分鐘**，見下面的疑難排解）
3. 圖表時區建議設 `America/New_York`（腳本內部自己換算，不設也不影響邏輯）
4. 加上去之後看右上角的診斷面板 —— 它會告訴你腳本實際看到了什麼

## 疑難排解：圖上有畫東西，但 Strategy Tester 一筆成交都沒有

這個症狀踩過，原因不只一個，而且**全部長得一模一樣**：
圖照畫、不報錯、成交表全空。所以腳本自己會數，右上角面板的四行診斷是：

| 面板那行 | 意思 | 這行是 0 代表 |
|---|---|---|
| `chart` | 商品 + 圖表週期 | 顯示 `NEEDS 1` → **圖表不是 1 分鐘** |
| `sess/swp/chc` | session 內 K 棒 / 掃針次數 / CHoCH 次數 | 第一格 0 → session 或時區不對；第二格 0 → 池子沒被掃到 |
| `conf/armed/rej` | 確認 / 下單 / 被風控擋掉 | 中間 0 而前面不是 0 → 訊號成立但算不出口數 |
| `FILLED` | 實際成交筆數 | **`armed` 不是 0 但這裡是 0 → 券商模擬器在丟單** |

打開 `debugLog` 之後，Pine Logs 分頁會有逐筆時間軸（掃針 → 下單 → 成交 → 出場），
比面板更細。

### 最容易中的兩個

**一、圖表週期不是 1 分鐘。**
這個模型是「15 分鐘的池子，被一根 1 分鐘 K 掃掉，再由 1 分鐘 CHoCH 確認」。
在 5 分鐘或 15 分鐘圖上那個結構根本不存在，一個 session 內幾乎跑不完 ——
**零成交是預期結果，不是 bug**。腳本現在會在圖上畫紅色警告。

**二、保證金。**
口數是**固定金額風險**算的：$500 ÷ 停損點數 ÷ $2。
17 點的停損 = 14 口 MNQ ≈ **$840,000 名目**。
初始資金 $25,000 配上任何非零的保證金要求，
**每一張單都會因為資金不足被靜默拒絕**。
所以 `strategy()` 裡現在明確寫死 `margin_long = 0, margin_short = 0`，
初始資金也提到 $100,000。
這兩個值**不影響任何訊號、口數或損益**，只是讓模擬器別擋單 ——
不是為了讓數字好看調的參數。

如果你自己改了 Properties 分頁裡的 Margin 欄位，記得改回 0。

## 怎麼確認哪一版在跑

**圖表左上角的圖例會顯示版本號**：`LTF Sweep v5`。
看到版本號就是新版，只看到 `LTF Sweep` 就是舊的還掛著 ——
不用開面板、不用翻 log，你截圖的時候本來就會拍到。

`debugLog` 打開的話，Pine Logs 第一行也會印 `LTF Sweep v5`。

貼上新版之後**一定要 Ctrl+S 存檔**，不存檔圖表不會重算。
圖表上如果掛了兩個 LTF Sweep 實例，把舊的移除。

### 面板 `rej` 後面的 `(+N late)`

`conf/armed/rej` 那格的 `(+N late)` 是**價格已經走過 FVG 而被拒絕**的次數。

模型的前提是「等回撤到 FVG 中點進場」。CHoCH 確認時價格如果已經在
FVG 的另一邊，那張限價單就是立即可成交的 —— 它會用市價成交，
而不是用當初算停損的那個價位，變成一筆頂著這個設置編號的不同交易。

研究引擎（`BacktestConfig.skip_marketable_entries`）拒絕這些，Pine 也拒絕。
**兩邊必須一致，否則會在 5% 的訊號上分歧**，而那 5% 在開發期的勝率只有 21%。

這個數字不是 0 是正常的。

### 同一個進場價出現兩次 = bug

v4 之前有一個會產生這種結果的錯誤：一筆交易出場的那根 K 棒上，
`strategy.position_size` 已經歸零而 `stage` 還是 2，
於是下單區塊**把同一張限價單原價重新掛了出去**，
而且掛完之後 `stage` 才歸零 —— 那張單變成**孤兒**，
唯一會取消它的路徑要求 `stage == 2`，所以沒有任何東西會清掉它。
它就一直躺在委託簿裡等價格碰到，可能是兩分鐘後，
也可能是**幾小時後、在交易時段之外**。

v4 把「上一張單怎麼了」的判定移到下單之前，並在重置時一併取消殘留訂單。

如果你在 v5 上還是看到**兩筆進場價完全相同**、
或**時段外成交**的交易，回報給我。

## TradingView 的數字和回測對不上時

先分清楚**哪些差異是設定造成的、哪些才是真的不一致**。

### 一定會差的：手續費

TradingView 預設手續費是 0，研究引擎收 **$1.24/口來回**（MNQ 實際費率）。
所以同一筆交易 TV 顯示的是毛利，回測顯示的是淨利。

**Properties → Commission 設成 `0.62` USD per contract** ——
研究引擎的 $1.24 是來回，TradingView 按每張單計，進出各一張，所以每邊 0.62。

不改的話每口每筆會多出 $1.24 的差，12 口就是 $14.88。

### 不會差的：圖表時區

圖表時區只影響**你讀到的時間**，不影響訊號 ——
腳本內部一律用 `America/New_York` 判定 session。

對照 `docs/forward_log.csv` 時記得換算。例如圖表設 UTC+8（台北／北京）時：

| 圖表顯示 | 實際 ET |
|---|---|
| Sep 2, 22:05 | Sep 2, 10:05 |
| Sep 4, 00:58 | Sep 3, 12:58 |

夏令時是 UTC−4，所以 UTC+8 的圖表比 ET 快 12 小時，**跨日要小心**。

### 怎麼對

`docs/forward_log.csv` 每一筆都有 ET 的進出場時間、價格、口數。
換算成你的圖表時區之後逐筆對：

1. **進場時間和價格** —— 對得上代表訊號引擎一致
2. **口數** —— 對不上代表停損距離算出來不一樣（回頭看 `stop`）
3. **淨利** —— 只有手續費的差，就是設定沒調

### 面板 `rej` 後面的 `(+N late)`

`conf/armed/rej` 那格的 `(+N late)` 是**價格已經走過 FVG 而被拒絕**的次數。

模型的前提是「等回撤到 FVG 中點進場」。CHoCH 確認時價格如果已經在
FVG 的另一邊，那張限價單就是立即可成交的 —— 它會用市價成交，
而不是用當初算停損的那個價位，變成一筆頂著這個設置編號的不同交易。

研究引擎（`BacktestConfig.skip_marketable_entries`）拒絕這些，Pine 也拒絕。
**兩邊必須一致，否則會在 5% 的訊號上分歧**，而那 5% 在開發期的勝率只有 21%。

這個數字不是 0 是正常的。

### 同一個進場價出現兩次 = bug，不是兩個訊號

面板 `FILLED` 那格後面的 `(n same-bar)` 是同一根 K 棒內進出場的次數。
1:1 的停利很近，這種交易是常態（前瞻記錄 5 筆裡有 2 筆），
腳本必須正確處理它們。如果你看到**兩筆交易的進場價完全相同**，
那是同一張限價單被重下了，回報給我。

## Prop firm 規則守衛層

打開 `propMode` 之後腳本會強制執行帳戶規則。**預設是關的** ——
關著的時候，下面每一條都不會改變任何一張單。

### 為什麼一定要開

`riskUSD = 500` 的樣本外最大回撤是 **$9,207**，而 Topstep 50K 的
Maximum Loss Limit 是 **$2,000**。直接接上去就是爆倉。

實測（用真實回測序列 bootstrap，$200/單，Topstep 50K）：

| | 通過 | **爆倉** | 停手（帳戶還在） |
|---|---|---|---|
| 守衛關閉 | 76.6% | **23.4%** | 0% |
| **守衛開啟** | 66.7% | **0%** | 33.3% |

**守衛把 23.4% 的「帳戶沒了」換成「停止交易但帳戶還在」**，
代價是 9.9 個百分點的通過率。這個交換很划算：爆倉要重買評估重來，
停手只是那一輪沒過。

### 四條規則

| 規則 | 觸發條件 | 動作 |
|---|---|---|
| 距離損失上限太近 | 剩餘空間 < 單筆風險 x `safetyMult`(1.5) | 不開新倉 |
| 當日虧損上限 | 這筆會讓當日虧損踩到 `dailyLossLimit` | 不開新倉 |
| 已達獲利目標 | `netprofit >= profitTarget` | 停手，別回吐 |
| 超過口數上限 | `qty > maxMicros` | **減碼到上限**（不是跳過） |

前三條記在 `nBlockMLL` / `nBlockDaily` / `nBlockTgt`，第四條記 `nClampQty`，
面板最後一行看得到，`debugLog` 會寫出當下的剩餘空間和這筆的風險。

### 風險金額改由損失上限推導

`propMode` 開啟時，每單風險 = `maxLossLimit x riskPctOfMLL%`，預設 10% ——
Topstep 50K 就是 **$200**。這不是拍腦袋的數字，是掃出來的拐點：

| $風險/單 | 成交 / 跳過 | 最大回撤 | 通過率 / 時間 |
|---|---|---|---|
| $100 | 429 / 258 | −$962 | 96% / 21 個月 |
| **$200** | **537 / 150** | **−$2,467** | **77% / 6.8 個月** |
| $300 | 550 / 137 | −$5,180 | 54% / 3.3 個月 |
| $500 | 550 / 137 | −$9,207 | 47% / 1.2 個月 |

低於 $150 會因為湊不滿 1 口而跳掉大量訊號（$100 那行跳了 250 個），
頻率塌掉、時間拖到快兩年。用「上限的百分比」而不是固定金額，
換帳戶尺寸的時候會自己跟著調。

### 預設值的可信度

**Topstep 那三行是照 Topstep 自己的規則頁填的。**
Apex 兩行來自 4.0 改版後的第三方整理，**我沒有查證過** ——
用之前自己對一次，或直接選 `Custom` 自己填。

| 預設 | 目標 | 損失上限 | 當日上限 | 微型口數 |
|---|---|---|---|---|
| Topstep 50K | 3,000 | 2,000 | 1,000 | 50 |
| Topstep 100K | 6,000 | 3,000 | 2,000 | 100 |
| Topstep 150K | 9,000 | 4,500 | 3,000 | 150 |
| Apex 50K EOD ⚠ | 3,000 | 2,500 | 無 | 20 |
| Apex 100K EOD ⚠ | 6,000 | 3,000 | 無 | 40 |

Apex 沒有當日虧損上限，表裡那格填的是一個大到不會觸發的數 ——
填 0 會讓每一筆都被擋掉。

## 參數對照表

改參數時兩邊要一起改，否則 Pine 和回測就對不起來了。

| Pine input | `LTFSweepConfig` 欄位 | 預設 |
|---|---|---|
| `lookbackCandles` 15m candles forming the pool | `lookback_candles` | 4 |
| `minPen` Minimum penetration | `min_penetration` | 0.25 |
| `minFvgSize` Minimum FVG size | `min_fvg_size` | 0.5 |
| `confirmWithin` CHoCH must follow within N bars | `confirm_within_bars` | 30 |
| `pivotLen` Swing pivot length | `swing_n` | 2 |
| `oneTradePerDay` | `one_per_day` 引數 | true |
| `stopBuffer` Stop buffer beyond extreme | `stop_buffer_points` | 1.0 |
| `targetR` Target (R multiple) | `target_r` | 1.0 |
| `minRiskPts` / `maxRiskPts` | `min_risk_points` / `max_risk_points` | 1.0 / 120.0 |
| `riskUSD` Risk per trade | `BacktestConfig.risk_per_trade_usd` | 500 |
| `pointValue` $ per point | tick 0.25 × `TICK_VALUE_MNQ` 0.5 = **2.0** | 2.0 |
| `sweepWindow` Sweeps accepted | `session_start` – `session_end` | 0930-1500 |
| `entryWindow` Limit order live | `entry_deadline` | 0930-1530 |
| `tradeSession` Session / flat by | `exit_minute` | 0930-1600 |

`showDiag`（診斷計數器）和 `debugLog`（Pine Logs 追蹤）也沒有對應欄位，
兩者純粹是排查用的，不影響任何訊號。確認一切正常之後可以把 `debugLog` 關掉。

`tz` 和 `showPool` / `showFvg` / `showLevels` / `showTable` 沒有對應欄位 ——
前者是時區（預設 `America/New_York`，對應 Python 的 `zoneinfo` 換算），
後四個純粹是畫面開關，不影響任何訊號。

換 NQ 大合約要把 `pointValue` 改成 20。注意：$500 風險下停利距離超過 25 點
就算不出 1 口，而實測中位數停損是 31.5 點 —— 大部分設置會被跳過。

## 警報設定

**必須選 Once Per Bar Close。**

掃針（影線穿出、收盤回到池內）、CHoCH（收盤突破擺動點）、FVG（第三根收盤）
三者的判定**全都讀當根 K 的收盤價**。用 Once Per Bar 或 On Every Tick 的話，
盤中價格還在動，訊號會反覆成立又消失，然後你會收到一串自相矛盾的警報。

建立方式：Alerts → Condition 選腳本 → Trigger 選 **Once Per Bar Close**
→ Message 留空（腳本用 `alert_message` / `alert()` 自己帶內容）
→ Webhook URL 填你的端點。

## Webhook JSON

預設模板（`alertTemplate` input，可直接在設定面板改）：

```json
{"strategy":"ltf_sweep","action":"%ACTION%","symbol":"%SYMBOL%","qty":%QTY%,
 "entry":%ENTRY%,"stop":%STOP%,"target":%TARGET%,"risk_points":%RISKPTS%,"time":"%TIME%"}
```

| token | 內容 | 範例 |
|---|---|---|
| `%ACTION%` | 動作 | `buy` / `sell` / `exit_stop` / `exit_target` / `close` |
| `%SYMBOL%` | 商品代碼 | `MNQ1!` |
| `%QTY%` | 口數（已依風險算好） | `14` |
| `%ENTRY%` | 進場價 | `30277.00` |
| `%STOP%` | 停損價 | `30294.00` |
| `%TARGET%` | 停利價 | `30260.00` |
| `%RISKPTS%` | 風險點數 | `17.00` |
| `%TIME%` | UTC ISO 時間 | `2026-05-28T15:23:00Z` |

換平台只要改模板那一行，邏輯不用動。
`strategy` 版的 `%ACTION%` 是 `buy`/`sell`/`exit`/`close`；
`indicator` 版會細分成 `exit_stop` / `exit_target`。

### 為什麼用 `%TOKEN%` 而不是 `str.format`

踩過的坑：`str.format()` 把 `{` `}` 當成佔位符界定符，
所以 JSON 模板會在執行期炸掉：

```
Error on bar 2406: can't parse argument number: "strategy":"ltf_sweep"
```

而且 Pine 的執行期錯誤會**中止整個腳本** —— 圖表跟著全白，
完全看不出是警報模板的問題。

Pine 可以用單引號跳脫大括號（`'{'`），但那對之後要改模板的人是地雷：
**漏一個引號，腳本就掛掉、圖表就空白**。
改用 `str.replace_all` 做具名 token 替換，沒有任何跳脫規則要記。

`tests/test_tradingview_assets.py` 會擋下 `str.format` 的回歸，
也會驗證模板裡每個 token 都真的有對應的替換 ——
少一個就會把字面的 `%TOKEN%` 送到券商去。

## 怎麼驗證移植是對的

`docs/ltf_signals_recent.csv` 是 Python 回測近四個月的每一筆訊號
（2026-05-08 → 2026-09-07，87 筆、73 筆成交），
每筆都有**到分鐘**的掃針與 CHoCH 時間。重現方式：

```bash
uv run python scripts/export_signals.py --months 4
```

最近幾筆長這樣，可以直接拿去 TV 上拉到那一分鐘對：

| date | sweep_et | choch_et | side | entry | stop | target | 結果 |
|---|---|---|---|---|---|---|---|
| 2026-08-31 | 09:35 | 10:03 | SHORT | 29428.25 | 29480.00 | 29376.50 | target |
| 2026-09-01 | 09:50 | 09:57 | SHORT | 29127.88 | 29159.00 | 29096.75 | target |
| 2026-09-02 | 09:42 | 09:50 | SHORT | 29100.50 | 29120.00 | 29081.00 | target |
| 2026-09-03 | 11:20 | 11:38 | SHORT | 29466.38 | 29543.50 | 29389.25 | stop |
| 2026-09-04 | 09:48 | 10:08 | SHORT | 29672.25 | 29692.75 | 29651.75 | 未成交 |
| 2026-09-07 | 09:35 | 09:58 | SHORT | 29620.12 | 29632.50 | 29607.75 | stop |

**近 13 個交易日（08-10 → 08-28）研究版是 15 個訊號、11 筆成交。**
1 分鐘圖上的 Strategy Tester 應該落在這個量級 ——
成交是 0 或個位數，就是設定有問題，不是模型安靜。

步驟：

1. TV 跳到 CSV 裡的某個 `date`
2. 對 `sweep_et`（掃針時間）和 `pool_level`（被掃的池位）—— 圖上該有三角形標記
3. 對 `choch_et` 和 `entry` / `stop` / `target`
4. 對右上表格的口數和 CSV 的 `planned_contracts`

**資料源不同，少數幾筆會對不起來**（例如剛好只穿透一檔的掃針，
在另一個數據源上可能沒穿透）。**幾筆不合是正常的，大面積不合就是移植錯了。**

### 非重繪實測

這是檢查 `request.security` 有沒有寫對的實地測試，比讀程式碼可靠：

1. 記下圖上最後 3 個訊號的時間和價位
2. 隔天重新載入圖表
3. **那 3 個訊號的位置不能有任何變化**

會變就是取值偷看了未來。腳本裡那兩行用的是官方文件的非重繪寫法
（`[1]` 位移搭配 `lookahead_on`，兩者互相依賴缺一不可）。

## 兩邊機制不同的地方

**同一根 1 分鐘 K 同時碰到停損和停利時。**
1 分鐘是最細的資料，這種情況無解。三邊處理不一樣：

- Python 引擎：明確標記為無法判定，同時輸出悲觀（假設停損）和樂觀兩組數字
- `indicator` 版：寫死悲觀，和 Python 的預設一致
- `strategy` 版：**TradingView 的 broker emulator 自己決定，我控制不了**

實測這類 K 佔 0.6–1.4%，影響小，但 strategy 版的損益會因此和 Python 有系統性差異。

**其他已知差異**：TV 的 1 分鐘 K 邊界和 session 定義可能與 Databento 不同；
Pine 的限價成交假設「碰到就成交」，Python 引擎可以切換成「要穿過一檔」
（`entry_fill_mode` / `exit_fill_mode`），Pine 沒有等價開關。

## 上線前

先跑 **paper / 模擬帳號**，至少一個月，比對 TV 訊號和實際成交。
在那之前不要接真錢 —— 這套策略十年半是虧的，最近三年半的獲利
還沒有被新資料驗證過。
