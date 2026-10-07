# PDFs de muestra — fuentes

PDFs reales y públicos usados como datos de demo y de evaluación (spec FR-070). Los tres
tienen capa de texto. Los proveyó el usuario el 2026-10-07 (fecha de descarga según los
archivos), obtenidos de las URLs de abajo; en el repo se renombraron y el de CEPAL se recortó.

| Archivo | Título | Organismo | Período | Páginas originales | Páginas que se conservan | URL |
|---|---|---|---|---|---|---|
| `indec_eph_mercado_trabajo_2t2026.pdf` | Mercado de trabajo. Tasas e indicadores socioeconómicos (EPH). Segundo trimestre de 2026 (Informes técnicos, Trabajo e ingresos, Vol. 10, n° 8; publicado el 17 de septiembre de 2026) | INDEC (Instituto Nacional de Estadística y Censos, Argentina) | 2.º trimestre de 2025 a 2.º trimestre de 2026 | 26 | 1–26 (completo) | https://www.indec.gob.ar/uploads/informesdeprensa/mercado_trabajo_eph_2trim26433FCBC5A8.pdf |
| `indec_eph_mercado_trabajo_2t2025.pdf` | Mercado de trabajo. Tasas e indicadores socioeconómicos (EPH). Segundo trimestre de 2025 (Informes técnicos, Trabajo e ingresos, Vol. 9, n° 8; publicado el 18 de septiembre de 2025) | INDEC | 2.º trimestre de 2024 a 2.º trimestre de 2025 | 23 | 1–23 (completo) | https://www.indec.gob.ar/uploads/informesdeprensa/mercado_trabajo_eph_2trim25C42A813B2A.pdf |
| `cepal_anuario_estadistico_2025_poblacion_trabajo.pdf` | Anuario Estadístico de América Latina y el Caribe 2025 / Statistical Yearbook for Latin America and the Caribbean 2025 (LC/PUB.2025/25-P/Rev.1), extracto | CEPAL (Comisión Económica para América Latina y el Caribe, Naciones Unidas) | Datos 2023–2025 según el cuadro | 175 | 15–24 del original (= 1–10 del extracto): capítulo "1. Estadísticas sociales", secciones 1.1 Población y 1.2 Trabajo | https://repositorio.cepal.org/server/api/core/bitstreams/b2476d6d-34cd-4e70-a7b7-d39e244a9070/content |

## Recorte del PDF de CEPAL

El original (175 páginas, ~24 MB) supera el máximo de 30 páginas de la spec (OQ-2). Se
conservaron las páginas 15–24 del PDF original (portadilla del capítulo 1, cuadros 1.1.1 a
1.2.6), que incluyen la tabla multipágina del set y los cuadros de población y trabajo
relacionados con los informes de la EPH. Equivalencia de páginas: página *n* del extracto =
página *n + 14* del original (los números impresos en el pie son *n + 12*).

Comando usado (PyMuPDF 1.28.2, desde la raíz del repo; `content.pdf` es el PDF original):

```powershell
.venv\Scripts\python -c "import pymupdf; s = pymupdf.open('content.pdf'); o = pymupdf.open(); o.insert_pdf(s, from_page=14, to_page=23); m = {k: v for k, v in s.metadata.items() if k not in ('format', 'encryption')}; m['subject'] = 'Extracto: páginas 15-24 de 175 del PDF original (1. Estadísticas sociales: 1.1 Población y 1.2 Trabajo).'; o.set_metadata(m); o.save('data/sample_pdfs/cepal_anuario_estadistico_2025_poblacion_trabajo.pdf', garbage=4, deflate=True)"
```

Verificación: el texto de cada página del extracto es idéntico al de la página
correspondiente del original (`page.get_text()`). SHA-256 del original:
`5723eb2a9edee2df64ec716a1690910ca07e10ccf120cabd9e5257d8238778c5`. El original no se versiona.

## Tabla multipágina

- `cepal_anuario_estadistico_2025_poblacion_trabajo.pdf`, cuadro 1.1.1 "Población:
  indicadores seleccionados (I)": páginas 3–4 del extracto (17–18 del original), con el
  encabezado repetido en la página 4. La extracción la une en una tabla lógica de 49 filas.

## Particularidades para la ingesta

- INDEC: números en formato es-AR (`48,1`, `29.909`); el encabezado de los cuadros está
  dibujado aparte, sobre la grilla de datos, en tres niveles ("Total 31 aglomerados urbanos"
  / "Año 2025" / "2° trimestre"); llamadas a nota pegadas a valores (`45,4 (1)`); el último
  trimestre lleva `*` (dato provisorio).
- CEPAL: números con punto decimal y espacio como separador de miles (`45 851.4`), tablas
  bilingües (columna "País" y columna "Country"), llamadas a nota pegadas a valores
  (`667 888.6b`) y a nombres de países (`Argentinab`), y `...`/`…` como dato faltante.
