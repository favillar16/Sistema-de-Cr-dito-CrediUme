# Cambios al sistema — lista de ejecución para la revisión total

**Basado en:** `docs/ES-006_Auditoria_Integral_del_Sistema.md` (auditoría del 2026-08-12).
**Propósito:** checklist ejecutable para ir marcando durante la revisión de mañana — cada ítem trae el archivo/línea afectado y el criterio de "hecho". El detalle técnico completo de cada hallazgo está en ES-006; acá solo va lo necesario para ejecutar, no para volver a justificar el hallazgo.

Orden sugerido: Bloque 0 primero (es un prerrequisito de todo lo demás), después Bloque 1 y 2 (impacto visible, rápidos), Bloque 3 (documentación, mientras el contexto está fresco), Bloque 4 (requiere una decisión antes de tocar código), Bloque 5 al final si sobra tiempo.

---

## Bloque 0 — Prerrequisito (hacer primero, en este orden)

- [x] **Crear `.gitignore`** en la raíz del repo. Debe excluir al menos: `*.env` (no `*.env.example`), `__pycache__/`, `*.pyc`, `.venv/`, y los documentos generados de prueba (`contrato_*.pdf`, `liquidacion_*.pdf`, `pagare_*.docx`, etc.).
  *Por qué primero:* si se hace `git init` sin esto, el primer commit puede filtrar `cas_server/.env`/`cas_client/.env` (contraseña real de Postgres y `JWT_SECRET_KEY`). Ver ES-006 §2.3.
- [x] **Mover o eliminar** `contrato_91cc3960.pdf`, `liquidacion_cba4ec88.pdf`, `pagare_91cc3960.pdf` de la raíz del repo (son salidas de prueba, no datos a conservar). Ver ES-006 §3.4.
- [ ] Decidir si se hace `git init` en esta sesión (requiere confirmación del usuario — no ejecutar sin decirlo explícitamente). Solo después de los dos puntos anteriores. **Sigue pendiente — no ejecutado, requiere confirmación explícita del usuario.**

---

## Bloque 1 — Bugs de UI con impacto visible

- [x] **Arreglar superposición/recorte de texto en las tarjetas del Dashboard.**
  Archivo: `cas_client/views/dashboard_view.py` (envolver el contenido con `wrap_scrollable()`, mismo patrón que `main_window.py:158` usa para `UsersView`) — o agregar un `QScrollArea` interno como ya tienen `ClientsView`/`LoansView`.
  Criterio de hecho: correr `python -m cas_client.main`, iniciar sesión, y confirmar en Dashboard que ninguna tarjeta de "Préstamos por estado" muestra texto superpuesto o números recortados, incluso achicando la ventana al tamaño mínimo (900×560).
  Detalle de la causa raíz: ES-006 §2.1.
  **Hecho:** `main_window.py` ahora envuelve `self._dashboard_view` con `wrap_scrollable()`. Pendiente verificación visual manual (no se pudo lanzar la GUI en esta sesión).

- [x] **Envolver en manejo de errores la generación de documentos.**
  Archivo: `cas_client/views/loans_view.py:1414-1469` (`_render_document`/`_save_document_docx`).
  Acción: agregar `try/except` alrededor de `document.print_(printer)` y `docx_document.save(path)`, traduciendo la excepción con el mismo patrón `_friendly_message`/`Toast` que usa el resto del archivo, en vez de dejar que la excepción se propague sin capturar.
  Criterio de hecho: intentar guardar un `.docx` sobre un archivo abierto en Word (o una ruta sin permisos) y confirmar que se muestra un Toast, no un traceback.
  Detalle: ES-006 §2.2.
  **Hecho:** ambas rutas (PDF y DOCX, descarga e impresión) envueltas en `try/except OSError` con `_friendly_file_error()`.

---

## Bloque 2 — Tooling

- [x] **Agregar `pyproject.toml`** con `[tool.black] extend-exclude` para los mismos patrones que ya excluye `flake8` en `setup.cfg` (`*_pb2*.py`, `migrations/versions/`).
- [x] Correr `black cas_client cas_server tests` una vez con el exclude ya en efecto, y revisar el diff antes de aceptarlo (dos archivos de test reales se reformatearían: `tests/server/test_loan_service.py`, `tests/server/test_loan_interceptor_integration.py`, más una migración y `cas_client/views/users_view.py`).
  Criterio de hecho: `black --check cas_client cas_server tests` sale limpio. **Verificado limpio.**
