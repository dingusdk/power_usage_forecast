"""Sensor platform for Power usage forecast integration."""

from __future__ import annotations

import json
import logging
import statistics

# Standard library
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

# Home Assistant
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import SensorDeviceClass
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import utcnow

if TYPE_CHECKING:
    from types import MappingProxyType

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

# Local
from .const import CONF_DAYS, CONF_RESULT_DAYS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Initialize Power forecast config entry."""
    registry = er.async_get(hass)
    # Validate + resolve entity registry id to entity_id
    entity_id = er.async_validate_entity_id(
        registry, config_entry.options[CONF_ENTITY_ID]
    )
    name = config_entry.title
    unique_id = config_entry.entry_id
    async_add_entities(
        [
            PowerUsageforecastSensorEntity(
                unique_id, name, entity_id, config_entry.options
            )
        ]
    )


@dataclass
class ForecastEntry:
    """Data class for a forecast entry."""

    time: datetime
    wh: float
    max: float
    min: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "time": self.time.isoformat(),
            "wh": self.wh,
            "max": self.max,
            "min": self.min,
        }


class PowerUsageforecastSensorEntity(SensorEntity):
    """Power usage forecast sensor."""

    def __init__(
        self,
        unique_id: str,
        name: str,
        wrapped_entity_id: str,
        options: MappingProxyType[str, Any],
    ) -> None:
        """Initialize powerforecast Sensor."""
        super().__init__()
        # the entity id of the wrapped total power sensor
        self._wrapped_entity_id = wrapped_entity_id
        # The days in the past to use for forecast
        self._days = int(options.get(CONF_DAYS, 8))
        # The number of days to return in the forecast
        self._result_days = int(options.get(CONF_RESULT_DAYS, 8))
        # The method to use for forecast
        self._method = options.get("forecast_method", "Average")
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_native_unit_of_measurement = "Wh"
        # optional: start with an explicit dict (keeps keys always present)
        self._attr_extra_state_attributes: dict[str, Any] = {
            "source": self._wrapped_entity_id,
            "days": self._days,
            "method": self._method,
            "forecast": [],
        }
        self._update_unsub = None
        self._state = None

    @property
    def state(self) -> Any:
        """
        Return the state of the sensor.

        This is the total forecasted usage for the next 24 hours.
        """
        return self._state

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:chart-bar"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional attributes for the sensor."""
        # Ensure the wrapped entity id is always present per rules
        attrs = dict(self._attr_extra_state_attributes)
        attrs["source"] = self._wrapped_entity_id
        return attrs

    async def async_added_to_hass(self) -> None:
        """Set up hourly history refresh when added to hass."""
        # Schedule hourly updates
        interval = timedelta(hours=1)
        # Do an immediate update first, then schedule periodic updates
        await self._async_update_forecast(utcnow())
        # Keep unsub function so we can remove the listener on unload
        self._update_unsub = async_track_time_interval(
            self.hass, self._async_update_forecast, interval
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup scheduled updates."""
        if self._update_unsub is not None:
            self._update_unsub()
            self._update_unsub = None

    async def _async_update_forecast(self, now: datetime) -> None:
        """Query recorder/history for the wrapped entity and update attributes."""
        _LOGGER.debug("Update power usage forecast %s", now.isoformat())
        now = dt_util.now()
        forecast: list[ForecastEntry] = []
        for r in range(self._result_days):
            forecast_one_day = await self._get_estimated_houly_usage_by_weekday(
                now + timedelta(days=r)
            )
            if forecast_one_day is None:
                forecast = []
                break
            forecast.extend(forecast_one_day)
        if len(forecast) == 0:
            _LOGGER.warning(
                "Fallback to all days because of not enough data for weekday forecast"
            )
            # Fallback to forecast for all days equal if not enough data
            forecast_one_day = await self._get_estimated_houly_usage(now)
            for r in range(self._result_days):
                for entry in forecast_one_day:
                    new_time = entry.time + timedelta(days=r)
                    forecast.append(
                        ForecastEntry(
                            time=new_time, wh=entry.wh, max=entry.max, min=entry.min
                        )
                    )
        if len(forecast) == 0:
            _LOGGER.warning("No forecast data available")
            self._state = 0
            self.async_write_ha_state()
            return

        self._state = round(sum([e.wh for e in forecast[0:24]]), 2)
        # Update attributes (keys always present)
        self._attr_extra_state_attributes.update(
            {
                "forecast": [e.to_dict() for e in forecast],
                "max_forecast": max(e.max for e in forecast),
                "min_forecast": min(e.min for e in forecast),
                "max_1day": sum(e.max for e in forecast),
                "min_1day": sum(e.min for e in forecast),
            }
        )
        _LOGGER.debug(
            "Forecast len: %s", len(json.dumps([e.to_dict() for e in forecast]))
        )
        # Ensure Home Assistant updates the entity state/attributes in the UI
        self.async_write_ha_state()

    async def get_usage_for_day(self, day: datetime) -> list[float] | None:
        """
        Get total usage for a specific day for each hour.

        We need to get 25 data points to cover the full 24 hours, since
        each hourly data point covers the hour starting at that time.
        If there is no data, we return 0 for that hour or all hours
        """
        result: list[float] = [0] * 24
        # 1 hour before the daystart to include any overlapping data
        start = day.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            hours=1
        )
        start = dt_util.as_utc(start)
        end = start + timedelta(days=1, minutes=59)
        try:
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                {self._wrapped_entity_id},
                "hour",  # The period is 'hourly'
                None,  # No specific units
                ("sum", "state"),  # The statistical functions to return
            )
            data = stats.get(self._wrapped_entity_id, [])
            if len(data) == 0 or "sum" not in data[0]:
                return None
            presum = data[0]["sum"]
            for stat in data[1:]:
                if "sum" not in stat or "start" not in stat:
                    continue
                value = stat["sum"]
                time = dt_util.as_local(dt_util.utc_from_timestamp(stat["start"]))
                hour = time.hour
                if value is not None and presum:
                    result[hour] = int(value - presum)
                presum = value
            _LOGGER.debug(result)
        except Exception as ex:  # noqa: BLE001
            _LOGGER.warning("Error getting statistics: %s", ex)
            return None
        return result

    async def _get_estimated_houly_usage(self, now: datetime) -> list[ForecastEntry]:
        """
        Get estimated hourly usage based on past days.

        First we get the usage for each of the past N days, then for each hour
        we compute the average and median usage across those days.
        """
        # Build a matrix of past usage data. day,hour
        day_matrix: list[list[float]] = []
        for day in range(self._days):
            start = now - timedelta(days=day + 1)
            day_data = await self.get_usage_for_day(start)
            if day_data is not None:
                day_matrix.append(day_data)
        if len(day_matrix) == 0:
            return []
        forecast: list[ForecastEntry] = []
        for hour in range(24):
            hourdata = []
            for day in range(len(day_matrix)):
                wh = day_matrix[day][hour]
                if wh > 0:
                    hourdata.append(wh)
            time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if len(hourdata) == 0:
                forecast.append(ForecastEntry(time=time, wh=0, max=0, min=0))
                continue
            wh = 0.0
            if self._method == "Median":
                wh = statistics.median(hourdata)
            if self._method == "Average":
                wh = statistics.mean(hourdata)
            entry = ForecastEntry(
                time=time, wh=wh, max=max(hourdata), min=min(hourdata)
            )
            forecast.append(entry)
        return forecast

    async def _get_estimated_houly_usage_by_weekday(
        self, now: datetime
    ) -> list[ForecastEntry] | None:
        """
        Get estimated hourly usage based on past days.

        First we get the usage for each of the past N days, then for each hour
        we compute the average and median usage across those days.
        """
        # Build a matrix of past usage data. day,hour
        day_matrix: list[list[float]] = []
        for day in range(self._days):
            start = now - timedelta(days=day * 7)
            day_data = await self.get_usage_for_day(start)
            if day_data is not None:
                day_matrix.append(day_data)
        if len(day_matrix) < self._days:
            return None
        _LOGGER.debug("Enough data to calculate weekday forecast")
        forecast: list[ForecastEntry] = []
        for hour in range(24):
            hourdata = []
            for day in range(len(day_matrix)):
                wh = day_matrix[day][hour]
                if wh > 0:
                    hourdata.append(wh)
            time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if len(hourdata) == 0:
                forecast.append(ForecastEntry(time=time, wh=0, max=0, min=0))
                continue
            wh = 0.0
            if self._method == "Median":
                wh = statistics.median(hourdata)
            if self._method == "Average":
                wh = statistics.mean(hourdata)
            entry = ForecastEntry(
                time=time, wh=wh, max=max(hourdata), min=min(hourdata)
            )
            forecast.append(entry)
        return forecast
