# ES-006 — Auditoría integral del sistema CrediUME/CAS

**Fecha:** 2026-08-12
**Alcance:** `cas_server/` (lógica de negocio, seguridad, RBAC, persistencia), `cas_client/` (UI de escritorio PySide6), `protos/`, `specs/`, `tests/`, migraciones, tooling/CI e higiene del repositorio.
**Método:** revisión de código línea por línea (sin ejecutar la app salvo capturas de pantalla ya existentes en `Templates y Detalles/`), más `flake8`/`black --check` reales sobre el árbol actual. No se modificó ningún archivo de producto durante la auditoría.

Este documento reemplaza a `docs/MEETING_PREP_NOTES.md` como el registro de gaps más reciente y **complementa, no reemplaza**, la sección "Recommendations for the next session" de `CLAUDE.md` — los ítems ya conocidos ahí se listan también aquí (§5) para tener una sola lista priorizada, pero no se repite el detalle técnico que ya está documentado allí.

---

## 1. Resumen ejecutivo

El sistema está, en general, en buen estado: no se encontró inyección SQL (ORM en el 100% de las consultas), la capa de RBAC servidor no tiene RPCs huérfanas (las 26 RPCs de los `.proto` están todas cubiertas en `rbac.py`), la cobertura de tests de lógica de negocio es más completa de lo que el propio `CLAUDE.md` sugiere, y la sincronización manual entre `rbac_ui.py` (cliente) y `rbac.py` (servidor) — un punto marcado como frágil en `CLAUDE.md` — está de hecho correcta en todos los puntos verificados.

Los problemas reales encontrados caen en tres grupos:

1. **Un bug visual activo y reproducible** en el Dashboard (texto superpuesto/recortado en las tarjetas de estadísticas — confirmado con capturas reales) con causa raíz identificada y no documentada en ningún lado.
2. **Desactualización de documentación**: `CLAUDE.md` y `specs/` no mencionan funcionalidad real ya implementada y probada (gestión de usuarios, ajuste de cuotas, `ListActiveLoans`, el estado `EXPIRED`).
3. **Gaps de concurrencia** en el servidor (sin bloqueo de filas) que son teóricamente explotables pero de bajo riesgo práctico en un despliegue LAN de baja concurrencia — se documentan para que sea una decisión consciente, no un descubrimiento accidental.

Ningún hallazgo requiere una respuesta de emergencia; se recomienda tratar la sección 2 (Alta) en la próxima sesión de desarrollo.

---

## 2. Hallazgos de prioridad ALTA

### 2.1 Bug visual activo: superposición/recorte de texto en las tarjetas del Dashboard
**Evidencia:** `Templates y Detalles/error.png`, `Errores del Dashboard.png`, `Error recurrente en el dashboard.png` (capturas reales de la app corriendo, no hipotéticas).

**Causa raíz identificada:** `DashboardView` (`cas_client/views/dashboard_view.py`) es la única vista de contenido principal que se agrega directo a `_content_stack` **sin** pasar por `wrap_scrollable()` — compárese `main_window.py:155` (`self._content_stack.addWidget(self._dashboard_view)`, sin wrap) contra `main_window.py:158` (`wrap_scrollable(users_view)`, con wrap). `ClientsView`/`LoansView` también se agregan sin wrap a ese nivel externo, pero eso es intencional porque *sus propias* páginas largas ya están envueltas internamente; `DashboardView` no tiene ningún `QScrollArea` interno (todo el archivo `dashboard_view.py`) — apila encabezado + tres bloques `section_label`+`ResponsiveGrid` (2+6+2 tarjetas) directo en `BaseView.content_layout`.

