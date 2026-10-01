"""Excel que trae la fila completa dentro de una sola celda."""

from openpyxl import Workbook

from nrp_osint.db import DB
from nrp_osint.historical import import_history
from nrp_osint.io_stream import ingest
from nrp_osint.tabular import csv_incrustado


def _una_columna(path, encabezado, filas):
    wb = Workbook()
    ws = wb.active
    ws.append([encabezado])
    for f in filas:
        ws.append([f])
    wb.save(path)


def test_detecta_csv_en_una_columna():
    assert csv_incrustado(["CUENTA,NSS,NOMBRE"])[0] == ["CUENTA", "NSS", "NOMBRE"]
    assert csv_incrustado(["CUENTA;NRP;EMPRESA"])[0] == ["CUENTA", "NRP", "EMPRESA"]
    # una sola columna de verdad, sin separadores, no se toca
    assert csv_incrustado(["CUENTA"]) is None
    # varias columnas normales tampoco
    assert csv_incrustado(["CUENTA", "NOMBRE"]) is None


def test_base_objetivo_en_una_columna(tmp_path):
    ruta = tmp_path / "base.xlsx"
    _una_columna(
        ruta,
        "CUENTA,NSS,ENTIDAD,NRP,NOMBRE_EMPRESA,NOMBRE,A_PATERNO,A_MATERNO",
        [
            "A1,LOCAL,JALISCO,N100,ACME SA DE CV,ANA,LOPEZ,DIAZ",
            "A2,LOCAL,PUEBLA,N100,ACME SA DE CV,LUIS,PEREZ,SOTO",
        ],
    )
    db = DB(str(tmp_path / "x.sqlite"))
    info = ingest(db, ruta)
    assert info["rows"] == 2
    fila = db.conn.execute(
        "SELECT account_id,nrp,company_input,full_name FROM people ORDER BY row_id"
    ).fetchone()
    assert fila["account_id"] == "A1"
    assert fila["nrp"] == "N100"
    assert fila["company_input"] == "ACME SA DE CV"
    assert fila["full_name"] == "ANA LOPEZ DIAZ"


def test_historico_en_una_columna(tmp_path):
    ruta = tmp_path / "hist.xlsx"
    _una_columna(
        ruta,
        "IDCUENTA,NRP,NOMBRE_ORIGINAL,DOMINIO,DOMINIO_VALIDO,NOMBRE_COMPLETO,CORREO",
        ["A1,N100,ACME SA DE CV,acme.com,mx,ANA LOPEZ DIAZ,ana.lopez@acme.com"],
    )
    db = DB(str(tmp_path / "y.sqlite"))
    info = import_history(db, ruta)
    assert info["rows_imported"] == 1
    fila = db.conn.execute(
        "SELECT account_id,nrp,domain,email,domain_status FROM historical_contacts"
    ).fetchone()
    assert fila["account_id"] == "A1"
    assert fila["domain"] == "acme.com"
    assert fila["email"] == "ana.lopez@acme.com"
    assert fila["domain_status"] == "mx"
    # El MX que ya venia validado se propaga al perfil de la empresa.
    perfil = db.conn.execute("SELECT mx_valid FROM company_profiles").fetchone()
    assert perfil["mx_valid"] == 1
