# ES-001: Project Charter (Acta de Constitución)
**Proyecto:** CAS Project (Credit System)

## 1. Propósito
Desarrollar un sistema de gestión de créditos centralizado, seguro y escalable que permita administrar carteras de clientes, otorgamiento de préstamos, cálculo de intereses y seguimiento de amortizaciones.

## 2. Alcance (In Scope)
*   **Gestión de Usuarios:** Autenticación y autorización basada en roles (Ej: Cajero, Administrador, Analista de Crédito).
*   **Gestión de Clientes (KYC):** Registro, actualización y evaluación del historial crediticio de los solicitantes.
*   **Motor de Préstamos:** 
    *   Creación de solicitudes de crédito.
    *   Cálculo de cuadros de amortización (sistemas francés, alemán, etc.).
    *   Registro de desembolsos y cobros.
*   **Reportes Básicos:** Estado de cartera, ingresos proyectados vs. reales, y morosidad.

## 3. Fuera de Alcance (Out of Scope)
*   Pasarelas de pagos en línea o integración con tarjetas de crédito/débito externas (en la Fase 1).
*   Aplicación móvil para los clientes finales (el sistema es de uso interno).

## 4. Supuestos y Restricciones
*   El sistema operará bajo una red interna (Intranet) o VPN, por lo que los certificados SSL/TLS para gRPC serán gestionados internamente.
*   La UI debe estar optimizada para resoluciones de escritorio estándar (1920x1080).