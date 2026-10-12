"""Shared entity identity and discovery."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


class ServerEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, suffix):
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="OpenRGB",
            model="SDK server",
        )


def discover(coordinator, entry, add_entities, collection, factory):
    known = set()

    def update():
        keys = set(getattr(coordinator.data, collection))
        new = keys - known
        known.update(new)
        if new:
            add_entities([factory(coordinator, key) for key in sorted(new)])

    update()
    entry.async_on_unload(coordinator.async_add_listener(update))
