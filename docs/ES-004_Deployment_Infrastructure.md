# ES-004: Especificación de Infraestructura y Despliegue (Red Local)
**Proyecto:** CAS Project (Credit System)
**Versión:** 1.1.0

## 1. Visión General de la Ejecución (Modelo Híbrido)
El sistema operará bajo una arquitectura de Red Local (LAN). Una de las computadoras de la entidad asumirá un rol híbrido: actuará como el **Servidor Principal** y, al mismo tiempo, será una **Estación de Trabajo** para un operador. 
Las demás computadoras de la red actuarán únicamente como clientes, conectándose a la IP local del Servidor Principal.

## 2. Orquestación del Backend (Instalación Nativa / Bare-Metal)
Para optimizar el uso de memoria RAM y CPU en la máquina híbrida, se prescinde de contenedores. Los servicios se ejecutarán de forma nativa:

### 2.1. Base de Datos (PostgreSQL)
*   Se instalará PostgreSQL directamente sobre el sistema operativo (mediante el instalador nativo para Windows/Linux).
*   Se configurará para que arranque automáticamente como un Servicio del Sistema al encender la computadora.
*   El archivo `pg_hba.conf` se configurará para permitir conexiones locales (127.0.0.1) y conexiones desde la subred local (Ej. `192.168.1.0/24`).

### 2.2. Servidor gRPC (`cas_server`)
*   Se creará un entorno virtual de Python (`.venv`) alojado en una carpeta específica del disco duro (Ej. `C:\CAS_Server\`).
*   El servidor se configurará para arrancar automáticamente sin requerir intervención manual. En Windows, esto se logrará envolviendo el script de inicio en un archivo `.bat` ejecutado mediante el **Programador de Tareas** (al iniciar el sistema) o utilizando utilidades como NSSM (Non-Sucking Service Manager) para convertir el script de Python en un Servicio de Windows.

## 3. Gestión de Configuración
Las configuraciones se mantendrán en un archivo `.env` ubicado en la carpeta del servidor.

**Ejemplo de Variables (`.env`):**
```ini
# Base de Datos (Conexión local vía localhost)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<contraseña_segura>
POSTGRES_DB=ds10_cas_db
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

# Servidor gRPC (Escuchando en todas las interfaces de red para permitir a otros PCs conectarse)
GRPC_HOST=0.0.0.0
GRPC_PORT=50051

JWT_SECRET_KEY=<llave_criptografica_aleatoria>