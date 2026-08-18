-- Docker runs this only when initializing a new local PostgreSQL volume.
-- The separate database protects development experiments from pytest cleanup.
SELECT 'CREATE DATABASE evalguard_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'evalguard_test') \gexec
