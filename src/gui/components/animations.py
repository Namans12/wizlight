"""Animation utilities for smooth GUI transitions."""

from typing import Callable, Optional, Any
import customtkinter as ctk


def ease_out_cubic(t: float) -> float:
    """Cubic ease-out function for smooth deceleration."""
    return 1 - pow(1 - t, 3)


def ease_in_out_quad(t: float) -> float:
    """Quadratic ease-in-out for smooth acceleration and deceleration."""
    if t < 0.5:
        return 2 * t * t
    return 1 - pow(-2 * t + 2, 2) / 2


def animate_value(
    widget: ctk.CTkBaseClass,
    start: float,
    end: float,
    duration_ms: int,
    on_update: Callable[[float], None],
    on_complete: Optional[Callable[[], None]] = None,
    easing: Callable[[float], float] = ease_out_cubic,
) -> str:
    """
    Animate a value from start to end over duration_ms milliseconds.
    
    Args:
        widget: Widget to use for after() scheduling
        start: Starting value
        end: Ending value
        duration_ms: Animation duration in milliseconds
        on_update: Callback with current value each frame
        on_complete: Optional callback when animation finishes
        easing: Easing function (default: ease_out_cubic)
    
    Returns:
        Animation ID that can be used with after_cancel()
    """
    steps = max(1, duration_ms // 16)  # ~60fps
    step_time = duration_ms // steps
    current_step = [0]
    animation_id = [None]
    
    def step():
        current_step[0] += 1
        progress = min(1.0, current_step[0] / steps)
        eased = easing(progress)
        value = start + (end - start) * eased
        
        on_update(value)
        
        if current_step[0] < steps:
            animation_id[0] = widget.after(step_time, step)
        elif on_complete:
            on_complete()
    
    animation_id[0] = widget.after(step_time, step)
    return animation_id[0]


def fade_widget(
    widget: ctk.CTkBaseClass,
    fade_in: bool = True,
    duration_ms: int = 200,
    on_complete: Optional[Callable[[], None]] = None,
) -> Optional[str]:
    """
    Fade a widget in or out by adjusting its fg_color alpha.
    
    Note: CustomTkinter doesn't support true alpha, so this simulates
    fade by transitioning between transparent and the target color.
    """
    # For widgets that support fg_color modification
    if not hasattr(widget, "configure"):
        return None
    
    try:
        # Get current color
        current_fg = widget.cget("fg_color")
        if isinstance(current_fg, tuple):
            current_fg = current_fg[1] if ctk.get_appearance_mode() == "Dark" else current_fg[0]
    except Exception:
        return None
    
    # Simulate fade by enabling/disabling with slight delay
    if fade_in:
        widget.configure(state="normal")
        if on_complete:
            widget.after(duration_ms, on_complete)
    else:
        widget.configure(state="disabled")
        if on_complete:
            widget.after(duration_ms, on_complete)
    
    return None


class AnimationMixin:
    """Mixin class to add animation capabilities to GUI classes."""
    
    _active_animations: dict[str, str]
    
    def __init__(self):
        self._active_animations = {}
    
    def animate_brightness(
        self,
        slider: ctk.CTkSlider,
        label: ctk.CTkLabel,
        target: int,
        duration_ms: int = 300,
    ) -> None:
        """Animate brightness slider to target value."""
        # Cancel existing animation
        if "brightness" in self._active_animations:
            try:
                slider.after_cancel(self._active_animations["brightness"])
            except Exception:
                pass
        
        start = slider.get()
        
        def update(value: float):
            slider.set(value)
            pct = int((value / 255) * 100)
            label.configure(text=f"Brightness: {pct}%")
        
        self._active_animations["brightness"] = animate_value(
            slider, start, target, duration_ms, update
        )
    
    def animate_color_preview(
        self,
        preview: ctk.CTkFrame,
        target_color: tuple[int, int, int],
        duration_ms: int = 250,
    ) -> None:
        """Animate color preview frame to target RGB color."""
        if "color" in self._active_animations:
            try:
                preview.after_cancel(self._active_animations["color"])
            except Exception:
                pass
        
        # Get current color from fg_color
        try:
            current = preview.cget("fg_color")
            if isinstance(current, str) and current.startswith("#"):
                r = int(current[1:3], 16)
                g = int(current[3:5], 16)
                b = int(current[5:7], 16)
                start_color = (r, g, b)
            else:
                start_color = target_color
        except Exception:
            start_color = target_color
        
        def update(progress: float):
            r = int(start_color[0] + (target_color[0] - start_color[0]) * progress)
            g = int(start_color[1] + (target_color[1] - start_color[1]) * progress)
            b = int(start_color[2] + (target_color[2] - start_color[2]) * progress)
            preview.configure(fg_color=f"#{r:02x}{g:02x}{b:02x}")
        
        steps = max(1, duration_ms // 16)
        step_time = duration_ms // steps
        current_step = [0]
        
        def step():
            current_step[0] += 1
            progress = ease_out_cubic(min(1.0, current_step[0] / steps))
            update(progress)
            if current_step[0] < steps:
                self._active_animations["color"] = preview.after(step_time, step)
        
        self._active_animations["color"] = preview.after(step_time, step)
    
    def pulse_widget(
        self,
        widget: ctk.CTkBaseClass,
        color: str = "#4CAF50",
        duration_ms: int = 400,
    ) -> None:
        """Briefly pulse a widget's border color to indicate action."""
        try:
            original = widget.cget("border_color")
        except Exception:
            return
        
        widget.configure(border_color=color, border_width=2)
        
        def restore():
            try:
                widget.configure(border_color=original, border_width=0)
            except Exception:
                pass
        
        widget.after(duration_ms, restore)
    
    def animate_status(
        self,
        label: ctk.CTkLabel,
        message: str,
        color: str = "gray",
        fade_after_ms: int = 3000,
    ) -> None:
        """Show status message with fade-out after delay."""
        if "status" in self._active_animations:
            try:
                label.after_cancel(self._active_animations["status"])
            except Exception:
                pass
        
        label.configure(text=message, text_color=color)
        
        def fade():
            label.configure(text_color="gray")
        
        if fade_after_ms > 0:
            self._active_animations["status"] = label.after(fade_after_ms, fade)
