from nrp_osint.crossmatch import build_local_crossmatch, crossmatch_counts
from nrp_osint.db import DB
from nrp_osint.historical import import_history
from nrp_osint.io_stream import ingest


def _base(tmp_path, historico, objetivo):
    h = tmp_path / "hist.csv"
    h.write_text(historico, encoding="utf-8")
    b = tmp_path / "base.csv"
    b.write_text(objetivo, encoding="utf-8")
    db = DB(str(tmp_path / "x.sqlite"))
    import_history(db, h)
    ingest(db, b)
    return db


def test_crossmatch_prioritizes_account_and_is_read_only(tmp_path):
    db = _base(
        tmp_path,
        "cuenta,nrp,nombre_empresa,nombre,a_paterno,correo,dominio\n"
        "A1,N2,Historica,ANA,LOPEZ,a@example.com,example.com\n",
        "cuenta,nombre,a_paterno,nrp,nombre_empresa\nA1,ANA,LOPEZ,N1,Empresa Uno\n",
    )
    antes = db.conn.execute("SELECT COUNT(*) FROM historical_contacts").fetchone()[0]

    filas = build_local_crossmatch(db)
    assert filas[0]["Coincidencia"] == "Sí"
    # La cuenta gana sobre el NRP aunque el NRP de la base sea otro.
    assert filas[0]["Tipo_coincidencia"] == "ID_CUENTA_EXACTO"
    assert filas[0]["Correo_histórico"] == "a@example.com"
    assert filas[0]["NRP_histórico"] == "N2"

    # Solo lectura: ni las personas ni los históricos cambian.
    assert db.conn.execute("SELECT COUNT(*) FROM historical_contacts").fetchone()[0] == antes
    assert db.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0


def test_crossmatch_reports_no_match(tmp_path):
    db = _base(
        tmp_path,
        "cuenta,nrp,nombre_empresa,nombre,a_paterno,correo,dominio\n"
        "OTRA,N9,Otra,LUZ,DIAZ,l@otra.com,otra.com\n",
        "cuenta,nombre,a_paterno,nombre_empresa\nA1,ANA,LOPEZ,Sin Match\n",
    )
    filas = build_local_crossmatch(db)
    assert filas[0]["Coincidencia"] == "No"
    assert filas[0]["Tipo_coincidencia"] == "SIN_COINCIDENCIA"
    assert filas[0]["Correo_histórico"] == ""


def test_crossmatch_matches_by_company_when_account_and_nrp_differ(tmp_path):
    db = _base(
        tmp_path,
        "cuenta,nrp,nombre_empresa,nombre,a_paterno,correo,dominio\n"
        "ZZ,N9,ACME SA DE CV,LUZ,DIAZ,luz.diaz@acme.com,acme.com\n",
        "cuenta,nombre,a_paterno,nombre_empresa\nA1,ANA,LOPEZ,  acme  sa de cv \n",
    )
    filas = build_local_crossmatch(db)
    assert filas[0]["Tipo_coincidencia"] == "EMPRESA_EXACTA"
    assert filas[0]["Dominio_histórico"] == "acme.com"


def test_normalize_company_no_colapsa_sufijos_con_puntos(tmp_path):
    """Limitación conocida: 'S.A. de C.V.' no se reduce igual que 'SA DE CV'."""
    from nrp_osint.normalize import normalize_company

    assert normalize_company("ACME SA DE CV") == "ACME"
    assert normalize_company("Acme S.A. de C.V.") == "ACME S A DE C V"


def test_crossmatch_counts_matches_rows(tmp_path):
    db = _base(
        tmp_path,
        "cuenta,nrp,nombre_empresa,nombre,a_paterno,correo,dominio\n"
        "A1,N1,ACME,ANA,LOPEZ,a@acme.com,acme.com\n",
        "cuenta,nombre,a_paterno,nombre_empresa\n"
        "A1,ANA,LOPEZ,ACME\n"
        "A2,LUZ,DIAZ,DESCONOCIDA\n",
    )
    resumen = crossmatch_counts(db)
    filas = build_local_crossmatch(db)
    assert resumen["total"] == len(filas) == 2
    assert resumen["con_coincidencia"] == 1
    assert resumen["sin_coincidencia"] == 1
