"""config/models.yaml loader (doc 10 §7). Cached; falls back to the doc defaults
when the file is missing so a bad deploy cannot silently change thresholds."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"

DEFAULT_COMMON = {
    "assets": ["BTC", "ETH"],
    "mode": "paper",
    "conviction_skip_below": 0.55,
    "conviction_full_from": 0.70,
    "exit_threshold": 2,
    "dead_trade_hold_multiple": 1.5,
    "partial_at_t1_pct": 40,
    "learning_enabled": False,
    "learning_start_after_days": 60,
}
MODEL_IDS = ("M1", "M2", "M3", "M4", "M5", "M6")


@lru_cache(maxsize=1)
def load() -> dict:
    try:
        data = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("models.yaml missing at %s — using doc defaults", MODELS_YAML)
        data = {}
    common_ = {**DEFAULT_COMMON, **(data.get("common") or {})}
    models = data.get("models") or {}
    for m in MODEL_IDS:
        models.setdefault(m, {})
        models[m].setdefault("mode", common_["mode"])
        models[m].setdefault("enabled", True)
    return {"common": common_, "models": models}


def common() -> dict:
    return load()["common"]


def model_cfg(model: str) -> dict:
    return load()["models"].get(model, {"mode": "paper", "enabled": True})


def assets() -> list[str]:
    return list(common().get("assets") or ["BTC", "ETH"])
