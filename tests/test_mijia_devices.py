import pytest

from app.mijia.client import MijiaClient


class FakeMina:
    async def device_list(self):
        return [
            {
                "name": "小爱触屏音箱",
                "hardware": "LX04",
                "miotDID": "266821396",
                "deviceID": "mina-1",
                "capabilities": {"yunduantts": True},
            },
            {
                "name": "小米 AI 音箱",
                "hardware": "S12A",
                "miotDID": "102162497",
                "deviceID": "mina-2",
            },
        ]


class BrokenMiio:
    async def device_list(self, name=None):
        raise RuntimeError("xiaomiio login failed")


async def test_discovery_degrades_to_mina_when_miio_fails():
    client = MijiaClient("default")
    client.authenticated = True
    client.mina = FakeMina()
    client.miio = BrokenMiio()

    devices = await client.discover_devices()
    assert [(d["did"], d["hardware"]) for d in devices] == [
        ("266821396", "LX04"),
        ("102162497", "S12A"),
    ]
    assert devices[0]["mina_device_id"] == "mina-1"
    assert devices[0]["model"] == ""


class PartialMina:
    async def device_list(self):
        return [
            {"name": "bad-no-did", "hardware": "LX04", "deviceID": "mina-1"},
            {"name": "bad-no-device", "hardware": "LX04", "miotDID": "1"},
        ]


async def test_discovery_ignores_incomplete_records():
    client = MijiaClient("default")
    client.authenticated = True
    client.mina = PartialMina()
    client.miio = BrokenMiio()
    assert await client.discover_devices() == []
