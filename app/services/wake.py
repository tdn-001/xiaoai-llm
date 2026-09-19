from __future__ import annotations

import re


# Characters ignored when comparing wake words: ASR frequently inserts spaces
# ("AI 助手"), converts punctuation, or changes case ("ai助手").
_SKIP_CHARS = re.compile(r"[\s，。！？、,.:：;；!？!…·'\"“”‘’()（）【】\[\]{}<>《》\\|/]+")
_LEADING_SKIP = re.compile(r"^[\s，。！？、,.:：;；!…·'\"“”‘’()（）【】\[\]{}<>《》\\|/]+")
_TRAILING_SKIP = re.compile(r"[\s，。！？、,.:：;；!…·'\"“”‘’()（）【】\[\]{}<>《》\\|/]+$")


def _normalize_char(char: str) -> str | None:
    """Return the comparison character, or None when it must be ignored."""
    if _SKIP_CHARS.fullmatch(char):
        return None
    return char.casefold()


def _normalize(text: str) -> tuple[str, list[int]]:
    """Return (normalized text, mapping of normalized index -> original index)."""
    normalized: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        folded = _normalize_char(char)
        if folded is None:
            continue
        normalized.append(folded)
        mapping.append(index)
    return "".join(normalized), mapping


def match_wake_word(text: str, words: list[str], mode: str = "prefix") -> tuple[str, str] | None:
    """Match a wake word in ASR text, ignoring spaces/punctuation/case.

    Returns (matched word, question with the wake word removed), or None.
    """
    normalized, mapping = _normalize(text)
    for word in sorted(words, key=len, reverse=True):
        target = "".join(_normalize_char(c) or "" for c in word)
        if not target:
            continue
        if mode == "prefix":
            position = 0 if normalized.startswith(target) else -1
        else:
            position = normalized.find(target)
        if position < 0:
            continue
        end_index = position + len(target) - 1
        if end_index >= len(mapping):
            end_original = len(text) - 1
        else:
            end_original = mapping[end_index]
        if mode == "prefix":
            query = _LEADING_SKIP.sub("", text[end_original + 1 :])
        else:
            start_original = mapping[position]
            prefix = _TRAILING_SKIP.sub("", text[:start_original])
            suffix = _LEADING_SKIP.sub("", text[end_original + 1 :])
            query = prefix + suffix
        return word, query.strip()
    return None
