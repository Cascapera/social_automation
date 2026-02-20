# Frontend - Social Automation

Aplicação React para o fluxo completo de automação de vídeos.

## Pré-requisitos

- **Node.js** (v18+) - [Baixar em nodejs.org](https://nodejs.org)

## Instalação

```bash
cd frontend
npm install
```

## Desenvolvimento

1. **Backend** (em um terminal):
   ```bash
   python manage.py runserver
   ```

2. **Frontend** (em outro terminal):
   ```bash
   cd frontend
   npm run dev
   ```

3. Acesse: **http://localhost:5173**

O Vite faz proxy das requisições `/api` e `/media` para o Django em `http://127.0.0.1:8000`.

## Build para produção

```bash
npm run build
```

Os arquivos estarão em `frontend/dist/`. Para servir junto ao Django, configure o `STATICFILES_DIRS` e as rotas.
