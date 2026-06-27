CREATE TABLE IF NOT EXISTS users (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    avatar_color TEXT NOT NULL DEFAULT '#3b82f6',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cards (
    id                   SERIAL PRIMARY KEY,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    set_name             TEXT,
    card_number          TEXT,
    language             TEXT NOT NULL DEFAULT 'english',
    variant              TEXT,
    condition            TEXT NOT NULL DEFAULT 'NM',
    is_graded            BOOLEAN NOT NULL DEFAULT FALSE,
    grade_company        TEXT,
    grade                REAL,
    purchase_price       REAL,
    purchase_date        TEXT,
    current_market_price REAL,
    last_priced_at       TIMESTAMPTZ,
    image_url            TEXT,
    photo_path           TEXT,
    notes                TEXT,
    product_type         TEXT NOT NULL DEFAULT 'card',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id);

CREATE TABLE IF NOT EXISTS tags (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    color        TEXT NOT NULL DEFAULT '#94a3b8',
    is_trade_tag BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_tags_user ON tags(user_id);

CREATE TABLE IF NOT EXISTS card_tags (
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (card_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_card_tags_card ON card_tags(card_id);
CREATE INDEX IF NOT EXISTS idx_card_tags_tag  ON card_tags(tag_id);

CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL PRIMARY KEY,
    card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    price_usd   REAL NOT NULL,
    source      TEXT,
    source_url  TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_history_card_time
    ON price_history(card_id, recorded_at);
