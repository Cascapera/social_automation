# PostgreSQL backup and restore

PowerShell redirect `>` corrupts binary dumps (saves as UTF-16). Use these scripts.

## On the source machine (create backup)

```powershell
cd c:\path\to\social_automation
.\scripts\backup_postgres.ps1
```

Creates `backup_banco.dump` at the project root. Copy that file and the `storage/media` folder to the other machine.

## On the destination machine (restore)

1. Place `backup_banco.dump` at the project root
2. Start PostgreSQL: `docker compose up -d postgres redis`
3. Run:

```powershell
cd c:\path\to\social_automation
.\scripts\restore_postgres.ps1
```

4. Copy the `storage/media` folder into the project
5. Start the rest: `docker compose up -d`

## Parameters

Restore a different file:

```powershell
.\scripts\restore_postgres.ps1 -backupFile "my_backup.dump"
```
