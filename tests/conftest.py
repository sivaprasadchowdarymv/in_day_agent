import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# never use real credentials in tests
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
    os.environ.pop(k, None)

from config.settings import Settings  # noqa: E402
from data.market_hours import TZ  # noqa: E402


@pytest.fixture(autouse=True)
def memory_db(tmp_path):
    from database import db

    db.init_db(f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}")
    yield


@pytest.fixture
def settings():
    s = Settings()
    s.app.data_provider = "simulated"
    return s


@pytest.fixture
def anchor():
    return datetime(2026, 11, 24, 13, 7, tzinfo=TZ)  # a Tuesday


@pytest.fixture
def sim(anchor):
    from brokers.simulated import SimulatedProvider

    return SimulatedProvider(anchor=anchor, unknown_symbols={"FAKESYM"})


def make_session(day: str, closes, vols=None, interval_min: int = 5, spread: float = 0.2) -> pd.DataFrame:
    """Build candles for one session from a list of closes (open = prev close)."""
    closes = np.asarray(closes, float)
    n = len(closes)
    idx = pd.date_range(pd.Timestamp(f"{day} 09:15", tz=TZ), periods=n, freq=f"{interval_min}min")
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    vols = np.full(n, 10_000.0) if vols is None else np.asarray(vols, float)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)
