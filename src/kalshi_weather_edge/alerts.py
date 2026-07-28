from __future__ import annotations

import os
from typing import Any

import requests

from .config import Settings

try:
    from .config import alert_webhook_url as _config_webhook_url
except ImportError:  # pragma: no cover - older deploys
    _config_webhook_url = None


def get_webhook_url() -> str | None:
    """Resolve ALERT_WEBHOOK_URL from env (.env or GitHub secrets)."""
    if _config_webhook_url is not None:
        try:
            return _config_webhook_url()
        except Exception:
            pass
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    return url or None


def webhook_ready() -> bool:
    return bool(get_webhook_url())


def rank_alert_signals(
    signals: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    """Pick top trade signals for alerts (by |edge|, then mid extremity)."""
    trades = [s for s in signals if s.get("action") not in (None, "PASS")]
    trades = [
        s
        for s in trades
        if abs(float(s.get("edge") or 0)) >= settings.min_edge_for_alert
    ]
    trades.sort(
        key=lambda s: (
            abs(float(s.get("edge") or 0)),
            abs(float(s.get("market_mid") or 0.5) - 0.5),
        ),
        reverse=True,
    )
    return trades[: settings.max_signals_alert]


def format_alert_text(
    strategy: str,
    signals: list[dict[str, Any]],
    *,
    settled_updated: int = 0,
    paper_pnl: float | None = None,
) -> str:
    lines = [
        f"**Kalshi {strategy}** — {len(signals)} top signal(s)",
    ]
    if paper_pnl is not None:
        lines.append(f"Paper PnL: ${paper_pnl:.2f} · Settlements applied: {settled_updated}")
    for s in signals:
        meta = s.get("meta") or {}
        series = meta.get("series") or s.get("city_id") or "?"
        lines.append(
            f"- `{s.get('action')}` **{s.get('ticker')}** ({series}) "
            f"mid={float(s.get('market_mid') or 0):.3f} edge={float(s.get('edge') or 0):.3f}"
        )
    return "\n".join(lines)


def send_webhook_alert(
    text: str,
    *,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """
    Post to Discord-style webhook (content) or Slack-style (text).
    Returns status dict; no-op if URL missing.
    """
    url = webhook_url or get_webhook_url()
    if not url:
        return {"ok": False, "skipped": True, "reason": "ALERT_WEBHOOK_URL not set"}

    payloads = [
        {"content": text[:1900]},
        {"text": text[:1900]},
    ]
    last_error = None
    for payload in payloads:
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code < 300:
                return {"ok": True, "status_code": resp.status_code}
            last_error = f"{resp.status_code}: {resp.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
    return {"ok": False, "error": last_error}


def maybe_alert_scan(
    settings: Settings,
    *,
    strategy: str,
    rows: list[dict[str, Any]],
    settled_updated: int = 0,
    paper_pnl: float | None = None,
) -> dict[str, Any]:
    if not settings.alerts_enabled:
        return {"ok": False, "skipped": True, "reason": "alerts disabled in config"}
    top = rank_alert_signals(rows, settings)
    if not top:
        return {"ok": True, "skipped": True, "reason": "no signals above alert threshold", "n": 0}
    text = format_alert_text(
        strategy,
        top,
        settled_updated=settled_updated,
        paper_pnl=paper_pnl,
    )
    result = send_webhook_alert(text)
    result["n"] = len(top)
    result["preview"] = text
    return result
