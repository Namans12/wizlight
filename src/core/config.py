"""Configuration management for WizLight."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


SCREEN_SYNC_MODES = ("single", "zones")
SCREEN_SYNC_REGIONS = (
    "left",
    "center",
    "right",
    "top-left",
    "top",
    "top-right",
    "bottom-left",
    "bottom",
    "bottom-right",
)


@dataclass
class BulbConfig:
    """Configuration for a single WiZ bulb."""

    ip: str
    name: str
    mac: Optional[str] = None


COLOR_ALGORITHMS = ("weighted", "kmeans", "histogram")


@dataclass
class ScreenSyncConfig:
    """Screen sync feature configuration."""

    enabled: bool = False
    mode: str = "single"
    fps: int = 12
    monitor: int = 1
    smoothing: float = 0.25
    sample_size: int = 48
    ignore_letterbox: bool = True
    edge_weight: float = 1.35
    color_boost: float = 1.15
    min_brightness: int = 28
    min_color_delta: int = 12
    bulb_layout: dict[str, str] = field(default_factory=dict)
    
    # V2 optimization settings
    use_gpu: bool = True
    adaptive_fps: bool = True
    min_fps: int = 8
    max_fps: int = 30
    color_algorithm: str = "weighted"
    predictive_smoothing: bool = True

    def __post_init__(self) -> None:
        self.mode = self.mode if self.mode in SCREEN_SYNC_MODES else "single"
        self.fps = max(4, min(30, int(self.fps)))
        self.monitor = int(self.monitor)
        self.smoothing = max(0.05, min(1.0, float(self.smoothing)))
        self.sample_size = max(16, min(96, int(self.sample_size)))
        self.edge_weight = max(1.0, min(2.5, float(self.edge_weight)))
        self.color_boost = max(1.0, min(2.0, float(self.color_boost)))
        self.min_brightness = max(0, min(255, int(self.min_brightness)))
        self.min_color_delta = max(1, min(255, int(self.min_color_delta)))
        self.bulb_layout = {
            ip: region
            for ip, region in dict(self.bulb_layout).items()
            if region in SCREEN_SYNC_REGIONS
        }
        # V2 validation
        self.min_fps = max(4, min(30, int(self.min_fps)))
        self.max_fps = max(self.min_fps, min(60, int(self.max_fps)))
        self.color_algorithm = self.color_algorithm if self.color_algorithm in COLOR_ALGORITHMS else "weighted"


@dataclass
class ClapConfig:
    """Clap detection feature configuration."""

    enabled: bool = False
    threshold: float = 0.5
    cooldown: float = 1.0
    double_clap: bool = True


@dataclass
class Config:
    """Main application configuration."""

    bulbs: list[BulbConfig] = field(default_factory=list)
    screen_sync: ScreenSyncConfig = field(default_factory=ScreenSyncConfig)
    clap: ClapConfig = field(default_factory=ClapConfig)
    default_brightness: int = 128
    default_color_temp: int = 4000
    _config_path: Path = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._config_path is None:
            self._config_path = Path.home() / ".wizlight" / "config.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        """Load configuration from file."""

        config_path = path or Path.home() / ".wizlight" / "config.json"
        if not config_path.exists():
            config = cls(_config_path=config_path)
            config.save()
            return config

        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        bulbs = [BulbConfig(**bulb) for bulb in data.get("bulbs", [])]
        screen_sync = ScreenSyncConfig(**data.get("screen_sync", {}))
        clap = ClapConfig(**data.get("clap", {}))

        return cls(
            bulbs=bulbs,
            screen_sync=screen_sync,
            clap=clap,
            default_brightness=data.get("default_brightness", 128),
            default_color_temp=data.get("default_color_temp", 4000),
            _config_path=config_path,
        )

    def save(self) -> None:
        """Save configuration to file."""

        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "bulbs": [asdict(bulb) for bulb in self.bulbs],
            "screen_sync": asdict(self.screen_sync),
            "clap": asdict(self.clap),
            "default_brightness": self.default_brightness,
            "default_color_temp": self.default_color_temp,
        }

        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_bulb(self, ip: str, name: str, mac: Optional[str] = None) -> None:
        """Add a bulb to configuration."""

        self.bulbs = [bulb for bulb in self.bulbs if bulb.ip != ip]
        self.bulbs.append(BulbConfig(ip=ip, name=name, mac=mac))
        self.save()

    def remove_bulb(self, ip: str) -> bool:
        """Remove a bulb from configuration."""

        original_len = len(self.bulbs)
        self.bulbs = [bulb for bulb in self.bulbs if bulb.ip != ip]
        self.screen_sync.bulb_layout.pop(ip, None)
        if len(self.bulbs) < original_len:
            self.save()
            return True
        return False
