"""Replay execution policies and tool overrides."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class Live:
    """Override directive indicating that a tool or model should execute live."""

    def __repr__(self) -> str:
        return "Live()"


class Mock:
    """Override directive returning a static mock response or raising an exception."""

    def __init__(self, value: Any = None, error: BaseException | None = None) -> None:
        self.value = value
        self.error = error

    def __repr__(self) -> str:
        if self.error is not None:
            return f"Mock(error={self.error!r})"
        return f"Mock(value={self.value!r})"


class ReplayPolicy(BaseModel):
    """Configuration determining whether LLM calls, tools, and captures use recorded data or execute live."""

    model_config = ConfigDict(extra="ignore")

    llm: Literal["recorded", "live", "mock"] = "recorded"
    tools: Literal["recorded", "live", "mock"] = "recorded"
    capture: Literal["recorded", "live", "mock"] = "recorded"

    @model_validator(mode="before")
    @classmethod
    def _normalize_tool_field(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "tool" in data and "tools" not in data:
                data["tools"] = data["tool"]
        return data

    @property
    def tool(self) -> Literal["recorded", "live", "mock"]:
        return self.tools

    @classmethod
    def all_recorded(cls) -> ReplayPolicy:
        """Freeze all LLM completions, tool invocations, and captured non-deterministic calls."""
        return cls(llm="recorded", tools="recorded", capture="recorded")

    @classmethod
    def llm_live_tools_recorded(cls) -> ReplayPolicy:
        """Execute LLM calls live (e.g. testing prompt/model changes) while keeping tool outputs frozen."""
        return cls(llm="live", tools="recorded", capture="recorded")

    @classmethod
    def tools_live_llm_recorded(cls) -> ReplayPolicy:
        """Freeze LLM responses while executing tools against live external environments."""
        return cls(llm="recorded", tools="live", capture="recorded")
