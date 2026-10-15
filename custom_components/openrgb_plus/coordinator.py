"""Serialize discovery, polling and commands on a single SDK connection."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, DEFAULT_SCAN_INTERVAL
from .sdk import Client

LOGGER = logging.getLogger(__name__)
ERRORS = (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError)


def describe_error(client, err):
    """Include the endpoint and a useful description even for empty exceptions."""
    if isinstance(err, TimeoutError):
        detail = "Timed out connecting to or waiting for the SDK server"
    elif isinstance(err, ConnectionRefusedError):
        detail = "Connection refused; check that the SDK server is listening on its LAN address"
    else:
        detail = str(err).strip() or type(err).__name__
    return f"OpenRGB {client.host}:{client.port}: {detail}"


class OpenRGBCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.entry = entry
        self.client = Client(entry.data["host"], entry.data["port"])
        self.lock = asyncio.Lock()
        self.last_profile = None

    async def _snapshot(self):
        return await self.client.snapshot(self.entry.options.get("effects_plugin", True))

    async def _async_update_data(self):
        async with self.lock:
            try:
                return await self._snapshot()
            except ERRORS as err:
                self.last_profile = None
                await self.client.close()
                raise UpdateFailed(describe_error(self.client, err)) from err
            except asyncio.CancelledError:
                await self.client.close()
                raise

    async def command(self, action, *args, **kwargs):
        async with self.lock:
            try:
                # Refresh both device indexes and plugin indexes before a command.
                snapshot = await self._snapshot()
                await self.client.connect()
                if action == "effect" and args[0] not in snapshot.effects:
                    raise ValueError("Effect is no longer configured")
                await getattr(self.client, action)(*args, **kwargs)
                self.last_profile = args[0] if action == "profile" else None
                self.async_set_updated_data(await self._snapshot())
            except ERRORS as err:
                await self.client.close()
                message = describe_error(self.client, err)
                self.async_set_update_error(UpdateFailed(message))
                raise HomeAssistantError(message) from err
            except asyncio.CancelledError:
                await self.client.close()
                raise
