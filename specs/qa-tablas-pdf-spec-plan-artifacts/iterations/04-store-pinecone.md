# Iteración 4 — Almacén de tablas y Pinecone

- **Fecha:** 2026-10-07 · **Rama:** `feat/qa-tablas`
- **SHA:** ver `git log -1 --format=%H -- specs/qa-tablas-pdf-spec-plan-artifacts/iterations/04-store-pinecone.md` (D-003)
- **Commit:** `feat(ingesta): almacén Parquet/JSON e indexación idempotente en Pinecone`

## Criterios de éxito

Corrida completa con todo en stage, justo antes del commit (Git Bash desde la raíz; en PowerShell, mismos comandos con `\`):

| # | Criterio | Comando | Resultado |
|---|---|---|---|
| 1 | VC-004, VC-025 (store + embedding text, con fakes), VC-026, VC-028 | `.venv\Scripts\python -m pytest tests/unit/test_table_store.py tests/unit/test_vectorstore.py -v` | PASS (36 passed, ~3 s). Por VC: `test_vc004_existing_index_with_other_dimension_raises_and_writes_nothing`; VC-025: `test_vc025_save_table_writes_parquet_and_json_and_roundtrips_dtypes` (`assert_frame_equal` con dtypes), `test_vc025_embedding_text_has_name_description_columns_rows_source_and_section`, `test_vc025_upsert_uses_table_id_as_vector_id_with_spec_metadata`; `test_vc026_large_table_metadata_fits_in_35kb` (5 000 filas, 300 rótulos; trunca preview y rótulos); VC-028: `test_vc028_delete_stale_removes_ids_of_the_document_not_in_the_new_ingestion` (Pinecone fake) y `test_vc028_delete_stale_removes_local_tables_not_in_new_ingestion` (`TABLES_DIR`). Extra: columna mixta → `str` (gotcha de Parquet), FR-003 lógico, lotes de 64 (NFR-007), falla parcial "1 de 3 tablas indexadas" (FR-029), conteo con espera, adaptador del SDK |
| 2 | VC-003 y VC-027 (parte vectorstore) | `.venv\Scripts\python -m pytest -m integration tests/integration/test_pinecone.py -v` | PASS (5 passed, ~80 s). `test_vc003_missing_index_is_created_with_embedding_dim_and_cosine` (índice efímero creado con dim 1536 y `cosine`, `ready`); `test_vc027_reupserting_renamed_pdf_keeps_ids_and_vector_count` (12 tablas reales de EPH 2T2025 con embeddings reales: N=12 → reupsert como `eph_renombrado.pdf` → mismos ids, 12 vectores; query devuelve la tabla; `delete_stale` → 11); `test_nfr005_real_tables_metadata_fits_without_truncation` ×3 PDFs. Después: `list_indexes()` = `[]` (índice de test borrado) |
| 3 | Suite sin red | `.venv\Scripts\python -m pytest -m "not integration"` | PASS (147 passed, 11 deselected, ~13 s) |
| 4 | Lint | `.venv\Scripts\ruff check .` | PASS ("All checks passed!") |
| 5 | Secretos | `git grep -nE "(sk-[A-Za-z0-9]{16,}\|pcsk[_]\|lsv2[_])"` y `git grep --cached -nE ...`; `git ls-files .env` | PASS (ambos vacíos, exit 1; `.env` no trackeado) |
| 6 | Artefactos | revisión | PASS: este record, `CONTEXT.md` reescrito, D-017..D-020 |

Ningún criterio dio FAIL en ninguna corrida. Regresión adicional: `pytest tests/integration/test_sample_pdfs.py` PASS (6 passed).

## Archivos

Agregados: `agents/__init__.py`, `agents/ingestion/__init__.py`, `agents/ingestion/schemas.py`, `services/table_store.py`, `services/vectorstore.py`, `tests/tables.py`, `tests/unit/test_table_store.py`, `tests/unit/test_vectorstore.py`, `tests/integration/test_pinecone.py`, este record.
Cambiados: `tests/fakes.py` (+ `FakeEmbeddings`, `FakeVectorStore`, `FakePineconeClient`, `FakePineconeIndex`), `CONTEXT.md`, `DECISIONS.md` (D-017..D-020). Incluidos del orquestador (pendientes sin commitear): spec v0.6 (BR-001 con la excepción de epígrafe propio, VC-016 ampliado, changelog) y D-015 marcada como ratificada.
Sin dependencias nuevas: `pinecone==10.0.0` ya estaba fijado en `requirements.txt` desde la Iteración 1 (SDK 10, serverless, `index.list(prefix=...)`).

## Desvíos del plan

1. **`TableStore` como clase** (D-017): las funciones del plan son métodos de `TableStore(tables_dir)`; `save_table` devuelve el registro completado (`n_rows`, `n_cols`, `dtypes`, `data_path`).
2. **Primitivas del `Protocol VectorStore`** = `upsert`/`list_ids`/`delete`/`query`/`count`; `upsert_records`, `delete_stale`, `query` y `count_vectors` son funciones del módulo que reciben el store (así el fake solo implementa primitivas). `ensure_index` recibe `client` e `index_name` opcionales (fakes en VC-004 y el índice de test).
3. **Metadata `pages` como lista de textos y `section` `""`** (D-018): §7.4 dice `list[int]`, Pinecone no admite listas de números ni `null`. Ajuste editorial de la spec pendiente de aprobación del usuario.
4. **Funciones extra**: `embed_texts`, `build_vector_records`, `decode_metadata`, `metadata_size`, `make_pinecone_client`, `doc_prefix`, `ensure_parquet_safe`; `tests/tables.py` con datos de ejemplo.
5. **VC-027 completo** (reingesta de punta a punta) queda para la Iteración 5, como dice el plan; acá se probó la parte vectorstore con tablas y embeddings reales.

## Problemas encontrados / gotchas

- SDK `pinecone` 10: `IndexModel.dimension`/`.metric` son propiedades deprecadas (se leen con el warning silenciado); `create_index(timeout=...)` bloquea hasta `ready`; `index.list(prefix=)` devuelve páginas con `.vectors[*].id`; `describe_index_stats().total_vector_count`.
- Conteos eventualmente consistentes: tras el upsert, `count_vectors(expected=N, timeout_s=30)` los vio en pocos segundos; la query inmediatamente después del conteo ya devolvía la tabla.
- `pyarrow` no escribe columnas `object` con números y textos mezclados (`ArrowInvalid`): `ensure_parquet_safe` las pasa a `str`.
- pandas 3: `astype("str")` conserva `NaN`; `Int64` con todos nulos y `str` toda `NaN` hacen round-trip exacto.
- Tamaños reales (con descripciones de columna vacías): metadata máx. 5,7 KB (CEPAL p006, INDEC 2T2026 p012), `columns_json` máx. 1,2 KB (CEPAL), texto de embedding máx. ~1,9 K caracteres. Con descripciones del LLM crecerá ~1–2 KB: lejos de 35 KB.

## For the next iteration

- Nodo `index` (Iteración 5): `store = TableStore(settings.tables_dir)`; por tabla `saved = store.save_table(record, df)`; `texts = [build_embedding_text(r) for r in saved_records]`; `vectors = embed_texts(get_embeddings(settings), texts, expected_dim=settings.embedding_dim)`; `upsert_records(vs, list(zip(saved_records, dfs)), vectors, texts)`; después `delete_stale(vs, doc_hash, ids)` y `store.delete_stale(doc_hash, ids)` (D-020). Capturar `IndexingError` → mensaje FR-029 (`str(e)` ya dice "k de n tablas indexadas…").
- `vs = ensure_index(settings)` crea el índice de producción la primera vez (FR-003; tarda ~10–30 s) y lanza `IndexDimensionMismatch` (FR-004). En `deps` inyectar `vector_store` (fake: `FakeVectorStore`) y `embeddings` (fake: `FakeEmbeddings(dimension=settings.embedding_dim)`).
- `TableRecord.table_id = make_table_id(loaded.doc_hash, t.pages[0], t.index_on_page)`; `source = TableSource(file_name=loaded.file_name, pages=t.pages)`; `section = context.section`. `record.columns` debe nombrar exactamente `t.df.columns` (lo garantizan BR-007/BR-008) o `save_table` lanza `ValueError`.
- FR-026 (sin tablas): no llamar a `ensure_index`/upsert. VC-033 con `FakeVectorStore(fail_on_upsert_call=2)`.
- Iteración 7 (Analista): leer metadata con `decode_metadata(match.metadata)`; FR-042 con `TableStore.table_exists(id)`.
- Si se mata un test de integración a mitad, revisar que no quede un índice `*-itest-*` en la consola de Pinecone.
