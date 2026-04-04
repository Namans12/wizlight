"""HSV Color Wheel Picker for WizLight GUI."""

import math
from typing import Callable, Optional, Tuple

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk


def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """Convert HSV (0-360, 0-1, 0-1) to RGB (0-255, 0-255, 0-255)."""
    h = h % 360
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """Convert RGB (0-255) to HSV (0-360, 0-1, 0-1)."""
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c
    
    v = max_c
    s = 0 if max_c == 0 else diff / max_c
    
    if diff == 0:
        h = 0
    elif max_c == r:
        h = (60 * ((g - b) / diff) + 360) % 360
    elif max_c == g:
        h = (60 * ((b - r) / diff) + 120) % 360
    else:
        h = (60 * ((r - g) / diff) + 240) % 360
    
    return (h, s, v)


def create_color_wheel_image(size: int = 200, inner_radius_ratio: float = 0.0) -> Image.Image:
    """
    Create a color wheel image.
    
    Args:
        size: Diameter of the wheel in pixels
        inner_radius_ratio: Ratio of inner hole (0 = solid, 0.5 = donut)
    
    Returns:
        PIL Image with RGBA color wheel
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    center = size // 2
    radius = center - 2
    inner_radius = int(radius * inner_radius_ratio)
    
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            distance = math.sqrt(dx * dx + dy * dy)
            
            if inner_radius <= distance <= radius:
                # Calculate hue from angle
                angle = math.atan2(dy, dx)
                hue = (math.degrees(angle) + 180) % 360
                
                # Saturation based on distance from center
                if inner_radius_ratio > 0:
                    sat = 1.0
                else:
                    sat = distance / radius
                
                r, g, b = hsv_to_rgb(hue, sat, 1.0)
                
                # Anti-aliasing at edges
                alpha = 255
                if distance > radius - 1:
                    alpha = int(255 * (radius - distance + 1))
                elif distance < inner_radius + 1 and inner_radius > 0:
                    alpha = int(255 * (distance - inner_radius + 1))
                
                image.putpixel((x, y), (r, g, b, max(0, min(255, alpha))))
    
    return image


def create_value_bar_image(width: int = 20, height: int = 200, hue: float = 0, saturation: float = 1.0) -> Image.Image:
    """Create a vertical value/brightness bar for the current hue."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    for y in range(height):
        value = 1.0 - (y / height)
        r, g, b = hsv_to_rgb(hue, saturation, value)
        draw.line([(2, y), (width - 2, y)], fill=(r, g, b, 255))
    
    return image


