"""Tests for ``PortfolioValuatorClient`` URL/header construction."""
from __future__ import annotations

from custom_components.portfolio_valuator.api import PortfolioValuatorClient


class _DummySession:
    """Stand-in for ``aiohttp.ClientSession`` — never actually used here."""


def _make_client(**kwargs) -> PortfolioValuatorClient:
    defaults = dict(
        session=_DummySession(),
        host="valuator.local",
        port=8000,
        use_ssl=False,
        api_token=None,
        verify_ssl=True,
    )
    defaults.update(kwargs)
    return PortfolioValuatorClient(**defaults)


def test_base_url_http() -> None:
    client = _make_client()
    assert client.base_url == "http://valuator.local:8000"


def test_base_url_https() -> None:
    client = _make_client(use_ssl=True, port=443)
    assert client.base_url == "https://valuator.local:443"


def test_ws_url_without_token() -> None:
    client = _make_client()
    assert client.ws_url == "ws://valuator.local:8000/ws"


def test_ws_url_includes_token_query() -> None:
    client = _make_client(use_ssl=True, port=8443, api_token="s3cret")
    assert client.ws_url == "wss://valuator.local:8443/ws?api_key=s3cret"


def test_headers_include_token() -> None:
    client = _make_client(api_token="abc")
    headers = client._headers()  # internal helper, kept stable on purpose
    assert headers["X-API-Key"] == "abc"
    assert headers["Accept"] == "application/json"


def test_headers_no_token() -> None:
    client = _make_client()
    headers = client._headers()
    assert "X-API-Key" not in headers


def test_blank_token_treated_as_none() -> None:
    client = _make_client(api_token="   ")
    assert "?api_key=" not in client.ws_url
    assert "X-API-Key" not in client._headers()
