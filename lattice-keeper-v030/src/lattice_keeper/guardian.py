"""Guardian: a simple keyword *heuristic* filter.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.

This is NOT an AI safety system. It blocks text containing configured words or
phrases (case-insensitive, whole-word matching). It is easy to evade
(misspellings, synonyms, encodings) and will produce false positives. Treat it
as a tripwire, not a guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

GUARDIAN_KIND = "heuristic_keyword_filter"
GUARDIAN_NOTE = (
    "Heuristic keyword filter with whole-word matching. Easy to evade and may "
    "produce false positives; not a safety guarantee."
)


@dataclass(frozen=True)
class GuardianVerdict:
    allowed: bool
    matched_in_prompt: tuple[str, ...]
    matched_in_output: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "type": GUARDIAN_KIND,
            "allowed": self.allowed,
            "matched_in_prompt": list(self.matched_in_prompt),
            "matched_in_output": list(self.matched_in_output),
            "note": GUARDIAN_NOTE,
        }


class KeywordGuardian:
    def __init__(self, blocklist: Iterable[str]) -> None:
        terms = sorted({t.strip().lower() for t in blocklist if t.strip()})
        self.terms = tuple(terms)
        # \b-anchored; inner whitespace in phrases matches any whitespace run.
        self._patterns = [
            (t, re.compile(r"\b" + r"\s+".join(map(re.escape, t.split())) + r"\b", re.IGNORECASE))
            for t in terms
        ]

    def scan(self, text: str) -> tuple[str, ...]:
        return tuple(t for t, p in self._patterns if p.search(text or ""))

    def check(self, prompt: str, output: str) -> GuardianVerdict:
        mp, mo = self.scan(prompt), self.scan(output)
        return GuardianVerdict(allowed=not (mp or mo), matched_in_prompt=mp, matched_in_output=mo)
