from __future__ import annotations

from typing import Any


MODELS: list[dict[str, Any]] = [
    {"key": "s12", "name": "小米 AI 音箱（第一代）", "hardware": "S12", "level": "full", "tts_command": "5-1", "wake_command": "5-3", "playing_command": None, "edge_audio": True},
    {"key": "s12a", "name": "小米 AI 音箱（第一代 S12A）", "hardware": "S12A", "level": "full", "tts_command": "5-1", "wake_command": "5-5", "playing_command": None, "edge_audio": True},
    {"key": "l15a", "name": "小米 AI 音箱（第二代）", "hardware": "L15A", "level": "full", "tts_command": "7-3", "wake_command": "7-1", "playing_command": "3-1-1", "edge_audio": True},
    {"key": "l06a", "name": "小爱音箱", "hardware": "L06A", "level": "partial", "tts_command": "5-1", "wake_command": "5-2", "playing_command": None, "edge_audio": True},
    {"key": "lx01", "name": "小爱音箱 mini", "hardware": "LX01", "level": "partial", "tts_command": "5-1", "wake_command": "5-2", "playing_command": None, "edge_audio": True},
    {"key": "lx06", "name": "小爱音箱 Pro", "hardware": "LX06", "level": "full", "tts_command": "5-1", "wake_command": "5-3", "playing_command": None, "edge_audio": True},
    {"key": "lx05", "name": "小爱音箱 Play（2019）", "hardware": "LX05", "level": "full", "tts_command": "5-1", "wake_command": "5-3", "playing_command": "3-1-1", "edge_audio": True},
    {"key": "l05b", "name": "小爱音箱 Play", "hardware": "L05B", "level": "partial", "tts_command": "5-3", "wake_command": "5-1", "playing_command": None, "edge_audio": True},
    {"key": "l05c", "name": "小爱音箱 Play 增强版", "hardware": "L05C", "level": "partial", "tts_command": "5-3", "wake_command": "5-1", "playing_command": None, "edge_audio": True},
    {"key": "lx5a", "name": "小爱音箱万能遥控版", "hardware": "LX5A", "level": "full", "tts_command": "5-1", "wake_command": "5-3", "playing_command": None, "edge_audio": True},
    {"key": "l09a", "name": "小爱音箱 Art", "hardware": "L09A", "level": "partial", "tts_command": "3-1", "wake_command": "3-2", "playing_command": None, "edge_audio": True},
    {"key": "oh2", "name": "Xiaomi 智能音箱", "hardware": "OH2", "level": "full", "tts_command": "5-3", "wake_command": "5-1", "playing_command": "3-1-1", "edge_audio": True},
    {"key": "oh2p", "name": "Xiaomi 智能音箱 Pro", "hardware": "OH2P", "level": "full", "tts_command": "7-3", "wake_command": "7-1", "playing_command": None, "edge_audio": True},
    {"key": "l17a", "name": "Xiaomi Sound Pro", "hardware": "L17A", "level": "full", "tts_command": "7-3", "wake_command": "7-1", "playing_command": None, "edge_audio": True},
    {"key": "lx04", "name": "小爱触屏音箱", "hardware": "LX04", "level": "partial", "tts_command": "5-1", "wake_command": "5-2", "playing_command": None, "edge_audio": True},
    {"key": "x10a", "name": "小爱智能家庭屏 10", "hardware": "X10A", "level": "full", "tts_command": "7-3", "wake_command": "7-1", "playing_command": None, "edge_audio": True},
    {"key": "x6a", "name": "Xiaomi 智能家庭屏 6", "hardware": "X6A", "level": "partial", "tts_command": "7-3", "wake_command": "7-1", "playing_command": None, "edge_audio": True},
    {"key": "x08e", "name": "Redmi 小爱触屏音箱 Pro 8", "hardware": "X08E", "level": "partial", "tts_command": "7-3", "wake_command": "7-1", "playing_command": None, "edge_audio": True},
    {"key": "x8f", "name": "Xiaomi 智能家庭屏 Pro 8", "hardware": "X8F", "level": "partial", "tts_command": "7-3", "wake_command": "7-1", "playing_command": None, "edge_audio": True},
    {"key": "sm4", "name": "小米小爱音箱 HD", "hardware": "SM4", "level": "unsupported", "tts_command": None, "wake_command": None, "playing_command": None, "edge_audio": False},
    {"key": "custom", "name": "其他 / 手动配置", "hardware": "", "level": "untested", "tts_command": None, "wake_command": None, "playing_command": None, "edge_audio": False},
]


def get_model(key: str) -> dict[str, Any]:
    return next((item for item in MODELS if item["key"] == key), MODELS[-1])
