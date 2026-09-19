from datetime import datetime

import pytest

from app.models import WebSearchConfig
from app.services.llm import _strip_thinking
from app.services.web_search import (
    WebSearchService,
    _BingParser,
    _SogouParser,
    _validate_public_url,
    inject_web_context,
    normalize_search_query,
)


def test_strip_thinking_removes_think_blocks():
    text = "<think>嗯用户问的是豆包…</think>豆包是字节跳动旗下的 AI 助手。"
    assert _strip_thinking(text) == "豆包是字节跳动旗下的 AI 助手。"


def test_strip_thinking_removes_leading_meta():
    text = "嗯，用户问的是豆包大模型。\n关键点是：豆包是字节跳动的产品。\n\n回答：豆包是字节跳动旗下的 AI 助手。"
    cleaned = _strip_thinking(text)
    # 开头元叙述（"嗯，用户问的是…"）必须被剥离
    assert "嗯，用户问的是" not in cleaned
    # 正文内容必须保留
    assert "豆包是字节跳动旗下的 AI 助手。" in cleaned


def test_strip_thinking_preserves_normal_text():
    text = "豆包是字节跳动旗下的 AI 助手。"
    assert _strip_thinking(text) == text


def test_bing_parser_extracts_results():
    parser = _BingParser()
    parser.feed(
        '<li class="b_algo"><h2><a href="https://example.com/b">Bing 标题</a></h2>'
        '<div class="b_caption"><p>Bing 摘要</p></div></li>'
    )
    assert parser.results[0] == {
        "title": "Bing 标题",
        "url": "https://example.com/b",
        "snippet": "Bing 摘要 ",
    }


def test_sogou_parser_extracts_results():
    parser = _SogouParser()
    parser.feed(
        '<h3 class="vr-title"><a href="/link?url=abc123">Sogou 标题</a></h3>'
        '<div class="space-txt">Sogou 摘要内容</div>'
    )
    assert parser.results[0]["title"] == "Sogou 标题"
    assert "abc123" in parser.results[0]["url"]
    assert "Sogou 摘要内容" in parser.results[0]["snippet"]


def test_sogou_parser_handles_absolute_url():
    parser = _SogouParser()
    parser.feed(
        '<h3 class="vr-title"><a href="https://example.com/article">标题</a></h3>'
    )
    assert parser.results[0]["url"] == "https://example.com/article"


def test_web_context_is_injected_before_user_message():
    messages = [{"role": "system", "content": "规则"}, {"role": "user", "content": "问题"}]
    result = inject_web_context(messages, "搜索资料")
    assert result[-1] == messages[-1]
    assert "web_search/web_fetch" in result[-2]["content"]
    assert "搜索资料" in result[-2]["content"]
    assert "没有搜到官方确认不代表" in result[-2]["content"]


def test_search_query_normalizes_spoken_iphone_model_and_intents():
    query = normalize_search_query(
        "知道IPHONE十八PRO多少钱什么时候发布的吗",
        datetime(2026, 9, 19, 17, 0),
    )
    assert query == "iPhone18Pro价格发布时间 截至2026年9月19日 最新消息"


def test_search_query_normalizes_concert_question():
    query = normalize_search_query(
        "薛之谦下次演唱会什么时候开始",
        datetime(2026, 9, 19, 17, 0),
    )
    assert query == "薛之谦下次演唱会开始时间 截至2026年9月19日 最新消息"


def test_search_query_normalizes_concert_question_with_particles():
    query = normalize_search_query(
        "你知道薛之谦下次演唱会什么时候开始吗",
        datetime(2026, 9, 19, 17, 0),
    )
    assert "薛之谦" in query
    assert "演唱会" in query
    assert "开始时间" in query
    assert "截至2026年9月19日" in query
    assert "吗" not in query


def test_search_query_handles_movie_question():
    query = normalize_search_query(
        "流浪地球三什么时候上映啊",
        datetime(2026, 9, 19, 17, 0),
    )
    assert query == "流浪地球3上映时间 截至2026年9月19日 最新消息"


def test_search_query_handles_weather_question():
    query = normalize_search_query(
        "明天成都天气怎么样",
        datetime(2026, 9, 19, 17, 0),
    )
    assert "成都天气" in query
    assert "截至2026年9月19日" in query


def test_search_query_handles_generic_question_without_time():
    query = normalize_search_query(
        "Python怎么学",
        datetime(2026, 9, 19, 17, 0),
    )
    assert query == "Python怎么学"


def test_search_query_removes_spoken_particles_between_intents():
    query = normalize_search_query(
        "你知道IPHONE十二PRO多少钱吗什么时候发布的吗",
        datetime(2026, 9, 19, 17, 0),
    )
    assert query == "iPhone12Pro价格发布时间 截至2026年9月19日 最新消息"


async def test_web_fetch_blocks_private_addresses():
    with pytest.raises(ValueError, match="内网"):
        await _validate_public_url("http://127.0.0.1/admin")


async def test_research_combines_results_and_fetched_pages():
    class FakeService(WebSearchService):
        async def web_search(self, config, query):
            return [{"title": "标题", "url": "https://example.com", "snippet": "摘要"}]

        async def web_fetch(self, config, url):
            return "正文"

    text = await FakeService().research(WebSearchConfig(enabled=True), "测试")
    assert "标题" in text
    assert "摘要" in text
    assert "正文" in text
