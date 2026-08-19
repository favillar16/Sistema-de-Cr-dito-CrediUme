# Abre el puerto 50051 en el Firewall de Windows para que las PCs cliente de la
# LAN puedan conectarse al servidor.
#
# Correr UNA VEZ desde una PowerShell abierta como Administrador:
#     powershell -ExecutionPolicy Bypass -File scripts/setup_server_admin.ps1
#
# Hace falta de verdad y no es obvio: enlazar GRPC_HOST=0.0.0.0 NO alcanza para
# que Windows acepte conexiones entrantes de otras PCs, y la regla no existe por
# defecto. El sintoma sin ella engania: el servidor arranca bien y responde en
# localhost, pero cualquier cliente de la red se queda en "No se pudo conectar
# con el servidor", que es el mismo mensaje de un servidor apagado.
#
# Este script YA NO arranca ni reinicia el servidor. cas_server corre como el
# servicio CASServer (ver scripts/install_service_admin.ps1); la version
# anterior de este archivo mataba lo que estuviera escuchando en 50051 -- es
# decir, el proceso del servicio -- y lanzaba un start_server.bat que chocaba
# por el puerto con el servicio que NSSM volvia a levantar. Para reiniciar:
#     Stop-Service CASServer ; Start-Service CASServer

param(
    [string]$RuleName = "CAS Server gRPC 50051",
    [int]$Port = 50051
)

$esAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    throw "Este script necesita una PowerShell abierta como Administrador."
}

# Perfil "any" a proposito: la PC servidor puede quedar con el perfil de red en
# "Publico" (pasa cuando el router no se marca como red privada) y una regla
# atada a un solo perfil dejaria de aplicar sin aviso.
netsh advfirewall firewall show rule name="$RuleName" | Out-Null
if ($LASTEXITCODE -ne 0) {
    netsh advfirewall firewall add rule name="$RuleName" dir=in action=allow `
        protocol=TCP localport=$Port profile=any | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Output "Regla de firewall '$RuleName' creada (TCP $Port, perfil any)."
    } else {
        throw "netsh fallo al crear la regla (codigo $LASTEXITCODE)."
    }
} else {
    Write-Output "La regla de firewall '$RuleName' ya existia."
}

Write-Output "Estado del servicio:"
Get-Service CASServer -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType | Format-Table -AutoSize
