# WizLight - Copilot Instructions

## Build & Run Commands

```bash
# Install dependencies
pip install -e .
# Or: pip install -r requirements.txt

# Run CLI
wizlight discover          # Find bulbs
wizlight on/off/toggle     # Basic control
wizlight preset party      # Apply preset

# Run GUI
python -m src.gui.app

# Run feature scripts
python scripts/sync_screen.py   # Screen color sync
python scripts/clap_toggle.py   # Clap detection

# Run tests
pytest                          # All tests
pytest tests/test_bulb.py       # Single file
pytest -k "test_toggle"         # Single test by name
```

## Architecture

```
src/
├── core/                 # Bulb control foundation
│   ├── bulb_controller.py   # BulbController class - all bulb operations
│   └── config.py            # Config dataclass - persists to ~/.wizlight/config.json
├── features/             # Optional features (each standalone)
│   ├── screen_sync.py       # ScreenSync class - captures screen, extracts color
│   ├── clap_detector.py     # ClapDetector class - audio input, clap recognition
│   └── alexa_bridge.py      # AlexaBridge class - fauxmo integration
├── cli/
│   └── commands.py          # Click CLI - entry point: `wizlight`
└── gui/
    └── app.py               # Tkinter GUI - WizLightGUI class
```

**Key patterns:**
- `BulbController` is the single interface for all bulb operations (async methods)
- Features (screen_sync, clap_detector) take callbacks; they don't know about bulbs directly
- Config uses dataclasses with JSON serialization to `~/.wizlight/config.json`
- GUI runs asyncio loop in background thread, uses `_run_async()` for bulb commands

## Key Dependencies

- **pywizlight** - WiZ bulb communication over UDP (async)
- **mss** - Fast screen capture
- **sounddevice** - Audio input for clap detection
- **fauxmo** - WeMo emulation for Alexa

## Conventions

- All bulb operations are async (use `asyncio.run()` or run in event loop)
- Bulk operations use `*_all()` methods: `turn_on_all()`, `set_rgb_all()`, etc.
- IPs are the primary bulb identifier (stored in config)
- Color presets defined in `PRESETS` dict in `bulb_controller.py`
- Features use threading with `daemon=True` for background processing

## Adding New Features

1. Create module in `src/features/` with a class that:
   - Takes a callback for events (don't couple to bulb controller)
   - Has `start()` and `stop()` methods
   - Uses `threading.Thread(daemon=True)` for background work

2. Add CLI command in `src/cli/commands.py`

3. Add toggle in `src/gui/app.py` features section

## Running the Application

Always use module entry points (requires `__main__.py`):
- `python -m src.gui` — launches the GUI (not `python -m src.gui.app`)
- `python -m src.cli` — launches CLI

## Network Notes

- WiZ uses UDP broadcast on port 38899
- Discovery broadcasts to `192.168.1.255` by default (adjust for your subnet)
- Ensure Windows Firewall allows UDP 38899

## Troubleshooting

### Bulb Discovery Fails
WiZ bulbs only work on 2.4GHz WiFi. If discovery fails:
1. Check if PC is on 5GHz network — switch to 2.4GHz temporarily
2. Get bulb IP from WiZ phone app (Settings → Device Info) and use `wizlight add-bulb <IP>`
3. Run `python scripts/network_diagnostic.py` for detailed diagnostics

### pywizlight Event Loop Cleanup
When using pywizlight with Tkinter/GUI, clear bulb references before stopping the asyncio loop to avoid `RuntimeError: Event loop is closed`:
```python
# In shutdown handler
self.controller._bulbs.clear()  # Clear references BEFORE stopping loop
self._async_runner.stop()
```
