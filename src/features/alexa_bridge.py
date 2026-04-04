"""Alexa integration via fauxmo (WeMo emulation)."""

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

from ..core.async_runtime import run_sync

# fauxmo uses its own plugin system, we'll create a custom handler


@dataclass
class AlexaDevice:
    """Virtual device exposed to Alexa."""
    name: str
    port: int
    on_action: Callable[[], None]
    off_action: Callable[[], None]


class AlexaBridge:
    """
    Bridge to expose WizLight actions to Alexa via fauxmo.
    
    This creates virtual WeMo devices that Alexa can discover and control.
    When Alexa sends on/off commands, we execute the configured actions.
    """
    
    def __init__(self, devices: Optional[list[AlexaDevice]] = None):
        self.devices = devices or []
        self._config_path = Path.home() / ".wizlight" / "fauxmo_config.json"
    
    def add_device(self, name: str, port: int, on_action: Callable, off_action: Callable) -> None:
        """Add a virtual device for Alexa."""
        self.devices.append(AlexaDevice(name, port, on_action, off_action))
    
    def _generate_config(self) -> dict:
        """Generate fauxmo configuration."""
        return {
            "FAUXMO": {
                "ip_address": "auto"
            },
            "PLUGINS": {
                "SimpleHTTPPlugin": {
                    "DEVICES": [
                        {
                            "name": device.name,
                            "port": device.port,
                            "on_cmd": f"http://localhost:38900/action/{device.name}/on",
                            "off_cmd": f"http://localhost:38900/action/{device.name}/off",
                        }
                        for device in self.devices
                    ]
                }
            }
        }
    
    def save_config(self) -> Path:
        """Save fauxmo configuration to file."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        config = self._generate_config()
        
        with open(self._config_path, "w") as f:
            json.dump(config, f, indent=2)
        
        return self._config_path
    
    def get_setup_instructions(self) -> str:
        """Get instructions for setting up Alexa integration."""
        return """
Alexa Integration Setup:
========================

1. First, save the fauxmo config and start the bridge:
   
   from src.features.alexa_bridge import AlexaBridge, create_default_bridge
   from src.core.bulb_controller import BulbController
   from src.core.config import Config
   controller = BulbController()
   bulb_ips = [b.ip for b in Config.load().bulbs]
   bridge = create_default_bridge(controller, bulb_ips)
   bridge.save_config()

2. Install fauxmo if not already installed:
   pip install fauxmo

3. Run fauxmo with the generated config:
   fauxmo -c ~/.wizlight/fauxmo_config.json -v

4. Ask Alexa to discover devices:
   "Alexa, discover devices"

5. Control your lights:
   "Alexa, turn on Party Mode"
   "Alexa, turn off WizLight"

Note: All devices must be on the same network subnet.
Fauxmo uses UPnP for discovery, which requires multicast support.
"""


def create_default_bridge(controller, bulb_ips: list[str]) -> AlexaBridge:
    """Create an Alexa bridge with default device mappings."""
    from ..core.bulb_controller import apply_preset
    
    bridge = AlexaBridge()

    def run_action(action: Callable[[], object]) -> None:
        async def wrapped() -> None:
            try:
                await action()
            finally:
                await controller.close_async()

        run_sync(wrapped())
    
    # Main light control
    def lights_on():
        run_action(lambda: controller.turn_on_all(bulb_ips))
    
    def lights_off():
        run_action(lambda: controller.turn_off_all(bulb_ips))
    
    bridge.add_device("WizLight", 52000, lights_on, lights_off)
    
    # Preset modes
    def party_on():
        run_action(lambda: apply_preset(controller, bulb_ips, "party"))
    
    def movie_on():
        run_action(lambda: apply_preset(controller, bulb_ips, "movie"))
    
    def reading_on():
        run_action(lambda: apply_preset(controller, bulb_ips, "reading"))
    
    def relax_on():
        run_action(lambda: apply_preset(controller, bulb_ips, "relax"))
    
    bridge.add_device("Party Mode", 52001, party_on, lights_off)
    bridge.add_device("Movie Mode", 52002, movie_on, lights_off)
    bridge.add_device("Reading Mode", 52003, reading_on, lights_off)
    bridge.add_device("Relax Mode", 52004, relax_on, lights_off)
    
    return bridge


# Simple HTTP server to receive fauxmo commands
# This allows fauxmo to call back into our Python code

from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

_action_handlers: dict[str, dict[str, Callable]] = {}


class ActionHandler(BaseHTTPRequestHandler):
    """HTTP handler for fauxmo callbacks."""
    
    def do_GET(self):
        # Parse /action/{device_name}/{on|off}
        request_path = urllib.parse.urlparse(self.path).path
        parts = request_path.split("/")
        if len(parts) >= 4 and parts[1] == "action":
            device_name = urllib.parse.unquote(parts[2])
            action = parts[3]
            
            if device_name in _action_handlers:
                handler = _action_handlers[device_name].get(action)
                if handler:
                    try:
                        handler()
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"OK")
                        return
                    except Exception as e:
                        print(f"Action error: {e}")
        
        self.send_response(404)
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logging


def start_action_server(bridge: AlexaBridge, port: int = 38900) -> HTTPServer:
    """Start HTTP server to receive fauxmo callbacks."""
    global _action_handlers
    
    # Register handlers
    for device in bridge.devices:
        _action_handlers[device.name] = {
            "on": device.on_action,
            "off": device.off_action,
        }
    
    server = HTTPServer(("localhost", port), ActionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    return server
