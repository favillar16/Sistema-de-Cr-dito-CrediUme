# Configurar una PC cliente en la LAN

Guía para dejar operativa una PC que va a usar el sistema como **cliente**,
conectándose al servidor que corre en `DESKTOP-5H7BABS` (ver
[docs/ES-004](ES-004_Deployment_Infrastructure.md) para el modelo de despliegue completo).

## 1. Obtener el código

```bash
git clone https://github.com/favillar16/Sistema-de-Cr-dito-CrediUme.git
cd Sistema-de-Cr-dito-CrediUme
```

Si la PC ya tiene una copia del repo, alcanza con `git pull`.

## 2. Entorno Python

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Certificado del servidor (fuera de git, a propósito)

La carpeta `certs/` está en `.gitignore` porque además del certificado
público contiene la **clave privada** del servidor, que nunca debe salir de
la máquina que lo aloja. Por eso este paso es manual:

1. En la PC servidor (`DESKTOP-5H7BABS`), copiar **solo**
   `certs\server.crt` (nunca `server.key`) a un medio compartido (USB,
   carpeta de red, etc.).
2. En la PC cliente, guardarlo en una ruta local, por ejemplo
   `C:\CredimedUme\certs\server.crt`.

## 4. Configurar `cas_client/.env`

Copiar `cas_client\.env.example` a `cas_client\.env` y completar:

```
GRPC_SERVER_HOST=DESKTOP-5H7BABS
GRPC_PORT=50051
GRPC_TLS_CA_FILE=C:\CredimedUme\certs\server.crt
```

Se usa el **nombre de red** del servidor (`DESKTOP-5H7BABS`) en vez de su
IP: la IP LAN se asigna por DHCP y puede cambiar, mientras que el nombre se
resuelve solo entre PCs Windows del mismo segmento de red (NetBIOS/LLMNR),
sin necesitar un servidor DNS propio. El certificado del servidor también
tiene ese hostname como Subject Alternative Name, así que la verificación
TLS funciona con el nombre sin configuración extra.

Antes de abrir la app, conviene confirmar que el nombre resuelve desde la
PC cliente:

```bash
ping DESKTOP-5H7BABS
```

Si no responde (red distinta, NetBIOS deshabilitado, etc.), usar como
respaldo la IP LAN actual del servidor en `GRPC_SERVER_HOST` -- con la
salvedad de que puede cambiar si esa IP no está reservada en el router.

## 5. Iniciar la app

```bash
python -m cas_client.main
```

## 6. Iniciar sesión

Usar las credenciales de administrador que te haya compartido quien
configuró el servidor (no se documentan acá por tratarse de un repositorio
de código). El primer usuario ADMIN puede crear el resto de los usuarios
desde "Usuarios" en la barra lateral (`BR-AUTH-005`).

## Del lado del servidor (ya hecho en `DESKTOP-5H7BABS`)

Para referencia -- no hace falta repetir esto por cada PC cliente:

- Regla de Firewall de Windows abierta para el puerto TCP 50051 (entrada).
- `cas_server` corriendo con `GRPC_HOST=0.0.0.0` y TLS habilitado
  (`GRPC_TLS_CERT_FILE`/`GRPC_TLS_KEY_FILE` en `cas_server/.env`).
- Certificado autofirmado en `certs/server.crt` con el hostname del
  servidor en su Subject Alternative Name (`certs/openssl.cnf`).
