from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import requests


@dataclass
class TempForecast:
    target_date: str
    mu: float
    sigma: float
    p10: float
    p50: float
    p90: float
    source: str
    members: list[float]


def _percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q))


def fetch_ensemble_highs(
    lat: float,
    lon: float,
    timezone: str,
    forecast_days: int = 3,
    sigma_floor: float = 0.5,
    sigma_scale: float = 1.0,
    timeout: float = 30.0,
) -> list[TempForecast]:
    """
    Open-Meteo GFS ensemble daily max temps (°F) → N(μ, σ) via p10/p50/p90.
    σ = max((p90 - p10) / 2.56, sigma_floor) * sigma_scale
    """
    url = "https://ensemble-api.open-meteo.com/v1/ensemble"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": timezone,
        "forecast_days": forecast_days,
        "models": "gfs_seamless",
    }
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    daily: dict[str, Any] = data.get("daily") or {}
    times: list[str] = daily.get("time") or []
    if not times:
        return []

    member_keys = [
        k
        for k in daily.keys()
        if k.startswith("temperature_2m_max_member") or k == "temperature_2m_max"
    ]
    # Prefer members only for spread; include control as a member if present
    if not member_keys:
        raise RuntimeError("No ensemble temperature members returned from Open-Meteo")

    out: list[TempForecast] = []
    for i, day in enumerate(times):
        members: list[float] = []
        for key in member_keys:
            series = daily.get(key) or []
            if i < len(series) and series[i] is not None:
                members.append(float(series[i]))
        if len(members) < 3:
            continue
        p10 = _percentile(members, 10)
        p50 = _percentile(members, 50)
        p90 = _percentile(members, 90)
        sigma_raw = max((p90 - p10) / 2.56, sigma_floor) * sigma_scale
        sigma = max(sigma_raw, 0.1)
        out.append(
            TempForecast(
                target_date=day,
                mu=p50,
                sigma=sigma,
                p10=p10,
                p50=p50,
                p90=p90,
                source="open_meteo_gfs_ensemble",
                members=members,
            )
        )
    return out


def fetch_historical_highs(
    lat: float,
    lon: float,
    timezone: str,
    start_date: str,
    end_date: str,
    sigma_floor: float = 1.5,
    sigma_scale: float = 1.35,
    timeout: float = 30.0,
) -> dict[str, TempForecast]:
    """
    Open-Meteo historical forecast daily max (°F) for backtests.
    Uses point forecast as μ; σ from configured floor/scale (no archived ensemble members).
    """
    url = "https://historical-forecast-api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": timezone,
    }
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    daily = resp.json().get("daily") or {}
    times = daily.get("time") or []
    vals = daily.get("temperature_2m_max") or []
    sigma = max(float(sigma_floor) * float(sigma_scale), 0.5)
    out: dict[str, TempForecast] = {}
    for i, day in enumerate(times):
        if i >= len(vals) or vals[i] is None:
            continue
        mu = float(vals[i])
        out[day] = TempForecast(
            target_date=day,
            mu=mu,
            sigma=sigma,
            p10=mu - 1.28 * sigma,
            p50=mu,
            p90=mu + 1.28 * sigma,
            source="open_meteo_historical_forecast",
            members=[mu],
        )
    return out
