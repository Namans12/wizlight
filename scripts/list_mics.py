"""Quick microphone check."""
import sounddevice as sd

print("Available microphones:")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0:
        default = " *DEFAULT*" if i == sd.default.device[0] else ""
        print(f"  [{i}] {d['name']}{default}")
