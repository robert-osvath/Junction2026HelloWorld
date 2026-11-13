-- test DB for tests/test_audit.py (DB_URL_TEST); tables are created by the app on startup
CREATE DATABASE IF NOT EXISTS aimo_test;
GRANT ALL ON aimo_test.* TO 'aimo'@'%';
