"""Regression tests for native Ollama multimodal conversion (#4249)."""

from src.llm_core import _ollama_normalize_tool_messages


def test_single_image_with_text():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ],
    }]
    result = _ollama_normalize_tool_messages(messages)
    assert result[0]["content"] == "What is in this image?"
    assert result[0]["images"] == ["iVBORw0KGgo="]


def test_multiple_images():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Compare these"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,def456"}},
        ],
    }]
    result = _ollama_normalize_tool_messages(messages)
    assert result[0]["content"] == "Compare these"
    assert result[0]["images"] == ["abc123", "def456"]


def test_image_only_message():
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/webp;base64,abc"}},
        ],
    }]
    result = _ollama_normalize_tool_messages(messages)
    assert result[0]["content"] == ""
    assert result[0]["images"] == ["abc"]


def test_plain_text_is_unchanged():
    messages = [{"role": "user", "content": "Hello"}]
    result = _ollama_normalize_tool_messages(messages)
    assert result == messages
    assert "images" not in result[0]


def test_external_image_url_is_not_forwarded_as_base64():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Look"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ],
    }]
    result = _ollama_normalize_tool_messages(messages)
    assert result[0]["content"] == "Look"
    assert "images" not in result[0]


def test_tool_calls_are_still_normalized():
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "function": {"name": "test", "arguments": '{"key": "value"}'},
        }],
    }]
    result = _ollama_normalize_tool_messages(messages)
    assert result[0]["tool_calls"][0]["function"]["arguments"] == {"key": "value"}


def test_multimodal_content_and_tool_calls_are_both_normalized():
    messages = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Analyzing..."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
        ],
        "tool_calls": [{
            "function": {"name": "analyze", "arguments": "{}"},
        }],
    }]
    result = _ollama_normalize_tool_messages(messages)
    assert result[0]["content"] == "Analyzing..."
    assert result[0]["images"] == ["xyz"]
    assert result[0]["tool_calls"][0]["function"]["arguments"] == {}


def test_non_dict_messages_pass_through():
    assert _ollama_normalize_tool_messages(["text", 42, None]) == ["text", 42, None]
