"""Channel-test report files: standalone HTML and JSON, saved locally."""

import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_STYLE = (
    "body{font-family:Segoe UI,system-ui,sans-serif;margin:24px}"
    "table{border-collapse:collapse}td,th{border:1px solid #bbb;"
    "padding:4px 10px;font-size:13px}th{background:#eee}"
    "td.n{text-align:right;font-variant-numeric:tabular-nums}"
)


def _fmt(value: Optional[float], decimals: int) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def render_html(meta: dict, rows: list) -> str:
    head = (
        f"{meta['started']} · reads/ch {meta['reads_per_channel']}"
        f" · resolution {meta['resolution_index']}"
        f" · settling {meta['settling_factor']}"
        f" · {meta['elapsed_s']:.1f} s"
        f" · {meta['channel_reads_per_s']:.2f} channel reads/s"
        f" · {meta['ain_points_per_s']:.2f} points/s"
    )
    if meta.get("aborted"):
        head += " · <b>ABORTED</b>"
    body = "".join(
        "<tr>"
        f"<td>{row['ch']}</td><td>{row['name']}</td>"
        f"<td class=n>{row['reads']}</td>"
        f"<td class=n>{_fmt(row['vref_mean_mv'], 4)}</td>"
        f"<td class=n>{_fmt(row['vref_sd_mv'], 4)}</td>"
        f"<td class=n>{_fmt(row['vrtd_mean_mv'], 4)}</td>"
        f"<td class=n>{_fmt(row['vrtd_sd_mv'], 4)}</td>"
        f"<td class=n>{_fmt(row['r_mean_ohm'], 4)}</td>"
        f"<td class=n>{_fmt(row['t_mean_c'], 4)}</td>"
        f"<td class=n>{_fmt(row['t_sd_c'], 4)}</td>"
        f"<td>{row['status']}</td></tr>"
        for row in rows
    )
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<title>Channel test — {meta['console']}</title>"
        f"<style>{_STYLE}</style>"
        f"<h2>Channel test — {meta['console']}</h2><p>{head}</p>"
        "<table><tr><th>CH</th><th>Name</th><th>Reads</th>"
        "<th>Vref x̄ [mV]</th><th>Vref σ</th><th>Vrtd x̄ [mV]</th><th>Vrtd σ</th>"
        "<th>R x̄ [Ω]</th><th>T x̄ [°C]</th><th>T σ [°C]</th><th>Status</th></tr>"
        f"{body}</table>"
    )


def write_reports(
    reports_dir: Path, meta: dict, rows: list, raw: dict
) -> tuple:
    """Write HTML and JSON reports; return their file names."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_console = "".join(
        ch if ch.isalnum() or ch in "-_" else "-" for ch in meta["console"]
    )
    base = f"channel-test_{safe_console}_{stamp}"
    html_name, json_name = base + ".html", base + ".json"

    (reports_dir / html_name).write_text(render_html(meta, rows), encoding="utf-8")
    payload = {"meta": meta, "channels": rows, "raw_readings": raw}
    (reports_dir / json_name).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    log.info("channel-test report written: %s", reports_dir / html_name)
    return html_name, json_name
