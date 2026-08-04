"""Privacy filter: strip secrets from text before storing in memory.

Adapted from agentmemory's ``src/functions/privacy.ts`` — a regex-only floor
(not a ceiling). It catches well-known secret shapes (API keys, bearer tokens,
JWTs) and ``<private>...</private>`` tags. It does NOT do entropy analysis or
load actual secret values from env; for stronger guarantees, callers should
add their own env-based redaction on top.

Used by the auto-capture hook (``post_tool_failure``) to ensure tool error
output containing secrets is scrubbed before being stored as a raw ``mistake``
memory.
"""
from __future__ import annotations

import re

# ``<private>...</private>`` tags → [REDACTED].
_PRIVATE_TAG_RE = re.compile(r"<private>[\s\S]*?</private>", re.IGNORECASE)

# Each pattern is cloned per-call (``re.compile(source, flags)``) to avoid the
# stateful ``lastIndex`` bug that would arise from reusing a compiled ``/g``
# regex across multiple calls. Listed verbatim from agentmemory's privacy.ts.
_SECRET_PATTERN_SOURCES: list[str] = [
    r"(?:api[_-]?key|secret|token|password|credential|auth)[\s]*[=:]\s*[\"']?[A-Za-z0-9_\-/.+]{20,}[\"']?",
    r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}",
    r"sk-proj-[A-Za-z0-9\-_]{20,}",
    r"(?:sk|pk|rk|ak)-[A-Za-z0-9][A-Za-z0-9\-_]{19,}",
    r"sk-ant-[A-Za-z0-9\-_]{20,}",
    r"gh[pus]_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{22,}",
    r"xoxb-[A-Za-z0-9\-]+",
    r"AKIA[0-9A-Z]{16}",
    r"AIza[A-Za-z0-9\-_]{35}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"npm_[A-Za-z0-9]{36}",
    r"glpat-[A-Za-z0-9\-_]{20,}",
    r"dop_v1_[A-Za-z0-9]{64}",
]

# Pre-compile once (module load). Each is stateless because we use
# ``re.sub`` (which resets match position), not ``.finditer`` with state.
_COMPILED_SECRETS = [re.compile(p, re.IGNORECASE) for p in _SECRET_PATTERN_SOURCES]


def strip_private_data(input_text: str) -> str:
    """Strip ``<private>`` tags and known secret shapes from ``input_text``.

    ``<private>...</private>`` blocks become ``[REDACTED]``.
    Secret-shaped tokens (API keys, bearer tokens, JWTs, etc.) become
    ``[REDACTED_SECRET]``.
    """
    result = _PRIVATE_TAG_RE.sub("[REDACTED]", input_text)
    for pattern in _COMPILED_SECRETS:
        result = pattern.sub("[REDACTED_SECRET]", result)
    return result
