# Quant Harbor — Research Protocol (投委会口径)

更新时间（PT）：2026-02-22

本文件定义 Quant Harbor 的“机构化/投委会式”研究纪律：
- 如何横向对比不同策略
- 如何选择最终可交易的策略+参数
- 如何避免 Test 泄露与反复调参

---

## 1. 研究对象定义

### 1.1 Production 对象（默认）
**策略 S + 固定参数 θ（frozen）**

这是横向比较与最终落地交易的默认对象。

### 1.2 Diagnostics 对象（研究诊断）
**策略 S + 调参规则 R（滚动选参）**

用于回答“策略是否依赖频繁调参才能活”。
不进入 Production 主榜排名。

---

## 2. 数据切分（不可打乱）

- **Test（盲测）**：固定保留最近 12 个月。
- **Pre（Train+Val）**：Test 之前的全部历史。
- **Train/Val**：在 Pre 内按时间切分（例如 80/20），用于候选比较与快速诊断。

纪律：
- Test 只用于冻结（freeze）后的最终验收；不可反复用 Test 调参。

---

## 3. Walk-forward（WFA）两种模式

### 3.1 WFA — fixed params（Production 用）
- 定义一组候选参数集合 Θ。
- 对每个候选 θ∈Θ：
  - 在多个 OOS 窗口上用同一个 θ 跑回测（固定参数跨窗口）。
  - 聚合 OOS 分布：pos_window_rate / median / mean / worst。

意义：
- 这是对“策略+参数 θ”组合的一致性验证。
- 所有策略进入主榜必须提供此模式 WFA 的证据。

### 3.2 WFA — re-tune per window（Diagnostics 用）
- 每个窗口：用训练窗选参 → 在 OOS 验证。
- 每个窗口可能选出不同参数。

意义：
- 评估“策略+调参规则”的稳定性。
- 用于诊断策略是否需要频繁调参，是否 regime-dependent。

---

## 4. FreezeA（方案 A：默认落地流程）

### 4.1 冻结参数选择（只用 Pre 内的 WFA OOS）
默认规则：
1) 过滤：pos_window_rate ≥ 0.70
2) 排序：OOS median(net_return_pct) 最大
3) tie-break：worst 再 mean
4) 若无候选满足 0.70：触发 fallback，直接在全体候选中按同样排序选第一名（必须在报告中标注）。

### 4.2 Test 验收（一次性）
- 用 frozen θ* 在 Test（最近12个月）上跑一次，输出 final_report。

---

## 5. 参数盆地（Parameter Basin）

围绕 θ* 的邻域扰动网格，统计“仍然合格”的区域占比（basin_pass_rate）。

- basin（VAL）：在 Val 段评估，快速判断参数敏感性。
- basin（WFA OOS）：可选，对多个 OOS 窗口评估并聚合（mean/median/worst），判断盆地是否随时间崩塌。

---

## 6. Scorecard（主榜打分口径）

主榜只对 Production 对象（S+θ*）打分。

- Robustness（55）：来自 WFA fixed-params 的一致性指标（pos_window_rate、median/worst等）+ basin 稳定性
- Risk/Tail（25）：主要来自 Test 段的 MaxDD（intrabar）等风险指标
- Return Quality（15）：Test 段 PF/expectancy/sharpe/net_return 等
- Implementability（5）：可实现性占位（后续升级：复杂度/限价成交/延迟敏感等）

---

## 7. Dashboard 规范

Dashboard v2 的主榜（Leaderboard v2）只展示：
- run_kind=freezeA 的 scorecard.json

Diagnostics 页面展示：
- WFA re-tune per window 的窗口表现

---

## 8. 必需产物（Production 主榜）

每个策略进入主榜必须落盘：
- `final_report.json`（freeze 过程 + frozen_params + Test）
- `wfa_windows.parquet` + `wfa_summary.json`（fixed-params WFA）
- `basin_report.json`（至少 Val 盆地；可选 basin_wfa_report）
- `scorecard.json`
