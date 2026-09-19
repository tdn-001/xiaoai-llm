from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=33003, ge=1, le=65535)
    public_base_url: str = ""
    password_hash: str = ""
    session_secret: str = ""
    bootstrap_token: str = ""
    # 一次性密码重置令牌（请求时生成、确认后或过期后清空）。
    # TTL 由 app.config 中的 _RESET_TOKEN_TTL 控制（默认 5 分钟）。
    password_reset_token: str = ""
    password_reset_expires_at: float = 0.0


class MijiaAccount(BaseModel):
    id: str
    name: str = ""
    enabled: bool = True
    login_type: Literal["password", "pass_token"] = "password"
    username: str = ""
    password: str = ""
    user_id: str = ""
    pass_token: str = ""
    region: str = "cn"
    token_path: str = ""


class MijiaConfig(BaseModel):
    # Global poll behavior shared by all accounts; per-account credentials live
    # under AppConfig.mijia_accounts (multi-account support).
    selected_devices: list[str] = Field(default_factory=list)
    poll_source: Literal["userprofile", "ubus"] = "userprofile"
    poll_fallback_to_ubus: bool = True
    fetch_native_answer: bool = False
    poll_interval_seconds: float = Field(default=2.0, ge=1.0, le=30.0)
    idle_poll_interval_seconds: float = Field(default=10.0, ge=3.0, le=300.0)
    active_window_seconds: float = Field(default=60.0, ge=10.0, le=600.0)
    max_backoff_seconds: float = Field(default=60.0, ge=5.0, le=600.0)
    # Legacy fields kept for backward-compat loading. They are migrated into
    # the first MijiaAccount on next save and otherwise unused.
    login_type: Literal["password", "pass_token"] = "password"
    username: str = ""
    password: str = ""
    user_id: str = ""
    pass_token: str = ""
    region: str = "cn"
    token_path: str = "data/mi-token.json"


class LLMProfile(BaseModel):
    id: str
    name: str
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    timeout_seconds: float = Field(default=200, ge=5, le=600)
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, gt=0, le=1)
    max_output_tokens: int = Field(default=800, ge=32, le=32768)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class TTSConfig(BaseModel):
    default_provider: Literal["mijia", "edge_tts"] = "mijia"
    edge_voice: str = "zh-CN-XiaoxiaoNeural"
    timeout_seconds: float = Field(default=30, ge=5, le=120)
    fallback_to_mijia: bool = True
    error_message: str = "网络异常，请稍后再试"
    # Shown after wake-word hit and mute, while waiting for LLM response.
    # Leave empty to skip the prompt.
    thinking_message: str = "让我想想"


class WebSearchConfig(BaseModel):
    enabled: bool = False
    search_url: str = "https://www.sogou.com/web"
    max_results: int = Field(default=5, ge=1, le=10)
    fetch_top_results: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=15, ge=3, le=60)
    max_page_chars: int = Field(default=6000, ge=500, le=20000)
    max_result_chars: int = Field(default=12000, ge=500, le=50000)


class DefaultsConfig(BaseModel):
    wake_words: list[str] = Field(default_factory=lambda: ["AI助手", "问AI"])
    wake_word_mode: Literal["prefix", "contains"] = "prefix"
    llm_profile_id: str = "default"
    persona: str = (
        "你是一个友善、准确的语音助手。"
        "请使用适合直接简洁的播报的内容精简的中文回答，控制在 120 字以内，"
        "不要使用 Markdown 表格，也不要读出网址，"
        "直接给出最终答案，禁止输出任何思考过程、分析过程或元叙述（如'嗯，用户问的是…'、'先看看…'、'关键点是…'）。"
    )
    tts_provider: Literal["mijia", "edge_tts"] = "mijia"
    context_enabled: bool = True
    web_search_enabled: bool = False
    session_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    max_context_tokens: int = Field(default=4096, ge=256, le=131072)

    @field_validator("wake_words")
    @classmethod
    def validate_wake_words(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(word.strip() for word in value if word.strip()))
        if not cleaned:
            raise ValueError("至少需要一个唤醒词")
        return cleaned


