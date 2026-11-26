"""Write database/schema.sql (SQLite dialect) from the SQLAlchemy models."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from database.models import Base  # noqa: E402

engine = create_engine("sqlite://")
out = ["-- BharatTrade AI schema (generated from database/models.py; SQLite dialect).",
       "-- PostgreSQL: set DATABASE_URL and the app creates the equivalent tables automatically.", ""]
for table in Base.metadata.sorted_tables:
    out.append(str(CreateTable(table).compile(engine)).strip() + ";")
    for idx in table.indexes:
        out.append(str(CreateIndex(idx).compile(engine)).strip() + ";")
    out.append("")
out.append("-- Candle cache (data/cache.py, separate file storage/candle_cache.sqlite):")
out.append("-- CREATE TABLE candles (symbol TEXT, interval TEXT, ts INTEGER, open REAL, high REAL, low REAL, "
           "close REAL, volume REAL, PRIMARY KEY (symbol, interval, ts));")
Path(__file__).resolve().parent.parent.joinpath("database", "schema.sql").write_text("\n".join(out), encoding="utf-8")
print("wrote database/schema.sql")
