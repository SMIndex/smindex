"""Per-cache hit/miss counters (optimization pass 2026-09-24), exposed on
GET /health as `cache_stats`. In-process, since the last restart."""
from collections import defaultdict

_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])


def record(name: str, hit: bool) -> None:
    _counts[name][0 if hit else 1] += 1


def snapshot() -> dict:
    return {k: {"hits": h, "misses": m, "hit_rate": round(h / (h + m), 3) if h + m else None}
            for k, (h, m) in sorted(_counts.items())}
