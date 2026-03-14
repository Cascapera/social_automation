# Restaura backup do PostgreSQL
# Usa docker cp + pg_restore dentro do container para evitar corrupção

param(
    [string]$backupFile = "backup_banco.dump"
)

# Encontra o container postgres (nome pode variar por pasta do projeto)
$dbName = "social_automation"
$user = "postgres"

if (-not (Test-Path $backupFile)) {
    Write-Host "Arquivo nao encontrado: $backupFile"
    exit 1
}

# Encontra o container postgres
$container = (docker ps --format "{{.Names}}" | Select-String -Pattern "postgres" | Select-Object -First 1).ToString()
if (-not $container) {
    Write-Host "Container postgres nao encontrado. Suba com: docker compose up -d postgres"
    exit 1
}
Write-Host "Usando container: $container"

# Copia o backup para dentro do container
docker cp $backupFile "${container}:/tmp/restore.dump"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Erro ao copiar arquivo para o container"
    exit 1
}

# Restaura
docker exec -i $container pg_restore -U $user -d $dbName --clean --if-exists /tmp/restore.dump
$restoreExit = $LASTEXITCODE

# Remove do container
docker exec $container rm -f /tmp/restore.dump 2>$null

if ($restoreExit -ne 0) {
    Write-Host "pg_restore retornou codigo $restoreExit (avisos sobre objetos existentes podem ser normais)"
} else {
    Write-Host "Restauracao concluida."
}
