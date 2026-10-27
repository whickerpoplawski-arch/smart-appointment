"""Stream-collecting chat model: invoke/ainvoke must succeed on stream-only gateways.

ChatOpenAI.ainvoke posts chat/completions with stream=false. Gateways that
reject that request never reach a booking call site. These tests pin the
wrapper that aggregates stream chunks into one AIMessage, including tool calls.
"""

import os

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from config.model_provider import create_chat_model
from config.stream_collecting_chat_model import StreamCollectingChatOpenAI
from langchain_openai import AzureChatOpenAI, ChatOpenAI


def _model(**overrides) -> StreamCollectingChatOpenAI:
    params = {
        "model": "deepseek-v4.1-flash",
        "api_key": "test-key",
        "base_url": "https://d1api.xin/v1",
        "temperature": 0,
    }
    params.update(overrides)
    return StreamCollectingChatOpenAI(**params)


def _content_chunks() -> list[ChatGenerationChunk]:
    return [
        ChatGenerationChunk(message=AIMessageChunk(content="已为")),
        ChatGenerationChunk(message=AIMessageChunk(content="患者预约内科初诊")),
    ]


def _tool_chunks() -> list[ChatGenerationChunk]:
    return [
        ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "book_department",
                        "args": "",
                        "id": "call_1",
                        "index": 0,
                    }
                ],
            )
        ),
        ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": None,
                        "args": '{"specialty": "内科"}',
                        "id": None,
                        "index": 0,
                    }
                ],
            )
        ),
    ]


def _install_chunks(model, chunks: list[ChatGenerationChunk]) -> None:
    def stream(messages, stop=None, run_manager=None, **kwargs):
        yield from chunks

    async def astream(messages, stop=None, run_manager=None, **kwargs):
        for chunk in chunks:
            yield chunk

    model._stream = stream
    model._astream = astream


class _SyncNonStreamClient:
    """Stand-in that fails the test if invoke hits the non-stream client."""

    def __init__(self):
        self.with_raw_response = self

    def create(self, *args, **kwargs):
        raise AssertionError(
            f"non-stream chat.completions.create called (stream={kwargs.get('stream')!r})"
        )


class _AsyncNonStreamClient:
    """Stand-in that fails the test if ainvoke hits the non-stream client."""

    def __init__(self):
        self.with_raw_response = self

    async def create(self, *args, **kwargs):
        raise AssertionError(
            f"non-stream chat.completions.create called (stream={kwargs.get('stream')!r})"
        )


def _forbid_non_stream(model) -> None:
    model.client = _SyncNonStreamClient()
    model.async_client = _AsyncNonStreamClient()


class TestPlainChatOpenAIUsesNonStream:
    @pytest.mark.asyncio
    async def test_ainvoke_payload_sets_stream_false(self):
        """Upstream characterization: ainvoke does not use the streaming endpoint.

        Production change that should keep this red: ChatOpenAI.ainvoke starting
        to send stream=true by default. The wrapper exists because that default
        is stream=false (HTTP 400 on stream-only gateways).
        """
        captured = {}

        class Raw:
            async def create(self, **payload):
                captured.update(payload)
                raise RuntimeError("captured-non-stream")

        class Client:
            def __init__(self):
                self.with_raw_response = Raw()

        model = ChatOpenAI(
            model="deepseek-v4.1-flash",
            api_key="test-key",
            base_url="https://d1api.xin/v1",
        )
        model.async_client = Client()

        with pytest.raises(RuntimeError, match="captured-non-stream"):
            await model.ainvoke("hi")

        assert captured["stream"] is False


class TestStreamCollection:
    @pytest.mark.asyncio
    async def test_ainvoke_joins_content_chunks_into_one_message(self):
        model = _model()
        model.streaming = False
        _install_chunks(model, _content_chunks())
        _forbid_non_stream(model)

        message = await model.ainvoke("帮我挂内科初诊")

        assert isinstance(message, AIMessage)
        assert message.content == "已为患者预约内科初诊"

    def test_invoke_joins_content_chunks_into_one_message(self):
        model = _model()
        model.streaming = False
        _install_chunks(model, _content_chunks())
        _forbid_non_stream(model)

        message = model.invoke("帮我挂内科初诊")

        assert isinstance(message, AIMessage)
        assert message.content == "已为患者预约内科初诊"

    @pytest.mark.asyncio
    async def test_ainvoke_merges_tool_call_chunks(self):
        model = _model()
        model.streaming = False
        _install_chunks(model, _tool_chunks())
        _forbid_non_stream(model)

        message = await model.ainvoke("内科初诊")

        assert message.content == ""
        assert message.tool_calls == [
            {
                "name": "book_department",
                "args": {"specialty": "内科"},
                "id": "call_1",
                "type": "tool_call",
            }
        ]

    def test_stream_still_yields_each_chunk(self):
        model = _model()
        _install_chunks(model, _content_chunks())

        contents = [chunk.content for chunk in model.stream("帮我挂内科初诊")]

        assert contents == ["已为", "患者预约内科初诊"]


def _provider_env(monkeypatch, provider: str, force_stream: str | None) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://d1api.xin/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4.1-flash")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-test")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-test")
    monkeypatch.setenv("AZURE_OPENAI_VERSION", "2024-02-01")
    if force_stream is None:
        monkeypatch.delenv("LLM_FORCE_STREAM", raising=False)
    else:
        monkeypatch.setenv("LLM_FORCE_STREAM", force_stream)


class TestCreateChatModel:
    @pytest.mark.parametrize(
        "provider",
        ["openai", "qwen", "deepseek", "zhipu", "openai-compatible"],
    )
    def test_non_azure_defaults_to_stream_collector(self, monkeypatch, provider):
        _provider_env(monkeypatch, provider, None)

        model = create_chat_model(temperature=0)

        assert isinstance(model, StreamCollectingChatOpenAI)
        assert type(model) is not ChatOpenAI

    def test_force_stream_zero_keeps_non_stream_payload(self, monkeypatch):
        _provider_env(monkeypatch, "openai-compatible", "0")

        model = create_chat_model()
        payload = model._get_request_payload([HumanMessage(content="hi")])

        assert type(model) is ChatOpenAI
        assert payload["stream"] is False

    def test_azure_stays_unwrapped_even_when_force_stream_set(self, monkeypatch):
        _provider_env(monkeypatch, "azure", "1")

        model = create_chat_model()

        assert isinstance(model, AzureChatOpenAI)
        assert not isinstance(model, StreamCollectingChatOpenAI)


def test_wrapper_payload_requests_stream_without_a_live_key():
    """The default request body uses stream=true and does not need a real key."""
    os.environ.pop("OPENAI_API_KEY", None)
    model = _model()
    payload = model._get_request_payload([HumanMessage(content="hi")])
    assert payload["stream"] is True
    assert payload["model"] == "deepseek-v4.1-flash"
