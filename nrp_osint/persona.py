"""Busqueda de personas sin cargarlas a la base: una sola (el formulario) o
muchas (un Excel). Las dos usan exactamente este codigo.

Usa el mismo motor que la carga masiva (DENUE local y luego Tukan, sin Internet),
pero en una base temporal en memoria: la consulta no se mezcla con la base
objetivo ni con los resultados guardados. Cada empresa distinta se busca una
sola vez aunque aparezca en muchas filas.
"""
from __future__ import annotations

import asyncio
import json

from .db import DB
from .emailintel import generate, pattern_from_local_part
from .normalize import canonical_person, clean_text, employer_key, normalize_company, person_key
from .pipeline import Pipeline

FALTAN_DATOS = "Faltan datos: primer nombre, apellido paterno y empresa son obligatorios."


def buscar_persona(settings, primer_nombre: str, segundo_nombre: str, paterno: str, materno: str,
                   empresa: str, tokens=None) -> dict:
    r = buscar_personas(settings, [{"primer_nombre": primer_nombre, "segundo_nombre": segundo_nombre,
                                    "primer_apellido": paterno, "segundo_apellido": materno,
                                    "empresa": empresa}], tokens=tokens)[0]
    if r["estado"] == "FALTAN_DATOS":
        raise ValueError(FALTAN_DATOS)
    return r


def buscar_personas(settings, filas: list[dict], tokens=None, avance=None) -> list[dict]:
    """filas: dicts con primer_nombre, segundo_nombre, primer_apellido,
    segundo_apellido, empresa (y cuenta, que solo se copia a la salida).
    Devuelve un resultado por fila, en el mismo orden."""
    limpias, people = [], []
    for i, f in enumerate(filas, 1):
        g = {k: clean_text(f.get(k, "")) for k in
             ("cuenta", "primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido", "empresa")}
        nombres = " ".join(x for x in (g["primer_nombre"], g["segundo_nombre"]) if x)
        g["persona"] = canonical_person(nombres, g["primer_apellido"], g["segundo_apellido"])
        g["valida"] = bool(g["primer_nombre"] and g["primer_apellido"] and g["empresa"])
        limpias.append(g)
        if g["valida"]:
            people.append({
                "row_id": i, "account_id": g["cuenta"], "person_key": person_key(g["persona"]),
                "full_name": g["persona"], "first_names": nombres, "paternal": g["primer_apellido"],
                "maternal": g["segundo_apellido"], "entity": "", "nrp": "", "company_input": g["empresa"],
                "company_norm": normalize_company(g["empresa"]), "blindaje": "", "sex": "", "institute": "",
                "age": "", "age_origin": "", "nss_local": "", "curp2_local": "",
                "employer_key": employer_key("", g["empresa"]), "ingest_source": "BUSQUEDA_PERSONA",
            })
    db = DB(":memory:")
    pipe = None
    if people:
        db.upsert_people(people)
        db.ensure_employer_jobs()
        empresas = db.conn.execute("SELECT COUNT(*) FROM employers").fetchone()[0]
        local = type(settings)(**vars(settings))
        local.denue_local_only = True  # solo copias locales: DENUE y Tukan
        pipe = Pipeline(db, local, tokens or {"denue": "", "searxng": ""})
        progreso = (lambda n, j, r: avance(n, empresas)) if avance else None
        asyncio.run(pipe.run(max_jobs=0, batch=200, progress=progreso))
    empleadores = {r["employer_key"]: dict(r) for r in db.conn.execute("SELECT * FROM employers")}
    resultados = {r["row_id"]: dict(r) for r in db.conn.execute("SELECT * FROM results")}
    evidencias: dict[str, list] = {}
    for r in db.conn.execute("SELECT employer_key, source_name, text, score FROM evidence ORDER BY id"):
        evidencias.setdefault(r["employer_key"], []).append(
            {"source_name": r["source_name"], "text": r["text"], "score": r["score"]})
    db.conn.close()

    reales_por_dominio: dict[str, list] = {}
    salida = []
    for i, g in enumerate(limpias, 1):
        if not g["valida"]:
            salida.append(_vacio(g))
            continue
        key = employer_key("", g["empresa"])
        emp, res = empleadores.get(key, {}), resultados.get(i, {})
        dominio = emp.get("domain", "")
        if dominio not in reales_por_dominio:
            reales_por_dominio[dominio] = _correos_reales(pipe, dominio)
        try:
            patrones = json.loads(emp.get("email_patterns_json") or "{}")
        except ValueError:
            patrones = {}
        correo = res.get("email", "")
        persona = {"first_names": " ".join(x for x in (g["primer_nombre"], g["segundo_nombre"]) if x),
                   "paternal": g["primer_apellido"], "maternal": g["segundo_apellido"]}
        r = {
            **{k: g[k] for k in ("cuenta", "primer_nombre", "segundo_nombre", "primer_apellido",
                                 "segundo_apellido")},
            "empresa_entrada": g["empresa"],
            "persona": g["persona"],
            "correo": correo,
            "patron": _patron_de(persona, correo, dominio, patrones),
            "tipo": res.get("email_type", ""),
            "confianza": res.get("email_confidence", 0) or 0,
            "alternativas": json.loads(res.get("email_alternatives_json") or "[]"),
            "estado": emp.get("status", ""),
            "empresa": emp.get("company_canonical", "") or g["empresa"],
            "dominio": dominio,
            "confianza_dominio": round(float(emp.get("domain_confidence") or 0) * 100, 1),
            "sitio_web": emp.get("website", ""),
            "patrones": patrones,
            "confianza_patron": round(float(emp.get("pattern_confidence") or 0) * 100, 1),
            "contacto_tukan": emp.get("tukan_email", ""),
            "estatus_tukan": emp.get("tukan_status", ""),
            "ruta": [x for x in (emp.get("match_route") or "").split("|") if x],
            "evidencia": evidencias.get(key, []),
            "correos_reales": reales_por_dominio[dominio],
        }
        r["desglose"] = desglose(r["correos_reales"])
        r["veredicto"] = veredicto(r)
        salida.append(r)
    return salida


