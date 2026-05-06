"""HTTP/WebSocket client for the Portfolio Valuator service."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import aiohttp
from aiohttp import ClientTimeout, WSMsgType

from .const import (
    WS_BACKOFF_INITIAL,
    WS_BACKOFF_MAX,
    WS_HEARTBEAT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class PortfolioValuatorAuthError(Exception):
    """Raised when the Valuator rejects the API token."""


class PortfolioValuatorConnectionError(Exception):
    """Raised when the Valuator cannot be reached."""


class PortfolioValuatorClient:
    """Async client for the Portfolio Valuator REST + WebSocket API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        use_ssl: bool = False,
        api_token: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._host = host
        self._port = int(port)
        self._use_ssl = bool(use_ssl)
        self._api_token = (api_token or "").strip() or None
        self._verify_ssl = bool(verify_ssl)

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ URLs
    @property
    def base_url(self) -> str:
        scheme = "https" if self._use_ssl else "http"
        return f"{scheme}://{self._host}:{self._port}"

    @property
    def ws_url(self) -> str:
        scheme = "wss" if self._use_ssl else "ws"
        url = f"{scheme}://{self._host}:{self._port}/ws"
        if self._api_token:
            url += f"?api_key={self._api_token}"
        return url

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self._api_token:
            h["X-API-Key"] = self._api_token
        return h

    # ----------------------------------------------------------------- REST
    async def _get_json(self, path: str, timeout: float = 15.0) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                timeout=ClientTimeout(total=timeout),
                ssl=self._verify_ssl if self._use_ssl else None,
            ) as resp:
                if resp.status == 401:
                    raise PortfolioValuatorAuthError("Invalid API token")
                if resp.status >= 400:
                    text = await resp.text()
                    raise PortfolioValuatorConnectionError(
                        f"GET {path} -> HTTP {resp.status}: {text[:200]}"
                    )
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PortfolioValuatorConnectionError(str(err)) from err

    async def async_test_connection(self) -> dict[str, Any]:
        """Verify the Valuator is reachable. Tries /api/health, falls back."""
        try:
            return await self._get_json("/api/health", timeout=10.0)
        except PortfolioValuatorAuthError:
            raise
        except PortfolioValuatorConnectionError:
            # Older Valuator versions: /api/health may be missing.
            await self._get_json("/api/portfolios", timeout=10.0)
            return {"status": "ok", "legacy": True}

    async def async_get_portfolios(self) -> list[dict[str, Any]]:
        return await self._get_json("/api/portfolios")

    async def async_get_watchlist(self) -> list[dict[str, Any]]:
        return await self._get_json("/api/watchlist")

    async def async_get_valuations(self) -> list[dict[str, Any]]:
        return await self._get_json("/api/portfolios/valuations", timeout=30.0)

    async def async_get_fx_rates(self) -> list[dict[str, Any]]:
        return await self._get_json("/api/fx-rates")

    # ------------------------------------------------------------ WebSocket
    async def async_run_ws(
        self,
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        on_state: Callable[[bool], Awaitable[None]] | None = None,
    ) -> None:
        """Run the WebSocket consumer loop with reconnect backoff.

        Calls ``on_message`` for each parsed JSON frame.
        Calls ``on_state(True)`` when connected, ``on_state(False)`` when lost.
        Returns when ``async_stop`` is called.
        """
        backoff = WS_BACKOFF_INITIAL
        while not self._stop.is_set():
            try:
                _LOGGER.debug("Connecting WebSocket: %s", self.ws_url)
                async with self._session.ws_connect(
                    self.ws_url,
                    headers=self._headers(),
                    heartbeat=25,
                    timeout=ClientTimeout(total=15),
                    ssl=self._verify_ssl if self._use_ssl else None,
                ) as ws:
                    self._ws = ws
                    backoff = WS_BACKOFF_INITIAL
                    if on_state is not None:
                        await on_state(True)
                    _LOGGER.info(
                        "Portfolio Valuator WebSocket connected (%s)", self.base_url
                    )
                    async for msg in ws:
                        if self._stop.is_set():
                            break
                        if msg.type == WSMsgType.TEXT:
                            try:
                                data = msg.json()
                            except ValueError:
                                _LOGGER.debug("Non-JSON WS frame ignored")
                                continue
                            mtype = (data or {}).get("type")
                            if mtype == "ping":
                                # Reply to server-initiated ping.
                                try:
                                    await ws.send_json({"type": "pong"})
                                except Exception:  # noqa: BLE001
                                    pass
                                continue
                            if mtype == "pong":
                                continue
                            try:
                                await on_message(data)
                            except Exception:  # noqa: BLE001
                                _LOGGER.exception("Error handling WS message")
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as err:
                if err.status == 401:
                    _LOGGER.error("WebSocket auth rejected (HTTP 401) – check token")
                    if on_state is not None:
                        await on_state(False)
                    # Wait a long time before retrying auth failures.
                    await self._sleep(min(WS_BACKOFF_MAX, 60))
                    continue
                _LOGGER.warning("WebSocket handshake failed: %s", err)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.warning("WebSocket connection error: %s", err)

            # disconnected
            self._ws = None
            if on_state is not None:
                try:
                    await on_state(False)
                except Exception:  # noqa: BLE001
                    pass
            if self._stop.is_set():
                break
            _LOGGER.debug("Reconnecting WebSocket in %ds", backoff)
            await self._sleep(backoff)
            backoff = min(WS_BACKOFF_MAX, max(WS_BACKOFF_INITIAL, backoff * 2))

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def async_stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "PortfolioValuatorClient",
    "PortfolioValuatorAuthError",
    "PortfolioValuatorConnectionError",
]
