import numpy as np

from src.features.clap_detector import ClapConfig, ClapDetector
from src.features.screen_sync import (
    build_bulb_color_map,
    detect_content_bounds,
    effective_screen_sync_mode,
    enhance_color,
    extract_dominant_color,
    resolve_active_regions,
    smooth_color,
)
from src.features.screen_sync_v2 import (
    ColorPredictor,
    MotionDetector,
    OptimizedCaptureConfig,
    extract_dominant_histogram,
    extract_dominant_kmeans,
    extract_dominant_weighted,
)


def test_extract_dominant_color_returns_average_rgb():
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    image[..., 0] = 120
    image[..., 1] = 60
    image[..., 2] = 30

    assert extract_dominant_color(image, sample_size=2) == (120, 60, 30)


def test_smooth_color_moves_toward_target():
    assert smooth_color((0, 0, 0), (100, 50, 25), factor=0.5) == (50, 25, 12)


def test_enhance_color_boosts_low_brightness_colors():
    color = enhance_color((10, 6, 4), color_boost=1.2, min_brightness=28)

    assert max(color) >= 28
    assert color[0] > color[1] > color[2]


def test_detect_content_bounds_ignores_letterbox_bars():
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    image[20:100, :, 0] = 60
    image[20:100, :, 1] = 120
    image[20:100, :, 2] = 180

    left, top, width, height = detect_content_bounds(image)

    assert left == 0
    assert top == 20
    assert width == 200
    assert height == 80


def test_zone_helpers_resolve_regions_and_mode():
    layout = {
        "192.168.1.10": "left",
        "192.168.1.11": "right",
        "192.168.1.12": "unknown",
    }

    active_regions = resolve_active_regions(
        layout,
        ["192.168.1.10", "192.168.1.11", "192.168.1.12"],
    )

    assert active_regions == ("left", "right")
    assert effective_screen_sync_mode("zones", active_regions) == "zones"
    assert effective_screen_sync_mode("zones", ("left",)) == "single"


def test_build_bulb_color_map_routes_zone_colors():
    bulb_colors = build_bulb_color_map(
        bulb_ips=["192.168.1.10", "192.168.1.11"],
        colors_by_target={
            "left": (255, 0, 0),
            "right": (0, 0, 255),
        },
        mode="zones",
        bulb_layout={
            "192.168.1.10": "left",
            "192.168.1.11": "right",
        },
    )

    assert bulb_colors == {
        "192.168.1.10": (255, 0, 0),
        "192.168.1.11": (0, 0, 255),
    }


def test_double_clap_requires_two_claps(monkeypatch):
    detector = ClapDetector(lambda: None, ClapConfig(double_clap=True, double_clap_window=0.5))
    triggers: list[str] = []
    monkeypatch.setattr(detector, "_trigger_callback", lambda: triggers.append("triggered"))

    detector._handle_clap(1.0)
    detector._handle_clap(1.4)

    assert triggers == ["triggered"]


def test_single_clap_mode_respects_cooldown(monkeypatch):
    detector = ClapDetector(lambda: None, ClapConfig(double_clap=False, cooldown=1.0))
    triggers: list[str] = []
    monkeypatch.setattr(detector, "_trigger_callback", lambda: triggers.append("triggered"))

    detector._handle_clap(1.0)
    detector._handle_clap(1.5)
    detector._handle_clap(2.2)

    assert triggers == ["triggered", "triggered"]


def test_audio_callback_detects_a_short_transient_clap(monkeypatch):
    detector = ClapDetector(
        lambda: None,
        ClapConfig(
            threshold=0.05,
            rms_threshold=0.01,
            min_peak_to_rms=2.5,
            double_clap=False,
            min_duration=0.005,
            max_duration=0.1,
            sample_rate=44100,
            block_size=512,
        ),
    )
    triggers: list[str] = []
    monkeypatch.setattr(detector, "_trigger_callback", lambda: triggers.append("triggered"))
    monkeypatch.setattr("src.features.clap_detector.time.time", lambda: 1.0)

    clap_block = np.zeros((512, 1), dtype=np.float32)
    clap_block[0, 0] = 0.5
    detector._audio_callback(clap_block, 512, None, None)

    monkeypatch.setattr("src.features.clap_detector.time.time", lambda: 1.03)
    detector._audio_callback(np.zeros((512, 1), dtype=np.float32), 512, None, None)

    assert triggers == ["triggered"]


