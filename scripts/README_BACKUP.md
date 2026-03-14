# Backup e Restauração PostgreSQL

O redirect `>` no PowerShell corrompe dumps binários (salva como UTF-16). Use os scripts.

## No PC de origem (gerar backup)

```powershell
cd c:\caminho\para\social_automation
.\scripts\backup_postgres.ps1
```

Gera `backup_banco.dump` na raiz do projeto. Copie esse arquivo e a pasta `storage/media` para o outro PC.

## No PC de destino (restaurar)

1. Coloque `backup_banco.dump` na raiz do projeto
2. Suba o PostgreSQL: `docker compose up -d postgres redis`
3. Execute:

```powershell
cd c:\caminho\para\social_automation
.\scripts\restore_postgres.ps1
```

4. Copie a pasta `storage/media` para o projeto
5. Suba o restante: `docker compose up -d`

## Parâmetros

Restaurar outro arquivo:
```powershell
.\scripts\restore_postgres.ps1 -backupFile "meu_backup.dump"
```
