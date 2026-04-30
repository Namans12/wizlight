"""Modern GUI for WizLight using CustomTkinter."""

import gc
from concurrent.futures import Future
from typing import Optional

import customtkinter as ctk

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

# Import new components
from .components.animations import AnimationMixin, animate_value, ease_out_cubic
from .components.color_wheel import ColorWheelPicker
from .components.dashboard import BulbDashboard, BulbInfo
from .components.smooth_scrollable_frame import SmoothScrollableFrame
from .components.tray import SystemTrayManager, is_tray_available


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

UNASSIGNED_REGION = "(Unassigned)"


class ModernColorPicker(ctk.CTkToplevel):
    """Minimal RGB picker (legacy fallback)."""

    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Pick Color")
        self.geometry("300x320")
        self.resizable(False, False)

        self.r_var = ctk.IntVar(value=255)
        self.g_var = ctk.IntVar(value=255)
        self.b_var = ctk.IntVar(value=255)

        for label, variable in (("Red", self.r_var), ("Green", self.g_var), ("Blue", self.b_var)):
            ctk.CTkLabel(self, text=label, font=("Segoe UI", 13)).pack(pady=(12, 4))
            ctk.CTkSlider(self, from_=0, to=255, variable=variable, command=self._update_preview).pack(
                fill="x", padx=20
            )

        self.preview = ctk.CTkFrame(self, width=90, height=90, corner_radius=14)
        self.preview.pack(pady=18)
        self._update_preview(None)

        ctk.CTkButton(self, text="Apply", command=self._apply).pack(fill="x", padx=20, pady=(0, 16))

    def _update_preview(self, _):
        color = f"#{self.r_var.get():02x}{self.g_var.get():02x}{self.b_var.get():02x}"
        self.preview.configure(fg_color=color)

    def _apply(self):
        self.callback(self.r_var.get(), self.g_var.get(), self.b_var.get())
        self.destroy()


