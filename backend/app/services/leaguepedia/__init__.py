"""
Leaguepedia integration via the MediaWiki Cargo API.

Public API:
  - LeaguepediaError      — raised for unrecoverable Fandom errors
  - run_leaguepedia_sync_sync(db, with_lolpros_bulk=False) -> dict
  - run_leaguepedia_sync(db) -> coroutine[dict]   (async wrapper)

Implementation lives in two submodules:
  - .sources — every Fandom API client (Cargo + wikitext + image lookup)
               and the data-shaping helpers that turn an API response
               into a flat record dict.
  - .sync    — orchestration. Picks targets, runs the multi-pass fetch
               pipeline, builds the lookup index, and writes results to
               PlayerMeta. The only entry point external callers should
               use is `run_leaguepedia_sync_sync`.

Set FANDOM_USERNAME / FANDOM_PASSWORD in .env to authenticate against a
Fandom bot account (relaxed rate-limits, larger batch sizes). Use a bot
password, not the regular account password.
"""
from .sources import LeaguepediaError
from .sync import run_leaguepedia_sync, run_leaguepedia_sync_sync

__all__ = [
    "LeaguepediaError",
    "run_leaguepedia_sync",
    "run_leaguepedia_sync_sync",
]
