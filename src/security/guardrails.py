"""
Prompt Injection & Adversarial Input Guardrails
Member 3 — Execution Engine & Security Lead

Blocks jailbreak attempts, pricing manipulation, and SQL/script injection
before user input reaches any LLM agent or the booking transaction layer.
Every block is written to the security audit log (see audit_log.py) so
it can be shown as evidence in the report and viva.

Matching is done on a normalised copy of the input (NFKC, zero-width stripping,
leetspeak folding, homoglyph mapping, separator collapsing). The original
assessment found the denylist evaded by all ten adversarial variants tested —
paraphrase, lexical substitution, hyphenation, leetspeak, zero-width insertion
and persona jailbreaks. Normalisation closes the encoding-based variants;
paraphrase and persona attacks are not encoding problems and remain out of reach
of any pattern list.
"""

from __future__ import annotations

import re
import unicodedata

from src.security.audit_log import log_security_event

# Leetspeak substitutions, applied inside words only so that route numbers,
# times and fares ("TRAIN-1001 at 08:30") still read as themselves.
_LEET = re.compile(r"[0134578@$]")
_LEET_MAP = {
    "0": "o", "1": "i", "3": "e", "4": "a",
    "5": "s", "7": "t", "8": "b", "@": "a", "$": "s",
}

# Cyrillic and Greek characters that render as Latin letters.
_HOMOGLYPHS = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "і": "i", "ј": "j", "һ": "h",
    "Α": "a", "Β": "b", "Ε": "e", "Ζ": "z", "Η": "h",
    "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o",
    "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x",
}

# Patterns that indicate adversarial or malicious prompts
_MALICIOUS_PATTERNS: list[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"system\s+override",
    r"you\s+are\s+now\s+an?\s+unfiltered",
    r"act\s+as\s+(if\s+)?you\s+have\s+no\s+restrictions",
    r"set\s+fare\s+to\s+0",
    r"give\s+(me\s+)?free\s+tickets",
    r"grant\s+admin\s+access",
    r"<script[\s>]",
    # HTML event handlers and embedded media: `<img src=x onerror=...>` renders
    # and executes in any context that trusts the markup, even with no <script>.
    r"<[^>]*\son\w+\s*=",
    r"<\s*(img|iframe|svg|object|embed|body|video|audio|form)\b",
    r"javascript\s*:",
    r"drop\s+table",
    r"--\s*;",  # SQL comment injection
    r"base64_decode",
    r"eval\s*\(",
]

_COMPILED: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in _MALICIOUS_PATTERNS]

# Patterns that depend on syntax normalisation destroys: normalisation strips
# `<`, `>`, `=`, `/` and `;`, so tag and handler structure is only visible in
# the original text. Checked against the raw prompt.
_RAW_STRUCTURAL: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"<script[\s>]",
        r"<[^>]*\son\w+\s*=",  # `<img src=x onerror=...>`
        r"<\s*/?\s*(img|iframe|svg|object|embed|body|video|audio|form)\b",
        r"javascript\s*:",
        r"--\s*;",
        r"\beval\s*\(",
        r"\bexec\s*\(",
    ]
]

# Keyword skeletons are matched against normalised input, so the patterns below
# are written to survive that folding: no punctuation, no separator tricks.
_KEYWORD_SKELETONS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions?",
        r"ignore\s+(all\s+)?(the\s+)?(above|prior)\s+(rules?|instructions?)",
        r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above)\s+instructions?",
        r"disregard\s+(everything|all\s+of\s+(it|that))",
        r"forget\s+(everything|all\s+previous|your\s+instructions?)",
        r"you\s+are\s+now\s+(an?\s+)?unfiltered",
        r"you\s+are\s+now\s+(a\s+)?(dan|developer\s+mode|jailbroken)",
        r"do\s+anything\s+now",
        r"act\s+as\s+(if\s+)?you\s+have\s+no\s+(restrictions?|rules?|filters?)",
        r"pretend\s+(you\s+are|to\s+be)\s+(an?\s+)?(unfiltered|unrestricted)",
        r"system\s+(override|prompt|message)",
        r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions?|secrets?)",
        r"show\s+me\s+your\s+(system\s+)?prompt",
        r"repeat\s+(the\s+)?(above|everything)",
        r"set\s+(the\s+)?fare\s+to\s*(0|zero|free)",
        r"give\s+(me\s+)?free\s+(tickets?|seats?|rides?)",
        r"grant\s+(me\s+)?admin(istrator)?\s+access",
        r"bypass\s+(the\s+)?(security|guardrails?|filter|validation)",
        r"drop\s+table",
        r"union\s+select",
        r"base64\s*decode",
        r"eval\s*\(",
        r"script\s*(alert|src)",
    ]
]


# Matched against a *fully collapsed* view of the input (all separators and
# whitespace removed). Reassembly heuristics cannot reliably undo "i g n o r e"
# or "Ig-nore all prev-ious inst-ructions", but both collapse to the same
# character run, so these patterns catch any spacing scheme.
_COLLAPSED_SKELETONS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore.*previous.*instructions?",
        r"ignore.*prior.*instructions?",
        r"disregard.*instructions?",
        r"forget.*(everything|instructions?)",
        r"youarenow.*(unfiltered|dan|jailbroken|developer)",
        r"doanythingnow",
        r"reveal.*(systemprompt|yourprompt|secrets?)",
        r"showme.*(systemprompt|yourprompt)",
        r"repeat.*above",
        r"set.*fare.*to.*(0|zero|o|free)",
        r"(make|change|force|reduce|lower|adjust).*fare.*(0|zero|o|free)",
        r"grant.*free.*(tickets?|seats?|rides?|travel)",
        r"give.*free.*(tickets?|seats?|rides?)",
        r"grant.*admin",
        r"bypass.*(security|guardrail|filter|validation)",
        r"actas.*no.*restrictions?",
        r"pretend.*unfiltered",
        r"systemoverride",
        r"unionselect",
        r"base64decode",
        r"drops?table",
    ]
]