def _vacio(g: dict) -> dict:
    return {**{k: g[k] for k in ("cuenta", "primer_nombre", "segundo_nombre", "primer_apellido",
                                 "segundo_apellido")},
            "empresa_entrada": g["empresa"], "persona": g["persona"], "correo": "", "patron": "", "tipo": "",
            "confianza": 0, "alternativas": [], "estado": "FALTAN_DATOS", "empresa": g["empresa"], "dominio": "",
            "confianza_dominio": 0, "sitio_web": "", "patrones": {}, "confianza_patron": 0, "contacto_tukan": "",
            "estatus_tukan": "", "ruta": [], "evidencia": [], "correos_reales": [], "desglose": {},
            "veredicto": FALTAN_DATOS}


def _patron_de(persona: dict, correo: str, dominio: str, patrones: dict) -> str:
    """Que formato produjo el correo elegido: primero los aprendidos de la empresa."""
    if not correo:
        return ""
    for p in [*sorted(patrones, key=lambda k: -patrones[k]), *ETIQUETA_FORMATO]:
        if p and generate(persona, p, dominio) == correo:
            return p
    return ""


def _correos_reales(pipe, domain: str) -> list[dict]:
    """Los correos que existen en DENUE y Tukan con ese dominio exacto: la prueba
    del dominio y del formato. Los que tienen forma de persona van primero."""
    if not domain:
        return []
    fuentes = {}
    if pipe.denue is not None and hasattr(pipe.denue, "domain_emails"):
        fuentes["DENUE"] = pipe.denue.domain_emails(domain)
    estatus = {}
    if pipe.tukan is not None:
        fuentes["Tukan"] = pipe.tukan.emails_for_domain(domain)
        estatus = pipe.tukan.statuses_for_domain(domain)
    filas = []
    for correo in sorted(set().union(*fuentes.values()) if fuentes else ()):
        filas.append({
            "correo": correo,
            "fuente": " y ".join(f for f, s in fuentes.items() if correo in s),
            "estatus_tukan": estatus.get(correo, ""),
            "formato": pattern_from_local_part(correo.split("@", 1)[0], allow_single=True),
        })
    filas.sort(key=lambda f: (not f["formato"], f["correo"]))
    return filas


