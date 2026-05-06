"""Tests for the watchlist merge logic in ``PortfolioValuatorCoordinator``."""
from __future__ import annotations

from custom_components.portfolio_valuator.coordinator import (
    PortfolioValuatorCoordinator,
)


def test_merge_watchlist_keeps_live_price_from_ws() -> None:
    """REST watchlist has no ``price``; WS-cached price must survive the merge."""
    rest = [
        {"id": 1, "instrument_code": "BTCUSD", "label": "BTC"},
        {"id": 2, "instrument_code": "ETHUSD", "label": "ETH"},
    ]
    ws = [
        {"id": 1, "instrument_code": "BTCUSD", "price": 50000.0, "price_source": "bitfinex"},
    ]
    out = PortfolioValuatorCoordinator._merge_watchlist(rest, ws)
    by_code = {it["instrument_code"]: it for it in out}
    assert by_code["BTCUSD"]["price"] == 50000.0
    assert by_code["BTCUSD"]["price_source"] == "bitfinex"
    # No live data available for ETH -> stays None / missing.
    assert by_code["ETHUSD"].get("price") is None


def test_merge_watchlist_does_not_overwrite_existing_price() -> None:
    """REST already provided a price -> WS cache must not clobber it."""
    rest = [
        {"id": 1, "instrument_code": "BTCUSD", "price": 49000.0, "price_source": "rest"},
    ]
    ws = [
        {"id": 1, "instrument_code": "BTCUSD", "price": 50000.0, "price_source": "bitfinex"},
    ]
    out = PortfolioValuatorCoordinator._merge_watchlist(rest, ws)
    assert out[0]["price"] == 49000.0
    assert out[0]["price_source"] == "rest"


def test_merge_watchlist_handles_none_ws() -> None:
    rest = [{"id": 1, "instrument_code": "X"}]
    assert PortfolioValuatorCoordinator._merge_watchlist(rest, None) == rest


def test_merge_watchlist_handles_none_rest() -> None:
    ws = [{"id": 1, "instrument_code": "X", "price": 1.0}]
    assert PortfolioValuatorCoordinator._merge_watchlist(None, ws) == []
