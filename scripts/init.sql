-- Cleantech Quant API — Database initialisation
-- Runs automatically when the postgres container first starts.
-- Alembic migrations handle schema creation; this script sets up
-- extensions and performance tuning.

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pg_trgm for fast ILIKE searches on text fields
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Performance: increase work_mem for complex analytical queries
ALTER SYSTEM SET work_mem = '16MB';
ALTER SYSTEM SET maintenance_work_mem = '128MB';
ALTER SYSTEM SET effective_cache_size = '512MB';

-- Connection pool friendliness
ALTER SYSTEM SET max_connections = 100;
ALTER SYSTEM SET idle_in_transaction_session_timeout = '30s';

SELECT pg_reload_conf();
