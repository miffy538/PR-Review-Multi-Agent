"""The shared contract: every agent returns list[Finding]. Single source of truth."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Severity(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class AgentName(str, Enum):
    SECURITY = "security"
    QUALITY = "quality"
    TESTS = "tests"
    DOCS = "docs"


class Evidence(BaseModel):
    """Why we believe a finding. Tool output and retrieval hits raise confidence."""
    type: Literal["bandit", "semgrep", "radon", "coverage", "retrieval", "llm"]
    detail: str


class Finding(BaseModel):
    agent: AgentName
    category: str = Field(min_length=1, description="e.g. sql_injection, missing_test")
    severity: Severity
    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    summary: str = Field(min_length=1)
    suggestion: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    agreed_by: list[AgentName] = Field(default_factory=list)

    @model_validator(mode="after")
    def _lines_ordered(self):
        if self.line_end < self.line_start:
            raise ValueError("line_end must be >= line_start")
        return self


class AgentOutput(BaseModel):
    """Wrapper used as the LLM structured-output target."""
    findings: list[Finding] = Field(default_factory=list)
