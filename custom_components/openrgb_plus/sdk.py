"""Dependency-free, bounded OpenRGB SDK protocol 3–6 client.

Newer servers negotiate the supported v6 wire format. Device and zone type
values intentionally remain integers: newly added hardware must not break discovery.
All public operations are serialized by the Home Assistant coordinator.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import logging
import struct

HEADER = struct.Struct("<4sIII")
MAX_PACKET = 16 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class ProtocolError(ValueError):
    """Malformed or unsupported server response."""


def string(value: str) -> bytes:
    if "\0" in value:
        raise ValueError("Names cannot contain NUL")
    raw = value.encode("utf-8") + b"\0"
    return struct.pack("<H", len(raw)) + raw


def block(raw: bytes) -> bytes:
    return struct.pack("<I", len(raw) + 4) + raw


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise ProtocolError("Truncated SDK packet")
        result = self.data[self.pos : self.pos + size]
        self.pos += size
        return result

    def number(self, fmt: str = "I") -> int:
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def string(self) -> str:
        raw = self.take(self.number("H"))
        if not raw.endswith(b"\0"):
            raise ProtocolError("Unterminated SDK string")
        return raw[:-1].decode("utf-8", errors="replace")

    def colors(self) -> list[tuple[int, int, int]]:
        return [tuple(self.take(4)[:3]) for _ in range(self.number("H"))]

    def end(self) -> None:
        if self.pos != len(self.data):
            raise ProtocolError("Unexpected trailing SDK data")


def read_block(data: bytes) -> Reader:
    reader = Reader(data)
    if reader.number() != len(data):
        raise ProtocolError("Invalid SDK block length")
    return reader


@dataclass
class Mode:
    name: str
    value: int
    flags: int
    speed_min: int
    speed_max: int
    brightness_min: int
    brightness_max: int
    colors_min: int
    colors_max: int
    speed: int
    brightness: int
    direction: int
    color_mode: int
    colors: list[tuple[int, int, int]]

    @classmethod
    def read(cls, r: Reader, protocol: int = 5) -> Mode:
        name = r.string()
        value = r.number() if protocol < 6 else 0
        return cls(name, value, *(r.number() for _ in range(11)), r.colors())

    def pack(self, protocol: int = 5) -> bytes:
        return (
            string(self.name)
            + (struct.pack("<I", self.value) if protocol < 6 else b"")
            + struct.pack(
                "<11I",
                self.flags,
                self.speed_min,
                self.speed_max,
                self.brightness_min,
                self.brightness_max,
                self.colors_min,
                self.colors_max,
                self.speed,
                self.brightness,
                self.direction,
                self.color_mode,
            )
            + pack_colors(self.colors)
        )


def pack_colors(colors: list[tuple[int, int, int]]) -> bytes:
    return struct.pack("<H", len(colors)) + b"".join(bytes((*c, 0)) for c in colors)


@dataclass
class Device:
    index: int
    name: str
    vendor: str
    description: str
    version: str
    serial: str
    location: str
    modes: list[Mode]
    active_mode: int
    colors: list[tuple[int, int, int]]
    key: str = ""

    @property
    def mode(self) -> Mode | None:
        return self.modes[self.active_mode] if 0 <= self.active_mode < len(self.modes) else None


def parse_device(data: bytes, index: int, protocol: int) -> Device:
    r = read_block(data)
    r.number()  # Device type is extensible, not a closed enum.
    name, vendor, description, version, serial, location = (r.string() for _ in range(6))
    count, active = r.number("H"), r.number()
    modes = [Mode.read(r, protocol) for _ in range(count)]
    for _ in range(r.number("H")):
        r.string()
        r.take(16)  # Type, minimum, maximum, LED count.
        r.take(r.number("H"))  # Optional matrix.
        if protocol >= 4:
            for _ in range(r.number("H")):
                r.string()
                r.take(12)
                if protocol >= 6:
                    r.take(r.number("H"))
                    r.number()
        if protocol >= 5:
            r.number()  # Zone flags.
        if protocol >= 6:
            r.number()  # Zone active mode.
            for _ in range(r.number("H")):
                Mode.read(r, protocol)
            r.string()  # Zone display name.
    for _ in range(r.number("H")):
        r.string()
        if protocol < 6:
            r.number()
    colors = r.colors()
    if protocol >= 5:
        for _ in range(r.number("H")):
            r.string()
        r.number()  # Controller flags.
    if protocol >= 6:
        r.string()  # Device display name.
        r.take(r.number())  # JSON device configuration.
    r.end()
    identity = "\0".join((name, vendor, serial, location))
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return Device(
        index, name, vendor, description, version, serial, location, modes, active, colors, key
    )


@dataclass
class Snapshot:
    devices: dict[str, Device]
    profiles: list[str]
    effects: dict[str, bool]
    active_profile: str | None = None


class Client:
    def __init__(self, host: str, port: int = 6742, timeout: float = 5):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = None
        self.writer = None
        self.protocol = 6
        self.plugin: tuple[int, int] | None = None
        self.devices: dict[str, Device] = {}
        self.profiles: list[str] = []
        self.plugin_retry_at = 0.0

    async def close(self) -> None:
        writer, self.writer = self.writer, None
        self.reader = None
        if writer:
            writer.close()
            try:
                async with asyncio.timeout(self.timeout):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass

    async def connect(self) -> None:
        if self.writer:
            return
        async with asyncio.timeout(self.timeout):
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        try:
            data = await self.request(40, payload=struct.pack("<I", 6))
            self.protocol = min(6, Reader(data).number())
            if self.protocol < 3:
                raise ProtocolError("OpenRGB SDK protocol 3 or later is required")
            await self.send(50, payload=b"Home Assistant OpenRGB Plus\0")
        except BaseException:
            await self.close()
            raise

    async def send(self, packet: int, device: int = 0, payload: bytes = b"") -> None:
        if not self.writer:
            raise ConnectionError("SDK is disconnected")
        self.writer.write(HEADER.pack(b"ORGB", device, packet, len(payload)) + payload)
        async with asyncio.timeout(self.timeout):
            await self.writer.drain()

    async def request(self, packet: int, device: int = 0, payload: bytes = b"") -> bytes:
        await self.send(packet, device, payload)
        data = await self.receive(packet, device)
        if self.protocol >= 6 and packet != 40:
            # Drain this request's acknowledgement before another command with
            # the same packet ID (notably plugin discovery/toggle, both 201).
            await self.receive(packet, device, ack=True)
        return data

    async def receive(self, packet: int, device: int, ack: bool = False) -> bytes:
        async with asyncio.timeout(self.timeout):
            while True:
                magic, response_device, response, size = HEADER.unpack(
                    await self.reader.readexactly(16)
                )
                if magic != b"ORGB" or size > MAX_PACKET:
                    raise ProtocolError("Invalid SDK header or excessive packet length")
                data = await self.reader.readexactly(size)
                if response == 10:
                    if len(data) != 8:
                        raise ProtocolError("Invalid SDK acknowledgement length")
                    acknowledged, status = struct.unpack("<II", data)
                    if acknowledged == packet and response_device == device:
                        if status:
                            raise ProtocolError(
                                f"SDK command {packet} rejected with status {status}"
                            )
                        if ack:
                            return data
                    continue
                if response == 100:
                    # Indexes may now point at different physical devices. Reconnect
                    # and rediscover before issuing another write.
                    raise ConnectionError("OpenRGB device list changed; rediscovery required")
                if not ack and response == packet and (response_device == device or packet == 201):
                    return data

    async def command(self, packet: int, device: int = 0, payload: bytes = b"") -> None:
        await self.send(packet, device, payload)
        if self.protocol >= 6:
            await self.receive(packet, device, ack=True)

    async def read_devices(self) -> dict[str, Device]:
        r = Reader(await self.request(0))
        count = r.number()
        if count > 4096:
            raise ProtocolError("Excessive controller count")
        devices = {}
        duplicates: dict[str, int] = {}
        indexes = [r.number() for _ in range(count)] if self.protocol >= 6 else range(count)
        r.end()
        for index in indexes:
            dev = parse_device(
                await self.request(1, index, struct.pack("<I", self.protocol)), index, self.protocol
            )
            base = dev.key
            occurrence = duplicates.get(base, 0)
            duplicates[base] = occurrence + 1
            dev.key = f"{base}_{occurrence}"
            devices[dev.key] = dev
        self.devices = devices
        return devices

    async def snapshot(self, plugins: bool = True) -> Snapshot:
        await self.connect()
        devices = await self.read_devices()
        r = read_block(await self.request(150))
        self.profiles = [r.string() for _ in range(r.number("H"))]
        r.end()
        active_profile = None
        if self.protocol >= 6:
            active_profile = (await self.request(156)).rstrip(b"\0").decode(
                "utf-8", errors="replace"
            ) or None
        effects = {}
        self.plugin = None
        if (
            plugins
            and self.protocol >= 4
            and asyncio.get_running_loop().time() >= self.plugin_retry_at
        ):
            try:
                effects = await self.read_effects()
            except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError) as err:
                LOGGER.warning(
                    "Effects plugin discovery unavailable; retrying in 60 seconds: %s",
                    type(err).__name__,
                )
                self.plugin = None
                self.plugin_retry_at = asyncio.get_running_loop().time() + 60
                # Discard late replies on the timed-out stream. Core lighting
                # remains available and reconnects on its next operation.
                await self.close()
        return Snapshot(devices, list(self.profiles), effects, active_profile)

    async def read_effects(self) -> dict[str, bool]:
        effects = {}
        r = read_block(await self.request(200))
        for _ in range(r.number("H")):
            name, _description, _version = r.string(), r.string(), r.string()
            index, version = r.number(), r.number()
            if name == "OpenRGB Effects Plugin" and version in (1, 2):
                self.plugin = (index, version)
        r.end()
        if self.plugin:
            index, version = self.plugin
            r = Reader(await self.request(201, index, struct.pack("<I", 0)))
            if r.number() != 0:
                raise ProtocolError("Unexpected Effects plugin response")
            if version == 2:
                r.number()  # Plugin-specific block size.
            for _ in range(r.number("H")):
                name, _description = r.string(), r.string()
                enabled = bool(r.number("B"))
                effects[name] = effects.get(name, False) or enabled
            r.end()
        return effects

    async def mode(self, dev: Device, index: int, mode: Mode | None = None) -> None:
        await self.command(
            1101,
            dev.index,
            block(struct.pack("<I", index) + (mode or dev.modes[index]).pack(self.protocol)),
        )

    async def color(self, dev: Device, colors: list[tuple[int, int, int]]) -> None:
        await self.command(1050, dev.index, block(pack_colors(colors)))

    async def light(self, key: str, on: bool, rgb=None, brightness=None, effect=None) -> None:
        # Refresh first so writes never deliberately use stale controller indexes.
        devices = await self.read_devices()
        if key not in devices:
            raise ValueError("Device is no longer connected")
        dev = devices[key]
        names = [m.name for m in dev.modes]
        if not on:
            if "Off" in names:
                await self.mode(dev, names.index("Off"))
                return
            rgb, brightness, effect = (0, 0, 0), 0, None
        if effect is not None:
            if effect not in names:
                raise ValueError(f"Unsupported effect: {effect}")
            index = names.index(effect)
        elif rgb is not None or not on or (dev.mode and dev.mode.name == "Off"):
            index = next(
                (
                    i
                    for label in ("Direct", "Static", "Custom")
                    for i, m in enumerate(dev.modes)
                    if m.name == label
                ),
                -1,
            )
            if index < 0:
                index = next((i for i, m in enumerate(dev.modes) if m.flags & 96), -1)
        else:
            index = dev.active_mode
        if not 0 <= index < len(dev.modes):
            raise ValueError("Device has no controllable mode")
        mode = replace(dev.modes[index], colors=list(dev.modes[index].colors))
        if effect is not None and effect != "Off" and rgb is None and mode.flags & 96:
            # Hardware modes remember their own palettes. Carry the CURRENT
            # color into a newly selected color-capable mode instead of reviving
            # that mode's previous palette. Read from the fresh SDK snapshot.
            previous = dev.mode
            source = previous.colors if previous and previous.color_mode == 2 else dev.colors
            current = next((c for c in source if any(c)), None)
            if current is not None:
                if previous and previous.flags & 16:
                    rgb = current
                    span = previous.brightness_max - previous.brightness_min
                    level = (
                        round(255 * (previous.brightness - previous.brightness_min) / span)
                        if span
                        else 255
                    )
                else:
                    level = max(current)
                    rgb = tuple(round(c * 255 / level) for c in current)
                if brightness is None:
                    brightness = max(0, min(255, level))
        if brightness is not None and not 0 <= brightness <= 255:
            raise ValueError("Brightness must be between 0 and 255")
        if rgb is not None and (len(rgb) != 3 or any(not 0 <= c <= 255 for c in rgb)):
            raise ValueError("RGB channels must be between 0 and 255")
        if brightness is not None and rgb is None and not mode.flags & 16:
            if not mode.flags & 96:
                raise ValueError("This effect does not support brightness")
            source = mode.colors if mode.color_mode == 2 else dev.colors
            old = next((c for c in source if any(c)), (255, 255, 255))
            peak = max(old)
            rgb = tuple(round(c * 255 / peak) for c in old)
        if brightness is not None and mode.flags & 16:
            mode.brightness = round(
                mode.brightness_min + brightness / 255 * (mode.brightness_max - mode.brightness_min)
            )
        if rgb is not None:
            if not mode.flags & 96:
                raise ValueError("This effect does not accept a custom color")
            color = (
                tuple(round(c * (brightness if brightness is not None else 255) / 255) for c in rgb)
                if not mode.flags & 16
                else tuple(rgb)
            )
            if mode.flags & 64:
                mode.color_mode = 2
                mode.colors = [color] * max(mode.colors_min, len(mode.colors), 1)
            else:
                mode.color_mode = 1
        await self.mode(dev, index, mode)
        if rgb is not None and mode.color_mode == 1:
            await self.color(dev, [color] * len(dev.colors))

    async def profile(self, name: str) -> None:
        if name not in self.profiles:
            raise ValueError("Unknown OpenRGB profile")
        await self.command(152, payload=name.encode() + b"\0")
        for dev in (await self.read_devices()).values():
            if dev.mode:
                await self.mode(dev, dev.active_mode)
                if dev.mode.color_mode == 1:
                    await self.color(dev, dev.colors)

    async def effect(self, name: str, enabled: bool) -> None:
        if not self.plugin:
            raise ValueError("Compatible Effects plugin is not available")
        await self.command(
            201, self.plugin[0], struct.pack("<I", 20 if enabled else 21) + string(name)
        )
