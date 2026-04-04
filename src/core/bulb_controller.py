"""Central controller for WiZ bulb management."""

import asyncio
from dataclasses import dataclass
from typing import Optional

from pywizlight import wizlight, discovery, PilotBuilder


@dataclass
class BulbState:
    """Current state of a bulb."""
    ip: str
    mac: Optional[str]
    is_on: bool
    brightness: Optional[int]  # 0-255
    rgb: Optional[tuple[int, int, int]]
    color_temp: Optional[int]  # Kelvin


class BulbController:
    """Manages discovery and control of WiZ bulbs."""
    
    def __init__(self):
        self._bulbs: dict[str, wizlight] = {}  # ip -> wizlight instance
    
    async def discover(self, broadcast_space: str = "192.168.1.255") -> list[dict]:
        """
        Discover WiZ bulbs on the local network.
        
        Args:
            broadcast_space: Broadcast address for discovery (e.g., "192.168.1.255")
        
        Returns:
            List of discovered bulbs with ip and mac
        """
        bulbs = await discovery.discover_lights(broadcast_space=broadcast_space)
        
        discovered = []
        for bulb in bulbs:
            self._bulbs[bulb.ip] = bulb
            discovered.append({
                "ip": bulb.ip,
                "mac": bulb.mac,
            })
        
        return discovered
    
    def _get_bulb(self, ip: str) -> wizlight:
        """Get or create a wizlight instance for an IP."""
        if ip not in self._bulbs:
            self._bulbs[ip] = wizlight(ip)
        return self._bulbs[ip]
    
    async def get_state(self, ip: str) -> BulbState:
        """Get current state of a bulb."""
        bulb = self._get_bulb(ip)
        state = await bulb.updateState()
        
        rgb = None
        if state.get_rgb() is not None:
            rgb = state.get_rgb()
        
        return BulbState(
            ip=ip,
            mac=bulb.mac,
            is_on=state.get_state(),
            brightness=state.get_brightness(),
            rgb=rgb,
            color_temp=state.get_colortemp(),
        )
    
    async def turn_on(self, ip: str, brightness: Optional[int] = None) -> None:
        """Turn on a bulb with optional brightness."""
        bulb = self._get_bulb(ip)
        if brightness is not None:
            await bulb.turn_on(PilotBuilder(brightness=brightness))
        else:
            await bulb.turn_on()
    
    async def turn_off(self, ip: str) -> None:
        """Turn off a bulb."""
        bulb = self._get_bulb(ip)
        await bulb.turn_off()
    
    async def toggle(self, ip: str) -> bool:
        """Toggle a bulb. Returns new state (True=on)."""
        state = await self.get_state(ip)
        if state.is_on:
            await self.turn_off(ip)
            return False
        else:
            await self.turn_on(ip)
            return True
    
    async def set_brightness(self, ip: str, brightness: int) -> None:
        """Set bulb brightness (0-255)."""
        bulb = self._get_bulb(ip)
        brightness = max(0, min(255, brightness))
        await bulb.turn_on(PilotBuilder(brightness=brightness))
    
    async def set_rgb(self, ip: str, r: int, g: int, b: int, brightness: Optional[int] = None) -> None:
        """Set bulb to RGB color."""
        bulb = self._get_bulb(ip)
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        
        builder = PilotBuilder(rgb=(r, g, b))
        if brightness is not None:
            builder = PilotBuilder(rgb=(r, g, b), brightness=brightness)
        
        await bulb.turn_on(builder)
    
    async def set_color_temp(self, ip: str, kelvin: int, brightness: Optional[int] = None) -> None:
        """Set bulb color temperature (2200-6500K typically)."""
        bulb = self._get_bulb(ip)
        kelvin = max(2200, min(6500, kelvin))
        
        builder = PilotBuilder(colortemp=kelvin)
        if brightness is not None:
            builder = PilotBuilder(colortemp=kelvin, brightness=brightness)
        
        await bulb.turn_on(builder)
    
    async def set_scene(self, ip: str, scene_id: int) -> None:
        """Set bulb to a WiZ scene by ID."""
        bulb = self._get_bulb(ip)
        await bulb.turn_on(PilotBuilder(scene=scene_id))
    
    # Bulk operations for multiple bulbs
    
    async def turn_on_all(self, ips: list[str], brightness: Optional[int] = None) -> None:
        """Turn on multiple bulbs."""
        await asyncio.gather(*[self.turn_on(ip, brightness) for ip in ips])
    
    async def turn_off_all(self, ips: list[str]) -> None:
        """Turn off multiple bulbs."""
        await asyncio.gather(*[self.turn_off(ip) for ip in ips])
    
    async def toggle_all(self, ips: list[str]) -> None:
        """Toggle multiple bulbs."""
        await asyncio.gather(*[self.toggle(ip) for ip in ips])
    
    async def set_rgb_all(self, ips: list[str], r: int, g: int, b: int, brightness: Optional[int] = None) -> None:
        """Set multiple bulbs to same RGB color."""
        await asyncio.gather(*[self.set_rgb(ip, r, g, b, brightness) for ip in ips])

    async def set_rgb_map(
        self,
        colors_by_ip: dict[str, tuple[int, int, int]],
        brightness: Optional[int] = None,
    ) -> None:
        """Set per-bulb RGB values in a single batch."""
        await asyncio.gather(
            *[
                self.set_rgb(ip, color[0], color[1], color[2], brightness)
                for ip, color in colors_by_ip.items()
            ]
        )
    
    async def set_color_temp_all(self, ips: list[str], kelvin: int, brightness: Optional[int] = None) -> None:
        """Set multiple bulbs to same color temperature."""
        await asyncio.gather(*[self.set_color_temp(ip, kelvin, brightness) for ip in ips])

    async def close_async(self) -> None:
        """Close bulb transports on the event loop that created them."""
        bulbs = list(self._bulbs.values())
        self._bulbs.clear()

        if bulbs:
            await asyncio.gather(
                *(bulb.async_close() for bulb in bulbs),
                return_exceptions=True,
            )

    def close(self) -> None:
        """Drop cached bulb instances when async cleanup is unavailable."""
        self._bulbs.clear()


# Color presets
PRESETS = {
    "warm": {"color_temp": 2700},
    "cool": {"color_temp": 5000},
    "daylight": {"color_temp": 6500},
    "sunset": {"rgb": (255, 100, 50)},
    "party": {"rgb": (255, 0, 255)},
    "movie": {"rgb": (255, 147, 41), "brightness": 80},
    "reading": {"color_temp": 4000, "brightness": 200},
    "night": {"rgb": (255, 50, 0), "brightness": 30},
    "focus": {"color_temp": 5500, "brightness": 255},
    "relax": {"color_temp": 2700, "brightness": 100},
}


async def apply_preset(controller: BulbController, ips: list[str], preset_name: str) -> bool:
    """Apply a color preset to bulbs. Returns True if preset exists."""
    if preset_name not in PRESETS:
        return False
    
    preset = PRESETS[preset_name]
    brightness = preset.get("brightness")
    
    if "rgb" in preset:
        r, g, b = preset["rgb"]
        await controller.set_rgb_all(ips, r, g, b, brightness)
    elif "color_temp" in preset:
        await controller.set_color_temp_all(ips, preset["color_temp"], brightness)
    
    return True
