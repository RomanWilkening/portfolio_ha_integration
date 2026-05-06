"""Sensor platform for Portfolio Valuator."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    MANUFACTURER,
    MODEL_FX,
    MODEL_PORTFOLIO,
    MODEL_WATCHLIST_ITEM,
    SIGNAL_UPDATE,
)
from .coordinator import PortfolioValuatorCoordinator

_LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------- helpers
def _device_for_portfolio(
    entry_id: str,
    pf: dict[str, Any],
    sw_version: str | None = None,
) -> DeviceInfo:
    pid = pf.get("id")
    name = pf.get("name") or f"Portfolio {pid}"
    info: DeviceInfo = DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_portfolio_{pid}")},
        name=f"Portfolio: {name}",
        manufacturer=MANUFACTURER,
        model=MODEL_PORTFOLIO,
    )
    if sw_version:
        info["sw_version"] = sw_version
    return info


def _device_for_watch_item(entry_id: str, item: dict[str, Any]) -> DeviceInfo:
    iid = item.get("id")
    label = (
        item.get("label")
        or item.get("instrument_name")
        or item.get("instrument_code")
        or f"Item {iid}"
    )
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_watch_{iid}")},
        name=f"Watchlist: {label}",
        manufacturer=MANUFACTURER,
        model=MODEL_WATCHLIST_ITEM,
    )


def _device_for_fx(entry_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_fx")},
        name="Portfolio Valuator – FX Rates",
        manufacturer=MANUFACTURER,
        model=MODEL_FX,
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return dt_util.parse_datetime(str(value))
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ entry point
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PortfolioValuatorCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}

    entities: list[SensorEntity] = []

    # Portfolio + position sensors
    for pf_val in data.get("valuations", []) or []:
        entities.extend(_build_portfolio_entities(coordinator, entry.entry_id, pf_val))

    # Watchlist sensors
    for item in data.get("watchlist", []) or []:
        entities.append(WatchlistPriceSensor(coordinator, entry.entry_id, item))

    # FX rate sensors
    for fx in data.get("fx_rates", []) or []:
        entities.append(FxRateSensor(coordinator, entry.entry_id, fx))

    async_add_entities(entities)


def _build_portfolio_entities(
    coordinator: PortfolioValuatorCoordinator,
    entry_id: str,
    pf_val: dict[str, Any],
) -> list[SensorEntity]:
    pf = pf_val.get("portfolio") or {}
    pid = pf.get("id")
    if pid is None:
        return []
    currency = pf_val.get("currency") or pf.get("currency") or "EUR"

    entities: list[SensorEntity] = [
        PortfolioTotalSensor(
            coordinator, entry_id, pid, "market_value",
            "Market Value", currency, SensorDeviceClass.MONETARY,
        ),
        PortfolioTotalSensor(
            coordinator, entry_id, pid, "cost_basis",
            "Cost Basis", currency, SensorDeviceClass.MONETARY,
        ),
        PortfolioTotalSensor(
            coordinator, entry_id, pid, "pnl",
            "Profit / Loss", currency, SensorDeviceClass.MONETARY,
        ),
        PortfolioPnlPctSensor(coordinator, entry_id, pid),
        PortfolioValuedAtSensor(coordinator, entry_id, pid),
    ]
    for pos in pf_val.get("positions", []) or []:
        pos_id = pos.get("id")
        if pos_id is None:
            continue
        pos_currency = pos.get("currency") or pos.get("position_currency") or currency
        entities.extend(
            [
                PositionPriceSensor(coordinator, entry_id, pid, pos_id, pos_currency),
                PositionMarketValueSensor(coordinator, entry_id, pid, pos_id, currency),
                PositionPnlSensor(coordinator, entry_id, pid, pos_id, currency),
                PositionPnlPctSensor(coordinator, entry_id, pid, pos_id),
            ]
        )
    return entities


# =============================================================== base class
class _PVBase(CoordinatorEntity[PortfolioValuatorCoordinator], SensorEntity):
    """Common base: subscribes to push-update dispatcher signals."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: PortfolioValuatorCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATE}_{self._entry_id}",
                self._handle_dispatch,
            )
        )

    @callback
    def _handle_dispatch(self) -> None:
        self.async_write_ha_state()


# =============================================================== Portfolio totals
def _find_portfolio(
    coordinator: PortfolioValuatorCoordinator, portfolio_id: int
) -> dict[str, Any] | None:
    for pf in (coordinator.data or {}).get("valuations", []) or []:
        if (pf.get("portfolio") or {}).get("id") == portfolio_id:
            return pf
    return None


