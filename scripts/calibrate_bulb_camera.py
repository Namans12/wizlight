#!/usr/bin/env python3
"""Capture a measured per-bulb calibration table using a local camera."""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.async_runtime import BackgroundAsyncLoop, configure_event_loop_policy
from src.core.bulb_controller import BulbController, BulbState
from src.core.calibration import (
    BulbCalibrationTable,
    CalibrationSample,
    CalibrationStore,
)
from src.core.color_mapping import BulbGamutMapper
from src.core.config import Config


DEFAULT_LEVELS = (0, 96, 176, 255)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure a per-bulb calibration table using a camera ROI."
    )
    parser.add_argument("--ip", help="Bulb IP. Defaults to the first reachable configured bulb.")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index.")
    parser.add_argument(
        "--levels",
        default="0,96,176,255",
        help="Comma-separated RGB levels for the calibration lattice.",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.0,
        help="Extra settle time in seconds, added on top of bulb fade time.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=8,
        help="Frames to average per calibration point after warmup.",
    )
    parser.add_argument(
        "--warmup-frames",
        type=int,
        default=6,
        help="Frames to discard after each color change before averaging.",
    )
    parser.add_argument(
        "--roi",
        help="Manual ROI as x,y,width,height. If omitted, an ROI picker window opens.",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Optional notes stored with the calibration table.",
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=8.0,
        help="Timeout in seconds for each bulb command.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries per bulb command before giving up.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=0.4,
        help="Delay in seconds between bulb command retries.",
    )
    parser.add_argument(
        "--camera-stabilize-seconds",
        type=float,
        default=4.0,
        help="Extra time to let the live camera feed settle before the first bulb update.",
    )
    return parser.parse_args()


def parse_levels(raw: str) -> tuple[int, ...]:
    values = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.append(max(0, min(255, int(chunk))))
    deduped = tuple(dict.fromkeys(values))
    return deduped or DEFAULT_LEVELS


def parse_roi(raw: Optional[str]) -> Optional[tuple[int, int, int, int]]:
    if not raw:
        return None
    parts = [int(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("ROI must be x,y,width,height")
    x, y, width, height = parts
    if width <= 0 or height <= 0:
        raise ValueError("ROI width and height must be positive")
    return (x, y, width, height)


def open_camera(camera_index: int) -> cv2.VideoCapture:
    camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError(f"Could not open camera index {camera_index}")
    return camera


def read_frame(camera: cv2.VideoCapture) -> np.ndarray:
    ok, frame = camera.read()
    if not ok or frame is None:
        raise RuntimeError("Camera did not return a frame")
    return frame


def frame_has_signal(
    frame: np.ndarray,
    roi: Optional[tuple[int, int, int, int]] = None,
) -> bool:
    """Return True when a captured frame contains real image signal."""

    mean_threshold = 2.0
    nonzero_threshold = 0.004

    region = frame
    if roi is not None:
        x, y, width, height = roi
        crop = frame[y : y + height, x : x + width]
        if crop.size:
            region = crop

    region = region.astype(np.float32)
    mean_value = float(region.mean())
    nonzero_ratio = float((region > 8.0).any(axis=2).mean())
    return mean_value >= mean_threshold or nonzero_ratio >= nonzero_threshold


def camera_feed_has_signal(
    camera: cv2.VideoCapture,
    roi: Optional[tuple[int, int, int, int]] = None,
    frame_count: int = 12,
) -> bool:
    """Return True when the camera feed contains real image signal, not a black placeholder."""

    for _ in range(max(1, frame_count)):
        frame = read_frame(camera)
        if frame_has_signal(frame, roi=roi):
            return True

    return False


def assert_camera_feed_live(
    camera: cv2.VideoCapture,
    roi: Optional[tuple[int, int, int, int]] = None,
    frame_count: int = 12,
    max_wait_seconds: float = 0.0,
    poll_interval: float = 2.0,
) -> None:
    """Raise when the camera is still black/loading instead of showing a live image."""

    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    while True:
        if camera_feed_has_signal(camera, roi=roi, frame_count=frame_count):
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.1, poll_interval))

    raise RuntimeError(
        "Camera feed is not live. The selected camera is still returning black/loading frames."
    )


