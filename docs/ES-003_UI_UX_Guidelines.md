# ES-003: Guías de UI/UX y Arquitectura del Cliente
**Proyecto:** CAS Project (Credit System)
**Versión:** 1.0.0
**Framework Frontend:** PySide6 (Python)

## 1. Visión General de la Interfaz
La aplicación cliente (`cas_client`) debe diseñarse como una aplicación de escritorio de una sola ventana (Single Page Application - SPA, adaptada a escritorio). Toda la navegación ocurrirá mediante el reemplazo de widgets en un área de contenido principal, manteniendo siempre visible un menú de navegación lateral constante.

## 2. Branding e Identidad Visual (Brand Guidelines)
El sistema debe proyectar autoridad, modernidad y eficiencia. Para lograrlo, la interfaz se regirá por los siguientes principios de diseño:
*   **Identificador de Marca:** El proyecto adoptará la denominación **"CrediUME"** como marca o identificador principal del sistema en la UI.
*   **Estilo del Logotipo:** El logotipo de "CrediUME" que se coloque en la parte superior del menú lateral debe tener un diseño de identidad tipo firma (tipografía cursiva elegante). Se deben eliminar los elementos ilustrativos complejos o recargados, manteniendo una estructura gráfica estrictamente minimalista.
*   **Inspiración Estética:** Toda la identidad visual debe alinearse con el diseño estructurado, limpio y premium que caracteriza a las marcas comerciales de los jugadores de fútbol de élite. El enfoque es transmitir alto rendimiento y exclusividad mediante componentes simples y espacios en blanco bien definidos.

## 3. Paleta de Colores y Tipografía

> **Nota (paleta real implementada):** los valores de esta sección son la intención original de ES-003. Por decisión de producto explícita, `cas_client/theme.py` implementa en su lugar la paleta "CrediMed" de `docs/Detalles de UI para CrediUme.txt` — navy `#2B407B` (`theme.PRIMARY`/`theme.SIDEBAR_BG`), lima `#8CC63F` (`theme.ACCENT`, solo como fondo, nunca como texto), esmeralda `#2E9344` (`theme.SUCCESS`), gris `#808285` (`theme.TEXT_MUTED`/`theme.BORDER`). Ver "Client UI conventions" en `CLAUDE.md` para el detalle completo; los valores originales de abajo quedan solo como referencia histórica, no como lo que la app realmente pinta hoy.

*   **Fondo Principal (App Background):** Gris muy claro (`#F8F9FA`) o Blanco Puro (`#FFFFFF`) para reducir la fatiga visual.
*   **Panel Lateral (Sidebar):** Tono oscuro y sobrio, como Azul Marino Profundo (`#1A252F`) o Negro Carbón (`#212121`).
*   **Acentos y Botones Primarios:** Un color de acción claro y contrastante, como un Azul Eléctrico o un Verde Esmeralda apagado, reservado solo para botones de guardado o confirmación (Ej. "Crear Préstamo", "Aprobar").
*   **Tipografía de Contenido:** 
    *   Primaria (Textos, Tablas, Inputs): Fuentes sans-serif limpias del sistema (Ej. *Segoe UI*, *San Francisco*, o *Inter*).
    *   Jerarquía: Títulos en Semi-Bold (20px-24px), textos base en Regular (14px).

## 4. Estructura de Componentes en PySide6
Todos los formularios y vistas deben heredar de clases base predefinidas para no repetir código:
*   `BaseView` (Widget central): Maneja el padding global y el título de la vista.
*   `StandardTable`: Hereda de `QTableWidget`. Debe configurarse por defecto sin bordes internos marcados, alternando colores por fila muy sutilmente (`#F2F2F2`), con encabezados en negrita y anchos de columna adaptativos (`QHeaderView.Stretch`).
*   `FormInput`: Hereda de `QLineEdit`. Debe tener un borde inferior o un contorno redondeado simple, con un margen (padding) interno de al menos 8px para una sensación de espacio (breathing room).

## 5. Manejo de Estado y Experiencia de Usuario (UX)
*   **Retroalimentación Inmediata:** Cualquier operación que dependa de gRPC (Ej. Buscar un cliente, calcular amortización) debe mostrar un indicador visual de carga (Spinner o ProgressBar) en la interfaz mientras se espera la respuesta del servidor.
*   **Manejo de Errores gRPC:** Los códigos de error retornados por el servidor deben capturarse y traducirse a mensajes amigables. 
    *   *Ejemplo:* Si el servidor retorna `INVALID_ARGUMENT` por un DNI duplicado, la UI no debe romperse ni mostrar un traceback, sino desplegar un componente flotante (Toast o Snackbar) con el texto: *"El documento ingresado ya está registrado"*.
*   **Validación Local:** Antes de enviar un request gRPC, el cliente PySide6 debe validar que los campos obligatorios no estén vacíos y que los formatos sean correctos (usando Expresiones Regulares o `QValidator`).

## 6. Jerarquía de Vistas (Vistas a Desarrollar)
1.  **Vista de Autenticación (Login):** Pantalla centrada, sin menú lateral.
2.  **Dashboard:** Gráficos resumen o KPIs principales del día (Caja, Préstamos activos).
3.  **ClientList / ClientDetail:** Tabla paginada de clientes y vista de formulario tipo tarjeta para ver/editar perfil.
4.  **LoanCreator:** Asistente (Wizard) de 3 pasos: Buscar Cliente -> Configurar Montos -> Confirmar Cuadro de Amortización.