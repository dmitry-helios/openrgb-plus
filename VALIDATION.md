# Validation record

## Automated

See the Tests workflow for protocol and Home Assistant entity checks. The SDK tests run locally without Home Assistant. Entity tests are skipped when Home Assistant is not installed; CI installs Home Assistant 2025.11.0 explicitly.

## Live OpenRGB checks

- Negotiated native protocol 6 successfully with Legacy Workaround enabled.
- Discovered the test server controllers, hardware mode lists and saved profiles.
- The live server did not respond to optional plugin-list requests. Plugin controls are wire-tested but not yet verified against this running plugin installation.
- Verified color, brightness, hardware effects (Breathing/Pulse), and off on all tested controllers with protocol 6. Loaded the saved profile and refreshed its devices. Restored the original modes and LED colors and verified them through SDK reads.
- An initial protocol 5 attempt could discover these devices but writes did not apply correctly. Inspection of the released server's queued controller-command path indicated an ID/index compatibility issue. Native protocol 6 addresses controllers by ID and resolves this in the live test. The old Home Assistant integration was disabled during final testing.
- No deployment into the user's Home Assistant instance has been performed.

