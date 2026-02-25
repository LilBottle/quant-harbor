from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from quant_harbor.dashboard.tv_chart import render_lightweight_chart
from quant_harbor.regime import RegimeConfig, compute_regime
from quant_harbor.dashboard.utils import (
    discover_runs,
    runs_to_dataframe,
    discover_scorecards,
    scorecards_to_dataframe,
    load_equity,
    load_trades,
    load_parquet,
    load_snapshot_bars,
)


st.set_page_config(page_title="Quant Harbor Dashboard", layout="wide")

ROOT = Path(__file__).resolve().parents[3]  # quant_harbor/
RESULTS_DIR = ROOT / "results"


def _fmt(x, digits=2):
    if x is None:
        return None
    try:
        return round(float(x), digits)
    except Exception:
        return x


def page_leaderboard_v2():
    st.title("Leaderboard (v2)")
    st.caption("优先展示 scorecard（投委会式打分）；若不存在则回退到单次回测 summary。")

    sc_runs = discover_scorecards(RESULTS_DIR)
    if sc_runs:
        df = scorecards_to_dataframe(sc_runs)

        with st.sidebar:
            st.header("Filters")
            symbol = st.selectbox("Symbol", ["(all)"] + sorted([x for x in df["symbol"].dropna().unique()]))
            strategy = st.selectbox("Strategy", ["(all)"] + sorted([x for x in df["strategy"].dropna().unique()]))
            run_kind = st.selectbox("Run kind", ["(all)"] + sorted([x for x in df["run_kind"].dropna().unique()]))
            min_score = st.slider("Total score >=", 0.0, 100.0, 0.0, 1.0)

        dff = df.copy()
        if symbol != "(all)":
            dff = dff[dff["symbol"] == symbol]
        if strategy != "(all)":
            dff = dff[dff["strategy"] == strategy]
        if run_kind != "(all)":
            dff = dff[dff["run_kind"] == run_kind]

        dff = dff[dff["total_score"].fillna(-1e9) >= float(min_score)]

        st.subheader("Scorecards")
        st.dataframe(
            dff.sort_values(by=["total_score"], ascending=False),
            use_container_width=True,
            hide_index=True,
        )
        st.info("去 Details (v2) 查看单个 run 的 WFA/盆地/门槛/分数细节。")
        return

    # fallback: legacy
    runs = discover_runs(RESULTS_DIR)
    if not runs:
        st.warning(f"No runs found in {RESULTS_DIR}. Run a backtest first.")
        return

    df = runs_to_dataframe(runs)

    with st.sidebar:
        st.header("Filters")
        symbol = st.selectbox("Symbol", ["(all)"] + sorted([x for x in df['symbol'].dropna().unique()]))
        strategy = st.selectbox("Strategy", ["(all)"] + sorted([x for x in df['strategy'].dropna().unique()]))
        maxdd = st.slider("MaxDD intrabar <= (%)", 0.0, 50.0, 10.0, 0.5)
        min_trades = st.number_input("Trades >=", min_value=0, value=200, step=10)

    dff = df.copy()
    if symbol != "(all)":
        dff = dff[dff["symbol"] == symbol]
    if strategy != "(all)":
        dff = dff[dff["strategy"] == strategy]

    dff = dff[(dff["max_dd_intrabar_pct"].isna()) | (dff["max_dd_intrabar_pct"] <= maxdd)]
    dff = dff[(dff["total_trades"].isna()) | (dff["total_trades"] >= min_trades)]

    st.subheader("Runs")
    st.dataframe(
        dff.sort_values(by=["net_pnl"], ascending=False),
        use_container_width=True,
        hide_index=True,
    )

    st.info("(fallback) 去 Details 查看单次回测的交易列表、权益曲线、回撤曲线。")


