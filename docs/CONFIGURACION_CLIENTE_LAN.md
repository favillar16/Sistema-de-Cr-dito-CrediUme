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
repo. Si `GRPC_TLS_CA_FILE` queda vacío o apunta a un archivo que no existe
en esta PC, la app **no arranca**: muestra un diálogo nombrando la variable o
la ruta a corregir. Antes se conectaba **sin TLS** en silencio, el servidor
rechazaba el handshake y el operador solo veía "No se pudo conectar con el
servidor", indistinguible de un servidor apagado.

Se usa el **nombre de red** del servidor (`DESKTOP-5H7BABS`) en vez de su
IP: la IP LAN se asigna por DHCP y puede cambiar, mientras que el nombre no.
El certificado del servidor también tiene ese hostname como Subject
Alternative Name, así que la verificación TLS funciona con el nombre sin
configuración extra.

En esta red el nombre resuelve por **DNS del propio router**
(`192.168.100.1` registra los hostnames que entrega por DHCP), verificado
con `Resolve-DnsName DESKTOP-5H7BABS -Server 192.168.100.1`. Eso es más
robusto que NetBIOS/LLMNR: no depende de que el perfil de red de Windows
esté en "Privado" ni del descubrimiento de red, y se actualiza solo si la
IP del servidor cambia.

Antes de abrir la app, correr el diagnóstico desde la raíz del repo con el
venv activado:

```bash
python scripts/diagnostico_cliente.py
```

Revisa en orden la configuración, la resolución del nombre, el puerto TCP, el
handshake TLS y un RPC real (`Login` con un usuario inexistente, que debe
contestar `UNAUTHENTICATED` — eso prueba que además del socket responden el
interceptor, el servicio y Postgres). Cada punto imprime `[OK]`/`[FALLA]` y,
cuando falla, qué corregir. No pide credenciales ni escribe en la base.

**No usar `ping` para esto**: en la PC cliente ya verificada, `ping
DESKTOP-5H7BABS` agota el tiempo de espera (resuelve por mDNS a una
dirección IPv6 de vínculo local, y el Firewall de Windows del servidor
descarta ICMP) mientras la aplicación funciona perfectamente. El `ping` mide
ICMP; lo que importa es TCP al puerto 50051, que es lo que mide el punto 3
del diagnóstico.

Si el diagnóstico falla, revisar en este orden: el servidor está encendido y
`cas_server` corriendo, la regla de Firewall para el puerto TCP 50051, y por
último la resolución del nombre.

Como respaldo, si el nombre no resuelve (red distinta, router sin DNS
propio), se puede poner la IP LAN del servidor en `GRPC_SERVER_HOST` -- con
una salvedad que hoy hace fallar este respaldo: la verificación TLS solo
funciona con una IP que figure en el Subject Alternative Name del
certificado (hoy `127.0.0.1` y `192.168.100.74`), y esa IP **no está
reservada en el router**, así que el servidor actualmente tiene otra. Leer
la sección "Reserva DHCP" más abajo antes de recurrir a este respaldo; una
vez hecha la reserva, `192.168.100.74` vuelve a ser válida.

## 5. Iniciar la app

```bash
python -m cas_client.main
```

## 6. Iniciar sesión

Usar las credenciales de administrador que te haya compartido quien
configuró el servidor (no se documentan acá por tratarse de un repositorio
de código). El primer usuario ADMIN puede crear el resto de los usuarios
desde "Usuarios" en la barra lateral (`BR-AUTH-005`).

## Reserva DHCP de la IP del servidor (tarea del router, una sola vez)

Todas las PCs del sistema están en el mismo segmento `192.168.100.0/24`,
con el router `192.168.100.1` haciendo de gateway, servidor DHCP y DNS.

La IP del servidor se entrega por DHCP con un lease de 24 horas y **no está
reservada**, así que puede cambiar en cualquier renovación. Ya cambió una
vez: el certificado TLS fue emitido con `IP.2 = 192.168.100.74` en su
Subject Alternative Name y la máquina pasó a `192.168.100.9`. El acceso por
hostname no se vio afectado (el DNS del router se actualiza solo), pero el
respaldo por IP quedó roto: apuntar `GRPC_SERVER_HOST` a una IP que no está
en el SAN hace fallar la verificación TLS.

Para cerrarlo, reservar en el router la IP que ya firma el certificado:

| Dato | Valor |
| --- | --- |
| Equipo | `DESKTOP-5H7BABS` (servidor) |
| Interfaz | Wi-Fi — Realtek RTL8188EU |
| Dirección MAC | `A8-29-48-88-FB-FB` |
| IP a reservar | `192.168.100.74` (la del SAN del certificado; verificada libre) |

Pasos:

1. Entrar a `http://192.168.100.1` con la clave de administrador del router.
2. Buscar la sección de DHCP (suele llamarse *DHCP Reservation*, *Address
   Reservation*, *Static Lease* o *Vinculación IP-MAC*).
3. Agregar una entrada con la MAC y la IP de la tabla de arriba.
4. En la PC servidor, renovar el lease para tomar la IP reservada:
   `ipconfig /release; ipconfig /renew` (o reiniciar).
5. Confirmar: `ipconfig` debe mostrar `192.168.100.74`, y
   `Resolve-DnsName DESKTOP-5H7BABS -Server 192.168.100.1` debe devolver
   esa misma IP.

Se reserva `.74` y no la IP actual justamente para no tener que reemitir el
certificado ni volver a copiar `server.crt` a cada PC cliente. Si en cambio
se prefiere fijar la IP actual (`192.168.100.9`), hay que actualizar `IP.2`
en `certs/openssl.cnf`, regenerar el certificado y repetir el paso 3 de esta
guía en **todas** las PCs cliente.

## Del lado del servidor (ya hecho en `DESKTOP-5H7BABS`)

Para referencia -- no hace falta repetir esto por cada PC cliente:

- Regla de Firewall de Windows abierta para el puerto TCP 50051 (entrada),
  perfil `Any` -- funciona aunque el perfil de red de Windows esté en
  "Público".
- `cas_server` corriendo con `GRPC_HOST=0.0.0.0` y TLS habilitado
  (`GRPC_TLS_CERT_FILE`/`GRPC_TLS_KEY_FILE` en `cas_server/.env`).
- Certificado autofirmado en `certs/server.crt` con el hostname del
  servidor en su Subject Alternative Name (`certs/openssl.cnf`), válido
  hasta el 11/08/2036.
