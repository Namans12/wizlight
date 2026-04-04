"""Debug microphone levels in real-time."""
import time
import numpy as np
import sounddevice as sd

print("=" * 50)
print("MICROPHONE DEBUG - Watch the levels")
print("=" * 50)
print()
print("Clap your hands and watch if levels spike!")
print("Press Ctrl+C to stop")
print()

max_peak = 0

def callback(indata, frames, time_info, status):
    global max_peak
    peak = float(np.max(np.abs(indata)))
    if peak > max_peak:
        max_peak = peak
    
    # Simple visual
    bars = int(peak * 100)
    meter = "#" * min(bars, 50)
    print(f"Level: {peak:.4f} |{meter}")

try:
    with sd.InputStream(callback=callback, channels=1, samplerate=44100, blocksize=1024):
        print("Listening... (5 seconds)")
        time.sleep(5)
except Exception as e:
    print(f"ERROR: {e}")
    print()
    print("Possible fixes:")
    print("1. Check Windows Settings > Privacy > Microphone > Allow apps")
    print("2. Right-click speaker icon > Sound settings > Input device")

print()
print(f"Max peak recorded: {max_peak:.4f}")
if max_peak < 0.001:
    print("⚠️ NO AUDIO DETECTED - Microphone may be muted or blocked")
elif max_peak < 0.03:
    print("⚠️ Very quiet - try clapping louder or check mic volume")
else:
    print("✓ Audio detected")
