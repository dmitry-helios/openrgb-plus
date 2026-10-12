"""Entity contracts against real Home Assistant classes (Linux CI)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.components.light import ColorMode
from custom_components.openrgb_plus.light import OpenRGBLight
from custom_components.openrgb_plus.select import ProfileSelect
from custom_components.openrgb_plus.switch import EffectSwitch
from custom_components.openrgb_plus.sdk import Device, Mode, Snapshot


def coordinator():
    mode = Mode("Direct", 0, 32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, [])
    dev = Device(
        0, "RAM", "Test Vendor", "Memory", "1.0", "", "slot 1", [mode], 0, [(0, 128, 0)], "ram"
    )
    return SimpleNamespace(
        entry=SimpleNamespace(entry_id="server", title="PC"),
        data=Snapshot({"ram": dev}, ["Example Green"], {"Rainbow": True}),
        last_update_success=True,
        client=SimpleNamespace(protocol=5),
        last_profile=None,
        async_add_listener=Mock(),
        command=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_light_color_dimming_and_missing_device():
    c = coordinator()
    light = OpenRGBLight(c, "ram")
    assert light.available and light.is_on
    assert light.supported_color_modes == {ColorMode.RGB}
    assert light.rgb_color == (0, 255, 0)
    assert light.brightness == 128
    await light.async_turn_on(brightness=64)
    c.command.assert_awaited_once_with(
        "light", "ram", True, rgb=(0, 255, 0), brightness=64, effect="Direct"
    )
    c.data.devices.clear()
    assert not light.available
    assert light.effect is None


@pytest.mark.asyncio
async def test_profiles_and_plugin_switch():
    c = coordinator()
    select = ProfileSelect(c)
    assert select.options == ["Example Green"] and select.current_option is None
    await select.async_select_option("Example Green")
    c.command.assert_awaited_with("profile", "Example Green")
    effect = EffectSwitch(c, "Rainbow")
    assert effect.is_on and effect.available
    await effect.async_turn_off()
    c.command.assert_awaited_with("effect", "Rainbow", False)
    c.data.effects.clear()
    assert not effect.available


@pytest.mark.asyncio
async def test_native_brightness_color_turns_back_on():
    c = coordinator()
    dev = c.data.devices["ram"]
    dev.mode.flags |= 16
    dev.mode.brightness_min, dev.mode.brightness_max = 0, 255
    dev.mode.brightness = 0
    dev.colors = [(0, 0, 0)]
    light = OpenRGBLight(c, "ram")
    await light.async_turn_on(rgb_color=(255, 0, 0))
    c.command.assert_awaited_with(
        "light", "ram", True, rgb=(255, 0, 0), brightness=255, effect=None
    )


def test_native_profile_state_is_polled():
    c = coordinator()
    c.client.protocol = 6
    c.data.active_profile = "Example Green"
    select = ProfileSelect(c)
    assert select.current_option == "Example Green"
    assert select.extra_state_attributes["state_source"] == "server"
