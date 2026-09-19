from __future__ import annotations

import re
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.models import LLMProfile


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LEADING_META_RE = re.compile(
    r"^(?:\s*(?:"
    r"嗯[，。、,\.]|"
    r"好的[，。、,\.]|"
    r"用户问的是[，。、,\.]|"
    r"用户想要[，。、,\.]|"
    r"先看看[，。、,\.]|"
    r"看看搜索[，。、,\.]|"
    r"关键点是[：:，,。.]|"
    r"回忆一下[，,。.]|"
    r"不过[，,。.]|"
    r"因此[，,。.]|"
    r"所以[，,。.]|"
    r"我的回答是[：:，,。.]|"
    r"我的回答[：:，,。.]|"
    r"回答[：:，,。.]|"
    r"让我[，。、,]"
    r")\s*)+"
)

_META_PHRASES = (
    "用户问的是", "用户想要", "用户要求", "关键点是", "回忆一下",
    "先看看", "看看搜索", "看看结果", "所以我", "因此我",
    "我的回答是", "我的回答", "不过我得", "不过，我得",
    "但要注意", "因此，我的回答", "所以，我的回答",
)


def _is_meta_paragraph(text: str) -> bool:
    """段落开头是否包含元叙述标记（推理模型的过程性文字）。"""
    head = re.sub(r"^[\s\u3000\uff0c\uff01\uff1f\uff1a\uff1b\uff0e]+", "", text)
    return any(p in head[:30] for p in _META_PHRASES)


def _split_sentences(text: str) -> list[str]:
    """按中英文句末标点切分。"""
    return [s.strip() for s in re.split(r"(?<=[。！？!?\.])\s*", text) if s.strip()]


def _truncate_meta_paragraphs(text: str, max_chars: int = 200) -> str:
    """截断推理模型的元叙述段，只保留最终答复。

    reasoning 模型经常先输出"嗯用户问的是…"、"先看看…"、"关键点是…"这类过程。
    按句子扫描，凡是开头包含元叙述标记的句子都丢弃，直到找到一个不含标记的句子为止。
    清洗后若仍超过 max_chars，按句末标点截断到接近 max_chars 的位置。
    如果全文都是元叙述（极端情况），保留原文不做处理。
    """
    if not text:
        return text
    sentences = _split_sentences(text)
    kept: list[str] = []
    for s in sentences:
        if _is_meta_paragraph(s):
            continue
        kept.append(s)
    if not kept:
        return text
    cleaned = "".join(kept).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    # 在最近的句末标点处截断
    head = cleaned[:max_chars]
    match = re.search(r"[。！？!?\.]", head[::-1])
    if match:
        cut = max_chars - match.start()
        return cleaned[:cut + 1].strip()
    return head.strip()


def _strip_thinking(text: str) -> str:
    """剥离推理模型输出的思考过程，只保留最终答复。"""
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _LEADING_META_RE.sub("", cleaned)
    cleaned = _truncate_meta_paragraphs(cleaned)
    return cleaned.strip()


class LLMClient:
    async def complete(self, profile: LLMProfile, messages: list[dict[str, str]]) -> str:
        if not profile.api_key:
            raise ValueError(f"LLM 预设“{profile.name}”尚未配置 API Key")
        client = AsyncOpenAI(
            api_key=profile.api_key,
            base_url=profile.api_base,
            timeout=profile.timeout_seconds,
        )
        options = {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_output_tokens,
            "stream": profile.stream,
            "extra_body": profile.extra_body or None,
        }
        response = await client.chat.completions.create(**options)
        if not profile.stream:
            content = response.choices[0].message.content or ""
            return _strip_thinking(content).strip()
        chunks: list[str] = []
        async for chunk in response:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            delta = choice.delta
            piece = getattr(delta, "content", None) if delta else None
            if piece:
                chunks.append(piece)
        return _strip_thinking("".join(chunks)).strip()
