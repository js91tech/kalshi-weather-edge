from __future__ import annotations

import os
from typing import Any

from .auth_client import KalshiTradingClient
from .config import Settings, active_kalshi_base_url, live_credentials_configured
from .password_auth import env_password_configured, login_with_password


def normalize_pem(pem: str) -> str:
    """Normalize pasted PEM (handle escaped newlines from secrets managers)."""
    text = (pem or "").strip()
    if "\\n" in text and "-----BEGIN" in text:
        text = text.replace("\\n", "\n")
    return text.strip()


def cents_to_dollars(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def format_balance(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize Kalshi balance payload into dollars for the UI."""
    balance_cents = raw.get("balance")
    portfolio_cents = raw.get("portfolio_value")
    return {
        "raw": raw,
        "balance_cents": balance_cents,
        "portfolio_value_cents": portfolio_cents,
        "balance_dollars": cents_to_dollars(balance_cents),
        "portfolio_value_dollars": cents_to_dollars(portfolio_cents),
    }


def build_trading_client(
    settings: Settings,
    *,
    api_key_id: str | None = None,
    private_key_pem: str | None = None,
    private_key_path: str | None = None,
    access_token: str | None = None,
    use_demo: bool | None = None,
) -> KalshiTradingClient:
    return KalshiTradingClient(
        base_url=active_kalshi_base_url(settings, use_demo=use_demo),
        api_key_id=api_key_id,
        private_key_pem=normalize_pem(private_key_pem) if private_key_pem else None,
        private_key_path=private_key_path,
        access_token=access_token,
    )


def connect_kalshi_account(
    settings: Settings,
    *,
    api_key_id: str,
    private_key_pem: str | None = None,
    private_key_path: str | None = None,
    use_demo: bool = False,
) -> dict[str, Any]:
    """Validate API-key credentials by fetching portfolio balance."""
    key_id = (api_key_id or "").strip()
    pem = normalize_pem(private_key_pem or "")
    path = (private_key_path or "").strip() or None
    if not key_id:
        raise ValueError("API Key ID is required")
    if not pem and not path:
        raise ValueError("Paste your private key PEM or provide a .pem file path")

    client = build_trading_client(
        settings,
        api_key_id=key_id,
        private_key_pem=pem or None,
        private_key_path=path,
        use_demo=use_demo,
    )
    raw_balance = client.get_balance()
    balance = format_balance(raw_balance)
    return {
        "ok": True,
        "auth_mode": "api_key",
        "api_key_id": key_id,
        "use_demo": bool(use_demo),
        "key_id_suffix": key_id[-8:] if len(key_id) >= 8 else key_id,
        "display_name": key_id[-8:] if len(key_id) >= 8 else key_id,
        "balance": balance,
        "private_key_pem": pem or None,
        "private_key_path": path if not pem else None,
        "access_token": None,
        "email": None,
    }


def connect_with_password(
    settings: Settings,
    *,
    email: str,
    password: str,
    use_demo: bool = False,
) -> dict[str, Any]:
    """Login with Kalshi email/password, then verify via /portfolio/balance."""
    login = login_with_password(
        settings, email=email, password=password, use_demo=use_demo
    )
    client = KalshiTradingClient(
        base_url=login["api_base"],
        access_token=login["token"],
    )
    balance = format_balance(client.get_balance())
    email_clean = login["email"]
    return {
        "ok": True,
        "auth_mode": "password",
        "api_key_id": None,
        "use_demo": bool(use_demo),
        "key_id_suffix": email_clean.split("@")[0][:12],
        "display_name": email_clean,
        "balance": balance,
        "private_key_pem": None,
        "private_key_path": None,
        "access_token": login["token"],
        "email": email_clean,
        "member_id": login.get("member_id"),
    }


def connect_from_env(settings: Settings, *, use_demo: bool | None = None) -> dict[str, Any]:
    """Connect using .env / Streamlit secrets (password preferred, else API key)."""
    demo = use_demo
    if demo is None:
        demo = live_credentials_configured().get("env") == "demo"

    pw = env_password_configured()
    if pw["ready"]:
        try:
            return connect_with_password(
                settings,
                email=pw["email"],
                password=os.getenv("KALSHI_PASSWORD", ""),
                use_demo=bool(demo),
            )
        except Exception:
            # Fall through to API key if password login is unavailable.
            pass

    creds = live_credentials_configured()
    if not creds["ready"] and not creds.get("pem_set"):
        raise ValueError(
            "No credentials in environment. Set KALSHI_EMAIL + KALSHI_PASSWORD "
            "(if your environment still supports password login) or "
            "KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM/PATH."
        )
    return connect_kalshi_account(
        settings,
        api_key_id=creds["key_id"],
        private_key_pem=creds.get("private_key_pem") or None,
        private_key_path=creds.get("key_path") or None,
        use_demo=bool(demo),
    )


def refresh_balance(
    settings: Settings,
    *,
    api_key_id: str | None = None,
    private_key_pem: str | None = None,
    private_key_path: str | None = None,
    access_token: str | None = None,
    use_demo: bool = False,
) -> dict[str, Any]:
    client = build_trading_client(
        settings,
        api_key_id=api_key_id,
        private_key_pem=private_key_pem,
        private_key_path=private_key_path,
        access_token=access_token,
        use_demo=use_demo,
    )
    return format_balance(client.get_balance())
