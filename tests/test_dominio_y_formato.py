from nrp_osint import denue_local
from nrp_osint.config import Settings
from nrp_osint.domain import mail_domain
from nrp_osint.emailintel import generate, nombres_juntos, pattern_from_local_part
from nrp_osint.persona import buscar_persona
from tests.test_denue_local import make_denue

PERSONA = {"first_names": "FRANCISCO JAVIER", "paternal": "ESPINOZA", "maternal": "MENDIETA"}


def test_dominio_es_lo_que_va_despues_de_la_arroba():
    assert mail_domain("luis.felipe@bancomer.com.mx") == "bancomer.com.mx"
    assert mail_domain("ana@Correo.Empresa.MX") == "correo.empresa.mx"  # no se recorta a empresa.mx
    assert mail_domain("WWW.PROFUTURO.MX") == "profuturo.mx"
    assert mail_domain("x@gmail") == ""
    # El correo generado usa ese dominio tal cual.
    assert generate(PERSONA, "first.paternal", "correo.empresa.mx") == "francisco.espinoza@correo.empresa.mx"


def test_nombres_juntos():
    assert pattern_from_local_part("victorhugo.olivares") == "given.paternal"
    assert pattern_from_local_part("juan.perez") == "first.paternal"
    # Varios con dos nombres juntos: la regla es todos los nombres juntos.
    assert nombres_juntos({"first.paternal": 15, "given.paternal": 7}) == {"given.paternal": 22}
    # Uno suelto es una excepcion: sigue siendo nombre.apellido.
    assert nombres_juntos({"first.paternal": 9, "given.paternal": 1}) == {"first.paternal": 10}
    assert generate(PERSONA, "given.paternal", "banco.mx") == "franciscojavier.espinoza@banco.mx"


# Como BBVA: la razon social identica no tiene correos; los del grupo estan en
# otras razones sociales y escriben los dos nombres juntos.
GRUPO = [
    ["21", "G1", "BANCO X", "BANCOX", "5221", "Banca", "51 a 100 personas", "1", "", "WWW.BANCOX.COM", "09", "Ciudad de México", "Cuauhtémoc", "2024-01"],
    ["22", "G2", "BANCOX SEGUROS", "BANCOX SEGUROS MEXICO SA DE CV", "5241", "Seguros", "51 a 100 personas", "2", "victorhugo.olivares@bancox.com.mx", "", "09", "Ciudad de México", "Cuauhtémoc", "2024-01"],
    ["23", "G3", "BANCOX CASA DE BOLSA", "CASA DE BOLSA BANCOX SA DE CV", "5231", "Bolsa", "51 a 100 personas", "3", "anapatricia.flores@bancox.com.mx", "", "09", "Ciudad de México", "Cuauhtémoc", "2024-01"],
    ["24", "G4", "BANCOX PENSIONES", "BANCOX PENSIONES SA DE CV", "5241", "Pensiones", "51 a 100 personas", "4", "miguelangel.pineda@bancox.com.mx", "", "09", "Ciudad de México", "Cuauhtémoc", "2024-01"],
    ["25", "G5", "BANCOX LEASING", "BANCOX LEASING SA DE CV", "5322", "Arrendamiento", "11 a 30 personas", "5", "juan.perez@bancox.com.mx", "", "09", "Ciudad de México", "Cuauhtémoc", "2024-01"],
]


def test_correos_de_todo_el_grupo_y_nombres_juntos(tmp_path):
    base = make_denue(tmp_path, GRUPO)
    denue_local.build(base)
    s = Settings(db_path=str(tmp_path / "x.sqlite"), denue_local_path=str(denue_local.db_path(base)),
                 tukan_local_path="", enable_ddgs=False)
    r = buscar_persona(s, "Francisco", "Javier", "Espinoza", "Mendieta", "BancoX")
    assert r["dominio"] == "bancox.com.mx"  # de los correos del grupo, no del sitio web bancox.com
    assert "DOMINIO_CORREOS_DENUE" in r["ruta"] and "FORMATO_NOMBRES_JUNTOS" in r["ruta"]
    assert r["correo"] == "franciscojavier.espinoza@bancox.com.mx"
    assert "francisco.espinoza@bancox.com.mx" in r["alternativas"]
    assert {f["correo"] for f in r["correos_reales"]} >= {"victorhugo.olivares@bancox.com.mx", "juan.perez@bancox.com.mx"}
    # Desglose uno por uno y veredicto en palabras.
    assert r["desglose"] == {"given.paternal": 3, "first.paternal": 1}
    assert "3 llevan dos nombres juntos" in r["veredicto"] and r["correo"] in r["veredicto"]


def test_estatus_de_tukan_en_los_correos_reales(tmp_path):
    from tests.test_tukan_local import make_tukan
    path, _ = make_tukan(tmp_path)
    s = Settings(db_path=str(tmp_path / "x.sqlite"), denue_local_path="", tukan_local_path=str(path),
                 enable_ddgs=False)
    r = buscar_persona(s, "Pedro", "", "Ramirez", "Soto", "ACME INDUSTRIAL")
    estatus = {f["correo"]: f["estatus_tukan"] for f in r["correos_reales"]}
    assert estatus == {"juan.perez@acme.com.mx": "ACTIVO", "maria.lopez@acme.com.mx": "CADUCO"}
    assert r["desglose"] == {"first.paternal": 2}
    assert r["veredicto"].startswith("2 de 2 correos de persona son nombre.apellido")
