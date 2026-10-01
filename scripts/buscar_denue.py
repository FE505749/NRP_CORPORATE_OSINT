"""Busca en INEGI DENUE las empresas de un Excel y trae su dominio.

Como busca:
  1. El nombre tal cual viene, solo sin la forma legal (SA DE CV, S DE RL...).
  2. Si no hay nada, una sola variacion: el nucleo distintivo del nombre.
  Siempre a nivel NACIONAL: el estado del Excel es el del trabajador, no el
  del patron, asi que filtrar por el estaba descartando empresas correctas.

Como limpia:
  Trae TODAS las opciones que devuelve DENUE y descarta las de giro
  incompatible: si el nombre dice CONSTRUCTORA, un abarrotes no pasa.
  Las que sobreviven se guardan en ALTERNATIVAS para revisarlas a mano.

Uso:
    .venv-windows\\Scripts\\python.exe scripts\\buscar_denue.py Empresas_para_DENUE.xlsx
    ... --limite 5000        solo las mas importantes
    ... --probar             prueba token y conexion
El token va en tokens.env o en TOKEN_INEGI.txt.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from nrp_osint.config import load_tokens  # noqa: E402
from nrp_osint.domain import root_domain  # noqa: E402
from nrp_osint.normalize import clean_text, norm, normalize_company  # noqa: E402

API = "https://www.inegi.org.mx/app/api/denue/v1/consulta/Nombre"
COLS_SALIDA = ["DENUE_NOMBRE_COMERCIAL", "DENUE_RAZON_SOCIAL", "DENUE_GIRO",
               "DENUE_SITIO_WEB", "DENUE_CORREO", "DENUE_TELEFONO", "DENUE_UBICACION",
               "SCORE_NOMBRE", "GIRO_COMPATIBLE", "PATRON_CORREO", "ORIGEN_PATRON",
               "ALTERNATIVAS", "CONSULTA_USADA", "DENUE_ESTATUS"]

RUIDO = re.compile(
    r"\b(SA DE CV|S A DE C V|SA|SC|AC|SAPI|SAS|SPR|SRL|RI|RL|S DE RL|S DE PR DE RL|"
    r"DE CV|CV|IAP|SAB|SOFOM|ENR|SNC|SPA)\b")
GENERICAS = {
    "GRUPO", "CORPORATIVO", "CORPORACION", "COMPANIA", "CIA", "SOCIEDAD", "EMPRESA",
    "COMERCIALIZADORA", "COMERCIAL", "DISTRIBUIDORA", "OPERADORA", "SERVICIOS",
    "SERVICIO", "INDUSTRIAS", "INDUSTRIAL", "PRODUCTOS", "PRODUCTORA", "CONSTRUCTORA",
    "CONSTRUCCIONES", "INMOBILIARIA", "DESARROLLADORA", "DESARROLLOS", "CONSULTORIA",
    "CONSULTORES", "ADMINISTRACION", "ADMINISTRADORA", "AGRICOLA", "AGROPECUARIA",
    "TRANSPORTES", "ALIMENTOS", "MEXICO", "MEXICANA", "NACIONAL", "INTERNACIONAL",
    "ASOC", "ASOCIACION", "SOLUCIONES", "INTEGRALES", "TECNICOS", "ADMVOS",
    "GENERALES", "DE", "DEL", "LA", "LAS", "EL", "LOS", "Y", "E", "EN",
}

# Si el nombre de la empresa dice X, el giro de DENUE debe hablar de alguna de estas.
GIROS = {
    ("CONSTRUCTORA", "CONSTRUCCIONES", "CONSTRUCCION", "EDIFICACIONES", "EDIFICACION",
     "INMOBILIARIA", "DESARROLLADORA", "URBANIZADORA", "INGENIERIA"):
        ("construc", "edifica", "inmuebl", "obra", "urbaniza", "ingenier", "arquitect"),
    ("AGRICOLA", "AGROPECUARIA", "HORTALIZAS", "LEGUMBRERA", "SIEMBRA", "CULTIVO",
     "GANADERA", "AGROINDUSTRIAL", "INVERNADEROS"):
        ("agric", "cultiv", "pecuar", "ganader", "siembr", "cosech", "hortaliz",
         "invernader", "agropecuar"),
    ("TRANSPORTES", "TRANSPORTE", "AUTOTRANSPORTE", "MUDANZAS", "LOGISTICA"):
        ("transporte", "autotransporte", "carga", "mudanza", "logistic", "paqueter"),
    ("FARMACIA", "FARMACIAS", "FARMACEUTICA"):
        ("farmac", "medicament"),
    ("HOTEL", "HOTELERA", "HOTELES", "MOTEL"):
        ("hotel", "hosped", "motel", "aloja"),
    ("RESTAURANTE", "RESTAURANTES", "COMEDORES", "CAFETERIA"):
        ("restaurant", "aliment", "comida", "cafeter", "banquete"),
    ("SEGURIDAD", "VIGILANCIA"):
        ("segurid", "vigilan", "proteccion", "investigacion"),
    ("LIMPIEZA", "SANITIZACION"):
        ("limpieza", "sanit", "aseo"),
    ("TEXTIL", "CONFECCIONES", "TEXTILES", "MANUFACTURERA"):
        ("textil", "prenda", "confecc", "fabricacion", "manufactur"),
    ("AUTOMOTRIZ", "AUTOMOTORES", "LLANTAS", "REFACCIONES"):
        ("automotriz", "vehicul", "automovil", "llanta", "refaccion"),
    ("ESCUELA", "COLEGIO", "UNIVERSIDAD", "INSTITUTO", "EDUCATIVA"):
        ("escuela", "educa", "ensenanza", "universi", "instituc"),
    ("HOSPITAL", "CLINICA", "MEDICA", "MEDICOS", "SALUD"):
        ("hospital", "medic", "salud", "clinic", "consultor"),
    ("TIENDAS", "ABARROTES", "SUPERMERCADO", "MINISUPER", "AUTOSERVICIO"):
        ("abarrote", "supermerc", "menor", "autoservic", "tienda"),
    ("BANCO", "FINANCIERA", "CASA DE BOLSA", "SEGUROS", "AFORE"):
        ("banc", "financ", "credit", "segur", "bursat", "afore"),
    ("MINERA", "MINERALES", "MINAS"):
        ("miner", "extracc"),
    ("PANIFICADORA", "PANADERIA", "TORTILLERIA"):
        ("pan", "tortill", "aliment"),
}


def sector_esperado(nombre: str):
    """Palabras que deberian aparecer en el giro de DENUE, segun el nombre."""
    tokens = set(normalize_company(nombre).split())
    esperado = set()
    for claves, giros in GIROS.items():
        if tokens & set(claves):
            esperado.update(giros)
    return esperado


def giro_compatible(nombre_empresa: str, clase_actividad: str):
    """(compatible, explicacion). Sin pista de giro en el nombre, no se juzga."""
    esperado = sector_esperado(nombre_empresa)
    if not esperado:
        return True, "sin pista de giro"
    giro = (clase_actividad or "").lower()
    if not giro:
        return True, "DENUE sin giro"
    return (True, "coincide") if any(p in giro for p in esperado) else (False, "giro distinto")


def termino_principal(nombre: str) -> str:
    return re.sub(r"\s+", " ", RUIDO.sub(" ", normalize_company(nombre))).strip()


def termino_variacion(nombre: str) -> str:
    """La unica variacion permitida: el nucleo sin palabras de giro."""
    nucleo = [t for t in termino_principal(nombre).split()
              if t not in GENERICAS and len(t) >= 3]
    return " ".join(nucleo[:3]) if nucleo else ""


GRATUITOS = {"hotmail.com", "gmail.com", "yahoo.com", "yahoo.com.mx", "outlook.com",
             "live.com", "live.com.mx", "prodigy.net.mx", "aol.com", "icloud.com",
             "msn.com", "hotmail.es", "yahoo.es", "me.com"}


def dominio_de(x):
    web = (x.get("Sitio_internet") or "").strip()
    correo = (x.get("Correo_e") or "").strip()
    d = root_domain(web) if web else (
        root_domain(correo.split("@", 1)[1]) if "@" in correo else "")
    return "" if d.lower() in GRATUITOS else d


class Cliente:
    def __init__(self, tiempo=12.0):
        self.c = httpx.Client(
            timeout=tiempo, follow_redirects=True,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            headers={"User-Agent": "NRP-Corporate-OSINT-V4/4.0 public-business-research"})

    def buscar(self, termino, token, limite=20):
        """DENUE corta la conexion cuando no hay resultados: eso es lista vacia."""
        url = f"{API}/{quote(termino, safe='')}/00/1/{limite}/{token}"
        try:
            r = self.c.get(url)
            if r.status_code != 200:
                return [], f"HTTP {r.status_code}"
            try:
                return r.json(), ""
            except Exception:
                return [], ""
        except Exception as e:
            n = type(e).__name__
            if ("status_code should be in range" in str(e)
                    or n in ("RemoteProtocolError", "ReadError", "ConnectError",
                             "ConnectTimeout", "ReadTimeout", "PoolTimeout")):
                return [], ""          # sin resultados
            return [], f"{n}"


def evaluar(datos, nombre):
    """Ordena las opciones de DENUE: primero las de giro correcto y con dominio."""
    objetivo = normalize_company(nombre)
    opciones = []
    for x in datos or []:
        rs = x.get("Razon_social") or ""
        nc = x.get("Nombre") or ""
        score = max(fuzz.token_sort_ratio(objetivo, normalize_company(rs)),
                    fuzz.token_sort_ratio(objetivo, normalize_company(nc)),
                    fuzz.WRatio(objetivo, normalize_company(nc)))
        ok, razon = giro_compatible(nombre, x.get("Clase_actividad", ""))
        dom = dominio_de(x)
        opciones.append({"x": x, "score": score, "giro_ok": ok, "giro_razon": razon,
                         "dominio": dom, "nombre": nc, "razon_social": rs})
    opciones.sort(key=lambda o: (o["giro_ok"], bool(o["dominio"]), o["score"]), reverse=True)
    return opciones


ARCHIVOS_TOKEN = (PROJECT_ROOT.parent / "TOKEN_INEGI.txt", PROJECT_ROOT / "TOKEN_INEGI.txt")


def leer_token() -> str:
    for ruta in ARCHIVOS_TOKEN:
        if not ruta.exists():
            continue
        for linea in ruta.read_text(encoding="utf-8", errors="ignore").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            if "=" in linea and linea.split("=", 1)[0].strip().upper() == "INEGI_DENUE_TOKEN":
                linea = linea.split("=", 1)[1].strip()
            linea = linea.strip().strip('"').strip("'")
            if linea:
                return linea
    load_tokens()
    return os.getenv("INEGI_DENUE_TOKEN", "").strip()


def _guardar(salida: Path, cabeceras, filas):
    wb = Workbook(write_only=True)
    hoja = wb.create_sheet("DENUE")
    hoja.append(cabeceras)
    for f in filas:
        hoja.append(f)
    try:
        wb.save(salida)
        return True
    except PermissionError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Busca empresas en INEGI DENUE.")
    ap.add_argument("entrada")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--hilos", type=int, default=32)
    ap.add_argument("--minimo", type=float, default=80.0,
                    help="score minimo de nombre para aceptar")
    ap.add_argument("--probar", action="store_true")
    args = ap.parse_args()

    token = leer_token()
    cli = Cliente()
    if args.probar:
        datos, err = cli.buscar("TIENDAS CHEDRAUI", token or "sin-token", 3)
        print(f"conexion: {'OK' if not err or 'HTTP' in err else err}")
        if not token:
            print(f"token: FALTA\n  Ponlo en {ARCHIVOS_TOKEN[0]}", file=sys.stderr)
            return 1
        print(f"token: cargado ({len(token)} caracteres)")
        print(f"prueba: DENUE devolvio {len(datos)} establecimientos")
        for x in datos[:3]:
            print(f"   {str(x.get('Nombre',''))[:38]:<38} {x.get('Sitio_internet','')}")
        return 0
    if not token:
        print(f"FALTA EL TOKEN. Ponlo en {ARCHIVOS_TOKEN[0]}", file=sys.stderr)
        return 1

    entrada = Path(args.entrada)
    if not entrada.is_absolute():
        for base in (Path.cwd(), PROJECT_ROOT.parent, PROJECT_ROOT):
            if (base / entrada).exists():
                entrada = base / entrada
                break
    if not entrada.exists():
        print(f"No se encontro: {args.entrada}", file=sys.stderr)
        return 1
    salida = Path(args.salida) if args.salida else entrada.with_name("DENUE_resultados.xlsx")

    wb = load_workbook(entrada, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    cabeceras = [clean_text(x) for x in next(it)]
    filas = [list(r) for r in it]
    wb.close()
    for col in COLS_SALIDA:
        if col not in cabeceras:
            cabeceras.append(col)
    ancho = len(cabeceras)
    filas = [list(f) + [""] * (ancho - len(f)) for f in filas]
    iNom = cabeceras.index("NOMBRE_EMPRESA")
    idx = {c: cabeceras.index(c) for c in COLS_SALIDA}

    pendientes = [i for i, f in enumerate(filas)
                  if str(f[idx["DENUE_ESTATUS"]] or "PENDIENTE").upper() == "PENDIENTE"]
    if args.limite:
        pendientes = pendientes[:args.limite]

    # una consulta por nombre: la misma empresa se repite en varios estados
    primero, gemelas = {}, {}
    for i in pendientes:
        clave = termino_principal(clean_text(filas[i][iNom]))
        if clave in primero:
            gemelas.setdefault(primero[clave], []).append(i)
        else:
            primero[clave] = i
    unicas = list(primero.values())

    patrones = {}
    try:
        from nrp_osint.config import Settings
        from nrp_osint.db import DB
        _db = DB(Settings.from_env().db_path)
        for dom, pat, _ in _db.conn.execute(
                "SELECT domain,pattern,COUNT(*) FROM historical_contacts"
                " WHERE domain<>'' AND pattern<>'' GROUP BY domain,pattern ORDER BY 3 DESC"):
            patrones.setdefault(dom, pat)
        _db.conn.close()
    except Exception:
        pass

    print(f"entrada  : {entrada.name} ({len(filas):,} filas)")
    print(f"a buscar : {len(pendientes):,}  ->  {len(unicas):,} consultas reales")
    print(f"hilos    : {args.hilos} | nacional | max 2 intentos | patrones conocidos: {len(patrones):,}")
    print(f"salida   : {salida}\n")

    t0 = time.perf_counter()
    candado = threading.Lock()
    con_dom = con_giro = descartados = vacios = errores = 0

    def trabajar(i):
        nombre = clean_text(filas[i][iNom])
        if not nombre:
            return i, [], "", "sin nombre"
        t1 = termino_principal(nombre)
        datos, err = cli.buscar(t1, token)
        usada = t1
        if not datos:
            t2 = termino_variacion(nombre)
            if t2 and t2 != t1:
                datos, err = cli.buscar(t2, token)
                usada = t2
        return i, datos, err, usada

    k = 0
    with ThreadPoolExecutor(max_workers=max(1, args.hilos)) as pool:
        for i, datos, err, usada in pool.map(trabajar, unicas):
            k += 1
            fila = filas[i]
            nombre = clean_text(fila[iNom])
            fila[idx["CONSULTA_USADA"]] = usada
            if err:
                fila[idx["DENUE_ESTATUS"]] = f"ERROR {err}"[:60]
                errores += 1
            elif not datos:
                fila[idx["DENUE_ESTATUS"]] = "NO ESTA EN DENUE"
                vacios += 1
            else:
                ops = evaluar(datos, nombre)
                buenas = [o for o in ops if o["giro_ok"] and o["score"] >= args.minimo]
                elegida = buenas[0] if buenas else None
                if elegida is None:
                    mejor = ops[0]
                    fila[idx["DENUE_ESTATUS"]] = (
                        "DESCARTADO: giro distinto" if not mejor["giro_ok"]
                        else f"DESCARTADO: score {mejor['score']:.0f}")
                    fila[idx["ALTERNATIVAS"]] = " | ".join(
                        f"{o['nombre'][:28]} [{o['score']:.0f}, {o['giro_razon']}]"
                        for o in ops[:3])
                    descartados += 1
                else:
                    x = elegida["x"]
                    dom = elegida["dominio"]
                    fila[idx["DENUE_NOMBRE_COMERCIAL"]] = elegida["nombre"]
                    fila[idx["DENUE_RAZON_SOCIAL"]] = elegida["razon_social"]
                    fila[idx["DENUE_GIRO"]] = x.get("Clase_actividad", "")
                    fila[idx["DENUE_SITIO_WEB"]] = dom
                    fila[idx["DENUE_CORREO"]] = (x.get("Correo_e") or "").strip()
                    fila[idx["DENUE_TELEFONO"]] = (x.get("Telefono") or "").strip()
                    fila[idx["DENUE_UBICACION"]] = x.get("Ubicacion") or x.get("Entidad", "")
                    fila[idx["SCORE_NOMBRE"]] = round(elegida["score"], 1)
                    fila[idx["GIRO_COMPATIBLE"]] = elegida["giro_razon"]
                    if dom:
                        pat = patrones.get(dom, "")
                        fila[idx["PATRON_CORREO"]] = pat or "first.paternal | f_paternal"
                        fila[idx["ORIGEN_PATRON"]] = ("heredado del padron" if pat
                                                      else "estandar (5 variantes)")
                    fila[idx["ALTERNATIVAS"]] = " | ".join(
                        f"{o['nombre'][:28]} [{o['score']:.0f}]" for o in ops[1:4])
                    fila[idx["DENUE_ESTATUS"]] = "OK" if dom else "OK SIN DOMINIO"
                    con_dom += bool(dom)
                    con_giro += 1
            for j in gemelas.get(i, ()):
                for col in COLS_SALIDA:
                    filas[j][idx[col]] = fila[idx[col]]
            if k % 200 == 0:
                ritmo = k / max(.001, time.perf_counter() - t0)
                print(f"   {k:,}/{len(unicas):,}  dominio={con_dom:,} ok={con_giro:,} "
                      f"descartado={descartados:,} no-esta={vacios:,} err={errores:,}  "
                      f"{ritmo:.1f}/s  ~{(len(unicas)-k)/max(.001,ritmo)/60:.0f} min",
                      flush=True)
            if k % 1000 == 0:
                with candado:
                    _guardar(salida, cabeceras, filas)

    if not _guardar(salida, cabeceras, filas):
        alterno = salida.with_name(salida.stem + "_nuevo.xlsx")
        _guardar(alterno, cabeceras, filas)
        salida = alterno
        print(f"(el archivo estaba abierto en Excel, se guardo como {alterno.name})")
    print(f"\nterminado en {(time.perf_counter()-t0)/60:.1f} min")
    print(f"  CON DOMINIO       : {con_dom:,}")
    print(f"  aceptadas sin web : {con_giro-con_dom:,}")
    print(f"  descartadas       : {descartados:,}")
    print(f"  no estan en DENUE : {vacios:,}")
    print(f"  errores           : {errores:,}")
    print(f"  -> {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
