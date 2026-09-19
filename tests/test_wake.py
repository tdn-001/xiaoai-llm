from app.services.wake import match_wake_word


def test_prefix_match_strips_word_and_punctuation():
    result = match_wake_word("AI助手，什么是黑洞", ["问AI", "AI助手"], mode="prefix")
    assert result == ("AI助手", "什么是黑洞")


def test_prefix_match_prefers_longest_word():
    result = match_wake_word("问一下AI天气如何", ["AI", "问"], mode="prefix")
    assert result == ("问", "一下AI天气如何")


def test_contains_match_removes_word_in_middle():
    result = match_wake_word("帮我问AI一个问题", ["问AI"], mode="contains")
    assert result == ("问AI", "帮我一个问题")


def test_no_match_returns_none():
    assert match_wake_word("打开客厅的灯", ["AI助手"], mode="prefix") is None
    assert match_wake_word("AI助手在吗", ["问AI"], mode="prefix") is None


def test_question_may_be_empty_after_strip():
    result = match_wake_word("AI助手，", ["AI助手"], mode="prefix")
    assert result == ("AI助手", "")


def test_case_insensitive_for_latin_words():
    result = match_wake_word("ai helper 你好", ["AI Helper"], mode="prefix")
    assert result == ("AI Helper", "你好")


def test_asr_inserted_space_between_letters_still_matches():
    result = match_wake_word("AI 助手，什么是黑洞", ["AI助手"], mode="prefix")
    assert result == ("AI助手", "什么是黑洞")


def test_asr_lowercase_and_space_variant():
    result = match_wake_word("问 ai 明天适合跑步吗", ["问AI"], mode="prefix")
    assert result == ("问AI", "明天适合跑步吗")


def test_asr_punctuation_inside_word_ignored():
    result = match_wake_word("小爱、AI。助手讲个笑话", ["AI助手"], mode="contains")
    assert result == ("AI助手", "小爱讲个笑话")


def test_plain_queries_still_do_not_match():
    assert match_wake_word("打开灯", ["AI助手", "问AI"], mode="prefix") is None
    assert match_wake_word("今天天气怎么样", ["问AI"], mode="contains") is None
