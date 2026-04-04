import json

from src.core.config import Config


def test_load_creates_default_config_file(tmp_path):
    config_path = tmp_path / "wizlight" / "config.json"

    config = Config.load(config_path)

    assert config._config_path == config_path
    assert config_path.exists()
    assert json.loads(config_path.read_text())["bulbs"] == []


def test_add_and_remove_bulbs_persist_to_disk(tmp_path):
    config_path = tmp_path / "wizlight" / "config.json"
    config = Config.load(config_path)

    config.add_bulb("192.168.1.10", "Desk", "AA:BB")
    config.add_bulb("192.168.1.10", "Desk Updated", "AA:BB")

    reloaded = Config.load(config_path)
    assert [(bulb.ip, bulb.name, bulb.mac) for bulb in reloaded.bulbs] == [
        ("192.168.1.10", "Desk Updated", "AA:BB")
    ]

    assert reloaded.remove_bulb("192.168.1.10") is True
    assert Config.load(config_path).bulbs == []


def test_screen_sync_settings_persist_to_disk(tmp_path):
    config_path = tmp_path / "wizlight" / "config.json"
    config = Config.load(config_path)

    config.add_bulb("192.168.1.10", "Left")
    config.add_bulb("192.168.1.11", "Right")
    config.screen_sync.mode = "zones"
    config.screen_sync.monitor = 2
    config.screen_sync.fps = 18
    config.screen_sync.smoothing = 0.4
    config.screen_sync.color_boost = 1.3
    config.screen_sync.min_brightness = 32
    config.screen_sync.bulb_layout = {
        "192.168.1.10": "left",
        "192.168.1.11": "right",
    }
    config.save()

    reloaded = Config.load(config_path)

    assert reloaded.screen_sync.mode == "zones"
    assert reloaded.screen_sync.monitor == 2
    assert reloaded.screen_sync.fps == 18
    assert reloaded.screen_sync.smoothing == 0.4
    assert reloaded.screen_sync.color_boost == 1.3
    assert reloaded.screen_sync.min_brightness == 32
    assert reloaded.screen_sync.bulb_layout == {
        "192.168.1.10": "left",
        "192.168.1.11": "right",
    }


def test_load_old_config_applies_new_screen_sync_defaults(tmp_path):
    config_path = tmp_path / "wizlight" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "bulbs": [],
                "screen_sync": {
                    "fps": 10,
                    "monitor": 0,
                    "smoothing": 0.5,
                },
                "clap": {},
            }
        )
    )

    config = Config.load(config_path)

    assert config.screen_sync.mode == "single"
    assert config.screen_sync.ignore_letterbox is True
    assert config.screen_sync.color_boost == 1.15
    assert config.screen_sync.bulb_layout == {}
