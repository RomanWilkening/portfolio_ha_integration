"""The Portfolio Valuator integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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

PLATFORMS: list[Platform] = [Platform.SENSOR]

_SERVICE_SCHEMA = vol.Schema(
    {vol.Optional("entry_id"): cv.string},
    extra=vol.ALLOW_EXTRA,
)


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