class ColorWheelPicker(ctk.CTkToplevel):
    """
    Modern HSV color wheel picker with:
    - Circular hue/saturation wheel
    - Vertical value slider
    - RGB input fields
    - Live preview
    """
    
    def __init__(
        self,
        parent,
        callback: Callable[[int, int, int], None],
        initial_color: Optional[Tuple[int, int, int]] = None,
    ):
        super().__init__(parent)
        self.callback = callback
        self.title("Color Picker")
        self.geometry("380x480")
        self.resizable(False, False)
        
        # HSV state
        if initial_color:
            self._h, self._s, self._v = rgb_to_hsv(*initial_color)
        else:
            self._h, self._s, self._v = 0.0, 1.0, 1.0
        
        # Wheel size
        self._wheel_size = 220
        self._wheel_image: Optional[ImageTk.PhotoImage] = None
        self._value_bar_image: Optional[ImageTk.PhotoImage] = None
        
        self._build_ui()
        self._update_all()
        
        # Center on parent
        self.transient(parent)
        self.grab_set()
    
    def _build_ui(self):
        # Main container
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=16)
        
        # Top row: wheel + value bar
        top_row = ctk.CTkFrame(main, fg_color="transparent")
        top_row.pack(fill="x", pady=(0, 16))
        
        # Color wheel canvas
        self._wheel_canvas = ctk.CTkCanvas(
            top_row,
            width=self._wheel_size,
            height=self._wheel_size,
            bg=self._get_bg_color(),
            highlightthickness=0,
        )
        self._wheel_canvas.pack(side="left", padx=(0, 16))
        self._wheel_canvas.bind("<Button-1>", self._on_wheel_click)
        self._wheel_canvas.bind("<B1-Motion>", self._on_wheel_click)
        
        # Value bar canvas
        self._value_canvas = ctk.CTkCanvas(
            top_row,
            width=30,
            height=self._wheel_size,
            bg=self._get_bg_color(),
            highlightthickness=0,
        )
        self._value_canvas.pack(side="left")
        self._value_canvas.bind("<Button-1>", self._on_value_click)
        self._value_canvas.bind("<B1-Motion>", self._on_value_click)
        
        # Preview frame
        preview_row = ctk.CTkFrame(main, fg_color="transparent")
        preview_row.pack(fill="x", pady=(0, 16))
        
        ctk.CTkLabel(preview_row, text="Preview:", font=("Segoe UI", 13)).pack(side="left")
        self._preview = ctk.CTkFrame(preview_row, width=80, height=40, corner_radius=8)
        self._preview.pack(side="left", padx=(8, 16))
        self._preview.pack_propagate(False)
        
        self._hex_label = ctk.CTkLabel(preview_row, text="#FFFFFF", font=("Consolas", 14))
        self._hex_label.pack(side="left")
        
        # RGB sliders row
        rgb_frame = ctk.CTkFrame(main, fg_color="transparent")
        rgb_frame.pack(fill="x", pady=(0, 16))
        
        self._r_var = ctk.IntVar(value=255)
        self._g_var = ctk.IntVar(value=255)
        self._b_var = ctk.IntVar(value=255)
        
        for label, var, color in [("R", self._r_var, "#FF6B6B"), ("G", self._g_var, "#51CF66"), ("B", self._b_var, "#339AF0")]:
            row = ctk.CTkFrame(rgb_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, font=("Segoe UI", 12, "bold"), text_color=color, width=20).pack(side="left")
            slider = ctk.CTkSlider(row, from_=0, to=255, variable=var, command=self._on_rgb_slider_change)
            slider.pack(side="left", fill="x", expand=True, padx=8)
            entry = ctk.CTkEntry(row, textvariable=var, width=50, justify="center")
            entry.pack(side="left")
            entry.bind("<Return>", self._on_rgb_entry_change)
            entry.bind("<FocusOut>", self._on_rgb_entry_change)
        
        # Buttons
        btn_row = ctk.CTkFrame(main, fg_color="transparent")
        btn_row.pack(fill="x")
        
        ctk.CTkButton(btn_row, text="Cancel", command=self.destroy, fg_color="gray").pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Apply", command=self._apply).pack(side="left", fill="x", expand=True)
    
    def _get_bg_color(self) -> str:
        """Get background color matching theme."""
        return "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#f0f0f0"
    
    def _on_wheel_click(self, event):
        """Handle click/drag on color wheel."""
        center = self._wheel_size // 2
        dx = event.x - center
        dy = event.y - center
        distance = math.sqrt(dx * dx + dy * dy)
        radius = center - 2
        
        if distance <= radius:
            # Calculate hue from angle
            angle = math.atan2(dy, dx)
            self._h = (math.degrees(angle) + 180) % 360
            
            # Calculate saturation from distance
            self._s = min(1.0, distance / radius)
            
            self._update_from_hsv()
    
    def _on_value_click(self, event):
        """Handle click/drag on value bar."""
        self._v = max(0.0, min(1.0, 1.0 - event.y / self._wheel_size))
        self._update_from_hsv()
    
    def _on_rgb_slider_change(self, _):
        """Handle RGB slider change."""
        r, g, b = self._r_var.get(), self._g_var.get(), self._b_var.get()
        self._h, self._s, self._v = rgb_to_hsv(r, g, b)
        self._update_preview()
        self._update_wheel_indicator()
        self._update_value_bar()
    
    def _on_rgb_entry_change(self, _):
        """Handle RGB entry change."""
        try:
            r = max(0, min(255, int(self._r_var.get())))
            g = max(0, min(255, int(self._g_var.get())))
            b = max(0, min(255, int(self._b_var.get())))
            self._r_var.set(r)
            self._g_var.set(g)
            self._b_var.set(b)
            self._h, self._s, self._v = rgb_to_hsv(r, g, b)
            self._update_preview()
            self._update_wheel_indicator()
            self._update_value_bar()
        except ValueError:
            pass
    
    def _update_from_hsv(self):
        """Update RGB vars from HSV state."""
        r, g, b = hsv_to_rgb(self._h, self._s, self._v)
        self._r_var.set(r)
        self._g_var.set(g)
        self._b_var.set(b)
        self._update_preview()
        self._update_wheel_indicator()
        self._update_value_bar()
    
    def _update_all(self):
        """Full refresh of all UI elements."""
        self._draw_wheel()
        self._update_value_bar()
        self._update_from_hsv()
    
    def _draw_wheel(self):
        """Draw the color wheel."""
        wheel_img = create_color_wheel_image(self._wheel_size)
        self._wheel_image = ImageTk.PhotoImage(wheel_img)
        self._wheel_canvas.delete("all")
        self._wheel_canvas.create_image(0, 0, anchor="nw", image=self._wheel_image)
        self._update_wheel_indicator()
    
    def _update_wheel_indicator(self):
        """Update the position indicator on the wheel."""
        self._wheel_canvas.delete("indicator")
        
        center = self._wheel_size // 2
        radius = (center - 2) * self._s
        angle = math.radians(self._h - 180)
        
        x = center + radius * math.cos(angle)
        y = center + radius * math.sin(angle)
        
        # Draw crosshair indicator
        size = 8
        self._wheel_canvas.create_oval(
            x - size, y - size, x + size, y + size,
            outline="white", width=2, tags="indicator"
        )
        self._wheel_canvas.create_oval(
            x - size - 1, y - size - 1, x + size + 1, y + size + 1,
            outline="black", width=1, tags="indicator"
        )
    
    def _update_value_bar(self):
        """Update the value/brightness bar."""
        bar_img = create_value_bar_image(30, self._wheel_size, self._h, self._s)
        self._value_bar_image = ImageTk.PhotoImage(bar_img)
        self._value_canvas.delete("all")
        self._value_canvas.create_image(0, 0, anchor="nw", image=self._value_bar_image)
        
        # Draw indicator
        y = int((1.0 - self._v) * self._wheel_size)
        self._value_canvas.create_polygon(
            0, y, 8, y - 5, 8, y + 5,
            fill="white", outline="black", tags="indicator"
        )
        self._value_canvas.create_polygon(
            30, y, 22, y - 5, 22, y + 5,
            fill="white", outline="black", tags="indicator"
        )
    
    def _update_preview(self):
        """Update the color preview."""
        r, g, b = self._r_var.get(), self._g_var.get(), self._b_var.get()
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        self._preview.configure(fg_color=hex_color)
        self._hex_label.configure(text=hex_color.upper())
    
    def _apply(self):
        """Apply the selected color and close."""
        r, g, b = self._r_var.get(), self._g_var.get(), self._b_var.get()
        self.callback(r, g, b)
        self.destroy()
