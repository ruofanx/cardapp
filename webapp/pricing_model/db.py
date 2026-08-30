# webapp/pricing_model/db.py
"""Dedicated SQLite data layer for the pricing prediction model.

Deliberately separate from db.py / db_postgres.py (see plan Global
Constraints): app.py reads cards via db_postgres in production but the
background-job modules (refresh_job.py, price_history_refresh.py) both
import plain `db` (SQLite) regardless — this file sidesteps that split by
following the pricecharting_cache.sqlite / ebay_cache.sqlite precedent of
one dedicated file per subsystem.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pricing_model.features import CardFeatures

DB_PATH = Path(os.environ.get("PRICING_MODEL_DB", str(Path(__file__).parent.parent / "pricing_model.sqlite")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_features (
    card_id               INTEGER PRIMARY KEY,
    canonical_rarity      TEXT NOT NULL,
    pull_scarcity         REAL NOT NULL,
    gem_rate              REAL NOT NULL,
    character_tier        TEXT NOT NULL,
    is_trainer_art        INTEGER NOT NULL DEFAULT 0,
    language              TEXT NOT NULL,
    era                   TEXT NOT NULL,
    release_date          TEXT NOT NULL,
    months_since_release  REAL NOT NULL,
    features_updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS corpus_cards (
    card_key        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    set_name        TEXT NOT NULL,
    card_number     TEXT NOT NULL,
    rarity_raw      TEXT,
    era             TEXT NOT NULL,
    language        TEXT NOT NULL DEFAULT 'english',
    release_date    TEXT NOT NULL,
    psa10_price_usd REAL,
    grade9_price_usd REAL,
    harvested_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS corpus_history (
    card_key      TEXT NOT NULL REFERENCES corpus_cards(card_key) ON DELETE CASCADE,
    month         TEXT NOT NULL,
    raw_price_usd REAL NOT NULL,
    PRIMARY KEY (card_key, month)
);

CREATE TABLE IF NOT EXISTS model_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fitted_at           TEXT NOT NULL DEFAULT (datetime('now')),
    coefficients_raw    TEXT NOT NULL,
    coefficients_psa10  TEXT NOT NULL,
    lifecycle_curve     TEXT NOT NULL,
    market_index        TEXT NOT NULL,
    psa9_fraction       REAL NOT NULL,
    residual_std_raw    REAL NOT NULL,
    residual_std_psa10  REAL NOT NULL,
    r_squared_raw       REAL NOT NULL,
    r_squared_psa10     REAL NOT NULL,
    n_cards             INTEGER NOT NULL
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# card_features
# ---------------------------------------------------------------------------

def upsert_card_features(card_id: int, f: CardFeatures) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO card_features
               (card_id, canonical_rarity, pull_scarcity, gem_rate, character_tier,
                is_trainer_art, language, era, release_date, months_since_release,
                features_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(card_id) DO UPDATE SET
                 canonical_rarity=excluded.canonical_rarity,
                 pull_scarcity=excluded.pull_scarcity,
                 gem_rate=excluded.gem_rate,
                 character_tier=excluded.character_tier,
                 is_trainer_art=excluded.is_trainer_art,
                 language=excluded.language,
                 era=excluded.era,
                 release_date=excluded.release_date,
                 months_since_release=excluded.months_since_release,
                 features_updated_at=datetime('now')""",
            (card_id, f.canonical_rarity, f.pull_scarcity, f.gem_rate, f.character_tier,
             int(f.is_trainer_art), f.language, f.era, f.release_date, f.months_since_release),
        )
        conn.commit()


def get_card_features(card_id: int) -> Optional[CardFeatures]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM card_features WHERE card_id = ?", (card_id,)
        ).fetchone()
    if not row:
        return None
    return CardFeatures(
        canonical_rarity=row["canonical_rarity"], pull_scarcity=row["pull_scarcity"],
        gem_rate=row["gem_rate"], character_tier=row["character_tier"],
        is_trainer_art=bool(row["is_trainer_art"]), language=row["language"],
        era=row["era"], release_date=row["release_date"],
        months_since_release=row["months_since_release"],
    )


# ---------------------------------------------------------------------------
# training corpus
# ---------------------------------------------------------------------------