`cas_client/widgets/scroll_area.py:9-11` describe exactamente este modo de falla: contenido sin contenedor de scroll se comprime forzadamente al viewport visible una vez que excede la altura de la ventana, y los hijos (los `QLabel` de `stat_tile()`) se encogen por debajo de su tamaño natural — el texto no se encoge con la caja, así que las etiquetas se superponen visualmente, y una caja comprimida más corta que la altura de línea de una etiqueta la recorta por arriba. Con el tamaño mínimo declarado de la ventana (`main_window.py:202`, 900×560) menos los márgenes de `BaseView` (32px, `base_view.py:20`) y la barra de encabezado, las tres filas de `ResponsiveGrid` probablemente exceden la altura disponible. Nada en `stat_tile()` (`card.py:126-158`) ni en `ResponsiveGrid._reflow` (`responsive_grid.py:49-57`) reserva una altura mínima por fila, así que no hay nada que frene la compresión una vez que el contenedor externo se queda sin espacio.

Los arreglos ya documentados en `card.py` (`_ElidingLabel` de una sola línea, reconstrucción completa de tiles en cada refresh) resuelven dos causas distintas y más angostas (ambigüedad de word-wrap, artefactos de repintado obsoleto) pero **no** esta causa arquitectónica — de ahí que el bug siga reproduciéndose.

**Arreglo recomendado:** envolver el contenido de `DashboardView` con `wrap_scrollable()` igual que `UsersView` en `main_window.py:158`, o darle a `DashboardView` su propio `QScrollArea` interno como ya hacen `ClientsView`/`LoansView` para sus páginas largas.

### 2.2 Generación de documentos sin manejo de errores (traceback crudo al usuario)
**Archivo:** `cas_client/views/loans_view.py:1414-1469` (`_render_document`/`_save_document_docx`).

Las llamadas de E/S de archivo (`document.print_(printer)`, `docx_document.save(path)`) se ejecutan sin `try/except` y sin pasar por el patrón `_friendly_message` que usa el resto de la app. Un archivo de destino bloqueado/abierto (p. ej. el `.docx` abierto en Word), disco lleno, o un error de permisos lanza una excepción no capturada (`OSError`/`PermissionError`) que llega al usuario como traceback crudo — violando la regla explícita de ES-003 §5 ("nunca mostrar un traceback crudo o un código gRPC") que el resto del cliente sí respeta.

### 2.3 Secretos reales sin `.gitignore` en el working tree
**Archivos:** `cas_server/.env`, `cas_client/.env` (existen junto a sus `.env.example`); no existe ningún `.gitignore` en el repo.

`cas_server/.env` contiene `POSTGRES_PASSWORD` y `JWT_SECRET_KEY` reales. Como el proyecto todavía no tiene control de versiones (ver §5), estos archivos no están expuestos *hoy* vía git — pero es exactamente el tipo de archivo que se filtra por accidente en el primer `git add -A`/`git init` si no hay un `.gitignore` preparado de antemano. Se recomienda crear el `.gitignore` (excluyendo `*.env`, conservando `*.env.example`) **antes** de inicializar el repositorio, no después.

### 2.4 Texto legal placeholder en documentos generados *(ya conocido, se reconfirma vigente)*
Sigue siendo el bloqueante de más alto impacto antes de cualquier uso con un cliente real — ver `CLAUDE.md` §"Recommendations" #1 para el detalle completo. No se encontró ningún avance sobre esto en el código actual.

---

## 3. Hallazgos de prioridad MEDIA

### 3.1 Condiciones de carrera en el servidor (sin bloqueo de filas)
Ningún flujo usa `SELECT ... FOR UPDATE` ni control de concurrencia optimista; todas las sesiones corren en `READ COMMITTED` (default de Postgres, `db/base.py:9`). Riesgo bajo en un despliegue LAN de baja concurrencia, pero real:

