import asyncio

import httpx

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PerplClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.PERPL_REST_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=10.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get_json(self, path: str, *, retries: int = 3) -> dict:
        """GET with 429-aware exponential backoff (spec requires backoff on 429).
        Honors Retry-After when present; otherwise 0.5s, 1s, 2s."""
        client = await self._get_client()
        delay = 0.5
        for attempt in range(retries + 1):
            resp = await client.get(path)
            if resp.status_code == 429 and attempt < retries:
                ra = resp.headers.get("retry-after")
                wait = float(ra) if (ra and ra.replace(".", "", 1).isdigit()) else delay
                logger.warning("Perpl 429 on %s — backing off %.1fs (attempt %d)", path, wait, attempt + 1)
                await asyncio.sleep(wait)
                delay *= 2
                continue
            resp.raise_for_status()
            return resp.json()
        # Exhausted retries on 429
        resp.raise_for_status()
        return resp.json()

    async def get_context(self) -> dict:
        return await self._get_json("/api/v1/pub/context")

    async def get_markets(self) -> list[dict]:
        ctx = await self.get_context()
        return ctx.get("markets", [])

    async def get_market(self, market_id: int) -> dict | None:
        ctx = await self.get_context()
        for m in ctx.get("markets", []):
            if m.get("id") == market_id:
                return m
        return None

    async def get_leaderboard(
        self, period: str = "all", sorting: str = "pnl"
    ) -> dict:
        """Fetch live leaderboard from Perpl API.

        Args:
            period: "all" or "day"
            sorting: "pnl" or "vol"

        Returns:
            Raw response with 'at' (timestamp) and 'd' (trader list).
            Each trader: i=rank, a=address, p=pnl_raw, r=roi_hundredths, v=volume_raw.
            PnL/Volume: divide by 1e6 for USD. ROI: divide by 100 for percent.
        """
        return await self._get_json(f"/api/v1/trading/leaderboard/{period}/{sorting}")

    async def get_leaderboard_parsed(
        self, period: str = "all", sorting: str = "pnl"
    ) -> list[dict]:
        """Fetch and parse leaderboard into clean format."""
        raw = await self.get_leaderboard(period, sorting)
        traders = []
        for entry in raw.get("d", []):
            pnl_raw = int(entry.get("p", 0))
            vol_raw = int(entry.get("v", 0))
            roi_raw = entry.get("r", 0)
            traders.append({
                "rank": entry.get("i", 0),
                "wallet_address": entry.get("a", ""),
                "pnl_total": pnl_raw / 1e6,
                "roi": roi_raw / 100,
                "volume": vol_raw / 1e6,
            })
        return traders


# Module-level singleton
perpl_client = PerplClient()
