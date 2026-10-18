"""Controlled vocabulary for finding categories.

Every system (baselines, agents, the golden set in Phase 9) uses these names, because the
evaluation matches findings on (category, line overlap). Free-form categories would make a
correct finding look wrong just because it was spelled differently.
"""
import re

# category -> (owning agent, one-line meaning shown to the LLM)
CATEGORIES: dict[str, tuple[str, str]] = {
    # security
    "sql_injection": ("security", "SQL built from untrusted input"),
    "command_injection": ("security", "shell/OS command built from untrusted input"),
    "path_traversal": ("security", "untrusted input used in a file path"),
    "hardcoded_secret": ("security", "password, token or key committed in code"),
    "insecure_deserialization": ("security", "pickle/yaml.load/eval on untrusted data"),
    "weak_crypto": ("security", "weak hash/cipher/randomness for security purposes"),
    "xss": ("security", "unescaped untrusted input rendered into HTML"),
    "ssrf": ("security", "server fetches a URL controlled by the user"),
    "missing_authz": ("security", "missing authentication/authorization check"),
    "insecure_config": ("security", "debug mode, disabled TLS verification, open CORS, etc."),
    # quality / bugs
    "missing_null_check": ("quality", "value that can be None/empty is used without a check"),
    "unhandled_exception": ("quality", "error path not handled, or exception swallowed"),
    "resource_leak": ("quality", "file/connection/lock not closed or released"),
    "logic_error": ("quality", "code does not do what it intends (wrong condition, wrong variable)"),
    "off_by_one": ("quality", "boundary or index error"),
    "race_condition": ("quality", "unsafe shared state under concurrency"),
    "performance_issue": ("quality", "avoidable O(n^2), query in a loop, etc."),
    "mutable_default_arg": ("quality", "mutable default argument value"),
    "dead_code": ("quality", "unreachable or unused code"),
    "high_complexity": ("quality", "function too complex or too long to maintain"),
    # tests
    "missing_test": ("tests", "new or changed behaviour has no test"),
    "weak_test": ("tests", "test exists but would not catch a regression"),
    # docs
    "missing_docstring": ("docs", "public function/class without a docstring"),
    "outdated_docs": ("docs", "docstring/README no longer matches the code"),
    # fallback
    "other": ("quality", "a real issue that fits none of the above"),
}

_ALIASES = {
    "sqli": "sql_injection", "sql_injection_vulnerability": "sql_injection",
    "null_check": "missing_null_check", "null_dereference": "missing_null_check",
    "none_check": "missing_null_check", "missing_none_check": "missing_null_check",
    "missing_tests": "missing_test", "untested_function": "missing_test", "no_test": "missing_test",
    "missing_docs": "missing_docstring", "no_docstring": "missing_docstring",
    "hardcoded_password": "hardcoded_secret", "hardcoded_credentials": "hardcoded_secret",
    "hardcoded_api_key": "hardcoded_secret", "hardcoded_token": "hardcoded_secret",
    "error_handling": "unhandled_exception", "missing_error_handling": "unhandled_exception",
    "bare_except": "unhandled_exception", "swallowed_exception": "unhandled_exception",
    "command_execution": "command_injection", "shell_injection": "command_injection",
    "os_command_injection": "command_injection", "unsafe_deserialization": "insecure_deserialization",
    "bug": "logic_error", "performance": "performance_issue",
}


def normalize_category(raw: str) -> str:
    """snake_case the model's label, map known aliases, and fall back to 'other'."""
    key = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    key = _ALIASES.get(key, key)
    return key if key in CATEGORIES else "other"


def category_help() -> str:
    """Bullet list for the prompt, grouped by agent."""
    lines = []
    for agent in ("security", "quality", "tests", "docs"):
        items = [f"{c} ({desc})" for c, (a, desc) in CATEGORIES.items()
                 if a == agent and c != "other"]
        lines.append(f"- {agent}: " + "; ".join(items))
    lines.append(f"- other: {CATEGORIES['other'][1]}")
    return "\n".join(lines)
