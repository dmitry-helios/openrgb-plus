"""Configure an OpenRGB SDK server through the UI."""

import asyncio
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, DEFAULT_PORT, DEFAULT_SCAN_INTERVAL
from .sdk import Client, ProtocolError


class OpenRGBConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input["host"].strip().lower()
            port = user_input["port"]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            client = Client(host, port)
            try:
                await client.connect()
                await client.read_devices()
            except ProtocolError:
                errors["base"] = "unsupported_protocol"
            except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
                errors["base"] = "cannot_connect"
            finally:
                await client.close()
            if not errors:
                return self.async_create_entry(
                    title=user_input["name"], data={**user_input, "host": host}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default="OpenRGB"): str,
                    vol.Required("host"): str,
                    vol.Required("port", default=DEFAULT_PORT): cv.port,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OpenRGBOptionsFlow()


class OpenRGBOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "scan_interval", default=options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                    vol.Required(
                        "effects_plugin", default=options.get("effects_plugin", True)
                    ): bool,
                }
            ),
        )
