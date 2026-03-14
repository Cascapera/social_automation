# Gera backup do PostgreSQL sem corrupção (evita UTF-16 do PowerShell)
# O redirect > corrompe arquivos binários. Usamos -f dentro do container + docker cp.

$container = "social_automation-postgres-1"
$dbName = "social_automation"
$user = "postgres"
$outFile = "backup_banco.dump"

# Encontra o container postgres (nome pode variar por pasta do projeto)
$container = (docker ps --format "{{.Names}}" | Select-String -Pattern "postgres" | Select-Object -First 1).ToString()
if (-not $container) {
    Write-Host "Container postgres nao encontrado. Suba com: docker compose up -d postgres"
    exit 1
}
Write-Host "Usando container: $container"

# Gera dump DENTRO do container (binário preservado)
docker exec $container pg_dump -U $user $dbName -F c -f /tmp/backup.dump
if ($LASTEXITCODE -ne 0) {
    Write-Host "Erro ao gerar dump"
    exit 1
}

# Copia para fora (preserva binário)
docker cp "${container}:/tmp/backup.dump" $outFile
if ($LASTEXITCODE -ne 0) {
    Write-Host "Erro ao copiar arquivo"
    exit 1
}

# Remove do container
docker exec $container rm -f /tmp/backup.dump 2>$null

$size = (Get-Item $outFile).Length / 1MB
Write-Host "Backup salvo: $outFile ($([math]::Round($size, 2)) MB)"
