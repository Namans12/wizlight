from click.testing import CliRunner

from src.cli.commands import cli, get_bulb_ips
from src.core.config import BulbConfig, Config


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int | None]] = []
        self.closed = False
        self.stale_bulbs: list[str] = []

    async def turn_on_all(self, ips, brightness=None):
        self.calls.append((ips, brightness))

    async def find_stale_bulbs(self, ips):
        return list(self.stale_bulbs)

    async def close_async(self):
        self.closed = True


def test_get_bulb_ips_returns_all_configured_bulbs(tmp_path):
    config = Config(
        bulbs=[
            BulbConfig(ip="192.168.1.10", name="Desk"),
            BulbConfig(ip="192.168.1.11", name="Lamp"),
        ],
        _config_path=tmp_path / "config.json",
    )

    assert get_bulb_ips(config) == ["192.168.1.10", "192.168.1.11"]
    assert get_bulb_ips(config, "192.168.1.20") == ["192.168.1.20"]


def test_brightness_command_uses_bulk_turn_on(monkeypatch, tmp_path):
    config = Config(
        bulbs=[BulbConfig(ip="192.168.1.10", name="Desk")],
        _config_path=tmp_path / "config.json",
    )
    controller = FakeController()

    monkeypatch.setattr("src.cli.commands.Config.load", lambda: config)
    monkeypatch.setattr("src.cli.commands.BulbController", lambda: controller)

    result = CliRunner().invoke(cli, ["brightness", "180"])

    assert result.exit_code == 0
    assert "Set brightness to 180 on 1 bulb(s)" in result.output
    assert controller.calls == [(["192.168.1.10"], 180)]
    assert controller.closed is True


def test_prune_bulbs_command_removes_stale_entries(monkeypatch, tmp_path):
    config = Config(
        bulbs=[
            BulbConfig(ip="192.168.1.10", name="Desk"),
            BulbConfig(ip="192.168.1.11", name="Lamp"),
        ],
        _config_path=tmp_path / "config.json",
    )
    config.save()
    controller = FakeController()
    controller.stale_bulbs = ["192.168.1.11"]

    monkeypatch.setattr("src.cli.commands.Config.load", lambda: config)
    monkeypatch.setattr("src.cli.commands.BulbController", lambda: controller)

    result = CliRunner().invoke(cli, ["prune-bulbs"])

    assert result.exit_code == 0
    assert "Removed 1 stale bulb(s): 192.168.1.11" in result.output
    assert [bulb.ip for bulb in config.bulbs] == ["192.168.1.10"]
