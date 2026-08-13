# ES-000: Especificación de Ingeniería y Arquitectura (SDD)
**Proyecto:** CAS Project (Credit System)
**Versión:** 1.0.0

## 1. Visión General de la Arquitectura
El sistema utiliza una arquitectura Cliente-Servidor comunicada vía gRPC, asegurando un tipado estricto y alta eficiencia en la transferencia de datos.

*   **Cliente (`cas_client`):** Aplicación de escritorio desarrollada en Python con **PySide6** (Qt). Maneja la capa de presentación y la validación de entrada inicial.
*   **Servidor (`cas_server`):** Backend en Python. Maneja la lógica de negocio, validaciones profundas y la persistencia de datos.
*   **Base de Datos:** **PostgreSQL**. Contiene la fuente de verdad del sistema, incluyendo esquemas de clientes, préstamos y amortizaciones.
*   **Comunicación (`protos/`):** Protocol Buffers (gRPC) define los contratos entre el cliente y el servidor.

## 2. Estándares de Codificación
*   **Lenguaje:** Python 3.11+
*   **Estilo:** PEP-8 (usando `black` y `flake8`).
*   **Type Hinting:** Obligatorio en el 100% de las funciones del servidor y cliente.
*   **Testing:** `pytest` para pruebas unitarias e integración en el directorio `/tests`.

## 3. Manejo de Estado y Errores
*   El servidor debe retornar códigos de estado gRPC estándar (`OK`, `INVALID_ARGUMENT`, `NOT_FOUND`, `UNAUTHENTICATED`).
*   El cliente nunca interactúa directamente con la base de datos; todas las peticiones pasan por los stubs de gRPC.

## 4. Índice de Especificaciones Modulares
Las reglas de negocio detalladas se encuentran en el directorio `/specs`:
*   [Autenticación](../specs/authentication/README.md)
*   [Gestión de Clientes](../specs/clients/README.md)
*   [Gestión de Préstamos](../specs/loans/README.md)