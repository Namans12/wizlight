# Alexa Voice Control Setup

Control your WiZ bulbs with Alexa using voice commands.

## How It Works

WizLight uses **fauxmo** to emulate WeMo smart plugs. Alexa can discover and control WeMo devices natively, so this gives you voice control without needing a custom Alexa Skill or cloud services.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Alexa     │────▶│   fauxmo    │────▶│  WizLight   │────▶ 💡
│  (Echo)     │     │ (WeMo emu)  │     │  (Python)   │
└─────────────┘     └─────────────┘     └─────────────┘
     Voice              UPnP              HTTP callback
```

## Quick Start

### 1. Start the Alexa bridge

```bash
wizlight alexa
```

This starts:
- fauxmo WeMo emulator (for Alexa discovery)
- HTTP callback server (for receiving commands)

### 2. Discover devices with Alexa

Say: **"Alexa, discover devices"**

Alexa will find these virtual devices:
- **WizLight** - Main on/off control
- **Party Mode** - Activate party preset
- **Movie Mode** - Activate movie preset
- **Reading Mode** - Activate reading preset
- **Relax Mode** - Activate relax preset

### 3. Control your lights

| Voice Command | Action |
|---------------|--------|
| "Alexa, turn on WizLight" | Turn all lights on |
| "Alexa, turn off WizLight" | Turn all lights off |
| "Alexa, turn on Party Mode" | Set party colors |
| "Alexa, turn on Movie Mode" | Set movie lighting |
| "Alexa, turn on Reading Mode" | Bright white light |
| "Alexa, turn off Party Mode" | Turn lights off |

## Requirements

- Amazon Echo or Alexa-enabled device
- All devices on the same WiFi network
- Network must support multicast/UPnP
- fauxmo installed: `pip install fauxmo`

## Troubleshooting

### Alexa can't find devices

1. **Check network**: Echo and PC must be on same subnet (e.g., 192.168.1.x)

2. **Check firewall**: Allow these ports:
   - UDP 1900 (UPnP discovery)
   - TCP 52000-52004 (virtual devices)
   - TCP 38900 (callback server)

3. **Restart discovery**: 
   - Stop `wizlight alexa`
   - Wait 30 seconds
   - Start `wizlight alexa` again
   - Say "Alexa, discover devices"

4. **Check fauxmo output**: Run with verbose logging:
   ```bash
   wizlight alexa --verbose
   ```

### Commands don't work

1. **Check callback server**: Visit `http://localhost:38900/action/WizLight/on` in browser

2. **Check bulbs**: Ensure bulbs are configured:
   ```bash
   wizlight list-bulbs
   ```

3. **Test directly**: Try CLI commands first:
   ```bash
   wizlight on
   wizlight preset party
   ```

### "Device is unresponsive"

This usually means the callback failed. Check:
- WizLight server is running
- Bulbs are online and on 2.4GHz WiFi
- No firewall blocking

## Advanced Configuration

### Custom devices

Create custom Alexa devices in Python:

```python
from src.features.alexa_bridge import AlexaBridge
from src.core.bulb_controller import BulbController

controller = BulbController()
bridge = AlexaBridge()

# Add custom device
bridge.add_device(
    name="Bedroom Light",
    port=52010,
    on_action=lambda: controller.turn_on("192.168.1.50"),
    off_action=lambda: controller.turn_off("192.168.1.50"),
)

# Start bridge
from src.features.alexa_bridge import start_action_server
start_action_server(bridge)
```

### Running as a service

Create a systemd service (Linux) or Task Scheduler task (Windows) to run `wizlight alexa` at startup.

**Linux systemd example** (`/etc/systemd/system/wizlight-alexa.service`):
```ini
[Unit]
Description=WizLight Alexa Bridge
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
ExecStart=/usr/local/bin/wizlight alexa
Restart=always

[Install]
WantedBy=multi-user.target
```

## About Clap Detection

**Note**: Alexa's microphone cannot be used for clap detection. Amazon Echo devices only process the wake word locally and don't expose raw audio.

For clap-to-toggle functionality, use the local clap detector instead:

```bash
wizlight clap
```

This uses your PC's microphone to detect claps and toggle the lights.

## Alternative: Alexa Routines

You can also create Alexa Routines that control WizLight devices:

1. Open Alexa app → More → Routines
2. Create new routine
3. Add trigger (voice command, time, etc.)
4. Add action → Smart Home → choose WizLight device
5. Save

This lets you create complex automations like:
- "Alexa, movie time" → dims lights, sets movie mode
- "Alexa, goodnight" → turns off all lights
- Sunset → automatically turn on relax mode
