"""Binary sensor platform for Portfolio Valuator.

Exposes a single connectivity entity per config entry that mirrors the live
WebSocket state. ``device_class=connectivity`` makes it appear with the standard
„connected / disconnected" UI in Home Assistant.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import PortfolioValuatorCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PortfolioValuatorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PortfolioValuatorWsConnected(coordinator, entry.entry_id)])


class PortfolioValuatorWsConnected(
    CoordinatorEntity[PortfolioValuatorCoordinator], BinarySensorEntity
):
    """`on` while the WebSocket consumer is delivering frames."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "ws_connected"
    _attr_name = "WebSocket connected"

    def __init__(
        self, coordinator: PortfolioValuatorCoordinator, entry_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_ws_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_service")},
            name="Portfolio Valuator service",
            manufacturer=MANUFACTURER,
            model="Service",
            sw_version=getattr(coordinator, "service_version", None),
        )

    @property
    def is_on(self) -> bool:
        data: dict[str, Any] = self.coordinator.data or {}
        return bool(data.get("ws_connected"))

    @property
    def available(self) -> bool:
        # Always available — the whole point of a connectivity sensor is to
        # signal "off" when the upstream link is gone.
        return True
