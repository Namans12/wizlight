import pytest

from src.core.bulb_controller import BulbController, PRESETS, apply_preset


class RecordingController:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def set_rgb_all(self, ips, r, g, b, brightness=None):
        self.calls.append(("rgb", ips, r, g, b, brightness))

    async def set_color_temp_all(self, ips, kelvin, brightness=None):
        self.calls.append(("temp", ips, kelvin, brightness))


@pytest.mark.asyncio
async def test_apply_preset_uses_rgb_settings():
    controller = RecordingController()
    ips = ["192.168.1.10"]

    success = await apply_preset(controller, ips, "party")

    assert success is True
    assert controller.calls == [
        ("rgb", ips, *PRESETS["party"]["rgb"], PRESETS["party"].get("brightness"))
    ]


@pytest.mark.asyncio
async def test_apply_preset_uses_color_temperature_settings():
    controller = RecordingController()
    ips = ["192.168.1.10", "192.168.1.11"]

    success = await apply_preset(controller, ips, "reading")

    assert success is True
    assert controller.calls == [
        (
            "temp",
            ips,
            PRESETS["reading"]["color_temp"],
            PRESETS["reading"].get("brightness"),
        )
    ]


@pytest.mark.asyncio
async def test_apply_preset_returns_false_for_unknown_preset():
    controller = RecordingController()

    success = await apply_preset(controller, ["192.168.1.10"], "unknown")

    assert success is False
    assert controller.calls == []


@pytest.mark.asyncio
async def test_close_async_closes_cached_bulbs():
    class FakeBulb:
        def __init__(self) -> None:
            self.closed = False

        async def async_close(self):
            self.closed = True

    controller = BulbController()
    bulb = FakeBulb()
    controller._bulbs["192.168.1.10"] = bulb

    await controller.close_async()

    assert bulb.closed is True
    assert controller._bulbs == {}
