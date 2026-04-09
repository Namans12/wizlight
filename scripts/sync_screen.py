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
from src.features.screen_sync import build_bulb_color_map, effective_screen_sync_mode, resolve_active_regions
from src.features.screen_sync_v2 import OptimizedScreenSync, build_optimized_capture_config


def main():
    config = Config.load()
    controller = BulbController()
    
    if not config.bulbs:
        print("No bulbs configured. Run 'wizlight discover' first.")
        return
    
    configured_ips = [b.ip for b in config.bulbs]
    runner = BackgroundAsyncLoop()
    ips = runner.run(controller.refresh_screen_sync_targets(configured_ips), timeout=6.0)
    if not ips:
        try:
            runner.run(controller.close_async(), timeout=2.0)
        finally:
            runner.shutdown()
        print("No reachable bulbs available for screen sync.")
        return
    screen_settings = config.screen_sync
    active_regions = resolve_active_regions(screen_settings.bulb_layout, ips)
    mode = effective_screen_sync_mode(screen_settings.mode, active_regions)

    print(f"Starting screen sync with {len(ips)} bulb(s)...")
    skipped = len(configured_ips) - len(ips)
    if skipped:
        print(f"Skipping {skipped} stale or unreachable bulb(s) for sync.")
    print(f"Mode: {mode}")
    print("Press Ctrl+C to stop\n")

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
        runner.submit(controller.set_screen_sync_map(bulb_colors))

    screen_sync = OptimizedScreenSync(
        on_color_change=on_color_change,
        config=build_optimized_capture_config(screen_settings, active_regions),
    )
    
    # Handle Ctrl+C
    running = True
    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nStopping screen sync...")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    screen_sync.start()
    profile = "cinematic single" if mode == "single" and screen_settings.color_algorithm == "auto" else screen_settings.color_algorithm.upper()
    print(
        f"Capture: {screen_sync.capture_method.upper()} | "
        f"Algorithm: {profile} | "
        f"Adaptive FPS: {'ON' if screen_settings.adaptive_fps else 'OFF'}"
    )
    mapping = controller.summarize_screen_sync_mapping(ips)
    if mapping:
        print(f"Mapping: {mapping}")
    
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