def _find_position(
    coordinator: PortfolioValuatorCoordinator, portfolio_id: int, position_id: int
) -> dict[str, Any] | None:
    pf = _find_portfolio(coordinator, portfolio_id)
    if not pf:
        return None
    for pos in pf.get("positions", []) or []:
        if pos.get("id") == position_id:
            return pos
    return None


class PortfolioTotalSensor(_PVBase):
    """Generic portfolio totals sensor (monetary)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: PortfolioValuatorCoordinator,
        entry_id: str,
        portfolio_id: int,
        key: str,
        readable: str,
        currency: str,
        device_class: SensorDeviceClass | None,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._portfolio_id = portfolio_id
        self._key = key
        self._attr_name = readable
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_{key}"
        self._attr_native_unit_of_measurement = currency
        if device_class is not None:
            self._attr_device_class = device_class
        pf = _find_portfolio(coordinator, portfolio_id) or {"portfolio": {"id": portfolio_id}}
        self._attr_device_info = _device_for_portfolio(entry_id, pf.get("portfolio") or {}, getattr(coordinator, "service_version", None))

    @property
    def native_value(self) -> float | None:
        pf = _find_portfolio(self.coordinator, self._portfolio_id)
        if not pf:
            return None
        return _safe_float((pf.get("totals") or {}).get(self._key))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pf = _find_portfolio(self.coordinator, self._portfolio_id) or {}
        totals = pf.get("totals") or {}
        return {
            "valued_at": pf.get("valued_at"),
            "missing_fx": totals.get("missing_fx"),
            "portfolio_id": self._portfolio_id,
            "integration": DOMAIN,
        }


class PortfolioPnlPctSensor(_PVBase):
    """Portfolio total P/L percentage."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: PortfolioValuatorCoordinator, entry_id: str, portfolio_id: int
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._portfolio_id = portfolio_id
        self._attr_name = "Profit / Loss %"
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_pnl_pct"
        pf = _find_portfolio(coordinator, portfolio_id) or {"portfolio": {"id": portfolio_id}}
        self._attr_device_info = _device_for_portfolio(entry_id, pf.get("portfolio") or {}, getattr(coordinator, "service_version", None))

    @property
    def native_value(self) -> float | None:
        pf = _find_portfolio(self.coordinator, self._portfolio_id)
        if not pf:
            return None
        pct = _safe_float((pf.get("totals") or {}).get("pnl_pct"))
        if pct is None:
            return None
        return round(pct * 100.0, 4)