- [x] **Acortar la línea larga** en `cas_client/views/loans_view.py:1327` (135 caracteres, límite 100).
  Criterio de hecho: `flake8` sale limpio. **Verificado limpio** (se extrajo el lambda a un método `_connect_adjust_button()` en vez de forzar el salto de línea, que `black` revertía).
  Detalle: ES-006 §3.3, §4.1.

---

## Bloque 3 — Documentación desactualizada

- [x] **`specs/authentication/README`**: agregar `CreateUser`/`ListUsers` (RPCs de gestión de usuarios), la tabla `RevokedToken` y la columna `locked_until`.
- [x] **`specs/loans/README`**: agregar el estado `EXPIRED` al enum documentado (línea 25 actualmente lista solo PENDING/APPROVED/ACTIVE/PAID/DEFAULTED), y documentar `UpdateInstallmentAmount`/`BR-LOAN-008/009/010` (pago dirigido a cuota, ajuste manual de cuota, cálculo de mora) y la RPC `ListActiveLoans`.
- [x] **`CLAUDE.md`** → sección "Business modules": agregar un párrafo para la gestión de usuarios (`UsersView`, `CreateUser`/`ListUsers`) y otro para `BR-LOAN-008/009/010`/`ListActiveLoans`, siguiendo el mismo estilo que los párrafos `BR-LOAN-004`/`BR-LOAN-005/006` ya existentes.
- [x] **`CLAUDE.md`** → inventario de vistas del cliente: agregar `cas_client/views/users_view.py` (falta hoy).
- [x] *(Opcional, cosmético)* `docs/ES-003_UI_UX_Guidelines.md:17`: actualizar la descripción de la paleta a los valores reales de `theme.py` (navy `#2B407B`, lima `#8CC63F`) en vez de la paleta antigua (`#1A252F`).
  Detalle de todos estos: ES-006 §3.2, §4.6, §4.7.

---

## Bloque 4 — Requieren una decisión antes de tocar código

No ejecutar sin antes decidir con el usuario/dueños del producto — **ninguno de estos 4 puntos se ejecutó esta sesión**, quedan explícitamente para decisión del usuario/producto (ver "Recommendations for the next session" en `CLAUDE.md`):

- [ ] **Condiciones de carrera del servidor** (ES-006 §3.1): decidir entre (a) aceptar el riesgo dado el modelo de despliegue LAN de baja concurrencia, dejándolo documentado como riesgo conocido, o (b) agregar `with_for_update()`/manejo de `IntegrityError` en los tres puntos:
  - `loan_service.py:303-315` y `:620-634` (conteo de préstamos activos, BR-LOAN-001)
  - `client_service.py:163-181,318-327,430-441` y `auth_service.py:192-198` (duplicados concurrentes de national_id/email/username)
  - `loan_service.py:753-789` (recálculo de `status` tras pago concurrente)
- [ ] **Revisión legal** de los textos de Liquidación/Pagaré/Contrato — bloqueante para producción, requiere un abogado o texto del dueño, no es un cambio de código. (Esta sesión sí actualizó la *estructura* de las cláusulas contra un documento real de referencia — ver CLAUDE.md — pero eso no reemplaza la revisión legal en sí.)
- [ ] **TLS en el canal gRPC** — decisión de despliegue (ES-004), no de código.
- [ ] Confirmar con los dueños el set de estadísticas del Dashboard antes de invertir más tiempo ahí.

---

## Bloque 5 — Mejoras menores (solo si sobra tiempo)

- [x] `cas_server/db/models.py:72`: cambiar el default de `AuditLog.timestamp` de `datetime.utcnow()` a `lambda: datetime.now(timezone.utc)` para que sea consistente con `DateTime(timezone=True)` y con todos los call sites reales.
- [x] `cas_client/views/dashboard_view.py:159-163`: cancelar/ignorar un `AsyncWorker` anterior antes de lanzar uno nuevo en `showEvent`, para evitar dos refrescos corriendo en paralelo al cambiar de pestaña rápido.
  **Hecho:** contador de generación (`_refresh_generation`) — las respuestas de un refresh superado se ignoran en vez de sobrescribir el estado o esconder la barra de progreso de uno más nuevo.
