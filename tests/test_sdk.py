"""Wire-level tests use synthetic packets and a real asyncio TCP server."""

import asyncio
import importlib.util
from pathlib import Path
import struct
import sys
from unittest.mock import AsyncMock

import pytest

spec = importlib.util.spec_from_file_location(
    "orgb_sdk", Path(__file__).parents[1] / "custom_components/openrgb_plus/sdk.py"
)
sdk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sdk
spec.loader.exec_module(sdk)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_native", [False, True])
@pytest.mark.parametrize("source_palette", [False, True])
@pytest.mark.parametrize("target_native", [False, True])
async def test_effect_change_retains_current_color_and_brightness(
    source_native, source_palette, target_native
):
    source_color = (255, 0, 0) if source_native else (128, 0, 0)
    previous = sdk.Mode(
        "Direct",
        0,
        32 | (16 if source_native else 0),
        0,
        0,
        0,
        255,
        0,
        0,
        0,
        128,
        0,
        2 if source_palette else 1,
        [source_color],
    )
    target = sdk.Mode(
        "Breathing",
        1,
        64 | (16 if target_native else 0),
        0,
        0,
        0,
        255,
        1,
        1,
        0,
        255,
        0,
        2,
        [(0, 255, 0)],
    )
    dev = sdk.Device(42, "Test", "", "", "", "", "", [previous, target], 0, [source_color], "test")
    client = sdk.Client("unused")
    client.read_devices = AsyncMock(return_value={"test": dev})
    client.mode = AsyncMock()
    await client.light("test", True, effect="Breathing")
    selected = client.mode.await_args.args[2]
    assert selected.colors == [(255, 0, 0) if target_native else (128, 0, 0)]
    if target_native:
        assert selected.brightness == 128
    assert target.colors == [(0, 255, 0)]  # No mutation of cached mode data.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect,rgb,brightness,expected",
    [
        ("Breathing", (0, 0, 255), 255, [(0, 0, 255)]),
        ("Breathing", None, 64, [(64, 0, 0)]),
        ("Rainbow", None, None, [(0, 255, 0)]),
        ("Off", None, None, [(0, 255, 0)]),
    ],
)
async def test_effect_change_respects_explicit_overrides_and_generated_colors(
    effect, rgb, brightness, expected
):
    previous = sdk.Mode("Direct", 0, 32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, [])
    target = sdk.Mode(
        effect,
        1,
        64 if effect != "Rainbow" else 128,
        0,
        0,
        0,
        255,
        1,
        1,
        0,
        255,
        0,
        2,
        [(0, 255, 0)],
    )
    dev = sdk.Device(42, "Test", "", "", "", "", "", [previous, target], 0, [(128, 0, 0)], "test")
    client = sdk.Client("unused")
    client.read_devices = AsyncMock(return_value={"test": dev})
    client.mode = AsyncMock()
    await client.light("test", True, effect=effect, rgb=rgb, brightness=brightness)
    assert client.mode.await_args.args[2].colors == expected


def fixture(protocol=5, location="RAM slot 1", device_type=999):
    mode = sdk.Mode("Direct", 7, 32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, [])
    raw = struct.pack("<I", device_type)
    raw += b"".join(
        sdk.string(s) for s in ("Test RAM", "Test Vendor", "Memory", "1.0", "", location)
    )
    raw += struct.pack("<HI", 1, 0) + mode.pack(protocol)
    raw += struct.pack("<H", 1) + sdk.string("Zone") + struct.pack("<4IH", 999, 1, 8, 2, 0)
    if protocol >= 4:
        raw += struct.pack("<H", 1) + sdk.string("Segment") + struct.pack("<3I", 999, 0, 2)
        if protocol >= 6:
            raw += struct.pack("<HI", 0, 0)
    if protocol >= 5:
        raw += struct.pack("<I", 1)
    if protocol >= 6:
        raw += struct.pack("<IH", 0, 1) + mode.pack(protocol) + sdk.string("Display zone")
    raw += struct.pack("<H", 2)
    raw += sdk.string("LED 1") + (struct.pack("<I", 0) if protocol < 6 else b"")
    raw += sdk.string("LED 2") + (struct.pack("<I", 1) if protocol < 6 else b"")
    raw += sdk.pack_colors([(0, 255, 0), (0, 255, 0)])
    if protocol >= 5:
        raw += struct.pack("<H", 1) + sdk.string("Alt LED") + struct.pack("<I", 3)
    if protocol >= 6:
        raw += sdk.string("Display device") + struct.pack("<I", 3) + b"{}\0"
    return sdk.block(raw)


@pytest.mark.parametrize("protocol", [3, 4, 5, 6])
def test_device_layout_and_unknown_types(protocol):
    dev = sdk.parse_device(fixture(protocol), 0, protocol)
    assert dev.name == "Test RAM"
    assert dev.colors == [(0, 255, 0), (0, 255, 0)]
    assert dev.mode.value == (7 if protocol < 6 else 0)


def test_identity_survives_index_changes_but_separates_ram_slots():
    one = sdk.parse_device(fixture(), 0, 5)
    assert one.key == sdk.parse_device(fixture(), 3, 5).key
    assert one.key != sdk.parse_device(fixture(location="RAM slot 2"), 1, 5).key


@pytest.mark.parametrize("cut", [0, 4, 25, 60, -1])
def test_truncated_packets_fail(cut):
    with pytest.raises(sdk.ProtocolError):
        sdk.parse_device(fixture()[:cut], 0, 5)


def test_mode_wire_roundtrip():
    mode = sdk.Mode("Breathing", 123, 80, 1, 100, 10, 255, 1, 2, 50, 200, 1, 2, [(10, 20, 30)])
    assert sdk.Mode.read(sdk.Reader(mode.pack())) == mode


