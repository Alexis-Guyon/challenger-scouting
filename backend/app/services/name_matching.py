"""
Shared player-name normalization for cross-source matching.

A single canonical implementation lives here, with two strictness modes:

- `strict=False` (default) — aggressive. Also pushes bare last-word
  fragments (so "Hide on Bush" → ["hideonbush", "bush"]). Used by
  lolpros + tournament resolution where the short-fragment collision
  risk is mitigated by region / is_pro tiebreakers in the caller.

- `strict=True` — conservative. Pushes the full base, the team-prefix-
  stripped form, and the suffix-stripped form, but NEVER the bare
  last-word fragment of a multi-word name. Used by Leaguepedia matching
  where no second-pass disambiguation exists. Avoids the historical
  Faker/sOAZ collision via the shared "bush" fragment.

Both modes return a list of normalized strings (lowercase + alphanum only,
Riot tag stripped), in priority order.
"""
import re

# Streamer / staff prefix that should be stripped from the base before any
# matching. Frozen as a module constant so callers can reuse it.
_STREAMER_PREFIX_RE = re.compile(r"^(twtv|trainer|coach|sub)\s+", re.I)

# Team-tag prefix: 1-5 uppercase alphanumerics + space + remainder.
# Examples: "FNC Razork", "G2 Caps", "KC NEXT ADKING".
_TEAM_PREFIX_RE = re.compile(r"^([A-Z0-9]{1,5})\s+(.+)$")

# Trailing role/account-type suffix: "MyName NEXT", "Caps academy", "Bo smurf".
_TRAILING_SUFFIX_RE = re.compile(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", re.I)


def normalize_name(s: str | None) -> str:
    """Strip Riot tag, lowercase, drop non-alphanum."""
    if not s:
        return ""
    s = s.split("#")[0]
    return re.sub(r"[^a-z0-9]", "", s.lower())


def name_candidates(s: str | None, *, strict: bool = False) -> list[str]:
    """Multi-strategy normalization for cross-source name matching.

    Generates the full base + variants stripping common prefixes (twtv,
    coach, ...) and team-tag prefixes (G2, FNC, KC NEXT, ...) and
    suffixes (academy, smurf, alt, NEXT, trailing digits).

    See module docstring for `strict` semantics.
    """
    if not s:
        return []
    base = s.split("#")[0].strip()
    is_multi_word = " " in base  # only relevant in loose mode (last-word fallback)
    out: list[str] = []
    seen: set[str] = set()

    def push(x: str) -> None:
        n = re.sub(r"[^a-z0-9]", "", x.lower())
        if not n or n in seen:
            return
        seen.add(n)
        out.append(n)

    # 1. Full normalized base — always pushed
    push(base)

    # 2. Strip streamer prefix
    no_prefix = _STREAMER_PREFIX_RE.sub("", base).strip()
    if no_prefix != base:
        push(no_prefix)

    # 3. Strip team-tag prefix ("FNC Razork" → "Razork")
    m = _TEAM_PREFIX_RE.match(base)
    if m:
        push(m.group(2))
        if not strict:
            push(m.group(2).split(" ")[-1])  # bare last-word fragment

    # 4. Strip trailing suffix ("Caps academy" → "Caps")
    no_suffix = _TRAILING_SUFFIX_RE.sub("", base).strip()
    if no_suffix != base:
        push(no_suffix)

    # 5. Combination: team prefix stripped + suffix stripped
    if m:
        cleaned = _TRAILING_SUFFIX_RE.sub("", m.group(2)).strip()
        push(cleaned)
        if not strict:
            push(cleaned.split(" ")[-1])

    # 6. Last-word fallback for multi-word bases (only loose mode)
    if not strict and is_multi_word:
        push(base.split(" ")[-1])

    return out


def strict_name_candidates(s: str | None) -> list[str]:
    """Convenience wrapper — `name_candidates(s, strict=True)`."""
    return name_candidates(s, strict=True)
