# ES-005: Evaluación de Notificaciones SMS a Clientes

**Proyecto:** CAS Project (Credit System)
**Estado:** Evaluación únicamente -- sin implementación. Ningún cambio de proto/servidor/cliente acompaña este documento.

## 1. Objetivo

Evaluar la dificultad y los requerimientos técnicos, legales y de costo para enviar notificaciones por SMS al número de teléfono de los clientes (recordatorios de pago, aprobación de préstamo, desembolso, mora, etc.), sin comprometerse todavía a una implementación.

## 2. Opciones de proveedor

| Opción | Alcance | Integración | Costo aproximado* | Notas |
|---|---|---|---|---|
| **Twilio** | Internacional, cobertura Paraguay vía rutas mayoristas | API REST simple, SDKs oficiales, sandbox gratuito | Por SMS + tarifa mensual de número | El más documentado; deliverability a números paraguayos depende de la ruta mayorista contratada, conviene probar antes de comprometerse |
| **Infobip / Vonage (Nexmo)** | Internacional, agregadores con rutas locales en LatAm | API REST similar a Twilio | Similar a Twilio, a veces mejor tarifa para LatAm | Alternativas directas a Twilio, vale comparar cobertura real a Tigo/Personal/Claro Paraguay antes de elegir |
| **APIs directas de operadoras locales (Tigo, Personal, Claro)** | Solo Paraguay | Suele requerir alta comercial/contrato B2B, no siempre hay API pública self-service | Potencialmente más barato por volumen alto, pero fricción de alta | Requiere investigación directa con cada operadora -- no hay documentación pública uniforme como con los agregadores internacionales |

*Los costos varían por volumen, ruta y fecha; no se tomó una cotización real para este documento -- son órdenes de magnitud típicas de la industria, no un compromiso de precio.

**Recomendación inicial:** empezar con un agregador internacional (Twilio o Infobip) para el prototipo, dado el self-service y la documentación madura; evaluar una operadora local sólo si el volumen mensual proyectado justifica negociar una tarifa B2B.

## 3. Requerimientos técnicos

### 3.1 Nuevo componente de envío
Este proyecto no tiene ningún cliente HTTP saliente hoy (`cas_server` sólo habla gRPC con `cas_client` y SQL con Postgres). Se necesitaría:
- Un nuevo servicio o extensión de servicio existente -- ya sea un `NotificationService` nuevo (`protos/notification_service.proto`) o un hook interno llamado desde `loan_service.py`/`client_service.py` en los eventos relevantes (aprobación, desembolso, pago registrado, préstamo vencido).
- Un cliente HTTP hacia el proveedor elegido (ninguna librería HTTP está en `requirements.txt` hoy -- se sumaría `requests` o similar).
- Credenciales de proveedor vía `.env`, siguiendo el patrón ya establecido en `cas_server/config.py`'s `_require()` (ej. `SMS_PROVIDER_API_KEY`, `SMS_PROVIDER_ACCOUNT_SID`).

### 3.2 Validación de número de teléfono
`Client.phone_number` es hoy texto libre sin validar (ni en `client_service.py` ni en el proto). Los proveedores de SMS requieren formato E.164 (`+595981234567`). Se necesitaría:
- Normalización/validación al crear o actualizar un cliente (o al momento de enviar, con manejo de error si el número no es válido).
- Decidir si se rechaza el alta de un cliente con número inválido (rompe compatibilidad hacia atrás con datos existentes) o si sólo se bloquea el envío de SMS para esos casos.

### 3.3 El problema arquitectónico más grande: no hay scheduler
Este proyecto no tiene ningún job en segundo plano -- el vencimiento de préstamos (`BR-LOAN-003`) se resuelve con chequeo perezoso en cada lectura/escritura, no con un cron. Los eventos que disparan un SMS se dividen en dos categorías con implicancias muy distintas:

- **Eventos síncronos** (aprobación, desembolso, pago registrado): pueden dispararse directamente dentro del RPC correspondiente (`ApproveLoan`, `DisburseLoan`, `RecordPayment`), igual que ya se escribe un `AuditLog` ahí. Bajo riesgo arquitectónico, pero el RPC se vuelve más lento y frágil (si el proveedor de SMS está caído, ¿debe fallar el préstamo se apruebe igual?).
- **Eventos temporales** (recordatorio de pago N días antes del vencimiento de cuota, aviso de mora): **no hay forma de dispararlos hoy** sin agregar infraestructura nueva -- un poller/outbox (tabla `notification_outbox` + un proceso separado que la recorra periódicamente) o un scheduler real (ej. APScheduler, un servicio Windows/NSSM adicional corriendo un script periódico, dado que el despliegue es bare-metal sin contenedores por `docs/ES-004`). Esto es, con diferencia, la pieza de mayor esfuerzo de todo el trabajo.

### 3.4 Manejo de fallas
Los proveedores de SMS pueden fallar, tener rate limits, o rebotar números inválidos. Se necesitaría una estrategia de reintento (con backoff) y, idealmente, no bloquear el flujo principal del préstamo/cliente si el SMS falla -- es decir, el envío debería ser "best effort" y quedar auditado (éxito/fracaso) sin abortar la transacción de negocio.

## 4. Cumplimiento legal

Paraguay cuenta con la **Ley N° 6534/2020 de Protección de Datos Personales**, que exige consentimiento informado para el tratamiento de datos personales -- el número de teléfono lo es. Antes de enviar cualquier SMS (incluso transaccional, no sólo marketing) se recomienda:
- Agregar un campo de consentimiento explícito al alta del cliente (ej. `sms_notifications_consent: bool`), o al menos documentar en el contrato/Términos y Condiciones que el cliente acepta recibir notificaciones operativas por SMS.
- Ofrecer un mecanismo de opt-out.

Esto es una recomendación de buena práctica basada en el marco legal general, no asesoría legal formal -- conviene una revisión por un abogado antes de implementar, en la misma línea que el texto placeholder de los documentos generados en `cas_client/documents.py` (ver `CLAUDE.md`).

## 5. Estimación de esfuerzo (orden de magnitud, no un compromiso)

| Pieza | Esfuerzo relativo |
|---|---|
| Cliente HTTP + credenciales + envío síncrono en 2-3 eventos existentes (aprobación/desembolso/pago) | Bajo -- días |
| Validación/normalización de `phone_number` | Bajo -- horas a un día |
| Consentimiento/opt-out | Bajo -- un campo + UI |
| Outbox/scheduler para recordatorios temporales | **Alto** -- es infraestructura nueva que no existe en el proyecto hoy (proceso en segundo plano + tabla de cola + manejo de reintentos) |
| Pruebas end-to-end con el proveedor elegido | Medio -- depende de si el proveedor ofrece sandbox |

## 6. Recomendación

Para un primer alcance acotado, limitar el envío de SMS a **eventos síncronos disparados dentro de un RPC existente** (aprobación, desembolso, pago) usando un agregador internacional con sandbox (Twilio o Infobip). Diferir los recordatorios temporales (que requieren scheduler) a una segunda fase, ya que es la pieza de mayor riesgo/esfuerzo y el proyecto no tiene ninguna infraestructura de jobs en segundo plano sobre la cual apoyarse hoy.
