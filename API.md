# API Social Automation

## Autenticação

### Registrar usuário
```
POST /api/register/
Content-Type: application/json

{
  "username": "usuario",
  "password": "senha123",
  "email": "email@exemplo.com"  // opcional
}
```

### Obter token JWT
```
POST /api/auth/token/
Content-Type: application/json

{
  "username": "usuario",
  "password": "senha123"
}

Resposta:
{
  "access": "eyJ...",
  "refresh": "eyJ..."
}
```

### Usar o token
```
Authorization: Bearer <access_token>
```

---

## Fluxo completo (uma página)

### 1. Listar marcas (para selecionar no upload)
```
GET /api/brands/
```

### 2. Upload do vídeo
```
POST /api/sources/
Content-Type: multipart/form-data

brand: <id da marca>
title: "Nome do vídeo"
file: <arquivo .mp4>
```

### 3. Configurar cortes (em lote)
```
POST /api/cuts/
Content-Type: application/json

{
  "source": <id do source>,
  "cuts": [
    {"name": "Intro", "start_tc": "00:00:00", "end_tc": "00:00:10"},
    {"name": "Corte 1", "start_tc": "00:01:00", "end_tc": "00:02:00"}
  ]
}
```

Ou criar um corte por vez:
```
POST /api/cuts/
{
  "source": <id>,
  "name": "Corte 1",
  "start_tc": "00:01:00",
  "end_tc": "00:02:00"
}
```

### 4. Listar intro/outro (opcional)
```
GET /api/brand-assets/?brand=<id>&asset_type=INTRO
GET /api/brand-assets/?brand=<id>&asset_type=OUTRO
```

### 5. Criar job
```
POST /api/jobs/
Content-Type: application/json

{
  "cut_ids": [1, 2, 3],
  "target_platforms": ["IG", "TT", "YT"],
  "make_vertical": true,
  "intro_asset": null,
  "outro_asset": null,
  "transition": "fade",
  "transition_duration": 0.5
}
```

### 6. Enfileirar processamento
```
POST /api/jobs/<id>/run/
```

### 7. Verificar status e link do resultado
```
GET /api/jobs/<id>/

Resposta (quando DONE):
{
  "id": 1,
  "status": "DONE",
  "progress": 100,
  "output_url": "http://localhost:8000/media/exports/job_1.mp4",
  ...
}
```

### 8. Agendar postagem
```
POST /api/scheduled-posts/
Content-Type: application/json

{
  "job": <id do job>,
  "platforms": ["IG", "TT"],
  "scheduled_at": "2025-02-20T14:00:00Z"
}
```

---

## Endpoints disponíveis

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| POST | /api/register/ | Registrar usuário |
| POST | /api/auth/token/ | Obter token JWT |
| POST | /api/auth/token/refresh/ | Renovar token |
| GET | /api/brands/ | Listar marcas |
| GET/POST | /api/sources/ | Listar / upload vídeos |
| GET/POST | /api/cuts/ | Listar / criar cortes |
| GET/POST | /api/jobs/ | Listar / criar jobs |
| POST | /api/jobs/<id>/run/ | Enfileirar job |
| GET/POST | /api/scheduled-posts/ | Listar / agendar postagens |
| GET | /api/brand-assets/ | Listar assets (intro/outro) |
