"""Bulb Dashboard with status cards for WizLight GUI."""

from typing import Callable, Optional, List, Dict, Any
from dataclasses import dataclass
from concurrent.futures import Future

import customtkinter as ctk

from ...core.bulb_controller import BulbController, BulbState


@dataclass
class BulbInfo:
    """Display info for a bulb card."""
    ip: str
    name: str
    is_on: bool = False
    brightness: int = 0
    color: Optional[tuple[int, int, int]] = None
    color_temp: Optional[int] = None
    reachable: bool = True


class BulbCard(ctk.CTkFrame):
    """
    Individual bulb status card with:
    - Power state indicator
    - Name and IP
    - Current color/temp display
    - Quick toggle button
    """
    
    def __init__(
        self,
        parent,
        bulb: BulbInfo,
        on_toggle: Callable[[str], None],
        on_click: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent, corner_radius=12)
        self.bulb = bulb
        self.on_toggle = on_toggle
        self.on_click = on_click
        
        self._build_ui()
        self._update_state()
        
        # Make entire card clickable
        if on_click:
            self.bind("<Button-1>", lambda e: on_click(bulb.ip))
            self.configure(cursor="hand2")
    
    def _build_ui(self):
        # Main container with padding
        self.configure(fg_color=("gray90", "gray17"))
        
        # Top row: status indicator + name
        top_row = ctk.CTkFrame(self, fg_color="transparent")
        top_row.pack(fill="x", padx=12, pady=(12, 6))
        
        # Power indicator (circle)
        self._power_indicator = ctk.CTkFrame(
            top_row,
            width=14,
            height=14,
            corner_radius=7,
            fg_color="gray"
        )
        self._power_indicator.pack(side="left", padx=(0, 8))
        self._power_indicator.pack_propagate(False)
        
        # Bulb name
        self._name_label = ctk.CTkLabel(
            top_row,
            text=self.bulb.name,
            font=("Segoe UI", 14, "bold"),
            anchor="w"
        )
        self._name_label.pack(side="left", fill="x", expand=True)
        
        # Toggle button
        self._toggle_btn = ctk.CTkButton(
            top_row,
            text="",
            width=50,
            height=28,
            command=self._on_toggle_click,
            corner_radius=6
        )
        self._toggle_btn.pack(side="right")
        
        # Middle row: IP and status
        mid_row = ctk.CTkFrame(self, fg_color="transparent")
        mid_row.pack(fill="x", padx=12, pady=(0, 6))
        
        self._ip_label = ctk.CTkLabel(
            mid_row,
            text=self.bulb.ip,
            font=("Consolas", 11),
            text_color="gray"
        )
        self._ip_label.pack(side="left")
        
        self._status_label = ctk.CTkLabel(
            mid_row,
            text="",
            font=("Segoe UI", 11),
            text_color="gray"
        )
        self._status_label.pack(side="right")
        
        # Bottom row: color preview + brightness
        bottom_row = ctk.CTkFrame(self, fg_color="transparent")
        bottom_row.pack(fill="x", padx=12, pady=(0, 12))
        
        # Color/temp preview square
        self._color_preview = ctk.CTkFrame(
            bottom_row,
            width=32,
            height=32,
            corner_radius=6,
            fg_color="gray40"
        )
        self._color_preview.pack(side="left", padx=(0, 8))
        self._color_preview.pack_propagate(False)
        
        # Brightness bar background
        bar_bg = ctk.CTkFrame(bottom_row, height=8, corner_radius=4, fg_color="gray30")
        bar_bg.pack(side="left", fill="x", expand=True, pady=12)
        
        # Brightness bar fill
        self._brightness_bar = ctk.CTkFrame(
            bar_bg,
            height=8,
            corner_radius=4,
            fg_color=("#3B8ED0", "#1F6AA5")
        )
        self._brightness_bar.place(relx=0, rely=0, relheight=1, relwidth=0.5)
    
    def _on_toggle_click(self):
        """Handle toggle button click."""
        self.on_toggle(self.bulb.ip)
    
    def _update_state(self):
        """Update UI to reflect current bulb state."""
        if not self.bulb.reachable:
            self._power_indicator.configure(fg_color="gray")
            self._toggle_btn.configure(text="?", fg_color="gray", state="disabled")
            self._status_label.configure(text="Unreachable")
            self._color_preview.configure(fg_color="gray40")
            self._brightness_bar.place(relwidth=0)
            return
        
        # Power indicator
        if self.bulb.is_on:
            self._power_indicator.configure(fg_color="#4CAF50")
            self._toggle_btn.configure(text="ON", fg_color="#4CAF50", state="normal")
        else:
            self._power_indicator.configure(fg_color="gray50")
            self._toggle_btn.configure(text="OFF", fg_color="gray50", state="normal")
        
        # Status text
        if self.bulb.is_on:
            if self.bulb.color:
                r, g, b = self.bulb.color
                self._status_label.configure(text=f"RGB({r}, {g}, {b})")
            elif self.bulb.color_temp:
                self._status_label.configure(text=f"{self.bulb.color_temp}K")
            else:
                self._status_label.configure(text="On")
        else:
            self._status_label.configure(text="Off")
        
        # Color preview
        if self.bulb.is_on and self.bulb.color:
            r, g, b = self.bulb.color
            self._color_preview.configure(fg_color=f"#{r:02x}{g:02x}{b:02x}")
        elif self.bulb.is_on and self.bulb.color_temp:
            # Approximate color temp to RGB for preview
            temp_color = self._kelvin_to_rgb(self.bulb.color_temp)
            self._color_preview.configure(
                fg_color=f"#{temp_color[0]:02x}{temp_color[1]:02x}{temp_color[2]:02x}"
            )
        elif self.bulb.is_on:
            self._color_preview.configure(fg_color="#FFFFEE")
        else:
            self._color_preview.configure(fg_color="gray40")
        
        # Brightness bar
        if self.bulb.is_on and self.bulb.brightness:
            width = self.bulb.brightness / 255
            self._brightness_bar.place(relwidth=width)
        else:
            self._brightness_bar.place(relwidth=0)
    
    def _kelvin_to_rgb(self, kelvin: int) -> tuple[int, int, int]:
        """Approximate color temperature to RGB."""
        temp = kelvin / 100
        
        # Red
        if temp <= 66:
            r = 255
        else:
            r = temp - 60
            r = 329.698727446 * pow(r, -0.1332047592)
            r = max(0, min(255, r))
        
        # Green
        if temp <= 66:
            g = temp
            g = 99.4708025861 * pow(g, 0.5) - 161.1195681661
        else:
            g = temp - 60
            g = 288.1221695283 * pow(g, -0.0755148492)
        g = max(0, min(255, g))
        
        # Blue
        if temp >= 66:
            b = 255
        elif temp <= 19:
            b = 0
        else:
            b = temp - 10
            b = 138.5177312231 * pow(b, 0.5) - 305.0447927307
            b = max(0, min(255, b))
        
        return (int(r), int(g), int(b))
    
    def update_bulb(self, bulb: BulbInfo):
        """Update with new bulb info."""
        self.bulb = bulb
        self._name_label.configure(text=bulb.name)
        self._ip_label.configure(text=bulb.ip)
        self._update_state()