def stabilize_camera_feed(
    camera: cv2.VideoCapture,
    duration_seconds: float = 3.0,
    roi: Optional[tuple[int, int, int, int]] = None,
) -> None:
    """Drain frames briefly so the live feed and auto-exposure can settle."""

    end_time = time.monotonic() + max(0.0, duration_seconds)
    checks = 0
    live_checks = 0
    while time.monotonic() < end_time:
        frame = read_frame(camera)
        if frame_has_signal(frame):
            live_checks += 1
        checks += 1
        time.sleep(0.05)

    if checks == 0:
        raise RuntimeError("Camera feed did not produce any frames while stabilizing.")

    live_ratio = live_checks / checks
    if live_ratio < 0.25:
        raise RuntimeError("Camera feed dropped out too often while stabilizing.")


def select_roi(camera: cv2.VideoCapture) -> tuple[int, int, int, int]:
    frame = read_frame(camera)
    window = "Select bulb region"
    roi = cv2.selectROI(window, frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(window)
    x, y, width, height = (int(value) for value in roi)
    if width <= 0 or height <= 0:
        raise RuntimeError("ROI selection cancelled")
    return (x, y, width, height)


def average_roi_color(frame: np.ndarray, roi: tuple[int, int, int, int]) -> tuple[int, int, int]:
    x, y, width, height = roi
    crop = frame[y : y + height, x : x + width]
    if crop.size == 0:
        raise RuntimeError("Selected ROI is empty")

    # OpenCV frames are BGR; convert to RGB for calibration storage.
    crop_rgb = crop[:, :, ::-1].astype(np.float32)
    return tuple(int(np.clip(round(channel), 0, 255)) for channel in crop_rgb.mean(axis=(0, 1)))


def average_frame(
    camera: cv2.VideoCapture,
    frame_count: int,
) -> np.ndarray:
    frames = [read_frame(camera).astype(np.float32) for _ in range(max(1, frame_count))]
    return np.mean(frames, axis=0)


def capture_measurement(
    camera: cv2.VideoCapture,
    roi: tuple[int, int, int, int],
    warmup_frames: int,
    capture_frames: int,
) -> tuple[int, int, int]:
    for _ in range(max(0, warmup_frames)):
        read_frame(camera)

    samples = []
    live_samples = []
    max_samples = max(1, capture_frames)
    attempts = 0
    max_attempts = max_samples * 6
    while len(samples) < max_samples and attempts < max_attempts:
        frame = read_frame(camera)
        attempts += 1
        color = average_roi_color(frame, roi)
        samples.append(color)
        if frame_has_signal(frame):
            live_samples.append(color)
        else:
            time.sleep(0.02)

    if not samples:
        raise RuntimeError("Camera feed dropped out during measurement.")

    usable_samples = live_samples or samples
    stacked = np.array(usable_samples, dtype=np.float32)
    return tuple(int(np.clip(round(channel), 0, 255)) for channel in np.median(stacked, axis=0))


def detect_roi_from_flash(
    camera: cv2.VideoCapture,
    controller: BulbController,
    runner: BackgroundAsyncLoop,
    ip: str,
    settle_time: float,
    command_timeout: float,
    retries: int,
    retry_delay: float,
) -> Optional[tuple[int, int, int, int]]:
    """Try to locate the bulb automatically by flashing it and measuring frame differences."""

    run_bulb_command(
        runner,
        lambda: controller.turn_off(ip),
        timeout=command_timeout,
        retries=retries,
        retry_delay=retry_delay,
    )
    time.sleep(settle_time)
    off_frame = average_frame(camera, 8)

    run_bulb_command(
        runner,
        lambda: controller.set_screen_sync_rgb(ip, 255, 255, 255),
        timeout=command_timeout,
        retries=retries,
        retry_delay=retry_delay,
    )
    time.sleep(settle_time)
    on_frame = average_frame(camera, 8)

    diff = np.abs(on_frame - off_frame).mean(axis=2).astype(np.uint8)
    threshold_value = max(18, int(np.quantile(diff, 0.995)))
    _, mask = cv2.threshold(diff, threshold_value, 255, cv2.THRESH_BINARY)
    mask = cv2.GaussianBlur(mask, (9, 9), 0)
    _, mask = cv2.threshold(mask, 24, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area < 120:
        return None

    x, y, width, height = cv2.boundingRect(contour)
    padding = 16
    frame_height, frame_width = diff.shape[:2]
    x = max(0, x - padding)
    y = max(0, y - padding)
    width = min(frame_width - x, width + padding * 2)
    height = min(frame_height - y, height + padding * 2)
    return (x, y, width, height)


def calibration_colors(levels: Iterable[int]) -> list[tuple[int, int, int]]:
    colors = list(itertools.product(levels, repeat=3))
    colors.sort(key=lambda color: (sum(color), color[0], color[1], color[2]))
    return colors


def run_bulb_command(
    runner: BackgroundAsyncLoop,
    coro_factory: Callable[[], Any],
    *,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            runner.run(coro_factory(), timeout=timeout)
            return
        except FutureTimeoutError as exc:
            last_error = exc
            if attempt >= max(1, retries):
                break
            time.sleep(retry_delay)
        except Exception:
            raise

    raise RuntimeError(
        f"Bulb command timed out after {max(1, retries)} attempts "
        f"(timeout={timeout:.1f}s)"
    ) from last_error


def save_partial_calibration(
    store: CalibrationStore,
    profile_key: str,
    profile_mac: Optional[str],
    profile_model: Optional[str],
    target_ip: str,
    roi: Optional[tuple[int, int, int, int]],
    camera_index: int,
    notes: str,
    samples: list[CalibrationSample],
) -> Path:
    table = BulbCalibrationTable(
        key=profile_key,
        bulb_mac=profile_mac,
        bulb_model=profile_model,
        bulb_ip=target_ip,
        source="camera",
        roi=roi,
        camera_index=camera_index,
        notes=notes,
        samples=samples,
    )
    return store.save(table)


def measurement_needs_retry(
    target_rgb: tuple[int, int, int],
    measured_rgb: tuple[int, int, int],
) -> bool:
    """Return True when a direct-calibration measurement is obviously invalid."""

    if target_rgb == (0, 0, 0):
        return sum(measured_rgb) > 72
    if measured_rgb == (0, 0, 0):
        return True
    if max(target_rgb) >= 176 and max(measured_rgb) < 24:
        return True
    return False


def restore_state(
    runner: BackgroundAsyncLoop,
    controller: BulbController,
    ip: str,
    state: BulbState,
    *,
    timeout: float,
    retries: int,
    retry_delay: float,
) -> None:
    if not state.is_on:
        run_bulb_command(
            runner,
            lambda: controller.turn_off(ip),
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
        )
        return

    if state.rgb is not None:
        run_bulb_command(
            runner,
            lambda: controller.set_rgb_exact(ip, *state.rgb, state.brightness),
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
        )
        return

    if state.color_temp is not None:
        run_bulb_command(
            runner,
            lambda: controller.set_color_temp(ip, state.color_temp, state.brightness),
            timeout=timeout,
            retries=retries,
            retry_delay=retry_delay,
        )
        return

    run_bulb_command(
        runner,
        lambda: controller.turn_on(ip, state.brightness),
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
    )


def main() -> int:
    args = parse_args()
    configure_event_loop_policy()

    levels = parse_levels(args.levels)
    roi = parse_roi(args.roi)

    config = Config.load()
    controller = BulbController()
    runner = BackgroundAsyncLoop()
    camera = None
    store = CalibrationStore()

    try:
        if args.ip:
            target_ip = args.ip
        else:
            target_ips = runner.run(
                controller.resolve_screen_sync_targets([bulb.ip for bulb in config.bulbs]),
                timeout=6.0,
            )
            if not target_ips:
                print("No reachable bulbs found for calibration.")
                return 1
            target_ip = target_ips[0]

        startup_timeout = max(10.0, args.command_timeout)
        profile = runner.run(controller.get_color_profile(target_ip), timeout=startup_timeout)
        previous_state = None
        try:
            previous_state = runner.run(controller.get_state(target_ip), timeout=startup_timeout)
        except FutureTimeoutError:
            print("Warning: timed out reading the current bulb state; skipping state restore.")
        except Exception as exc:
            print(f"Warning: could not read the current bulb state ({exc}); skipping state restore.")
        mapper = BulbGamutMapper(profile)

        camera = open_camera(args.camera)

        print(f"Calibrating bulb {target_ip} ({profile.model_name or 'unknown model'})")
        print(f"Calibration key: {profile.mac or target_ip}")
        print(f"Levels: {levels} -> {len(levels) ** 3} samples")

        settle_time = max(args.settle, (max(profile.fade_in_ms, profile.fade_out_ms) / 1000.0) + 0.2)
        preflight_payload = mapper.map_rgb((255, 255, 255))
        run_bulb_command(
            runner,
            lambda: controller.set_screen_sync_payload(target_ip, preflight_payload),
            timeout=args.command_timeout,
            retries=args.retries,
            retry_delay=args.retry_delay,
        )
        time.sleep(settle_time)
        print("Waiting for live camera stream...")
        assert_camera_feed_live(
            camera,
            roi=roi,
            frame_count=12,
            max_wait_seconds=35.0,
            poll_interval=3.0,
        )
        print("Stabilizing camera feed...")
        stabilize_camera_feed(
            camera,
            duration_seconds=args.camera_stabilize_seconds,
            roi=roi,
        )

        if roi is None:
            print("Trying automatic bulb ROI detection...")
            roi = detect_roi_from_flash(
                camera,
                controller,
                runner,
                target_ip,
                settle_time,
                args.command_timeout,
                args.retries,
                args.retry_delay,
            )
            if roi is None:
                print("Auto ROI detection failed. Select the bulb region in the camera preview.")
                roi = select_roi(camera)
            else:
                print(f"Auto-detected ROI: {roi}")
        print(f"Using ROI: {roi}")

        colors = calibration_colors(levels)
        samples: list[CalibrationSample] = []
        partial_path: Optional[Path] = None

        for index, color in enumerate(colors, start=1):
            payload = mapper.map_rgb(color)
            print(
                f"[{index:02d}/{len(colors):02d}] target={color} payload={payload}",
                end="",
                flush=True,
            )
            measured = (0, 0, 0)
            for measurement_attempt in range(3):
                try:
                    if color == (0, 0, 0):
                        run_bulb_command(
                            runner,
                            lambda: controller.turn_off(target_ip),
                            timeout=args.command_timeout,
                            retries=args.retries,
                            retry_delay=args.retry_delay,
                        )
                    else:
                        run_bulb_command(
                            runner,
                            lambda payload=payload: controller.set_screen_sync_payload(target_ip, payload),
                            timeout=args.command_timeout,
                            retries=args.retries,
                            retry_delay=args.retry_delay,
                        )
                except RuntimeError as exc:
                    print(f" failed ({exc})")
                    if samples:
                        partial_path = save_partial_calibration(
                            store,
                            profile.mac or target_ip,
                            profile.mac,
                            profile.model_name,
                            target_ip,
                            roi,
                            args.camera,
                            args.notes,
                            samples,
                        )
                        print(f"Saved partial calibration: {partial_path}")
                    raise
                if settle_time > 0:
                    time.sleep(settle_time + (0.25 * measurement_attempt))
                measured = capture_measurement(camera, roi, args.warmup_frames, args.frames)
                if not measurement_needs_retry(color, measured):
                    break
                if measurement_attempt < 2:
                    print(f" retry#{measurement_attempt + 1}", end="", flush=True)
            samples.append(CalibrationSample(target_rgb=color, measured_rgb=measured, payload=payload))
            print(f" measured={measured}")

        output_path = save_partial_calibration(
            store,
            profile.mac or target_ip,
            profile.mac,
            profile.model_name,
            target_ip,
            roi,
            args.camera,
            args.notes,
            samples,
        )
        print(f"\nSaved calibration: {output_path}")
        print("Restart screen sync to load the new per-bulb calibration table.")
        return 0
    finally:
        if camera is not None:
            camera.release()
            cv2.destroyAllWindows()
        try:
            if previous_state is not None and "target_ip" in locals():
                restore_state(
                    runner,
                    controller,
                    target_ip,
                    previous_state,
                    timeout=args.command_timeout,
                    retries=args.retries,
                    retry_delay=args.retry_delay,
                )
        except Exception:
            pass
        try:
            runner.run(controller.close_async(), timeout=args.command_timeout)
        finally:
            runner.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
