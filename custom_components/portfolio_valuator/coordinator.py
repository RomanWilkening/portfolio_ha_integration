"""DataUpdateCoordinator for the Portfolio Valuator integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PortfolioValuatorAuthError,
    PortfolioValuatorClient,
    PortfolioValuatorConnectionError,
)
from .const import (
    CONF_REST_FALLBACK,
    CONF_SCAN_INTERVAL,
    DEFAULT_REST_FALLBACK,
    DOMAIN,
    SCAN_INTERVAL_SECONDS,
    SIGNAL_STRUCTURE_CHANGED,
    SIGNAL_UPDATE,
)

_LOGGER = logging.getLogger(__name__)


class PortfolioValuatorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates REST polling and live WebSocket updates.

    ``data`` shape::

        {
            "valuations": [ ... portfolio dicts ... ],
            "watchlist":  [ ... watchlist item dicts ... ],
            "fx_rates":   [ ... fx rate dicts ... ],
            "ws_connected": bool,
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PortfolioValuatorClient,
    ) -> None:
        scan = int(entry.options.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL_SECONDS))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=scan),
        )
        self.entry = entry
        self.client = client
        self.rest_fallback: bool = bool(
            entry.options.get(CONF_REST_FALLBACK, DEFAULT_REST_FALLBACK)
        )
        self.service_version: str | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_connected: bool = False
        self.data = {
            "valuations": [],
            "watchlist": [],
            "fx_rates": [],
            "ws_connected": False,
        }

    # --------------------------------------------------------------- Polling
    async def _async_update_data(self) -> dict[str, Any]:
        """REST poll. Used as fallback and for initial load. Skipped while WS live."""
        # If WebSocket is delivering data we still poll occasionally for watchlist /
        # fx_rates because not every Valuator version pushes those on every tick —
        # unless the user explicitly disabled REST fallback.
        if (
            self._ws_connected
            and self.data
            and self.data.get("valuations")
            and not self.rest_fallback
        ):
            return self.data
        try:
            valuations, watchlist, fx_rates = await asyncio.gather(
                self.client.async_get_valuations(),
                self.client.async_get_watchlist(),
                self.client.async_get_fx_rates(),
            )
        except PortfolioValuatorAuthError as err:
            raise UpdateFailed(f"Auth failed: {err}") from err
        except PortfolioValuatorConnectionError as err:
            # If WS is up we don't want REST failure to nuke the data.
            if self._ws_connected and self.data:
                _LOGGER.debug("REST poll failed while WS live, keeping cached: %s", err)
                return self.data
            raise UpdateFailed(str(err)) from err

        merged = dict(self.data or {})
        merged.update(
            {
                "valuations": valuations or [],
                "watchlist": self._merge_watchlist(watchlist, merged.get("watchlist")),
                "fx_rates": fx_rates or [],
                "ws_connected": self._ws_connected,
            }
        )
        return merged

    @staticmethod
    def _merge_watchlist(
        from_rest: list[dict[str, Any]] | None,
        from_ws: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """REST watchlist lacks live ``price``/``price_source``. Preserve them from WS."""
        rest = list(from_rest or [])
        if not from_ws:
            return rest
        by_code: dict[str, dict[str, Any]] = {}
        for it in from_ws:
            code = it.get("instrument_code") or it.get("key")
            if code:
                by_code[code] = it
        for it in rest:
            code = it.get("instrument_code") or it.get("key")
            live = by_code.get(code) if code else None
            if live:
                # only fill what REST does not provide
                for fld in ("price", "price_source", "field"):
                    if it.get(fld) is None and live.get(fld) is not None:
                        it[fld] = live[fld]
        return rest

    # ------------------------------------------------------------- WebSocket
    async def async_start_ws(self) -> None:
        """Start the background WebSocket task."""
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = self.hass.async_create_background_task(
            self.client.async_run_ws(self._on_ws_message, self._on_ws_state),
            name=f"{DOMAIN}_ws_{self.entry.entry_id}",
        )

    async def async_stop_ws(self) -> None:
        await self.client.async_stop()
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._ws_task = None

    async def async_restart_ws(self) -> None:
        """Stop and restart the WebSocket consumer."""
        await self.async_stop_ws()
        # Reset the client's stop event so a new run can begin.
        self.client.reset()
        await self.async_start_ws()

    async def _on_ws_state(self, connected: bool) -> None:
        self._ws_connected = connected
        data = dict(self.data or {})
        data["ws_connected"] = connected
        self.async_set_updated_data(data)

    async def _on_ws_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        data = dict(self.data or {})
        changed = False

        if mtype == "snapshot":
            if "valuations" in msg:
                data["valuations"] = msg.get("valuations") or []
                changed = True
            if "watchlist" in msg:
                data["watchlist"] = msg.get("watchlist") or []
                changed = True
            if "fx_rates" in msg:
                data["fx_rates"] = msg.get("fx_rates") or []
                changed = True
        elif mtype == "valuations":
            if "valuations" in msg:
                data["valuations"] = msg.get("valuations") or []
                changed = True
            if "fx_rates" in msg:
                data["fx_rates"] = msg.get("fx_rates") or []
                changed = True
            if "watchlist" in msg:
                data["watchlist"] = msg.get("watchlist") or []
                changed = True
        elif mtype == "quote":
            # Lightweight per-item update; merge into existing watchlist if matched.
            key = msg.get("key") or msg.get("instrument_code")
            price = msg.get("price")
            if key and price is not None:
                wl = list(data.get("watchlist") or [])
                for it in wl:
                    if (it.get("instrument_code") or it.get("key")) == key:
                        it["price"] = price
                        if msg.get("source"):
                            it["price_source"] = msg["source"]
                        changed = True
                data["watchlist"] = wl
        elif mtype == "structure_changed":
            # Server signals that portfolios/watchlist/fx structure changed.
            # Trigger a REST refresh and tell the integration to reload entities.
            async_dispatcher_send(self.hass, f"{SIGNAL_STRUCTURE_CHANGED}_{self.entry.entry_id}")
            self.hass.async_create_task(self.async_request_refresh())
            return
        elif mtype == "status":
            # informational only
            return

        if changed:
            data["ws_connected"] = self._ws_connected
            self.async_set_updated_data(data)
            async_dispatcher_send(self.hass, f"{SIGNAL_UPDATE}_{self.entry.entry_id}")
