-- Phase O1 / section 48: least-privilege database roles.
--
--   atp_migrator  owns the schema; the only role that may CREATE/ALTER/DROP. Used by
--                 `alembic upgrade` (MIGRATION_DATABASE_URL) and the migration guard.
--   atp_app       what the API and worker connect as (DATABASE_URL): SELECT/INSERT/UPDATE/DELETE
--                 on every table and sequence, nothing else. A compromised app process cannot drop
--                 or alter tables, create roles, or read other databases.
--
-- Run once as the Postgres superuser (or the database owner) against the platform database:
--     psql "$SUPERUSER_URL" -v app_password="'<strong password>'" -v migrator_password="'<strong password>'" -f scripts/db_roles.sql
-- Then point MIGRATION_DATABASE_URL at atp_migrator and DATABASE_URL at atp_app, run
-- `python scripts/migrate_guard.py --force` once (it re-owns existing tables), and restart.
-- Idempotent: safe to re-run after adding tables (default privileges cover future ones too).

\set ON_ERROR_STOP on

-- psql interpolates :'var' only in top-level statements, not inside DO blocks, so the roles are
-- created with \gexec and their passwords set with plain ALTER ROLE statements.
SELECT 'CREATE ROLE atp_migrator LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atp_migrator') \gexec
ALTER ROLE atp_migrator WITH LOGIN PASSWORD :'migrator_password';
SELECT 'CREATE ROLE atp_app LOGIN NOCREATEDB NOCREATEROLE NOSUPERUSER NOINHERIT' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atp_app') \gexec
ALTER ROLE atp_app WITH LOGIN PASSWORD :'app_password' NOCREATEDB NOCREATEROLE NOSUPERUSER;

-- The migrator owns the database and the public schema, so alembic can create anything.
ALTER DATABASE :"DBNAME" OWNER TO atp_migrator;
GRANT ALL ON SCHEMA public TO atp_migrator;
ALTER SCHEMA public OWNER TO atp_migrator;

-- Existing objects (a database that predates this script) move to the migrator.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO atp_migrator', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO atp_migrator', r.sequencename);
  END LOOP;
END $$;

-- The app: DML only, on what exists now and on whatever the migrator creates later.
GRANT CONNECT ON DATABASE :"DBNAME" TO atp_app;
GRANT USAGE ON SCHEMA public TO atp_app;
REVOKE CREATE ON SCHEMA public FROM atp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO atp_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO atp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE atp_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO atp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE atp_migrator IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO atp_app;
-- Nobody but the migrator may create objects in public (PostgreSQL 15+ already defaults to this).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
