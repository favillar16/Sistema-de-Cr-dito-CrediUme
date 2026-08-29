# Corrige de forma definitiva el problema de conexion LAN documentado en
# CLAUDE.md ("Deployment model" / item 2 de "Recommendations for the next
# session"): la PC servidor (DESKTOP-5H7BABS) recibia su IP por DHCP y el
# router no siempre registra el hostname en su DNS, asi que cada renovacion
# de lease (se la vio en .74, despues en .9) rompia la resolucion de nombre
# para todas las PCs cliente, incluso aunque el certificado TLS del servidor
# siga firmando el hostname y la IP 192.168.100.74 como Subject Alternative
# Name (ver certs/openssl.cnf).
#
# Esto NO depende del panel de administracion del router -- ambos pasos son
# configuracion local de Windows:
#   - En la PC servidor: fija la IP 192.168.100.74 en el adaptador de red
#     (deja de depender de DHCP, asi no vuelve a cambiar sola).
#   - En cualquier otra PC (cliente): agrega/corrige la entrada
#     "192.168.100.74  DESKTOP-5H7BABS" en el archivo hosts, para que
#     GRPC_SERVER_HOST=DESKTOP-5H7BABS (cas_client/.env) resuelva sin
#     depender del DNS del router.
#
# El script detecta solo que rol cumple la PC donde se lo corre (por
# nombre de equipo) y aplica la correccion correspondiente. Es idempotente:
# correrlo de nuevo no duplica nada ni rompe una configuracion ya corregida.
#
# Correr UNA VEZ en cada PC que lo necesite (la servidora y cada cliente),
# desde una PowerShell abierta como Administrador:
#     powershell -ExecutionPolicy Bypass -File scripts\fix_lan_ip_admin.ps1
#
# Este archivo es temporal: una vez confirmado que todas las PCs conectan
# bien (ver scripts/diagnostico_cliente.py / "Verificar Conexion.bat"), se
# puede borrar del repo sin dejar ningun cabo suelto -- no es infraestructura
# permanente, solo el vehiculo para aplicar la correccion una vez por PC.

param(
    [string]$ServerHostname = "DESKTOP-5H7BABS",
    [string]$ServerIp = "192.168.100.74",
    [string]$SubnetMask = "255.255.255.0",
    [string]$Gateway = "192.168.100.1",
    [string]$Dns = "192.168.100.1",
    [string]$AdapterName = ""
)

$ErrorActionPreference = "Stop"

$esAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    throw "Este script necesita una PowerShell abierta como Administrador."
}

function Fix-Servidor {
    Write-Output "Este equipo es la PC servidor ($ServerHostname) -- fijando IP estatica."

    $adaptador = $AdapterName
    if (-not $adaptador) {
        $activo = Get-NetAdapter -Physical | Where-Object Status -eq "Up" | Select-Object -First 1
        if (-not $activo) {
            throw "No se encontro ningun adaptador de red fisico activo. Pasar -AdapterName explicitamente."
        }
        $adaptador = $activo.Name
    }
    Write-Output "Adaptador: $adaptador"

    netsh interface ip set address name="$adaptador" static $ServerIp $SubnetMask $Gateway | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "netsh fallo fijando la IP (codigo $LASTEXITCODE)." }
    netsh interface ip set dns name="$adaptador" static $Dns | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "netsh fallo fijando el DNS (codigo $LASTEXITCODE)." }

    Write-Output "OK: $adaptador ahora tiene IP fija $ServerIp / $SubnetMask, gateway $Gateway, DNS $Dns."
    Write-Output "No hace falta reiniciar el servicio CASServer: escucha en 0.0.0.0:50051."
    Write-Output ""
    Write-Output "Verificacion:"
    Get-NetIPAddress -InterfaceAlias $adaptador -AddressFamily IPv4 |
        Select-Object InterfaceAlias, IPAddress, PrefixLength | Format-Table -AutoSize
}

function Fix-Cliente {
    Write-Output "Este equipo es una PC cliente -- corrigiendo el archivo hosts."

    $hostsPath = Join-Path $env:WINDIR "System32\drivers\etc\hosts"
    $contenido = Get-Content -Path $hostsPath -Raw

    $lineaCorrecta = "$ServerIp`t$ServerHostname"
    $patronLineaExistente = "(?m)^\s*\d{1,3}(\.\d{1,3}){3}[ \t]+$([regex]::Escape($ServerHostname))\s*$"
    $patronPegadoSinSalto = "(?<antes>[^\r\n])\d{1,3}(\.\d{1,3}){3}[ \t]+$([regex]::Escape($ServerHostname))\s*$"

    $cambiado = $false

    if ($contenido -match $patronPegadoSinSalto) {
        # Caso visto en produccion: una entrada vieja quedo pegada sin salto de
        # linea al final de otro comentario (p.ej. "...helper server192.168.100.9	DESKTOP-5H7BABS"),
        # asi que Windows nunca la lee como mapping real. Se restaura la linea
        # original y la entrada correcta se agrega aparte, mas abajo.
        $contenido = [regex]::Replace($contenido, $patronPegadoSinSalto, '${antes}')
        $cambiado = $true
        Write-Output "Se encontro y limpio una entrada vieja mal formada (pegada sin salto de linea)."
    }

    if ($contenido -match $patronLineaExistente) {
        $yaCorrecta = [regex]::IsMatch($contenido, "(?m)^\s*$([regex]::Escape($ServerIp))[ \t]+$([regex]::Escape($ServerHostname))\s*$")
        if ($yaCorrecta) {
            Write-Output "El archivo hosts ya tiene la entrada correcta ($lineaCorrecta). Nada que hacer."
            if ($cambiado) {
                Set-Content -Path $hostsPath -Value $contenido -Encoding ASCII -NoNewline
            }
            return
        }
        # Habia una entrada para el mismo hostname mapeada a otra IP (quedo
        # vieja de una renovacion de DHCP anterior) -- se reemplaza.
        $contenido = [regex]::Replace($contenido, $patronLineaExistente, $lineaCorrecta)
        $cambiado = $true
        Write-Output "Se reemplazo una entrada vieja de $ServerHostname por la IP fija actual."
    } else {
        if (-not $contenido.EndsWith("`n")) { $contenido += "`r`n" }
        $contenido += "`r`n# CrediUme -- servidor CAS, IP fija (ver CLAUDE.md, seccion `"Deployment model`")`r`n$lineaCorrecta`r`n"
        $cambiado = $true
        Write-Output "Se agrego la entrada $lineaCorrecta."
    }

    Set-Content -Path $hostsPath -Value $contenido -Encoding ASCII -NoNewline
    Write-Output "OK: archivo hosts actualizado ($hostsPath)."
    Write-Output ""
    Write-Output "Verificacion: ping $ServerHostname"
    ping -n 2 $ServerHostname
}

if ($env:COMPUTERNAME -eq $ServerHostname) {
    Fix-Servidor
} else {
    Fix-Cliente
}