@dataclass
class CorpusCardRow:
    card_key: str
    name: str
    set_name: str
    card_number: str
    rarity_raw: Optional[str]
    era: str
    language: str
    release_date: str
    psa10_price_usd: Optional[float] = None
    grade9_price_usd: Optional[float] = None


def upsert_corpus_card(row: CorpusCardRow) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO corpus_cards
               (card_key, name, set_name, card_number, rarity_raw, era, language,
                release_date, psa10_price_usd, grade9_price_usd, harvested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(card_key) DO UPDATE SET
                 name=excluded.name, set_name=excluded.set_name,
                 card_number=excluded.card_number, rarity_raw=excluded.rarity_raw,
                 era=excluded.era, language=excluded.language,
                 release_date=excluded.release_date,
                 psa10_price_usd=excluded.psa10_price_usd,
                 grade9_price_usd=excluded.grade9_price_usd,
                 harvested_at=datetime('now')""",
            (row.card_key, row.name, row.set_name, row.card_number, row.rarity_raw,
             row.era, row.language, row.release_date, row.psa10_price_usd, row.grade9_price_usd),
        )
        conn.commit()


def insert_corpus_history(card_key: str, points: list[tuple[str, float]]) -> None:
    with connect() as conn:
        conn.executemany(
            """INSERT INTO corpus_history (card_key, month, raw_price_usd)
               VALUES (?, ?, ?)
               ON CONFLICT(card_key, month) DO UPDATE SET raw_price_usd=excluded.raw_price_usd""",
            [(card_key, month, price) for month, price in points],
        )
        conn.commit()


def get_all_corpus_cards() -> list[CorpusCardRow]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM corpus_cards").fetchall()
    return [
        CorpusCardRow(
            card_key=r["card_key"], name=r["name"], set_name=r["set_name"],
            card_number=r["card_number"], rarity_raw=r["rarity_raw"], era=r["era"],
            language=r["language"], release_date=r["release_date"],
            psa10_price_usd=r["psa10_price_usd"], grade9_price_usd=r["grade9_price_usd"],
        )
        for r in rows
    ]


def get_corpus_history(card_key: str) -> list[tuple[str, float]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT month, raw_price_usd FROM corpus_history WHERE card_key = ? ORDER BY month",
            (card_key,),
        ).fetchall()
    return [(r["month"], r["raw_price_usd"]) for r in rows]


def get_all_corpus_history() -> dict[str, list[tuple[str, float]]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT card_key, month, raw_price_usd FROM corpus_history ORDER BY card_key, month"
        ).fetchall()
    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        out.setdefault(r["card_key"], []).append((r["month"], r["raw_price_usd"]))
    return out


# ---------------------------------------------------------------------------
# model runs
# ---------------------------------------------------------------------------

@dataclass
class ModelRun:
    id: Optional[int]
    fitted_at: str
    coefficients_raw: dict
    coefficients_psa10: dict
    lifecycle_curve: dict
    market_index: dict
    psa9_fraction: float
    residual_std_raw: float
    residual_std_psa10: float
    r_squared_raw: float
    r_squared_psa10: float
    n_cards: int


def save_model_run(run: ModelRun) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO model_runs
               (coefficients_raw, coefficients_psa10, lifecycle_curve, market_index,
                psa9_fraction, residual_std_raw, residual_std_psa10,
                r_squared_raw, r_squared_psa10, n_cards)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (json.dumps(run.coefficients_raw), json.dumps(run.coefficients_psa10),
             json.dumps(run.lifecycle_curve), json.dumps(run.market_index),
             run.psa9_fraction, run.residual_std_raw, run.residual_std_psa10,
             run.r_squared_raw, run.r_squared_psa10, run.n_cards),
        )
        conn.commit()
        return cur.lastrowid


def get_latest_model_run() -> Optional[ModelRun]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM model_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return ModelRun(
        id=row["id"], fitted_at=row["fitted_at"],
        coefficients_raw=json.loads(row["coefficients_raw"]),
        coefficients_psa10=json.loads(row["coefficients_psa10"]),
        lifecycle_curve=json.loads(row["lifecycle_curve"]),
        market_index=json.loads(row["market_index"]),
        psa9_fraction=row["psa9_fraction"],
        residual_std_raw=row["residual_std_raw"], residual_std_psa10=row["residual_std_psa10"],
        r_squared_raw=row["r_squared_raw"], r_squared_psa10=row["r_squared_psa10"],
        n_cards=row["n_cards"],
    )