- **BR-LOAN-001 (máx. 3 préstamos activos):** `CreateLoan` (`loan_service.py:303-315`) y `ApproveLoan` (`:620-634`) cuentan préstamos activos con un `SELECT` simple. Dos solicitudes concurrentes para el mismo cliente pueden ambas leer `cantidad_activos < 3` antes de que cualquiera haga commit, permitiendo superar el tope.
- **Duplicados concurrentes sin manejo de `IntegrityError`:** `CreateClient` (`client_service.py:163-181`), `UpdateClient` (`:318-327`), `UpdateNationalId` (`:430-441`) y `AuthServicer.CreateUser` (`auth_service.py:192-198`) validan "¿ya existe?" con un `SELECT` previo al insert, pero `sesion.commit()` nunca está envuelto en `try/except IntegrityError`. Dos altas concurrentes con el mismo `national_id`/email/username: la perdedora recibe un `INTERNAL`/`UNKNOWN` genérico de gRPC en vez del `ALREADY_EXISTS` esperado (la restricción única de la BD sí evita el dato duplicado — esto es un problema de contrato de error, no de integridad de datos).
- **`RecordPayment` concurrente puede dejar `status` desactualizado tras el pago completo:** `loan_service.py:753-789`. Dos llamadas simultáneas que en conjunto saldan el préstamo, pero que individualmente no ven el total combinado ≥ `total_programado`, pueden dejar el préstamo sin pasar a `PAID` aunque `remaining_balance` (recalculado siempre al vuelo) ya muestre 0. Consecuencia práctica: `DeactivateClient` (BR-CLI-004) seguiría bloqueando la baja de un cliente cuyo préstamo en realidad ya está saldado, hasta que otra llamada a `RecordPayment` reevalúe el estado.

**Recomendación:** documentar esto como riesgo aceptado (dado el modelo de despliegue LAN de baja concurrencia, ES-004) o agregar `with_for_update()` en los tres puntos si el volumen de usuarios simultáneos llega a preocupar.

### 3.2 Documentación (`CLAUDE.md` y `specs/`) desactualizada respecto al código real
Se confirmó funcionalidad real, probada y con RBAC correcto que no aparece descrita en ningún documento del proyecto:

- **Gestión de usuarios** (`CreateUser`/`ListUsers` en `auth_service.py`, pantalla `UsersView`/"Usuarios" en el sidebar) — ausente de la sección "Business modules" de `CLAUDE.md` y de `specs/authentication/README`.
- **`BR-LOAN-008/009/010`** (pago dirigido a una cuota específica vía `RecordPayment(installment_number=...)`, ajuste manual de cuota vía `UpdateInstallmentAmount`, y el cálculo de estado de mora `_estado_pago_prestamo`) y la RPC **`ListActiveLoans`** — todo implementado, con RBAC (`MANAGER_AND_ABOVE` para `UpdateInstallmentAmount`) y con tests reales (`test_loan_service.py:631-776`), pero ausente tanto de `CLAUDE.md` como de `specs/loans/README` (que solo documenta hasta BR-LOAN-007).
- `specs/loans/README:25` tampoco incluye el estado `EXPIRED` en el enum documentado, aunque existe en `models.py:184` y está implementado (BR-LOAN-003).
- `specs/authentication/README` no menciona la tabla `RevokedToken` ni la columna `locked_until`, ambas centrales al mecanismo de logout/lockout ya implementado.

**Recomendación:** actualizar `specs/authentication/README` y `specs/loans/README` (son la fuente autoritativa de reglas de negocio según el propio `CLAUDE.md`) y agregar estos puntos a la sección "Business modules" de `CLAUDE.md` en la próxima sesión que toque estas áreas.

### 3.3 `black --check` falla en 20 archivos — no hay exclusión de código generado
`flake8` sí excluye `*_pb2*.py`/`versions/` vía `setup.cfg`, pero no existe ningún `pyproject.toml`/config de `black` con el mismo exclude. `black --check cas_client cas_server tests` reformatearía 20 archivos, incluyendo dos archivos de test reales (`test_loan_service.py`, `test_loan_interceptor_integration.py`), una migración, y `cas_client/views/users_view.py` — además de todos los `*_pb2*.py`. El comando documentado en `CLAUDE.md` (`black cas_client cas_server tests`) hoy no pasa limpio.

**Recomendación:** agregar un `pyproject.toml` con `[tool.black] extend-exclude` para los mismos patrones que ya excluye `flake8`, y correr `black` una vez sobre los archivos reales que sí necesitan formateo.

