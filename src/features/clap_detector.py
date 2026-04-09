"""Clap detection for toggle functionality."""

import time
import threading
from dataclasses import dataclass
from typing import Optional, Callable

import numpy as np
import sounddevice as sd


@dataclass
class ClapConfig:
    """Configuration for clap detection."""
    threshold: float = 0.055  # Peak amplitude threshold (0-1)
    rms_threshold: float = 0.01  # Minimum RMS energy for a clap block
    min_peak_to_rms: float = 2.7  # Clap should be a short transient, not steady noise
    adaptive_multiplier: float = 5.0  # Dynamic threshold factor over ambient noise floor
    noise_floor_decay: float = 0.985  # Smoothing for ambient-noise tracking
    min_duration: float = 0.005  # Minimum clap duration in seconds
    max_duration: float = 0.2  # Maximum clap duration in seconds
    cooldown: float = 0.45  # Minimum time between triggered actions
    double_clap: bool = True  # Require double clap to trigger
    double_clap_window: float = 0.85  # Max time between double claps
    sample_rate: int = 44100
    block_size: int = 512
    device_index: Optional[int] = None


class ClapDetector:
    """Real-time clap detection using microphone input."""
    
    def __init__(
        self,
        on_clap: Callable[[], None],
        config: Optional[ClapConfig] = None,
    ):
        """
        Args:
            on_clap: Callback when clap (or double clap) is detected
            config: Detection configuration
        """
        self.config = config or ClapConfig()
        self.on_clap = on_clap
        
        self._running = False
        self._stream: Optional[sd.InputStream] = None
        self._thread: Optional[threading.Thread] = None
        
        # State for detection
        self._last_clap_time = 0.0
        self._single_clap_time = 0.0  # For double clap detection
        self._in_clap = False
        self._clap_start_time = 0.0
        self._last_signal_time = 0.0
        self._noise_floor = max(self.config.rms_threshold / 4, 1e-4)
    
    def start(self) -> None:
        """Start clap detection."""
        if self._running:
            return
        
        self._running = True
        self._stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            blocksize=self.config.block_size,
            channels=1,
            device=self.config.device_index,
            dtype="float32",
            latency="low",
            callback=self._audio_callback,
        )
        self._stream.start()
    
    def stop(self) -> None:
        """Stop clap detection."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
    
    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """Process audio block."""
        if status:
            print(f"Audio status: {status}")

        samples = np.abs(indata[:, 0].astype(np.float32))
        rms = float(np.sqrt(np.mean(samples ** 2)))
        peak = float(np.max(samples))
        current_time = time.time()

        if not self._in_clap:
            self._noise_floor = (
                self._noise_floor * self.config.noise_floor_decay
                + rms * (1 - self.config.noise_floor_decay)
            )

        dynamic_rms_threshold = max(
            self.config.rms_threshold,
            self._noise_floor * self.config.adaptive_multiplier,
        )
        dynamic_peak_threshold = max(
            self.config.threshold,
            dynamic_rms_threshold * 2.5,
        )
        peak_to_rms = peak / max(rms, 1e-6)
        is_clap_block = (
            peak >= dynamic_peak_threshold
            and rms >= dynamic_rms_threshold * 0.4
            and peak_to_rms >= self.config.min_peak_to_rms
        )
        block_duration = frames / self.config.sample_rate

        if is_clap_block and not self._in_clap:
            self._in_clap = True
            self._clap_start_time = current_time
            self._last_signal_time = current_time
            return

        if is_clap_block and self._in_clap:
            self._last_signal_time = current_time
            return

        if self._in_clap and current_time - self._last_signal_time >= block_duration:
            clap_duration = (self._last_signal_time + block_duration) - self._clap_start_time
            self._in_clap = False
            if self.config.min_duration <= clap_duration <= self.config.max_duration:
                self._handle_clap(current_time)
    
    def _handle_clap(self, current_time: float) -> None:
        """Handle a detected clap."""
        # Check cooldown
        if current_time - self._last_clap_time < self.config.cooldown:
            return
        
        if self.config.double_clap:
            # Double clap mode
            if self._single_clap_time == 0:
                # First clap of potential double
                self._single_clap_time = current_time
            elif current_time - self._single_clap_time <= self.config.double_clap_window:
                # Second clap within window - trigger!
                self._single_clap_time = 0
                self._last_clap_time = current_time
                self._trigger_callback()
            else:
                # Too slow, start over
                self._single_clap_time = current_time
        else:
            # Single clap mode
            self._last_clap_time = current_time
            self._trigger_callback()
    
    def _trigger_callback(self) -> None:
        """Trigger the on_clap callback in a separate thread."""
        threading.Thread(target=self.on_clap, daemon=True).start()
    
    @property
    def is_running(self) -> bool:
        return self._running


def list_audio_devices() -> list[dict]:
    """List available audio input devices."""
    devices = sd.query_devices()
    input_devices = []
    default_input = None

    try:
        default_input = sd.default.device[0]
    except (TypeError, IndexError):
        default_input = None
    
    for i, device in enumerate(devices):
        if device["max_input_channels"] > 0:
            input_devices.append({
                "index": i,
                "name": device["name"],
                "channels": device["max_input_channels"],
                "sample_rate": device["default_samplerate"],
                "is_default": i == default_input,
            })
    
    return input_devices


def set_audio_device(device_index: int) -> None:
    """Set the default audio input device."""
    sd.default.device = (device_index, None)
