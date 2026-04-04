# WizLight

Advanced WiZ smart light controller with screen sync, clap detection, and Alexa integration.

## Features

- **Basic Control** - On/off, brightness, RGB color, color temperature
- **Color Presets** - warm, cool, daylight, sunset, party, movie, reading, night, focus, relax
- **Screen Sync** - Match bulb color to your screen content in real-time
- **Clap Detection** - Double clap to toggle lights on/off
- **Alexa Integration** - Control via Alexa voice commands (via fauxmo)
- **CLI & GUI** - Both command-line and graphical interfaces

## Installation

```bash
# Clone and install
cd wizlight
pip install -e .

# Or install dependencies directly
pip install -r requirements.txt
```

## Quick Start

### 1. Discover your bulbs

```bash
wizlight discover
```

This scans your network for WiZ bulbs and saves them to `~/.wizlight/config.json`.

### 2. Control your lights

```bash
# Power
wizlight on
wizlight off
wizlight toggle

# Brightness (0-255)
wizlight brightness 200

# RGB Color
wizlight color 255 100 50

# Color Temperature (2200-6500K)
wizlight temp 4000

# Presets
wizlight preset party
wizlight preset movie
wizlight preset        # List all presets
```

### 3. Launch GUI

```bash
# Modern GUI (recommended)
python -m src.gui

# Classic GUI
python -m src.gui.app
```

### 4. Run feature scripts

```bash
# Screen sync (matches bulbs to screen color)
python scripts/sync_screen.py

# Clap detection (double clap to toggle)
python scripts/clap_toggle.py

# Quick preset
python scripts/color_preset.py sunset
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `wizlight discover` | Find WiZ bulbs on network |
| `wizlight status` | Show bulb status |
| `wizlight on [-i IP] [-b BRIGHTNESS]` | Turn on |
| `wizlight off [-i IP]` | Turn off |
| `wizlight toggle [-i IP]` | Toggle on/off |
| `wizlight brightness LEVEL [-i IP]` | Set brightness (0-255) |
| `wizlight color R G B [-i IP]` | Set RGB color |
| `wizlight temp KELVIN [-i IP]` | Set color temperature |
| `wizlight preset [NAME] [-i IP]` | Apply preset |

Use `-i IP` to target a specific bulb; omit for all bulbs.

## Configuration

Config is stored at `~/.wizlight/config.json`:

```json
{
  "bulbs": [
    {"ip": "192.168.1.100", "name": "Bulb 1", "mac": "AA:BB:CC:DD:EE:FF"}
  ],
  "screen_sync": {"enabled": false, "fps": 15},
  "clap": {"enabled": false, "threshold": 0.3, "double_clap": true}
}
```

## Alexa Integration

WizLight uses [fauxmo](https://github.com/n8henrie/fauxmo) to create virtual WeMo devices that Alexa can discover.

1. Generate fauxmo config:
   ```python
   from src.features.alexa_bridge import create_default_bridge
   from src.core.bulb_controller import BulbController
   from src.core.config import Config
   
   config = Config.load()
   controller = BulbController()
   bridge = create_default_bridge(controller, [b.ip for b in config.bulbs])
   bridge.save_config()
   ```

2. Run fauxmo:
   ```bash
   fauxmo -c ~/.wizlight/fauxmo_config.json -v
   ```

3. Say "Alexa, discover devices"

4. Control with voice:
   - "Alexa, turn on WizLight"
   - "Alexa, turn on Party Mode"
   - "Alexa, turn on Movie Mode"

## Network Requirements

- WiZ bulbs communicate over UDP port 38899
- All devices must be on the same subnet
- Windows Firewall may need an exception for discovery
- Alexa integration requires UPnP/multicast support

## Troubleshooting

**No bulbs found during discovery:**
- Check bulbs are connected to WiFi and on same network
- Try different broadcast address: `wizlight discover -b 192.168.0.255`
- Ensure UDP port 38899 isn't blocked

**Screen sync is laggy:**
- Reduce FPS in config or use smaller sample size
- Close other GPU-intensive applications

**Clap detection not working:**
- Check microphone permissions in Windows Settings
- Adjust threshold in config (lower = more sensitive)
- Ensure room isn't too noisy

## License

MIT
