"""Chat model that fulfills invoke/ainvoke from a streaming completion.

Some OpenAI-compatible gateways reject non-streaming ``chat/completions``
(HTTP 400) and only accept ``stream=true``. ``ChatOpenAI.invoke`` and
``ainvoke`` send ``stream=false`` unless ``streaming`` is set, and a later
flip of that flag sends the request back to the non-streaming client.

This subclass ignores that flag: both calls aggregate ``_stream`` /
``_astream`` chunks into one ``AIMessage`` (text plus any tool-call chunks).
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import (
    agenerate_from_stream,
    generate_from_stream,
)
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI


class StreamCollectingChatOpenAI(ChatOpenAI):
    """OpenAI-compatible chat model whose invoke/ainvoke collect a stream."""

    streaming: bool = True

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return generate_from_stream(
            self._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await agenerate_from_stream(
            self._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
        )
