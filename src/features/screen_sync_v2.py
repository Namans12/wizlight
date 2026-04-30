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
    adaptive_color_boost,
    crop_content_area,
    crop_relative_region,
    enhance_color,
    smooth_color,
    color_distance,
    perceptual_color_distance,
    effective_screen_sync_mode,
)


@dataclass
class OptimizedCaptureConfig(CaptureConfig):
    """Extended configuration with optimization settings."""
    
    smoothing: float = 0.25
    
    # GPU capture settings
    use_gpu: bool = True
    gpu_device: int = 0
    
    # Adaptive FPS settings
    adaptive_fps: bool = True
    min_fps: int = 8
    max_fps: int = 30
    motion_threshold: float = 0.015  # Frame diff threshold for motion detection
    
    # Color algorithm
    color_algorithm: str = "auto"  # "auto", "weighted", "kmeans", "histogram"
    
    # Predictive smoothing
    predictive_smoothing: bool = True
    prediction_frames: int = 3
    prediction_weight: float = 0.3
    
    # Parallel processing
    parallel_regions: bool = True
    
    def __post_init__(self) -> None:
        super().__post_init__()
        self.smoothing = max(0.05, min(0.85, float(self.smoothing)))
        self.min_fps = max(4, min(30, int(self.min_fps)))
        self.max_fps = max(self.min_fps, min(60, int(self.max_fps)))
        self.motion_threshold = max(0.001, min(0.1, float(self.motion_threshold)))
        self.color_algorithm = self.color_algorithm if self.color_algorithm in {"auto", "weighted", "kmeans", "histogram"} else "weighted"
        self.prediction_frames = max(2, min(10, int(self.prediction_frames)))
        self.prediction_weight = max(0.0, min(0.8, float(self.prediction_weight)))
        self.use_gpu = bool(self.use_gpu)
        self.adaptive_fps = bool(self.adaptive_fps)
        self.predictive_smoothing = bool(self.predictive_smoothing)
        self.parallel_regions = bool(self.parallel_regions)


def blend_colors(
    base: tuple[int, int, int],
    accent: tuple[int, int, int],
    accent_weight: float,
) -> tuple[int, int, int]:
    """Blend two RGB colors with a controllable accent weight."""

    accent_weight = max(0.0, min(1.0, float(accent_weight)))
    base_weight = 1.0 - accent_weight
    return tuple(
        int(np.clip(base[channel] * base_weight + accent[channel] * accent_weight, 0, 255))
        for channel in range(3)
    )


