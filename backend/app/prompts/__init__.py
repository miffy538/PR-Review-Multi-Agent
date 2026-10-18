"""Versioned prompt files: app/prompts/<name>.<version>.md

Edit a prompt by creating a NEW version file (baseline_a.v2.md) instead of changing v1, so old
benchmark results stay reproducible. The loader returns a `label` (name.version+hash) that we
store on every run and event.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.schemas.categories import category_help

_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str
    digest: str

    @property
    def label(self) -> str:
        return f"{self.name}.{self.version}+{self.digest}"


def load_prompt(name: str, version: str) -> Prompt:
    path = _DIR / f"{name}.{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path.name}")
    raw = path.read_text(encoding="utf-8")
    text = (raw.replace("{{categories}}", category_help())
               .replace("{{max_findings}}", str(settings.max_findings)))
    return Prompt(name, version, text, hashlib.sha256(raw.encode()).hexdigest()[:8])
