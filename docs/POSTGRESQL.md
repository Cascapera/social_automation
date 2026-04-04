# PostgreSQL

The project supports **PostgreSQL** (recommended for Celery with multiple workers) or **SQLite** (simple development).

## Why PostgreSQL?

SQLite locks the entire database on writes. With several Celery jobs running at once, you may hit `database is locked`. PostgreSQL supports concurrent writes without that issue.

## Using PostgreSQL with Docker

1. **Configure `.env`** (optional — defaults exist):

   ```
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=social_automation
   ```

2. **Start services:**

   ```bash
   docker compose up -d
   ```

   `docker-compose` sets `DATABASE_URL` automatically for `web`, `celery`, `celery_publish`, and `beat`, pointing at PostgreSQL.

3. Migrations run on `web` startup and create tables in PostgreSQL.

## Migrating data from SQLite to PostgreSQL

If you already use SQLite and want to migrate:

1. **Start empty PostgreSQL** (first use):

   ```bash
   docker compose up -d postgres
   docker compose run --rm web python manage.py migrate --noinput
   ```

2. **Export from SQLite** (with the project configured for SQLite — no `DATABASE_URL` in `.env`):

   ```bash
   # Ensure DATABASE_URL is NOT in .env (or use another terminal without Docker)
   python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.Permission -o backup.json
   ```

3. **Import into PostgreSQL:**

   ```bash
   # With DATABASE_URL pointing at PostgreSQL (Docker)
   docker compose run --rm web python manage.py loaddata backup.json
   ```

   Or, if running locally against PostgreSQL:

   ```bash
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/social_automation python manage.py loaddata backup.json
   ```

## Running without Docker

To use PostgreSQL without Docker (local install):

1. Create the database: `createdb social_automation`
2. Add to `.env`:

   ```
   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/social_automation
   ```

3. Run migrations: `python manage.py migrate`

## Switching back to SQLite

Remove or comment out `DATABASE_URL` in `.env`. The project will use `db.sqlite3` again.
