import pytest

from src.core.calibration import BulbCalibrationTable, CalibrationSample
from src.core.bulb_controller import BulbController, BulbState, PRESETS, apply_preset
from src.core.color_mapping import BulbColorProfile, BulbGamutMapper


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


@pytest.mark.asyncio
async def test_set_rgb_exact_uses_direct_rgbw_payload():
    class FakeBulb:
        def __init__(self) -> None:
            self.calls = []

        async def turn_on(self, builder):
            self.calls.append(builder.pilot_params)

    controller = BulbController()
    bulb = FakeBulb()
    controller._bulbs["192.168.1.10"] = bulb

    await controller.set_rgb_exact("192.168.1.10", 97, 138, 156)

    assert bulb.calls == [{"r": 97, "g": 138, "b": 156, "w": 0}]


@pytest.mark.asyncio
async def test_set_screen_sync_payload_uses_direct_rgbw_payload():
    class FakeBulb:
        def __init__(self) -> None:
            self.calls = []

        async def turn_on(self, builder):
            self.calls.append(builder.pilot_params)

    controller = BulbController()
    bulb = FakeBulb()
    controller._bulbs["192.168.1.10"] = bulb

    await controller.set_screen_sync_payload("192.168.1.10", (10, 20, 30, 40))

    assert bulb.calls == [{"r": 10, "g": 20, "b": 30, "w": 40}]


@pytest.mark.asyncio
async def test_set_screen_sync_rgb_turns_off_for_true_black():
    class FakeBulb:
        def __init__(self) -> None:
            self.off_calls = 0
            self.on_calls = []

        async def turn_off(self):
            self.off_calls += 1

        async def turn_on(self, builder):
            self.on_calls.append(builder.pilot_params)

    controller = BulbController()
    bulb = FakeBulb()
    controller._bulbs["192.168.1.10"] = bulb

    await controller.set_screen_sync_rgb("192.168.1.10", 0, 0, 0)

    assert bulb.off_calls == 1
    assert bulb.on_calls == []


def test_bulb_gamut_mapper_keeps_vivid_colors_out_of_white_channel():
    mapper = BulbGamutMapper(
        BulbColorProfile(white_channels=1, white_to_color_ratio=80, rgb_channel_current=(9, 8, 6))
    )

    payload = mapper.map_rgb((12, 130, 255))

    assert payload[3] <= 10
    assert payload[2] >= payload[1] > payload[0]


def test_bulb_gamut_mapper_routes_neutral_color_into_white_channel():
    mapper = BulbGamutMapper(
        BulbColorProfile(white_channels=1, white_to_color_ratio=80, rgb_channel_current=(9, 8, 6))
    )

    payload = mapper.map_rgb((180, 176, 170))

    assert payload[3] >= 120
    assert max(payload[:3]) <= 80


def test_bulb_gamut_mapper_can_chain_tone_lut_before_calibration():
    tone_lut = BulbCalibrationTable(
        key="tone",
        strength=0.22,
        samples=[
            CalibrationSample(target_rgb=(0, 0, 0), measured_rgb=(0, 0, 0)),
            CalibrationSample(target_rgb=(255, 0, 0), measured_rgb=(220, 20, 20)),
            CalibrationSample(target_rgb=(0, 255, 0), measured_rgb=(0, 255, 0)),
            CalibrationSample(target_rgb=(0, 0, 255), measured_rgb=(0, 0, 255)),
            CalibrationSample(target_rgb=(255, 255, 255), measured_rgb=(255, 255, 255)),
        ],
    )
    mapper_plain = BulbGamutMapper(BulbColorProfile())
    mapper_toned = BulbGamutMapper(BulbColorProfile(), tone_lut=tone_lut)

    plain = mapper_plain.map_rgb((220, 20, 20))
    toned = mapper_toned.map_rgb((220, 20, 20))

    assert toned[0] >= plain[0]


@pytest.mark.asyncio
async def test_resolve_screen_sync_targets_skips_duplicates_and_unreachable(monkeypatch):
    controller = BulbController()

    states = {
        "192.168.1.3": BulbState(
            ip="192.168.1.3",
            mac="aa:bb",
            is_on=True,
            brightness=128,
            rgb=(1, 2, 3),
            color_temp=None,
        ),
        "192.168.1.19": BulbState(
            ip="192.168.1.19",
            mac="aa:bb",
            is_on=True,
            brightness=128,
            rgb=(1, 2, 3),
            color_temp=None,
        ),
        "192.168.1.7": BulbState(
            ip="192.168.1.7",
            mac="cc:dd",
            is_on=False,
            brightness=None,
            rgb=None,
            color_temp=None,
        ),
    }

    async def fake_resolve_target(ip):
        if ip == "192.168.1.4":
            raise TimeoutError("timed out")
        return states[ip]

    monkeypatch.setattr(controller, "_resolve_screen_sync_target", fake_resolve_target)

    resolved = await controller.resolve_screen_sync_targets(
        ["192.168.1.3", "192.168.1.19", "192.168.1.4", "192.168.1.7"]
    )

    assert resolved == ["192.168.1.3", "192.168.1.7"]


@pytest.mark.asyncio
async def test_get_color_profile_times_out_slow_metadata_queries(monkeypatch):
    class FakeBulb:
        mac = "aa:bb"

        async def getBulbConfig(self):
            await asyncio.sleep(0.05)
            return {"result": {"moduleName": "slow"}}

        async def getModelConfig(self):
            await asyncio.sleep(0.05)
            return {"result": {"nowc": 2, "wcr": 90}}

        async def getUserConfig(self):
            await asyncio.sleep(0.05)
            return {"result": {"fadeIn": 400}}

    import src.core.bulb_controller as bulb_controller_module

    controller = BulbController()
    monkeypatch.setattr(controller, "_get_bulb", lambda ip: FakeBulb())
    monkeypatch.setattr(bulb_controller_module, "PROFILE_QUERY_TIMEOUT", 0.01)

    profile = await controller.get_color_profile("192.168.1.55")

    assert profile.mac == "aa:bb"
    assert profile.model_name is None
    assert profile.white_channels == BulbColorProfile().white_channels
