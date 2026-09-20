from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

import edge_tts

from app.mijia.client import MijiaClient
from app.models import AppConfig, EffectiveDeviceConfig


class TTSService:
    def __init__(self, audio_dir: str | Path):
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    async def speak(self, config: AppConfig, device: EffectiveDeviceConfig, text: str, mijia: MijiaClient) -> None:
        if device.tts_provider == "mijia":
            await mijia.speak_mijia(device, text)
            return
        try:
            await self.speak_edge(config, device, text, mijia)
        except Exception:
            if not config.tts.fallback_to_mijia:
                raise
            await mijia.speak_mijia(device, text)

    async def speak_edge(
        self, config: AppConfig, device: EffectiveDeviceConfig, text: str, mijia: MijiaClient
    ) -> None:
        await asyncio.wait_for(
            self._speak_edge(config, device, text, mijia),
            config.tts.timeout_seconds,
        )

    async def _speak_edge(
        self, config: AppConfig, device: EffectiveDeviceConfig, text: str, mijia: MijiaClient
    ) -> None:
        if not config.web.public_base_url:
            raise ValueError("EdgeTTS 需要配置音箱可访问的局域网地址")
        token = secrets.token_urlsafe(18)
        path = self.audio_dir / f"{token}.mp3"
        rate = f"{(device.speed - 1.0) * 100:+.0f}%"
        await edge_tts.Communicate(text, device.edge_voice, rate=rate).save(str(path))
        url = f"{config.web.public_base_url.rstrip('/')}/audio/{path.name}"
        await mijia.play_url(device, url)

    def cleanup(self, max_age_seconds: int = 3600) -> None:
        threshold = time.time() - max_age_seconds
        for path in self.audio_dir.glob("*.mp3"):
            if path.stat().st_mtime < threshold:
                path.unlink(missing_ok=True)
