"""
Shared player-name normalization for cross-source matching.

Three sources use slightly different rules:

- `normalize_name(s)` / `name_candidates(s)` — used by lolpros + tournament
  resolution. Aggressive: pushes the last-word fragment as a candidate
  (so "FNC Razork" → "razork"). Risk of false positives on short shared
  fragments (the historical "bush" Faker/sOAZ collision) is mitigated
  by callers that gate on residency / pro flags.

- `strict_name_candidates(s)` — used by Leaguepedia matching. Same shape,
  but never pushes a fragment shorter than 5 chars and never pushes a
  bare last-word fragment from a multi-word base. Use this when the
  caller cannot disambiguate cross-region collisions.

Both produce a list of normalized strings (lowercase + alphanumeric only,
with the Riot tag stripped).
"""
import re


def normalize_name(s: str | None) -> str:
    """Strip Riot tag, lowercase, drop non-alphanum."""
    if not s:
        return ""
    s = s.split("#")[0]
    return re.sub(r"[^a-z0-9]", "", s.lower())


def name_candidates(s: str | None) -> list[str]:
    """Multi-strategy normalization for cross-source name matching.

    Generates the full base + variants stripping common prefixes (twtv,
    coach, ...) and team-tag prefixes (G2, FNC, KC NEXT, ...) and
    suffixes (academy, smurf, alt, NEXT, trailing digits).
    """
    if not s:
        return []
    base = s.split("#")[0].strip()
    out: list[str] = []
    seen: set[str] = set()

    def push(x: str) -> None:
        n = re.sub(r"[^a-z0-9]", "", x.lower())
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    push(base)
    push(re.sub(r"^(twtv|trainer|coach|sub)\s+", "", base, flags=re.I).strip())

    m = re.match(r"^([A-Z0-9]{1,5})\s+(.+)$", base)
    if m:
        push(m.group(2))
        push(m.group(2).split(" ")[-1])

    no_suffix = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", base, flags=re.I).strip()
    if no_suffix != base:
        push(no_suffix)
    if m:
        cleaned = re.sub(r"\s+(NEXT|academy|smurf|alt|main|\d+)$", "", m.group(2), flags=re.I).strip()
        push(cleaned)
        push(cleaned.split(" ")[-1])

    parts = base.split(" ")
    if len(parts) > 1:
        push(parts[-1])
    return out
