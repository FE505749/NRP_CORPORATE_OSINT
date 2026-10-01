import asyncio

from openpyxl import Workbook

from nrp_osint import tukan_local
from nrp_osint.config import Settings
from nrp_osint.db import DB
from nrp_osint.io_stream import ingest
from nrp_osint.pipeline import Pipeline

ROWS = [
    ("NRP", "EMPRESA", "ESTATUS", "EMAIL"),
    # El correo REVOCADO es de otro dominio y es el mas repetido: igual gana el ACTIVO.
    ("N0000000001", "ACME INDUSTRIAL SA DE CV", "REVOCADO", "ana.diaz@acmeviejo.com"),
    ("N0000000001", "ACME INDUSTRIAL SA DE CV", "REVOCADO", "luis.soto@acmeviejo.com"),
    ("N0000000001", "ACME INDUSTRIAL SA DE CV", "ACTIVO", "juan.perez@acme.com.mx"),
    ("N0000000001", "ACME INDUSTRIAL SA DE CV", "CADUCO", "maria.lopez@acme.com.mx"),
    # Solo correo gratuito: es contacto, no dominio.
    ("N0000000002", "TALLER EL GUERO", "ACTIVO", "taller.guero@hotmai.com"),
    # Un despacho contable lleva cuatro empresas distintas: no es dominio de ninguna.
    *[(f"N00000001{i}0", f"EMPRESA {x} SA", "ACTIVO", f"nomina{i}@despachofiscal.com.mx")
      for i, x in enumerate(["ALFA", "BRAVO", "CHARLIE", "DELTA"])],
    ("BAD", "SIN CORREO", "ACTIVO", "no-es-correo"),
]


def make_tukan(tmp_path):
    wb = Workbook()
    for r in ROWS:
        wb.active.append(r)
    src = tmp_path / "Tukan.xlsx"
    wb.save(src)
    meta = tukan_local.build(src, tmp_path / "tukan")
    return tukan_local.db_path(tmp_path / "tukan"), meta


def test_build_and_lookup(tmp_path):
    path, meta = make_tukan(tmp_path)
    assert meta["contactos"] == 9 and meta["activos"] == 6  # la fila sin @ se descarta
    src = tukan_local.TukanLocalSource(path)
    h = src.by_nrp("N0000000001")
    assert h["domain"] == "acme.com.mx" and h["domain_confidence"] == .86
    assert h["email"] == "juan.perez@acme.com.mx" and h["status"] == "ACTIVO"
    assert [s for _, s in h["emails"]][0] == "ACTIVO"
    g = src.by_nrp("N0000000002")
    assert g["domain"] == "" and g["email"] == "taller.guero@hotmai.com"
    d = src.by_company("EMPRESA ALFA")
    assert d["domain"] == "" and d["shared_domain"] == "despachofiscal.com.mx"
    assert src.by_company("ACME INDUSTRIAL")["nrp"] == "N0000000001"
    votes, conf = src.pattern("acme.com.mx")
    assert votes == {"first.paternal": 2} and 0 < conf < .72


def test_pipeline_tukan_only(tmp_path):
    path, _ = make_tukan(tmp_path)
    main = tmp_path / "main.csv"
    main.write_text(
        "cuenta,nombre,a_paterno,a_materno,nombre_empresa,nrp\n"
        "A1,MARIA,LOPEZ,RUIZ,,N0000000001\n"        # empresa por NRP; su correo ya esta en Tukan
        "A2,PEDRO,RAMIREZ,SOTO,,N0000000001\n"
        "A3,ANA,DIAZ,SOTO,TALLER EL GUERO,N0000000002\n"
        "A4,LUIS,CRUZ,PEREZ,EMPRESA ALFA SA,X999\n", encoding="utf-8")
    db = DB(str(tmp_path / "x.sqlite"))
    ingest(db, main)
    s = Settings(db_path=str(tmp_path / "x.sqlite"), denue_local_path="", tukan_local_path=str(path),
                 denue_local_only=True, enable_ddgs=False)
    assert asyncio.run(Pipeline(db, s, {"denue": "", "searxng": ""}).run(max_jobs=0, batch=10)) == 3
    st = {r["nrp"]: dict(r) for r in db.conn.execute("SELECT * FROM employers")}
    acme = st["N0000000001"]
    assert acme["status"] == "RESUELTO" and acme["domain"] == "acme.com.mx"
    assert acme["company_canonical"] == "ACME INDUSTRIAL SA DE CV"
    assert acme["tukan_email"] == "juan.perez@acme.com.mx" and acme["tukan_status"] == "ACTIVO"
    assert "TUKAN_PATRON" in acme["match_route"]
    assert st["N0000000002"]["status"] == "SIN_DOMINIO_TUKAN"
    assert st["N0000000002"]["tukan_email"] == "taller.guero@hotmai.com"
    assert st["X999"]["status"] == "SIN_DOMINIO_TUKAN" and st["X999"]["domain"] == ""
    res = {r["row_id"]: dict(r) for r in db.conn.execute("SELECT * FROM results")}
    assert (res[1]["email"], res[1]["email_type"]) == ("maria.lopez@acme.com.mx", "CONFIRMADO_TUKAN")
    assert (res[2]["email"], res[2]["email_type"]) == ("pedro.ramirez@acme.com.mx", "DEDUCIDO_PATRON_TUKAN")
    assert db.conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0] == 0
