# CREDIMED UME — Sistema de Administración de Créditos (CAS)

Sistema centralizado de gestión de créditos de **CREDIMED UME** (RUC 1276703-4 — Ayolas c/ Acaray, Coronel Oviedo, Paraguay): clientes (KYC), ciclo de vida de préstamos con amortización francesa, un panel de estadísticas agregadas y reportes de cierre de período. Arquitectura cliente-servidor sobre gRPC, con un cliente de escritorio en PySide6 y un servidor Python respaldado por PostgreSQL.

## Estado del proyecto

Cuatro servicios implementados en el servidor, cada uno con su vista correspondiente en el cliente de escritorio:

| Servicio | Funcionalidad | Vista cliente |
|---|---|---|
| `AuthService` | Login/Logout, reseteo de contraseña, alta y listado de usuarios con sus datos personales (solo ADMIN) | Login, `UsersView` |
| `ClientService` | Alta/edición/baja de clientes, reglas KYC, referencias personales/laborales | `ClientsView` |
| `LoanService` | Ciclo de vida completo del préstamo, propuesta editable, garantías, cargos, cronograma de amortización, pagos, mora | `LoansView` |
| `DashboardService` | Estadísticas agregadas de solo lectura (clientes, préstamos por estado, cartera, monto total de mora) y reportes de cierre de período | `DashboardView` |

Los cuatro servicios están registrados en `cas_server/server.py` y cubiertos por el control de acceso basado en roles (`cas_server/security/rbac.py`).

## Arquitectura

```
PySide6 Client  →  gRPC / Protocol Buffers  →  CAS Server  →  SQLAlchemy  →  PostgreSQL
```

- **`cas_client/`** — Aplicación de escritorio (PySide6). Solo presentación y validación superficial; nunca accede a la base de datos directamente, todo pasa por un stub gRPC.
- **`cas_server/`** — Lógica de negocio, validación profunda y persistencia. Las RPC devuelven códigos gRPC estándar (`OK`, `INVALID_ARGUMENT`, `NOT_FOUND`, `UNAUTHENTICATED`, `PERMISSION_DENIED`).
- **`protos/`** — Contrato fuente de verdad entre cliente y servidor.
- **`specs/`** — Reglas de negocio autoritativas por módulo (autenticación, clientes, préstamos, panel/reportes).
- **PostgreSQL** — Sistema de registro, accedido únicamente desde `cas_server`.

## Reglas de negocio destacadas

- RBAC por niveles de rol: `CASHIER` < `CREDIT_ANALYST` < `MANAGER` < `ADMIN`.
- Hash de contraseñas con Argon2, bloqueo temporal tras 5 intentos fallidos, JWT de 8 horas sin refresh.
- Máximo 3 préstamos activos por cliente; cuota mensual limitada al 40% del ingreso declarado.
- Aprobaciones no desembolsadas caducan a los 30 días (estado `EXPIRED`, presentado al usuario como "Rechazado").
- El panel muestra el **monto total de mora**: la suma de lo ya vencido e impago de los préstamos activos, que es un subconjunto del saldo pendiente total (este último incluye además las cuotas futuras).
- Reportes de cierre de período (mes/trimestre/año), exportables a PDF y `.docx`, restringidos a MANAGER+.
- Fechas: la interfaz y los documentos usan `DD/MM/AAAA`; el contrato gRPC sigue usando ISO `YYYY-MM-DD`.
- Tasa de interés estándar fija (24% anual); solo roles MANAGER+ pueden fijar una tasa distinta.
- Todo cobro se registra con referencia de transferencia/débito directo (no se maneja efectivo).
- Cada pago registrado emite un Comprobante de Pago para el deudor, con el monto abonado y las cuotas que cubrió ("Cuota(s) 1,2 de 18").
- Los operadores se registran con nombre, apellido y C.I.; esos datos identifican al responsable en el Cronograma y en el Comprobante de Pago.
- Documentos de préstamo generados (Liquidación, Pagaré, Contrato, Cronograma de Pago, Comprobante de Pago) en PDF y `.docx` — **el texto legal de Pagaré/Contrato es un borrador pendiente de revisión legal real**, no usar en producción con clientes reales sin ese paso.

El detalle completo de cada regla vive en `specs/` y en `CLAUDE.md`.

## Requisitos previos

- Python 3.11+ (desarrollado con 3.12)
- PostgreSQL accesible localmente o en la red
- `pip`

## Instalación

```bash
git clone https://github.com/favillar16/Sistema-de-Cr-dito-CrediUme.git
cd Sistema-de-Cr-dito-CrediUme
python -m venv .venv
# Windows: .venv\Scripts\activate | Unix: source .venv/bin/activate
pip install -r requirements.txt
```

### Configuración

Copiar y completar las variables de entorno (nunca commitear el `.env` real):

```bash
cp cas_server/.env.example cas_server/.env
cp cas_client/.env.example cas_client/.env
```

`cas_server/.env` necesita `POSTGRES_*`, `GRPC_HOST`/`GRPC_PORT` y `JWT_SECRET_KEY`. `cas_client/.env` solo necesita `GRPC_SERVER_HOST`/`GRPC_PORT`. Ver `.env.example` en la raíz para la lista completa.

### Base de datos

Las migraciones se ejecutan **desde dentro de `cas_server/`**, no desde la raíz del repo:

```bash
cd cas_server
alembic upgrade head
cd ..
```

### Primer usuario administrador

```bash
python -m cas_server.scripts.seed_admin <usuario> <contraseña>
```

## Ejecutar

```bash
# Servidor (una terminal)
python -m cas_server.server

# Cliente de escritorio (otra terminal)
python -m cas_client.main
```

## Regenerar contratos gRPC

Después de editar cualquier archivo en `protos/*.proto`:

```bash
python scripts/generate_protos.py
```

Esto escribe los stubs generados tanto en `cas_server/` como en `cas_client/`.

## Tests

Requiere una instancia real de PostgreSQL accesible vía `cas_server/.env` (los tests no usan una base mock ni SQLite):

```bash
pytest
```

## Lint / formato

```bash
black cas_client cas_server tests
flake8
```

## Estructura del repositorio

```
cas_client/        Aplicación de escritorio PySide6
cas_server/         Servidor gRPC, lógica de negocio, migraciones Alembic
protos/             Contratos .proto (fuente de verdad cliente-servidor)
specs/              Reglas de negocio autoritativas por módulo
docs/               Especificaciones de ingeniería, guías de UI/UX, notas de auditoría
tests/              Suite de pytest (servidor + cliente)
.ai/skills/         Guías específicas del proyecto para agentes de IA que trabajen en el código
scripts/            Utilidades (regeneración de protos)
```

## Documentación adicional

- `docs/ES-000_Engineering_Specification.md` — especificación técnica general.
- `docs/ES-003_UI_UX_Guidelines.md` — lineamientos de interfaz.
- `docs/ES-004_Deployment_Infrastructure.md` — modelo de despliegue (LAN, sin contenedores).
- `CLAUDE.md` — contexto detallado de arquitectura y convenciones para desarrollo asistido por IA.

## Pendientes conocidos antes de producción

- Revisión legal real de los documentos generados (Liquidación/Pagaré/Contrato).
- Habilitar TLS en el canal gRPC (actualmente en puerto inseguro, deliberado para esta fase).
- Confirmar el set de estadísticas del Dashboard con el dueño del producto.

Ver la sección "Recommendations for the next session" de `CLAUDE.md` para el detalle completo y actualizado.