- [ ] `cas_client/widgets/card.py:40-49`: aplicar `_apply_elided_text()` también en `__init__` de `_ElidingLabel`, no solo en `resizeEvent`.
  **Evaluado, no aplicado a propósito:** en `__init__`, `self.width()` todavía no refleja el ancho real del layout (valor por defecto de Qt pre-layout), así que elidir ahí arriesgaría truncar mal el texto hasta el primer `resizeEvent` de todos modos — mismo resultado final, con riesgo agregado y sin beneficio real. Ver nota en CLAUDE.md.
- [ ] `cas_client/widgets/responsive_grid.py:28-30`: evaluar si conviene evitar el `_reflow(force=True)` en cada `add_widget()` individual (hoy inofensivo, a vigilar si una grilla crece).
  **Evaluado:** el propio hallazgo dice "inofensivo a la escala actual (≤6 tiles)" — no se justifica un cambio sin una grilla real que lo necesite. Sin cambios.

---

---

## Bloque 6 — Sesión 2026-08-13 (identidad, fechas, mora y reportes)

Pedido del usuario, ejecutado íntegramente en esta sesión. Estado inicial verificado antes de tocar nada: `pytest` 156/156 en verde.

- [x] **Identidad del establecimiento → CREDIMED UME.** Nombre según la DNIT, RUC `1276703-4`, dirección "Ayolas c/ Acaray — Coronel Oviedo, Paraguay", celular `(0984) 319243`. Reemplaza los placeholders anteriores (`CREDIUME S.A.`, `80XXXXXXX-X`, "Servicios Financieros — Coronel Oviedo, Py") en `documents.py`/`documents_docx.py`, y el texto de marca de la interfaz (`theme.BRAND_NAME`, usado por el login, el sidebar y el título de ventana). El celular es un dato nuevo: no existía en el encabezado antes, ahora se imprime en los cuatro documentos. Guardado por `tests/client/test_documents_identity.py`.
  **No incluido, a propósito:** los bitmaps de `cas_client/assets/` siguen siendo el logo/isotipo de CrediUme — son assets de diseño provistos, no algo a regenerar acá; hace falta un logo nuevo del diseñador. Tampoco se tocó el texto legal de las cláusulas (sigue en borrador, ver Bloque 4).
- [x] **Fechas `AAAA-MM-DD` → `DD/MM/AAAA`** en toda la interfaz y en los documentos generados. La traducción vive solo en `cas_client/formatting.py` (`fecha`, `fecha_a_iso`, `fecha_hora`, `DISPLAY_DATE_PLACEHOLDER`); **el contrato gRPC sigue en ISO** y el servidor sigue validando con `analizar_fecha` — no se cambió el formato de cable, y hay un test que lo fija (`test_period_report_rejects_a_malformed_date`). Convertidos: fecha de nacimiento, vencimiento de cédula, primer vencimiento, columna "Vencimiento" del cronograma, desplegable de cuota a pagar, último acceso de usuarios, y los cuatro documentos. Cobertura: `tests/client/test_formatting.py`.
- [x] **"Préstamos Caducados" → "Préstamos Rechazados".** Cambio de etiqueta en el dashboard, en la vista de préstamos (`_ESTADOS_LABEL`) y en los documentos. `LoanStatusEnum.EXPIRED`, el campo `expired_loans_count` y la regla BR-LOAN-003 quedan intactos: es terminología, no un estado nuevo.
- [x] **Monto total de mora en el dashboard (`BR-DASH-001`).** Suma de todo lo vencido e impago de los préstamos ACTIVE, más un contador de préstamos en mora. Reutiliza `_estado_pago_prestamo` (BR-LOAN-009) en vez de recalcular, así el total del panel siempre coincide con la suma de los `overdue_amount` de cada préstamo. Se muestra en rojo (`theme.ERROR`) al lado del saldo pendiente justamente para que no se lean como lo mismo: **la mora es un subconjunto del saldo**, que además incluye las cuotas futuras.
- [x] **Reportes de cierre de período (`BR-DASH-002`).** Nueva RPC `GetPeriodReport(start_date, end_date)`, gateada `MANAGER_AND_ABOVE` (más arriba que `GetDashboardStats`, que es de todos los roles). Tarjeta nueva en el dashboard con atajos Mes actual / Mes anterior / Año actual, tabla de resultados y exportación a PDF/DOCX/impresión. El contenido del reporte se define una sola vez (`documents._filas_reporte`) y lo consumen la tabla, el PDF y el DOCX. Especificado en `specs/dashboard/README` (módulo nuevo).
- [x] **Revisión visual.** Se revisaron las capturas de `Templates y Detalles/` y se reprodujo la interfaz real (renderizando `MainWindow` a 1366×728 y al mínimo de 900×560):
  - La compresión de tarjetas del dashboard que muestran `Errores del Dashboard.png` / `error.png` **ya estaba corregida** por el `wrap_scrollable()` del Bloque 1 y no se pudo reproducir. Sin cambios.
  - **Encabezados de tabla recortados** ("Saldo restante (" en `Ajustes en la Tabla de Datos.png`): real y corregido con el helper nuevo `size_columns()` en `cas_client/widgets/table.py`, aplicado a las 7 tablas de la app. Caso aparte: la columna del botón "Ajustar" usa un ancho fijo derivado del `sizeHint()` del propio botón, porque `ResizeToContents` no mide widgets puestos con `setCellWidget`.
  - **Fila en blanco sobre la lista de clientes** (`Errores en la ventana de clientes.png`): no se pudo reproducir. Queda anotado en `CLAUDE.md` como pendiente de capturar en la máquina real antes de intentar un arreglo a ciegas.
