# LTF Sweep — TradingView

`strategies/ltf_sweep.py` 移植到 Pine Script v6。
15 分鐘流動性池 → 1 分鐘掃針 → 1 分鐘 CHoCH → FVG 限價進場 → 固定 1:1。

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

邏輯核心兩份**逐字元相同**（189 行），只有結尾的下單/發警報不同。
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
| `ltf_sweep_strategy.pine` | 15.8 KB | 完整版，桌機用、看註解 |
| `ltf_sweep_strategy_compact.pine` | 12.8 KB | **手機貼這個** |
| `ltf_sweep_indicator.pine` | 15.5 KB | 完整版 |
| `ltf_sweep_indicator_compact.pine` | 12.5 KB | **手機貼這個** |

精簡版由 `scripts/make_compact_pine.py` 產生，
`tests/test_tradingview_assets.py` 會驗證兩者的程式碼逐行一致 ——
改了本體忘記重新產生，測試就會失敗。

**貼完務必檢查最後一行是不是完整的** `text_color = color.white, text_size = size.tiny)`。

## 安裝

1. TradingView → Pine Editor → 貼上 → Save → Add to chart
2. 圖表設成 **MNQ1!、1 分鐘**
3. 圖表時區建議設 `America/New_York`（腳本內部自己換算，不設也不影響邏輯）

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
{"strategy":"ltf_sweep","action":"{0}","symbol":"{1}","qty":{2},
 "entry":{3},"stop":{4},"target":{5},"risk_points":{6},"time":"{7}"}
```

| placeholder | 內容 | 範例 |
|---|---|---|
| `{0}` | 動作 | `buy` / `sell` / `exit_stop` / `exit_target` / `close` |
| `{1}` | 商品代碼 | `MNQ1!` |
| `{2}` | 口數（已依風險算好） | `14` |
| `{3}` | 進場價 | `30277.00` |
| `{4}` | 停損價 | `30294.00` |
| `{5}` | 停利價 | `30260.00` |
| `{6}` | 風險點數 | `17.00` |
| `{7}` | UTC ISO 時間 | `2026-05-28T15:23:00Z` |

換平台（TradersPost、自建服務等）只要改那一行模板，邏輯不用動。
`strategy` 版的 `action` 只有 `buy`/`sell`/`exit`/`close`；
`indicator` 版會細分成 `exit_stop` / `exit_target`。

## 怎麼驗證移植是對的

`docs/ltf_signals_recent.csv` 是 Python 回測近三個月的每一筆訊號
（67 筆、55 筆成交）。重現方式：

```bash
uv run python scripts/export_signals.py --months 3
```

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
