# ICT / SMC 術語 → 程式碼對照

每個術語都對應一個純函式，可以獨立測試、獨立檢查。這張表是「看影片學的概念」和
「能跑的程式」之間的橋。

## 基礎結構

| 術語 | 中文 | 定義 | 實作 |
|---|---|---|---|
| Swing High / Low | 擺動高低點 | 分形高低點：高點高於左右各 n 根 | `ict/swings.py::find_swings` |
| MSB / BOS | 結構突破 | **收盤**突破前擺動點，順勢方向 | `ict/structure.py::find_msb` (`structure="bos"`) |
| CHoCH | 性格轉變 | 突破**反向**擺動點，反轉的第一個訊號 | `ict/structure.py::find_msb` (`structure="choch"`) |

## 失衡與缺口

| 術語 | 中文 | 定義 | 實作 |
|---|---|---|---|
| FVG / IMB | 公允價值缺口 | 三根 K：`low[i] > high[i-2]`（多方） | `ict/fvg.py::find_fvgs` |
| Consequent Encroachment | 缺口中線 | FVG 的 50%，ICT 偏好的進場點 | `find_fvgs` 的 `midpoint` 欄 |
| BPR | 平衡價格區間 | 多空 FVG 重疊處 | `ict/fvg.py::find_bpr` |

## 訂單塊家族

| 術語 | 中文 | 定義 | 實作 |
|---|---|---|---|
| OB | 訂單塊 | 位移前最後一根反向 K | `ict/orderblocks.py::find_order_blocks` |
| SOB | 超級訂單塊 | 位移腿中帶 FVG 的 OB | 同上，`kind="super_order_block"` |
| Breaker | 破壞塊 | 失效的 OB，回測時極性反轉 | `ict/orderblocks.py::find_breakers` |
| Mitigation Block | 緩解塊 | 未先掃流動性就形成的 OB | `ict/orderblocks.py::find_mitigation_blocks` |
| RBR / DBD / RBD / DBR | 漲基漲等 | 同一結構的不同命名 | `find_order_blocks` |

## 流動性

| 術語 | 中文 | 定義 | 實作 |
|---|---|---|---|
| Liquidity Sweep / Raid | 掃流動性 | **影線**穿過關鍵位後收回 | `ict/liquidity.py::find_sweeps` |
| SFP | 擺動失敗型態 | 掃針後收回，與 sweep 同構 | `ict/liquidity.py::find_sfp` |
| Turtle Soup | 海龜湯 | 掃前日高低後反轉 | `find_level_sweeps` 搭配 `prior_day_levels` |
| EQH / EQL | 等高 / 等低 | 多個擺動點在容差內聚集 | `ict/liquidity.py::find_equal_levels` |
| PDH / PDL | 前日高 / 低 | 前一 **CME 交易日**極值 | `ict/sessions.py::prior_day_levels` |

> **最重要的一組區別**：*掃針* 是影線穿過後**收回**（流動性被拿走，反向訊號）；
> *突破* 是**收盤**站穩另一側（該位真的失守，順向訊號）。同一個價位，
> 相反的結論。把兩者搞混會讓策略在型態最有效的那些天做反方向。

## 價格區間

| 術語 | 中文 | 定義 | 實作 |
|---|---|---|---|
| Dealing Range | 交易區間 | 擺動低點到擺動高點 | `ict/levels.py::DealingRange` |
| Equilibrium | 均衡 | 區間 50% | `DealingRange.equilibrium` |
| Premium / Discount | 溢價 / 折價 | 均衡上方 / 下方 | `is_premium` / `is_discount` |
| OTE | 最佳進場 | 0.62–0.79 回撤帶，甜蜜點 0.705 | `DealingRange.ote_zone` |
| ROTE | 反向 OTE | 同樣比例套在延伸方向 | `DealingRange.retracement` |

## 型態

| 術語 | 中文 | 實作 |
|---|---|---|
| QML / Quasimodo | 過頭破底 | `ict/patterns.py::find_qml` |
| S/R Flip | 支撐阻力互換 | `ict/patterns.py::find_sr_flips` |
| Three Tap (TT3) | 三推 | `ict/patterns.py::find_three_taps` |
| Range + Deviation | 區間偏離 | `ict/patterns.py::find_range_deviations` |
| FTR / FTB / IC | 未回補 | `ict/patterns.py::find_ftr` |

## 時段與 PO3

| 術語 | 中文 | 定義 (紐約時間) | 實作 |
|---|---|---|---|
| Asian Range | 亞洲區間 | 20:00–00:00，累積階段 | `ict/sessions.py::KILLZONES["asian"]` |
| London KZ | 倫敦killzone | 02:00–05:00 | `KILLZONES["london"]` |
| NY AM KZ | 紐約早盤 | 07:00–10:00 | `KILLZONES["ny_am"]` |
| Silver Bullet | 銀彈時段 | 10:00–11:00 | `KILLZONES["silver_bullet"]` |
| PO3 | 三重力量 | 累積 → 操縱 → 派發 | `ict/po3.py::find_po3` |
| Judas Swing | 猶大擺動 | PO3 的操縱腿：假突破後反轉 | 同上 |

> **PO3 的方向會反轉**：掃區間**高點**是**看跌**訊號，掃低點是看漲。
> `find_po3` 回傳的 `direction` 是**預期的派發方向**，不是掃針的方向。
> 這點在 `tests/test_po3.py` 有專門的測試鎖住。

---

## 兩條貫穿全部程式碼的規矩

**1. `confirmed_at` — 因果契約**

每個偵測器回傳兩個時間：`ts`（型態在圖上的位置）和 `confirmed_at`（最早可知的時間）。
n=2 的擺動高點要 2 根後才成立；三根 K 的 FVG 要第三根收盤才看得到。
回測引擎只吃 `confirmed_at <= 現在` 的事件。

`tests/test_no_lookahead.py` 對**每一個**偵測器強制驗證：用截斷資料重跑，
結果必須和完整資料跑完再過濾一模一樣。

**2. 訊號走 5 分鐘，成交走 1 分鐘**

5 分鐘 K 內部無法判斷先碰停損還是停利。降到 1 分鐘可以消掉幾乎所有歧義；
剩下真的無解的（同一根 1m 內兩邊都碰），引擎會標記並同時輸出悲觀/樂觀兩種結果。
