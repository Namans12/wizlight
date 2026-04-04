#!/usr/bin/env python3
"""Quick script to run clap detection toggle."""

import argparse
import signal
import sys
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.async_runtime import BackgroundAsyncLoop
from src.core.bulb_controller import BulbController
from src.core.config import Config
from src.features.clap_detector import ClapDetector, ClapConfig, list_audio_devices


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Toggle Wiz bulbs with clap detection.")
    parser.add_argument("--device", type=int, help="Audio input device index to use")
    parser.add_argument("--threshold", type=float, default=0.08, help="Peak clap threshold")
    parser.add_argument(
        "--single",
        action="store_true",
        help="Trigger on a single clap instead of a double clap",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = Config.load()
    controller = BulbController()
    
    if not config.bulbs:
        print("No bulbs configured. Run 'wizlight discover' first.")
        return
    
    ips = [b.ip for b in config.bulbs]
    
    # Show available audio devices
    print("Available audio devices:")
    for device in list_audio_devices():
        suffix = " (default)" if device.get("is_default") else ""
        print(f"  [{device['index']}] {device['name']}{suffix}")
    print()
    
    print(f"Starting clap detection with {len(ips)} bulb(s)...")
    print("Double clap to toggle lights")
    print("Press Ctrl+C to stop\n")
    runner = BackgroundAsyncLoop()
    
    clap_count = [0]
    
    def on_clap():
        clap_count[0] += 1
        print(f"[{clap_count[0]}] Clap detected! Toggling lights...")
        runner.submit(controller.toggle_all(ips))
    
    clap_detector = ClapDetector(
        on_clap=on_clap,
        config=ClapConfig(
            threshold=args.threshold,
            rms_threshold=0.015,
            double_clap=not args.single,
            double_clap_window=0.6,
            cooldown=1.0,
            device_index=args.device,
        )
    )
    
    # Handle Ctrl+C
    running = True
    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nStopping clap detection...")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    clap_detector.start()
    
    # Keep running
    try:
        while running and clap_detector.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        clap_detector.stop()
        try:
            runner.run(controller.close_async(), timeout=2.0)
        finally:
            runner.shutdown()
        print("Done.")


if __name__ == "__main__":
    main()
