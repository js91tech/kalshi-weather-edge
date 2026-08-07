from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from .config import ROOT, Settings, active_kalshi_base_url


# Hosts/paths Kalshi has used historically for email/password session login.
_LOGIN_PATHS = ("/login", "/log_in")


def _login_bases(settings: Settings, *, use_demo: bool) -> list[str]:
    if use_demo:
        return [
            settings.kalshi_demo_base_url.rstrip("/"),
            "https://external-api.demo.kalshi.co/trade-api/v2",
            "https://demo-api.kalshi.co/trade-api/v2",
        ]
    return [
        settings.kalshi_base_url.rstrip("/"),
        "https://external-api.kalshi.com/trade-api/v2",
        "https://api.elections.kalshi.com/trade-api/v2",
        "https://trading-api.kalshi.com/trade-api/v2",
        "https://trading-api.kalshi.com/v1",
    ]


def login_with_password(
    settings: Settings,
    *,
    email: str,
    password: str,
    use_demo: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Attempt Kalshi email/password login and return a bearer token session.

    Note: Kalshi's current Trade API docs recommend API keys and no longer
    publish a password login endpoint. We still try known legacy hosts so
    accounts/environments that still support it keep working.
    """
    email = (email or "").strip()
    password = password or ""
    if not email or not password:
        raise ValueError("Email and password are required")

    errors: list[str] = []
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "kalshi-weather-edge/0.4",
        }
    )
    body = {"email": email, "password": password}

    tried: set[str] = set()
    for base in _login_bases(settings, use_demo=use_demo):
        for path in _LOGIN_PATHS:
            url = f"{base.rstrip('/')}{path}"
            if url in tried:
                continue
            tried.add(url)
            try:
                resp = session.post(url, json=body, timeout=timeout)
            except requests.RequestException as exc:
                errors.append(f"{url}: {exc}")
                continue

            if resp.status_code >= 400:
                snippet = (resp.text or "")[:160].replace("\n", " ")
                errors.append(f"{url} -> {resp.status_code} {snippet}")
                continue

            try:
                data = resp.json() if resp.content else {}
            except Exception:
                errors.append(f"{url}: non-JSON response")
                continue

            token = (
                data.get("token")
                or data.get("access_token")
                or (data.get("session") or {}).get("token")
            )
            if not token:
                errors.append(f"{url}: login OK but no token in response")
                continue

            # Prefer the trade-api v2 base for subsequent portfolio calls.
            api_base = active_kalshi_base_url(settings, use_demo=use_demo)
            if "/v1" in base and "trade-api/v2" not in base:
                api_base = active_kalshi_base_url(settings, use_demo=use_demo)

            return {
                "ok": True,
                "email": email,
                "token": str(token),
                "member_id": data.get("member_id") or data.get("user_id"),
                "api_base": api_base,
                "login_url": url,
                "raw": {k: v for k, v in data.items() if k not in ("token", "access_token")},
                "use_demo": bool(use_demo),
            }

    hint = (
        "Kalshi's current Trade API no longer accepts regular website email/password "
        "for apps (official auth is API Key ID + private key). "
        "Create a key at https://kalshi.com/account/profile -> API Keys, "
        "then use the API key tab — or set KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM."
    )
    detail = "; ".join(errors[:4]) if errors else "no login endpoints responded"
    raise RuntimeError(f"{hint} (attempts: {detail})")


def env_password_configured() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    email = os.getenv("KALSHI_EMAIL", "").strip() or os.getenv("KALSHI_USERNAME", "").strip()
    password = os.getenv("KALSHI_PASSWORD", "")
    return {
        "email": email,
        "password_set": bool(password),
        "ready": bool(email and password),
    }
