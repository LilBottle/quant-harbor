# Task List（按顺序执行：每次只做一个 task → 测试 → 修复 → 下一个）

## Phase 0 — 工程与依赖（一次性）
- [x] T0.1 创建 Python 虚拟环境（venv）并锁定依赖（requirements.txt / pyproject）
- [x] T0.2 项目配置：读取 `~/.alpaca.env`（API_KEY/SECRET/ENDPOINT），禁止把密钥写入 repo/日志

## Phase 1 — 数据层（Alpaca → 本地快照）
- [x] T1.1 写 Alpaca 15m bars 拉取器：symbol=QQQ，支持 start/end，输出标准化 DataFrame（UTC 索引）
- [x] T1.2 RTH 过滤器：仅保留 09:30–16:00 ET（处理夏令时），并生成缺口统计（v1：基于 clock time 过滤）
- [x] T1.3 数据快照落盘：parquet + meta.json（含拉取时间戳、参数、口径）

## Phase 2 — 回测层（Backtrader）
- [x] T2.1 Backtrader runner：读本地快照 → feed → broker（cash, commission, slippage）
- [x] T2.2 成本模型：滑点 5 bps/side（通过 backtrader slippage perc），并可配置
- [x] T2.3 指标输出：equity 曲线、MaxDD（close-to-close & intrabar）、trade list（含时间戳/持有bar数/PnL）、PF/expectancy/尾部分位数

## Phase 3 — 策略（先只做 RSI2 日内）
- [x] T3.1 RSI2 策略实现（15m，RTH）：Entry=RSI2 < threshold；Exit=SL/TP/TimeStop；是否 EOD 强制平仓由参数/配置决定
- [x] T3.2 单次回测 CLI：一条命令跑出结果到 `results/<run_id>/...` 并打印关键指标

## Phase 4 — 评审与筛选（投委会规则 v1：稳第一）
- [x] T4.1 数据切分：最近 12 个月为 Test（盲测）；其余 Train/Val（用于选参/排名）
- [x] T4.2 Walk-forward（在 Train+Val 内滚动）：训练窗选参 → OOS 窗口评估 → 聚合一致性指标（输出 wfa_windows.parquet + wfa_summary.json）
- [x] T4.3 Gate（硬门槛）：MaxDD(intrabar)≤10%，trades≥200，成本后 Net≥0（分别在 Val/OOS 与 Test 上评估；输出 gate_report.json / wfa_gate_report.json）
- [x] T4.4 参数盆地（Parameter basin）：最优点邻域扰动网格，统计合格区域面积占比（输出 basin_grid.parquet + basin_report.json；并支持在 WFA OOS windows 上批量评估 basin_wfa_windows.parquet + basin_wfa_report.json）
- [x] T4.5 Scorecard v1：Robustness 55 / Risk 25 / Return 15 / Implementability 5（基于 Val/OOS 排名；输出 scorecard.json）

## Phase 5 — Dashboard（Streamlit）
- [x] T5.1 单策略详情页：参数、equity、drawdown、trades 表（v1）
- [x] T5.2 Leaderboard：读取 results 汇总（v1）
- [x] T5.3 Dashboard v2：展示 Train/Val/Test 范围与分段指标；展示 WFA 与参数盆地；按 scorecard 排名（Leaderboard v2 + Details v2）

---

## 当前用户需求（立即）
- [x] N0：先用 Alpaca 数据把 **RSI2 策略回测**跑通，并输出一份结果摘要（含 MaxDD、PF、trade count、net）。
