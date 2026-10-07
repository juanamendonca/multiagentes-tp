"""VC-025 (parte almacén) y VC-028 (parte local): almacén Parquet + JSON en ``TABLES_DIR``."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.ingestion.schemas import ColumnMeta, TableRecord
from services.table_store import TableStore, ensure_parquet_safe, make_table_id
from tests.tables import DOC_HASH, OTHER_DOC_HASH, sample_df, sample_record

# --- make_table_id (BR-010) ----------------------------------------------------------------------


def test_make_table_id_follows_br010_format() -> None:
    """Given doc_hash, primera página 1-based e índice 0-based, then el id es
    `doc_hash[:16]-pNNN-tI`."""
    assert make_table_id(DOC_HASH, 3, 0) == "0123456789abcdef-p003-t0"
    assert make_table_id(DOC_HASH.upper(), 112, 4) == "0123456789abcdef-p112-t4"


@pytest.mark.parametrize(
    ("doc_hash", "page", "index"),
    [("xyz", 1, 0), ("0123456789abcde", 1, 0), (DOC_HASH, 0, 0), (DOC_HASH, 1, -1)],
)
def test_make_table_id_rejects_invalid_input(doc_hash: str, page: int, index: int) -> None:
    """Given un doc_hash que no es hex de ≥ 16 caracteres, una página 0 o un índice negativo,
    then ValueError."""
    with pytest.raises(ValueError):
        make_table_id(doc_hash, page, index)


def test_store_rejects_table_ids_that_could_escape_the_directory(tmp_path: Path) -> None:
    """Given un table_id con ruta, when se pide su archivo, then ValueError (no se sale de TABLES_DIR)."""
    store = TableStore(tmp_path)
    with pytest.raises(ValueError):
        store.parquet_path("../secret")
    with pytest.raises(ValueError):
        store.table_exists("0123456789abcdef-p003-t0/../../x")


# --- save / load (VC-025, parte almacén) ---------------------------------------------------------


def test_vc025_save_table_writes_parquet_and_json_and_roundtrips_dtypes(tmp_path: Path) -> None:
    """Given una tabla normalizada (str con faltante, float64 con NaN, Int64), when se guarda,
    then existen <id>.parquet y <id>.json, y read_parquet devuelve un DataFrame igual con dtypes."""
    store = TableStore(tmp_path / "tables")
    df = sample_df()
    record = sample_record(df=df)

    saved = store.save_table(record, df)

    parquet = tmp_path / "tables" / f"{record.table_id}.parquet"
    json_file = tmp_path / "tables" / f"{record.table_id}.json"
    assert parquet.is_file() and json_file.is_file()
    pd.testing.assert_frame_equal(pd.read_parquet(parquet), df, check_dtype=True)
    pd.testing.assert_frame_equal(store.load_table(record.table_id), df, check_dtype=True)
    assert saved.n_rows == 4 and saved.n_cols == 3
    assert saved.dtypes == {
        "Región": "str",
        "Tasa de desocupación - 2T2026": "float64",
        "Año": "Int64",
    }
    assert saved.data_path == parquet.as_posix()
    assert TableRecord.model_validate_json(json_file.read_text(encoding="utf-8")) == saved
    assert store.load_record(record.table_id) == saved
    assert not [p for p in (tmp_path / "tables").iterdir() if p.suffix == ".tmp"]


def test_save_table_keeps_missing_values_as_nan_never_zero(tmp_path: Path) -> None:
    """Given celdas faltantes, when se guarda y se recarga, then siguen siendo NaN (BR-004)."""
    store = TableStore(tmp_path)
    df = sample_df()
    store.save_table(sample_record(df=df), df)

    loaded = store.load_table(sample_record().table_id)

    assert loaded["Tasa de desocupación - 2T2026"].isna().tolist() == [False, False, True, False]
    assert loaded["Año"].isna().tolist() == [False, False, True, False]
    assert (loaded["Tasa de desocupación - 2T2026"].fillna(-1) != 0).all()


def test_mixed_object_column_is_converted_to_text_before_writing(tmp_path: Path) -> None:
    """Given una columna object con números y textos mezclados (pyarrow no la puede escribir),
    when se guarda, then se convierte a texto (str) conservando los faltantes y se escribe OK."""
    df = pd.DataFrame(
        {
            "Concepto": pd.Series(["a", "b", "c"], dtype="str"),
            "Mixta": pd.Series([1, "texto", None], dtype=object),
        }
    )
    with pytest.raises(Exception):  # noqa: B017 - lo que hay que evitar: pyarrow no la escribe
        df.to_parquet(tmp_path / "raw.parquet", engine="pyarrow", index=False)
    store = TableStore(tmp_path)
    record = sample_record(df=df)

    saved = store.save_table(record, df)

    loaded = store.load_table(record.table_id)
    assert str(loaded["Mixta"].dtype) == "str"
    assert loaded["Mixta"].iloc[:2].tolist() == ["1", "texto"]
    assert pd.isna(loaded["Mixta"].iloc[2])
    assert saved.dtypes["Mixta"] == "str"


def test_ensure_parquet_safe_asserts_names_and_types() -> None:
    """Given nombres de columna no str o repetidos, then ValueError; columnas numéricas y de
    texto pasan sin cambios."""
    with pytest.raises(ValueError, match="no str"):
        ensure_parquet_safe(pd.DataFrame({0: [1.0, 2.0], "b": [1.0, 2.0]}))
    duplicated = pd.DataFrame([[1.0, 2.0]], columns=["a", "a"])
    with pytest.raises(ValueError, match="repetidos"):
        ensure_parquet_safe(duplicated)
    df = sample_df()
    pd.testing.assert_frame_equal(ensure_parquet_safe(df), df)


def test_save_table_rejects_columns_that_do_not_match_the_record(tmp_path: Path) -> None:
    """Given un registro cuyas columnas no son las del DataFrame, when se guarda, then ValueError
    y no se escribe nada."""
    store = TableStore(tmp_path)
    record = sample_record(columns=[ColumnMeta(name="Otra", description="", dtype="text")])

    with pytest.raises(ValueError, match="no coinciden"):
        store.save_table(record, sample_df())
    assert list(tmp_path.iterdir()) == []


def test_save_table_replaces_previous_files(tmp_path: Path) -> None:
    """Given una tabla ya guardada, when se guarda otra vez con el mismo id, then se reemplaza."""
    store = TableStore(tmp_path)
    df = sample_df()
    store.save_table(sample_record(df=df), df)
    smaller = df.head(2)

    store.save_table(sample_record(df=smaller, name="Nuevo nombre"), smaller)

    record_id = sample_record().table_id
    assert len(store.load_table(record_id)) == 2
    assert store.load_record(record_id).name == "Nuevo nombre"
    assert len(list(tmp_path.glob("*.parquet"))) == 1


# --- catálogo, existencia y borrado --------------------------------------------------------------


def test_list_records_returns_all_json_sorted_and_skips_unreadable(tmp_path: Path) -> None:
    """Given tablas guardadas, un JSON corrupto y un archivo ajeno, when se listan, then vuelven
    los registros válidos ordenados por table_id."""
    store = TableStore(tmp_path)
    df = sample_df()
    for page in (5, 3):
        store.save_table(sample_record(first_page=page, df=df), df)
    (tmp_path / "0123456789abcdef-p009-t0.json").write_text("{no es json", encoding="utf-8")
    (tmp_path / "notas.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

    records = store.list_records()

    assert [r.table_id for r in records] == ["0123456789abcdef-p003-t0", "0123456789abcdef-p005-t0"]


def test_list_records_on_missing_directory_is_empty(tmp_path: Path) -> None:
    """Given un TABLES_DIR que todavía no existe, then el catálogo está vacío."""
    assert TableStore(tmp_path / "nada").list_records() == []


def test_table_exists_checks_the_parquet(tmp_path: Path) -> None:
    """Given una tabla guardada y otra sin Parquet, then table_exists es True solo para la primera."""
    store = TableStore(tmp_path)
    df = sample_df()
    saved = store.save_table(sample_record(df=df), df)
    other = sample_record(first_page=7)

    assert store.table_exists(saved.table_id)
    assert not store.table_exists(other.table_id)
    store.parquet_path(saved.table_id).unlink()
    assert not store.table_exists(saved.table_id)


def test_delete_document_removes_only_that_document(tmp_path: Path) -> None:
    """Given tablas de dos documentos, when se borra uno (por doc_hash completo o prefijo),
    then solo desaparecen sus archivos."""
    store = TableStore(tmp_path)
    df = sample_df()
    for doc, page in ((DOC_HASH, 3), (DOC_HASH, 4), (OTHER_DOC_HASH, 3)):
        store.save_table(sample_record(doc_hash=doc, first_page=page, df=df), df)

    deleted = store.delete_document(DOC_HASH[:16])

    assert deleted == ["0123456789abcdef-p003-t0", "0123456789abcdef-p004-t0"]
    assert [r.doc_hash for r in store.list_records()] == [OTHER_DOC_HASH]
    assert store.delete_document(DOC_HASH) == []


def test_vc028_delete_stale_removes_local_tables_not_in_new_ingestion(tmp_path: Path) -> None:
    """Given una ingesta previa con ids {a,b,c} y una nueva con {a,b}, when se borran los
    obsoletos, then c desaparece de TABLES_DIR y a, b y las tablas de otro documento quedan."""
    store = TableStore(tmp_path)
    df = sample_df()
    ids = [store.save_table(sample_record(first_page=p, df=df), df).table_id for p in (1, 2, 3)]
    other = store.save_table(sample_record(doc_hash=OTHER_DOC_HASH, first_page=3, df=df), df)

    deleted = store.delete_stale(DOC_HASH, keep_ids=ids[:2])

    assert deleted == [ids[2]]
    assert not store.parquet_path(ids[2]).exists() and not store.json_path(ids[2]).exists()
    assert store.list_table_ids() == sorted([*ids[:2], other.table_id])


def test_int64_column_with_all_missing_and_float_nan_roundtrip(tmp_path: Path) -> None:
    """Given columnas totalmente vacías (str) y numéricas con todos NaN, then el round-trip
    conserva dtypes."""
    df = pd.DataFrame(
        {
            "A": pd.Series([None, None], dtype="str"),
            "B": pd.Series([np.nan, np.nan], dtype="float64"),
            "C": pd.Series([None, None], dtype="Int64"),
        }
    )
    store = TableStore(tmp_path)
    saved = store.save_table(sample_record(df=df), df)

    pd.testing.assert_frame_equal(store.load_table(saved.table_id), df, check_dtype=True)
