-- Postgres schema for SoftMouse mirror
-- Run via: psql -h <host> -U <user> -d <db> -f pg_schema.sql

CREATE TABLE IF NOT EXISTS mice (
    rfid TEXT PRIMARY KEY,
    softmouse_id TEXT UNIQUE,
    sex TEXT,
    dob DATE,
    strain TEXT,
    status TEXT,
    cage_id TEXT,
    genotype_json JSONB,
    notes TEXT,
    source TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cages (
    cage_id TEXT PRIMARY KEY,
    room TEXT,
    rack TEXT,
    status TEXT,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS matings (
    mating_id TEXT PRIMARY KEY,
    sire_rfid TEXT REFERENCES mice(rfid),
    dam_rfid TEXT REFERENCES mice(rfid),
    setup_date DATE,
    end_date DATE,
    status TEXT,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS litters (
    litter_id TEXT PRIMARY KEY,
    mating_id TEXT REFERENCES matings(mating_id),
    dob DATE,
    wean_date DATE,
    count INTEGER,
    status TEXT,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- History of cage assignments (optional extension)
CREATE TABLE IF NOT EXISTS cage_history (
    id BIGSERIAL PRIMARY KEY,
    rfid TEXT REFERENCES mice(rfid),
    cage_id TEXT,
    start_date DATE,
    end_date DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Materialized view denormalizing mouse data
CREATE MATERIALIZED VIEW IF NOT EXISTS mouse_full AS
SELECT
    m.rfid,
    m.softmouse_id,
    m.sex,
    m.dob,
    m.strain,
    m.status,
    m.cage_id,
    m.genotype_json,
    m.notes,
    m.source,
    m.updated_at,
    (
        SELECT jsonb_agg(jsonb_build_object('cage_id', ch.cage_id, 'start_date', ch.start_date, 'end_date', ch.end_date) ORDER BY ch.start_date DESC)
        FROM cage_history ch WHERE ch.rfid = m.rfid
    ) AS cage_history,
    (
        SELECT jsonb_agg(jsonb_build_object('locus', g->>'locus', 'genotype', g->>'genotype'))
        FROM jsonb_array_elements(coalesce(m.genotype_json,'[]'::jsonb)) g
    ) AS genotypes
FROM mice m;

CREATE INDEX IF NOT EXISTS idx_mouse_full_rfid ON mouse_full(rfid);

-- Refresh helper function (optional convenience)
CREATE OR REPLACE FUNCTION refresh_mouse_full() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mouse_full;
END; $$ LANGUAGE plpgsql;
