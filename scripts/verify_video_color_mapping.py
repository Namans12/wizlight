#!/usr/bin/env python3
"""Verify single-bulb video color mapping against a camera ROI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.calibrate_bulb_camera import (
    assert_camera_feed_live,
    capture_measurement,
    open_camera,
    parse_roi,
    restore_state,
    run_bulb_command,
    stabilize_camera_feed,
)
from src.core.async_runtime import BackgroundAsyncLoop, configure_event_loop_policy
from src.core.bulb_controller import BulbController
from src.core.calibration import (
    CalibrationSample,
    CalibrationStore,
    BulbCalibrationTable,
    ToneCalibrationStore,
)
from src.core.config import Config
from src.features.screen_sync import (
    adaptive_color_boost,
    crop_content_area,
    enhance_color,
    perceptual_color_distance,
)
from src.features.screen_sync_v2 import extract_cinematic_single_color


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify screen-sync video mapping by measuring the bulb with a camera ROI."
    )
    parser.add_argument("video", help="Path to a local video file.")
    parser.add_argument("--ip", default="192.168.1.3", help="Bulb IP to verify.")
    parser.add_argument("--camera", type=int, help="OpenCV camera index. Defaults to the saved calibration camera.")
    parser.add_argument(
        "--roi",
        help="Manual ROI as x,y,width,height. Defaults to the saved calibration ROI.",
    )
    parser.add_argument(
        "--sample-step",
        type=float,
        default=1.0,
        help="Seconds between scene-detection probes.",
    )
    parser.add_argument(
        "--scene-gap",
        type=float,
        default=4.0,
        help="Minimum seconds between selected scenes.",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=22.0,
        help="Mean absolute RGB delta threshold for a scene cut.",
    )
    parser.add_argument(
        "--max-scenes",
        type=int,
        default=14,
        help="Maximum number of scenes to verify.",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.4,
        help="Extra settle time after each bulb update.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=6,
        help="Frames to average per measured scene.",
    )
    parser.add_argument(
        "--warmup-frames",
        type=int,
        default=4,
        help="Frames to discard after each bulb update before measuring.",
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
        "--output-dir",
        default="output/video-verify",
        help="Directory for JSON and annotated scene images.",
    )
    parser.add_argument(
        "--camera-stabilize-seconds",
        type=float,
        default=4.0,
        help="Extra time to let the live camera feed settle before the first bulb update.",
    )
    parser.add_argument(
        "--save-tone-lut",
        action="store_true",
        help="Persist a sparse scene-driven tone LUT from the measured video scenes.",
    )
    parser.add_argument(
        "--ignore-tone-lut",
        action="store_true",
        help="Ignore any saved tone LUT for this verification run.",
    )
    return parser.parse_args()


def detect_scene_times(
    video_path: Path,
    sample_step: float,
    scene_gap: float,
    threshold: float,
    max_scenes: int,
) -> tuple[list[float], float]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 23.976
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if fps else 0.0

    scene_times = [0.0]
    prev_small = None
    last_scene_time = -999.0
    t = 0.0

    while t < duration and len(scene_times) < max_scenes:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            t += sample_step
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        small = cv2.resize(rgb, (64, 36), interpolation=cv2.INTER_AREA)
        if prev_small is not None:
            delta = float(np.mean(np.abs(small.astype(np.float32) - prev_small.astype(np.float32))))
            if delta >= threshold and (t - last_scene_time) >= scene_gap:
                scene_times.append(round(t, 2))
                last_scene_time = t
        prev_small = small
        t += sample_step

    if len(scene_times) < max_scenes:
        anchors = np.linspace(0.0, max(0.0, duration - 1.0), min(8, max_scenes))
        scene_times = sorted({*scene_times, *[round(value, 2) for value in anchors]})[:max_scenes]

    cap.release()
    return scene_times, duration


def load_saved_camera_settings(ip: str) -> tuple[Optional[int], Optional[tuple[int, int, int, int]]]:
    store = CalibrationStore()
    table = store.load_any((ip, "cc4085e2d228"))
    if table is None:
        return None, None
    roi = tuple(table.roi) if table.roi else None
    return table.camera_index, roi


def annotate_scene(
    frame_rgb: np.ndarray,
    target: tuple[int, int, int],
    measured: tuple[int, int, int],
    payload: tuple[int, ...],
    scene_time: float,
    error: float,
    output_path: Path,
) -> None:
    image = Image.fromarray(frame_rgb)
    image.thumbnail((640, 360))

    target_swatch = Image.new("RGB", (120, image.height), target)
    measured_swatch = Image.new("RGB", (120, image.height), measured)
    canvas = Image.new("RGB", (image.width + 240, image.height))
    canvas.paste(image, (0, 0))
    canvas.paste(target_swatch, (image.width, 0))
    canvas.paste(measured_swatch, (image.width + 120, 0))

    draw = ImageDraw.Draw(canvas)
    text_x = image.width + 10
    draw.text((text_x, 12), f"t={scene_time:.1f}s", fill=(255, 255, 255))
    draw.text((text_x, 34), f"target={target}", fill=(255, 255, 255))
    draw.text((text_x, 56), f"measured={measured}", fill=(255, 255, 255))
    draw.text((text_x, 78), f"payload={payload}", fill=(255, 255, 255))
    draw.text((text_x, 100), f"error={error:.1f}", fill=(255, 255, 255))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def save_tone_lut(
    ip: str,
    profile_mac: Optional[str],
    profile_model: Optional[str],
    camera_index: int,
    roi: tuple[int, int, int, int],
    video_path: Path,
    scene_records: list[dict[str, object]],
) -> Path:
    reliable_records: list[dict[str, object]] = []
    for record in scene_records:
        target = tuple(int(value) for value in record["target_rgb"])
        measured = tuple(int(value) for value in record["measured_rgb"])
        error = float(record["perceptual_error"])
        target_brightness = sum(target) / 3.0
        measured_brightness = sum(measured) / 3.0
        if measured_brightness < 18.0:
            continue
        if target_brightness < 20.0 and measured_brightness < 28.0:
            continue
        if error > 230.0:
            continue
        reliable_records.append(record)

    samples = [
        CalibrationSample(
            target_rgb=tuple(int(value) for value in record["target_rgb"]),
            measured_rgb=tuple(int(value) for value in record["measured_rgb"]),
            payload=tuple(int(value) for value in record["payload"]),
        )
        for record in reliable_records
        if record.get("target_rgb") and record.get("measured_rgb")
    ]
    if len(samples) < 4:
        raise RuntimeError("Not enough reliable measured scenes to build a tone LUT.")
    table = BulbCalibrationTable(
        key=profile_mac or ip,
        bulb_mac=profile_mac,
        bulb_model=profile_model,
        bulb_ip=ip,
        source="video-tone",
        roi=roi,
        camera_index=camera_index,
        notes=f"Video tone LUT from {video_path.name}",
        strength=0.18,
        samples=samples,
    )
    return ToneCalibrationStore().save(table)


def main() -> int:
    args = parse_args()
    configure_event_loop_policy()

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise RuntimeError(f"Video not found: {video_path}")

    saved_camera_index, saved_roi = load_saved_camera_settings(args.ip)
    camera_index = args.camera if args.camera is not None else saved_camera_index
    roi = parse_roi(args.roi) if args.roi else saved_roi

    if camera_index is None:
        raise RuntimeError("No camera index provided and no saved calibration camera was found.")
    if roi is None:
        raise RuntimeError("No ROI provided and no saved calibration ROI was found.")

    output_root = Path(args.output_dir).expanduser().resolve() / video_path.stem
    output_root.mkdir(parents=True, exist_ok=True)

    config = Config.load()
    settings = config.screen_sync

    controller = BulbController()
    if args.ignore_tone_lut:
        controller._tone_store = ToneCalibrationStore(base_dir=output_root / "_empty-tone")
    runner = BackgroundAsyncLoop()
    camera = None

    try:
        startup_timeout = max(10.0, args.command_timeout)
        profile = runner.run(controller.get_color_profile(args.ip), timeout=startup_timeout)
        previous_state = None
        try:
            previous_state = runner.run(controller.get_state(args.ip), timeout=startup_timeout)
        except FutureTimeoutError:
            print("Warning: timed out reading the current bulb state; skipping state restore.")
        except Exception as exc:
            print(f"Warning: could not read the current bulb state ({exc}); skipping state restore.")
        mapper = controller._gamut_mappers[args.ip]

        scene_times, duration = detect_scene_times(
            video_path,
            args.sample_step,
            args.scene_gap,
            args.scene_threshold,
            args.max_scenes,
        )

        camera = open_camera(camera_index)
        print("Waiting for live camera stream...")
        assert_camera_feed_live(
            camera,
            frame_count=12,
            max_wait_seconds=35.0,
            poll_interval=3.0,
        )
        print("Stabilizing camera feed...")
        stabilize_camera_feed(camera, duration_seconds=args.camera_stabilize_seconds, roi=roi)
        print(f"Video: {video_path}")
        print(f"Bulb: {args.ip} ({profile.model_name or 'unknown model'})")
        print(f"Camera: {camera_index}")
        print(f"ROI: {roi}")
        print(f"Scenes: {scene_times}")

        cap = cv2.VideoCapture(str(video_path))
        report: dict[str, object] = {
            "video": str(video_path),
            "duration_seconds": round(duration, 2),
            "bulb": {
                "ip": args.ip,
                "model": profile.model_name,
                "mac": profile.mac,
            },
            "camera_index": camera_index,
            "roi": list(roi),
            "scenes": [],
        }

        errors: list[float] = []
        for index, scene_time in enumerate(scene_times, start=1):
            cap.set(cv2.CAP_PROP_POS_MSEC, scene_time * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            content = crop_content_area(rgb, ignore_letterbox=settings.ignore_letterbox)
            extracted = extract_cinematic_single_color(
                content,
                settings.sample_size,
                settings.edge_weight,
            )
            target = enhance_color(
                extracted,
                color_boost=adaptive_color_boost(extracted, settings.color_boost),
                min_brightness=settings.min_brightness,
            )
            payload = mapper.map_rgb(target)

            print(
                f"[{index:02d}/{len(scene_times):02d}] t={scene_time:.1f}s "
                f"target={target} payload={payload}",
                end="",
                flush=True,
            )
            run_bulb_command(
                runner,
                lambda target=target: controller.set_screen_sync_rgb(args.ip, *target),
                timeout=args.command_timeout,
                retries=args.retries,
                retry_delay=args.retry_delay,
            )
            time.sleep(max(0.0, args.settle))
            measured = capture_measurement(camera, roi, args.warmup_frames, args.frames)
            error = float(perceptual_color_distance(target, measured))
            errors.append(error)
            print(f" measured={measured} error={error:.1f}")

            scene_record = {
                "scene_index": index,
                "time_seconds": round(scene_time, 2),
                "extracted_rgb": list(extracted),
                "target_rgb": list(target),
                "measured_rgb": list(measured),
                "payload": list(payload),
                "perceptual_error": round(error, 2),
            }
            report["scenes"].append(scene_record)

            annotate_scene(
                content,
                target,
                measured,
                payload,
                scene_time,
                error,
                output_root / f"scene_{index:02d}_{int(scene_time * 10):04d}.png",
            )

        cap.release()

        report["summary"] = {
            "scene_count": len(report["scenes"]),
            "mean_error": round(float(np.mean(errors)), 2) if errors else None,
            "max_error": round(float(np.max(errors)), 2) if errors else None,
            "min_error": round(float(np.min(errors)), 2) if errors else None,
        }
        if args.save_tone_lut and report["scenes"]:
            tone_path = save_tone_lut(
                args.ip,
                profile.mac,
                profile.model_name,
                camera_index,
                roi,
                video_path,
                report["scenes"],
            )
            report["tone_lut_path"] = str(tone_path)
        report_path = output_root / "verification_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nSaved report: {report_path}")
        if errors:
            print(
                "Error summary: "
                f"mean={np.mean(errors):.1f}, min={np.min(errors):.1f}, max={np.max(errors):.1f}"
            )
        return 0
    finally:
        if camera is not None:
            camera.release()
            cv2.destroyAllWindows()
        try:
            if "previous_state" in locals():
                restore_state(
                    runner,
                    controller,
                    args.ip,
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
