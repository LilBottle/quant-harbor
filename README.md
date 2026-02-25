# quant_harbor

本地回测 + Dashboard 工程（Backtrader + Plotly + Streamlit）。

目标：机构化研究闭环：数据快照（可复现）→ 回测（成本/滑点）→ 鲁棒性筛选（MaxDD≤10% 等）→ 排名 → Dashboard。

- 时间粒度：15m
- RTH：09:30–16:00 ET
- 成本：滑点 5 bps/side（单边 0.05%）
- 盲测：最近 12 个月为 Test（freeze 后才跑）

入口：
- `src/` 代码
- `tasks/TASKS.md` 任务拆解
- `docs/` 设计与评审规则

## 运行 Dashboard
```bash
cd ~/Desktop/trader/quant_harbor
source .venv/bin/activate
streamlit run src/quant_harbor/dashboard/app.py
```
