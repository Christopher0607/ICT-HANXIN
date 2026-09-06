# ICT-HANXIN

把 ICT / SMC 的概念寫成**可執行、可測試**的程式碼，然後在 16 年的 NQ 期貨資料上
誠實回測。

對應學習清單第 2 點：**「看完後至少回測一套交易系統最近兩年」**。

```bash
uv venv && uv pip install -e ".[dev]"
uv run python scripts/ingest.py          # 合併原始 parquet + 驗證
uv run pytest                            # 155 個測試
uv run python scripts/compare_strategies.py --split      # 七套 + 組合帳戶
uv run python scripts/compare_strategies.py --hindsight-bars 1   # 前視敏感度
```

## 結論先講

七套策略，同樣期間，一天一張單，固定每筆風險 $500。**全部虧錢。**

📊 **[完整視覺化報告（權益曲線 + 全部數據）](https://claude.ai/code/artifact/97822a07-7616-42a1-b874-5a404d67f460)**

| 策略 | 開發 2016–2024 | 樣本外 2024–2026 |
|---|---|---|
| po3_judas | +$3,832 (PF 1.26) | −$1,159 (PF 0.85) |
| silver_bullet | −$180,752 (PF 0.68) | −$45,782 (PF 0.74) |
| turtle_soup | −$47,762 (PF 0.75) | −$18,194 (PF 0.76) |
| ob_retest | −$181,086 (PF 0.70) | −$85,988 (PF 0.67) |
| breaker_retest | −$126,135 (PF 0.77) | −$68,738 (PF 0.73) |
| ote_retracement | −$488,427 (PF 0.39) | −$353,924 (PF 0.28) |
| sfp_reversal | −$430,414 (PF 0.51) | −$102,529 (PF 0.64) |
| **組合帳戶** | **−$393,706 (PF 0.55)** | **−$83,929 (PF 0.69)** |

樣本外沒有一套獲利因子到 1.0。最好的 po3_judas 八年 83 筆交易賺 $3,832，不是一門生意。

### 最重要的發現：一根 K 棒的後見之明值三十萬美金

第一次跑出來的數字很漂亮 —— sfp_reversal 樣本外 +$197,038（PF 2.24）、
ob_retest +$71,935、組合帳戶 +$63,628。**全部是假的。**

每個偵測器把 `confirmed_at` 設成錨定 K 棒的**開盤**時間，但這些型態都要讀那根 K 的
**收盤**（掃針要收回、結構突破要收破、FVG 要第三根收完）。於是「確認後才下的單」
可以被同一根 5 分鐘 K 內部的 1 分鐘 K 成交，用一個還沒印出來的收盤價。

症狀是 sfp_reversal 的**成交率 100%**。修正後：

| | 修正前（偷看一根 K） | 修正後 |
|---|---|---|
| sfp_reversal 樣本外 | +$197,038 (PF 2.24) | **−$102,529 (PF 0.64)** |
| ob_retest 樣本外 | +$71,935 (PF 1.36) | **−$85,988 (PF 0.67)** |
| 組合帳戶樣本外 | +$63,628 (PF 1.28) | **−$83,929 (PF 0.69)** |

**原本的無前視測試沒抓到**，因為它驗的是*自洽*（截斷重跑要一致）。
一個每個事件都早一根 K 的偵測器，截斷重跑完全一致 —— 它一致地錯。
`tests/test_confirmation_timing.py` 現在驗性質本身：確認時間必須落在 K 棒收盤。

這個 bug 已經變成永久的診斷工具：

```bash
uv run python scripts/compare_strategies.py --hindsight-bars 1
```

任何新策略都可以直接問：這個優勢有多少是靠看到下一根 K？在 N=1 就崩掉的，
本來就沒在量別的東西。

### 關於「避免過擬合」

測七套挑最好的，本身就是過擬合 —— 七套零優勢策略裡最好的那套，
在開發期看起來也會不錯。所以做了 **best-of-N 虛無檢定**：
把每套策略的方向隨機化（設置、進場、停損停利距離全保留，只擲硬幣決定多空），
跑 50 輪，每輪取七套的最大值，得到「七套雜訊的贏家」分布。

兩個期間 p 值都是 0.000：真實的 po3_judas 贏過那條分布。
**所以方向判斷確實帶有資訊 —— 但它還是虧錢。比擲硬幣好，不代表比不交易好。**

*檢定的限制*：比較用總金額，沒按交易次數正規化。po3_judas 只有 83 筆、
sfp_reversal 有 1,761 筆，隨機化後前者虧最少，「七套最大值」幾乎永遠是它，
所以這實際上退化成「po3_judas 對上自己的隨機版本」。結論仍成立，但不是公平擂台。

### 七套其實只有兩三個想法

訊號日重疊（樣本外）：ob_retest、breaker_retest、ote_retracement、sfp_reversal
四套共用 **97–100%** 的交易日。組合帳戶用「全天最早觸發的那張單」，
結果 po3_judas、silver_bullet、turtle_soup 樣本外**一筆都沒下到** ——
它們發訊號較晚，永遠被搶先，而搶先的正是虧最多的那幾套。
「最早訊號優先」本身就是一條很差的選擇規則。

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
strategies/     base.py (共用機制) · registry.py (七套預先登記)
                po3_judas · silver_bullet · turtle_soup · ob_retest
                breaker_retest · ote_retracement · sfp_reversal
backtest/       engine.py (1 分鐘解析 + 固定風險部位) · metrics.py
                controls.py (隨機方向 + best-of-N) · portfolio.py
tests/          155 個測試，含 test_no_lookahead.py 與
                test_confirmation_timing.py（抓前一個測試抓不到的那類 bug）
docs/           curriculum.md (40 堂課 + 30 策略) · glossary.md (術語→函式)
```

## 文件

- **[docs/curriculum.md](docs/curriculum.md)** — 土哥 40 堂課 + 30 支策略影片，
  全部連結 + 每堂課對應的程式碼。可以當學習進度表用。
- **[docs/glossary.md](docs/glossary.md)** — ICT 術語 → 實作函式對照。
- **[docs/DATABENTO_README.md](docs/DATABENTO_README.md)** — 原始資料說明（隨資料附上）。
- **[docs/strategy_report.html](docs/strategy_report.html)** — 七套策略完整視覺化報告
  （[線上版](https://claude.ai/code/artifact/97822a07-7616-42a1-b874-5a404d67f460)）。

## 常用指令

```bash
# 七套策略 + 組合帳戶，開發期 vs 樣本外
uv run python scripts/compare_strategies.py --split

# 加上 best-of-N 虛無檢定（慢，約 40 分鐘）
uv run python scripts/compare_strategies.py --null-runs 50 --json results.json

# 前視敏感度：訂單提早 N 根 K 生效，量「偷看」值多少錢
uv run python scripts/compare_strategies.py --hindsight-bars 1

# 改風險預算
uv run python scripts/compare_strategies.py --risk 250

# 單看 PO3（第一階段的腳本，含逐年拆解與隨機對照組）
uv run python scripts/run_backtest.py --split --by-year --control
```

## 免責

回測不是未來績效。這個 repo 的價值在於**方法**——概念如何變成可測的程式碼、
如何避免前視偏誤、如何在自我欺騙之前先驗證資料。上面那組負面結果正是
這套方法運作正常的證據。

期貨交易風險極高，多數人虧錢。