def _sample_pixels(
    image: np.ndarray,
    sample_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Resize an image once and return pixel arrays for shared color analysis."""

    img = Image.fromarray(image)
    img = img.resize((sample_size, sample_size), Image.Resampling.BILINEAR)
    pixels = np.array(img).astype(np.float32)
    return pixels, pixels / 255.0


def _scene_channels(normalized: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return saturation and luma maps for a normalized RGB sample."""

    saturation = normalized.max(axis=2) - normalized.min(axis=2)
    luma = (
        normalized[:, :, 0] * 0.2126
        + normalized[:, :, 1] * 0.7152
        + normalized[:, :, 2] * 0.0722
    )
    return saturation, luma


def _edge_mask(height: int, width: int, edge_weight: float) -> np.ndarray:
    """Bias sampling toward the screen perimeter for ambient lighting."""

    mask = np.ones((height, width), dtype=np.float32)
    if edge_weight <= 1.0:
        return mask

    border = max(1, min(height, width) // 6)
    mask[:border, :] *= edge_weight
    mask[-border:, :] *= edge_weight
    mask[:, :border] *= edge_weight
    mask[:, -border:] *= edge_weight
    return mask


def _ambient_weights(
    saturation: np.ndarray,
    luma: np.ndarray,
    edge_weight: float,
) -> np.ndarray:
    """Build fast ambient weights that emphasize vivid edge content."""

    weights = 0.15 + saturation * 2.2 + luma * 0.5
    weights *= _edge_mask(*saturation.shape, edge_weight)
    return weights


def _weighted_color(
    pixels: np.ndarray,
    weights: np.ndarray,
) -> tuple[int, int, int]:
    """Compute a weighted RGB average from sampled pixels."""

    total = max(float(weights.sum()), 1e-6)
    avg_color = (pixels * weights[:, :, None]).sum(axis=(0, 1)) / total
    return tuple(int(np.clip(channel, 0, 255)) for channel in avg_color[:3])


def _extract_vibrant_accent_from_sample(
    pixels: np.ndarray,
    saturation: np.ndarray,
    luma: np.ndarray,
) -> tuple[int, int, int]:
    """Extract a vivid accent directly from a shared low-resolution sample."""

    energy = saturation * 0.8 + luma * 0.2
    threshold = float(np.quantile(energy, 0.8))
    mask = energy >= threshold
    if not np.any(mask):
        return tuple(int(channel) for channel in pixels.mean(axis=(0, 1)))
    return tuple(int(channel) for channel in pixels[mask].mean(axis=0))


def _extract_palette_anchor_from_sample(
    pixels: np.ndarray,
    saturation: np.ndarray,
    luma: np.ndarray,
) -> tuple[int, int, int]:
    """Extract a tighter palette anchor from the most color-rich pixels."""

    energy = saturation * 0.9 + np.clip(0.65 - np.abs(luma - 0.42), 0.0, 0.65) * 0.1
    threshold = float(np.quantile(energy, 0.9))
    mask = (energy >= threshold) & (luma > 0.05)
    if not np.any(mask):
        return _extract_vibrant_accent_from_sample(pixels, saturation, luma)
    return tuple(int(channel) for channel in pixels[mask].mean(axis=0))


def _palette_candidates(
    pixels: np.ndarray,
    weights: np.ndarray,
    top_k: int = 6,
    bin_size: int = 32,
) -> list[tuple[tuple[int, int, int], float]]:
    """Build a small weighted palette from quantized sampled pixels."""

    flat_pixels = pixels.reshape(-1, 3)
    flat_weights = weights.reshape(-1).astype(np.float64)
    if flat_pixels.size == 0:
        return []

    levels = max(2, min(16, (255 // max(8, bin_size)) + 1))
    quantized = np.clip((flat_pixels / float(bin_size)).astype(np.int32), 0, levels - 1)
    indices = quantized[:, 0] * levels * levels + quantized[:, 1] * levels + quantized[:, 2]
    palette_size = levels ** 3

    counts = np.bincount(indices, weights=flat_weights, minlength=palette_size)
    nonzero = int(np.count_nonzero(counts))
    if nonzero == 0:
        return []

    candidate_count = min(top_k, nonzero)
    candidate_indices = np.argpartition(counts, -candidate_count)[-candidate_count:]
    candidate_indices = candidate_indices[np.argsort(counts[candidate_indices])[::-1]]

    sum_r = np.bincount(indices, weights=flat_pixels[:, 0] * flat_weights, minlength=palette_size)
    sum_g = np.bincount(indices, weights=flat_pixels[:, 1] * flat_weights, minlength=palette_size)
    sum_b = np.bincount(indices, weights=flat_pixels[:, 2] * flat_weights, minlength=palette_size)

    candidates: list[tuple[tuple[int, int, int], float]] = []
    for idx in candidate_indices:
        weight = float(counts[idx])
        if weight <= 0.0:
            continue
        color = (
            int(np.clip(sum_r[idx] / weight, 0, 255)),
            int(np.clip(sum_g[idx] / weight, 0, 255)),
            int(np.clip(sum_b[idx] / weight, 0, 255)),
        )
        candidates.append((color, weight))
    return candidates


def _select_palette_match(
    reference: tuple[int, int, int],
    candidates: Sequence[tuple[tuple[int, int, int], float]],
    prefer_vivid: bool = False,
) -> tuple[int, int, int]:
    """Choose the closest palette entry using perceptual distance and prominence."""

    if not candidates:
        return reference

    max_weight = max(weight for _, weight in candidates) or 1.0
    reference_luma = color_luma(reference)
    best_color = reference
    best_score: Optional[float] = None

    for color, weight in candidates:
        prominence = weight / max_weight
        saturation = color_saturation(color)
        luma = color_luma(color)
        score = perceptual_color_distance(color, reference)
        score -= prominence * 52.0
        score -= saturation * (78.0 if prefer_vivid else 42.0)
        score += abs(luma - reference_luma) * 24.0
        if reference_luma > 0.08 and luma < 0.02:
            score += 18.0
        if best_score is None or score < best_score:
            best_score = score
            best_color = color

    return best_color


def _palette_balance_score(
    candidates: Sequence[tuple[tuple[int, int, int], float]],
) -> float:
    """Estimate whether a frame contains several equally strong, distinct colors."""

    if len(candidates) < 3:
        return 0.0

    top = list(candidates[:3])
    weights = np.array([weight for _, weight in top], dtype=np.float32)
    if float(weights[0]) <= 0.0:
        return 0.0

    prominence = float(np.mean(weights[1:] / weights[0]))
    separation = float(
        np.mean(
            [
                perceptual_color_distance(top[i][0], top[j][0])
                for i in range(len(top))
                for j in range(i + 1, len(top))
            ]
        )
    )
    return min(1.0, prominence * min(1.0, separation / 170.0))


def extract_vibrant_accent(
    image: np.ndarray,
    sample_size: int = 48,
) -> tuple[int, int, int]:
    """Extract a vivid accent color from the most energetic pixels in the frame."""

    pixels, normalized = _sample_pixels(image, sample_size)
    saturation, luma = _scene_channels(normalized)
    return _extract_vibrant_accent_from_sample(pixels, saturation, luma)


def extract_palette_anchor(
    image: np.ndarray,
    sample_size: int = 48,
) -> tuple[int, int, int]:
    """Extract a tighter palette anchor from the most color-rich pixels."""

    pixels, normalized = _sample_pixels(image, sample_size)
    saturation, luma = _scene_channels(normalized)
    return _extract_palette_anchor_from_sample(pixels, saturation, luma)


def color_saturation(color: tuple[int, int, int]) -> float:
    """Approximate saturation for an RGB tuple."""

    values = np.array(color, dtype=np.float32) / 255.0
    return float(values.max() - values.min())


def color_luma(color: tuple[int, int, int]) -> float:
    """Approximate perceived brightness for an RGB tuple."""

    values = np.array(color, dtype=np.float32) / 255.0
    return float(values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722)


def extract_dominant_auto(
    image: np.ndarray,
    sample_size: int = 48,
    edge_weight: float = 1.35,
) -> tuple[int, int, int]:
    """Blend ambient weighting with a fast perceptual palette match."""

    analysis_size = max(16, min(sample_size, 32))
    pixels, normalized = _sample_pixels(image, analysis_size)
    saturation, luma = _scene_channels(normalized)
    weights = _ambient_weights(saturation, luma, edge_weight)

    weighted = _weighted_color(pixels, weights)
    accent = _extract_vibrant_accent_from_sample(pixels, saturation, luma)

    vivid_ratio = float(((saturation > 0.35) & (luma > 0.08)).mean())
    scene_energy = min(1.0, float(vivid_ratio * 2.2 + saturation.mean() * 1.8))

    accent_weight = 0.10 + scene_energy * 0.22
    if scene_energy < 0.35:
        accent_weight *= 0.55
    reference = blend_colors(weighted, accent, accent_weight)

    palette_weights = weights * (0.65 + saturation * 1.35)
    candidates = _palette_candidates(pixels, palette_weights, top_k=6, bin_size=32)
    balance_score = _palette_balance_score(candidates)
    weighted_saturation = color_saturation(weighted)
    if balance_score > 0.32 and weighted_saturation < 0.22:
        neutral_pull = min(0.88, balance_score * (0.55 + max(0.0, 0.22 - weighted_saturation) * 2.4))
        reference = blend_colors(reference, weighted, neutral_pull)
    matched = _select_palette_match(reference, candidates, prefer_vivid=scene_energy > 0.3)

    palette_weight = 0.12 + scene_energy * 0.28
    if scene_energy < 0.2:
        palette_weight *= 0.45
    if balance_score > 0.32 and weighted_saturation < 0.22:
        palette_weight *= max(0.15, 1.0 - balance_score * 1.25)
    return blend_colors(reference, matched, palette_weight)


def extract_cinematic_single_color(
    image: np.ndarray,
    sample_size: int = 48,
    edge_weight: float = 1.35,
) -> tuple[int, int, int]:
    """Single-bulb extractor biased toward richer cinematic ambience."""

    analysis_size = max(16, min(sample_size, 32))
    pixels, normalized = _sample_pixels(image, analysis_size)
    saturation, luma = _scene_channels(normalized)
    weights = _ambient_weights(saturation, luma, edge_weight)

    weighted = _weighted_color(pixels, weights)
    sat_mean = float(saturation.mean())
    vivid_ratio = float(((saturation > 0.32) & (luma > 0.08)).mean())
    darkness = float(luma.mean())

    if darkness < 0.03 and sat_mean < 0.04:
        return weighted

    accent = _extract_vibrant_accent_from_sample(pixels, saturation, luma)
    palette_anchor = _extract_palette_anchor_from_sample(pixels, saturation, luma)

    accent_weight = 0.18 + min(0.30, sat_mean * 0.5 + vivid_ratio * 0.22)
    if sat_mean < 0.08 and darkness > 0.18:
        accent_weight *= 0.8

    reference = blend_colors(weighted, accent, accent_weight)
    reference = blend_colors(reference, palette_anchor, 0.08 + min(0.18, vivid_ratio * 0.24))

    palette_weights = weights * (0.7 + saturation * 1.8 + np.clip(0.6 - np.abs(luma - 0.42), 0.0, 0.6) * 0.3)
    candidates = _palette_candidates(pixels, palette_weights, top_k=7, bin_size=32)
    balance_score = _palette_balance_score(candidates)
    weighted_saturation = color_saturation(weighted)
    if balance_score > 0.3 and weighted_saturation < 0.22:
        neutral_pull = min(0.9, balance_score * (0.6 + max(0.0, 0.22 - weighted_saturation) * 2.6))
        reference = blend_colors(reference, weighted, neutral_pull)
    matched = _select_palette_match(reference, candidates, prefer_vivid=True)

    palette_weight = 0.20 + min(0.34, sat_mean * 0.6 + vivid_ratio * 0.3)
    if sat_mean < 0.12:
        palette_weight *= 0.55
    if balance_score > 0.3 and weighted_saturation < 0.22:
        palette_weight *= max(0.12, 1.0 - balance_score * 1.35)

    return blend_colors(reference, matched, palette_weight)


def apply_cinematic_palette_hold(
    target: tuple[int, int, int],
    previous: Optional[tuple[int, int, int]],
    motion_score: float,
) -> tuple[int, int, int]:
    """Keep some prior hue in calm, low-saturation scenes without lagging on cuts."""

    if previous is None:
        return target

    target_sat = color_saturation(target)
    previous_sat = color_saturation(previous)
    target_luma = color_luma(target)

    if target_luma < 0.035:
        return target
    if motion_score > 0.045:
        return target
    if previous_sat < 0.12:
        return target
    if target_sat >= previous_sat * 0.92:
        return target

    hold_strength = min(0.34, max(0.0, (previous_sat - target_sat) * 0.85))
    hold_strength *= max(0.0, 1.0 - min(1.0, motion_score / 0.03))

    if target_sat < 0.12 and target_luma < 0.55:
        hold_strength = min(0.4, hold_strength + 0.08)

    if hold_strength <= 0.01:
        return target

    return blend_colors(target, previous, hold_strength)


def build_optimized_capture_config(
    settings,
    active_regions: Sequence[str],
) -> OptimizedCaptureConfig:
    """Build optimized runtime config from persisted screen-sync settings."""

    mode = getattr(settings, "mode", "single")
    color_algorithm = str(getattr(settings, "color_algorithm", "auto"))
    effective_mode = effective_screen_sync_mode(mode, active_regions)
    max_fps = int(getattr(settings, "max_fps", getattr(settings, "fps", 12)))
    min_fps = int(getattr(settings, "min_fps", max(4, max_fps // 2)))
    adaptive_fps = bool(getattr(settings, "adaptive_fps", True))
    smoothing = float(getattr(settings, "smoothing", 0.25))
    sample_size = int(getattr(settings, "sample_size", 48))
    color_boost = float(getattr(settings, "color_boost", 1.15))
    min_color_delta = int(getattr(settings, "min_color_delta", 12))

    single_cinematic = effective_mode == "single" and color_algorithm == "auto"
    if single_cinematic:
        sample_size = max(sample_size, 60)
        smoothing = min(smoothing, 0.18)
        color_boost = min(color_boost, 1.14)
        min_color_delta = min(min_color_delta, 6)
        max_fps = max(max_fps, 26)
        min_fps = max(min_fps, min(14, max_fps))

    return OptimizedCaptureConfig(
        mode=mode,
        monitor=getattr(settings, "monitor", 1),
        fps=max_fps if adaptive_fps else int(getattr(settings, "fps", max_fps)),
        smoothing=smoothing,
        sample_size=sample_size,
        region=getattr(settings, "region", None),
        ignore_letterbox=bool(getattr(settings, "ignore_letterbox", True)),
        edge_weight=float(getattr(settings, "edge_weight", 1.35)),
        color_boost=color_boost,
        min_brightness=int(getattr(settings, "min_brightness", 28)),
        min_color_delta=min_color_delta,
        active_regions=tuple(active_regions),
        use_gpu=bool(getattr(settings, "use_gpu", True)),
        adaptive_fps=adaptive_fps,
        min_fps=min_fps,
        max_fps=max_fps,
        color_algorithm=color_algorithm,
        predictive_smoothing=bool(getattr(settings, "predictive_smoothing", True)),
        parallel_regions=len(tuple(active_regions)) > 1,
    )


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
                return frame.copy()
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
        self._last_target_colors: dict[str, tuple[int, int, int]] = {}
        self._stats_lock = threading.Lock()
        self._last_send_time: Optional[float] = None
        self._send_intervals: deque = deque(maxlen=30)
        self._motion_score = 0.0
        self._smoothing_factor = self.config.smoothing
        self._prediction_strength = 0.0
        self._updates_sent = 0
        self._frames_processed = 0
        self._last_error: Optional[str] = None
        
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
        self._current_fps = self.config.max_fps if self.config.adaptive_fps else self.config.fps
        
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
        self._current_fps = self.config.max_fps if self.config.adaptive_fps else self.config.fps
        with self._stats_lock:
            self._last_target_colors = {}
            self._last_send_time = None
            self._send_intervals.clear()
            self._motion_score = 0.0
            self._smoothing_factor = self.config.smoothing
            self._prediction_strength = 0.0
            self._updates_sent = 0
            self._frames_processed = 0
            self._last_error = None
        
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
        if self.config.color_algorithm == "auto":
            return extract_dominant_auto(image, self.config.sample_size, self.config.edge_weight)
        if self.config.color_algorithm == "histogram":
            return extract_dominant_histogram(image, self.config.sample_size)
        elif self.config.color_algorithm == "kmeans":
            return extract_dominant_kmeans(image, self.config.sample_size)
        else:
            return extract_dominant_weighted(image, self.config.sample_size, self.config.edge_weight)

    def _extract_single_color(self, image: np.ndarray) -> tuple[int, int, int]:
        """Extract a richer single-bulb ambient color."""

        if self.config.color_algorithm == "auto":
            return extract_cinematic_single_color(
                image,
                self.config.sample_size,
                self.config.edge_weight,
            )
        return self._extract_color(image)
    
    def _process_region(self, content: np.ndarray, region: str) -> tuple[str, tuple[int, int, int]]:
        """Process a single region (for parallel execution)."""
        region_image = crop_relative_region(content, region)
        color = self._extract_color(region_image)
        enhanced = enhance_color(
            color,
            color_boost=adaptive_color_boost(color, self.config.color_boost),
            min_brightness=self.config.min_brightness,
        )
        return region, enhanced
    
    def _extract_colors(self, image: np.ndarray) -> dict[str, tuple[int, int, int]]:
        """Extract colors from image, using parallel processing if enabled."""
        content = crop_content_area(image, ignore_letterbox=self.config.ignore_letterbox)
        
        if self._effective_mode == "single":
            color = self._extract_single_color(content)
            return {
                "all": enhance_color(
                    color,
                    color_boost=adaptive_color_boost(color, self.config.color_boost),
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

    def _target_smoothing(self, motion_score: float) -> float:
        """Increase responsiveness for games while keeping calmer scenes stable."""

        base = self.config.smoothing
        normalized_motion = min(1.0, motion_score / max(self.config.motion_threshold * 3.0, 0.01))
        smoothing = max(0.05, min(0.85, base * (0.72 + normalized_motion * 0.9)))
        if self._effective_mode == "single":
            smoothing = max(0.06, min(0.9, smoothing * (0.95 + normalized_motion * 0.2)))
        return smoothing

    def _prediction_weight(self, motion_score: float) -> float:
        """Reduce prediction during scene cuts or very high motion."""

        if not self.config.predictive_smoothing:
            return 0.0

        normalized_motion = min(1.0, motion_score / max(self.config.motion_threshold * 4.0, 0.02))
        return self.config.prediction_weight * max(0.0, 1.0 - normalized_motion)
    
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
                smoothing_factor = self._target_smoothing(motion_score)
                prediction_weight = self._prediction_weight(motion_score)
                with self._stats_lock:
                    self._motion_score = motion_score
                    self._smoothing_factor = smoothing_factor
                    self._prediction_strength = prediction_weight
                    self._frames_processed += 1
                
                # Extract colors
                target_colors = self._extract_colors(image)
                with self._stats_lock:
                    self._last_target_colors = dict(target_colors)
                changed_colors: dict[str, tuple[int, int, int]] = {}
                
                for key, target in target_colors.items():
                    current = self._current_colors.get(key, target)

                    if self._effective_mode == "single" and key == "all":
                        previous = self._current_colors.get(key)
                        target = apply_cinematic_palette_hold(target, previous, motion_score)
                    
                    # Apply predictive smoothing
                    if prediction_weight > 0.0:
                        predicted = self._color_predictor.predict(key, target)
                        # Blend predicted with target
                        target = tuple(
                            int(target[c] * (1.0 - prediction_weight) + predicted[c] * prediction_weight)
                            for c in range(3)
                        )
                    
                    # Smooth transition
                    smoothed = (
                        target
                        if key not in self._current_colors
                        else smooth_color(current, target, smoothing_factor)
                    )
                    
                    with self._stats_lock:
                        self._current_colors[key] = smoothed
                    self._color_predictor.add_sample(key, smoothed)
                    
                    # Check if change is significant
                    last_sent = self._last_sent_colors.get(key)
                    if last_sent is None or color_distance(smoothed, last_sent) >= self.config.min_color_delta:
                        with self._stats_lock:
                            self._last_sent_colors[key] = smoothed
                        changed_colors[key] = smoothed
                
                # Clean up stale keys
                stale_keys = set(self._current_colors) - set(target_colors)
                for key in stale_keys:
                    with self._stats_lock:
                        self._current_colors.pop(key, None)
                        self._last_sent_colors.pop(key, None)
                
                # Send changes
                if changed_colors:
                    self.on_color_change(changed_colors)
                    now = time.perf_counter()
                    with self._stats_lock:
                        if self._last_send_time is not None:
                            self._send_intervals.append(now - self._last_send_time)
                        self._last_send_time = now
                        self._updates_sent += 1
                        self._last_error = None
                
            except Exception as exc:
                print(f"Screen sync error: {exc}")
                with self._stats_lock:
                    self._last_error = str(exc)
            
            # Frame timing
            frame_time = time.perf_counter() - frame_start
            with self._stats_lock:
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
    def debug_snapshot(self) -> dict[str, object]:
        """Return a thread-safe snapshot of runtime sync stats for the UI."""

        with self._stats_lock:
            frame_times = tuple(self._frame_times)
            send_intervals = tuple(self._send_intervals)
            target_colors = dict(self._last_target_colors)
            current_colors = dict(self._current_colors)
            avg_frame_time_ms = float(np.mean(frame_times) * 1000) if frame_times else 0.0
            avg_send_interval_ms = float(np.mean(send_intervals) * 1000) if send_intervals else 0.0
            send_rate_hz = float(1.0 / np.mean(send_intervals)) if send_intervals else 0.0
            return {
                "running": self._running,
                "mode": self._effective_mode,
                "capture_method": self._capture_method,
                "current_fps": self._current_fps,
                "average_frame_time_ms": avg_frame_time_ms,
                "motion_score": self._motion_score,
                "smoothing_factor": self._smoothing_factor,
                "prediction_weight": self._prediction_strength,
                "target_colors": target_colors,
                "current_colors": current_colors,
                "send_interval_ms": avg_send_interval_ms,
                "send_rate_hz": send_rate_hz,
                "updates_sent": self._updates_sent,
                "frames_processed": self._frames_processed,
                "last_error": self._last_error,
            }
    
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