class WizLightModernGUI(AnimationMixin):
    """Modern GUI application for WizLight."""

    @staticmethod
    def _surface_color(widget) -> str | tuple[str, str]:
        color = widget.cget("fg_color")
        return color if color != "transparent" else ("gray92", "gray14")

    def __init__(self):
        AnimationMixin.__init__(self)
        self.config = Config.load()
        self.controller = BulbController()
        self.screen_sync: Optional[OptimizedScreenSync] = None
        self.clap_detector: Optional[ClapDetector] = None
        self._async_runner = BackgroundAsyncLoop()
        self._pending_tasks: list[Future] = []
        self._brightness_debounce_id = None
        self._temp_debounce_id = None
        self._screen_sync_settings_save_id = None
        self._screen_sync_reconfigure_id = None
        self._screen_sync_debug_id = None
        self._screen_layout_vars: dict[str, ctk.StringVar] = {}
        self._monitor_options = self._load_monitor_options()
        self._monitor_label_to_index = {
            option["label"]: int(option["index"]) for option in self._monitor_options
        }
        
        # System tray
        self._tray: Optional[SystemTrayManager] = None
        self._minimized_to_tray = False
        
        # Dashboard
        self._dashboard: Optional[BulbDashboard] = None
        self._current_view = "controls"  # "controls" or "dashboard"

        self.root = ctk.CTk()
        self.root.title("WizLight")
        self.root.geometry("520x920")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._update_bulb_status()
        self._setup_system_tray()

    def _load_monitor_options(self) -> list[dict[str, int | str]]:
        try:
            return list_monitors()
        except Exception:
            return [{"index": 0, "label": "Primary monitor"}]

    def _run_async(self, coro):
        future = self._async_runner.submit(coro)
        self._pending_tasks.append(future)
        self._pending_tasks = [task for task in self._pending_tasks if not task.done()]

    def _get_bulb_ips(self) -> list[str]:
        return [bulb.ip for bulb in self.config.bulbs]

    def _build_ui(self):
        self._main_container = ctk.CTkFrame(self.root, fg_color="transparent")
        self._main_container.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Header with view toggle
        header = ctk.CTkFrame(self._main_container)
        header.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(header, text="WizLight", font=("Segoe UI", 26, "bold")).pack(side="left", padx=12, pady=12)
        
        # View toggle buttons
        view_toggle = ctk.CTkFrame(header, fg_color="transparent")
        view_toggle.pack(side="right", padx=12)
        
        self._controls_btn = ctk.CTkButton(
            view_toggle, text="Controls", width=80,
            command=lambda: self._switch_view("controls"),
            fg_color=("#3B8ED0", "#1F6AA5")
        )
        self._controls_btn.pack(side="left", padx=(0, 4))
        
        self._dashboard_btn = ctk.CTkButton(
            view_toggle, text="Dashboard", width=80,
            command=lambda: self._switch_view("dashboard"),
            fg_color="transparent", border_width=1
        )
        self._dashboard_btn.pack(side="left", padx=(0, 8))
        
        # Minimize to tray button
        if is_tray_available():
            self._tray_btn = ctk.CTkButton(
                view_toggle, text="⊟", width=32,
                command=self._minimize_to_tray,
                fg_color="transparent", border_width=1
            )
            self._tray_btn.pack(side="left")
        
        self.status_label = ctk.CTkLabel(header, text="", text_color="gray")
        self.status_label.pack(side="right", padx=12)
        
        # Controls view (scrollable)
        self._controls_frame = SmoothScrollableFrame(
            self._main_container,
            fg_color=("gray92", "gray14"),
        )
        self._controls_frame.pack(fill="both", expand=True)
        
        # Dashboard view (hidden initially)
        self._dashboard_frame = ctk.CTkFrame(self._main_container, fg_color="transparent")
        
        # Build controls view content
        self._build_controls_view()
        
        # Build dashboard
        self._dashboard = BulbDashboard(
            self._dashboard_frame,
            self.controller,
            self._run_async,
            on_bulb_select=self._on_bulb_select
        )
        self._dashboard.pack(fill="both", expand=True)
    
    def _build_controls_view(self):
        """Build the main controls view."""
        main = self._controls_frame

        bulbs = ctk.CTkFrame(main)
        bulbs.pack(fill="x", pady=(0, 12))
        bulb_actions = ctk.CTkFrame(bulbs, fg_color=self._surface_color(bulbs))
        bulb_actions.pack(side="right", padx=12, pady=12)
        ctk.CTkButton(
            bulb_actions,
            text="Remove Stale",
            command=self._remove_stale_bulbs,
            width=120,
        ).pack(side="right")
        ctk.CTkButton(
            bulb_actions,
            text="Discover",
            command=self._discover_bulbs,
            width=110,
        ).pack(side="right", padx=(0, 8))
        ctk.CTkLabel(bulbs, text="Configured Bulbs", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=12, pady=(12, 4)
        )
        self.bulb_info = ctk.CTkLabel(bulbs, text="No bulbs found", justify="left", text_color="gray")
        self.bulb_info.pack(anchor="w", padx=12, pady=(0, 12))

        controls = ctk.CTkFrame(main)
        controls.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(controls, text="Quick Controls", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=12, pady=(12, 8)
        )
        button_row = ctk.CTkFrame(controls, fg_color=self._surface_color(controls))
        button_row.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(button_row, text="ON", command=self._turn_on, width=90).pack(side="left", padx=(0, 8))
        ctk.CTkButton(button_row, text="OFF", command=self._turn_off, width=90).pack(side="left", padx=(0, 8))
        ctk.CTkButton(button_row, text="Toggle", command=self._toggle, width=90).pack(side="left")

        self.brightness_label = ctk.CTkLabel(controls, text="Brightness: 50%")
        self.brightness_label.pack(anchor="w", padx=12)
        self.brightness_slider = ctk.CTkSlider(controls, from_=0, to=255, command=self._on_brightness_change)
        self.brightness_slider.set(128)
        self.brightness_slider.pack(fill="x", padx=12, pady=(0, 12))

        color_row = ctk.CTkFrame(controls, fg_color=self._surface_color(controls))
        color_row.pack(fill="x", padx=12, pady=(0, 8))
        self.color_preview = ctk.CTkFrame(color_row, width=42, height=42, corner_radius=10, fg_color="#ffffff")
        self.color_preview.pack(side="left", padx=(0, 10))
        self.color_preview.pack_propagate(False)
        ctk.CTkButton(color_row, text="Pick Color", command=self._pick_color).pack(side="left", padx=(0, 8))
        self.preset_menu = ctk.CTkOptionMenu(
            color_row,
            values=[name.title() for name in PRESETS],
            command=self._apply_preset_from_menu,
        )
        self.preset_menu.pack(side="left", fill="x", expand=True)
        self.preset_menu.set("Preset")

        self.temp_label = ctk.CTkLabel(controls, text="Color Temp: 4000K")
        self.temp_label.pack(anchor="w", padx=12)
        self.temp_slider = ctk.CTkSlider(controls, from_=2200, to=6500, command=self._on_temp_change)
        self.temp_slider.set(4000)
        self.temp_slider.pack(fill="x", padx=12, pady=(0, 12))

        features = ctk.CTkFrame(main)
        features.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(features, text="Features", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=12, pady=(12, 8)
        )
        screen_row = ctk.CTkFrame(features, fg_color=self._surface_color(features))
        screen_row.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(screen_row, text="Screen Sync").pack(side="left")
        self.screen_sync_switch = ctk.CTkSwitch(screen_row, text="", command=self._toggle_screen_sync)
        self.screen_sync_switch.pack(side="right")
        clap_row = ctk.CTkFrame(features, fg_color=self._surface_color(features))
        clap_row.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(clap_row, text="Clap Detection").pack(side="left")
        self.clap_switch = ctk.CTkSwitch(clap_row, text="", command=self._toggle_clap_detection)
        self.clap_switch.pack(side="right")

        settings = ctk.CTkFrame(main)
        settings.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(settings, text="Screen Sync Settings", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=12, pady=(12, 8)
        )

        self.mode_menu = ctk.CTkOptionMenu(
            settings,
            values=[mode.title() for mode in SCREEN_SYNC_MODES],
            command=self._queue_screen_sync_settings_save,
        )
        self.mode_menu.pack(fill="x", padx=12, pady=(0, 8))
        self.mode_menu.set(self.config.screen_sync.mode.title())

        self.monitor_menu = ctk.CTkOptionMenu(
            settings,
            values=[str(option["label"]) for option in self._monitor_options],
            command=self._queue_screen_sync_settings_save,
        )
        self.monitor_menu.pack(fill="x", padx=12, pady=(0, 8))
        selected_monitor = next(
            (
                str(option["label"])
                for option in self._monitor_options
                if int(option["index"]) == self.config.screen_sync.monitor
            ),
            str(self._monitor_options[0]["label"]),
        )
        self.monitor_menu.set(selected_monitor)

        self.fps_value = ctk.IntVar(value=self.config.screen_sync.max_fps)
        self.min_fps_value = ctk.IntVar(value=self.config.screen_sync.min_fps)
        self.smoothing_value = ctk.DoubleVar(value=self.config.screen_sync.smoothing)
        self.boost_value = ctk.DoubleVar(value=self.config.screen_sync.color_boost)
        self.min_brightness_value = ctk.IntVar(value=self.config.screen_sync.min_brightness)
        self.ignore_letterbox_var = ctk.BooleanVar(value=self.config.screen_sync.ignore_letterbox)
        self.algorithm_value = ctk.StringVar(value=self.config.screen_sync.color_algorithm.title())
        self.use_gpu_var = ctk.BooleanVar(value=self.config.screen_sync.use_gpu)
        self.adaptive_fps_var = ctk.BooleanVar(value=self.config.screen_sync.adaptive_fps)
        self.predictive_smoothing_var = ctk.BooleanVar(value=self.config.screen_sync.predictive_smoothing)

        self.fps_label = ctk.CTkLabel(settings, text=f"Max FPS: {self.fps_value.get()}")
        self.fps_label.pack(anchor="w", padx=12)
        self.fps_slider = ctk.CTkSlider(settings, from_=4, to=60, number_of_steps=56, command=self._on_fps_change)
        self.fps_slider.set(self.fps_value.get())
        self.fps_slider.pack(fill="x", padx=12, pady=(0, 8))

        self.min_fps_label = ctk.CTkLabel(settings, text=f"Min FPS: {self.min_fps_value.get()}")
        self.min_fps_label.pack(anchor="w", padx=12)
        self.min_fps_slider = ctk.CTkSlider(
            settings,
            from_=4,
            to=30,
            number_of_steps=26,
            command=self._on_min_fps_change,
        )
        self.min_fps_slider.set(self.min_fps_value.get())
        self.min_fps_slider.pack(fill="x", padx=12, pady=(0, 8))

        self.smoothing_label = ctk.CTkLabel(settings, text=f"Smoothing: {self.smoothing_value.get():.2f}")
        self.smoothing_label.pack(anchor="w", padx=12)
        self.smoothing_slider = ctk.CTkSlider(settings, from_=0.05, to=0.75, command=self._on_smoothing_change)
        self.smoothing_slider.set(self.smoothing_value.get())
        self.smoothing_slider.pack(fill="x", padx=12, pady=(0, 8))

        self.boost_label = ctk.CTkLabel(settings, text=f"Color Boost: {self.boost_value.get():.2f}x")
        self.boost_label.pack(anchor="w", padx=12)
        self.boost_slider = ctk.CTkSlider(settings, from_=1.0, to=1.8, command=self._on_boost_change)
        self.boost_slider.set(self.boost_value.get())
        self.boost_slider.pack(fill="x", padx=12, pady=(0, 8))

        self.min_brightness_label = ctk.CTkLabel(
            settings,
            text=f"Min Brightness: {self.min_brightness_value.get()}",
        )
        self.min_brightness_label.pack(anchor="w", padx=12)
        self.min_brightness_slider = ctk.CTkSlider(
            settings,
            from_=0,
            to=80,
            number_of_steps=80,
            command=self._on_min_brightness_change,
        )
        self.min_brightness_slider.set(self.min_brightness_value.get())
        self.min_brightness_slider.pack(fill="x", padx=12, pady=(0, 8))

        self.algorithm_menu = ctk.CTkOptionMenu(
            settings,
            values=[algorithm.title() for algorithm in COLOR_ALGORITHMS],
            variable=self.algorithm_value,
            command=self._queue_screen_sync_settings_save,
        )
        self.algorithm_menu.pack(fill="x", padx=12, pady=(0, 8))

        advanced_flags = ctk.CTkFrame(settings, fg_color=self._surface_color(settings))
        advanced_flags.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkCheckBox(
            advanced_flags,
            text="Adaptive FPS",
            variable=self.adaptive_fps_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkCheckBox(
            advanced_flags,
            text="Predictive",
            variable=self.predictive_smoothing_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(side="left")

        ctk.CTkCheckBox(
            settings,
            text="Use GPU capture when available",
            variable=self.use_gpu_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkCheckBox(
            settings,
            text="Ignore black bars / letterboxing",
            variable=self.ignore_letterbox_var,
            command=self._queue_screen_sync_settings_save,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        ctk.CTkLabel(
            settings,
            text="Bulb Layout (zone mode needs at least 2 assigned regions)",
            text_color="gray",
        ).pack(anchor="w", padx=12)
        self.layout_frame = ctk.CTkFrame(settings, fg_color=self._surface_color(settings))
        self.layout_frame.pack(fill="x", padx=12, pady=(6, 12))
        self._refresh_screen_sync_layout_controls()
        self._build_screen_sync_debug(main)

    def _build_screen_sync_debug(self, parent):
        card = ctk.CTkFrame(parent)
        card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(card, text="Sync Debug", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", padx=12, pady=(12, 8)
        )

        preview_row = ctk.CTkFrame(card, fg_color=self._surface_color(card))
        preview_row.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(preview_row, text="Output", width=80, anchor="w").pack(side="left")
        self.screen_sync_debug_preview = ctk.CTkFrame(
            preview_row,
            width=22,
            height=22,
            corner_radius=6,
            fg_color="#000000",
        )
        self.screen_sync_debug_preview.pack(side="left", padx=(0, 8))
        self.screen_sync_debug_preview.pack_propagate(False)

        self.screen_sync_debug_runtime = ctk.StringVar(value="Runtime: idle")
        self.screen_sync_debug_target = ctk.StringVar(value="Target: -")
        self.screen_sync_debug_output = ctk.StringVar(value="Output: -")
        self.screen_sync_debug_perf = ctk.StringVar(value="FPS: -")
        self.screen_sync_debug_cadence = ctk.StringVar(value="Cadence: -")
        self.screen_sync_debug_motion = ctk.StringVar(value="Motion: -")
        self.screen_sync_debug_error = ctk.StringVar(value="")

        for variable in (
            self.screen_sync_debug_runtime,
            self.screen_sync_debug_target,
            self.screen_sync_debug_output,
            self.screen_sync_debug_perf,
            self.screen_sync_debug_cadence,
            self.screen_sync_debug_motion,
            self.screen_sync_debug_error,
        ):
            ctk.CTkLabel(card, textvariable=variable, anchor="w", justify="left").pack(
                anchor="w", padx=12, pady=(0, 2)
            )

    def _refresh_screen_sync_layout_controls(self):
        for child in self.layout_frame.winfo_children():
            child.destroy()
        current_ips = set(self._get_bulb_ips())
        self._screen_layout_vars = {ip: var for ip, var in self._screen_layout_vars.items() if ip in current_ips}
        if not self.config.bulbs:
            ctk.CTkLabel(self.layout_frame, text="Add bulbs first.", text_color="gray").pack(anchor="w")
            return
        values = [UNASSIGNED_REGION] + [region.title() for region in SCREEN_SYNC_REGIONS]
        for bulb in self.config.bulbs:
            row = ctk.CTkFrame(self.layout_frame, fg_color=self._surface_color(self.layout_frame))
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=f"{bulb.name} ({bulb.ip})", anchor="w").pack(side="left")
            selected = self.config.screen_sync.bulb_layout.get(bulb.ip)
            display = selected.title() if selected else UNASSIGNED_REGION
            variable = self._screen_layout_vars.setdefault(bulb.ip, ctk.StringVar(value=display))
            variable.set(display)
            ctk.CTkOptionMenu(
                row,
                values=values,
                variable=variable,
                command=lambda _value, ip=bulb.ip: self._on_layout_change(ip),
                width=170,
            ).pack(side="right")

    def _current_screen_layout(self) -> dict[str, str]:
        layout: dict[str, str] = {}
        for bulb in self.config.bulbs:
            value = self._screen_layout_vars.get(bulb.ip)
            if value is None:
                continue
            region = value.get().strip().lower()
            if region and region != UNASSIGNED_REGION.lower() and region in SCREEN_SYNC_REGIONS:
                layout[bulb.ip] = region
        return layout

    def _save_screen_sync_settings(self):
        if self._screen_sync_settings_save_id:
            self.root.after_cancel(self._screen_sync_settings_save_id)
            self._screen_sync_settings_save_id = None
        settings = self.config.screen_sync
        settings.mode = self.mode_menu.get().strip().lower()
        settings.monitor = self._monitor_label_to_index.get(self.monitor_menu.get(), settings.monitor)
        settings.fps = int(self.fps_value.get())
        settings.max_fps = int(self.fps_value.get())
        settings.min_fps = int(self.min_fps_value.get())
        settings.smoothing = float(self.smoothing_value.get())
        settings.color_boost = float(self.boost_value.get())
        settings.min_brightness = int(self.min_brightness_value.get())
        settings.ignore_letterbox = bool(self.ignore_letterbox_var.get())
        settings.color_algorithm = self.algorithm_menu.get().strip().lower()
        settings.use_gpu = bool(self.use_gpu_var.get())
        settings.adaptive_fps = bool(self.adaptive_fps_var.get())
        settings.predictive_smoothing = bool(self.predictive_smoothing_var.get())
        settings.bulb_layout = self._current_screen_layout()
        settings.__post_init__()
        self.config.save()
        if self.screen_sync and self.screen_sync.is_running:
            self._schedule_screen_sync_restart()

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

    def _schedule_screen_sync_restart(self):
        if self._screen_sync_reconfigure_id:
            self.root.after_cancel(self._screen_sync_reconfigure_id)
        self._screen_sync_reconfigure_id = self.root.after(250, self._restart_screen_sync)

    def _restart_screen_sync(self):
        self._screen_sync_reconfigure_id = None
        if self.screen_sync and self.screen_sync.is_running:
            self._stop_screen_sync(update_status=False)
            self._start_screen_sync()

    def _on_fps_change(self, value):
        self.fps_value.set(int(float(value)))
        self.fps_label.configure(text=f"Max FPS: {self.fps_value.get()}")
        if self.min_fps_value.get() > self.fps_value.get():
            self.min_fps_value.set(self.fps_value.get())
            self.min_fps_slider.set(self.min_fps_value.get())
            self.min_fps_label.configure(text=f"Min FPS: {self.min_fps_value.get()}")
        self._queue_screen_sync_settings_save()

    def _on_min_fps_change(self, value):
        self.min_fps_value.set(int(float(value)))
        if self.min_fps_value.get() > self.fps_value.get():
            self.min_fps_value.set(self.fps_value.get())
            self.min_fps_slider.set(self.min_fps_value.get())
        self.min_fps_label.configure(text=f"Min FPS: {self.min_fps_value.get()}")
        self._queue_screen_sync_settings_save()

    def _on_smoothing_change(self, value):
        self.smoothing_value.set(float(value))
        self.smoothing_label.configure(text=f"Smoothing: {self.smoothing_value.get():.2f}")
        self._queue_screen_sync_settings_save()

    def _on_boost_change(self, value):
        self.boost_value.set(float(value))
        self.boost_label.configure(text=f"Color Boost: {self.boost_value.get():.2f}x")
        self._queue_screen_sync_settings_save()

    def _on_min_brightness_change(self, value):
        self.min_brightness_value.set(int(float(value)))
        self.min_brightness_label.configure(text=f"Min Brightness: {self.min_brightness_value.get()}")
        self._queue_screen_sync_settings_save()

    def _on_layout_change(self, _ip: str):
        self._queue_screen_sync_settings_save()

    def _set_status(self, msg: str):
        self.status_label.configure(text=msg)

    def _format_debug_color(self, color: Optional[tuple[int, int, int]]) -> str:
        if color is None:
            return "-"
        return f"RGB({color[0]}, {color[1]}, {color[2]})"

    def _refresh_screen_sync_debug(self):
        self._screen_sync_debug_id = None
        if not self.screen_sync or not self.screen_sync.is_running:
            self.screen_sync_debug_runtime.set("Runtime: idle")
            self.screen_sync_debug_target.set("Target: -")
            self.screen_sync_debug_output.set("Output: -")
            self.screen_sync_debug_perf.set("FPS: -")
            self.screen_sync_debug_cadence.set("Cadence: -")
            self.screen_sync_debug_motion.set("Motion: -")
            self.screen_sync_debug_error.set("")
            self.screen_sync_debug_preview.configure(fg_color="#000000")
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

        self.screen_sync_debug_runtime.set(
            f"Runtime: {snapshot['capture_method'].upper()} | {snapshot['mode']}"
        )
        self.screen_sync_debug_target.set(f"Target: {self._format_debug_color(target)}")
        self.screen_sync_debug_output.set(f"Output: {self._format_debug_color(output)}")
        self.screen_sync_debug_perf.set(
            f"FPS: {snapshot['current_fps']} | frame {snapshot['average_frame_time_ms']:.1f} ms"
        )
        if snapshot["send_rate_hz"] > 0:
            self.screen_sync_debug_cadence.set(
                f"Cadence: {snapshot['send_rate_hz']:.1f} Hz | {snapshot['send_interval_ms']:.0f} ms | sends {snapshot['updates_sent']}"
            )
        else:
            self.screen_sync_debug_cadence.set(
                f"Cadence: warming up | sends {snapshot['updates_sent']}"
            )
        self.screen_sync_debug_motion.set(
            f"Motion: {snapshot['motion_score']:.3f} | smooth {snapshot['smoothing_factor']:.2f} | predict {snapshot['prediction_weight']:.2f}"
        )
        self.screen_sync_debug_error.set(
            f"Error: {snapshot['last_error']}" if snapshot["last_error"] else ""
        )
        if output is not None:
            self.screen_sync_debug_preview.configure(
                fg_color=f"#{output[0]:02x}{output[1]:02x}{output[2]:02x}"
            )

        self._screen_sync_debug_id = self.root.after(350, self._refresh_screen_sync_debug)

    def _update_bulb_status(self):
        if self.config.bulbs:
            text = "\n".join([f"- {bulb.name} ({bulb.ip})" for bulb in self.config.bulbs])
            self.bulb_info.configure(text=text, text_color=("gray20", "gray80"))
        else:
            self.bulb_info.configure(text="No bulbs found", text_color="gray")
        self._refresh_screen_sync_layout_controls()

    def _discover_bulbs(self):
        self._set_status("Discovering...")

        async def discover():
            try:
                bulbs = await self.controller.discover()
                for index, bulb in enumerate(bulbs, 1):
                    self.config.add_bulb(bulb["ip"], f"Bulb {index}", bulb["mac"])
                self.root.after(0, self._update_bulb_status)
                self.root.after(0, lambda: self._set_status(f"Found {len(bulbs)} bulb(s)"))
            except Exception as exc:
                self.root.after(0, lambda: self._set_status(f"Error: {exc}"))

        self._run_async(discover())

    def _remove_stale_bulbs(self):
        configured_ips = self._get_bulb_ips()
        if not configured_ips:
            self._set_status("No bulbs configured")
            return

        self._set_status("Checking stale bulbs...")

        async def prune():
            try:
                stale_ips = await self.controller.find_stale_bulbs(configured_ips)
                removed = self.config.remove_bulbs(stale_ips)
                self.root.after(0, self._update_bulb_status)
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
        self.brightness_label.configure(text=f"Brightness: {int((brightness / 255) * 100)}%")
        if self._brightness_debounce_id:
            self.root.after_cancel(self._brightness_debounce_id)

        def send():
            ips = self._get_bulb_ips()
            if ips:
                self._run_async(self.controller.turn_on_all(ips, brightness))

        self._brightness_debounce_id = self.root.after(100, send)

    def _pick_color(self):
        def on_color(r, g, b):
            # Animate color preview transition
            self.animate_color_preview(self.color_preview, (r, g, b))
            ips = self._get_bulb_ips()
            if ips:
                self._run_async(self.controller.set_rgb_all(ips, r, g, b))
                self._set_status(f"Color: RGB({r}, {g}, {b})")

        # Use new color wheel picker
        try:
            # Get current color from preview
            current = self.color_preview.cget("fg_color")
            if isinstance(current, str) and current.startswith("#"):
                r = int(current[1:3], 16)
                g = int(current[3:5], 16)
                b = int(current[5:7], 16)
                initial = (r, g, b)
            else:
                initial = None
        except Exception:
            initial = None
        
        ColorWheelPicker(self.root, on_color, initial_color=initial)

    def _apply_preset_from_menu(self, label: str):
        if label.lower() in PRESETS:
            self._apply_preset(label.lower())

    def _apply_preset(self, preset_name: str):
        ips = self._get_bulb_ips()
        if ips:
            self._run_async(apply_preset(self.controller, ips, preset_name))
            self._set_status(f"Preset: {preset_name}")

    def _on_temp_change(self, value):
        kelvin = int(float(value))
        self.temp_label.configure(text=f"Color Temp: {kelvin}K")
        if self._temp_debounce_id:
            self.root.after_cancel(self._temp_debounce_id)

        def send():
            ips = self._get_bulb_ips()
            if ips:
                self._run_async(self.controller.set_color_temp_all(ips, kelvin))

        self._temp_debounce_id = self.root.after(100, send)

    def _start_screen_sync(self):
        configured_ips = self._get_bulb_ips()
        try:
            ips = self._async_runner.run(
                self.controller.refresh_screen_sync_targets(configured_ips),
                timeout=6.0,
            )
        except Exception as exc:
            self.screen_sync_switch.deselect()
            self._set_status(f"Sync preflight failed: {exc}")
            return
        if not ips:
            self.screen_sync_switch.deselect()
            self.config.screen_sync.enabled = False
            self.config.save()
            self._set_status("No reachable bulbs available for screen sync")
            return

        self._save_screen_sync_settings()
        settings = self.config.screen_sync
        active_regions = resolve_active_regions(settings.bulb_layout, ips)
        mode = effective_screen_sync_mode(settings.mode, active_regions)

        def on_color_change(colors_by_target):
            bulb_colors = build_bulb_color_map(ips, colors_by_target, settings.mode, settings.bulb_layout)
            if bulb_colors:
                self._run_async(self.controller.set_screen_sync_map(bulb_colors))
            preview = average_colors(tuple(colors_by_target.values()))
            self.root.after(
                0,
                lambda: self.color_preview.configure(
                    fg_color=f"#{preview[0]:02x}{preview[1]:02x}{preview[2]:02x}"
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
                f"Screen sync ON ({len(active_regions)} zones, {runtime}{', skipped ' + str(skipped) + ' stale bulb(s)' if skipped else ''})"
            )
        elif settings.mode == "zones":
            self._set_status(
                f"Screen sync ON (single fallback until 2+ zones are assigned, {runtime}{', skipped ' + str(skipped) + ' stale bulb(s)' if skipped else ''})"
            )
        else:
            self._set_status(
                f"Screen sync ON ({runtime}{', skipped ' + str(skipped) + ' stale bulb(s)' if skipped else ''})"
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
            self._set_status("Screen sync OFF")

    def _toggle_screen_sync(self):
        self.config.screen_sync.enabled = bool(self.screen_sync_switch.get())
        self.config.save()
        if self.screen_sync_switch.get():
            self._start_screen_sync()
        else:
            self._stop_screen_sync()

    def _toggle_clap_detection(self):
        self.config.clap.enabled = bool(self.clap_switch.get())
        self.config.save()
        if self.clap_switch.get():
            ips = self._get_bulb_ips()
            if not ips:
                self.clap_switch.deselect()
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
            self._set_status(f"Clap detection ON ({mode})")
        else:
            if self.clap_detector:
                self.clap_detector.stop()
                self.clap_detector = None
            self._set_status("Clap detection OFF")
    
    # === View Switching ===
    
    def _switch_view(self, view: str):
        """Switch between controls and dashboard views."""
        if view == self._current_view:
            return
        
        self._current_view = view
        
        if view == "controls":
            self._dashboard_frame.pack_forget()
            self._controls_frame.pack(fill="both", expand=True)
            self._controls_btn.configure(fg_color=("#3B8ED0", "#1F6AA5"))
            self._dashboard_btn.configure(fg_color="transparent")
            if self._dashboard:
                self._dashboard.stop_auto_refresh()
        else:
            self._controls_frame.pack_forget()
            self._dashboard_frame.pack(fill="both", expand=True)
            self._controls_btn.configure(fg_color="transparent")
            self._dashboard_btn.configure(fg_color=("#3B8ED0", "#1F6AA5"))
            self._update_dashboard()
    
    def _update_dashboard(self):
        """Update dashboard with current bulbs."""
        if self._dashboard:
            bulbs = [
                BulbInfo(ip=b.ip, name=b.name)
                for b in self.config.bulbs
            ]
            self._dashboard.set_bulbs(bulbs)
            self._dashboard.start_auto_refresh(interval_ms=5000)
    
    def _on_bulb_select(self, ip: str):
        """Handle bulb selection from dashboard."""
        # Switch to controls view and scroll to bulb
        self._switch_view("controls")
        self._set_status(f"Selected: {ip}")
    
    # === System Tray ===
    
    def _setup_system_tray(self):
        """Initialize system tray if available."""
        if not is_tray_available():
            return
        
        try:
            self._tray = SystemTrayManager(
                on_show=self._show_from_tray,
                on_quit=self._quit_app,
                on_toggle=self._toggle,
                on_turn_on=self._turn_on,
                on_turn_off=self._turn_off,
                on_preset=self._apply_preset,
                presets=list(PRESETS.keys()),
            )
            self._tray.start()
        except Exception as e:
            print(f"System tray init failed: {e}")
            self._tray = None
    
    def _minimize_to_tray(self):
        """Minimize window to system tray."""
        if self._tray and self._tray.is_running:
            self._minimized_to_tray = True
            self.root.withdraw()
            self._tray.show_notification("WizLight", "Minimized to tray. Click icon to restore.")
    
    def _show_from_tray(self):
        """Restore window from system tray."""
        self._minimized_to_tray = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
    
    def _quit_app(self):
        """Quit application from tray."""
        self._minimized_to_tray = False
        self._on_close()

    def _on_close(self):
        # If minimized to tray, just hide
        if self._tray and self._tray.is_running and self._minimized_to_tray:
            return
        
        # Stop tray
        if self._tray:
            self._tray.stop()
        
        # Stop dashboard refresh
        if self._dashboard:
            self._dashboard.stop_auto_refresh()
        
        if self.screen_sync:
            self.screen_sync.stop()
        if self.clap_detector:
            self.clap_detector.stop()
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
            gc.collect()
            self._async_runner.shutdown()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = WizLightModernGUI()
    app.run()


if __name__ == "__main__":
    main()