def normalize_for_matching(text: str, fold_leet: bool = True) -> str:
    """
    Folds the textual tricks that let a payload slip past a plain regex.

    Every one of these was found empirically: the original denylist was evaded
    by paraphrase, hyphenation, leetspeak, homoglyphs and zero-width characters
    (see the red-team evidence, tests G-05 to G-14). None of these transformations
    changes what a human means by the text, so folding them before matching
    closes the gap without altering the user's message.

    Steps, in order:
      1. NFKC normalises compatibility forms (fullwidth letters, ligatures).
      2. Zero-width and bidi control characters are removed, so
         "i\\u200bgnore" cannot hide a keyword.
      3. Hyphens, underscores and plus signs between two characters become
         spaces, so "ig-nore" and "ignore_all_previous" separate into words
         (step 6 rejoins the ones split mid-word).
      4. Leetspeak is mapped back to letters (1→i, 0→o, 3→e, @→a). Skipped when
         `fold_leet` is False, because "set fare to 0" must still be seen as a
         digit rather than folded into the letter "o".
      5. Cyrillic/Greek homoglyphs are mapped to their Latin lookalikes.
      6. Anything still non-alphanumeric becomes a space, and single-letter gaps
         are closed, so "Ig nore" also folds to "ignore".
    """
    if not text:
        return ""

    # 1. Compatibility normalisation.
    folded = unicodedata.normalize("NFKC", text)

    # 2. Invisible characters: zero-width, soft hyphen, bidi overrides.
    folded = "".join(
        ch
        for ch in folded
        if unicodedata.category(ch) != "Cf"
        and ch not in {"​", "‌", "‍", "⁠", "﻿", "­"}
    )

    folded = folded.lower()

    # 3. Separators smuggled *inside* a word become spaces, so "ig-nore",
    #    "ig_nore" and "ignore_all_previous" all separate into words. Step 6
    #    then rejoins the ones that were split mid-word ("ig nore" → "ignore").
    folded = re.sub(r"(?<=[a-z0-9])[-_+](?=[a-z0-9])", " ", folded)

    # 4. Leetspeak.
    if fold_leet:
        folded = _LEET.sub(lambda m: _LEET_MAP[m.group(0)], folded)

    # 5. Homoglyphs.
    folded = "".join(_HOMOGLYPHS.get(ch, ch) for ch in folded)

    # 6. Remaining punctuation to spaces, then close single-letter gaps.
    folded = re.sub(r"[^a-z0-9\s]+", " ", folded)
    folded = re.sub(r"\b(\w)\s+(?=\w\b)", r"\1", folded)
    folded = re.sub(r"\s+", " ", folded)

    return folded.strip()


def sanitize_user_input(prompt: str) -> str:
    """
    Validates user input against known adversarial patterns.

    Matching runs against a normalised copy of the input (see
    `normalize_for_matching`), so obfuscated spellings are caught. The value
    returned is the ORIGINAL stripped prompt — normalisation is only ever used
    to detect, never to rewrite what the traveller typed.

    Args:
        prompt: Raw user input string.

    Returns:
        The sanitized prompt (stripped of leading/trailing whitespace).

    Raises:
        ValueError: If a malicious pattern is detected. The event is also
            recorded in the security audit log before the exception is raised.
    """
    cleaned = prompt.strip()
    # Two views of the same input: one with leetspeak folded to letters, one
    # with digits left intact. A payload like "set fare to 0" is only visible
    # in the second view, while "1gn0re" is only visible in the first.
    haystacks = (
        normalize_for_matching(cleaned, fold_leet=True),
        normalize_for_matching(cleaned, fold_leet=False),
    )
    # Third view: every separator and space removed, which catches spacing and
    # hyphenation schemes that no reassembly rule can undo.
    collapsed = re.sub(r"[^a-z0-9]", "", haystacks[0])

    for pattern in (*_COMPILED, *_KEYWORD_SKELETONS):
        if any(pattern.search(h) for h in haystacks):
            _block(pattern, cleaned)
    for pattern in _COLLAPSED_SKELETONS:
        if pattern.search(collapsed):
            _block(pattern, cleaned)
    for pattern in _RAW_STRUCTURAL:
        if pattern.search(cleaned):
            _block(pattern, cleaned)

    return cleaned


    return cleaned


def _block(pattern: re.Pattern, cleaned: str) -> None:
    """Records the block and raises. Shared by both pattern passes."""
    log_security_event(
        "PROMPT_INJECTION_BLOCKED",
        f"pattern={pattern.pattern!r} input_length={len(cleaned)}",
    )
    raise ValueError(
        f"SECURITY_VIOLATION: Suspicious input pattern detected. "
        f"Pattern: {pattern.pattern!r}"
    )


def is_safe(prompt: str) -> bool:
    """Returns True if the prompt passes all guardrail checks."""
    try:
        sanitize_user_input(prompt)
        return True
    except ValueError:
        return False
