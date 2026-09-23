from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    DATABASE_URL: str = "mysql+aiomysql://root:@localhost:3306/perpl"
    JWT_SECRET: str = "change-me-in-production"
    JWT_EXPIRY_HOURS: int = 24
    PERPL_REST_URL: str = "https://app.perpl.xyz"
    PERPL_WS_URL: str = "wss://app.perpl.xyz/ws/v1"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174"
    ETHERSCAN_API_KEY: str = ""
    MONAD_RPC_URL: str = "https://rpc.monad.xyz"
    PERPL_CONTRACT: str = "0x34b6552d57a35a1d042ccae1951bd1c370112a6f"
    PERPL_CHAIN_ID: int = 143
    WHALE_THRESHOLD_USD: float = 500
    HEATMAP_INTERVAL_SECONDS: int = 10
    # Copy Trading: master gate for LIVE copy ORDER PLACEMENT (real Perpl orders).
    # Env-controlled (set COPY_LIVE_ENABLED=true in prod/staging .env to allow real
    # copy orders). Default False so no real copy order is ever placed unless turned
    # on deliberately. This gates ORDER EXECUTION only — configuring a live_manual
    # subscription (saved risk settings) is allowed regardless. Terminal manual
    # trading is unaffected.
    COPY_LIVE_ENABLED: bool = False
    # Primary copy product mode: live_manual (user confirms each copy -> real order),
    # live_auto (backend auto-executes — NOT implemented yet, refused), or paper
    # (sandbox simulation only). live_manual is the primary product.
    COPY_MODE: str = "live_manual"
    # OPTIONAL internal private-beta allowlist — NOT the normal gate. Only enforced
    # when COPY_REQUIRE_INTERNAL_ALLOWLIST=true. Normal live-copy eligibility is
    # decided by Perpl trading approval (on-chain Perpl account), not by this list.
    # Comma-separated wallet addresses (normalized lowercase).
    COPY_LIVE_ALLOWED_WALLETS: Annotated[list[str], NoDecode] = []
    # When true, additionally require the wallet to be in COPY_LIVE_ALLOWED_WALLETS
    # (private beta/testing). Default false — any Perpl-approved trader may live-copy.
    COPY_REQUIRE_INTERNAL_ALLOWLIST: bool = False

    # LIVE COPY EXECUTION allowlist (staged rollout, 2026-08-14): the wallets
    # allowed to place REAL copy orders through the gate ladder. FAIL-CLOSED:
    # an EMPTY list blocks EVERYONE (live execution is allowlist-only until the
    # owner decides on full-user enablement). AND-ed with COPY_LIVE_ENABLED —
    # the global kill switch still disables everything instantly.
    # Comma-separated wallet addresses (normalized lowercase).
    LIVE_COPY_ALLOWLIST: Annotated[list[str], NoDecode] = []

    @field_validator("COPY_LIVE_ALLOWED_WALLETS", "LIVE_COPY_ALLOWLIST", "STRATEGY_LIVE_ALLOWLIST", mode="before")
    @classmethod
    def _parse_allowlist(cls, v):
        """Accept comma-separated string OR list; trim + lowercase + drop empties."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [w.strip().lower() for w in v.split(",") if w.strip()]
        if isinstance(v, (list, tuple)):
            return [str(w).strip().lower() for w in v if str(w).strip()]
        return v
    TELEGRAM_BOT_TOKEN: str = ""
    LOG_LEVEL: str = "INFO"
    # Smart-money analytics (Phase 1): gates the /api/analytics router AND the
    # position-sweep lifespan task. Default true; flip to false to gate later.
    ANALYTICS_ENABLED: bool = True
    # Admin wallets (phase 7): comma-separated addresses allowed to hide/unhide
    # trader profiles. Enforced SERVER-SIDE via require_admin (JWT wallet must be
    # in this set) — any client-side affordance is cosmetic only.
    ADMIN_ADDRESSES: str = ""

    @property
    def admin_address_set(self) -> set[str]:
        return {a.strip().lower() for a in self.ADMIN_ADDRESSES.split(",") if a.strip()}

    # --- Strategy engine (docs/strategies, Phase 1) ------------------------
    # Master switch for the perpl-strategy-worker process. Default FALSE.
    # The worker checks this every cycle; `systemctl stop perpl-strategy-worker`
    # is the documented hard stop. Phase 1 runs the DATA LAYER only when true —
    # nothing trades until Phase 2/3 (owner-gated).
    STRATEGY_ENGINE_ENABLED: bool = False
    # Wallets allowed to place REAL strategy orders — FAIL-CLOSED, empty blocks
    # everyone. Unused in Phase 1 (no execution adapter exists); defined now so
    # effective_mode can be computed. Comma-separated, normalised lowercase.
    STRATEGY_LIVE_ALLOWLIST: Annotated[list[str], NoDecode] = []
    # Assets the engine covers (doc 00 §1: BTC and ETH only at launch).
    STRATEGY_ASSETS: str = "BTC,ETH"
    # Paper account equity for RiskEngine sizing in paper mode (doc default $1,000).
    STRATEGY_PAPER_EQUITY_USD: float = 1000.0
    # 0xArchive key (spec v1.1 Part B / D-69): liquidation history + weekly
    # live_coverage refresh. Empty = archive loader is a no-op (never fabricates).
    OXARCHIVE_API_KEY: str = ""
    # D-79: background wallet-insights refresh (full leader_trade_events scan);
    # false = page serves stored rows only.
    WALLET_INSIGHTS_ENABLED: bool = True
    # Shared HL info client (Wallet Explorer Part 1): soft weight ceiling per
    # rolling 60s window. Venue hard limit is 1200/min/IP; 1000 leaves headroom
    # that only critical-class (copy/live) requests may borrow.
    HL_WEIGHT_CEILING: int = 1000
    # Envio HyperSync token (Wallet Explorer Part 4 indexer). Declared here so
    # the API's pydantic Settings doesn't crash-loop on the .env key (the
    # worker itself reads os.environ). Empty = indexer idles, PENDING owner.
    ENVIO_API_TOKEN: str = ""

    @property
    def strategy_assets_list(self) -> list[str]:
        return [a.strip().upper() for a in self.STRATEGY_ASSETS.split(",") if a.strip()]

    def strategy_live_allowlist_ok(self, wallet: str) -> bool:
        """Fail-closed: empty allowlist blocks everyone (mirrors live-copy)."""
        return bool(wallet) and wallet.lower() in self.STRATEGY_LIVE_ALLOWLIST

    # MCP server hardening
    MCP_TOKEN_TTL_DAYS: int = 90
    MCP_MAX_TOKENS_PER_USER: int = 10
    MCP_RATE_LIMIT_PER_MIN: int = 60
    MCP_STAGE_TTL_SECONDS: int = 300
    # Comma-separated allowlist for HTTP/SSE Origin header. Empty = allow any
    # *or no Origin* (Claude Desktop / Code launch via stdio or no-Origin requests).
    # Public site URL used for Telegram deep links, MCP deep links and OG card
    # urls. Declared here because pydantic Settings refuses unknown .env keys
    # (it crash-loops the API on boot), and this key now lives in prod .env.
    PERPL_TERMINAL_URL: str = "https://smindex.xyz"

    # Comma-separated CORS allowlist for the MCP endpoint. Any additional host
    # that must keep working (a legacy domain still serving the same app, a
    # staging origin) is added via the env var, not hardcoded here.
    MCP_ALLOWED_ORIGINS: str = ("https://smindex.xyz,https://www.smindex.xyz,"
                                "https://claude.ai,https://www.claude.ai")

    def is_live_copy_enabled(self) -> bool:
        """Global live-manual switch: live enabled AND mode is live_manual.
        (Per-wallet eligibility = Perpl approval, checked in live_orders.)"""
        return self.COPY_LIVE_ENABLED and self.COPY_MODE == "live_manual"

    def live_allowlist_ok(self, wallet: str) -> bool:
        """Staged-rollout gate for live copy EXECUTION. FAIL-CLOSED: empty
        allowlist means nobody may place a real copy order."""
        return bool(wallet) and wallet.lower() in self.LIVE_COPY_ALLOWLIST

    def internal_allowlist_ok(self, wallet: str) -> bool:
        """Internal private-beta allowlist policy. Returns True (no restriction)
        unless COPY_REQUIRE_INTERNAL_ALLOWLIST=true, in which case the wallet must
        be in COPY_LIVE_ALLOWED_WALLETS. This is NOT the normal gate."""
        if not self.COPY_REQUIRE_INTERNAL_ALLOWLIST:
            return True
        return bool(wallet) and wallet.lower() in self.COPY_LIVE_ALLOWED_WALLETS

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