### 3.4 Archivos generados sueltos en la raíz del repositorio
`contrato_91cc3960.pdf`, `liquidacion_cba4ec88.pdf`, `pagare_91cc3960.pdf` — nombrados con el patrón `documents.py`/`documents_docx.py` (UUID corto), consistentes con salidas de prueba/walkthrough, no datos fabricados como reales. Aun así, no deberían vivir en la raíz del repo. Recomendación: moverlos a una carpeta ignorada (o borrarlos) y agregar el patrón `*.pdf`/`*.docx` de salida al futuro `.gitignore`.

### 3.5 `DashboardView` no cancela una carga en curso al volver a la pestaña
`dashboard_view.py:159-163` (`showEvent`) llama `_refresh_stats()` cada vez que se vuelve al Dashboard, lanzando un nuevo `AsyncWorker` sin cancelar uno en vuelo. `AsyncWorker._ACTIVE_WORKERS` (`async_worker.py:17`) evita el crash de QThread destruido en ejecución, así que no hay fuga — pero un cambio de pestaña rápido puede dejar dos respuestas corriendo y la UI mostrando la que llegue última. Impacto: cosmético/UX, no funcional.

---

## 4. Hallazgos de prioridad BAJA

| # | Hallazgo | Ubicación |
|---|---|---|
| 4.1 | Una línea de 135 caracteres (límite: 100) | `cas_client/views/loans_view.py:1327` |
| 4.2 | `AuditLog.timestamp` tiene un default *timezone-naive* (`datetime.utcnow()`) inconsistente con `DateTime(timezone=True)` y con todos los call sites reales (que sí pasan `datetime.now(timezone.utc)` explícito). Hoy inalcanzable — bug latente si algún nuevo call site omite el parámetro. | `cas_server/db/models.py:72` |
| 4.3 | `DisburseLoan` tiene el mismo patrón sin bloqueo de fila que 3.1, pero de bajo impacto: solo duplicaría una línea de auditoría, no movimiento de dinero real. | `cas_server/services/loan_service.py:655-689` |
| 4.4 | `ResponsiveGrid.add_widget()` fuerza un `_reflow()` completo (O(N²) en total) en cada widget agregado — inofensivo a la escala actual (≤6 tiles), a vigilar si una grilla crece mucho. | `cas_client/widgets/responsive_grid.py:28-30` |
| 4.5 | `_ElidingLabel.__init__` no aplica el elide en su primer render (usa `super().setText()` en vez de `_apply_elided_text()`) — se autocorrige en el primer resize antes de pintar, pero es una inconsistencia real del propio invariante de la clase. | `cas_client/widgets/card.py:40-49` |
| 4.6 | `docs/ES-003_UI_UX_Guidelines.md:17` todavía describe la paleta antigua (`#1A252F`) en vez de la paleta CrediMed actual (`#2B407B`/`#8CC63F`) que ya está en `theme.py`. Cosmético — `CLAUDE.md` ya deja constancia del cambio. | `docs/ES-003_UI_UX_Guidelines.md` |
| 4.7 | `cas_client/views/users_view.py` no está en el inventario de vistas de `CLAUDE.md`. | — |

---

## 5. Ítems ya conocidos que siguen vigentes (llevados de `CLAUDE.md`)

Se listan aquí solo para tener una única lista priorizada; el detalle técnico completo de cada uno ya está en `CLAUDE.md` → "Recommendations for the next session" y no se repite:

1. Revisión legal de los documentos generados (Liquidación/Pagaré/Contrato) — **bloqueante para producción**, no para demo.
2. TLS diferido en el canal gRPC — decisión de despliegue pendiente, no un olvido.
3. El proyecto no tiene control de versiones — se recomienda hacerlo **junto con** el `.gitignore` de §2.3, no antes.
4. Falta una carpeta de capturas de pantalla para revisar cada vista rediseñada contra `docs/Detalles de UI para CrediUme.txt`.
5. `cas_client/assets/app_icon.ico` es una sola resolución no cuadrada — sin urgencia.
6. Confirmar con los dueños del producto que el set de estadísticas del Dashboard es el que quieren ver.
7. Notificaciones SMS evaluadas pero no implementadas (requieren un scheduler que hoy no existe en el proyecto).
8. Fuentes de marca (Comfortaa/Inter) no empaquetadas — el render exacto depende de qué haya instalado cada máquina.
9. Búsqueda de préstamos standalone requiere pegar un UUID de cliente a mano.
10. Cobertura de tests de `cas_client` (Qt) sigue siendo solo `rbac_ui.py` — nada cubre las vistas mismas (necesitaría `pytest-qt`).
11. Warning cosmético de versión de protobuf en cada arranque — deliberadamente pospuesto.

