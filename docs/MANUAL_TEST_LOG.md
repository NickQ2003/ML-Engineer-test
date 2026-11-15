# Bitácora de pruebas manuales

Este documento registra pruebas cuyo **criterio de aceptación lo determina una
persona** mediante inspección directa. Una herramienta puede generar o mostrar
los datos, pero una evaluación de IA no sustituye la revisión ni la aprobación
humana. Registrar únicamente observaciones comunicadas por quien revisó; no
suponer resultados ni incluir datos sensibles.

Las pruebas automatizadas, comandos y resultados técnicos se registran por
separado en [TEST_LOG.md](TEST_LOG.md).

## MT-001 — Ubicación visual de palabras OCR

| Campo | Valor |
|---|---|
| Fecha del registro | 2026-11-15 |
| Persona revisora | Usuario del proyecto; nombre no proporcionado |
| Resultado | **Aprobado por la persona revisora**, para el documento inspeccionado |
| Documento original | `sample_pdfs/meeting_minutes.pdf` |
| Evidencia visual | `docs_dev/meeting_minutes_ocr_visual.pdf`, copia local excluida de Git |
| Alcance | Cuadros de palabras OCR en las dos páginas |

### Preparación y separación de responsabilidades

El servicio OCR generó los cuadros y se utilizó un script preparado con ayuda
del asistente para dibujarlos sobre una copia del PDF. La **evaluación registrada
a continuación es la observación comunicada por el usuario**, no una aprobación
automática ni un resultado inferido por IA. El procedimiento de generación y el
recuento técnico están en [TEST_LOG.md](TEST_LOG.md#2026-11-15--revisión-visual-preliminar-de-cuadros-ocr).

### Procedimiento manual

1. Abrir la copia local con los cuadros rojos superpuestos.
2. Revisar visualmente las dos páginas, con zoom cuando sea necesario.
3. Comprobar que los cuadros cubran las palabras sin cortarlas ni desplazarse.
4. Revisar los nombres visibles y buscar palabras sin cuadro o cuadros en vacío.

### Observaciones comunicadas por la persona revisora

- Los cuadros cubren las palabras y no las cortan, aunque no todos tienen el
  mismo tamaño.
- Los nombres visibles revisados, incluidos los mencionados durante la prueba,
  están correctamente encerrados.
- Las palabras están rodeadas por rectángulos, incluso cuando incluyen signos
  como `-`, `,`, `:` o `.`.

### Limitaciones

- Se revisó un PDF de ejemplo; esto no valida todos los documentos ni todos los
  casos de rotación o recorte.
- Los rectángulos corresponden a **palabras OCR**, no a nombres completos
  detectados por NER. Esa funcionalidad aún no está implementada.
- La inspección visual de cuadros no verifica por sí sola que el texto reconocido
  coincida carácter por carácter ni establece una métrica de precisión.

## Plantilla para próximas pruebas manuales

```markdown
## MT-XXX — Nombre de la prueba
- Fecha:
- Persona revisora:
- Entorno y versión o commit:
- Objetivo y alcance:
- Herramientas utilizadas para preparar y visualizar la evidencia:
- Pasos realizados por la persona:
- Resultado esperado:
- Observaciones comunicadas por la persona:
- Resultado: aprobado / fallido / parcial / pendiente.
- Evidencia disponible y ubicación:
- Limitaciones y acciones pendientes:
```
