# OpenRGB Plus for Home Assistant

A HACS custom integration for OpenRGB 1.0-era SDK servers, with per-device RGB lights, hardware effects, saved profiles, and optional Effects plugin switches.

## Install with HACS

1. In **HACS → ⋮ → Custom repositories**, add `https://github.com/dmitry-helios/openrgb-plus` and choose **Integration**.
2. Download **OpenRGB Plus**, then restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → OpenRGB Plus**.
4. Enter the LAN IP address of the computer running OpenRGB and SDK port **6742**.

Requires Home Assistant **2025.11 or newer**. This is an integration, not a Supervisor add-on. It connects to an existing OpenRGB installation; it does not install OpenRGB on Home Assistant.

In OpenRGB, start the **SDK Server**, listening on `0.0.0.0` or the computer's LAN interface. Permit TCP 6742 from Home Assistant through the computer's firewall. `0.0.0.0` is a listening address, not the address to enter in Home Assistant. `127.0.0.1` points to Home Assistant itself unless both programs share a host/network namespace. Use a trusted LAN; the SDK has no authentication or encryption.

Disable the built-in OpenRGB integration if it is also controlling these devices, to avoid competing commands. OpenRGB Plus uses its own `openrgb_plus` domain and does not overwrite the built-in integration or migrate its entity IDs. Legacy Workaround can remain enabled: this client sends correctly sized protocol packets.

If OpenRGB runs as a Windows service, check the service's SDK listening address as well as the desktop app's settings. They can use separate configurations; a service listening only on `127.0.0.1` cannot accept connections from another computer. After changing the service configuration, restart the OpenRGB service with administrator privileges. Version 0.2.2 includes the server address and an explicit timeout or connection-refused explanation in HA errors.

## Controls

| Entity | What it controls |
| --- | --- |
| Light for each controller | On/off, RGB color, brightness where the active mode supports it, and the hardware modes advertised by that controller |
| Light for each populated zone | Independent RGB color, software brightness, and on/off, grouped under the same physical device |
| Light for each valid custom segment | Controls a configured subsection of a zone, using its OpenRGB segment name |
| Profile selector on the server | Loads an existing saved OpenRGB profile and reapplies its controller modes and LED colors |
| Optional Effects switches | Starts/stops effects already configured in the OpenRGB Effects plugin |

Both identically named RAM sticks get distinct IDs using their hardware metadata/location. If devices expose identical metadata, an occurrence suffix distinguishes them; their assignment can change when identical devices are reordered. You can rename entities in Home Assistant.

### Zone lights (0.2.0)

Version 0.2.1 also discovers **custom segments** within zones. Each valid, non-empty segment becomes another light using its OpenRGB name. Segment commands update the parent zone from a freshly read color buffer, replacing only the segment's LED range. Parent-zone and whole-device lights remain available. Overlapping segments share physical LEDs, so changes to the overlap are reflected in both lights. Segments outside the physical color range are not exposed, including virtual subdivisions of a single non-addressable RGB output. Renaming a segment changes its entity identity; changing its range keeps it. Other RGB clients can race with a segment's read/modify/write, so avoid competing writers for predictable results.

Populated OpenRGB zones are discovered automatically as additional HA lights, named after the zones shown in OpenRGB. Existing whole-device entity IDs stay unchanged. Zone lights support dashboards, groups, scenes, and standard `light.turn_on`/`light.turn_off` actions. Whole-device commands and profiles still affect all zones.

Select **Direct** (or another mode using per-LED colors) on the whole-device light before controlling zones. A zone command sends only that zone's colors; it does not change a shared hardware mode or shared hardware brightness. If the active device/zone mode does not accept per-LED colors, the command returns an explanatory error. Hardware effects remain on the whole-device lights; zone effect selection is not exposed in this release. Running plugin effects can overwrite zone colors.

Zone brightness scales its RGB values. Shared hardware brightness remains a master multiplier, so set the device brightness to maximum for the full zone range. Off sets only that zone to black; on restores its last color/brightness held during the current HA session. Multi-color zones report their first non-black LED. Zones with zero configured LEDs appear once configured in OpenRGB. Non-addressable RGB headers with virtual effect lengths occupy one physical color slot and are handled accordingly. Zone IDs use the original zone name and an occurrence suffix; resizing or changing its display name keeps the ID, while reordering otherwise identical duplicate names can change their assignment.

Selecting a color normally switches to Direct, Static, or Custom mode. Supply both `effect` and `rgb_color` to color a supported hardware effect. Brightness is native when the mode supports it; otherwise RGB values are scaled. Modes without color or brightness support expose only the controls they support. Off uses the hardware Off mode when available, otherwise black in a color-capable mode. RGB/brightness state represents the first non-black LED for multi-color devices.