---

## 6. Verificado sin problemas (para no re-auditar innecesariamente)

- **Sin inyección SQL:** el 100% de las consultas usa el ORM de SQLAlchemy (`session.get`/`session.query`); no se encontró SQL crudo con interpolación de strings en ningún servicio.
- **Cobertura de RBAC completa:** las 26 RPCs definidas en `protos/*.proto` están todas presentes en `cas_server/security/rbac.py` — no hay endpoints inalcanzables por omisión.
- **Sincronización `rbac_ui.py` ↔ `rbac.py`:** se verificaron todas las funciones de gating del cliente (`role_at_least`, `can_manage_users`, `can_edit_interest_rate`, `can_edit_installment_amount`) contra los tiers reales del servidor para cada acción gateada — coinciden en todos los puntos revisados.
- **BR-LOAN-001/BR-LOAN-002** están correctamente implementados en el caso no concurrente (operadores de comparación correctos); `UpdateLoanProposal` revalida BR-LOAN-002 y omite BR-LOAN-001 intencionalmente, tal como documenta `CLAUDE.md`.
- **Cobertura de tests de las RPCs más nuevas** (`UpdateInstallmentAmount`, `UpdateLoanGuarantee`/`UpdateLoanCharges`, `CreateUser`/`ListUsers`, `GetDashboardStats`) es más fuerte de lo que el silencio de `CLAUDE.md` sobre ellas sugiere — todas tienen aserciones de negocio reales, no solo cobertura de RBAC.
- **Migraciones recientes** usan el patrón seguro de 3 pasos (columna nullable → backfill → `NOT NULL`) cuando corresponde; no se encontraron `downgrade()` rotos o faltantes en las revisadas.
- **`tests/conftest.py`** — su lista de `TRUNCATE` cubre las 7 tablas actuales de `cas_server/db/models.py`, incluyendo `LoanInstallmentAdjustment` (la más reciente); no hay riesgo de fuga de estado entre tests hoy.
- **Sin bugs de closures de Python en loops de señales Qt** — todos los `lambda` conectados dentro de un `for` en `clients_view.py`/`loans_view.py`/`users_view.py` usan binding correcto por argumento por defecto.
- **Validación cliente-servidor consistente** en los campos requeridos verificados: `transfer_reference` (RecordPayment), `source_of_funds` y las referencias personales/laborales (BR-CLI-005/006) se validan tanto en el cliente como en el servidor con las mismas reglas.
- **Argon2, expiración de JWT y el mecanismo de lockout (BR-AUTH-002)** están implementados correctamente, incluyendo el reset del contador de intentos al loguear con éxito.

---

## 7. Plan de acción sugerido (orden recomendado)

1. Arreglar el bug del Dashboard (§2.1) — es visible, reproducible, y el fix es acotado (`wrap_scrollable()`).
2. Envolver la generación de documentos en manejo de errores (§2.2) — riesgo de traceback crudo en una función que se usa en cada demo/operación real.
3. Crear `.gitignore` **antes** de inicializar git (§2.3 + §5.3) — evita que el primer commit filtre credenciales.
4. Actualizar `specs/authentication/README` y `specs/loans/README`, y la sección "Business modules" de `CLAUDE.md` (§3.2) — deuda de documentación que crece con cada sesión que no se registra.
5. Agregar `pyproject.toml` con exclude de `black` (§3.3) — barato, deja el comando documentado en `CLAUDE.md` funcionando de verdad.
6. Decidir conscientemente sobre las condiciones de carrera (§3.1): aceptar el riesgo dado el modelo LAN, o agregar `with_for_update()` en los tres puntos señalados.
7. El resto (§4 y §5) puede abordarse oportunistamente, sin bloquear ninguna entrega.