class DeviceOverride(BaseModel):
    name: str = ""
    hardware: str = ""
    mina_device_id: str = ""
    compatibility_key: str = "custom"
    account_id: str = ""
    enabled: bool = True
    llm_profile_id: str | None = None
    persona: str | None = None
    wake_words: list[str] | None = None
    wake_word_mode: Literal["prefix", "contains"] | None = None
    tts_provider: Literal["mijia", "edge_tts"] | None = None
    edge_voice: str | None = None
    context_enabled: bool | None = None
    web_search_enabled: bool | None = None
    session_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    max_context_tokens: int | None = Field(default=None, ge=256, le=131072)
    tts_command: str | None = None
    wake_command: str | None = None
    playing_command: str | None = None


class AppConfig(BaseModel):
    version: int = 1
    web: WebConfig = Field(default_factory=WebConfig)
    # Global poll behavior shared by all accounts.
    mijia: MijiaConfig = Field(default_factory=MijiaConfig)
    # Multi-account list. First entry becomes the default for legacy bindings
    # that have no account_id set.
    mijia_accounts: list[MijiaAccount] = Field(default_factory=list)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    llm_profiles: list[LLMProfile] = Field(
        default_factory=lambda: [LLMProfile(id="default", name="默认模型")]
    )
    devices: dict[str, DeviceOverride] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "AppConfig":
        ids = [profile.id for profile in self.llm_profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("LLM 预设 ID 不能重复")
        if self.defaults.llm_profile_id not in ids:
            raise ValueError("全局默认 LLM 预设不存在")
        account_ids = {acct.id for acct in self.mijia_accounts}
        for did, device in self.devices.items():
            if device.llm_profile_id and device.llm_profile_id not in ids:
                raise ValueError(f"设备 {did} 引用的 LLM 预设不存在")
            if device.account_id and device.account_id not in account_ids:
                raise ValueError(f"设备 {did} 关联的米家账号不存在")
        return self

    def primary_account(self) -> MijiaAccount | None:
        for acct in self.mijia_accounts:
            if acct.enabled and self._account_has_credentials(acct):
                return acct
        return None

    @staticmethod
    def _account_has_credentials(acct: MijiaAccount) -> bool:
        if acct.login_type == "pass_token":
            return bool(acct.user_id and acct.pass_token)
        return bool(acct.username and acct.password)


class EffectiveDeviceConfig(BaseModel):
    did: str
    enabled: bool
    name: str
    hardware: str
    mina_device_id: str
    account_id: str
    compatibility_key: str
    llm_profile_id: str
    persona: str
    wake_words: list[str]
    wake_word_mode: Literal["prefix", "contains"]
    tts_provider: Literal["mijia", "edge_tts"]
    edge_voice: str
    context_enabled: bool
    web_search_enabled: bool
    session_timeout_minutes: int
    max_context_tokens: int
    tts_command: str | None
    wake_command: str | None
    playing_command: str | None


def resolve_device(config: AppConfig, did: str) -> EffectiveDeviceConfig:
    from app.mijia.compatibility import get_model

    device = config.devices[did]
    defaults = config.defaults
    compatibility = get_model(device.compatibility_key)
    return EffectiveDeviceConfig(
        did=did,
        enabled=device.enabled,
        name=device.name or did,
        hardware=device.hardware,
        mina_device_id=device.mina_device_id,
        account_id=device.account_id,
        compatibility_key=device.compatibility_key,
        llm_profile_id=device.llm_profile_id or defaults.llm_profile_id,
        persona=device.persona if device.persona is not None else defaults.persona,
        wake_words=device.wake_words if device.wake_words is not None else defaults.wake_words,
        wake_word_mode=device.wake_word_mode or defaults.wake_word_mode,
        tts_provider=device.tts_provider or config.tts.default_provider,
        edge_voice=device.edge_voice or config.tts.edge_voice,
        context_enabled=(device.context_enabled if device.context_enabled is not None else defaults.context_enabled),
        web_search_enabled=(
            device.web_search_enabled
            if device.web_search_enabled is not None
            else defaults.web_search_enabled
        ),
        session_timeout_minutes=(device.session_timeout_minutes or defaults.session_timeout_minutes),
        max_context_tokens=device.max_context_tokens or defaults.max_context_tokens,
        tts_command=device.tts_command or compatibility.get("tts_command"),
        wake_command=device.wake_command or compatibility.get("wake_command"),
        playing_command=device.playing_command or compatibility.get("playing_command"),
    )
