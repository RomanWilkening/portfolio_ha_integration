"""Config flow for the Portfolio Valuator integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    PortfolioValuatorAuthError,
    PortfolioValuatorClient,
    PortfolioValuatorConnectionError,
)
from .const import (
    CONF_API_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_REST_FALLBACK,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_REST_FALLBACK,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=d.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=d.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Optional(CONF_USE_SSL, default=d.get(CONF_USE_SSL, DEFAULT_USE_SSL)): bool,
            vol.Optional(
                CONF_VERIFY_SSL, default=d.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            ): bool,
            vol.Optional(CONF_API_TOKEN, default=d.get(CONF_API_TOKEN, "")): str,
        }
    )


class PortfolioValuatorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Portfolio Valuator."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            use_ssl = bool(user_input.get(CONF_USE_SSL, DEFAULT_USE_SSL))
            verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
            token = (user_input.get(CONF_API_TOKEN) or "").strip()

            unique_id = f"{host}:{port}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = PortfolioValuatorClient(
                session=session,
                host=host,
                port=port,
                use_ssl=use_ssl,
                api_token=token or None,
                verify_ssl=verify_ssl,
            )
            try:
                await client.async_test_connection()
            except PortfolioValuatorAuthError:
                errors["base"] = "invalid_auth"
            except PortfolioValuatorConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Portfolio Valuator ({host}:{port})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_USE_SSL: use_ssl,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_API_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PortfolioValuatorOptionsFlow(config_entry)

    # ------------------------------------------------------------------ reauth
    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered by ``ConfigEntryAuthFailed`` — ask the user for a new token."""
        # ``self.context`` already carries ``entry_id`` set by HA core.
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        if entry is None:
            return self.async_abort(reason="reauth_unknown_entry")

        if user_input is not None:
            token = (user_input.get(CONF_API_TOKEN) or "").strip()
            session = async_get_clientsession(self.hass)
            client = PortfolioValuatorClient(
                session=session,
                host=entry.data[CONF_HOST],
                port=entry.data[CONF_PORT],
                use_ssl=entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL),
                api_token=token or None,
                verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            try:
                await client.async_test_connection()
            except PortfolioValuatorAuthError:
                errors["base"] = "invalid_auth"
            except PortfolioValuatorConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                new_data = {**entry.data, CONF_API_TOKEN: token}
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Optional(CONF_API_TOKEN, default=""): str}
            ),
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )


class PortfolioValuatorOptionsFlow(OptionsFlow):
    """Options flow: token, scan interval, REST fallback, SSL verification."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Note: do NOT store ``self.config_entry =`` directly — HA exposes it
        # via ``self.config_entry`` automatically in newer versions and warns
        # about overriding it. We keep a reference under a different name.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self._entry.data, **self._entry.options}

        if user_input is not None:
            scan = int(user_input.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL_SECONDS))
            if not MIN_SCAN_INTERVAL_SECONDS <= scan <= MAX_SCAN_INTERVAL_SECONDS:
                errors[CONF_SCAN_INTERVAL] = "scan_interval_out_of_range"
            else:
                # API token can also be (re-)set via options; persist it into
                # ``data`` so it survives reloads even if options later get cleared.
                new_data = dict(self._entry.data)
                token = (user_input.get(CONF_API_TOKEN) or "").strip()
                new_data[CONF_API_TOKEN] = token
                new_data[CONF_VERIFY_SSL] = bool(
                    user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                )
                self.hass.config_entries.async_update_entry(
                    self._entry, data=new_data
                )
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SCAN_INTERVAL: scan,
                        CONF_REST_FALLBACK: bool(
                            user_input.get(CONF_REST_FALLBACK, DEFAULT_REST_FALLBACK)
                        ),
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_API_TOKEN, default=current.get(CONF_API_TOKEN, "")
                ): str,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL_SECONDS),
                ): int,
                vol.Optional(
                    CONF_REST_FALLBACK,
                    default=current.get(CONF_REST_FALLBACK, DEFAULT_REST_FALLBACK),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
