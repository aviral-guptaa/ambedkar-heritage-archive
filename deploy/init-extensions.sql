-- Extensions the archive's schema depends on.
--
-- pgvector holds the embedding vectors. Without it the migration that creates
-- the vector columns fails, so it is created before the API starts rather than
-- discovered to be missing afterwards.

CREATE EXTENSION IF NOT EXISTS vector;

-- The search fallback for a degraded installation with no vector support.
-- pgvector's own text search is used when available; this is the safety net.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
