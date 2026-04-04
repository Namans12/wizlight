"""Optimized screen capture and color extraction with GPU acceleration and adaptive FPS."""

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

import numpy as np
from PIL import Image

# Try GPU capture first, fallback to mss
try:
    import dxcam
    HAS_DXCAM = True
except ImportError:
    HAS_DXCAM = False

import mss

from .screen_sync import (
    SCREEN_REGIONS,
    CaptureConfig,
    crop_content_area,
    crop_relative_region,
    enhance_color,
    smooth_color,
    color_distance,
    effective_screen_sync_mode,
)


@dataclass
class OptimizedCaptureConfig(CaptureConfig):
    """Extended configuration with optimization settings."""
    
    # GPU capture settings
    use_gpu: bool = True
    gpu_device: int = 0
    
    # Adaptive FPS settings
    adaptive_fps: bool = True
    min_fps: int = 8
    max_fps: int = 30
    motion_threshold: float = 0.015  # Frame diff threshold for motion detection
    
    # Color algorithm
    color_algorithm: str = "weighted"  # "weighted", "kmeans", "histogram"
    
    # Predictive smoothing
    predictive_smoothing: bool = True
    prediction_frames: int = 3
    prediction_weight: float = 0.3
    
    # Parallel processing
    parallel_regions: bool = True
    
    def __post_init__(self) -> None:
        super().__post_init__()
        self.min_fps = max(4, min(30, int(self.min_fps)))
        self.max_fps = max(self.min_fps, min(60, int(self.max_fps)))
        self.motion_threshold = max(0.001, min(0.1, float(self.motion_threshold)))
        self.color_algorithm = self.color_algorithm if self.color_algorithm in {"weighted", "kmeans", "histogram"} else "weighted"
        self.prediction_frames = max(2, min(10, int(self.prediction_frames)))
        self.prediction_weight = max(0.0, min(0.8, float(self.prediction_weight)))


class GPUCaptureManager:
    """Manages GPU-accelerated screen capture via DXCam."""
    
    def __init__(self, device: int = 0, monitor: int = 0):
        self._camera = None
        self._device = device
        self._monitor = monitor
        self._lock = threading.Lock()
    
    def start(self, target_fps: int = 30) -> bool:
        """Initialize GPU capture."""
        if not HAS_DXCAM:
            return False
        
        with self._lock:
            try:
                self._camera = dxcam.create(device_idx=self._device, output_idx=self._monitor)
                self._camera.start(target_fps=target_fps, video_mode=True)
                return True
            except Exception:
                self._camera = None
                return False
    
    def grab(self) -> Optional[np.ndarray]:
        """Capture a frame. Returns RGB numpy array or None."""
        if self._camera is None:
            return None
        
        try:
            frame = self._camera.get_latest_frame()
            if frame is not None:
                # DXCam returns BGR, convert to RGB
                return frame[:, :, ::-1].copy()
            return None
        except Exception:
            return None
    
    def stop(self) -> None:
        """Stop GPU capture."""
        with self._lock:
            if self._camera is not None:
                try:
                    self._camera.stop()
                except Exception:
                    pass
                self._camera = None
    
    @property
    def is_active(self) -> bool:
        return self._camera is not None


def extract_dominant_histogram(
    image: np.ndarray,
    sample_size: int = 48,
    bins: int = 16,
) -> tuple[int, int, int]:
    """Extract dominant color using histogram peaks."""
    
    img = Image.fromarray(image)
    img = img.resize((sample_size, sample_size), Image.Resampling.BILINEAR)
    pixels = np.array(img)
    
    # Build histograms for each channel
    colors = []
    for channel in range(3):
        hist, bin_edges = np.histogram(pixels[:, :, channel].ravel(), bins=bins, range=(0, 256))
        # Find peak bin
        peak_bin = hist.argmax()
        # Get center of peak bin
        color_val = int((bin_edges[peak_bin] + bin_edges[peak_bin + 1]) / 2)
        colors.append(color_val)
    
    return tuple(colors)


