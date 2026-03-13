# PostgreSQL

O projeto suporta **PostgreSQL** (recomendado para Celery com múltiplos workers) ou **SQLite** (desenvolvimento simples).

## Por que PostgreSQL?

O SQLite bloqueia o banco inteiro em escritas. Com vários jobs Celery rodando ao mesmo tempo, isso pode gerar o erro `database is locked`. O PostgreSQL suporta escritas concorrentes sem esse problema.

## Usando PostgreSQL com Docker

1. **Configure o `.env`** (opcional – há valores padrão):

   ```
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=social_automation
   ```

2. **Suba os serviços**:

   ```bash
   docker compose up -d
   ```

   O `docker-compose` define `DATABASE_URL` automaticamente para os serviços `web`, `celery`, `celery_publish` e `beat`, apontando para o PostgreSQL.

3. As migrações rodam na subida do `web` e criam as tabelas no PostgreSQL.

## Migrando dados do SQLite para PostgreSQL

Se você já usa SQLite e quer migrar os dados:

1. **Subir o PostgreSQL vazio** (primeiro uso):

   ```bash
   docker compose up -d postgres
   docker compose run --rm web python manage.py migrate --noinput
   ```

2. **Exportar do SQLite** (com o projeto configurado para SQLite – sem `DATABASE_URL` no .env):

   ```bash
   # Garanta que DATABASE_URL NÃO está no .env (ou use outro terminal sem Docker)
   python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.Permission -o backup.json
   ```

3. **Importar no PostgreSQL**:

   ```bash
   # Com DATABASE_URL apontando para o PostgreSQL (Docker)
   docker compose run --rm web python manage.py loaddata backup.json
   ```

   Ou, se estiver rodando localmente com PostgreSQL:

   ```bash
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/social_automation python manage.py loaddata backup.json
   ```

## Rodando sem Docker

Para usar PostgreSQL sem Docker (PostgreSQL instalado localmente):

1. Crie o banco: `createdb social_automation`
2. Adicione ao `.env`:

   ```
   DATABASE_URL=postgresql://postgres:SUA_SENHA@localhost:5432/social_automation
   ```

3. Rode as migrações: `python manage.py migrate`

## Voltar para SQLite

Remova ou comente `DATABASE_URL` no `.env`. O projeto voltará a usar `db.sqlite3`.
