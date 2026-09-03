from __future__ import annotations

import re

_DIRECT_PROVIDER_REFERENCE = re.compile(
    r"\b(?:gmail|google\s+(?:workspace|calendar|tasks?)|composio|connected\s+account)\b",
    re.IGNORECASE,
)
_PERSONAL_SERVICE_READ = re.compile(
    r"\b(?:show|search|find|list|read|check|tell|what(?:'s|\s+is))\b.{0,60}"
    r"\b(?:my\s+)?(?:emails?|inbox|mailbox|calendar|schedule|events?|tasks?|to-?dos?|meetings?)\b",
    re.IGNORECASE,
)
_PERSONAL_SERVICE_WRITE = re.compile(
    r"\b(?:create|add|schedule|book|reserve|complete|draft|publish|send|post)\b.{0,60}"
    r"\b(?:emails?|mail|drafts?|events?|meetings?|calendar|tasks?|to-?dos?|tweets?|posts?)\b",
    re.IGNORECASE,
)


def requires_personal_access(request: str) -> bool:
    """Recognize personal-provider requests without blocking ordinary general prompts."""
    return any(
        pattern.search(request)
        for pattern in (
            _DIRECT_PROVIDER_REFERENCE,
            _PERSONAL_SERVICE_READ,
            _PERSONAL_SERVICE_WRITE,
        )
    )


PUBLIC_PERSONAL_MESSAGE = (
    "Personal connected services are disabled in the public demo. "
    "Unlock Admin access to use Gmail, Calendar, Tasks, or connected writes."
)
