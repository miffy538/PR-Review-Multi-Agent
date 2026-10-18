"""What we ask the LLM to emit. Lenient on spelling/format, strict on meaning.

RawFinding is deliberately NOT the final Finding: the model also gives us `evidence_quote`
(the code line it is pointing at), which the validator uses to catch hallucinated locations.
"""
from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.categories import normalize_category
from app.schemas.finding import AgentName, Severity


class RawFinding(BaseModel):
    agent: AgentName
    category: str
    severity: Severity
    file: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int | None = None
    evidence_quote: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    suggestion: str | None = None
    confidence: float = 0.5
    rationale: str | None = None

    @field_validator("agent", mode="before")
    @classmethod
    def _agent(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, v):
        return v.strip().capitalize() if isinstance(v, str) else v

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return normalize_category(v) if isinstance(v, str) else v

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.5
        if 1 < x <= 100:           # model answered "85" meaning 85%
            x /= 100
        return min(max(x, 0.0), 1.0)

    @model_validator(mode="after")
    def _lines(self):
        if self.line_end is None or self.line_end < self.line_start:
            self.line_end = self.line_start
        return self
