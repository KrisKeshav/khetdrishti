"""Database tables (SQLAlchemy Core). SQLite by default; set DATABASE_URL to Postgres to persist on hosts
with ephemeral disks. Everything stored here originates from live APIs or from users — never seeded fakes."""
import json
from contextlib import contextmanager
import threading
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine, event, insert, select, update, delete)

from app.config import DATA_DIR, settings

DATA_DIR.mkdir(parents=True, exist_ok=True)
IS_SQLITE = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if IS_SQLITE else {},
    pool_pre_ping=True,
)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL: readers never block the writer. busy_timeout: a writer waits for the lock instead of failing.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

# SQLite allows one writer at a time; our parallel fetch threads take turns here instead of colliding
# ("database is locked" under load was a real bug found by stress-testing).
_write_lock = threading.RLock()


@contextmanager
def write():
    """Every write transaction goes through here."""
    with _write_lock, engine.begin() as conn:
        yield conn


meta = MetaData()

# Last-known-good copy of every external API response, keyed by request. Lets us serve labelled
# "stale" data when a provider is down instead of crashing or inventing numbers.
cache = Table(
    "cache", meta,
    Column("key", String(512), primary_key=True),
    Column("source", String(128)),
    Column("payload", Text),
    Column("fetched_at", DateTime(timezone=True)),
)

plots = Table(
    "plots", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120)),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("crop", String(40), nullable=False),
    Column("sowing_date", String(10)),          # ISO date or NULL
    Column("language", String(8), default="hi"),
    Column("telegram_chat_id", String(64)),
    Column("created_at", DateTime(timezone=True)),
)

alerts = Table(
    "alerts", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("plot_id", Integer, nullable=False),
    Column("risk_id", String(64), nullable=False),
    Column("severity", String(10)),
    Column("title", String(200)),
    Column("detail", Text),
    Column("action", Text),
    Column("evidence", Text),                   # JSON list
    Column("for_date", String(10)),             # date the alert was generated for (dedupe key)
    Column("notified", Boolean, default=False),
    Column("created_at", DateTime(timezone=True)),
    UniqueConstraint("plot_id", "risk_id", "for_date", name="uq_alert_day"),
)

# Every mandi record we fetch is kept, so price trends grow from real observations over time.
mandi_snapshots = Table(
    "mandi_snapshots", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("arrival_date", String(10)),         # ISO yyyy-mm-dd
    Column("state", String(64)),
    Column("district", String(64)),
    Column("market", String(128)),
    Column("commodity", String(96)),
    Column("variety", String(96)),
    Column("grade", String(32)),
    Column("min_price", Float),
    Column("max_price", Float),
    Column("modal_price", Float),
    Column("fetched_at", DateTime(timezone=True)),
    UniqueConstraint("arrival_date", "market", "commodity", "variety", "grade", name="uq_mandi_row"),
)

agent_runs = Table(
    "agent_runs", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True)),
    Column("plot", Text),
    Column("question", Text),
    Column("language", String(8)),
    Column("answer", Text),
    Column("verified", Boolean),
    Column("tool_calls", Integer),
    Column("model", String(96)),
    Column("latency_ms", Integer),
)

meta.create_all(engine)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite drops tzinfo on read; everything we write is UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- cache helpers ----------

def cache_get(key: str):
    with engine.connect() as c:
        row = c.execute(select(cache).where(cache.c.key == key)).first()
    if not row:
        return None
    return {"payload": json.loads(row.payload), "fetched_at": _aware(row.fetched_at), "source": row.source}


def cache_put(key: str, source: str, payload) -> datetime:
    ts = now_utc()
    body = json.dumps(payload)
    with write() as c:
        if c.execute(select(cache.c.key).where(cache.c.key == key)).first():
            c.execute(update(cache).where(cache.c.key == key).values(payload=body, fetched_at=ts, source=source))
        else:
            c.execute(insert(cache).values(key=key, source=source, payload=body, fetched_at=ts))
    return ts


# ---------- plots ----------

def list_plots() -> list[dict]:
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(select(plots).order_by(plots.c.id))]


def get_plot(plot_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(plots).where(plots.c.id == plot_id)).first()
    return dict(row._mapping) if row else None


def add_plot(**values) -> int:
    with write() as c:
        res = c.execute(insert(plots).values(created_at=now_utc(), **values))
        return res.inserted_primary_key[0]


def delete_plot(plot_id: int) -> None:
    with write() as c:
        c.execute(delete(alerts).where(alerts.c.plot_id == plot_id))
        c.execute(delete(plots).where(plots.c.id == plot_id))


# ---------- alerts ----------

def save_alert(plot_id: int, risk: dict, for_date: str) -> bool:
    """Insert an alert; returns False if the same risk was already raised for this plot today."""
    with write() as c:
        exists = c.execute(select(alerts.c.id).where(
            alerts.c.plot_id == plot_id, alerts.c.risk_id == risk["id"], alerts.c.for_date == for_date)).first()
        if exists:
            return False
        c.execute(insert(alerts).values(
            plot_id=plot_id, risk_id=risk["id"], severity=risk["severity"], title=risk["title"],
            detail=risk["detail"], action=risk["action"], evidence=json.dumps(risk["evidence"]),
            for_date=for_date, notified=False, created_at=now_utc()))
    return True


def mark_notified(plot_id: int, risk_id: str, for_date: str) -> None:
    with write() as c:
        c.execute(update(alerts).where(alerts.c.plot_id == plot_id, alerts.c.risk_id == risk_id,
                                       alerts.c.for_date == for_date).values(notified=True))


def recent_alerts(limit: int = 50) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(alerts, plots.c.name, plots.c.crop)
                         .join(plots, plots.c.id == alerts.c.plot_id)
                         .order_by(alerts.c.created_at.desc()).limit(limit))
        out = []
        for r in rows:
            d = dict(r._mapping)
            d["evidence"] = json.loads(d["evidence"] or "[]")
            out.append(d)
        return out


# ---------- mandi snapshots ----------

def save_mandi_rows(rows: list[dict]) -> int:
    """Upsert-ignore mandi records. Returns number of new rows."""
    added = 0
    ts = now_utc()
    with write() as c:
        for r in rows:
            key = (mandi_snapshots.c.arrival_date == r["arrival_date"]) & (mandi_snapshots.c.market == r["market"]) \
                & (mandi_snapshots.c.commodity == r["commodity"]) & (mandi_snapshots.c.variety == r["variety"]) \
                & (mandi_snapshots.c.grade == r["grade"])
            if c.execute(select(mandi_snapshots.c.id).where(key)).first():
                continue
            c.execute(insert(mandi_snapshots).values(fetched_at=ts, **r))
            added += 1
    return added


def mandi_history(state: str, commodity: str, since_iso: str) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(mandi_snapshots).where(
            mandi_snapshots.c.state == state, mandi_snapshots.c.commodity == commodity,
            mandi_snapshots.c.arrival_date >= since_iso).order_by(mandi_snapshots.c.arrival_date))
        return [dict(r._mapping) for r in rows]


def log_agent_run(**values) -> None:
    with write() as c:
        c.execute(insert(agent_runs).values(created_at=now_utc(), **values))
