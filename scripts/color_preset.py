#!/usr/bin/env python3
"""Quick script to apply color presets."""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.async_runtime import run_sync


def main():
    from src.core.bulb_controller import BulbController, apply_preset, PRESETS
    from src.core.config import Config
    
    if len(sys.argv) < 2:
        print("Usage: python color_preset.py <preset_name>")
        print("\nAvailable presets:")
        for name, settings in PRESETS.items():
            print(f"  {name}: {settings}")
        return
    
    preset_name = sys.argv[1].lower()
    
    if preset_name not in PRESETS:
        print(f"Unknown preset: {preset_name}")
        print(f"Available: {', '.join(PRESETS.keys())}")
        return
    
    config = Config.load()
    controller = BulbController()
    
    if not config.bulbs:
        print("No bulbs configured. Run 'wizlight discover' first.")
        return
    
    ips = [b.ip for b in config.bulbs]
    
    async def apply_and_cleanup():
        try:
            await apply_preset(controller, ips, preset_name)
            print(f"Applied preset '{preset_name}' to {len(ips)} bulb(s)")
        finally:
            await controller.close_async()

    run_sync(apply_and_cleanup())


if __name__ == "__main__":
    main()
