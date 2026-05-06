"""Config flow for the Portfolio Valuator integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
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
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
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
