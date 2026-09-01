# ICT-HANXIN

把 ICT / SMC 的概念寫成**可執行、可測試**的程式碼，然後在 16 年的 NQ 期貨資料上
誠實回測。

對應學習清單第 2 點：**「看完後至少回測一套交易系統最近兩年」**。

```bash
uv venv && uv pip install -e ".[dev]"
uv run python scripts/ingest.py          # 合併原始 parquet + 驗證
uv run pytest                            # 99 個測試
uv run python scripts/run_backtest.py --split --by-year --control
```

## 結論先講

第一套編碼的模型是 **PO3 / Judas Swing**（第 12 堂課）。結果誠實報告如下：

| | 開發期 2016–2023 | 樣本外 2024–2026 |
|---|---|---|
| 訊號 / 成交 | 129 / 88 | 51 / 37 |
| 勝率 | 60.2% | 48.6% |
| 平均 R | +0.221 | +0.063 |
| 淨損益 | **+$383** | **−$3,158** |
| 獲利因子 | 1.010 | 0.882 |
| 最大回撤 | −$9,186 | −$6,112 |

**這套策略在扣掉成本後沒有可交易的優勢。** 開發期基本打平（獲利因子 1.01，
88 筆交易賺 $383），樣本外是虧的。蒙地卡羅重排 2000 次：**虧損機率 100%**。

逐年看更清楚 —— 2020 賺 $4,564，2022 虧 $7,247，其餘年份都是雜訊。
一套靠單一年份撐起來的策略不是策略。

### 但方向判斷本身是有資訊的

這是比「沒用」更有價值的發現。控制組實驗：**同樣的設置、同樣的進場價、
同樣的停損停利距離，只把方向隨機化**：

| | 策略 | 隨機方向 (200 次中位數) |
|---|---|---|
| 開發期損益 | +$383 | −$26,100 |
| 樣本外損益 | −$3,158 | −$26,374 |
| 勝率 | 60.2% / 48.6% | 34.7% / 30.2% |

策略在兩個期間都落在隨機方向分布的 **~95 百分位**。

也就是說：PO3 對「掃了哪一邊之後價格會往哪走」的判斷**確實帶有真實資訊**
（勝率 60% vs 隨機 35%），問題出在**風險幾何**——停損放在猶大極值外，
中位數風險 69 點（每口 $1,380），優勢被停損距離和成本吃光了。

這指向具體的改進方向（更緊的停損、更好的進場位），而不是放棄這個模型。

## 資料

`data/raw/` 是你用 Databento API 拉的原始資料（詳見 `docs/DATABENTO_README.md`）。

| | 期間 | 筆數 |
|---|---|---|
| 1 分鐘 OHLCV | 2010-06 → 2026-08 | 4,806,377 |
| 5 分鐘 + EMA12 + ATR14 | 同上 | 989,324 |

`scripts/ingest.py` 合併分片並做三重驗證：時間單調遞增、無重複、
OHLC 內部一致。另外**從合併後的序列重算 `ema12` / `atr14`，
和原檔比對** —— 兩者在 989,311 根 K 上最大誤差 **0.0000000000**，
同時證明了合併順序和指標實作都正確。

### 一個必須知道的資料限制

亞洲時段（20:00–00:00 ET）每天應該有 48 根 5 分鐘 K。實際覆蓋率：

| 年份 | 覆蓋率 |
|---|---|
| 2010–2012 | **34% – 47%** |
| 2013–2015 | 96% – 97% |
| 2016 以後 | ~100% |

Databento 的 ohlcv schema 在沒有成交的區間**不會產生 K 棒**，
早年 NQ 隔夜盤太薄。所以 2016 年以前算出來的「亞洲區間高低點」
是從三分之一的資料取的，和現在的不可比。

**開發期因此從 2016 開始**，不是 2010。這不是調參，是排除撐不住計算的資料。
用 `ict.sessions.session_coverage()` 可以自己驗。

## 兩條讓回測不說謊的規矩

### 1. `confirmed_at` — 因果契約

每個偵測器回傳 `ts`（型態在圖上的位置）和 `confirmed_at`（最早可知的時間）。
n=2 的擺動高點要等 2 根後才成立；三根 K 的 FVG 要第三根收盤才看得到；
訂單塊錨定在過去，但要等位移完成才認得出來。

回測引擎**只吃 `confirmed_at <= 現在` 的事件**。

`tests/test_no_lookahead.py` 對 14 個偵測器逐一強制驗證：
用截斷到第 i 根的資料重跑，結果必須和完整資料跑完再過濾**完全一致**。
偷看未來的偵測器過不了這關。

### 2. 訊號走 5 分鐘，成交走 1 分鐘

5 分鐘 K 內部無法判斷先碰停損還是先碰停利。常見的預設（假設先到停利）
會把整批虧損交易變成獲利交易。降到 1 分鐘可以解決絕大多數情況 ——
**本次回測 137 筆交易，1 分鐘解析度消掉了全部歧義（ambiguous = 0）**，
所以上面的數字不是成交假設造成的。

真的無解時（同一根 1m 內兩邊都碰），引擎會標記並可以用
`--ambiguity optimistic` 跑出另一端，當作穩健性區間。

成本也照收：每口來回 $4 手續費、停損出場滑 1 tick。

## 結構

```
ict/            概念庫 — 純函式，可獨立測試
  events.py       confirmed_at 契約（先讀這個）
  data.py         載入、UTC→ET、CME 交易日（18:00 ET 換日）
  sessions.py     killzone、時段區間、覆蓋率檢查
  swings.py       分形擺動點        structure.py   MSB / BOS / CHoCH
  fvg.py          FVG / BPR         liquidity.py   掃針 / SFP / 等高低
  levels.py       溢價折價 / OTE    orderblocks.py OB / Breaker / Mitigation
  patterns.py     QML / FTR / TT3   po3.py         累積-操縱-派發
strategies/     po3_judas.py
backtest/       engine.py (1 分鐘解析) · metrics.py · controls.py
tests/          99 個測試，含 test_no_lookahead.py
docs/           curriculum.md (40 堂課 + 30 策略) · glossary.md (術語→函式)
```

## 文件

- **[docs/curriculum.md](docs/curriculum.md)** — 土哥 40 堂課 + 30 支策略影片，
  全部連結 + 每堂課對應的程式碼。可以當學習進度表用。
- **[docs/glossary.md](docs/glossary.md)** — ICT 術語 → 實作函式對照。
- **[docs/DATABENTO_README.md](docs/DATABENTO_README.md)** — 原始資料說明（隨資料附上）。

## 常用指令

```bash
# 最近兩年（清單第 2 點的要求）
uv run python scripts/run_backtest.py --start 2024-09-01

# 開發期 vs 樣本外，逐年拆解，加控制組
uv run python scripts/run_backtest.py --split --by-year --control --monte-carlo

# 換參數（注意：在樣本外調參就不叫樣本外了）
uv run python scripts/run_backtest.py --target-mode fixed_r --target-r 3 --entry-level far

# 導出逐筆交易，人工抽查
uv run python scripts/run_backtest.py --start 2024-09-01 --csv trades.csv
```

## 免責

回測不是未來績效。這個 repo 的價值在於**方法**——概念如何變成可測的程式碼、
如何避免前視偏誤、如何在自我欺騙之前先驗證資料。上面那組負面結果正是
這套方法運作正常的證據。

期貨交易風險極高，多數人虧錢。
