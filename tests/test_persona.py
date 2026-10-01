import pytest

from nrp_osint import denue_local
from nrp_osint.config import Settings
from nrp_osint.persona import buscar_persona
from tests.test_denue_local import SUCURSALES, make_denue


def test_buscar_persona_sin_excel(tmp_path):
    base = make_denue(tmp_path, SUCURSALES)
    denue_local.build(base)
    s = Settings(db_path=str(tmp_path / "no_se_usa.sqlite"), denue_local_path=str(denue_local.db_path(base)),
                 tukan_local_path="", enable_ddgs=False)
    r = buscar_persona(s, "Francisco", "Javier", "Espinoza", "Mendieta", "Profu Afore")
    assert r["persona"] == "Francisco Javier Espinoza Mendieta"
    assert r["correo"] == "francisco.espinoza@profu.com.mx"
    assert r["tipo"] == "DEDUCIDO_PATRON_DENUE" and r["dominio"] == "profu.com.mx"
    assert "DOMINIO_CORREOS_DENUE" in r["ruta"]
    # La busqueda corre en memoria: no crea ni toca la base objetivo.
    assert not (tmp_path / "no_se_usa.sqlite").exists()


def test_buscar_persona_valida_campos(tmp_path):
    with pytest.raises(ValueError):
        buscar_persona(Settings(), "Ana", "", "", "", "Profu")
