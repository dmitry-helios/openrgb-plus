"""Toggle effects already configured in the OpenRGB Effects plugin."""

from homeassistant.components.switch import SwitchEntity
from .entity import ServerEntity, discover

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    discover(entry.runtime_data, entry, async_add_entities, "effects", EffectSwitch)


class EffectSwitch(ServerEntity, SwitchEntity):
    _attr_icon = "mdi:animation-play"

    def __init__(self, coordinator, name):
        super().__init__(coordinator, f"effect_{name}")
        self.effect_name = name
        self._attr_name = f"Effect {name}"

    @property
    def available(self):
        return super().available and self.effect_name in self.coordinator.data.effects

    @property
    def is_on(self):
        return self.coordinator.data.effects.get(self.effect_name, False)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.command("effect", self.effect_name, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.command("effect", self.effect_name, False)
