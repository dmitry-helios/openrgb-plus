"""Load OpenRGB profiles from a select entity."""

from homeassistant.components.select import SelectEntity
from .entity import ServerEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ProfileSelect(entry.runtime_data)])


class ProfileSelect(ServerEntity, SelectEntity):
    _attr_name = "Profile"
    _attr_icon = "mdi:palette"

    def __init__(self, coordinator):
        super().__init__(coordinator, "profile")

    @property
    def options(self):
        return self.coordinator.data.profiles

    @property
    def current_option(self):
        value = (
            self.coordinator.data.active_profile
            if self.coordinator.client.protocol >= 6
            else self.coordinator.last_profile
        )
        return value if value in self.options else None

    @property
    def extra_state_attributes(self):
        return {
            "state_source": "server"
            if self.coordinator.client.protocol >= 6
            else "last profile requested by Home Assistant"
        }

    async def async_select_option(self, option):
        await self.coordinator.command("profile", option)