class BulbDashboard(ctk.CTkFrame):
    """
    Dashboard view showing all bulbs as cards in a grid.
    Provides real-time status updates and quick controls.
    """
    
    def __init__(
        self,
        parent,
        controller: BulbController,
        run_async: Callable,
        on_bulb_select: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent, fg_color="transparent")
        self.controller = controller
        self._run_async = run_async
        self.on_bulb_select = on_bulb_select
        
        self._cards: Dict[str, BulbCard] = {}
        self._bulbs: List[BulbInfo] = []
        self._refresh_id: Optional[str] = None
        self._auto_refresh = True
        self._refresh_interval_ms = 5000
        
        self._build_ui()
    
    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        
        ctk.CTkLabel(
            header,
            text="Bulb Dashboard",
            font=("Segoe UI", 16, "bold")
        ).pack(side="left")
        
        self._refresh_btn = ctk.CTkButton(
            header,
            text="⟳ Refresh",
            width=90,
            command=self.refresh_status,
            fg_color="transparent",
            border_width=1
        )
        self._refresh_btn.pack(side="right")
        
        # Grid container
        self._grid = ctk.CTkFrame(self, fg_color="transparent")
        self._grid.pack(fill="both", expand=True)
        
        # Configure grid columns
        self._grid.columnconfigure(0, weight=1)
        self._grid.columnconfigure(1, weight=1)
        
        # Empty state label
        self._empty_label = ctk.CTkLabel(
            self._grid,
            text="No bulbs configured.\nClick 'Discover' to find bulbs.",
            text_color="gray",
            justify="center"
        )
    
    def set_bulbs(self, bulbs: List[BulbInfo]):
        """Update the dashboard with new bulb list."""
        self._bulbs = bulbs
        self._rebuild_cards()
        
        if self._auto_refresh:
            self.refresh_status()
    
    def _rebuild_cards(self):
        """Rebuild all bulb cards."""
        # Clear existing cards
        for card in self._cards.values():
            card.destroy()
        self._cards.clear()
        
        if not self._bulbs:
            self._empty_label.grid(row=0, column=0, columnspan=2, pady=40)
            return
        
        self._empty_label.grid_forget()
        
        # Create cards in 2-column grid
        for i, bulb in enumerate(self._bulbs):
            row = i // 2
            col = i % 2
            
            card = BulbCard(
                self._grid,
                bulb,
                on_toggle=self._toggle_bulb,
                on_click=self.on_bulb_select
            )
            card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            self._cards[bulb.ip] = card
    
    def _toggle_bulb(self, ip: str):
        """Toggle a specific bulb."""
        async def toggle():
            try:
                new_state = await self.controller.toggle(ip)
                # Update card immediately
                for bulb in self._bulbs:
                    if bulb.ip == ip:
                        bulb.is_on = new_state
                        if ip in self._cards:
                            self._cards[ip].after(0, lambda: self._cards[ip].update_bulb(bulb))
                        break
            except Exception as e:
                print(f"Toggle error: {e}")
        
        self._run_async(toggle())
    
    def refresh_status(self):
        """Refresh status of all bulbs."""
        if not self._bulbs:
            return
        
        async def fetch_states():
            for bulb in self._bulbs:
                try:
                    state = await self.controller.get_state(bulb.ip)
                    bulb.is_on = state.is_on
                    bulb.brightness = state.brightness or 0
                    bulb.color = state.rgb
                    bulb.color_temp = state.color_temp
                    bulb.reachable = True
                except Exception:
                    bulb.reachable = False
                
                # Update card on main thread
                if bulb.ip in self._cards:
                    card = self._cards[bulb.ip]
                    card.after(0, lambda b=bulb, c=card: c.update_bulb(b))
        
        self._run_async(fetch_states())
        
        # Schedule next refresh
        if self._auto_refresh and self._refresh_id is None:
            self._schedule_refresh()
    
    def _schedule_refresh(self):
        """Schedule the next auto-refresh."""
        if self._refresh_id:
            self.after_cancel(self._refresh_id)
        self._refresh_id = self.after(self._refresh_interval_ms, self._auto_refresh_tick)
    
    def _auto_refresh_tick(self):
        """Auto-refresh tick."""
        self._refresh_id = None
        if self._auto_refresh and self.winfo_exists():
            self.refresh_status()
    
    def start_auto_refresh(self, interval_ms: int = 5000):
        """Start automatic status refresh."""
        self._auto_refresh = True
        self._refresh_interval_ms = interval_ms
        self._schedule_refresh()
    
    def stop_auto_refresh(self):
        """Stop automatic status refresh."""
        self._auto_refresh = False
        if self._refresh_id:
            self.after_cancel(self._refresh_id)
            self._refresh_id = None
    
    def destroy(self):
        """Clean up on destroy."""
        self.stop_auto_refresh()
        super().destroy()
