from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json

from quant_harbor.scorecard import scorecard_v1

UTC = ZoneInfo("UTC")


def _load_json(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    return json.loads(p.read_text())


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--out-dir", default="", help="Output directory. Default: alongside --gate-report")

    ap.add_argument("--wfa-summary", default="", help="Path to wfa_summary.json (optional)")
    ap.add_argument("--wfa-gate", default="", help="Path to wfa_gate_report.json (optional)")
    ap.add_argument("--basin", default="", help="Path to basin_report.json (optional)")
    ap.add_argument("--basin-wfa", default="", help="Path to basin_wfa_report.json (optional)")

    ap.add_argument("--val-summary", default="", help="Path to val summary.json (optional)")
    ap.add_argument("--test-summary", default="", help="Path to test summary.json (optional)")
    ap.add_argument("--gate-report", default="", help="Path to gate_report.json (optional; used only for metadata + default out-dir)")

    args = ap.parse_args()

    wfa_summary = _load_json(args.wfa_summary) if args.wfa_summary else None
    wfa_gate = _load_json(args.wfa_gate) if args.wfa_gate else None
    basin = _load_json(args.basin) if args.basin else None
    basin_wfa = _load_json(args.basin_wfa) if args.basin_wfa else None

    val_summary = _load_json(args.val_summary) if args.val_summary else None
    test_summary = _load_json(args.test_summary) if args.test_summary else None

    gate_report = _load_json(args.gate_report) if args.gate_report else None

    out_dir = None
    if args.out_dir:
        out_dir = Path(args.out_dir).expanduser().resolve()
    elif gate_report and args.gate_report:
        out_dir = Path(args.gate_report).expanduser().resolve().parent
    else:
        out_dir = Path.cwd() / "results" / f"scorecard_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"

    out_dir.mkdir(parents=True, exist_ok=True)

    sc = scorecard_v1(
        wfa_summary=wfa_summary,
        wfa_gate_report=wfa_gate,
        basin_report=basin,
        basin_wfa_report=basin_wfa,
        val_summary=val_summary,
        test_summary=test_summary,
    )

    # Attach metadata (best-effort)
    meta = {}
    if gate_report:
        meta["symbol"] = gate_report.get("symbol")
        meta["strategy"] = gate_report.get("strategy")
        meta["chosen_params"] = gate_report.get("chosen_params")
        meta["snapshot_dir"] = gate_report.get("snapshot_dir")

    out = {
        "generated_utc": datetime.now(tz=UTC).isoformat(),
        "meta": meta,
        "scorecard": sc,
        "sources": {
            "wfa_summary": args.wfa_summary or None,
            "wfa_gate": args.wfa_gate or None,
            "basin": args.basin or None,
            "basin_wfa": args.basin_wfa or None,
            "val_summary": args.val_summary or None,
            "test_summary": args.test_summary or None,
            "gate_report": args.gate_report or None,
        },
    }

    (out_dir / "scorecard.json").write_text(json.dumps(out, indent=2, default=str))

    print("Scorecard written:", out_dir / "scorecard.json")
    print("total_score:", out["scorecard"]["total_score"])


if __name__ == "__main__":
    main()
