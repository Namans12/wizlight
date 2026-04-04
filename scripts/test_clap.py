"""Test and calibrate clap detection."""

import time
import numpy as np
import sounddevice as sd
from src.features.clap_detector import list_audio_devices, ClapDetector, ClapConfig


def test_microphone():
    """Test if microphone is working and show audio levels."""
    print("=" * 50)
    print("MICROPHONE TEST")
    print("=" * 50)
    print()
    
    # List devices
    devices = list_audio_devices()
    print("Available microphones:")
    for d in devices:
        default = " (DEFAULT)" if d["is_default"] else ""
        print(f"  [{d['index']}] {d['name']}{default}")
    
    print()
    print("Testing default microphone for 5 seconds...")
    print("Clap your hands and watch the levels!")
    print()
    print("Level:  [quiet]  ==================>  [LOUD]")
    print("-" * 50)
    
    max_peak = 0
    
    def audio_callback(indata, frames, time_info, status):
        nonlocal max_peak
        samples = np.abs(indata[:, 0])
        peak = float(np.max(samples))
        rms = float(np.sqrt(np.mean(samples ** 2)))
        
        if peak > max_peak:
            max_peak = peak
        
        # Visual meter
        bar_len = int(peak * 50)
        bar = "█" * bar_len + "░" * (50 - bar_len)
        print(f"\r{bar} Peak: {peak:.3f}", end="", flush=True)
    
    try:
        with sd.InputStream(callback=audio_callback, channels=1, samplerate=44100, blocksize=512):
            time.sleep(5)
    except Exception as e:
        print(f"\nError: {e}")
        return None
    
    print()
    print()
    print(f"Maximum peak detected: {max_peak:.3f}")
    
    if max_peak < 0.01:
        print("⚠️  Very low levels - microphone may be muted or not working")
    elif max_peak < 0.05:
        print("⚠️  Low levels - try clapping closer to microphone")
    elif max_peak > 0.3:
        print("✓ Good levels detected!")
    
    return max_peak


def test_clap_detection():
    """Test clap detection with visual feedback."""
    print()
    print("=" * 50)
    print("CLAP DETECTION TEST")
    print("=" * 50)
    print()
    print("Configuration: DOUBLE CLAP mode")
    print("Clap twice quickly to trigger (within 0.6 seconds)")
    print()
    print("Listening for 20 seconds... Press Ctrl+C to stop")
    print("-" * 50)
    
    clap_count = 0
    
    def on_clap():
        nonlocal clap_count
        clap_count += 1
        print(f"\n🎉 DOUBLE CLAP DETECTED! (#{clap_count})")
        print("-" * 50)
    
    # Use more sensitive settings
    config = ClapConfig(
        threshold=0.06,  # Lower threshold for easier detection
        rms_threshold=0.012,
        min_peak_to_rms=3.0,  # Less strict
        double_clap=True,
        double_clap_window=0.6,
        cooldown=0.5,
    )
    
    detector = ClapDetector(on_clap, config)
    detector.start()
    
    try:
        start = time.time()
        while time.time() - start < 20:
            time.sleep(0.1)
            elapsed = int(time.time() - start)
            print(f"\rListening... {20 - elapsed}s remaining | Claps detected: {clap_count}", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        detector.stop()
    
    print()
    print()
    print("=" * 50)
    print(f"Test complete! Detected {clap_count} double claps")
    print("=" * 50)
    
    if clap_count == 0:
        print()
        print("No claps detected. Try:")
        print("  1. Clap louder or closer to microphone")
        print("  2. Check microphone isn't muted in Windows sound settings")
        print("  3. Run: python scripts/test_clap.py --sensitive")


def test_sensitive():
    """Test with very sensitive settings."""
    print()
    print("=" * 50)
    print("SENSITIVE MODE TEST")
    print("=" * 50)
    print()
    
    clap_count = 0
    
    def on_clap():
        nonlocal clap_count
        clap_count += 1
        print(f"\n🎉 CLAP #{clap_count}")
    
    config = ClapConfig(
        threshold=0.03,  # Very sensitive
        rms_threshold=0.008,
        min_peak_to_rms=2.5,
        double_clap=True,
        double_clap_window=0.8,  # More time between claps
        cooldown=0.3,
    )
    
    detector = ClapDetector(on_clap, config)
    detector.start()
    
    print("SENSITIVE mode - listening for 15 seconds...")
    print("Clap twice (can be softer now)")
    
    try:
        time.sleep(15)
    except KeyboardInterrupt:
        pass
    finally:
        detector.stop()
    
    print(f"\nDetected {clap_count} double claps")


if __name__ == "__main__":
    import sys
    
    if "--sensitive" in sys.argv:
        test_sensitive()
    else:
        max_peak = test_microphone()
        if max_peak and max_peak > 0.01:
            test_clap_detection()
