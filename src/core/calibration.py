"""Measured per-bulb calibration tables and persistence helpers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import numpy as np


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


def sanitize_calibration_key(value: str) -> str:
    """Normalize a bulb identity into a safe calibration filename stem."""

    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return normalized or "unknown"


@dataclass(frozen=True)
class CalibrationSample:
    """One measured calibration point for a bulb."""

    target_rgb: tuple[int, int, int]
    measured_rgb: tuple[int, int, int]
    payload: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_rgb", tuple(int(value) for value in self.target_rgb))
        object.__setattr__(self, "measured_rgb", tuple(int(value) for value in self.measured_rgb))
        object.__setattr__(self, "payload", tuple(int(value) for value in self.payload))


@dataclass
class BulbCalibrationTable:
    """Measured inverse-correction table for one bulb."""

    key: str
    bulb_mac: Optional[str] = None
    bulb_model: Optional[str] = None
    bulb_ip: Optional[str] = None
    source: str = "camera"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    roi: Optional[tuple[int, int, int, int]] = None
    camera_index: Optional[int] = None
    notes: str = ""
    strength: float = 0.45
    samples: list[CalibrationSample] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalized_samples: list[CalibrationSample] = []
        for sample in self.samples:
            if isinstance(sample, CalibrationSample):
                normalized_samples.append(sample)
            else:
                normalized_samples.append(CalibrationSample(**sample))
        self.samples = normalized_samples

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["samples"] = [asdict(sample) for sample in self.samples]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "BulbCalibrationTable":
        return cls(
            key=data["key"],
            bulb_mac=data.get("bulb_mac"),
            bulb_model=data.get("bulb_model"),
            bulb_ip=data.get("bulb_ip"),
            source=data.get("source", "camera"),
            created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            roi=tuple(data["roi"]) if data.get("roi") else None,
            camera_index=data.get("camera_index"),
            notes=data.get("notes", ""),
            strength=float(data.get("strength", 0.45)),
            samples=[CalibrationSample(**sample) for sample in data.get("samples", [])],
        )

    def _sample_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        inputs = np.array([sample.target_rgb for sample in self.samples], dtype=np.float32) / 255.0
        outputs = np.array([sample.measured_rgb for sample in self.samples], dtype=np.float32) / 255.0

        black_reference = np.zeros(3, dtype=np.float32)
        white_reference = np.ones(3, dtype=np.float32)

        for sample, measured in zip(self.samples, outputs):
            if tuple(sample.target_rgb) == (0, 0, 0):
                black_reference = measured.astype(np.float32)
            elif tuple(sample.target_rgb) == (255, 255, 255):
                white_reference = measured.astype(np.float32)

        headroom = np.maximum(white_reference - black_reference, 1e-3)
        outputs = np.clip((outputs - black_reference) / headroom, 0.0, 1.0)

        return _srgb_to_linear(inputs), _srgb_to_linear(outputs)

    def correct_rgb_batch(self, normalized_rgb: np.ndarray) -> np.ndarray:
        """Map desired sRGB colors onto corrected sRGB inputs using measured samples."""

        if self.sample_count < 4:
            return np.clip(normalized_rgb, 0.0, 1.0)

        input_linear, measured_linear = self._sample_arrays()
        targets = np.clip(np.asarray(normalized_rgb, dtype=np.float32), 0.0, 1.0)
        flat_targets = targets.reshape(-1, 3)
        target_linear = _srgb_to_linear(flat_targets)
        target_luma = (
            flat_targets[:, 0] * 0.2126
            + flat_targets[:, 1] * 0.7152
            + flat_targets[:, 2] * 0.0722
        )
        target_peak = flat_targets.max(axis=1)

        corrected_linear = np.empty_like(target_linear)
        k = min(6, self.sample_count)
        chunk_size = 2048

        for start in range(0, len(target_linear), chunk_size):
            end = min(len(target_linear), start + chunk_size)
            chunk = target_linear[start:end]
            delta = chunk[:, None, :] - measured_linear[None, :, :]
            distance = np.sqrt(
                delta[:, :, 0] * delta[:, :, 0] * 2.0
                + delta[:, :, 1] * delta[:, :, 1] * 4.0
                + delta[:, :, 2] * delta[:, :, 2] * 3.0
            )

            nearest_indices = np.argpartition(distance, kth=k - 1, axis=1)[:, :k]
            nearest_distances = np.take_along_axis(distance, nearest_indices, axis=1)
            nearest_inputs = input_linear[nearest_indices]

            weights = 1.0 / np.maximum(nearest_distances, 1e-4) ** 2
            weight_sum = np.maximum(weights.sum(axis=1, keepdims=True), 1e-6)
            estimated = (nearest_inputs * weights[:, :, None]).sum(axis=1) / weight_sum

            nearest_distance = nearest_distances.min(axis=1)
            confidence = np.exp(-nearest_distance * 8.0).reshape(-1, 1)
            darkness = np.clip((target_peak[start:end] - 0.05) / 0.18, 0.0, 1.0).reshape(-1, 1)
            luma_factor = np.clip(target_luma[start:end] / 0.08, 0.0, 1.0).reshape(-1, 1)
            confidence *= np.maximum(darkness, luma_factor)
            corrected_linear[start:end] = chunk * (1.0 - confidence) + estimated * confidence

        corrected = _linear_to_srgb(np.clip(corrected_linear, 0.0, 1.0))
        calibration_strength = max(0.0, min(1.0, float(self.strength)))
        corrected = flat_targets * (1.0 - calibration_strength) + corrected * calibration_strength
        dark_mask = (target_peak <= 0.05) | (target_luma <= 0.02)
        corrected[dark_mask] = flat_targets[dark_mask]
        return corrected.reshape(targets.shape)

    def correct_rgb(self, color: tuple[int, int, int]) -> tuple[int, int, int]:
        """Correct one desired RGB sample using the measured inverse table."""

        normalized = np.array(color, dtype=np.float32).reshape(1, 3) / 255.0
        corrected = self.correct_rgb_batch(normalized)[0]
        return tuple(int(np.clip(round(channel * 255.0), 0, 255)) for channel in corrected)


class CalibrationStore:
    """Persist calibration tables under the user's WiZ config directory."""

    def __init__(self, base_dir: Optional[Path] = None, suffix: str = ".json"):
        self.base_dir = base_dir or (Path.home() / ".wizlight" / "calibration")
        self.suffix = suffix

    def calibration_path(self, key: str) -> Path:
        return self.base_dir / f"{sanitize_calibration_key(key)}{self.suffix}"

    def save(self, table: BulbCalibrationTable) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.calibration_path(table.key)
        path.write_text(json.dumps(table.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, key: Optional[str]) -> Optional[BulbCalibrationTable]:
        if not key:
            return None
        path = self.calibration_path(key)
        if not path.exists():
            return None
        return BulbCalibrationTable.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def load_any(self, keys: Sequence[Optional[str]]) -> Optional[BulbCalibrationTable]:
        for key in keys:
            table = self.load(key)
            if table is not None:
                return table
        return None


class ToneCalibrationStore(CalibrationStore):
    """Persist sparse scene-tone correction tables separately from bulb calibration."""

    def __init__(self, base_dir: Optional[Path] = None):
        super().__init__(base_dir=base_dir, suffix="-tone.json")
