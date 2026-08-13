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

## Verificado sin problemas — no se necesita ningún cambio aquí

(Para no perder tiempo mañana re-revisando lo que ya se confirmó que está bien: RBAC sin RPCs huérfanas, sin SQL injection, `rbac_ui.py`↔`rbac.py` sincronizados, cobertura de tests de las RPCs nuevas, migraciones con backfill seguro, `conftest.py` con las 7 tablas cubiertas, sin bugs de closures en loops de señales Qt, validación cliente-servidor consistente en campos requeridos. Detalle completo en ES-006 §6.)
