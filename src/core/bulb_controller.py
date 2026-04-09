"""Central controller for WiZ bulb management."""

import asyncio
from dataclasses import dataclass
from typing import Optional

from pywizlight import wizlight, discovery, PilotBuilder

from .calibration import (
    BulbCalibrationTable,
    CalibrationStore,
    ToneCalibrationStore,
    sanitize_calibration_key,
)
from .color_mapping import BulbColorProfile, BulbGamutMapper

PROFILE_QUERY_TIMEOUT = 2.5


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
        self._color_profiles: dict[str, BulbColorProfile] = {}
        self._gamut_mappers: dict[str, BulbGamutMapper] = {}
        self._calibration_store = CalibrationStore()
        self._tone_store = ToneCalibrationStore()
        self._calibration_tables: dict[str, BulbCalibrationTable] = {}
        self._tone_tables: dict[str, BulbCalibrationTable] = {}
    
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

    def invalidate_screen_sync_mapping(self, ips: Optional[list[str]] = None) -> None:
        """Drop cached profiles and mappers so new calibration/tone files are picked up."""

        if not ips:
            self._color_profiles.clear()
            self._gamut_mappers.clear()
            self._calibration_tables.clear()
            self._tone_tables.clear()
            return

        for ip in ips:
            profile = self._color_profiles.pop(ip, None)
            self._gamut_mappers.pop(ip, None)
            self._calibration_tables.pop(sanitize_calibration_key(ip), None)
            self._tone_tables.pop(sanitize_calibration_key(ip), None)
            if profile and profile.mac:
                key = sanitize_calibration_key(profile.mac)
                self._calibration_tables.pop(key, None)
                self._tone_tables.pop(key, None)

    async def get_color_profile(self, ip: str) -> BulbColorProfile:
        """Fetch and cache hardware hints used for screen-sync color mapping."""

        if ip in self._color_profiles:
            return self._color_profiles[ip]

        bulb = self._get_bulb(ip)
        profile = BulbColorProfile()

        system_result = await self._query_profile_metadata(bulb.getBulbConfig())
        model_result = await self._query_profile_metadata(bulb.getModelConfig())
        user_result = await self._query_profile_metadata(bulb.getUserConfig())

        rgb_current = profile.rgb_channel_current
        driver = model_result.get("i2cDrv")
        if isinstance(driver, list) and driver:
            currents = driver[0].get("curr")
            if isinstance(currents, list) and len(currents) >= 3:
                rgb_current = tuple(int(max(1, value)) for value in currents[:3])

        profile = BulbColorProfile(
            model_name=system_result.get("moduleName"),
            mac=system_result.get("mac") or bulb.mac,
            white_channels=int(model_result.get("nowc", profile.white_channels)),
            white_to_color_ratio=int(model_result.get("wcr", profile.white_to_color_ratio)),
            rgb_channel_current=rgb_current,
            render_factor=tuple(int(value) for value in model_result.get("renderFactor", ())),
            fade_in_ms=int(user_result.get("fadeIn", 0)),
            fade_out_ms=int(user_result.get("fadeOut", 0)),
        )
        calibration = self._load_calibration(profile.mac, ip)
        tone_lut = self._load_tone_lut(profile.mac, ip)
        if calibration is not None and (not profile.model_name or not profile.mac):
            profile = BulbColorProfile(
                model_name=profile.model_name or calibration.bulb_model,
                mac=profile.mac or calibration.bulb_mac,
                white_channels=profile.white_channels,
                white_to_color_ratio=profile.white_to_color_ratio,
                rgb_channel_current=profile.rgb_channel_current,
                render_factor=profile.render_factor,
                fade_in_ms=profile.fade_in_ms,
                fade_out_ms=profile.fade_out_ms,
            )
        self._color_profiles[ip] = profile
        self._gamut_mappers[ip] = BulbGamutMapper(
            profile,
            calibration=calibration,
            tone_lut=tone_lut,
        )
        return profile

    async def _query_profile_metadata(self, request) -> dict:
        """Return WiZ profile metadata without letting a slow query stall startup."""

        try:
            response = await asyncio.wait_for(request, timeout=PROFILE_QUERY_TIMEOUT)
        except Exception:
            return {}
        if not isinstance(response, dict):
            return {}
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    def _load_calibration(
        self,
        mac: Optional[str],
        ip: Optional[str],
    ) -> Optional[BulbCalibrationTable]:
        """Load a saved calibration table using MAC first, then IP fallback."""

        calibration = self._calibration_store.load_any((mac, ip))
        if calibration is None:
            return None

        if mac:
            self._calibration_tables[sanitize_calibration_key(mac)] = calibration
        if ip:
            self._calibration_tables[sanitize_calibration_key(ip)] = calibration
        return calibration

    def _load_tone_lut(
        self,
        mac: Optional[str],
        ip: Optional[str],
    ) -> Optional[BulbCalibrationTable]:
        """Load a saved video-tone correction table using MAC first, then IP fallback."""

        tone_lut = self._tone_store.load_any((mac, ip))
        if tone_lut is None:
            return None

        if mac:
            self._tone_tables[sanitize_calibration_key(mac)] = tone_lut
        if ip:
            self._tone_tables[sanitize_calibration_key(ip)] = tone_lut
        return tone_lut

    async def resolve_screen_sync_targets(self, ips: list[str]) -> list[str]:
        """Return reachable, deduplicated bulbs for low-latency screen sync."""

        resolved, _ = await self._classify_screen_sync_targets(ips)
        return resolved

    async def find_stale_bulbs(self, ips: list[str]) -> list[str]:
        """Return unreachable or duplicate bulbs that should be pruned from config."""

        _, stale = await self._classify_screen_sync_targets(ips)
        return stale

    async def _classify_screen_sync_targets(self, ips: list[str]) -> tuple[list[str], list[str]]:
        """Split configured bulbs into reachable targets and stale entries."""

        unique_ips = list(dict.fromkeys(ips))
        results = await asyncio.gather(
            *(self._resolve_screen_sync_target(ip) for ip in unique_ips),
            return_exceptions=True,
        )

        resolved: list[str] = []
        stale: list[str] = []
        seen_macs: set[str] = set()
        for ip, result in zip(unique_ips, results):
            if isinstance(result, Exception) or result is None:
                stale.append(ip)
                continue
            key = result.mac or result.ip or ip
            if key in seen_macs:
                stale.append(ip)
                continue
            seen_macs.add(key)
            resolved.append(ip)
        return resolved, stale

    async def _resolve_screen_sync_target(self, ip: str) -> Optional[BulbState]:
        """Return bulb state only when the target answers a real local WiZ request."""

        return await asyncio.wait_for(self.get_state(ip), timeout=1.8)

    async def refresh_screen_sync_targets(self, ips: list[str]) -> list[str]:
        """Refresh cached screen-sync mapping state, then resolve reachable targets."""

        self.invalidate_screen_sync_mapping(ips)
        return await self.resolve_screen_sync_targets(ips)

    def get_screen_sync_mapping_flags(self, ip: str) -> dict[str, bool | str | None]:
        """Return whether a reachable bulb currently has camera calibration and tone tuning."""

        profile = self._color_profiles.get(ip)
        primary_key = sanitize_calibration_key(profile.mac) if profile and profile.mac else None
        ip_key = sanitize_calibration_key(ip)
        calibration_active = ip_key in self._calibration_tables or (
            primary_key in self._calibration_tables if primary_key else False
        )
        tone_active = ip_key in self._tone_tables or (
            primary_key in self._tone_tables if primary_key else False
        )
        return {
            "model_name": profile.model_name if profile else None,
            "camera_calibration": calibration_active,
            "tone_lut": tone_active,
        }

    def summarize_screen_sync_mapping(self, ips: list[str]) -> str:
        """Return a compact user-facing summary of active screen-sync mapping layers."""

        flags = [self.get_screen_sync_mapping_flags(ip) for ip in ips]
        parts: list[str] = []
        if any(bool(flag["camera_calibration"]) for flag in flags):
            parts.append("camera calibration")
        if any(bool(flag["tone_lut"]) for flag in flags):
            parts.append("tone LUT")
        return ", ".join(parts)
    
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

    def _build_exact_rgb_builder(
        self,
        r: int,
        g: int,
        b: int,
        brightness: Optional[int] = None,
    ) -> PilotBuilder:
        """Build a direct RGB payload without pywizlight's RGB-to-RGBCW remapping."""

        params = {"rgbw": (r, g, b, 0)}
        if brightness is not None:
            params["brightness"] = brightness
        return PilotBuilder(**params)

    def _build_exact_sync_payload_builder(
        self,
        payload: tuple[int, ...],
        brightness: Optional[int] = None,
    ) -> PilotBuilder:
        """Build a direct RGBW/RGBWW payload without additional mapping layers."""

        params = {"rgbw": payload} if len(payload) == 4 else {"rgbww": payload}
        if brightness is not None:
            params["brightness"] = brightness
        return PilotBuilder(**params)

    async def _build_screen_sync_builder(
        self,
        ip: str,
        r: int,
        g: int,
        b: int,
        brightness: Optional[int] = None,
    ) -> PilotBuilder:
        """Build a bulb-aware sync payload using cached hardware hints."""

        mapper = self._gamut_mappers.get(ip)
        if mapper is None:
            profile = await self.get_color_profile(ip)
            calibration = self._load_calibration(profile.mac, ip)
            tone_lut = self._load_tone_lut(profile.mac, ip)
            mapper = BulbGamutMapper(profile, calibration=calibration, tone_lut=tone_lut)
            self._gamut_mappers[ip] = mapper

        payload = mapper.map_rgb((r, g, b))
        return self._build_exact_sync_payload_builder(payload, brightness)
    
    async def set_rgb(self, ip: str, r: int, g: int, b: int, brightness: Optional[int] = None) -> None:
        """Set bulb to RGB color."""
        bulb = self._get_bulb(ip)
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        
        builder = PilotBuilder(rgb=(r, g, b))
        if brightness is not None:
            builder = PilotBuilder(rgb=(r, g, b), brightness=brightness)
        
        await bulb.turn_on(builder)

    async def set_rgb_exact(
        self,
        ip: str,
        r: int,
        g: int,
        b: int,
        brightness: Optional[int] = None,
    ) -> None:
        """Set bulb to an exact RGB payload for screen-sync style ambience."""

        bulb = self._get_bulb(ip)
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        await bulb.turn_on(self._build_exact_rgb_builder(r, g, b, brightness))

    async def set_screen_sync_rgb(
        self,
        ip: str,
        r: int,
        g: int,
        b: int,
        brightness: Optional[int] = None,
    ) -> None:
        """Set bulb color using the bulb-aware screen-sync mapper."""

        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        if max(r, g, b) == 0 and (brightness is None or brightness <= 0):
            await self.turn_off(ip)
            return

        bulb = self._get_bulb(ip)
        await bulb.turn_on(await self._build_screen_sync_builder(ip, r, g, b, brightness))

    async def set_screen_sync_payload(
        self,
        ip: str,
        payload: tuple[int, ...],
        brightness: Optional[int] = None,
    ) -> None:
        """Set a precomputed bulb-aware sync payload directly."""

        bulb = self._get_bulb(ip)
        await bulb.turn_on(self._build_exact_sync_payload_builder(payload, brightness))
    
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

    async def set_rgb_all_exact(
        self,
        ips: list[str],
        r: int,
        g: int,
        b: int,
        brightness: Optional[int] = None,
    ) -> None:
        """Set multiple bulbs to the same exact RGB payload."""

        await asyncio.gather(*[self.set_rgb_exact(ip, r, g, b, brightness) for ip in ips])

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

    async def set_rgb_map_exact(
        self,
        colors_by_ip: dict[str, tuple[int, int, int]],
        brightness: Optional[int] = None,
    ) -> None:
        """Set per-bulb exact RGB values in a single batch."""

        await asyncio.gather(
            *[
                self.set_rgb_exact(ip, color[0], color[1], color[2], brightness)
                for ip, color in colors_by_ip.items()
            ]
        )

    async def set_screen_sync_map(
        self,
        colors_by_ip: dict[str, tuple[int, int, int]],
        brightness: Optional[int] = None,
    ) -> None:
        """Set per-bulb colors using the screen-sync mapper and tolerate dropouts."""

        await asyncio.gather(
            *[
                self.set_screen_sync_rgb(ip, color[0], color[1], color[2], brightness)
                for ip, color in colors_by_ip.items()
            ],
            return_exceptions=True,
        )
    
    async def set_color_temp_all(self, ips: list[str], kelvin: int, brightness: Optional[int] = None) -> None:
        """Set multiple bulbs to same color temperature."""
        await asyncio.gather(*[self.set_color_temp(ip, kelvin, brightness) for ip in ips])

    async def close_async(self) -> None:
        """Close bulb transports on the event loop that created them."""
        bulbs = list(self._bulbs.values())
        self._bulbs.clear()
        self._color_profiles.clear()
        self._gamut_mappers.clear()
        self._calibration_tables.clear()
        self._tone_tables.clear()

        if bulbs:
            await asyncio.gather(
                *(bulb.async_close() for bulb in bulbs),
                return_exceptions=True,
            )

    def close(self) -> None:
        """Drop cached bulb instances when async cleanup is unavailable."""
        self._bulbs.clear()
        self._color_profiles.clear()
        self._gamut_mappers.clear()
        self._calibration_tables.clear()
        self._tone_tables.clear()


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