class FakeServer:
    def __init__(self, plugin=True, version=2, protocol=5, reject_effect=False):
        self.packets = []
        self.plugin = plugin
        self.version = version
        self.protocol = protocol
        self.reject_effect = reject_effect

    async def handle(self, reader, writer):
        try:
            while True:
                magic, device, packet, size = sdk.HEADER.unpack(await reader.readexactly(16))
                request_device = device
                data = await reader.readexactly(size)
                assert magic == b"ORGB"
                self.packets.append((packet, device, data))
                response = None
                if packet == 40:
                    assert data == struct.pack("<I", 6)
                    response = struct.pack("<I", self.protocol)
                elif packet == 0:
                    response = struct.pack("<I", 2)
                    if self.protocol >= 6:
                        response += struct.pack("<II", 42, 99)
                elif packet == 1:
                    response = fixture(self.protocol, location=f"slot {device}")
                elif packet == 150:
                    response = sdk.block(struct.pack("<H", 1) + sdk.string("Example Green"))
                elif packet == 156:
                    response = b"Example Green\0"
                elif packet == 200 and self.plugin:
                    response = sdk.block(
                        struct.pack("<H", 1)
                        + sdk.string("OpenRGB Effects Plugin")
                        + sdk.string("Effects")
                        + sdk.string("1.0")
                        + struct.pack("<II", 7, self.version)
                    )
                elif packet == 201 and data == struct.pack("<I", 0):
                    effects = (
                        struct.pack("<H", 1)
                        + sdk.string("Rainbow")
                        + sdk.string("Configured effect")
                        + b"\1"
                    )
                    response = struct.pack("<I", 0) + (
                        sdk.block(effects) if self.version == 2 else effects
                    )
                    device = 0  # Actual OpenRGB replies use zero, not plugin index.
                if response is not None:
                    raw = sdk.HEADER.pack(b"ORGB", device, packet, len(response)) + response
                    # TCP boundaries must not be confused with packet boundaries.
                    writer.write(raw[:7])
                    await writer.drain()
                    await asyncio.sleep(0)
                    writer.write(raw[7:])
                    await writer.drain()
                if self.protocol >= 6:
                    status = (
                        4
                        if self.reject_effect
                        and packet == 201
                        and data[:4] == struct.pack("<I", 21)
                        else 0
                    )
                    writer.write(
                        sdk.HEADER.pack(b"ORGB", request_device, 10, 8)
                        + struct.pack("<II", packet, status)
                    )
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("protocol", [5, 6])
async def test_discovery_commands_profiles_and_effects(version, protocol):
    fake = FakeServer(version=version, protocol=protocol)
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    client = sdk.Client("127.0.0.1", server.sockets[0].getsockname()[1])
    async with server:
        try:
            state = await client.snapshot()
            assert len(state.devices) == 2
            assert state.effects == {"Rainbow": True}
            assert client.protocol == protocol
            if protocol == 6:
                assert [d.index for d in state.devices.values()] == [42, 99]
                assert state.active_profile == "Example Green"
            key = next(iter(state.devices))
            await client.light(key, True, rgb=(255, 0, 0), brightness=128)
            await client.profile("Example Green")
            await client.effect("Rainbow", False)
            await client.snapshot()
            colors = [data for p, d, data in fake.packets if p == 1050]
            assert colors[0] == sdk.block(sdk.pack_colors([(128, 0, 0)] * 2))
            assert any(p == 152 and data == b"Example Green\0" for p, d, data in fake.packets)
            assert any(
                p == 201 and d == 7 and data == struct.pack("<I", 21) + sdk.string("Rainbow")
                for p, d, data in fake.packets
            )
            for p, d, data in fake.packets:
                if p == 1101:
                    r = sdk.read_block(data)
                    assert r.number() == 0
                    assert sdk.Mode.read(r, protocol).value == (7 if protocol < 6 else 0)
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_missing_plugin_reply_does_not_break_core_and_reconnects():
    fake = FakeServer(plugin=False)
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    client = sdk.Client("127.0.0.1", server.sockets[0].getsockname()[1], timeout=0.1)
    async with server:
        try:
            data = await client.snapshot()
            assert len(data.devices) == 2 and data.profiles == ["Example Green"]
            assert data.effects == {} and client.writer is None
            assert len((await client.snapshot()).devices) == 2
            assert sum(p == 200 for p, _, _ in fake.packets) == 1
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_unknown_profile_rejected_without_write():
    client = sdk.Client("unused")
    with pytest.raises(ValueError, match="Unknown"):
        await client.profile("not saved")


@pytest.mark.asyncio
async def test_native_ack_failure_is_reported():
    async def reject(reader, writer):
        _, device, packet, size = sdk.HEADER.unpack(await reader.readexactly(16))
        await reader.readexactly(size)
        writer.write(sdk.HEADER.pack(b"ORGB", device, 10, 8) + struct.pack("<II", packet, 4))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(reject, "127.0.0.1", 0)
    c = sdk.Client("127.0.0.1", server.sockets[0].getsockname()[1])
    async with server:
        c.reader, c.writer = await asyncio.open_connection(c.host, c.port)
        try:
            with pytest.raises(sdk.ProtocolError, match="status 4"):
                await c.command(1050, 99, b"test")
        finally:
            await c.close()


@pytest.mark.asyncio
async def test_plugin_discovery_ack_cannot_mask_toggle_rejection():
    fake = FakeServer(protocol=6, reject_effect=True)
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    c = sdk.Client("127.0.0.1", server.sockets[0].getsockname()[1])
    async with server:
        try:
            await c.snapshot()
            with pytest.raises(sdk.ProtocolError, match="status 4"):
                await c.effect("Rainbow", False)
        finally:
            await c.close()
