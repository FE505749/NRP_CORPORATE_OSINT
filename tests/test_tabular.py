from nrp_osint.historical import iter_rows
from nrp_osint.io_stream import ingest
from nrp_osint.db import DB


def test_windows_excel_csv_encoding_and_delimiter(tmp_path):
    historical = tmp_path / "historico.csv"
    historical.write_bytes(
        "cuenta;nrp;nombre_empresa;nombre;correo\nC1;N1;Compañía México;José;jose@demo.mx\n".encode("cp1252")
    )
    rows = list(iter_rows(historical))
    assert rows[0]["company_name"] == "Compañía México"
    assert rows[0]["first_names"] == "José"
    assert rows[0]["source_file"] == "historico.csv"


def test_main_csv_uses_portable_source_name(tmp_path):
    source = tmp_path / "base.csv"
    source.write_text("cuenta;nombre;nombre_empresa\nC1;Ana;Empresa Demo\n", encoding="utf-8")
    db = DB(str(tmp_path / "db.sqlite"))
    ingest(db, source)
    row = db.conn.execute("SELECT ingest_source FROM people").fetchone()
    assert row["ingest_source"] == "base.csv"
