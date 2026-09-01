"""Public execution events emitted by the demo agent.

These events describe observable work such as tool selection and validation.
They intentionally do not expose a model's hidden chain of thought.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field


EventPhase = Literal["understand", "action", "observe", "review", "answer", "error"]


class AgentEvent(BaseModel):
    phase: EventPhase
    title: str
    detail: str | None = None
    status: Literal["running", "completed", "warning", "error"] = "running"
    metadata: dict[str, Any] = Field(default_factory=dict)


EventCallback = Callable[[AgentEvent], None]


def emit(
    callback: EventCallback | None,
    phase: EventPhase,
    title: str,
    detail: str | None = None,
    *,
    status: Literal["running", "completed", "warning", "error"] = "running",
    metadata: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    callback(
        AgentEvent(
            phase=phase,
            title=title,
            detail=detail,
            status=status,
            metadata=metadata or {},
        )
    )
