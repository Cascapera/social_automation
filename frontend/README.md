# Frontend — Social Automation

React app for the full video automation workflow.

## Prerequisites

- **Node.js** (v18+) — [Download at nodejs.org](https://nodejs.org)

## Install

```bash
cd frontend
npm install
```

## Development

1. **Backend** (one terminal):
   ```bash
   python manage.py runserver
   ```

2. **Frontend** (another terminal):
   ```bash
   cd frontend
   npm run dev
   ```

3. Open **http://localhost:5173**

Vite proxies `/api` and `/media` to Django at `http://127.0.0.1:8000`.

## Production build

```bash
npm run build
```

Output is in `frontend/dist/`. To serve with Django, configure `STATICFILES_DIRS` and routes.
