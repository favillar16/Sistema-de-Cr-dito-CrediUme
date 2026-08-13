# Notas de preparación — próxima sesión antes de la reunión con los dueños

**Contexto:** en la sesión del 2026-08-04 se agregaron referencias personales/laborales de clientes (`BR-CLI-005`), se reemplazó la paleta del cliente por la del doc "CrediMed", se rediseñó la ventana de préstamos, se agregó generación de documentos (Liquidación/Pagaré/Contrato) en PDF, y se evaluó (sin implementar) el envío de notificaciones por SMS. Ver `CLAUDE.md` → "Recommendations for the next session" para el detalle técnico completo; este archivo es el recorte orientado a **decisiones que sólo los dueños pueden tomar** antes de mostrarles el sistema, más una lista de pulido rápido antes de la demo.

## 1. Decisiones que necesitan feedback real de los dueños

Estas son suposiciones que tomé para poder avanzar, no confirmaciones del negocio. Cada una cambia código si la respuesta es distinta:

1. **Tasa de interés fija (24% anual, `FIXED_INTEREST_RATE` en `cas_client/rbac_ui.py`).** Es un placeholder razonable, no un dato dado por el dueño. Preguntar: ¿es correcta? ¿debería variar según plazo/monto/producto en lugar de ser una constante única?
2. **Set de referencias (BR-CLI-005): 2 personales (nombre/parentesco/teléfono) + 1 laboral (empleador/cargo/teléfono/antigüedad).** Confirmar que este set alcanza para su proceso de KYC real -- ¿falta dirección de la referencia, tiempo de conocerla, referencia comercial además de laboral, etc.?
3. **Paleta de marca (navy/lima/esmeralda, del doc "Detalles de UI para CrediUme.txt").** Este es ya el *segundo* cambio de paleta del proyecto (DS10 → ES-003 → esta). Antes de invertir más en el detalle visual, confirmar con los dueños que esta paleta es la definitiva y no una tercera exploración más.
4. **Texto legal de Liquidación/Pagaré/Contrato (`cas_client/documents.py`) es un borrador genérico, no texto legal real.** Esto es lo más urgente de resolver antes de cualquier uso real: necesitamos que un abogado (o el dueño, si ya tiene el texto) provea las cláusulas reales -- tasas de interés máximas legales, requisitos de la ley de protección al consumidor, formato exigido para pagarés en Paraguay, etc. No se puede usar el sistema con un cliente real hasta que esto se resuelva.
5. **Notificaciones SMS (`docs/ES-005_SMS_Notifications_Evaluation.md`): ¿vale la pena invertir?** Es la pieza de mayor esfuerzo evaluada (requiere un scheduler que hoy no existe en el proyecto). Antes de scopearlo: ¿qué eventos les importa notificar (aprobación, vencimiento, mora)? ¿hay presupuesto para un proveedor de SMS? Esto define si conviene limitarlo a eventos síncronos (bajo esfuerzo) o incluir recordatorios programados (alto esfuerzo).

## 2. Pulido rápido antes de la demo (no requiere decisión de negocio, sólo tiempo)

- Los documentos generados no tienen logo/isotipo real, sólo el texto "CrediUME" -- si van a mostrarse en la reunión, considerar agregar el logo real antes.
- Las fuentes de marca (Comfortaa/Inter) no están empaquetadas con la app -- en la máquina de la demo, instalarlas primero o el texto se verá con la fuente del sistema (Segoe UI), no con la tipografía de marca.
- Limpiar o dejar claro qué es dato de prueba: usuario `smoke_admin`, cliente "Juana Perez" (documento `99988877`) y su préstamo, creados al validar el flujo completo esta sesión. No conviene mostrarlos en la demo sin aclarar que son de prueba.
- El dashboard sigue siendo sólo un mensaje de bienvenida -- si los dueños van a ver la pantalla principal, aclarar que las estadísticas (cantidad de clientes, préstamos pendientes, etc.) todavía no existen, para que no se lean como "no funciona" sino como "no implementado aún".

## 3. Gaps conocidos para mencionar proactivamente en la reunión

Mejor decirlos nosotros que que los encuentren ellos:

- La tasa de interés personalizada (`Agente de Crédito`/`Administrador`) no está reforzada en el servidor, sólo ocultada en la UI -- cualquier usuario con acceso a la API podría enviar una tasa distinta a la fija.
- No hay tests automáticos para `cas_client` (sólo para `cas_server`).
- La búsqueda de préstamos standalone requiere pegar un UUID de cliente a mano; el único camino amigable es "Ver préstamos" desde la ficha de un cliente.
