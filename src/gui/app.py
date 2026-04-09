"""Tkinter-based GUI for WizLight."""

import tkinter as tk
from concurrent.futures import Future
from tkinter import colorchooser, ttk
from typing import Optional

from ..core.async_runtime import BackgroundAsyncLoop
from ..core.bulb_controller import BulbController, PRESETS, apply_preset
from ..core.config import COLOR_ALGORITHMS, Config, SCREEN_SYNC_MODES, SCREEN_SYNC_REGIONS
from ..features.clap_detector import ClapConfig, ClapDetector
from ..features.screen_sync import (
    average_colors,
    build_bulb_color_map,
    effective_screen_sync_mode,
    list_monitors,
    resolve_active_regions,
)
from ..features.screen_sync_v2 import OptimizedScreenSync, build_optimized_capture_config


UNASSIGNED_REGION = "(Unassigned)"


class WizLightGUI:
    """Main GUI application for WizLight."""

    def __init__(self):
        self.config = Config.load()
        self.controller = BulbController()

        self.screen_sync: Optional[OptimizedScreenSync] = None
        self.clap_detector: Optional[ClapDetector] = None
        self._async_runner = BackgroundAsyncLoop()

        self._brightness_debounce_id = None
        self._temp_debounce_id = None
        self._screen_sync_settings_save_id = None
        self._screen_sync_reconfigure_id = None
        self._screen_sync_debug_id = None
        self._pending_tasks: list[Future] = []
        self._screen_layout_vars: dict[str, tk.StringVar] = {}

        self._monitor_options = self._load_monitor_options()
        self._monitor_label_to_index = {
            option["label"]: int(option["index"]) for option in self._monitor_options
        }

        self.root = tk.Tk()
        self.root.title("WizLight Controller")
        self.root.geometry("660x860")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._update_bulb_list()

    def _load_monitor_options(self) -> list[dict[str, int | str]]:
        try:
            return list_monitors()
        except Exception:
            return [{"index": 0, "label": "Primary monitor"}]

    def _run_async(self, coro):
        """Run async coroutine from the GUI thread."""

        future = self._async_runner.submit(coro)
        self._pending_tasks.append(future)
        self._pending_tasks = [task for task in self._pending_tasks if not task.done()]

    def _get_bulb_ips(self) -> list[str]:
        return [bulb.ip for bulb in self.config.bulbs]

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        bulb_frame = ttk.LabelFrame(main_frame, text="Bulbs", padding="5")
        bulb_frame.pack(fill=tk.X, pady=(0, 10))

        self.bulb_listbox = tk.Listbox(bulb_frame, height=4)
        self.bulb_listbox.pack(fill=tk.X, pady=(0, 5))

        bulb_btn_frame = ttk.Frame(bulb_frame)
        bulb_btn_frame.pack(fill=tk.X)

        ttk.Button(bulb_btn_frame, text="Discover", command=self._discover_bulbs).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(bulb_btn_frame, text="Remove Stale", command=self._remove_stale_bulbs).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(bulb_btn_frame, text="Refresh", command=self._update_bulb_list).pack(
            side=tk.LEFT, padx=2
        )

        power_frame = ttk.LabelFrame(main_frame, text="Power", padding="5")
        power_frame.pack(fill=tk.X, pady=(0, 10))

        power_btn_frame = ttk.Frame(power_frame)
        power_btn_frame.pack()

        ttk.Button(power_btn_frame, text="ON", command=self._turn_on, width=10).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(power_btn_frame, text="OFF", command=self._turn_off, width=10).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(power_btn_frame, text="Toggle", command=self._toggle, width=10).pack(
            side=tk.LEFT, padx=5
        )

        brightness_frame = ttk.LabelFrame(main_frame, text="Brightness", padding="5")
        brightness_frame.pack(fill=tk.X, pady=(0, 10))

        self.brightness_var = tk.IntVar(value=128)
        self.brightness_slider = ttk.Scale(
            brightness_frame,
            from_=0,
            to=255,
            variable=self.brightness_var,
            orient=tk.HORIZONTAL,
            command=self._on_brightness_change,
        )
        self.brightness_slider.pack(fill=tk.X, pady=5)

        self.brightness_label = ttk.Label(brightness_frame, text="128")
        self.brightness_label.pack()

        color_frame = ttk.LabelFrame(main_frame, text="Color", padding="5")
        color_frame.pack(fill=tk.X, pady=(0, 10))

        color_btn_frame = ttk.Frame(color_frame)
        color_btn_frame.pack()

        self.color_preview = tk.Canvas(
            color_btn_frame,
            width=40,
            height=40,
            bg="#FFFFFF",
            relief=tk.SUNKEN,
        )
        self.color_preview.pack(side=tk.LEFT, padx=5)

        ttk.Button(color_btn_frame, text="Pick Color", command=self._pick_color).pack(
            side=tk.LEFT, padx=5
        )

        temp_frame = ttk.Frame(color_frame)
        temp_frame.pack(fill=tk.X, pady=5)

        ttk.Label(temp_frame, text="Color Temp:").pack(side=tk.LEFT)
        self.temp_var = tk.IntVar(value=4000)
        temp_scale = ttk.Scale(
            temp_frame,
            from_=2200,
            to=6500,
            variable=self.temp_var,
            orient=tk.HORIZONTAL,
            command=self._on_temp_change,
        )
        temp_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.temp_label = ttk.Label(temp_frame, text="4000K")
        self.temp_label.pack(side=tk.LEFT)

        preset_frame = ttk.LabelFrame(main_frame, text="Presets", padding="5")
        preset_frame.pack(fill=tk.X, pady=(0, 10))

        preset_btn_frame = ttk.Frame(preset_frame)
        preset_btn_frame.pack()

        row = 0
        col = 0
        for preset_name in PRESETS.keys():
            button = ttk.Button(
                preset_btn_frame,
                text=preset_name.title(),
                command=lambda preset=preset_name: self._apply_preset(preset),
                width=10,
            )
            button.grid(row=row, column=col, padx=2, pady=2)
            col += 1
            if col >= 5:
                col = 0
                row += 1

        features_frame = ttk.LabelFrame(main_frame, text="Features", padding="5")
        features_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.screen_sync_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            features_frame,
            text="Screen Sync",
            variable=self.screen_sync_var,
            command=self._toggle_screen_sync,
        ).pack(anchor=tk.W)

        ttk.Label(
            features_frame,
            text="Ambient sync can run in single-color or zone mode.",
        ).pack(anchor=tk.W, pady=(0, 6))

        self._build_screen_sync_settings(features_frame)

        self.clap_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            features_frame,
            text="Clap Detection (double clap to toggle)",
            variable=self.clap_var,
            command=self._toggle_clap_detection,
        ).pack(anchor=tk.W, pady=(12, 0))

        self._build_screen_sync_debug(features_frame)

        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_screen_sync_settings(self, parent):
        settings = self.config.screen_sync
        self.screen_sync_settings_frame = ttk.LabelFrame(
            parent,
            text="Screen Sync Settings",
            padding="8",
        )
        self.screen_sync_settings_frame.pack(fill=tk.X)

        self.screen_monitor_var = tk.StringVar()
        self.screen_fps_var = tk.IntVar(value=settings.max_fps)
        self.screen_min_fps_var = tk.IntVar(value=settings.min_fps)
        self.screen_smoothing_var = tk.DoubleVar(value=settings.smoothing)
        self.screen_boost_var = tk.DoubleVar(value=settings.color_boost)
        self.screen_min_brightness_var = tk.IntVar(value=settings.min_brightness)
        self.screen_ignore_letterbox_var = tk.BooleanVar(value=settings.ignore_letterbox)
        self.screen_algorithm_var = tk.StringVar(value=settings.color_algorithm.title())
        self.screen_use_gpu_var = tk.BooleanVar(value=settings.use_gpu)
        self.screen_adaptive_fps_var = tk.BooleanVar(value=settings.adaptive_fps)
        self.screen_predictive_var = tk.BooleanVar(value=settings.predictive_smoothing)

        monitor_label = next(
            (
                option["label"]
                for option in self._monitor_options
                if int(option["index"]) == settings.monitor
            ),
            self._monitor_options[0]["label"],
        )
        self.screen_monitor_var.set(str(monitor_label))

        mode_row = ttk.Frame(self.screen_sync_settings_frame)
        mode_row.pack(fill=tk.X, pady=2)
        ttk.Label(mode_row, text="Mode:", width=14).pack(side=tk.LEFT)
        self.mode_combo = ttk.Combobox(
            mode_row,
            values=[mode.title() for mode in SCREEN_SYNC_MODES],
            state="readonly",
            width=14,
        )
        self.mode_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.mode_combo.set(settings.mode.title())
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_screen_sync_setting_change)

        ttk.Label(mode_row, text="Monitor:", width=10).pack(side=tk.LEFT)
        self.monitor_combo = ttk.Combobox(
            mode_row,
            values=[str(option["label"]) for option in self._monitor_options],
            state="readonly",
            width=28,
            textvariable=self.screen_monitor_var,
        )
        self.monitor_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.monitor_combo.bind("<<ComboboxSelected>>", self._on_screen_sync_setting_change)

        tuning_row = ttk.Frame(self.screen_sync_settings_frame)
        tuning_row.pack(fill=tk.X, pady=2)
        ttk.Label(tuning_row, text="Max FPS:", width=14).pack(side=tk.LEFT)
        self.fps_spinbox = tk.Spinbox(
            tuning_row,
            from_=4,
            to=60,
            width=6,
            textvariable=self.screen_fps_var,
            command=self._queue_screen_sync_settings_save,
        )
        self.fps_spinbox.pack(side=tk.LEFT, padx=(0, 10))
        self.fps_spinbox.bind("<FocusOut>", self._on_screen_sync_setting_change)

        ttk.Label(tuning_row, text="Min FPS:", width=14).pack(side=tk.LEFT)
        self.min_fps_spinbox = tk.Spinbox(
            tuning_row,
            from_=4,
            to=30,
            width=6,
            textvariable=self.screen_min_fps_var,
            command=self._queue_screen_sync_settings_save,
        )
        self.min_fps_spinbox.pack(side=tk.LEFT, padx=(0, 10))
        self.min_fps_spinbox.bind("<FocusOut>", self._on_screen_sync_setting_change)

        ttk.Label(tuning_row, text="Min Brightness:", width=14).pack(side=tk.LEFT)
        self.min_brightness_spinbox = tk.Spinbox(
            tuning_row,
            from_=0,
            to=255,
            width=6,
            textvariable=self.screen_min_brightness_var,
            command=self._queue_screen_sync_settings_save,
        )
        self.min_brightness_spinbox.pack(side=tk.LEFT)
        self.min_brightness_spinbox.bind("<FocusOut>", self._on_screen_sync_setting_change)

        smoothing_row = ttk.Frame(self.screen_sync_settings_frame)
        smoothing_row.pack(fill=tk.X, pady=2)
        ttk.Label(smoothing_row, text="Smoothing:", width=14).pack(side=tk.LEFT)
        self.smoothing_value_label = ttk.Label(smoothing_row, width=6)
        self.smoothing_value_label.pack(side=tk.RIGHT)
        ttk.Scale(
            smoothing_row,
            from_=0.05,
            to=0.75,
            variable=self.screen_smoothing_var,
            orient=tk.HORIZONTAL,
            command=self._on_screen_smoothing_change,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        boost_row = ttk.Frame(self.screen_sync_settings_frame)
        boost_row.pack(fill=tk.X, pady=2)
        ttk.Label(boost_row, text="Color Boost:", width=14).pack(side=tk.LEFT)
        self.boost_value_label = ttk.Label(boost_row, width=6)
        self.boost_value_label.pack(side=tk.RIGHT)
        ttk.Scale(
            boost_row,
            from_=1.0,
            to=1.8,
            variable=self.screen_boost_var,
            orient=tk.HORIZONTAL,
            command=self._on_screen_boost_change,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        ttk.Checkbutton(
            self.screen_sync_settings_frame,
            text="Ignore black bars / letterboxing",
            variable=self.screen_ignore_letterbox_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(anchor=tk.W, pady=(4, 4))

        advanced_row = ttk.Frame(self.screen_sync_settings_frame)
        advanced_row.pack(fill=tk.X, pady=2)
        ttk.Label(advanced_row, text="Algorithm:", width=14).pack(side=tk.LEFT)
        self.algorithm_combo = ttk.Combobox(
            advanced_row,
            values=[algorithm.title() for algorithm in COLOR_ALGORITHMS],
            state="readonly",
            width=14,
            textvariable=self.screen_algorithm_var,
        )
        self.algorithm_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.algorithm_combo.bind("<<ComboboxSelected>>", self._on_screen_sync_setting_change)

        ttk.Checkbutton(
            advanced_row,
            text="Adaptive FPS",
            variable=self.screen_adaptive_fps_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(
            advanced_row,
            text="Predictive",
            variable=self.screen_predictive_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(side=tk.LEFT)

        ttk.Checkbutton(
            self.screen_sync_settings_frame,
            text="Use GPU capture when available",
            variable=self.screen_use_gpu_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(anchor=tk.W, pady=(2, 4))

        ttk.Label(
            self.screen_sync_settings_frame,
            text="Bulb Layout (zone mode needs at least 2 assigned regions)",
        ).pack(anchor=tk.W, pady=(4, 2))

        self.screen_layout_frame = ttk.Frame(self.screen_sync_settings_frame)
        self.screen_layout_frame.pack(fill=tk.X)

        self._refresh_screen_sync_value_labels()
        self._refresh_screen_sync_layout_controls()

    def _build_screen_sync_debug(self, parent):
        frame = ttk.LabelFrame(parent, text="Sync Debug", padding="8")
        frame.pack(fill=tk.X, pady=(12, 0))

        preview_row = ttk.Frame(frame)
        preview_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(preview_row, text="Output:", width=14).pack(side=tk.LEFT)
        self.screen_sync_debug_preview = tk.Canvas(
            preview_row,
            width=22,
            height=22,
            bg="#000000",
            relief=tk.SUNKEN,
            highlightthickness=0,
        )
        self.screen_sync_debug_preview.pack(side=tk.LEFT, padx=(0, 8))

        self.screen_sync_debug_runtime_var = tk.StringVar(value="Runtime: idle")
        self.screen_sync_debug_target_var = tk.StringVar(value="Target: -")
        self.screen_sync_debug_output_var = tk.StringVar(value="Output: -")
        self.screen_sync_debug_perf_var = tk.StringVar(value="FPS: -")
        self.screen_sync_debug_cadence_var = tk.StringVar(value="Cadence: -")
        self.screen_sync_debug_motion_var = tk.StringVar(value="Motion: -")
        self.screen_sync_debug_error_var = tk.StringVar(value="")

        for variable in (
            self.screen_sync_debug_runtime_var,
            self.screen_sync_debug_target_var,
            self.screen_sync_debug_output_var,
            self.screen_sync_debug_perf_var,
            self.screen_sync_debug_cadence_var,
            self.screen_sync_debug_motion_var,
            self.screen_sync_debug_error_var,
        ):
            ttk.Label(frame, textvariable=variable).pack(anchor=tk.W)

    def _refresh_screen_sync_value_labels(self):
        self.smoothing_value_label.config(text=f"{self.screen_smoothing_var.get():.2f}")
        self.boost_value_label.config(text=f"{self.screen_boost_var.get():.2f}x")

    def _on_screen_smoothing_change(self, _):
        self._refresh_screen_sync_value_labels()
        self._queue_screen_sync_settings_save()

    def _on_screen_boost_change(self, _):
        self._refresh_screen_sync_value_labels()
        self._queue_screen_sync_settings_save()

    def _on_screen_sync_setting_change(self, _=None):
        self._queue_screen_sync_settings_save()

    def _queue_screen_sync_settings_save(self, _=None):
        if self._screen_sync_settings_save_id:
            self.root.after_cancel(self._screen_sync_settings_save_id)
        self._screen_sync_settings_save_id = self.root.after(180, self._save_screen_sync_settings)

    def _current_clap_config(self) -> ClapConfig:
        clap = self.config.clap
        return ClapConfig(
            threshold=clap.threshold,
            rms_threshold=clap.rms_threshold,
            min_peak_to_rms=clap.min_peak_to_rms,
            adaptive_multiplier=clap.adaptive_multiplier,
            max_duration=clap.max_duration,
            cooldown=clap.cooldown,
            double_clap=clap.double_clap,
            double_clap_window=clap.double_clap_window,
        )

    def _refresh_screen_sync_layout_controls(self):
        for child in self.screen_layout_frame.winfo_children():
            child.destroy()

        current_ips = set(self._get_bulb_ips())
        self._screen_layout_vars = {
            ip: var for ip, var in self._screen_layout_vars.items() if ip in current_ips
        }

        if not self.config.bulbs:
            ttk.Label(
                self.screen_layout_frame,
                text="Add bulbs first to assign zones.",
            ).pack(anchor=tk.W)
            return

        for bulb in self.config.bulbs:
            row = ttk.Frame(self.screen_layout_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"{bulb.name} ({bulb.ip})", width=28).pack(side=tk.LEFT)

            selected_region = self.config.screen_sync.bulb_layout.get(bulb.ip)
            display_value = selected_region.title() if selected_region else UNASSIGNED_REGION
            variable = self._screen_layout_vars.setdefault(
                bulb.ip,
                tk.StringVar(value=display_value),
            )
            variable.set(display_value)

            combo = ttk.Combobox(
                row,
                values=[UNASSIGNED_REGION] + [region.title() for region in SCREEN_SYNC_REGIONS],
                state="readonly",
                textvariable=variable,
                width=18,
            )
            combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
            combo.bind("<<ComboboxSelected>>", self._on_screen_sync_setting_change)

    def _current_screen_layout(self) -> dict[str, str]:
        layout: dict[str, str] = {}
        for bulb in self.config.bulbs:
            variable = self._screen_layout_vars.get(bulb.ip)
            if variable is None:
                continue
            value = variable.get().strip().lower()
            if value and value != UNASSIGNED_REGION.lower() and value in SCREEN_SYNC_REGIONS:
                layout[bulb.ip] = value
        return layout

    def _save_screen_sync_settings(self):
        if self._screen_sync_settings_save_id:
            self.root.after_cancel(self._screen_sync_settings_save_id)
            self._screen_sync_settings_save_id = None

        settings = self.config.screen_sync
        if self.screen_min_fps_var.get() > self.screen_fps_var.get():
            self.screen_min_fps_var.set(self.screen_fps_var.get())
        settings.mode = self.mode_combo.get().strip().lower() or "single"
        settings.monitor = self._monitor_label_to_index.get(
            self.screen_monitor_var.get(),
            settings.monitor,
        )
        settings.fps = int(self.screen_fps_var.get())
        settings.max_fps = int(self.screen_fps_var.get())
        settings.min_fps = int(self.screen_min_fps_var.get())
        settings.smoothing = float(self.screen_smoothing_var.get())
        settings.color_boost = float(self.screen_boost_var.get())
        settings.min_brightness = int(self.screen_min_brightness_var.get())
        settings.ignore_letterbox = bool(self.screen_ignore_letterbox_var.get())
        settings.color_algorithm = self.screen_algorithm_var.get().strip().lower() or "auto"
        settings.use_gpu = bool(self.screen_use_gpu_var.get())
        settings.adaptive_fps = bool(self.screen_adaptive_fps_var.get())
        settings.predictive_smoothing = bool(self.screen_predictive_var.get())
        settings.bulb_layout = self._current_screen_layout()
        settings.__post_init__()
        self.config.save()

        if self.screen_sync and self.screen_sync.is_running:
            self._schedule_screen_sync_restart()

    def _schedule_screen_sync_restart(self):
        if self._screen_sync_reconfigure_id:
            self.root.after_cancel(self._screen_sync_reconfigure_id)
        self._screen_sync_reconfigure_id = self.root.after(250, self._restart_screen_sync)

    def _restart_screen_sync(self):
        self._screen_sync_reconfigure_id = None
        if self.screen_sync and self.screen_sync.is_running:
            self._stop_screen_sync(update_status=False)
            self._start_screen_sync()

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def _format_debug_color(self, color: Optional[tuple[int, int, int]]) -> str:
        if color is None:
            return "-"
        return f"RGB({color[0]}, {color[1]}, {color[2]})"

    def _refresh_screen_sync_debug(self):
        self._screen_sync_debug_id = None
        if not self.screen_sync or not self.screen_sync.is_running:
            self.screen_sync_debug_runtime_var.set("Runtime: idle")
            self.screen_sync_debug_target_var.set("Target: -")
            self.screen_sync_debug_output_var.set("Output: -")
            self.screen_sync_debug_perf_var.set("FPS: -")
            self.screen_sync_debug_cadence_var.set("Cadence: -")
            self.screen_sync_debug_motion_var.set("Motion: -")
            self.screen_sync_debug_error_var.set("")
            self.screen_sync_debug_preview.config(bg="#000000")
            return

        snapshot = self.screen_sync.debug_snapshot
        target_colors = snapshot["target_colors"]
        current_colors = snapshot["current_colors"]
        target = target_colors.get("all") or (
            average_colors(tuple(target_colors.values())) if target_colors else None
        )
        output = current_colors.get("all") or (
            average_colors(tuple(current_colors.values())) if current_colors else None
        )

        self.screen_sync_debug_runtime_var.set(
            f"Runtime: {snapshot['capture_method'].upper()} | {snapshot['mode']}"
        )
        self.screen_sync_debug_target_var.set(f"Target: {self._format_debug_color(target)}")
        self.screen_sync_debug_output_var.set(f"Output: {self._format_debug_color(output)}")
        self.screen_sync_debug_perf_var.set(
            f"FPS: {snapshot['current_fps']} | frame {snapshot['average_frame_time_ms']:.1f} ms"
        )
        if snapshot["send_rate_hz"] > 0:
            self.screen_sync_debug_cadence_var.set(
                f"Cadence: {snapshot['send_rate_hz']:.1f} Hz | {snapshot['send_interval_ms']:.0f} ms | sends {snapshot['updates_sent']}"
            )
        else:
            self.screen_sync_debug_cadence_var.set(
                f"Cadence: warming up | sends {snapshot['updates_sent']}"
            )
        self.screen_sync_debug_motion_var.set(
            f"Motion: {snapshot['motion_score']:.3f} | smooth {snapshot['smoothing_factor']:.2f} | predict {snapshot['prediction_weight']:.2f}"
        )
        self.screen_sync_debug_error_var.set(
            f"Error: {snapshot['last_error']}" if snapshot["last_error"] else ""
        )
        if output is not None:
            self.screen_sync_debug_preview.config(
                bg=f"#{output[0]:02x}{output[1]:02x}{output[2]:02x}"
            )

        self._screen_sync_debug_id = self.root.after(350, self._refresh_screen_sync_debug)

    def _update_bulb_list(self):
        self.bulb_listbox.delete(0, tk.END)
        for bulb in self.config.bulbs:
            self.bulb_listbox.insert(tk.END, f"{bulb.name} - {bulb.ip}")

        if not self.config.bulbs:
            self.bulb_listbox.insert(tk.END, "(No bulbs - click Discover)")

        self._refresh_screen_sync_layout_controls()

    def _discover_bulbs(self):
        self._set_status("Discovering bulbs...")

        async def discover():
            try:
                bulbs = await self.controller.discover()
                for index, bulb in enumerate(bulbs, 1):
                    self.config.add_bulb(bulb["ip"], f"Bulb {index}", bulb["mac"])
                self.root.after(0, self._update_bulb_list)
                self.root.after(0, lambda: self._set_status(f"Found {len(bulbs)} bulb(s)"))
            except Exception as exc:
                self.root.after(0, lambda: self._set_status(f"Discovery failed: {exc}"))

        self._run_async(discover())

    def _remove_stale_bulbs(self):
        configured_ips = self._get_bulb_ips()
        if not configured_ips:
            self._set_status("No bulbs configured")
            return

        self._set_status("Checking for stale bulbs...")

        async def prune():
            try:
                stale_ips = await self.controller.find_stale_bulbs(configured_ips)
                removed = self.config.remove_bulbs(stale_ips)
                self.root.after(0, self._update_bulb_list)
                if removed:
                    self.root.after(0, lambda: self._set_status(f"Removed {removed} stale bulb(s)"))
                else:
                    self.root.after(0, lambda: self._set_status("No stale bulbs found"))
            except Exception as exc:
                self.root.after(0, lambda: self._set_status(f"Stale check failed: {exc}"))

        self._run_async(prune())

    def _turn_on(self):
        ips = self._get_bulb_ips()
        if ips:
            self._run_async(self.controller.turn_on_all(ips))
            self._set_status("Turned on")

    def _turn_off(self):
        ips = self._get_bulb_ips()
        if ips:
            self._run_async(self.controller.turn_off_all(ips))
            self._set_status("Turned off")

    def _toggle(self):
        ips = self._get_bulb_ips()
        if ips:
            self._run_async(self.controller.toggle_all(ips))
            self._set_status("Toggled")

    def _on_brightness_change(self, value):
        brightness = int(float(value))
        self.brightness_label.config(text=str(brightness))

        if self._brightness_debounce_id:
            self.root.after_cancel(self._brightness_debounce_id)

        def send_brightness():
            ips = self._get_bulb_ips()
            if ips:
                self._run_async(self.controller.turn_on_all(ips, brightness))

        self._brightness_debounce_id = self.root.after(100, send_brightness)

    def _pick_color(self):
        color = colorchooser.askcolor(title="Choose Color")
        if color[0]:
            r, g, b = [int(channel) for channel in color[0]]
            self.color_preview.config(bg=color[1])

            ips = self._get_bulb_ips()
            if ips:
                self._run_async(self.controller.set_rgb_all(ips, r, g, b))
                self._set_status(f"Color: RGB({r}, {g}, {b})")

    def _on_temp_change(self, value):
        kelvin = int(float(value))
        self.temp_label.config(text=f"{kelvin}K")

        if self._temp_debounce_id:
            self.root.after_cancel(self._temp_debounce_id)

        def send_temp():
            ips = self._get_bulb_ips()
            if ips:
                self._run_async(self.controller.set_color_temp_all(ips, kelvin))

        self._temp_debounce_id = self.root.after(100, send_temp)

    def _apply_preset(self, preset_name: str):
        ips = self._get_bulb_ips()
        if ips:
            self._run_async(apply_preset(self.controller, ips, preset_name))
            self._set_status(f"Preset: {preset_name}")

    def _start_screen_sync(self):
        configured_ips = self._get_bulb_ips()
        try:
            ips = self._async_runner.run(
                self.controller.refresh_screen_sync_targets(configured_ips),
                timeout=6.0,
            )
        except Exception as exc:
            self.screen_sync_var.set(False)
            self._set_status(f"Screen sync preflight failed: {exc}")
            return
        if not ips:
            self.screen_sync_var.set(False)
            self.config.screen_sync.enabled = False
            self.config.save()
            self._set_status("No reachable bulbs available for screen sync")
            return

        self._save_screen_sync_settings()
        settings = self.config.screen_sync
        active_regions = resolve_active_regions(settings.bulb_layout, ips)
        mode = effective_screen_sync_mode(settings.mode, active_regions)

        def on_color_change(colors_by_target):
            bulb_colors = build_bulb_color_map(
                ips,
                colors_by_target,
                settings.mode,
                settings.bulb_layout,
            )
            if bulb_colors:
                self._run_async(self.controller.set_screen_sync_map(bulb_colors))

            preview = average_colors(tuple(colors_by_target.values()))
            self.root.after(
                0,
                lambda: self.color_preview.config(
                    bg=f"#{preview[0]:02x}{preview[1]:02x}{preview[2]:02x}"
                ),
            )

        self.screen_sync = OptimizedScreenSync(
            on_color_change=on_color_change,
            config=build_optimized_capture_config(settings, active_regions),
        )
        self.screen_sync.start()
        if self._screen_sync_debug_id:
            self.root.after_cancel(self._screen_sync_debug_id)
        self._refresh_screen_sync_debug()

        profile = "cinematic single" if mode == "single" and settings.color_algorithm == "auto" else settings.color_algorithm.upper()
        mapping = self.controller.summarize_screen_sync_mapping(ips)
        runtime = f"{self.screen_sync.capture_method.upper()} / {profile}"
        if mapping:
            runtime = f"{runtime} / {mapping}"
        skipped = len(configured_ips) - len(ips)
        if mode == "zones":
            self._set_status(
                f"Screen sync started in zones mode ({len(active_regions)} regions, {runtime}{', skipped ' + str(skipped) + ' stale bulb(s)' if skipped else ''})"
            )
        elif settings.mode == "zones":
            self._set_status(
                f"Screen sync started in single mode (assign 2+ bulb regions for zones, {runtime}{', skipped ' + str(skipped) + ' stale bulb(s)' if skipped else ''})"
            )
        else:
            self._set_status(
                f"Screen sync started in single mode ({runtime}{', skipped ' + str(skipped) + ' stale bulb(s)' if skipped else ''})"
            )

    def _stop_screen_sync(self, update_status: bool = True):
        if self.screen_sync:
            self.screen_sync.stop()
            self.screen_sync = None
        if self._screen_sync_debug_id:
            self.root.after_cancel(self._screen_sync_debug_id)
            self._screen_sync_debug_id = None
        self._refresh_screen_sync_debug()
        if update_status:
            self._set_status("Screen sync stopped")

    def _toggle_screen_sync(self):
        self.config.screen_sync.enabled = bool(self.screen_sync_var.get())
        self.config.save()

        if self.screen_sync_var.get():
            self._start_screen_sync()
        else:
            self._stop_screen_sync()

    def _toggle_clap_detection(self):
        self.config.clap.enabled = bool(self.clap_var.get())
        self.config.save()
        if self.clap_var.get():
            ips = self._get_bulb_ips()
            if not ips:
                self.clap_var.set(False)
                self.config.clap.enabled = False
                self.config.save()
                self._set_status("No bulbs configured")
                return

            def on_clap():
                self._run_async(self.controller.toggle_all(ips))
                self.root.after(0, lambda: self._set_status("Clap detected - toggled"))

            self.clap_detector = ClapDetector(
                on_clap=on_clap,
                config=self._current_clap_config(),
            )
            self.clap_detector.start()
            mode = "double clap" if self.config.clap.double_clap else "single clap"
            self._set_status(f"Clap detection started ({mode})")
        else:
            if self.clap_detector:
                self.clap_detector.stop()
                self.clap_detector = None
            self._set_status("Clap detection stopped")

    def _on_close(self):
        if self.screen_sync:
            self.screen_sync.stop()
            self.screen_sync = None
        if self.clap_detector:
            self.clap_detector.stop()
            self.clap_detector = None

        if self._brightness_debounce_id:
            self.root.after_cancel(self._brightness_debounce_id)
        if self._temp_debounce_id:
            self.root.after_cancel(self._temp_debounce_id)
        if self._screen_sync_settings_save_id:
            self.root.after_cancel(self._screen_sync_settings_save_id)
        if self._screen_sync_reconfigure_id:
            self.root.after_cancel(self._screen_sync_reconfigure_id)
        if self._screen_sync_debug_id:
            self.root.after_cancel(self._screen_sync_debug_id)

        for future in self._pending_tasks:
            if not future.done():
                future.cancel()

        try:
            self._async_runner.run(self.controller.close_async(), timeout=2.0)
        finally:
            self._async_runner.shutdown()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    """Entry point for the classic GUI."""

    app = WizLightGUI()
    app.run()


if __name__ == "__main__":
    main()
