"""One RGB light per physical OpenRGB controller."""

from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .entity import ServerEntity, discover

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    discover(entry.runtime_data, entry, async_add_entities, "devices", OpenRGBLight)
    coordinator = entry.runtime_data
    known = set()

    def update_zones():
        entities = []
        for key, dev in coordinator.data.devices.items():
            for zone in dev.zones:
                identity = (key, zone.key)
                if zone.color_count and identity not in known:
                    known.add(identity)
                    entities.append(OpenRGBZoneLight(coordinator, key, zone.key))
        if entities:
            async_add_entities(entities)

    update_zones()
    entry.async_on_unload(coordinator.async_add_listener(update_zones))


class OpenRGBLight(ServerEntity, LightEntity):
    _attr_name = None

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key
        self._last_rgb = (255, 255, 255)
        self._last_brightness = 255
        dev = self.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=dev.name,
            manufacturer=dev.vendor or "OpenRGB",
            model=dev.description,
            sw_version=dev.version,
            serial_number=dev.serial or None,
            via_device=(DOMAIN, coordinator.entry.entry_id),
        )

    @property
    def device(self):
        return self.coordinator.data.devices.get(self.key)

    @property
    def available(self):
        return super().available and self.device is not None

    @property
    def supported_features(self):
        return LightEntityFeature.EFFECT if self.effect_list else LightEntityFeature(0)

    @property
    def effect_list(self):
        return list(dict.fromkeys(m.name for m in self.device.modes)) if self.device else []

    @property
    def effect(self):
        return self.device.mode.name if self.device and self.device.mode else None

    @property
    def supported_color_modes(self):
        mode = self.device.mode if self.device else None
        if mode and mode.flags & 96:
            return {ColorMode.RGB}
        if mode and mode.flags & 16:
            return {ColorMode.BRIGHTNESS}
        return {ColorMode.ONOFF}

    @property
    def color_mode(self):
        return next(iter(self.supported_color_modes))

    def _color(self):
        if not self.device:
            return (0, 0, 0)
        mode = self.device.mode
        colors = mode.colors if mode and mode.color_mode == 2 else self.device.colors
        return next((c for c in colors if any(c)), (0, 0, 0))

    @property
    def is_on(self):
        if not self.device or self.effect == "Off":
            return False
        if self.color_mode == ColorMode.RGB and not any(self._color()):
            return False
        return self.brightness != 0

    @property
    def rgb_color(self):
        rgb = self._color()
        peak = max(rgb)
        if peak:
            mode = self.device.mode
            self._last_rgb = (
                rgb if mode and mode.flags & 16 else tuple(round(c * 255 / peak) for c in rgb)
            )
        return self._last_rgb

    @property
    def brightness(self):
        mode = self.device.mode if self.device else None
        if mode and mode.flags & 16:
            span = mode.brightness_max - mode.brightness_min
            value = round(255 * (mode.brightness - mode.brightness_min) / span) if span else 255
        elif self.color_mode == ColorMode.RGB:
            value = max(self._color())
        else:
            return None
        value = max(0, min(255, value))
        if value:
            self._last_brightness = value
        return value

    async def async_turn_on(self, **kwargs):
        rgb = kwargs.get("rgb_color")
        brightness = kwargs.get("brightness")
        effect = kwargs.get("effect")
        if rgb is not None and brightness is None:
            brightness = self.brightness or self._last_brightness
        if effect is None and rgb is None and self.color_mode == ColorMode.RGB:
            rgb = self.rgb_color
            if brightness is None:
                brightness = self.brightness or self._last_brightness
            # Preserve a selected color-capable hardware effect during dimming.
            effect = self.effect if self.is_on else None
        elif effect is None and not self.is_on and rgb is None:
            rgb, brightness = self._last_rgb, brightness or self._last_brightness
        await self.coordinator.command(
            "light", self.key, True, rgb=rgb, brightness=brightness, effect=effect
        )

    async def async_turn_off(self, **kwargs):
        _ = self.rgb_color, self.brightness
        await self.coordinator.command("light", self.key, False)


class OpenRGBZoneLight(ServerEntity, LightEntity):
    """An RGB zone belonging to the existing physical HA device."""

    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, coordinator, key, zone_key):
        super().__init__(coordinator, f"{key}_zone_{zone_key}")
        self.key, self.zone_key = key, zone_key
        self._last_rgb, self._last_brightness = (255, 255, 255), 255
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{key}")}
        )

    @property
    def device(self):
        return self.coordinator.data.devices.get(self.key)

    @property
    def zone(self):
        return (
            next((z for z in self.device.zones if z.key == self.zone_key), None)
            if self.device
            else None
        )

    @property
    def name(self):
        return self.zone.name if self.zone else "Zone"

    @property
    def available(self):
        return super().available and self.zone is not None and self.zone.color_count > 0

    def _color(self):
        if not self.zone:
            return (0, 0, 0)
        colors = self.device.colors[self.zone.offset : self.zone.offset + self.zone.color_count]
        return next((c for c in colors if any(c)), (0, 0, 0))

    @property
    def is_on(self):
        mode = self.device.mode if self.device else None
        if self.zone and self.zone.modes:
            mode = (
                self.zone.modes[self.zone.active_mode]
                if 0 <= self.zone.active_mode < len(self.zone.modes)
                else None
            )
        return bool(any(self._color())) and not (mode and mode.name == "Off")

    @property
    def rgb_color(self):
        color = self._color()
        peak = max(color)
        if peak:
            self._last_rgb = tuple(round(c * 255 / peak) for c in color)
        return self._last_rgb

    @property
    def brightness(self):
        value = max(self._color())
        if value:
            self._last_brightness = value
        return value

    async def async_turn_on(self, **kwargs):
        rgb = kwargs.get("rgb_color", self.rgb_color)
        brightness = kwargs.get("brightness", self.brightness or self._last_brightness)
        await self.coordinator.command(
            "zone_light", self.key, self.zone_key, True, rgb=rgb, brightness=brightness
        )

    async def async_turn_off(self, **kwargs):
        _ = self.rgb_color, self.brightness
        await self.coordinator.command("zone_light", self.key, self.zone_key, False)