Since 0.1.1, selecting a color-capable hardware effect (for example Direct → Breathing) carries over the current color and brightness rather than restoring that effect's old palette. Explicit color/brightness values still take precedence. Effects such as Rainbow that generate their own colors keep their normal behavior. Multi-color layouts use the first non-black LED as the representative color.

On SDK v6, the profile selector polls and displays the **active profile reported by the server**. On SDK v3–v5, it can only report the last profile requested by Home Assistant; that fallback selection resets on reconnection and after a light/effect command, and changes made in OpenRGB may not be reflected.

### Effects plugin

Install a compatible [OpenRGB Effects plugin](https://openrgb.org/plugins), create effects, and assign devices/zones in OpenRGB first. This integration discovers Effects SDK versions 1 and 2, then exposes one switch per advertised effect name. The plugin API addresses effects by name, so duplicate names cannot be controlled independently. It does not create effects or edit their internal parameters.

Hardware modes and plugin effects are different: a running plugin can continually overwrite manual colors and hardware modes. Stop the relevant plugin effect before setting a static color. Plugin discovery is optional; if the server does not respond, lights and profiles still work and discovery retries after 60 seconds. You can disable it in the integration's **Configure** options. Unknown plugin protocol versions are not controlled.

If OpenRGB's GUI connects as an **SDK client** to a background system service, plugins loaded only in the GUI may not be accessible through the service's SDK endpoint. The release 1.0 server sends no plugin-list response when it has no plugin manager. In that arrangement, use an SDK server hosted by the plugin-bearing OpenRGB instance (a different port if the service occupies 6742), then configure the integration for that endpoint. Do not assume that an Effects tab in a connected GUI means its background service exposes the plugin API.

## Automation examples

Replace these example entity IDs with the IDs shown in your installation.

```yaml
action: select.select_option
target:
  entity_id: select.openrgb_profile
data:
  option: Example Green
```

```yaml
action: light.turn_on
target:
  entity_id: light.test_ram
data:
  rgb_color: [0, 255, 0]
  brightness: 128
  effect: Static
```

```yaml
action: switch.turn_on
target:
  entity_id: switch.openrgb_effect_rainbow
```

Polling defaults to 10 seconds, configurable from 5 to 300 seconds. Disconnected servers become unavailable and reconnect automatically. Newly discovered devices/effects are added without restarting Home Assistant. Removed ones become unavailable; stale registry entries can be removed manually.

## Why a new integration?

The [official integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/openrgb) currently uses `openrgb-python==0.3.8`, whose upstream client negotiates SDK v4. Its light implementation does not provide profile selectors or Effects plugin switches. Forking just the entity layer would retain that protocol dependency.

OpenRGB Plus is a separate implementation with a small asynchronous SDK client, bounded packet parsing, serialized commands, connection timeouts and no external Python dependencies. It negotiates **SDK v6**, supports v3–v6 controller layouts, and preserves unknown hardware/zone type values rather than rejecting new types. Protocol v6 uses stable controller IDs and waits for command acknowledgements, surfacing server rejections. Per-zone modes are parsed; zone color control is available in per-LED modes, while effect selection remains at whole-device level. Remote settings are not exposed. Supporting a protocol version is not a guarantee that every controller or downstream build behaves identically.

Sources consulted:

- [OpenRGB SDK protocol documentation](https://gitlab.com/CalcProgrammer1/OpenRGB/-/blob/master/Documentation/OpenRGBSDK.md)
- [OpenRGB network server implementation](https://github.com/CalcProgrammer1/OpenRGB/blob/master/NetworkServer.cpp)
- [Effects plugin SDK command implementation](https://gitlab.com/OpenRGBDevelopers/OpenRGBEffectsPlugin/-/blob/master/OpenRGBEffectsPlugin.cpp)
- [Home Assistant light entity API](https://developers.home-assistant.io/docs/core/entity/light/)
- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)

## Validation and development

Protocol tests exercise synthetic v3/v4/v5/v6 packets, unknown hardware types, RAM identity, truncated data, a real local TCP fake server with fragmented replies, color/mode/profile/plugin commands, and recovery from optional plugin timeouts. Linux CI additionally tests entities against real Home Assistant imports. These tests do not simulate every hardware controller.

Discovery and reversible device control have been verified against a live OpenRGB 1.0 server, including its saved profile list. See [VALIDATION.md](VALIDATION.md) for the current live-test scope and limitations. Installation into a real Home Assistant instance remains a user acceptance step.

```sh
python -m venv .venv
# Activate the environment, then:
pip install pytest pytest-asyncio ruff
python -m pytest tests/test_sdk.py
ruff check custom_components tests
```

On Linux, install `homeassistant==2025.11.0` to run the Home Assistant entity tests too. For troubleshooting, enable debug logging for `custom_components.openrgb_plus`, check the SDK server and firewall, and include OpenRGB/HA versions and the exception when reporting an issue. Do not include credentials or private network dumps.