def page_details_v2():
    st.title("Run Details (v2)")

    sc_runs = discover_scorecards(RESULTS_DIR)
    if not sc_runs:
        st.warning("No scorecard runs found. Run T4.5 scorecard first (or use legacy Details page).")
        return

    run_map = {r.run_id: r for r in sc_runs}

    with st.sidebar:
        st.header("Select scorecard run")
        run_id = st.selectbox("run_id", list(run_map.keys()))

    run = run_map[run_id]
    sc = run.scorecard

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total score", _fmt(sc.get("total_score"), 2))
    subs = sc.get("subscores01") or {}
    c2.metric("Robustness", _fmt(subs.get("robustness"), 3))
    c3.metric("Risk", _fmt(subs.get("risk"), 3))
    c4.metric("ReturnQ", _fmt(subs.get("return_quality"), 3))

    st.write("### Meta")
    st.code(json.dumps(run.meta, indent=2), language="json")

    st.write("### Scorecard")
    st.code(json.dumps(sc, indent=2), language="json")

    sources = run.sources or {}

    # ---- Train/Val/Test summary cards (if provided) ----
    st.write("---")
    st.write("## Segments (Train/Val/Test)")

    def _load_summary(path_str: str | None):
        if not path_str:
            return None
        p = (ROOT / path_str).resolve() if str(path_str).startswith("results/") else Path(path_str).expanduser().resolve()
        return json.loads(p.read_text()) if p.exists() else None

    val_summary = _load_summary(sources.get("val_summary"))
    test_summary = _load_summary(sources.get("test_summary"))

    cols = st.columns(2)
    if val_summary:
        cols[0].subheader("VAL")
        cols[0].metric("Net%", _fmt(val_summary.get("net_return_pct"), 3))
        cols[0].metric("MaxDD intra%", _fmt(val_summary.get("max_drawdown_intrabar_pct"), 3))
        cols[0].metric("PF", _fmt(val_summary.get("profit_factor"), 3))
        cols[0].metric("Trades", val_summary.get("total_trades"))
        cols[0].caption(f"UTC: {val_summary.get('data_dt_min_utc')} → {val_summary.get('data_dt_max_utc')}")
    else:
        cols[0].info("VAL summary not provided")

    if test_summary:
        cols[1].subheader("TEST")
        cols[1].metric("Net%", _fmt(test_summary.get("net_return_pct"), 3))
        cols[1].metric("MaxDD intra%", _fmt(test_summary.get("max_drawdown_intrabar_pct"), 3))
        cols[1].metric("PF", _fmt(test_summary.get("profit_factor"), 3))
        cols[1].metric("Trades", test_summary.get("total_trades"))
        cols[1].caption(f"UTC: {test_summary.get('data_dt_min_utc')} → {test_summary.get('data_dt_max_utc')}")
    else:
        cols[1].info("TEST summary not provided")

    # ---- TEST candlesticks + trade markers ----
    st.write("---")
    st.write("## TEST price (candles) + trades")

    snapshot_dir = run.meta.get("snapshot_dir")
    test_summary_path = sources.get("test_summary")

    if not snapshot_dir or not test_summary or not test_summary_path:
        st.info("Need snapshot_dir in meta + test_summary in sources to plot candles.")
    else:
        # Determine symbol list (for pairs we allow selecting a leg)
        sym_field = run.meta.get("symbol") or ""
        syms = [s for s in sym_field.split("-") if s] if "-" in sym_field else [sym_field]
        if not syms or syms == [""]:
            syms = ["QQQ"]

        sym_sel = st.selectbox("Symbol (leg)", syms)

        snap_path = Path(snapshot_dir).expanduser().resolve()
        bars = load_snapshot_bars(snap_path, sym_sel)

        if bars is None or bars.empty:
            st.warning(f"No bars found in snapshot for {sym_sel}: {snap_path}")
        else:
            # Filter to test range
            t0 = pd.to_datetime(test_summary.get("data_dt_min_utc"), utc=True)
            t1 = pd.to_datetime(test_summary.get("data_dt_max_utc"), utc=True)
            bars = bars[(bars.index >= t0) & (bars.index <= t1)].copy()

            # Load trades from the test directory (if present)
            test_dir = (ROOT / Path(test_summary_path).parent).resolve() if str(test_summary_path).startswith("results/") else Path(test_summary_path).expanduser().resolve().parent
            tr = load_trades(test_dir)
            if tr is not None and not tr.empty:
                tr = tr.copy()
                tr["entry_dt"] = pd.to_datetime(tr["entry_dt"], utc=True, errors="coerce")
                tr["exit_dt"] = pd.to_datetime(tr["exit_dt"], utc=True, errors="coerce")

            # Convert to ET for nicer rangebreaks (skip overnight/weekend gaps)
            bars_et = bars.copy()
            bars_et.index = bars_et.index.tz_convert("America/New_York").tz_localize(None)

            fig = go.Figure(
                data=[
                    go.Candlestick(
                        x=bars_et.index,
                        open=bars_et["open"],
                        high=bars_et["high"],
                        low=bars_et["low"],
                        close=bars_et["close"],
                        name="TEST candles",
                    )
                ]
            )

            # Add markers (convert timestamps to ET-naive to align with candles)
            if tr is not None and not tr.empty:
                # Convert timestamps to ET-naive and SNAP to the nearest candle timestamp.
                # This avoids marker drift when zooming + when trade timestamps are not exactly on the bar grid.
                tr_et = tr.copy()
                tr_et["entry_dt_et"] = tr_et["entry_dt"].dt.tz_convert("America/New_York").dt.tz_localize(None)
                tr_et["exit_dt_et"] = tr_et["exit_dt"].dt.tz_convert("America/New_York").dt.tz_localize(None)

                x_index = pd.DatetimeIndex(bars_et.index)

                def _snap(ts_series: pd.Series) -> pd.Series:
                    # nearest bar within 20 minutes; else NaT
                    idx = x_index.get_indexer(ts_series, method="nearest", tolerance=pd.Timedelta(minutes=20))
                    out = []
                    for i in idx:
                        out.append(x_index[i] if i != -1 else pd.NaT)
                    return pd.Series(out, index=ts_series.index)

                tr_et["entry_x"] = _snap(tr_et["entry_dt_et"])
                tr_et["exit_x"] = _snap(tr_et["exit_dt_et"])

                # Keep only markers that snapped inside the visible candle range
                tr_et = tr_et[(tr_et["entry_x"].notna()) | (tr_et["exit_x"].notna())]

                fig.add_trace(
                    go.Scatter(
                        x=tr_et["entry_x"],
                        y=tr_et["entry_price"],
                        mode="markers",
                        marker=dict(symbol="triangle-up", size=9),
                        name="Buy/Entry",
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=tr_et["exit_x"],
                        y=tr_et["exit_price"],
                        mode="markers",
                        marker=dict(symbol="triangle-down", size=9),
                        name="Sell/Exit",
                    )
                )

            # Remove non-trading gaps (weekends + overnight). Times are ET-naive now.
            fig.update_xaxes(
                rangebreaks=[
                    dict(bounds=["sat", "mon"]),
                    dict(bounds=[16, 9.5], pattern="hour"),
                ]
            )

            # Robust y-axis scaling: use quantiles to avoid single outlier making candles look too tall.
            try:
                lo = float(bars_et["low"].quantile(0.01))
                hi = float(bars_et["high"].quantile(0.99))
                pad = (hi - lo) * 0.05 if hi > lo else (hi * 0.01 if hi else 1.0)
                fig.update_yaxes(range=[lo - pad, hi + pad])
            except Exception:
                pass

            use_tv = st.toggle("TradingView-like chart", value=True, help="Use lightweight-charts (TradingView-style zoom/pan).")

            if use_tv:
                # Build lightweight-charts candles (unix seconds)
                c = []
                for ts, row in bars.iterrows():
                    c.append({
                        "time": int(ts.timestamp()),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    })

                m = []
                if tr is not None and not tr.empty:
                    # snap markers to nearest candle time to avoid drift
                    x_index = pd.DatetimeIndex(bars.index)

                    # Parse trade timestamps robustly
                    entry_dt = pd.to_datetime(tr["entry_dt"], utc=True, errors="coerce")
                    exit_dt = pd.to_datetime(tr["exit_dt"], utc=True, errors="coerce")

                    def _snap(ts_series: pd.Series) -> list[int | None]:
                        idx = x_index.get_indexer(ts_series, method="nearest", tolerance=pd.Timedelta(minutes=60))
                        out: list[int | None] = []
                        for i in idx:
                            out.append(int(x_index[i].timestamp()) if i != -1 else None)
                        return out

                    entry_ts = _snap(entry_dt)
                    exit_ts = _snap(exit_dt)

                    for t in entry_ts:
                        if t is None:
                            continue
                        m.append({"time": t, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp", "text": "BUY"})
                    for t in exit_ts:
                        if t is None:
                            continue
                        m.append({"time": t, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown", "text": "SELL"})

                    # Lightweight-charts expects markers sorted by time.
                    m = sorted(m, key=lambda x: (x.get("time", 0), x.get("text", "")))

                # Regime overlay on TEST bars (15m): show background blocks by regime.
                try:
                    reg = compute_regime(bars, RegimeConfig())
                    # compute contiguous blocks and render as semi-transparent spans
                    blocks = []
                    cur = None
                    for ts, lab in reg["regime"].items():
                        if cur is None:
                            cur = [ts, ts, lab]
                        elif lab == cur[2]:
                            cur[1] = ts
                        else:
                            blocks.append(tuple(cur))
                            cur = [ts, ts, lab]
                    if cur is not None:
                        blocks.append(tuple(cur))

                    colors = {"Trend": "rgba(38,166,154,0.10)", "Range": "rgba(239,83,80,0.10)", "Neutral": "rgba(201,209,217,0.06)"}
                    # Lightweight-charts doesn't support background regions natively.
                    # As MVP: show a small regime timeline below the chart using Plotly.
                    st.caption("Regime (15m) on TEST segment: Trend/Range/Neutral (computed from ER+ADX+Bandwidth; EMA-smoothed).")
                    reg_plot = reg[["trend_score"]].copy()
                    reg_plot.index = reg_plot.index.tz_convert("America/New_York").tz_localize(None)
                    figr = go.Figure()
                    figr.add_trace(go.Scatter(x=reg_plot.index, y=reg_plot["trend_score"], name="trend_score"))
                    figr.update_layout(height=180, margin=dict(l=10, r=10, t=20, b=10))
                    figr.add_hline(y=0.6, line_dash="dot")
                    figr.add_hline(y=0.4, line_dash="dot")
                    st.plotly_chart(figr, use_container_width=True)
                except Exception:
                    pass

                html = render_lightweight_chart(candles=c, markers=m, height=560)
                components.html(html, height=580, scrolling=False)
            else:
                fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10), xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)

            if tr is None:
                st.caption("No trades.parquet found under test/ for this run.")
            else:
                st.dataframe(tr, use_container_width=True, hide_index=True)

    # ---- WFA ----
    st.write("---")
    st.write("## Walk-forward (WFA)")
    wfa_win_path = ROOT / (Path(sources.get("wfa_summary", "")).parent / "wfa_windows.parquet") if sources.get("wfa_summary") else None
    if wfa_win_path and wfa_win_path.exists():
        wfa_df = load_parquet(wfa_win_path)
        if wfa_df is not None:
            for c in ["oos_start_utc", "oos_end_utc", "train_start_utc", "train_end_utc"]:
                if c in wfa_df.columns:
                    wfa_df[c] = pd.to_datetime(wfa_df[c], utc=True)
            fig = px.bar(wfa_df, x="window", y="oos_net_return_pct", title="OOS net_return_pct per window")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(wfa_df, use_container_width=True, hide_index=True)
    else:
        st.info("No wfa_windows.parquet found (provide wfa sources).")

    # ---- Basin ----
    st.write("---")
    st.write("## Parameter basin")
    basin_path = sources.get("basin")
    if basin_path:
        basin_rep = _load_summary(basin_path)
        if basin_rep:
            st.metric("Basin pass rate", _fmt(basin_rep.get("basin_pass_rate"), 4))
            st.caption(f"Grid points: {basin_rep.get('grid_points')} | Passed: {basin_rep.get('passed_points')}")

    basin_wfa_path = sources.get("basin_wfa")
    if basin_wfa_path:
        basin_wfa_rep = _load_summary(basin_wfa_path)
        if basin_wfa_rep:
            st.write("Basin (WFA aggregate)")
            st.json({
                "windows": basin_wfa_rep.get("windows"),
                "mean": basin_wfa_rep.get("basin_pass_rate_mean"),
                "median": basin_wfa_rep.get("basin_pass_rate_median"),
                "worst": basin_wfa_rep.get("basin_pass_rate_worst"),
            })
            # if windows parquet exists alongside report
            win_parq = Path(basin_wfa_path).expanduser()
            if str(basin_wfa_path).startswith("results/"):
                win_parq = (ROOT / basin_wfa_path).resolve()
            win_parq = win_parq.parent / "basin_wfa_windows.parquet"
            bw = load_parquet(win_parq)
            if bw is not None and "basin_pass_rate" in bw.columns:
                fig2 = px.line(bw, x="window", y="basin_pass_rate", markers=True, title="Basin pass rate per OOS window")
                st.plotly_chart(fig2, use_container_width=True)

    st.write("---")
    st.write("### Sources")
    st.code(json.dumps(sources, indent=2), language="json")


def page_details_legacy():
    st.title("Run Details (legacy)")

    runs = discover_runs(RESULTS_DIR)
    if not runs:
        st.warning(f"No runs found in {RESULTS_DIR}. Run a backtest first.")
        return

    run_map = {r.run_id: r for r in runs}

    with st.sidebar:
        st.header("Select run")
        run_id = st.selectbox("run_id", list(run_map.keys()))

    run = run_map[run_id]
    s = run.summary

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Net PnL", _fmt(s.get('net_pnl')))
    col2.metric("Net Return %", _fmt(s.get('net_return_pct')))
    col3.metric("MaxDD intrabar %", _fmt(s.get('max_drawdown_intrabar_pct')))
    col4.metric("Profit Factor", _fmt(s.get('profit_factor'), 3))

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Trades", s.get('total_trades'))
    col6.metric("Win rate %", _fmt(s.get('win_rate_pct')))
    col7.metric("Expectancy", _fmt(s.get('expectancy'), 4))
    col8.metric("Sharpe", _fmt(s.get('sharpe'), 3))

    st.write("### Strategy params")
    st.code(json.dumps(s.get('strategy_params', {}), indent=2), language='json')

    eq = load_equity(run.path)
    if eq is None:
        st.info("No equity.parquet for this run")
        return

    eq['dt'] = pd.to_datetime(eq['dt'])
    eq = eq.sort_values('dt')

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(x=eq['dt'], y=eq['equity_close'], name='Equity (close)'))
    fig_eq.add_trace(go.Scatter(x=eq['dt'], y=eq['equity_intrabar_min'], name='Equity (intrabar min)', line=dict(dash='dot')))
    fig_eq.update_layout(height=350, margin=dict(l=10,r=10,t=30,b=10))

    st.plotly_chart(fig_eq, use_container_width=True)

    tr = load_trades(run.path)
    if tr is not None and not tr.empty:
        tr['entry_dt'] = pd.to_datetime(tr['entry_dt'])
        tr['exit_dt'] = pd.to_datetime(tr['exit_dt'])
        tr = tr.sort_values('entry_dt')
        st.dataframe(tr, use_container_width=True, hide_index=True)


PAGES = {
    "Leaderboard (v2)": page_leaderboard_v2,
    "Details (v2)": page_details_v2,
    "Details (legacy)": page_details_legacy,
}

with st.sidebar:
    st.title("Quant Harbor")
    page = st.radio("Page", list(PAGES.keys()))

PAGES[page]()
