# Quant Harbor — How to Run Backtests & Add Strategies

更新时间（PT）：2026-02-24

这份文档解决三件事：
1) **如何回测每个策略**（可直接复制粘贴的 command + 参数说明）
2) **如何实现一个新策略**（要实现的接口/文件/注册步骤）
3) **代码结构说明**（每个关键文件是做什么的）

> 约定：命令默认在项目根目录执行：`~/Desktop/trader/quant_harbor/`

---

## 0) 快速开始（先把环境对齐）

### 0.1 进入项目与虚拟环境
```bash
cd ~/Desktop/trader/quant_harbor
# 如果你已经有 .venv 就直接用下面的 python/streamlit
.venv/bin/python -V
```

---

## 1) 如何回测每个策略（命令 + 参数）

Quant Harbor 有两层“回测/评估”命令：

- **单次回测（看曲线/交易点）**：`cli_backtest.py`
- **Production 评估闭环（FreezeA：WFA fixed-params → 冻结参数 → 最后12个月 Test → scorecard）**：`cli_freezeA.py`

> 建议比较策略优劣时，一律用 `cli_freezeA.py`（因为它会产出 scorecard，可横向比较）。

### 1.1 单次回测（任意策略）

**模板：**
```bash
.venv/bin/python -m quant_harbor.cli_backtest \
  --strategy <strategy_id> \
  --symbol QQQ \
  --years 5
```

- `--strategy`：策略 id（见 registry）
- `--years`：拉取历史长度（年）
- `--params`：可选，JSON 字符串，覆盖策略参数（默认空=用策略类默认值）


### 1.2 FreezeA（默认：用于挑选“可交易”的固定参数）

**模板：**
```bash
.venv/bin/python -m quant_harbor.cli_freezeA \
  --strategy <strategy_id> \
  --symbols <symbols_csv> \
  --years 5 \
  --train-months 12 \
  --oos-months 3 \
  --min-pos-window-rate 0.7
```

说明：
- `--symbols`：单标的用 `QQQ`；pairs 用 `QQQ,SPY`（逗号分隔）
- `--min-pos-window-rate`：默认 0.7；如果没有任何候选满足会 fallback（会写入 final_report.json）

输出位置：
- `results/freezeA_<strategy_id>_<SYMBOLS>_<timestamp>/`
  - `scorecard.json`（dashboard 主榜读取）
  - `final_report.json`
  - `test/summary.json + test/trades.parquet + test/equity.parquet`
  - `wfa_windows.parquet` / `wfa_summary.json`


### 1.3 五个策略的具体命令（默认参数网格）

> 注意：pairs 策略默认需要 2 个 symbols。

#### A) RSI2 MR
单次回测：
```bash
.venv/bin/python -m quant_harbor.cli_backtest --strategy rsi2 --symbol QQQ --years 5
```
FreezeA：
```bash
.venv/bin/python -m quant_harbor.cli_freezeA --strategy rsi2 --symbols QQQ --years 5
```

#### B) Bollinger MR
单次回测：
```bash
.venv/bin/python -m quant_harbor.cli_backtest --strategy bollinger_mr --symbol QQQ --years 5
```
FreezeA：
```bash
.venv/bin/python -m quant_harbor.cli_freezeA --strategy bollinger_mr --symbols QQQ --years 5
```

#### C) Z-score MR
单次回测：
```bash
.venv/bin/python -m quant_harbor.cli_backtest --strategy zscore_mr --symbol QQQ --years 5
```
FreezeA：
```bash
.venv/bin/python -m quant_harbor.cli_freezeA --strategy zscore_mr --symbols QQQ --years 5
```

#### D) VWAP deviation MR
单次回测：
```bash
.venv/bin/python -m quant_harbor.cli_backtest --strategy vwap_mr --symbol QQQ --years 5
```
FreezeA：
```bash
.venv/bin/python -m quant_harbor.cli_freezeA --strategy vwap_mr --symbols QQQ --years 5
```

#### E) Pairs MR (ratio z-score)
单次回测：
```bash
.venv/bin/python -m quant_harbor.cli_backtest --strategy pairs_mr --symbols QQQ,SPY --years 5
```
FreezeA：
```bash
.venv/bin/python -m quant_harbor.cli_freezeA --strategy pairs_mr --symbols QQQ,SPY --years 5
```


### 1.4 WFA（Diagnostics：每窗重新选参）

用途：诊断“策略是否需要频繁调参才能活”。不作为 Production 主榜排名依据。

```bash
.venv/bin/python -m quant_harbor.cli_wfa_retune \
  --strategy rsi2 \
  --symbols QQQ \
  --years 5
```

---

## 2) 如何实现一个新策略（接口/文件/注册步骤）

目标：新增策略时，**只写策略文件 + 注册**，不修改回测/评估系统。

### 2.1 你需要新增/修改哪些文件？

1) 新增策略文件：
- 放到：`src/quant_harbor/strategies/<your_strategy>.py`
- 必须定义一个 Backtrader Strategy 类（继承 `bt.Strategy`）

