import time

from fastapi.testclient import TestClient

from api.main import crear_app
from nrp_osint import denue_local
from nrp_osint.config import Settings
from tests.test_denue_local import SUCURSALES, make_denue


def cliente(tmp_path, con_denue=True):
    denue = ""
    if con_denue:
        base = make_denue(tmp_path, SUCURSALES)
        denue_local.build(base)
        denue = str(denue_local.db_path(base))
    s = Settings(db_path=str(tmp_path / "x.sqlite"), denue_local_path=denue, tukan_local_path="",
                 upload_dir=str(tmp_path / "subidas"), output_dir=str(tmp_path / "salidas"),
                 tokens_path=str(tmp_path / "tokens.env"), enable_ddgs=False)
    return TestClient(crear_app(s))


def esperar(c, tid):
    for _ in range(200):
        t = c.get(f"/api/trabajos/{tid}").json()
        if t["estado"] != "corriendo":
            return t
        time.sleep(0.05)
    raise AssertionError("el trabajo no termino")


def test_pagina_y_estaticos(tmp_path):
    c = cliente(tmp_path, con_denue=False)
    r = c.get("/")
    assert r.status_code == 200 and "NRP Intelligence" in r.text and "Corporate OSINT" not in r.text
    assert c.get("/front/styles/tokens.css").status_code == 200
    assert c.get("/front/img/marca.png").status_code == 200
    assert c.get("/api/salud").json()["ok"] is True


def test_buscar_persona(tmp_path):
    c = cliente(tmp_path)
    r = c.post("/api/persona", json={"primer_nombre": "Francisco", "segundo_nombre": "Javier",
                                     "apellido_paterno": "Espinoza", "apellido_materno": "Mendieta",
                                     "empresa": "Profu Afore"})
    assert r.status_code == 200
    d = r.json()
    assert d["correo"] == "francisco.espinoza@profu.com.mx"
    assert d["tipo_texto"].startswith("Deducido") and d["ruta_texto"]
    assert d["desglose_texto"][0] == {"formato": "nombre.apellido", "n": 3}
    # Faltan datos: error legible, no un 500.
    r = c.post("/api/persona", json={"primer_nombre": "Ana"})
    assert r.status_code == 400 and "obligatorios" in r.json()["detail"]


def test_subir_base_y_resultados(tmp_path):
    c = cliente(tmp_path)
    csv = "cuenta,nombre,a_paterno,a_materno,nombre_empresa,nrp\nC1,ANA,DIAZ,SOTO,PROFU AFORE,\n"
    r = c.post("/api/base", files={"archivo": ("base.csv", csv.encode(), "text/csv")})
    t = esperar(c, r.json()["trabajo"])
    assert t["estado"] == "listo", t["error"]
    assert t["resultado"]["registros"] == 1 and t["resultado"]["con_correo"] == 1
    assert c.get("/api/estado").json()["conteos"]["personas"] == 1
    res = c.get("/api/resultados", params={"q": "C1"}).json()
    fila = dict(zip(res["columnas"], res["filas"][0]))
    assert fila["correo_principal"] == "ana.diaz@profu.com.mx"
    exp = c.post("/api/exportar").json()
    assert c.get(f"/api/descargas/{exp['archivo']}").status_code == 200
    # Solo se descargan archivos de la carpeta de salidas.
    assert c.get("/api/descargas/..%2Fx.sqlite").status_code == 404


def test_rechaza_tipo_de_archivo(tmp_path):
    c = cliente(tmp_path, con_denue=False)
    r = c.post("/api/base", files={"archivo": ("virus.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
