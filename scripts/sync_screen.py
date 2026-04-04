#!/usr/bin/env python3
"""Quick script to run screen sync."""

import signal
import sys
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.async_runtime import BackgroundAsyncLoop
from src.core.bulb_controller import BulbController
from src.core.config import Config
from src.features.screen_sync import (
    CaptureConfig,
    ScreenSync,
    build_bulb_color_map,
    effective_screen_sync_mode,
    resolve_active_regions,
)


def main():
    config = Config.load()
    controller = BulbController()
    
    if not config.bulbs:
        print("No bulbs configured. Run 'wizlight discover' first.")
        return
    
    ips = [b.ip for b in config.bulbs]
    screen_settings = config.screen_sync
    active_regions = resolve_active_regions(screen_settings.bulb_layout, ips)
    mode = effective_screen_sync_mode(screen_settings.mode, active_regions)

    print(f"Starting screen sync with {len(ips)} bulb(s)...")
    print(f"Mode: {mode}")
    print("Press Ctrl+C to stop\n")
    runner = BackgroundAsyncLoop()

    def on_color_change(colors_by_target):
        bulb_colors = build_bulb_color_map(
            ips,
            colors_by_target,
            screen_settings.mode,
            screen_settings.bulb_layout,
        )
        if not bulb_colors:
            return

        summary = ", ".join(
            f"{target}=RGB{color}" for target, color in sorted(colors_by_target.items())
        )
        print(f"\rColors: {summary}", end="", flush=True)
        runner.submit(controller.set_rgb_map(bulb_colors))

    screen_sync = ScreenSync(
        on_color_change=on_color_change,
        config=CaptureConfig(
            mode=screen_settings.mode,
            monitor=screen_settings.monitor,
            fps=screen_settings.fps,
            sample_size=screen_settings.sample_size,
            ignore_letterbox=screen_settings.ignore_letterbox,
            edge_weight=screen_settings.edge_weight,
            color_boost=screen_settings.color_boost,
            min_brightness=screen_settings.min_brightness,
            min_color_delta=screen_settings.min_color_delta,
            active_regions=active_regions,
        ),
        smoothing=screen_settings.smoothing,
    )
    
    # Handle Ctrl+C
    running = True
    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nStopping screen sync...")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    screen_sync.start()
    
    # Keep running with the event loop
    try:
        while running and screen_sync.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        screen_sync.stop()
        try:
            runner.run(controller.close_async(), timeout=2.0)
        finally:
            runner.shutdown()
        print("Done.")


if __name__ == "__main__":
    main()