def extract_dominant_weighted(
    image: np.ndarray,
    sample_size: int = 48,
    edge_weight: float = 1.35,
) -> tuple[int, int, int]:
    """Extract weighted ambient color (original algorithm)."""
    
    img = Image.fromarray(image)
    img = img.resize((sample_size, sample_size), Image.Resampling.BILINEAR)
    pixels = np.array(img).astype(np.float32)
    
    normalized = pixels / 255.0
    height, width = pixels.shape[:2]
    max_channel = normalized.max(axis=2)
    min_channel = normalized.min(axis=2)
    saturation = max_channel - min_channel
    luma = (
        normalized[:, :, 0] * 0.2126
        + normalized[:, :, 1] * 0.7152
        + normalized[:, :, 2] * 0.0722
    )
    
    weights = 0.15 + saturation * 2.2 + luma * 0.5
    border = max(1, min(height, width) // 6)
    if edge_weight > 1.0:
        edge_mask = np.ones((height, width), dtype=np.float32)
        edge_mask[:border, :] *= edge_weight
        edge_mask[-border:, :] *= edge_weight
        edge_mask[:, :border] *= edge_weight
        edge_mask[:, -border:] *= edge_weight
        weights *= edge_mask
    
    avg_color = (pixels * weights[:, :, None]).sum(axis=(0, 1)) / weights.sum()
    return tuple(int(channel) for channel in avg_color[:3])


def extract_dominant_kmeans(
    image: np.ndarray,
    sample_size: int = 48,
    k: int = 3,
) -> tuple[int, int, int]:
    """Extract dominant color using k-means clustering."""
    
    img = Image.fromarray(image)
    img = img.resize((sample_size, sample_size), Image.Resampling.BILINEAR)
    pixels = np.array(img).reshape(-1, 3).astype(np.float32)
    
    np.random.seed(42)
    indices = np.random.choice(len(pixels), k, replace=False)
    centroids = pixels[indices]
    
    for _ in range(10):
        distances = np.sqrt(((pixels[:, np.newaxis] - centroids) ** 2).sum(axis=2))
        labels = distances.argmin(axis=1)
        new_centroids = np.array([
            pixels[labels == i].mean(axis=0) if (labels == i).any() else centroids[i]
            for i in range(k)
        ])
        if np.allclose(centroids, new_centroids):
            break
        centroids = new_centroids
    
    counts = np.bincount(labels, minlength=k)
    return tuple(int(channel) for channel in centroids[counts.argmax()])


class MotionDetector:
    """Detects motion between frames for adaptive FPS."""
    
    def __init__(self, threshold: float = 0.015):
        self._threshold = threshold
        self._last_frame: Optional[np.ndarray] = None
        self._motion_history: deque = deque(maxlen=5)
    
    def update(self, frame: np.ndarray) -> float:
        """Update with new frame and return motion score (0-1)."""
        # Downsample for fast comparison
        small = frame[::8, ::8].astype(np.float32)
        
        if self._last_frame is None:
            self._last_frame = small
            return 0.5  # Default to medium motion on first frame
        
        # Calculate frame difference
        diff = np.abs(small - self._last_frame).mean() / 255.0
        self._last_frame = small
        self._motion_history.append(diff)
        
        return float(np.mean(self._motion_history))
    
    def is_high_motion(self) -> bool:
        """Returns True if recent frames show high motion."""
        if not self._motion_history:
            return False
        return np.mean(self._motion_history) > self._threshold
    
    def reset(self) -> None:
        """Reset motion history."""
        self._last_frame = None
        self._motion_history.clear()


class ColorPredictor:
    """Predicts next color using linear extrapolation."""
    
    def __init__(self, history_size: int = 3, weight: float = 0.3):
        self._history: dict[str, deque] = {}
        self._history_size = history_size
        self._weight = weight
    
    def add_sample(self, key: str, color: tuple[int, int, int]) -> None:
        """Add a color sample for a key (region)."""
        if key not in self._history:
            self._history[key] = deque(maxlen=self._history_size)
        self._history[key].append(color)
    
    def predict(self, key: str, current: tuple[int, int, int]) -> tuple[int, int, int]:
        """Predict next color based on trend."""
        if key not in self._history or len(self._history[key]) < 2:
            return current
        
        history = list(self._history[key])
        if len(history) < 2:
            return current
        
        # Calculate velocity (color change per frame)
        velocities = []
        for i in range(1, len(history)):
            vel = tuple(history[i][c] - history[i-1][c] for c in range(3))
            velocities.append(vel)
        
        # Average velocity
        avg_vel = tuple(int(np.mean([v[c] for v in velocities])) for c in range(3))
        
        # Predict next color
        predicted = tuple(
            int(np.clip(current[c] + avg_vel[c] * self._weight, 0, 255))
            for c in range(3)
        )
        
        return predicted
    
    def clear(self) -> None:
        """Clear all history."""
        self._history.clear()


class OptimizedScreenSync:
    """High-performance screen color sync with all optimizations."""
    
    def __init__(
        self,
        on_color_change: Callable[[dict[str, tuple[int, int, int]]], None],
        config: Optional[OptimizedCaptureConfig] = None,
    ):
        self.config = config or OptimizedCaptureConfig()
        self.on_color_change = on_color_change
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_colors: dict[str, tuple[int, int, int]] = {}
        self._last_sent_colors: dict[str, tuple[int, int, int]] = {}
        
        # Optimization components
        self._gpu_capture: Optional[GPUCaptureManager] = None
        self._motion_detector = MotionDetector(self.config.motion_threshold)
        self._color_predictor = ColorPredictor(
            self.config.prediction_frames,
            self.config.prediction_weight,
        )
        self._executor: Optional[ThreadPoolExecutor] = None
        self._mss_instance: Optional[mss.mss] = None
        
        self._effective_mode = effective_screen_sync_mode(
            self.config.mode,
            self.config.active_regions,
        )
        self._current_fps = self.config.fps
        
        # Performance stats
        self._frame_times: deque = deque(maxlen=30)
        self._capture_method = "mss"
    
    def start(self) -> None:
        """Start optimized screen sync."""
        if self._running:
            return
        
        self._running = True
        self._motion_detector.reset()
        self._color_predictor.clear()
        
        # Initialize GPU capture if available and enabled
        if HAS_DXCAM and self.config.use_gpu:
            self._gpu_capture = GPUCaptureManager(
                device=self.config.gpu_device,
                monitor=max(0, self.config.monitor - 1),  # dxcam uses 0-indexed
            )
            if self._gpu_capture.start(target_fps=self.config.max_fps):
                self._capture_method = "dxcam"
            else:
                self._gpu_capture = None
                self._capture_method = "mss"
        
        # Initialize thread pool for parallel region processing
        if self.config.parallel_regions and self._effective_mode == "zones":
            self._executor = ThreadPoolExecutor(max_workers=min(4, len(self.config.active_regions)))
        
        # Initialize mss as fallback
        self._mss_instance = mss.mss()
        
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Stop screen sync and release resources."""
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        
        if self._gpu_capture:
            self._gpu_capture.stop()
            self._gpu_capture = None
        
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None
        
        if self._mss_instance:
            self._mss_instance.close()
            self._mss_instance = None
    
    def _capture_frame(self) -> Optional[np.ndarray]:
        """Capture a frame using best available method."""
        # Try GPU capture first
        if self._gpu_capture and self._gpu_capture.is_active:
            frame = self._gpu_capture.grab()
            if frame is not None:
                return frame
        
        # Fallback to mss
        if self._mss_instance:
            try:
                monitors = self._mss_instance.monitors
                if self.config.region:
                    monitor = {
                        "left": self.config.region[0],
                        "top": self.config.region[1],
                        "width": self.config.region[2],
                        "height": self.config.region[3],
                    }
                else:
                    if len(monitors) == 1:
                        monitor = monitors[0]
                    elif self.config.monitor == 0:
                        monitor = monitors[0]
                    else:
                        monitor = monitors[min(max(1, self.config.monitor), len(monitors) - 1)]
                
                screenshot = self._mss_instance.grab(monitor)
                image = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                return np.array(image)
            except Exception:
                pass
        
        return None
    
    def _extract_color(self, image: np.ndarray) -> tuple[int, int, int]:
        """Extract dominant color using configured algorithm."""
        if self.config.color_algorithm == "histogram":
            return extract_dominant_histogram(image, self.config.sample_size)
        elif self.config.color_algorithm == "kmeans":
            return extract_dominant_kmeans(image, self.config.sample_size)
        else:
            return extract_dominant_weighted(image, self.config.sample_size, self.config.edge_weight)
    
    def _process_region(self, content: np.ndarray, region: str) -> tuple[str, tuple[int, int, int]]:
        """Process a single region (for parallel execution)."""
        region_image = crop_relative_region(content, region)
        color = self._extract_color(region_image)
        enhanced = enhance_color(
            color,
            color_boost=self.config.color_boost,
            min_brightness=self.config.min_brightness,
        )
        return region, enhanced
    
    def _extract_colors(self, image: np.ndarray) -> dict[str, tuple[int, int, int]]:
        """Extract colors from image, using parallel processing if enabled."""
        content = crop_content_area(image, ignore_letterbox=self.config.ignore_letterbox)
        
        if self._effective_mode == "single":
            color = self._extract_color(content)
            return {
                "all": enhance_color(
                    color,
                    color_boost=self.config.color_boost,
                    min_brightness=self.config.min_brightness,
                )
            }
        
        # Zone mode - process regions
        colors: dict[str, tuple[int, int, int]] = {}
        
        if self._executor and self.config.parallel_regions:
            # Parallel processing
            futures = [
                self._executor.submit(self._process_region, content, region)
                for region in self.config.active_regions
            ]
            for future in futures:
                try:
                    region, color = future.result(timeout=0.1)
                    colors[region] = color
                except Exception:
                    pass
        else:
            # Sequential processing
            for region in self.config.active_regions:
                _, color = self._process_region(content, region)
                colors[region] = color
        
        return colors
    
    def _update_adaptive_fps(self, motion_score: float) -> None:
        """Update FPS based on motion detection."""
        if not self.config.adaptive_fps:
            self._current_fps = self.config.fps
            return
        
        # Scale FPS based on motion
        fps_range = self.config.max_fps - self.config.min_fps
        # motion_score typically 0-0.05, normalize to 0-1
        normalized_motion = min(1.0, motion_score / 0.05)
        self._current_fps = int(self.config.min_fps + fps_range * normalized_motion)
    
    def _sync_loop(self) -> None:
        """Main sync loop with all optimizations."""
        while self._running:
            frame_start = time.perf_counter()
            
            try:
                # Capture frame
                image = self._capture_frame()
                if image is None:
                    time.sleep(0.1)
                    continue
                
                # Detect motion and update adaptive FPS
                motion_score = self._motion_detector.update(image)
                self._update_adaptive_fps(motion_score)
                
                # Extract colors
                target_colors = self._extract_colors(image)
                changed_colors: dict[str, tuple[int, int, int]] = {}
                
                for key, target in target_colors.items():
                    current = self._current_colors.get(key, target)
                    
                    # Apply predictive smoothing
                    if self.config.predictive_smoothing:
                        predicted = self._color_predictor.predict(key, target)
                        # Blend predicted with target
                        target = tuple(
                            int(target[c] * 0.7 + predicted[c] * 0.3)
                            for c in range(3)
                        )
                    
                    # Smooth transition
                    smoothed = (
                        target
                        if key not in self._current_colors
                        else smooth_color(current, target, self.config.smoothing)
                    )
                    
                    self._current_colors[key] = smoothed
                    self._color_predictor.add_sample(key, smoothed)
                    
                    # Check if change is significant
                    last_sent = self._last_sent_colors.get(key)
                    if last_sent is None or color_distance(smoothed, last_sent) >= self.config.min_color_delta:
                        self._last_sent_colors[key] = smoothed
                        changed_colors[key] = smoothed
                
                # Clean up stale keys
                stale_keys = set(self._current_colors) - set(target_colors)
                for key in stale_keys:
                    self._current_colors.pop(key, None)
                    self._last_sent_colors.pop(key, None)
                
                # Send changes
                if changed_colors:
                    self.on_color_change(changed_colors)
                
            except Exception as exc:
                print(f"Screen sync error: {exc}")
            
            # Frame timing
            frame_time = time.perf_counter() - frame_start
            self._frame_times.append(frame_time)
            
            target_frame_time = 1.0 / self._current_fps
            if frame_time < target_frame_time:
                time.sleep(target_frame_time - frame_time)
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def effective_mode(self) -> str:
        return self._effective_mode
    
    @property
    def current_colors(self) -> dict[str, tuple[int, int, int]]:
        return dict(self._current_colors)
    
    @property
    def current_fps(self) -> int:
        return self._current_fps
    
    @property
    def capture_method(self) -> str:
        return self._capture_method
    
    @property
    def average_frame_time_ms(self) -> float:
        if not self._frame_times:
            return 0.0
        return float(np.mean(self._frame_times) * 1000)


def is_gpu_capture_available() -> bool:
    """Check if GPU capture is available on this system."""
    if not HAS_DXCAM:
        return False
    try:
        cam = dxcam.create()
        if cam is not None:
            del cam
            return True
    except Exception:
        pass
    return False
