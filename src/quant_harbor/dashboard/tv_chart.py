from __future__ import annotations

import json
from typing import Any


def render_lightweight_chart(
    *,
    candles: list[dict[str, Any]],
    markers: list[dict[str, Any]] | None = None,
    height: int = 560,
) -> str:
    """Return an HTML payload embedding TradingView-like Lightweight Charts.

    candles format (list of dicts):
      {"time": <unix seconds int>, "open": float, "high": float, "low": float, "close": float}

    markers format:
      {"time": <unix seconds int>, "position": "belowBar"|"aboveBar", "color": str,
       "shape": "arrowUp"|"arrowDown", "text": str}

    Notes:
    - Uses CDN for lightweight-charts. No python deps.
    - Interactions (zoom/pan/crosshair) are close to TradingView.
    """

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