# ============== Screen Sync V2 Tests ==============

def test_extract_dominant_histogram_finds_peak_color():
    """Histogram extraction should find the most common color."""
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:, :, 0] = 200  # Red channel
    image[:, :, 1] = 100  # Green channel
    image[:, :, 2] = 50   # Blue channel
    
    r, g, b = extract_dominant_histogram(image, sample_size=16, bins=16)
    # Should be close to the actual values (within bin range)
    assert 180 <= r <= 220
    assert 80 <= g <= 120
    assert 30 <= b <= 70


def test_extract_dominant_kmeans_clusters_dominant():
    """K-means should identify dominant cluster color."""
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[:, :, 0] = 150
    image[:, :, 1] = 75
    image[:, :, 2] = 200
    
    r, g, b = extract_dominant_kmeans(image, sample_size=16, k=3)
    # Should be close to uniform color
    assert 130 <= r <= 170
    assert 55 <= g <= 95
    assert 180 <= b <= 220


def test_extract_dominant_weighted_handles_uniform_image():
    """Weighted extraction matches original algorithm behavior."""
    image = np.full((16, 16, 3), fill_value=128, dtype=np.uint8)
    
    color = extract_dominant_weighted(image, sample_size=8, edge_weight=1.0)
    # Allow for minor rounding differences
    assert all(abs(c - 128) <= 1 for c in color)


def test_motion_detector_detects_change():
    """Motion detector should report high motion for different frames."""
    detector = MotionDetector(threshold=0.01)
    
    # First frame - baseline
    frame1 = np.zeros((64, 64, 3), dtype=np.uint8)
    score1 = detector.update(frame1)
    
    # Second frame - different
    frame2 = np.full((64, 64, 3), fill_value=200, dtype=np.uint8)
    score2 = detector.update(frame2)
    
    # Score should be high for dramatic change
    assert score2 > 0.5
    assert detector.is_high_motion()


def test_motion_detector_detects_static():
    """Motion detector should report low motion for same frames."""
    detector = MotionDetector(threshold=0.01)
    
    frame = np.full((64, 64, 3), fill_value=100, dtype=np.uint8)
    
    # Feed same frame multiple times
    for _ in range(5):
        detector.update(frame)
    
    assert not detector.is_high_motion()


def test_color_predictor_extrapolates_trend():
    """Color predictor should extrapolate based on history."""
    predictor = ColorPredictor(history_size=3, weight=0.5)
    
    # Add increasing brightness samples
    predictor.add_sample("all", (100, 100, 100))
    predictor.add_sample("all", (110, 110, 110))
    predictor.add_sample("all", (120, 120, 120))
    
    # Predict should be higher than current
    predicted = predictor.predict("all", (120, 120, 120))
    assert all(p >= 120 for p in predicted)


def test_color_predictor_handles_missing_key():
    """Predictor returns current color for unknown keys."""
    predictor = ColorPredictor()
    
    current = (50, 100, 150)
    predicted = predictor.predict("unknown", current)
    
    assert predicted == current


def test_optimized_config_validates_ranges():
    """OptimizedCaptureConfig should clamp values to valid ranges."""
    config = OptimizedCaptureConfig(
        min_fps=1,  # Too low
        max_fps=100,  # Too high
        color_algorithm="invalid",
        prediction_frames=100,  # Too high
    )
    
    assert config.min_fps == 4
    assert config.max_fps == 60
    assert config.color_algorithm == "weighted"
    assert config.prediction_frames == 10
