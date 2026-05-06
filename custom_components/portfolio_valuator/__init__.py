"""The Portfolio Valuator integration."""
from __future__ import annotations

import logging
import os

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType

from .api import PortfolioValuatorAuthError, PortfolioValuatorClient
from .const import (
    CONF_API_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    ISSUE_AUTH_FAILED,
    SERVICE_FORCE_REFRESH,
    SERVICE_RESTART_STREAM,
    SIGNAL_STRUCTURE_CHANGED,
)
from .coordinator import PortfolioValuatorCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_SERVICE_SCHEMA = vol.Schema(
    {vol.Optional("entry_id"): cv.string},
    extra=vol.ALLOW_EXTRA,
)

# URL prefix under which we expose the bundled Lovelace card. Anything inside
# ``custom_components/portfolio_valuator/www/`` is served from this path.
_FRONTEND_URL_PREFIX = f"/{DOMAIN}_frontend"
_FRONTEND_CARD_FILE = "portfolio-valuator-card.js"

# Sidebar panel URL path (visible in the URL bar as ``/portfolio-valuator``).
_PANEL_URL_PATH = "portfolio-valuator"
_PANEL_REGISTERED_KEY = f"{DOMAIN}_panel_registered"
_STATIC_REGISTERED_KEY = f"{DOMAIN}_static_registered"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """One-time setup: register bundled frontend assets and the sidebar panel."""
    await _async_register_frontend_resources(hass)
    await _async_register_panel(hass)
    return True


async def _async_register_frontend_resources(hass: HomeAssistant) -> None:
    """Serve the bundled card under a stable URL and register it in Lovelace.

    Idempotent — safe to call from both ``async_setup`` and
    ``async_setup_entry``.

    The Lovelace resource auto-registration only works for users on Lovelace
    "storage" mode. YAML-mode users still need to add the resource manually
    (the README documents that). The static path itself is always available.

    The same JS module also exports a custom element used by the bundled
    sidebar panel (see ``_async_register_panel``).
    """
    if hass.data.get(_STATIC_REGISTERED_KEY):
        return

    www_dir = os.path.join(os.path.dirname(__file__), "www")
    card_file = os.path.join(www_dir, _FRONTEND_CARD_FILE)
    if not os.path.isfile(card_file):
        _LOGGER.debug("Frontend card not found at %s — skipping", card_file)
        return

    # Try the modern API first; fall back to the legacy one for older HA cores.
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(_FRONTEND_URL_PREFIX, www_dir, cache_headers=False)]
        )
    except Exception:  # noqa: BLE001
        try:
            hass.http.register_static_path(  # type: ignore[attr-defined]
                _FRONTEND_URL_PREFIX, www_dir, cache_headers=False
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Could not register static frontend path", exc_info=True
            )
            return

    hass.data[_STATIC_REGISTERED_KEY] = True

    # Best-effort auto-registration in the Lovelace dashboard resources list.
    try:
        from homeassistant.components.lovelace import (  # noqa: WPS433
            CONF_RESOURCES,
        )
        from homeassistant.components.lovelace.resources import (  # noqa: WPS433
            ResourceStorageCollection,
        )

        lovelace = hass.data.get("lovelace")
        if lovelace is None:
            return
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            return
        # Only the storage-backed collection supports programmatic add.
        if not isinstance(resources, ResourceStorageCollection):
            return
        if not resources.loaded:
            await resources.async_load()
        url = f"{_FRONTEND_URL_PREFIX}/{_FRONTEND_CARD_FILE}"
        existing = {
            (r.get("url") or "").split("?")[0]
            for r in (resources.async_items() or [])
        }
        if url in existing:
            return
        await resources.async_create_item({CONF_RESOURCES: url, "res_type": "module"})
        _LOGGER.info("Registered Lovelace resource %s", url)
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "Could not auto-register Lovelace resource — add it manually",
            exc_info=True,
        )


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Auto-register the bundled sidebar panel.

    The panel is shared by all configured Portfolio Valuator instances and is
    self-discovering — it walks ``hass.states`` for every entity tagged with
    ``attributes.integration == "portfolio_valuator"``. Users get a
    zero-configuration overview that lists portfolios, positions, watchlist
    items, FX rates and the live WebSocket status as soon as the integration
    is loaded.
    """
    if hass.data.get(_PANEL_REGISTERED_KEY):
        return

    try:
        from homeassistant.components import panel_custom  # noqa: WPS433
    except Exception:  # noqa: BLE001
        _LOGGER.debug("panel_custom unavailable — skipping panel registration")
        return

    module_url = f"{_FRONTEND_URL_PREFIX}/{_FRONTEND_CARD_FILE}"
    try:
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name="portfolio-valuator-panel",
            frontend_url_path=_PANEL_URL_PATH,
            module_url=module_url,
            sidebar_title="Portfolios",
            sidebar_icon="mdi:chart-line",
            embed_iframe=False,
            require_admin=False,
            config={},
        )
        hass.data[_PANEL_REGISTERED_KEY] = True
        _LOGGER.info(
            "Registered Portfolio Valuator sidebar panel at /%s", _PANEL_URL_PATH
        )
    except ValueError:
        # ``async_register_panel`` raises ValueError if the URL path is
        # already taken (e.g. after a config-entry reload). Treat as success.
        hass.data[_PANEL_REGISTERED_KEY] = True
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not register sidebar panel", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Portfolio Valuator from a config entry."""
    session = async_get_clientsession(hass)
    client = PortfolioValuatorClient(
        session=session,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        use_ssl=entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL),
        api_token=entry.data.get(CONF_API_TOKEN) or None,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    coordinator = PortfolioValuatorCoordinator(hass, entry, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except PortfolioValuatorAuthError as err:
        _async_create_auth_issue(hass, entry)
        raise ConfigEntryAuthFailed(str(err)) from err

    # Optional /api/version probe — purely informational, must never fail setup.
    try:
        version = await client.async_get_version()
    except PortfolioValuatorAuthError:
        _async_create_auth_issue(hass, entry)
        raise ConfigEntryAuthFailed("Auth rejected during version probe")
    except Exception:  # noqa: BLE001
        version = None
    if version:
        coordinator.service_version = str(version.get("version") or version)
        _LOGGER.info(
            "Portfolio Valuator service version: %s", coordinator.service_version
        )

    # Setup succeeded -> clear any stale auth-repair issue.
    _async_clear_auth_issue(hass, entry)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Make sure frontend assets and the sidebar panel are available even if
    # ``async_setup`` ran before ``frontend`` / ``panel_custom`` were ready.
    await _async_register_frontend_resources(hass)
    await _async_register_panel(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start live WebSocket after platforms are up so they can react immediately.
    await coordinator.async_start_ws()

    # Reload entry when structure changes (portfolios/positions/watchlist added/removed).
    async def _on_structure_changed() -> None:
        _LOGGER.debug("Structure changed -> scheduling entry reload")
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_STRUCTURE_CHANGED}_{entry.entry_id}",
            _on_structure_changed,
        )
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)

    return True


