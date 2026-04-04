# skills/wiz_config/control_light.py

from wizlight import WizLight, PilotBuilder
import asyncio

LIGHT_IP = "192.168.1.10"

async def turn_on():
    light = WizLight(LIGHT_IP)
    await light.turn_on(PilotBuilder(brightness=255))

asyncio.run(turn_on())