"""Ask the LLM for a JSON list and validate it item by item.

Failure handling (all real things LLMs do):
  * JSON wrapped in ```fences``` or prose  -> extracted
  * bare list instead of {"findings": [...]} -> accepted
  * one malformed item in ten               -> only that item is dropped (and counted)
  * unparseable reply                       -> ONE repair call, then StructuredOutputError
"""
import json
import re
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.base import LLMClient, LLMError, LLMResponse

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(LLMError):
    """The model never produced usable JSON, even after one repair attempt."""


@dataclass
class StructuredResult(Generic[T]):
    items: list[T]
    invalid: list[dict] = field(default_factory=list)      # [{"index": 3, "error": "..."}]
    responses: list[LLMResponse] = field(default_factory=list)
    repaired: bool = False


def extract_json(text: str):
    """Parse model output as JSON, tolerating code fences and leading/trailing chatter."""
    t = text.strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} or [...] in the text.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = t.find(open_c), t.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("reply is not valid JSON")


def _items_from(data, key: str) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    raise ValueError(f'expected a JSON object with a "{key}" list')


async def generate_list(client: LLMClient, *, system: str, user: str, item_model: type[T],
                        key: str = "findings") -> StructuredResult[T]:
    responses: list[LLMResponse] = []
    resp = await client.generate(system=system, user=user, json_mode=True)
    responses.append(resp)
    repaired = False
    try:
        raw_items = _items_from(extract_json(resp.text), key)
    except ValueError as first_err:
        repaired = True
        nudge = (f"{user}\n\n[Your previous reply was rejected: {first_err}. "
                 f'Reply again with ONLY a JSON object of the form {{"{key}": [...]}} and nothing else.]')
        resp = await client.generate(system=system, user=nudge, json_mode=True)
        responses.append(resp)
        try:
            raw_items = _items_from(extract_json(resp.text), key)
        except ValueError as e:
            raise StructuredOutputError(
                f"Model did not return valid JSON, even after a repair attempt: {e}") from e

    items: list[T] = []
    invalid: list[dict] = []
    for i, raw in enumerate(raw_items):
        try:
            items.append(item_model.model_validate(raw))
        except ValidationError as e:
            first = e.errors()[0]
            invalid.append({"index": i, "error": f"{'.'.join(map(str, first['loc']))}: {first['msg']}"})
    return StructuredResult(items=items, invalid=invalid, responses=responses, repaired=repaired)
