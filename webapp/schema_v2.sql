-- Accounts table — one row per Supabase Auth user
CREATE TABLE IF NOT EXISTS accounts (
    id            UUID PRIMARY KEY,          -- matches Supabase auth.users.id
    email         TEXT NOT NULL UNIQUE,
    plan          TEXT NOT NULL DEFAULT 'free',  -- 'free' | 'pro'
    trial_ends_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add account_id to users (profiles)
ALTER TABLE users ADD COLUMN IF NOT EXISTS account_id UUID REFERENCES accounts(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_users_account ON users(account_id);

-- Scan usage — one row per account per calendar month
CREATE TABLE IF NOT EXISTS scan_usage (
    id          SERIAL PRIMARY KEY,
    account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    month       TEXT NOT NULL,             -- 'YYYY-MM'
    scan_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, month)
);

CREATE INDEX IF NOT EXISTS idx_scan_usage_account ON scan_usage(account_id);