- [x] **Estado final:** `black`/`flake8` limpios, `pytest` **186/186** (30 tests nuevos).

---

## Bloque 7 — Sesión 2026-08-13 (comprobante de pago y datos del operador)

Pedido del usuario: dos elementos nuevos. Estado inicial: `pytest` 186/186 en verde.

- [x] **`BR-AUTH-006` — datos personales del operador.** `users` suma `first_name`, `last_name` y `national_id` (C.I.), obligatorios en `CreateUser` y cargados desde `UsersView` (formulario + columnas nuevas en la tabla). Migración `875e90c71fc6_add_personal_data_to_users.py`: columnas nullable planas, sin backfill.
  - Los usuarios que ya existían siguen funcionando: sin datos personales, los documentos caen de vuelta a su `username`.
  - `seed_admin` los acepta como **opcionales** a propósito: es el único camino para crear el primer ADMIN cuando todavía nadie puede llamar a `CreateUser`, y exigirlos dejaría una instalación nueva sin forma de entrar.
  - `users.national_id` **no** lleva UNIQUE (a diferencia de `clients.national_id`): obligaría a distinguir cuál de las dos restricciones falló para devolver un mensaje correcto, y los operadores son pocos y los da de alta un ADMIN que ve la lista.
- [x] **`BR-LOAN-011` — Comprobante de Pago.** `RecordPayment` ahora devuelve monto abonado, referencia, fecha/hora, total de cuotas, operador que lo registró (nombre + C.I.) y **las cuotas que cubrió el pago**. El documento (PDF y `.docx`) imprime "Cuota(s) 1 de 18" o "Cuota(s) 1,2 de 18" según corresponda. Se emite desde la tarjeta "Documentos" del préstamo, habilitado solo después de registrar un pago en esa sesión.
  - **Decisión a revisar con el usuario:** las cuotas informadas salen de la imputación FIFO real del préstamo, no de la cuota que el operador eligió en el desplegable. Difieren cuando quedan cuotas anteriores impagas (el dinero siempre se imputa a la más antigua). Se eligió la imputación real para que el comprobante no contradiga el cronograma que el cliente puede ver; hay un test (`test_record_payment_covered_installments_match_the_schedule_fifo`) que fija esa coherencia.
  - Sin banner de borrador: no tiene texto legal, solo cifras de un pago ya registrado. El aviso de la tarjeta "Documentos" se acotó a Liquidación/Pagaré/Contrato.
- [x] **Arreglo visual encontrado de paso:** la columna del botón "Restablecer contraseña" en `UsersView` salía recortada al agregarle 2 columnas a la tabla — mismo caso que la columna "Ajustar" del cronograma (`ResizeToContents` no mide widgets puestos con `setCellWidget`), misma solución: ancho fijo derivado del `sizeHint()` del propio botón.
- [x] **Estado final:** `black`/`flake8` limpios, `pytest` **205/205** (19 tests nuevos).

---

## Verificado sin problemas — no se necesita ningún cambio aquí

(Para no perder tiempo mañana re-revisando lo que ya se confirmó que está bien: RBAC sin RPCs huérfanas, sin SQL injection, `rbac_ui.py`↔`rbac.py` sincronizados, cobertura de tests de las RPCs nuevas, migraciones con backfill seguro, `conftest.py` con las 7 tablas cubiertas, sin bugs de closures en loops de señales Qt, validación cliente-servidor consistente en campos requeridos. Detalle completo en ES-006 §6.)