def _async_create_auth_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create a HA repair issue suggesting the user re-enters the API token."""
    try:
        from homeassistant.helpers import issue_registry as ir

        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_AUTH_FAILED}_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_AUTH_FAILED,
            translation_placeholders={
                "host": entry.data.get(CONF_HOST, ""),
                "title": entry.title,
            },
        )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not create auth repair issue", exc_info=True)


def _async_clear_auth_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    try:
        from homeassistant.helpers import issue_registry as ir

        ir.async_delete_issue(
            hass, DOMAIN, f"{ISSUE_AUTH_FAILED}_{entry.entry_id}"
        )
    except Exception:  # noqa: BLE001
        pass


def _coordinators_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> list[PortfolioValuatorCoordinator]:
    store: dict[str, PortfolioValuatorCoordinator] = hass.data.get(DOMAIN, {})
    target_entry = call.data.get("entry_id")
    if target_entry:
        coord = store.get(target_entry)
        if coord is None:
            raise ServiceValidationError(
                f"No Portfolio Valuator config entry with id '{target_entry}'"
            )
        return [coord]
    if not store:
        raise ServiceValidationError("No Portfolio Valuator config entries are loaded")
    return list(store.values())


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services exactly once."""
    if hass.services.has_service(DOMAIN, SERVICE_FORCE_REFRESH):
        return

    async def _force_refresh(call: ServiceCall) -> None:
        for coord in _coordinators_for_call(hass, call):
            await coord.async_request_refresh()

    async def _restart_stream(call: ServiceCall) -> None:
        for coord in _coordinators_for_call(hass, call):
            await coord.async_restart_ws()

    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_REFRESH, _force_refresh, schema=_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTART_STREAM, _restart_stream, schema=_SERVICE_SCHEMA
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: PortfolioValuatorCoordinator | None = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id)
    )
    if coordinator is not None:
        await coordinator.async_stop_ws()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        store = hass.data.get(DOMAIN, {})
        store.pop(entry.entry_id, None)
        # Last entry gone -> drop services so they don't dangle.
        if not store and hass.services.has_service(DOMAIN, SERVICE_FORCE_REFRESH):
            hass.services.async_remove(DOMAIN, SERVICE_FORCE_REFRESH)
            hass.services.async_remove(DOMAIN, SERVICE_RESTART_STREAM)
    return unload_ok
