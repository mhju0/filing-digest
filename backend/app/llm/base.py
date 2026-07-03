"""Provider-agnostic LLM abstraction.

A thin, swappable seam between the app and whichever chat-completion provider
backs it (Solar Pro today; another OpenAI-compatible provider tomorrow). This
module is intentionally *pure interface + data*: no HTTP, no citation logic, no
output guards. Concrete adapters live alongside it (see :mod:`app.llm.solar`).

Design notes:
- :class:`ChatMessage` mirrors the OpenAI chat message shape (``role``/``content``)
  as a ``TypedDict`` so it serializes straight into a request body with no mapping.
- ``response_format`` is a passthrough of the OpenAI-style dict (e.g.
  ``{"type": "json_schema", ...}``). This layer never inspects or builds it --
  structured-output / citation schemas are a caller concern, not the transport's.
- :class:`LLMResult` normalizes just the few fields callers need, and keeps the
  full provider JSON in ``raw`` so nothing is lost when a provider adds fields.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict


class ChatMessage(TypedDict):
    """One OpenAI-style chat message: a ``role`` and its ``content``.

    Kept as a ``TypedDict`` (not a dataclass) so a ``list[ChatMessage]`` is already
    the exact JSON shape the ``messages`` field of a chat-completions request wants.
    """

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResult:
    """Normalized result of one chat completion.

    ``text`` / ``finish_reason`` come from ``choices[0]``; ``input_tokens`` /
    ``output_tokens`` from the provider's ``usage`` block (``None`` when a provider
    omits it). ``raw`` is the full decoded response JSON so callers can reach any
    provider-specific field this dataclass does not surface.
    """

    text: str
    model: str
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    raw: dict[str, Any]


class LLMClient(Protocol):
    """The swappable seam: any chat-completion provider the app can call.

    Adapters implement :meth:`complete`; callers depend on this Protocol, never on
    a concrete provider. ``response_format`` is an optional OpenAI-style dict passed
    through verbatim to the provider (e.g. ``{"type": "json_schema", ...}``) -- this
    layer builds no citation/schema logic of its own.
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Run one chat completion and return a normalized :class:`LLMResult`."""
        ...
