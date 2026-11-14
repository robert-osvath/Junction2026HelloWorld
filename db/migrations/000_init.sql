-- runs once on first `docker compose up` (fresh volume); tables are created by the app on startup
-- test DB for tests/test_audit.py (DB_URL_TEST)
CREATE DATABASE IF NOT EXISTS aimo_test;
GRANT ALL ON aimo_test.* TO 'aimo'@'%';

-- audit log is append-only evidence: the app user must not purge its history or drop the table
REVOKE DELETE HISTORY, DROP ON aimo.* FROM 'aimo'@'%';
