"""Diagnostics support for Portfolio Valuator."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_TOKEN, CONF_HOST, DOMAIN
from .coordinator import PortfolioValuatorCoordinator

# Keys that must never appear in a downloaded diagnostics file.
_REDACT_ENTRY = {CONF_API_TOKEN, CONF_HOST}
# Position-level fields can be PII (custom names). Redact identifiers / monetary
# values are useful, names should be obfuscated.
_REDACT_DATA = {"isin", "instrument_isin"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: PortfolioValuatorCoordinator | None = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id)
    )
    data = (coordinator.data if coordinator else None) or {}
    valuations = data.get("valuations") or []
    watchlist = data.get("watchlist") or []
    fx_rates = data.get("fx_rates") or []

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), _REDACT_ENTRY),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "service": {
            "version": getattr(coordinator, "service_version", None),
            "ws_connected": data.get("ws_connected"),
        },
        "counts": {
            "portfolios": len(valuations),
            "positions": sum(len((v or {}).get("positions") or []) for v in valuations),
            "watchlist": len(watchlist),
            "fx_rates": len(fx_rates),
        },
        # Snapshot of the most recent payload (redacted).
        "snapshot": {
            "valuations": async_redact_data(valuations, _REDACT_DATA),
            "watchlist": async_redact_data(watchlist, _REDACT_DATA),
            "fx_rates": async_redact_data(fx_rates, _REDACT_DATA),
        },
    }