# Textos legibles para explicar como se construyo un correo.
ETIQUETA_TIPO = {
    "PUBLICADO": "Publicado por la empresa",
    "CONFIRMADO_TUKAN": "Confirmado: el correo existe en Tukan",
    "DEDUCIDO_PATRON": "Deducido con formato aprendido",
    "DEDUCIDO_PATRON_DENUE_TUKAN": "Deducido con el formato de correos reales de DENUE y Tukan",
    "DEDUCIDO_PATRON_DENUE": "Deducido con el formato de correos reales de DENUE",
    "DEDUCIDO_PATRON_TUKAN": "Deducido con el formato de correos reales de Tukan",
    "DEDUCIDO_PATRON_DEBIL": "Deducido con un formato débil",
    "DEDUCIDO_SIN_PATRON": "Deducido sin formato: el más común en México",
}
ETIQUETA_ESTADO = {
    "SIN_DOMINIO_DENUE": "La empresa está en DENUE pero sin correo ni sitio web.",
    "SIN_DOMINIO_TUKAN": "La empresa está en Tukan pero solo con correo gratuito o de un despacho.",
    "SIN_COINCIDENCIA_DENUE": "No se encontró la empresa en DENUE ni en Tukan.",
    "SIN_EMPRESA": "No se pudo identificar la empresa.",
}
ETIQUETA_RUTA = {
    "EMPRESA_APORTADA": "Empresa escrita en la búsqueda",
    "DENUE_LOCAL": "Empresa encontrada en INEGI DENUE",
    "TUKAN_NOMBRE": "Empresa encontrada en Tukan por nombre",
    "TUKAN_NRP": "Empresa encontrada en Tukan por NRP",
    "DOMINIO_CORREOS_DENUE": "Dominio sacado de los correos de sus sucursales en DENUE",
    "DOMINIO_CORREOS_TUKAN": "Dominio sacado de los correos en Tukan",
    "TUKAN_CONFIRMA_DOMINIO": "DENUE y Tukan coinciden en el dominio",
    "DOMINIO_SITIO_WEB": "Sin correos de empresa: dominio tomado del sitio web",
    "DOMINIO_SITIO_WEB_CONFIRMADO": "Dominio del sitio web, confirmado con correos reales",
    "FORMATO_NOMBRES_JUNTOS": "La empresa escribe todos los nombres juntos (victorhugo.olivares)",
    "DENUE_PATRON": "Formato aprendido de correos de DENUE",
    "TUKAN_PATRON": "Formato aprendido de correos de Tukan",
    "COMPANY_PROFILE_LOCAL": "Perfil de empresa ya conocido",
}

ETIQUETA_FORMATO = {
    "first.paternal": "nombre.apellido",
    "given.paternal": "nombresjuntos.apellido",
    "f.paternal": "inicial.apellido",
    "f_paternal": "inicialapellido",
    "first_paternal": "nombre_apellido",
    "first-paternal": "nombre-apellido",
    "firstpaternal": "nombreapellido",
    "first.paternalmaternal": "nombre.paternomaterno",
    "first.last": "nombre.materno",
    "f_last": "inicialmaterno",
    "f.last": "inicial.materno",
    "": "genérico o no reconocido",
}


def desglose(correos_reales: list[dict]) -> dict[str, int]:
    """Cuantos correos reales hay de cada formato, contados uno por uno, antes de
    decidir la regla. Los genericos (ventas@, info@...) van aparte con la clave ''."""
    out: dict[str, int] = {}
    for f in correos_reales:
        out[f["formato"]] = out.get(f["formato"], 0) + 1
    return dict(sorted(out.items(), key=lambda x: (x[0] == "", -x[1])))


def veredicto(r: dict) -> str:
    """Por que salio ese correo, en palabras."""
    correo, dominio = r["correo"], r["dominio"]
    if not correo:
        return "No se pudo construir un correo: no se encontró el dominio de la empresa."
    d = desglose(r["correos_reales"])
    personas = sum(n for k, n in d.items() if k)
    genericos = d.get("", 0)
    nota_gen = ("" if not genericos else
                " 1 correo genérico o no reconocido (ventas@, info@…) no cuenta." if genericos == 1 else
                f" {genericos} correos genéricos o no reconocidos (ventas@, info@…) no cuentan.")
    if r["tipo"] == "CONFIRMADO_TUKAN":
        return f"El correo {correo} existe tal cual en Tukan para esta empresa."
    if not personas:
        return (f"No hay correos de persona con @{dominio} en DENUE ni en Tukan, así que se usó "
                f"nombre.apellido, el formato más común en México, sin respaldo: {correo}. Tómalo con cautela.")
    pat = r["patrones"]
    principal = max(pat, key=pat.get) if pat else ""
    juntos, simple = d.get("given.paternal", 0), d.get("first.paternal", 0)
    un_nombre = not r.get("segundo_nombre")
    if principal == "given.paternal":
        txt = (f"De {juntos + simple} correos tipo nombre.apellido, {juntos} llevan dos nombres juntos "
               f"({juntos / (juntos + simple):.0%}). Si la empresa usara solo el primer nombre, nunca "
               f"aparecerían dos nombres juntos; los otros {simple} son de personas con un solo nombre, que "
               f"se ven igual con las dos reglas. Por eso se usan todos los nombres juntos")
        txt += (f". Esta persona tiene un solo nombre, así que da lo mismo: {correo}." if un_nombre else f": {correo}.")
    elif principal == "first.paternal":
        txt = f"{juntos + simple} de {personas} correos de persona son nombre.apellido"
        if juntos:
            txt += (f"; solo {juntos} llevan dos nombres juntos, muy pocos para cambiar la regla "
                    f"(se necesitan al menos 3 y el 20 %)")
        txt += f". Por eso se usa solo el primer nombre: {correo}."
    else:
        txt = (f"El formato más común es {ETIQUETA_FORMATO.get(principal, principal)} "
               f"({pat.get(principal, 0)} de {personas} correos de persona): {correo}.")
    return txt + nota_gen
