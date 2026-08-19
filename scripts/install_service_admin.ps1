# Registra cas_server como servicio de Windows usando NSSM.
#
# Correr desde una PowerShell ABIERTA COMO ADMINISTRADOR:
#     powershell -ExecutionPolicy Bypass -File scripts\install_service_admin.ps1
#
# Por que un servicio y no start_server.bat: un proceso lanzado desde una
# consola vive en la sesion interactiva y se cae al cerrar sesion el usuario.
# El servicio corre en la sesion 0 bajo LocalSystem, arranca solo con la
# maquina y sobrevive al logoff -- que es el modelo que ES-004 ya preveia
# ("PostgreSQL y cas_server como servicios nativos, p.ej. NSSM en Windows").
#
# Es idempotente: si el servicio ya existe lo reconfigura en vez de fallar.

param(
    [string]$Nssm = "C:\Users\Usuario\nssm\nssm.exe",
    [string]$ServiceName = "CASServer",
    [string]$PostgresService = "postgresql-x64-16"
)

$ErrorActionPreference = "Stop"

# --- 0. Comprobaciones previas ---
$esAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    throw "Este script necesita una PowerShell abierta como Administrador."
}
if (-not (Test-Path $Nssm)) {
    throw "No se encontro nssm.exe en $Nssm. Descargarlo de https://nssm.cc/download"
}

# Raiz del repo = carpeta padre de scripts\, para que esto siga funcionando
# si el repositorio se clona en otra ruta.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
$LogFile = Join-Path $LogDir "server.log"

if (-not (Test-Path $Python)) {
    throw "No se encontro el interprete del venv en $Python"
}
if (-not (Test-Path (Join-Path $RepoRoot "cas_server\.env"))) {
    throw "Falta cas_server\.env en $RepoRoot -- el servidor no arrancaria."
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory $LogDir | Out-Null }

Write-Output "Repositorio: $RepoRoot"
Write-Output "Interprete:  $Python"

# --- 1. Bajar cualquier servidor que este corriendo suelto ---
# Si quedo un start_server.bat de antes, el servicio no podria tomar el puerto.
$conn = Get-NetTCPConnection -LocalPort 50051 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $procId = $conn[0].OwningProcess
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        Write-Output "Deteniendo el servicio $ServiceName..."
        Stop-Service -Name $ServiceName -Force
    } else {
        Write-Output "Deteniendo proceso suelto en el puerto 50051 (PID $procId)..."
        Stop-Process -Id $procId -Force
    }
    Start-Sleep -Seconds 3
}

# --- 2. Instalar o reconfigurar el servicio ---
$existe = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existe) {
    Write-Output "El servicio $ServiceName ya existe: se reconfigura."
} else {
    Write-Output "Instalando el servicio $ServiceName..."
    & $Nssm install $ServiceName $Python "-m cas_server.server"
    if ($LASTEXITCODE -ne 0) { throw "nssm install fallo (codigo $LASTEXITCODE)" }
}

& $Nssm set $ServiceName Application $Python
& $Nssm set $ServiceName AppParameters "-m cas_server.server"
& $Nssm set $ServiceName AppDirectory $RepoRoot
& $Nssm set $ServiceName DisplayName "CAS Server (CREDIMED UME)"
& $Nssm set $ServiceName Description "Servidor gRPC del sistema de creditos. Escucha en 0.0.0.0:50051 con TLS."

# Arranque automatico, pero DESPUES de PostgreSQL: sin esta dependencia, al
# encender la maquina el servidor gana la carrera, no puede abrir la base y
# muere antes de que Postgres termine de levantar.
& $Nssm set $ServiceName Start SERVICE_AUTO_START
& $Nssm set $ServiceName DependOnService $PostgresService

# Log: mismo archivo que usaba start_server.bat, ahora con rotacion a 10 MB
# para que no crezca sin limite ahora que el servicio corre siempre.
& $Nssm set $ServiceName AppStdout $LogFile
& $Nssm set $ServiceName AppStderr $LogFile
& $Nssm set $ServiceName AppRotateFiles 1
& $Nssm set $ServiceName AppRotateOnline 1
& $Nssm set $ServiceName AppRotateBytes 10485760

# Si el proceso se cae, reintentar a los 5 s.
& $Nssm set $ServiceName AppExit Default Restart
& $Nssm set $ServiceName AppRestartDelay 5000

# Al detener el servicio, pedir cierre limpio antes de matar el proceso.
& $Nssm set $ServiceName AppStopMethodConsole 5000

# --- 3. Arrancar y verificar ---
Write-Output "Arrancando $ServiceName..."
Start-Service -Name $ServiceName
Start-Sleep -Seconds 4

$svc = Get-Service -Name $ServiceName
Write-Output "Servicio: $($svc.Name) -- estado $($svc.Status), arranque $($svc.StartType)"

$conn2 = Get-NetTCPConnection -LocalPort 50051 -State Listen -ErrorAction SilentlyContinue
if ($conn2) {
    $procId2 = $conn2[0].OwningProcess
    $sesion = (Get-Process -Id $procId2).SessionId
    Write-Output "OK: escuchando en 50051 (PID $procId2, sesion $sesion)."
    if ($sesion -ne 0) {
        Write-Output "AVISO: se esperaba sesion 0; revisar la configuracion del servicio."
    }
} else {
    Write-Output "AVISO: nadie escucha en 50051. Revisar $LogFile"
}
