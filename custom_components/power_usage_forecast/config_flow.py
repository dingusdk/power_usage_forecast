"""Config flow for the Power usage forecast integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

import voluptuous as vol
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
    SchemaFlowMenuStep,
)

from .const import CONF_DAYS, CONF_FORECAST_METHOD, CONF_RESULT_DAYS, DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

FORECAST_METHOD: Final[list[str]] = ["Average", "Median"]

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DAYS, default=3): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=30, step=1)
        ),
        vol.Required(CONF_RESULT_DAYS, default=1): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=7, step=1)
        ),
        vol.Required(
            CONF_FORECAST_METHOD, default=FORECAST_METHOD[0]
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=FORECAST_METHOD,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("name"): selector.TextSelector(),
        #        vol.Required("power_entity"): selector.EntitySelector(),
        vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=SENSOR_DOMAIN, device_class="energy")
        ),
    }
).extend(OPTIONS_SCHEMA.schema)

CONFIG_FLOW: dict[str, SchemaFlowFormStep | SchemaFlowMenuStep] = {
    "user": SchemaFlowFormStep(CONFIG_SCHEMA)
}

OPTIONS_FLOW: dict[str, SchemaFlowFormStep | SchemaFlowMenuStep] = {
    "init": SchemaFlowFormStep(OPTIONS_SCHEMA)
}


class ConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Handle a config or options flow for Power usage forecast."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return cast("str", options["name"]) if "name" in options else ""
