"""Constants for the Portfolio Valuator integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "portfolio_valuator"

CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_USE_SSL: Final = "use_ssl"
CONF_API_TOKEN: Final = "api_token"
CONF_VERIFY_SSL: Final = "verify_ssl"

DEFAULT_PORT: Final = 8000
DEFAULT_USE_SSL: Final = False
DEFAULT_VERIFY_SSL: Final = True

# Polling fallback when WebSocket cannot be established.
SCAN_INTERVAL_SECONDS: Final = 60

# WebSocket reconnect backoff
WS_BACKOFF_INITIAL: Final = 2
WS_BACKOFF_MAX: Final = 60
WS_HEARTBEAT_TIMEOUT: Final = 90  # seconds without any frame -> assume dead

MANUFACTURER: Final = "Portfolio Valuator"
MODEL_PORTFOLIO: Final = "Portfolio"
MODEL_WATCHLIST_ITEM: Final = "Watchlist Item"
MODEL_FX: Final = "FX Rates"

# Dispatcher signals
SIGNAL_UPDATE: Final = f"{DOMAIN}_update"
SIGNAL_STRUCTURE_CHANGED: Final = f"{DOMAIN}_structure_changed"
