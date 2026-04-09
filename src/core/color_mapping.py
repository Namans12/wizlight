"""Bulb-aware color mapping for fast, accurate screen sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .calibration import BulbCalibrationTable


_GRID_SIZE = 33
_GRID_MAX_INDEX = _GRID_SIZE - 1
_EPSILON = 1e-6


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, 0.0, 1.0)
    return np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )


def _linear_to_srgb(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, 0.0, 1.0)
    return np.where(
        values <= 0.0031308,
        values * 12.92,
        1.055 * (values ** (1.0 / 2.4)) - 0.055,
    )


def _smoothstep01(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


@dataclass(frozen=True)
class BulbColorProfile:
    """Hardware hints used to map screen colors onto a WiZ bulb."""

    model_name: Optional[str] = None
    mac: Optional[str] = None
    white_channels: int = 1
    white_to_color_ratio: int = 20
    rgb_channel_current: tuple[int, int, int] = (9, 8, 6)
    render_factor: tuple[int, ...] = field(default_factory=tuple)
    fade_in_ms: int = 0
    fade_out_ms: int = 0

    @property
    def white_ratio(self) -> float:
        return max(0.0, min(1.0, float(self.white_to_color_ratio) / 100.0))


class BulbGamutMapper:
    """Fast lookup mapper from target RGB to a bulb-aware RGBW/RGBWW payload."""

    def __init__(
        self,
        profile: Optional[BulbColorProfile] = None,
        calibration: Optional[BulbCalibrationTable] = None,
        tone_lut: Optional[BulbCalibrationTable] = None,
    ):
        self.profile = profile or BulbColorProfile()
        self.calibration = calibration
        self.tone_lut = tone_lut
        self._rgb_gain = self._derive_rgb_gain()
        self._single_white = self.profile.white_channels <= 1
        self._lookup = self._build_lookup()

    def _derive_rgb_gain(self) -> np.ndarray:
        current = np.array(self.profile.rgb_channel_current, dtype=np.float32)
        if current.size < 3:
            return np.ones(3, dtype=np.float32)
        current = np.clip(current[:3], 1.0, None)
        gain = (current.max() / current) ** 0.45
        return np.clip(gain, 0.85, 1.35)

    def _build_lookup(self) -> np.ndarray:
        grid = np.linspace(0.0, 1.0, _GRID_SIZE, dtype=np.float32)
        red, green, blue = np.meshgrid(grid, grid, grid, indexing="ij")
        rgb = np.stack((red, green, blue), axis=-1)
        if self.tone_lut is not None:
            rgb = self.tone_lut.correct_rgb_batch(rgb)
        if self.calibration is not None:
            rgb = self.calibration.correct_rgb_batch(rgb)

        linear = _srgb_to_linear(rgb)
        balanced = np.clip(linear * self._rgb_gain.reshape((1, 1, 1, 3)), 0.0, None)
        peak = np.maximum(balanced.max(axis=-1, keepdims=True), 1.0)
        balanced = balanced / peak

        max_channel = balanced.max(axis=-1)
        min_channel = balanced.min(axis=-1)
        saturation = np.where(
            max_channel > _EPSILON,
            (max_channel - min_channel) / np.maximum(max_channel, _EPSILON),
            0.0,
        )
        luma = (
            balanced[..., 0] * 0.2126
            + balanced[..., 1] * 0.7152
            + balanced[..., 2] * 0.0722
        )

        white_ratio = self.profile.white_ratio
        neutral_threshold = 0.18 + white_ratio * 0.22
        white_gate = _smoothstep01(
            np.clip((neutral_threshold - saturation) / max(neutral_threshold, 0.05), 0.0, 1.0)
        )
        brightness_gate = _smoothstep01(np.clip((luma - 0.03) / 0.32, 0.0, 1.0))
        white_strength = min(1.0, 0.68 + white_ratio * 0.42)

        white_linear = min_channel * white_gate * brightness_gate * white_strength
        colored_dark_mask = (luma < 0.08) & (saturation > 0.14)
        white_linear = np.where(colored_dark_mask, white_linear * 0.15, white_linear)

        neutral_mask = saturation < 0.035
        white_linear = np.where(
            neutral_mask,
            np.maximum(white_linear, max_channel * min(1.0, 0.84 + white_ratio * 0.3)),
            white_linear,
        )

        rgb_linear = np.clip(balanced - white_linear[..., None], 0.0, None)
        if np.any(neutral_mask):
            rgb_linear[neutral_mask] *= 0.08

        rgb_encoded = np.clip(np.round(_linear_to_srgb(rgb_linear) * 255.0), 0, 255).astype(np.uint8)
        white_encoded = np.clip(
            np.round(_linear_to_srgb(np.clip(white_linear * (1.0 + white_ratio * 0.25), 0.0, 1.0)) * 255.0),
            0,
            255,
        ).astype(np.uint8)

        if self._single_white:
            return np.concatenate((rgb_encoded, white_encoded[..., None]), axis=-1)

        cool_mix = np.clip((balanced[..., 2] - balanced[..., 0] + 1.0) * 0.5, 0.0, 1.0)
        cool_mix *= _smoothstep01(np.clip((0.28 - saturation) / 0.28, 0.0, 1.0))
        cold_white = np.clip(np.round(white_encoded * cool_mix), 0, 255).astype(np.uint8)
        warm_white = np.clip(white_encoded - cold_white, 0, 255).astype(np.uint8)
        return np.concatenate(
            (rgb_encoded, cold_white[..., None], warm_white[..., None]),
            axis=-1,
        )

    def map_rgb(self, color: tuple[int, int, int]) -> tuple[int, ...]:
        red, green, blue = (max(0, min(255, int(channel))) for channel in color)
        ri = min(_GRID_MAX_INDEX, int(round(red / 255.0 * _GRID_MAX_INDEX)))
        gi = min(_GRID_MAX_INDEX, int(round(green / 255.0 * _GRID_MAX_INDEX)))
        bi = min(_GRID_MAX_INDEX, int(round(blue / 255.0 * _GRID_MAX_INDEX)))
        payload = self._lookup[ri, gi, bi]
        return tuple(int(value) for value in payload.tolist())
