from __future__ import annotations

import json
from typing import Any


def render_lightweight_chart(
    *,
    candles: list[dict[str, Any]],
    markers: list[dict[str, Any]] | None = None,
    height: int = 560,
) -> str:
    """Single-pane TradingView-like candlestick chart."""

    markers = markers or []

    data_json = json.dumps(candles, ensure_ascii=False)
    markers_json = json.dumps(markers, ensure_ascii=False)

    return f"""<!doctype html>
<html>
  <head>
    <meta charset='utf-8'/>
    <meta name='viewport' content='width=device-width, initial-scale=1'/>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
      html, body {{ margin: 0; padding: 0; background: #0b0f14; }}
      #c {{ width: 100%; height: {height}px; }}
    </style>
  </head>
  <body>
    <div id="c"></div>
    <script>
      const container = document.getElementById('c');
      const chart = LightweightCharts.createChart(container, {{
        layout: {{ background: {{ type: 'solid', color: '#0b0f14' }}, textColor: '#c9d1d9' }},
        grid: {{ vertLines: {{ color: '#1f2a37' }}, horzLines: {{ color: '#1f2a37' }} }},
        crosshair: {{ mode: 1 }},
        rightPriceScale: {{ borderColor: '#1f2a37' }},
        timeScale: {{ borderColor: '#1f2a37', timeVisible: true, secondsVisible: false }},
        handleScroll: true,
        handleScale: true,
      }});

      const series = chart.addCandlestickSeries({{
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
      }});

      const data = {data_json};
      series.setData(data);

      const markers = {markers_json};
      if (markers && markers.length) {{
        series.setMarkers(markers);
      }}

      chart.timeScale().fitContent();

      // Resize
      const ro = new ResizeObserver(entries => {{
        for (const entry of entries) {{
          chart.applyOptions({{ width: entry.contentRect.width }});
        }}
      }});
      ro.observe(container);
    </script>
  </body>
</html>"""


def render_lightweight_chart_dual(
    *,
    candles: list[dict[str, Any]],
    markers: list[dict[str, Any]] | None = None,
    regime_hist: list[dict[str, Any]] | None = None,
    direction_line: list[dict[str, Any]] | None = None,
    height_top: int = 420,
    height_bottom: int = 160,
) -> str:
    """Two-pane chart with synced time axis.

    Pane 1: Candles (+ trade markers)
    Pane 2: Regime histogram (colored) + optional direction line

    Both panes stay in sync when zooming/panning either one.
    """

    markers = markers or []
    regime_hist = regime_hist or []
    direction_line = direction_line or []

    data_json = json.dumps(candles, ensure_ascii=False)
    markers_json = json.dumps(markers, ensure_ascii=False)
    hist_json = json.dumps(regime_hist, ensure_ascii=False)
    dir_json = json.dumps(direction_line, ensure_ascii=False)

    total_h = int(height_top) + int(height_bottom)

    # Avoid f-string brace escaping hell: use a plain template with token replacement.
    tpl = """<!doctype html>
<html>
  <head>
    <meta charset='utf-8'/>
    <meta name='viewport' content='width=device-width, initial-scale=1'/>
    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
      html, body { margin: 0; padding: 0; background: #0b0f14; }
      #wrap { width: 100%; height: __TOTAL_H__px; display: flex; flex-direction: column; gap: 6px; }
      #top { width: 100%; height: __TOP_H__px; }
      #bot { width: 100%; height: __BOT_H__px; }
    </style>
  </head>
  <body>
    <div id="wrap">
      <div id="top"></div>
      <div id="bot"></div>
    </div>
    <script>
      const topEl = document.getElementById('top');
      const botEl = document.getElementById('bot');

      const baseOpts = {
        layout: { background: { type: 'solid', color: '#0b0f14' }, textColor: '#c9d1d9' },
        grid: { vertLines: { color: '#1f2a37' }, horzLines: { color: '#1f2a37' } },
        crosshair: { mode: 1 },
        timeScale: { borderColor: '#1f2a37', timeVisible: true, secondsVisible: false },
        handleScroll: true,
        handleScale: true,
      };

      const chartTop = LightweightCharts.createChart(topEl, Object.assign({}, baseOpts, {
        rightPriceScale: { borderColor: '#1f2a37' },
      }));

      const chartBot = LightweightCharts.createChart(botEl, Object.assign({}, baseOpts, {
        rightPriceScale: { borderColor: '#1f2a37' },
      }));

      const candleSeries = chartTop.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
      });

      const data = __CANDLES__;
      candleSeries.setData(data);

      const markers = __MARKERS__;
      if (markers && markers.length) {
        candleSeries.setMarkers(markers);
      }

      // Bottom: colored histogram for regime
      const histData = __HIST__;
      if (histData && histData.length) {
        const hs = chartBot.addHistogramSeries({
          base: 0,
          lastValueVisible: false,
          priceLineVisible: false,
        });
        hs.setData(histData);
      }

      // Optional direction line
      const dirData = __DIR__;
      if (dirData && dirData.length) {
        const ls = chartBot.addLineSeries({
          color: '#c9d1d9',
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
        });
        ls.setData(dirData);
      }

      chartTop.timeScale().fitContent();
      chartBot.timeScale().fitContent();

      // Sync visible range (two-way) with a guard to avoid loops
      let syncing = false;
      function sync(from, to) {
        if (syncing) return;
        syncing = true;
        const r = from.timeScale().getVisibleLogicalRange();
        if (r) {
          to.timeScale().setVisibleLogicalRange(r);
        }
        syncing = false;
      }

      chartTop.timeScale().subscribeVisibleLogicalRangeChange(() => sync(chartTop, chartBot));
      chartBot.timeScale().subscribeVisibleLogicalRangeChange(() => sync(chartBot, chartTop));

      // Resize
      const ro = new ResizeObserver(entries => {
        for (const entry of entries) {
          chartTop.applyOptions({ width: entry.contentRect.width });
          chartBot.applyOptions({ width: entry.contentRect.width });
        }
      });
      ro.observe(document.getElementById('wrap'));
    </script>
  </body>
</html>"""

    return (
        tpl.replace("__TOTAL_H__", str(total_h))
        .replace("__TOP_H__", str(int(height_top)))
        .replace("__BOT_H__", str(int(height_bottom)))
        .replace("__CANDLES__", data_json)
        .replace("__MARKERS__", markers_json)
        .replace("__HIST__", hist_json)
        .replace("__DIR__", dir_json)
    )
