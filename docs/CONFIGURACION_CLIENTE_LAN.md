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

## 3. Certificado del servidor

La carpeta `certs/` está en `.gitignore` porque en la PC servidor contiene
también la **clave privada** (`server.key`), que nunca debe salir de esa
máquina. Por eso este paso es manual:

1. En la PC servidor (`DESKTOP-5H7BABS`), copiar **solo**
   `certs\server.crt` (nunca `server.key`) a un medio compartido (USB,
   carpeta de red, etc.).
2. En la PC cliente, guardarlo en una ruta local, por ejemplo dentro del
   propio repo en `certs\server.crt`.

> El `server.crt` estuvo versionado brevemente (commit `90dd025`, agregado
> con `git add -f`) como método de transferencia puntual para la primera PC
> cliente, y se volvió a quitar del control de versiones en `06fd51f` una
> vez completada. **Cuidado con eso:** si una PC cliente lo recibió por
> `git pull` en esa ventana, el `git pull` siguiente le *borra* el archivo
> (git elimina del working tree lo que se borró en el commit), y la app deja
> de conectar con un error de TLS. Si pasa, volver a copiar el `.crt` a mano
> siguiendo los dos pasos de arriba: ya está gitignorado, así que no se
> vuelve a perder.

## 4. Configurar `cas_client/.env`

Copiar `cas_client\.env.example` a `cas_client\.env` y completar:

```
GRPC_SERVER_HOST=DESKTOP-5H7BABS
GRPC_PORT=50051
GRPC_TLS_CA_FILE=C:\ruta\al\repo\certs\server.crt
```

`GRPC_TLS_CA_FILE` apunta al `server.crt` que copiaste en el paso 3. Poner
la ruta **absoluta** de esta PC (p. ej.
`C:\Users\Usuario\CAS_client\certs\server.crt`): una ruta relativa se
resolvería contra el directorio de trabajo, que no siempre es la raíz del
repo. Si `GRPC_TLS_CA_FILE` queda vacío, el cliente conecta **sin TLS** y el
servidor va a rechazar el handshake.

Se usa el **nombre de red** del servidor (`DESKTOP-5H7BABS`) en vez de su
IP: la IP LAN se asigna por DHCP y puede cambiar, mientras que el nombre se
resuelve solo entre PCs Windows del mismo segmento de red (NetBIOS/LLMNR),
sin necesitar un servidor DNS propio. El certificado del servidor también
tiene ese hostname como Subject Alternative Name, así que la verificación
TLS funciona con el nombre sin configuración extra.

Antes de abrir la app, conviene confirmar que el cliente llega al servidor.
**No usar `ping` para esto**: en la PC cliente ya verificada, `ping
DESKTOP-5H7BABS` agota el tiempo de espera (resuelve por mDNS a una
dirección IPv6 de vínculo local, y el Firewall de Windows del servidor
descarta ICMP) mientras la aplicación funciona perfectamente. El `ping` mide
ICMP; lo que importa es TCP al puerto 50051. La comprobación correcta es:

```bash
python -c "import socket; s=socket.create_connection(('DESKTOP-5H7BABS',50051),timeout=8); print('OK ->', s.getpeername())"
```

Si eso devuelve `OK`, la app va a conectar. Si falla, revisar en este orden:
el servidor está encendido y `cas_server` corriendo, la regla de Firewall
para el puerto TCP 50051, y por último la resolución del nombre.

Como respaldo, si el nombre no resuelve (red distinta, NetBIOS
deshabilitado, etc.), se puede poner la IP LAN actual del servidor en
`GRPC_SERVER_HOST` -- con dos salvedades: puede cambiar si esa IP no está
reservada en el router, y la verificación TLS solo funciona con una IP que
figure en el Subject Alternative Name del certificado (hoy `127.0.0.1` y
`192.168.100.74`). Si el servidor toma otra IP, hay que regenerar el
certificado o usar el nombre de red.

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