2) 在 registry 注册：
- 修改：`src/quant_harbor/strategies/registry.py`
- 添加一个 `StrategySpec`：
  - `id`（字符串唯一）
  - `name`
  - `cls`（策略类）
  - `n_legs`（需要几条 data feed；单标的=1；pairs=2）
  - `default_param_grid()`（默认参数空间，用于 FreezeA/WFA 搜索）

3) （可选）如果策略需要特殊的盆地扰动（basin）：
- 优先用 `basin.make_basin_params` 的通用规则（百分比扰动/整数扰动/离散覆盖）
- 不要写策略专用 basin 逻辑（保持评估系统通用）

### 2.2 策略类需要满足什么“接口”？

Backtrader 策略只需要：
- `params = dict(...)`（策略参数，FreezeA 会用 registry 的 grid 生成）
- `__init__()`（定义指标）
- `next()`（交易逻辑：发出 buy/sell/close）

建议：
- 所有策略在 summary.json 里输出统一字段（由 backtest_runner + analyzers 负责）
- 策略本体不要写文件 IO，不要写“评估逻辑”（保持可复用）

### 2.3 n_legs（单标的 vs 多标的）

- 单标的：使用 `self.data` 或 `self.datas[0]`
- pairs：使用 `self.datas[0]` + `self.datas[1]`

数据由 runner 统一提供，snapshot 由 `make_snapshot_multi` 统一生成。

---

## 3) 代码结构：每个文件做什么

> 路径均相对 `src/quant_harbor/`

### 3.1 数据层
- `alpaca_data.py`
  - 从 Alpaca 拉取历史 bars
  - 默认 `adjustment="all"`（splits+dividends）
  - 落盘 snapshot：
    - `data/snapshots/<snapshot_id>/bars_<SYMBOL>.parquet`
    - 单标的同时写 `bars.parquet` 兼容旧脚本
    - `meta.json` 写入 feed/adjustment/bar_timestamp_semantics 等审计字段

### 3.2 回测层
- `backtest_runner.py`
  - **通用 runner**：`run_backtest_df(dfs_utc=[...], strategy_cls=..., strat_params=...)`
  - 支持多 data feeds（pairs）
  - 自动输出：summary.json / trades.parquet / equity.parquet
  - 交易成本敏感性：summary 中带 `slippage_sensitivity`

- `analyzers.py`
  - Backtrader analyzer：收集 trade list 与 equity curve

- `metrics.py`
  - 计算 PF、expectancy、win rate、分位数 PnL
  - 计算 avg_hold_bars、trades_annualized 等可实现性指标

### 3.3 切分与 WFA
- `split.py`
  - `split_train_val_test_last12m`：最后 12 个月为 Test，其余为 Pre，再在 Pre 内切 Train/Val

- `walk_forward.py`
  - 生成滚动窗口（12m train / 3m OOS，按季度滚动）

### 3.4 评估层（Gate / Basin / Scorecard）
- `gates.py`
  - hard gate 评估（MaxDD、年化 trades、可实现性门槛等）

- `basin.py`
  - 参数盆地（parameter basin）通用扰动网格生成

- `stats.py`
  - MCP（多重比较）相关：DSR（Deflated Sharpe Ratio）近似实现

- `scorecard.py`
  - Scorecard v1 打分（Robustness/Risk/ReturnQ/Implementability）

### 3.5 CLI（你日常会用）
- `cli_backtest.py`
  - 任意策略单次回测

- `cli_freezeA.py`
  - 任意策略 FreezeA 评估闭环（fixed-params WFA → freeze → Test → scorecard）

- `cli_wfa_retune.py`
  - Diagnostics：每窗重新选参的 WFA

（旧版 RSI2 专用脚本仍在：`cli_rsi2_*`，后续可逐步弃用）

### 3.6 Dashboard
- `dashboard/app.py`
  - Streamlit dashboard
  - Details(v2) 可以画：
    - WFA OOS bar chart
    - Test candles（支持 TradingView-like lightweight-charts）+ buy/sell markers

- `dashboard/tv_chart.py`
  - TradingView Lightweight Charts HTML 组件

- `dashboard/utils.py`
  - 发现 runs/scorecards，加载 parquet/json，读取 snapshot bars

---

## 4) Dashboard 怎么启动（本机/局域网）

本机：
```bash
cd ~/Desktop/trader/quant_harbor
.venv/bin/streamlit run src/quant_harbor/dashboard/app.py
```

局域网手机访问：
```bash
.venv/bin/streamlit run src/quant_harbor/dashboard/app.py --server.address 0.0.0.0 --server.port 8501
```
然后手机打开：`http://<你的Mac局域网IP>:8501`

---

## 5) 常见问题

### 5.1 为什么 FreezeA 很慢？
默认参数网格会把每个候选参数 θ 在多个 OOS 窗口都跑一遍，再跑 Test；这是机构式稳健性验证的代价。

### 5.2 如何缩小参数网格？
- 改 registry 的 `default_param_grid()`
- 或在命令里传 `--grid-json '{...}'` 覆盖

---

完。
