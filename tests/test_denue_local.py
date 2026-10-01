import asyncio, csv, io, zipfile

from nrp_osint import denue_local
from nrp_osint.config import Settings
from nrp_osint.db import DB
from nrp_osint.emailintel import pattern_from_local_part, weak_pattern_confidence
from nrp_osint.io_stream import ingest
from nrp_osint.pipeline import Pipeline

COLS = ["id","clee","nom_estab","raz_social","codigo_act","nombre_act","per_ocu","telefono","correoelec","www",
        "cve_ent","entidad","municipio","fecha_alta"]

ROWS = [
    # Dos establecimientos de la misma empresa: solo uno registra sitio web.
    ["1","C1","ACME SUCURSAL CENTRO","ACME INDUSTRIAL SA DE CV","3311","Fabricación","51 a 100 personas","555","","","09","Ciudad de México","Cuauhtémoc","2024-01"],
    ["2","C2","ACME PLANTA","ACME INDUSTRIAL SA DE CV","3311","Fabricación","251 y más personas","556","juan.perez@acme.com.mx","WWW.ACME.COM.MX","15","México","Toluca","2024-01"],
    ["3","C3","ACME PLANTA 2","ACME INDUSTRIAL SA DE CV","3311","Fabricación","51 a 100 personas","557","ventas@acme.com.mx","","15","México","Toluca","2024-01"],
    # Empresa sin sitio web, con correo gratuito.
    ["4","C4","TACOS EL GÜERO","TAQUERIA EL GUERO SA DE CV","7225","Restaurantes","0 a 5 personas","558","guero@gmail.com","","09","Ciudad de México","Iztapalapa","2023-05"],
    # Sitio en Facebook: no es un dominio de empresa.
    ["5","C5","PAPELERIA LUNA","PAPELERIA LUNA SA DE CV","4653","Papelerías","0 a 5 personas","559","","https://facebook.com/papelerialuna","09","Ciudad de México","Coyoacán","2023-05"],
]


def make_denue(tmp_path):
    raw = tmp_path / "denue" / "raw"
    raw.mkdir(parents=True)
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(COLS)
    w.writerows(ROWS)
    with zipfile.ZipFile(raw / "denue_09_csv.zip", "w") as z:
        # Mismo formato que INEGI: latin-1 dentro de conjunto_de_datos/
        z.writestr("conjunto_de_datos/denue_inegi_09_.csv", buf.getvalue().encode("latin-1"))
        z.writestr("diccionario_de_datos/denue_diccionario_de_datos.csv", "x")
    return tmp_path / "denue"


def test_pattern_from_local_part():
    assert pattern_from_local_part("tiare.otero") == "first.paternal"
    assert pattern_from_local_part("m.hernandez") == "f.paternal"
    assert pattern_from_local_part("juan_perez") == "first_paternal"
    assert pattern_from_local_part("ventas") == ""
    assert pattern_from_local_part("ventas.mx") == ""
    assert pattern_from_local_part("mhernandez") == ""  # ambiguo: no se adivina
    assert 0 < weak_pattern_confidence({"first.paternal": 1}) < 0.72


def test_build_and_search(tmp_path):
    base = make_denue(tmp_path)
    meta = denue_local.build(base)
    assert meta["establecimientos"] == "5" or meta["establecimientos"] == 5
    src = denue_local.DenueLocalSource(denue_local.db_path(base))
    hits = src.search("Acme Industrial, S.A. de C.V.")
    assert hits[0]["score"] == 1.0
    assert hits[0]["domain"] == "acme.com.mx"  # a igual nombre gana el que tiene web
    assert "Toluca" in hits[0]["location"]
    # Acentos en latin-1 leidos bien
    assert src.search("TAQUERIA EL GUERO")[0]["trade_name"] == "TACOS EL GÜERO"
    assert src.search("TAQUERIA EL GUERO")[0]["domain"] == ""
    assert src.search("PAPELERIA LUNA")[0]["domain"] == ""  # facebook descartado
    votes, n = src.email_pattern_votes("ACME INDUSTRIAL", "acme.com.mx")
    assert votes == {"first.paternal": 1} and n == 1  # 'ventas' no cuenta
    # Busqueda por terminos cuando no hay igualdad exacta
    assert src.search("ACME INDUSTRIAL MEXICO")[0]["company"] == "ACME INDUSTRIAL SA DE CV"


def test_pipeline_denue_local_only(tmp_path):
    base = make_denue(tmp_path)
    denue_local.build(base)
    main = tmp_path / "main.csv"
    main.write_text(
        "cuenta,nombre,a_paterno,a_materno,nombre_empresa,nrp\n"
        "A1,MARIA GUADALUPE,HERNANDEZ,GARCIA,ACME INDUSTRIAL SA DE CV,Y111\n"
        "A2,PEDRO,LOPEZ,RUIZ,TAQUERIA EL GUERO,Y222\n"
        "A3,ANA,DIAZ,SOTO,EMPRESA QUE NO EXISTE,Y333\n", encoding="utf-8")
    db = DB(str(tmp_path / "x.sqlite"))
    ingest(db, main)
    s = Settings(db_path=str(tmp_path / "x.sqlite"), denue_local_path=str(denue_local.db_path(base)),
                 denue_local_only=True, enable_ddgs=False)
    n = asyncio.run(Pipeline(db, s, {"denue": "", "searxng": ""}).run(max_jobs=0, batch=10))
    assert n == 3
    st = {r["nrp"]: dict(r) for r in db.conn.execute("SELECT * FROM employers")}
    assert st["Y111"]["status"] == "RESUELTO" and st["Y111"]["domain"] == "acme.com.mx"
    assert st["Y222"]["status"] == "SIN_DOMINIO_DENUE"
    assert st["Y333"]["status"] == "SIN_COINCIDENCIA_DENUE"
    r = db.conn.execute("SELECT email,email_type FROM results WHERE row_id=1").fetchone()
    assert r["email"] == "maria.hernandez@acme.com.mx"
    assert r["email_type"] == "DEDUCIDO_PATRON_DENUE"
    # Sin salir a la red: ninguna fuente externa registrada
    assert db.conn.execute("SELECT COUNT(*) FROM source_health").fetchone()[0] == 0
