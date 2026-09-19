from app.services.memory import ConversationMemory


def make(memory, did, enabled=True, timeout=30, max_tokens=4096):
    return memory.build_messages(did, "系统提示", "问题A", enabled, timeout, max_tokens)


def test_context_isolated_per_device():
    memory = ConversationMemory()
    memory.remember("did1", "问题A", "回答A", enabled=True)
    messages = make(memory, "did2")
    assert messages == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "问题A"},
    ]
    messages1 = make(memory, "did1")
    assert {"role": "assistant", "content": "回答A"} in messages1


def test_disabled_context_sends_only_system_and_question():
    memory = ConversationMemory()
    memory.remember("did1", "旧问题", "旧回答", enabled=True)
    messages = make(memory, "did1", enabled=False)
    assert messages == [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "问题A"},
    ]
    # Remembered history is untouched by disabled-context requests.
    messages2 = make(memory, "did1", enabled=True)
    assert messages2[1:-1] == [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ]


def test_timeout_clears_only_target_device():
    memory = ConversationMemory()
    memory.remember("did1", "旧问题", "旧回答", enabled=True)
    memory.remember("did2", "旧问题", "旧回答", enabled=True)
    # Simulate expiry of did1 session.
    memory._sessions["did1"].updated_at -= 31 * 60
    messages = make(memory, "did1", timeout=30)
    assert {"role": "assistant", "content": "旧回答"} not in messages
    assert {"role": "assistant", "content": "旧回答"} in make(memory, "did2")


def test_trim_keeps_system_and_current_question():
    memory = ConversationMemory()
    for i in range(50):
        memory.remember("did1", f"问题{i}", "回答" * 200, enabled=True)
    messages = make(memory, "did1", max_tokens=512)
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "问题A"}
    total = sum(len(m["content"]) // 2 + 8 for m in messages)
    assert total <= 512


def test_clear_single_and_all():
    memory = ConversationMemory()
    memory.remember("did1", "问题", "回答", enabled=True)
    memory.remember("did2", "问题", "回答", enabled=True)
    memory.clear("did1")
    assert len(make(memory, "did1")) == 2
    assert len(make(memory, "did2")) == 4
    memory.clear()
    assert len(make(memory, "did2")) == 2