class PortfolioValuedAtSensor(_PVBase):
    """Last valuation timestamp."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: PortfolioValuatorCoordinator, entry_id: str, portfolio_id: int
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._portfolio_id = portfolio_id
        self._attr_name = "Valued At"
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_valued_at"
        pf = _find_portfolio(coordinator, portfolio_id) or {"portfolio": {"id": portfolio_id}}
        self._attr_device_info = _device_for_portfolio(entry_id, pf.get("portfolio") or {}, getattr(coordinator, "service_version", None))

    @property
    def native_value(self) -> datetime | None:
        pf = _find_portfolio(self.coordinator, self._portfolio_id)
        if not pf:
            return None
        return _parse_dt(pf.get("valued_at"))


# =============================================================== Position sensors
class _PositionBase(_PVBase):
    def __init__(
        self,
        coordinator: PortfolioValuatorCoordinator,
        entry_id: str,
        portfolio_id: int,
        position_id: int,
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._portfolio_id = portfolio_id
        self._position_id = position_id
        pf = _find_portfolio(coordinator, portfolio_id) or {"portfolio": {"id": portfolio_id}}
        self._attr_device_info = _device_for_portfolio(entry_id, pf.get("portfolio") or {}, getattr(coordinator, "service_version", None))

    def _position(self) -> dict[str, Any] | None:
        return _find_position(self.coordinator, self._portfolio_id, self._position_id)

    def _position_label(self) -> str:
        pos = self._position() or {}
        return (
            pos.get("name")
            or pos.get("instrument_name")
            or pos.get("instrument_code")
            or f"Position {self._position_id}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pos = self._position() or {}
        return {
            "instrument_code": pos.get("instrument_code"),
            "instrument_isin": pos.get("instrument_isin"),
            "instrument_name": pos.get("instrument_name"),
            "quantity": pos.get("quantity"),
            "entry_price": pos.get("entry_price"),
            "price_source": pos.get("price_source"),
            "currency": pos.get("currency"),
            "fx_rate": pos.get("fx_rate"),
            "fx_missing": pos.get("fx_missing"),
        }


class PositionPriceSensor(_PositionBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 4

    def __init__(self, coordinator, entry_id, portfolio_id, position_id, currency):
        super().__init__(coordinator, entry_id, portfolio_id, position_id)
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_pos_{position_id}_price"
        self._attr_native_unit_of_measurement = currency

    @property
    def name(self) -> str:
        return f"Position {self._position_label()} – Price"

    @property
    def native_value(self) -> float | None:
        pos = self._position()
        return _safe_float(pos.get("price")) if pos else None


class PositionMarketValueSensor(_PositionBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, entry_id, portfolio_id, position_id, currency):
        super().__init__(coordinator, entry_id, portfolio_id, position_id)
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_pos_{position_id}_mv"
        self._attr_native_unit_of_measurement = currency

    @property
    def name(self) -> str:
        return f"Position {self._position_label()} – Market Value"

    @property
    def native_value(self) -> float | None:
        pos = self._position()
        return _safe_float(pos.get("market_value")) if pos else None


class PositionPnlSensor(_PositionBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, entry_id, portfolio_id, position_id, currency):
        super().__init__(coordinator, entry_id, portfolio_id, position_id)
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_pos_{position_id}_pnl"
        self._attr_native_unit_of_measurement = currency

    @property
    def name(self) -> str:
        return f"Position {self._position_label()} – P/L"

    @property
    def native_value(self) -> float | None:
        pos = self._position()
        return _safe_float(pos.get("pnl")) if pos else None


class PositionPnlPctSensor(_PositionBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator, entry_id, portfolio_id, position_id):
        super().__init__(coordinator, entry_id, portfolio_id, position_id)
        self._attr_unique_id = f"{entry_id}_portfolio_{portfolio_id}_pos_{position_id}_pnl_pct"

    @property
    def name(self) -> str:
        return f"Position {self._position_label()} – P/L %"

    @property
    def native_value(self) -> float | None:
        pos = self._position()
        if not pos:
            return None
        pct = _safe_float(pos.get("pnl_pct"))
        if pct is None:
            return None
        return round(pct * 100.0, 4)


# =============================================================== Watchlist sensor
class WatchlistPriceSensor(_PVBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 4

    def __init__(
        self,
        coordinator: PortfolioValuatorCoordinator,
        entry_id: str,
        item: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._item_id = item.get("id")
        self._attr_unique_id = f"{entry_id}_watch_{self._item_id}_price"
        self._attr_name = "Price"
        self._attr_native_unit_of_measurement = (
            item.get("currency") or item.get("instrument_currency") or "EUR"
        )
        self._attr_device_info = _device_for_watch_item(entry_id, item)

    def _item(self) -> dict[str, Any] | None:
        for it in (self.coordinator.data or {}).get("watchlist", []) or []:
            if it.get("id") == self._item_id:
                return it
        return None

    @property
    def native_value(self) -> float | None:
        it = self._item()
        return _safe_float(it.get("price")) if it else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        it = self._item() or {}
        return {
            "label": it.get("label"),
            "instrument_code": it.get("instrument_code"),
            "instrument_name": it.get("instrument_name"),
            "price_source": it.get("price_source"),
            "field": it.get("field"),
        }


# =============================================================== FX sensor
class FxRateSensor(_PVBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 6

    def __init__(
        self,
        coordinator: PortfolioValuatorCoordinator,
        entry_id: str,
        fx: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._fx_id = fx.get("id")
        self._fx_code = fx.get("code")
        base = fx.get("base_currency") or ""
        quote = fx.get("quote_currency") or ""
        readable = fx.get("name") or self._fx_code or f"{base}/{quote}"
        self._attr_unique_id = f"{entry_id}_fx_{self._fx_id}"
        self._attr_name = f"FX {readable}"
        self._attr_native_unit_of_measurement = quote or None
        self._attr_device_info = _device_for_fx(entry_id)

    def _item(self) -> dict[str, Any] | None:
        for it in (self.coordinator.data or {}).get("fx_rates", []) or []:
            if it.get("id") == self._fx_id:
                return it
        return None

    @property
    def native_value(self) -> float | None:
        it = self._item()
        return _safe_float(it.get("price")) if it else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        it = self._item() or {}
        return {
            "code": it.get("code"),
            "base_currency": it.get("base_currency"),
            "quote_currency": it.get("quote_currency"),
            "price_source": it.get("price_source"),
        }
