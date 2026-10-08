# Capturas de la verificación manual (Iteración 9)

Tomadas con la vista previa del navegador integrado (`.claude/launch.json` → `streamlit`), contra el índice de
producción, 2026-10-07. Son JPG de 800 px de ancho (la vista previa escala la ventana emulada de 1400×900; en
algunas el contenido aparece recortado o con zoom del 60 %). Sirven como evidencia; para el README y el video
conviene volver a tomarlas a pantalla completa (ver "Pendientes para el usuario").

| Archivo | Qué muestra | VC |
|---|---|---|
| `subir-pdf-vacio.jpg` | Pestaña Subir PDF al abrir la app | — |
| `vc070-ingesta-en-curso.jpg` | `st.status` con el nodo en curso ("Leyendo el PDF…") | VC-070 |
| `vc070-ingesta-pasos-en-vivo.jpg` | Status abierto con las líneas `load_pdf → extract_tables → collect_context` mientras corre | VC-070 |
| `vc070-ingesta-terminada.jpg` | Ingesta terminada: "5 tablas indexadas en 10 s." | VC-070 |
| `vc070-pasos-del-grafo.jpg` | Resumen con contadores y "Pasos del grafo" (load_pdf … index) | VC-070 |
| `vc029-detalle-tabla.jpg` | Detalle de una tabla ingerida: columnas (tipo, dtype, unidad, descripción), encabezados de fila, vista previa de 5 filas | VC-029 |
| `vc071-explorar-lista.jpg` | Explorar: documentos, nombre, descripción, fuente, "Ver tabla completa" | VC-071 |
| `vc071-explorar-tabla-completa.jpg` | Explorar con conteo de vectores (33 / 5) y una tabla abierta | VC-071 |
| `vc072-confirmar-borrado.jpg` | Confirmación de borrado del documento CEPAL | VC-072 |
| `vc072-documento-borrado.jpg` | Aviso "Se borró …: 5 vectores y 5 tablas locales (… quedan en Pinecone: 0)"; 28 tablas en 2 documentos | VC-072 |
| `vc073-preguntar-en-curso.jpg` | Pregunta en curso con el nodo actual ("Redactando la respuesta…") | VC-073 |
| `vc073-respuesta-answered.jpg` | Respuesta `answered` (7,9 % y 0,3 p.p.): badge, texto, reintentos, valores, tablas | VC-073 |
| `vc073-tabla-seleccionada.jpg` | Tabla elegida `t1` abierta con sus datos | VC-073 |
| `vc073-codigo-generado.jpg` | Código generado (`st.code`) | VC-073 |
| `vc073-vc075-salida-y-traza.jpg` | Salida de la ejecución (returncode, duración, stdout) y link "Ver la traza en LangSmith" | VC-073, VC-075 |
| `vc073-historial-cannot-answer.jpg` | Segunda pregunta en el mismo historial: `cannot_answer` con datos faltantes | VC-073 |

## Pendientes para el usuario (para el README / video de la Iteración 11)

1. Subir un PDF **arrastrándolo** al área de carga (la automatización lo cargó en el `<input type=file>`).
2. Abrir el link "Ver la traza en LangSmith" de una respuesta con la sesión de LangSmith iniciada y capturar la traza.
3. Capturas a pantalla completa de: ingesta en curso (status abierto), resumen por tabla, Explorar, y una respuesta
   con código y salida abiertos.
4. (Opcional) App sin una variable obligatoria en `.env` → error de configuración (FR-002).
