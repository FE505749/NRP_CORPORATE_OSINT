"""Servidor local de NRP Intelligence (FastAPI + uvicorn).

Mismo esquema que skill-diagramas-visio: el servidor entrega la pagina
(`front/index.html`), los estaticos en `/front` y las funciones en `/api`.
Escucha solo en 127.0.0.1: NSS y CURP_2 no salen del equipo.

Correr a mano:
    .venv-windows\\Scripts\\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8501
o con el lanzador de siempre: run_dashboard_windows.bat
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from nrp_osint import denue_local, lote, tukan_local
from nrp_osint.config import Settings, load_tokens, tokens
from nrp_osint.crossmatch import build_local_crossmatch, crossmatch_counts, write_crossmatch_csv
from nrp_osint.db import DB
from nrp_osint.exporter import RESULT_SQL, export_all
from nrp_osint.historical import import_history, rebuild_knowledge
from nrp_osint.io_stream import ingest
from nrp_osint.local_reuse import apply_local_reuse
from nrp_osint.paths import PROJECT_ROOT, safe_upload_name
from nrp_osint.persona import (ETIQUETA_ESTADO, ETIQUETA_FORMATO, ETIQUETA_RUTA, ETIQUETA_TIPO,
                               buscar_persona)
from nrp_osint.pipeline import Pipeline
from nrp_osint.stats import counts
from nrp_osint.tabular import revisar_archivo

DIR_FRONT = PROJECT_ROOT / "front"
TIPOS_ARCHIVO = {".xlsx", ".xlsm", ".csv"}


# --------------------------------------------------------------------------- trabajos

class Trabajos:
    """Procesos largos en un hilo aparte: la pagina consulta su avance cada
    segundo en vez de quedarse esperando una respuesta de varios minutos."""

    def __init__(self):
        self._t: dict[str, dict] = {}
        self._lock = threading.Lock()

    def iniciar(self, nombre: str, fn) -> str:
        tid = uuid.uuid4().hex[:12]
        with self._lock:
            self._t[tid] = {"id": tid, "nombre": nombre, "estado": "corriendo", "texto": "Preparando…",
                            "hecho": 0, "total": 0, "resultado": None, "error": "", "inicio": time.time()}

        def avance(texto: str, hecho: int = 0, total: int = 0):
            with self._lock:
                self._t[tid].update(texto=texto, hecho=hecho, total=total)

        def correr():
            try:
                resultado = fn(avance)
                with self._lock:
                    self._t[tid].update(estado="listo", resultado=resultado, texto="Listo")
            except Exception as exc:  # el error se muestra en la pagina
                with self._lock:
                    self._t[tid].update(estado="error", error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=correr, daemon=True).start()
        return tid

    def ver(self, tid: str) -> dict | None:
        with self._lock:
            t = self._t.get(tid)
            return dict(t) if t else None


# --------------------------------------------------------------------------- app

class PersonaIn(BaseModel):
    primer_nombre: str = ""
    segundo_nombre: str = ""
    apellido_paterno: str = ""
    apellido_materno: str = ""
    empresa: str = ""


class TokensIn(BaseModel):
    denue: str = ""
    searxng: str = ""


def crear_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        load_tokens()
        settings = Settings.from_env()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.output_dir).mkdir(parents=True, exist_ok=True)
    trabajos = Trabajos()
    app = FastAPI(title="NRP Intelligence", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.trabajos = trabajos

    @contextmanager
    def base():
        """Una conexion por peticion: SQLite en modo WAL deja leer mientras un
        trabajo en segundo plano escribe con la suya."""
        db = DB(settings.db_path)
        try:
            yield db
        finally:
            db.conn.close()

    def guardar(archivo: UploadFile, prefijo: str = "") -> Path:
        nombre = safe_upload_name(archivo.filename or "archivo")
        if Path(nombre).suffix.lower() not in TIPOS_ARCHIVO:
            raise HTTPException(400, f"{nombre}: solo se aceptan Excel (.xlsx, .xlsm) o CSV.")
        ruta = Path(settings.upload_dir) / (prefijo + nombre)
        with open(ruta, "wb") as f:
            while bloque := archivo.file.read(1 << 20):
                f.write(bloque)
        problema = revisar_archivo(ruta)
        if problema:
            raise HTTPException(400, problema.replace("**", ""))
        return ruta

    def info_denue():
        return denue_local.info(Path(settings.denue_local_path).parent)

    def info_tukan():
        return tukan_local.info(Path(settings.tukan_local_path).parent)

    # ---- pagina y estaticos ------------------------------------------------
    app.mount("/front", StaticFiles(directory=DIR_FRONT), name="front")

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(DIR_FRONT / "index.html", media_type="text/html")

    @app.get("/api/salud")
    def salud():
        return {"ok": True, "app": "NRP Intelligence"}

    @app.get("/api/estado")
    def estado():
        with base() as db:
            c = counts(db)
        return {"conteos": c, "denue": info_denue(), "tukan": info_tukan()}

    @app.get("/api/etiquetas")
    def etiquetas():
        return {"tipo": ETIQUETA_TIPO, "estado": ETIQUETA_ESTADO, "ruta": ETIQUETA_RUTA, "formato": ETIQUETA_FORMATO}

    @app.get("/api/trabajos/{tid}")
    def trabajo(tid: str):
        t = trabajos.ver(tid)
        if not t:
            raise HTTPException(404, "Ese proceso ya no existe.")
        return t

    # ---- buscar: base completa ------------------------------------------------
    @app.post("/api/base")
    def subir_base(archivo: UploadFile = File(...)):
        ruta = guardar(archivo)

        def proceso(avance):
            pasos = []
            with base() as db:
                avance("Importando tu base…")
                info = ingest(db, ruta)
                fuente = ruta.name
                if counts(db)["historicos"]:
                    avance("Cruzando con el histórico local…")
                    reuso = apply_local_reuse(db, settings)
                    pasos.append(f"Histórico: {reuso['matched']:,} coincidencias, "
                                 f"{reuso['direct_account_emails']:,} con correo directo por cuenta")
                if info_denue() or info_tukan():
                    pendientes = db.conn.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status IN ('PENDING','RETRY') AND employer_key IN "
                        "(SELECT employer_key FROM people WHERE ingest_source=?)", (fuente,)).fetchone()[0]
                    local = Settings(**vars(settings))
                    local.denue_local_only = True
                    pipe = Pipeline(db, local, tokens())
                    avance(f"DENUE + Tukan: 0 de {pendientes:,} empleadores", 0, pendientes)
                    hechos = asyncio.run(pipe.run(
                        max_jobs=0, batch=200, source=fuente,
                        progress=lambda n, j, r: avance(f"DENUE + Tukan: {n:,} de {pendientes:,} empleadores",
                                                        n, pendientes))) if pendientes else 0
                    reutilizados = pipe.materialize_known(fuente)
                    pasos.append(f"DENUE + Tukan: {hechos:,} empleadores buscados, "
                                 f"{reutilizados:,} ya conocidos reutilizados")
                else:
                    pasos.append("No hay DENUE ni Tukan precargados (Fuentes): se omitió")
                total, con_correo = db.conn.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN r.email<>'' THEN 1 ELSE 0 END) FROM people p "
                    "LEFT JOIN results r USING(row_id) WHERE p.ingest_source=?", (fuente,)).fetchone()
            return {"archivo": ruta.name, "registros": info["rows"], "total": total or 0,
                    "con_correo": con_correo or 0, "pasos": pasos, "columnas": info["mapping"]}

        return {"trabajo": trabajos.iniciar("base", proceso)}

    # ---- buscar: Excel de personas (igual que la busqueda individual) ----------
    @app.get("/api/lote/plantilla")
    def plantilla_lote():
        ruta = lote.plantilla(Path(settings.output_dir) / "plantilla_personas.xlsx")
        return FileResponse(ruta, filename="plantilla_personas.xlsx")

    @app.post("/api/lote")
    def subir_lote(archivo: UploadFile = File(...)):
        ruta = guardar(archivo, "lote_")
        try:
            filas = lote.leer(ruta)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        def proceso(avance):
            avance(f"Buscando {len(filas):,} personas…", 0, len(filas))
            resultados = lote.procesar(settings, filas,
                                       avance=lambda n, total: avance(f"Empresas buscadas: {n:,} de {total:,}", n, total))
            avance("Escribiendo el Excel…", len(filas), len(filas))
            nombre = f"correos_estimados_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
            lote.exportar(resultados, Path(settings.output_dir) / nombre)
            con_correo = sum(bool(r["correo"]) for r in resultados)
            vista = [{"cuenta": r["cuenta"], "correo": r["correo"],
                      "patron": ETIQUETA_FORMATO.get(r["patron"], r["patron"]) if r["patron"] else "",
                      "dominio": r["dominio"]} for r in resultados[:200]]
            return {"archivo": nombre, "filas": len(resultados), "con_correo": con_correo,
                    "sin_correo": len(resultados) - con_correo, "vista": vista}

        return {"trabajo": trabajos.iniciar("lote", proceso)}

    # ---- buscar: una persona --------------------------------------------------
    @app.post("/api/persona")
    def persona(p: PersonaIn):
        try:
            r = buscar_persona(settings, p.primer_nombre, p.segundo_nombre, p.apellido_paterno,
                               p.apellido_materno, p.empresa)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        r["tipo_texto"] = ETIQUETA_TIPO.get(r["tipo"], r["tipo"])
        r["estado_texto"] = ETIQUETA_ESTADO.get(r["estado"], r["estado"])
        r["ruta_texto"] = [ETIQUETA_RUTA.get(x, x) for x in r["ruta"]]
        r["desglose_texto"] = [{"formato": ETIQUETA_FORMATO.get(k, k), "n": n} for k, n in r["desglose"].items()]
        for f in r["correos_reales"]:
            f["formato_texto"] = ETIQUETA_FORMATO.get(f["formato"], f["formato"])
        return r

    # ---- cruce ----------------------------------------------------------------
    @app.get("/api/cruce")
    def cruce(limite: int = 500):
        with base() as db:
            return {"resumen": crossmatch_counts(db), "filas": build_local_crossmatch(db, limit=min(limite, 2000))}

    @app.post("/api/cruce/csv")
    def cruce_csv():
        destino = Path(settings.output_dir) / "cruce_local.csv"
        with base() as db:
            info = write_crossmatch_csv(db, destino)
        return {"filas": info["filas"], "archivo": destino.name}

    @app.get("/api/descargas/{nombre}")
    def descarga(nombre: str):
        ruta = (Path(settings.output_dir) / Path(nombre).name).resolve()
        if ruta.parent != Path(settings.output_dir).resolve() or not ruta.is_file():
            raise HTTPException(404, "El archivo no existe.")
        return FileResponse(ruta, filename=ruta.name)

    # ---- resultados -----------------------------------------------------------
    @app.get("/api/resultados")
    def resultados(q: str = ""):
        sql = RESULT_SQL.replace("ORDER BY p.row_id", "")
        params: list = []
        if q:
            sql += " WHERE p.account_id LIKE ? OR p.nrp LIKE ? OR p.company_input LIKE ? OR r.email LIKE ?"
            params = ["%" + q + "%"] * 4
        sql += " ORDER BY p.row_id LIMIT 2000"
        with base() as db:
            cur = db.conn.execute(sql, params)
            columnas = [d[0] for d in cur.description]
            filas = [list(r) for r in cur.fetchall()]
        return {"columnas": columnas, "filas": filas}

    @app.post("/api/exportar")
    def exportar():
        with base() as db:
            try:
                info = export_all(db, settings.output_dir)
            except PermissionError as exc:
                raise HTTPException(409, str(exc))
        return {"filas": info["rows"], "archivo": Path(info["csv"]).name}

    # ---- historico ------------------------------------------------------------
    @app.get("/api/historico")
    def historico():
        with base() as db:
            c = counts(db)
            con_correo = db.conn.execute("SELECT COUNT(*) FROM historical_contacts WHERE email<>''").fetchone()[0]
            archivos = [dict(r) for r in db.conn.execute(
                "SELECT source_file AS archivo, COUNT(*) AS filas, SUM(CASE WHEN email<>'' THEN 1 ELSE 0 END) AS con_correo,"
                " SUM(verified) AS validados FROM historical_contacts GROUP BY source_file ORDER BY filas DESC")]
        return {"registros": c["historicos"], "con_correo": con_correo, "empresas": c["empresas_unicas"],
                "nrp": c["nrp_unicos"], "archivos": archivos}

    @app.post("/api/historico")
    def subir_historico(archivos: list[UploadFile] = File(...), validado: bool = Form(False)):
        rutas = [(a.filename, guardar(a, "hist_")) for a in archivos]

        def proceso(avance):
            reporte = []
            with base() as db:
                for i, (nombre, ruta) in enumerate(rutas, 1):
                    avance(f"Guardando {nombre} ({i} de {len(rutas)})…", i - 1, len(rutas))
                    try:
                        info = import_history(db, ruta, rebuild=False)
                    except Exception as exc:
                        reporte.append({"archivo": nombre, "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    if validado:
                        db.conn.execute("UPDATE historical_contacts SET verified=1 WHERE source_file=?",
                                        (info["source_file"],))
                        db.conn.commit()
                    reporte.append({"archivo": nombre, "filas": info["rows_imported"],
                                    "utilizables": info["rows_usable"], "columnas": info["columns"]})
                avance("Reconstruyendo el conocimiento local…", len(rutas), len(rutas))
                rebuild_knowledge(db)
            return {"reporte": reporte}

        return {"trabajo": trabajos.iniciar("historico", proceso)}

    # ---- fuentes --------------------------------------------------------------
    @app.get("/api/fuentes")
    def fuentes():
        t = tokens()
        with base() as db:
            salud_fuentes = [dict(r) for r in db.conn.execute(
                "SELECT source_name AS fuente, state AS estado, success_count AS exitos, failure_count AS fallas,"
                " throttle_count AS limitadas, last_status AS ultimo, last_error AS error,"
                " datetime(updated_at,'unixepoch','localtime') AS actualizado FROM source_health ORDER BY source_name")]
        fuente_tk = tukan_local.default_source_file()
        return {"denue": info_denue(), "tukan": info_tukan(), "salud": salud_fuentes,
                "token_denue": bool(t.get("denue")), "searxng": t.get("searxng", ""),
                "archivo_tukan": fuente_tk.name if fuente_tk else ""}

    @app.post("/api/fuentes/tokens")
    def guardar_tokens(t: TokensIn):
        env = Path(settings.tokens_path)
        actual = {}
        if env.exists():
            for linea in env.read_text(encoding="utf-8").splitlines():
                if "=" in linea and not linea.lstrip().startswith("#"):
                    k, v = linea.split("=", 1)
                    actual[k] = v
        if t.denue:
            actual["INEGI_DENUE_TOKEN"] = t.denue
        if t.searxng:
            actual["SEARXNG_URL"] = t.searxng
        for k, v in {"ENABLE_CLEARBIT_AUTOCOMPLETE": "true", "ENABLE_WIKIDATA": "true", "ENABLE_COMMON_CRAWL": "true",
                     "ENABLE_DDGS": "true", "ENABLE_DNS": "true", "GLOBAL_CONCURRENCY": "12",
                     "PER_HOST_CONCURRENCY": "2"}.items():
            actual.setdefault(k, v)
        env.write_text("\n".join(f"{k}={v}" for k, v in actual.items()) + "\n", encoding="utf-8")
        return {"ok": True}

    @app.post("/api/fuentes/denue")
    def actualizar_denue():
        def proceso(avance):
            meta = denue_local.update(Path(settings.denue_local_path).parent,
                                      lambda fase, i, n, nombre: avance(f"{fase.capitalize()} {i}/{n} · {nombre}", i, n))
            denue_local.default_source.cache_clear()
            return meta
        return {"trabajo": trabajos.iniciar("denue", proceso)}

    @app.post("/api/fuentes/tukan")
    def precargar_tukan():
        fuente = tukan_local.default_source_file()
        if not fuente:
            raise HTTPException(404, "No se encontró Tukan.xlsx junto al proyecto.")

        def proceso(avance):
            meta = tukan_local.build(fuente, Path(settings.tukan_local_path).parent,
                                     lambda n: avance(f"Precargando Tukan: {n:,} contactos", n, 0))
            tukan_local.default_source.cache_clear()
            return meta
        return {"trabajo": trabajos.iniciar("tukan", proceso)}

    # ---- resumen y auditoria ----------------------------------------------------
    @app.get("/api/resumen")
    def resumen():
        with base() as db:
            c = counts(db)
            salud_fuentes = [dict(r) for r in db.conn.execute(
                "SELECT source_name AS fuente, state AS estado FROM source_health ORDER BY source_name LIMIT 6")]
        personas = c["personas"]
        return {
            "conteos": c, "salud": salud_fuentes,
            "cobertura": {
                "contactos": round(c["contactos_resueltos"] / personas * 100, 1) if personas else 0,
                "empleadores": round(c["jobs_done"] / max(1, c["jobs_done"] + c["jobs_pendientes"]) * 100, 1),
                "cuenta": round(c["resueltos_id_cuenta"] / max(1, personas) * 100, 1),
            },
        }

    @app.get("/api/auditoria")
    def auditoria():
        with base() as db:
            evidencia = [dict(r) for r in db.conn.execute(
                "SELECT kind AS tipo, source_name AS fuente, url, score AS puntaje, text AS detalle,"
                " datetime(observed_at,'unixepoch','localtime') AS fecha FROM evidence ORDER BY id DESC LIMIT 1000")]
            red = [dict(r) for r in db.conn.execute(
                "SELECT source_name AS fuente, event_type AS evento, detail AS detalle,"
                " datetime(observed_at,'unixepoch','localtime') AS fecha FROM network_events ORDER BY id DESC LIMIT 500")]
        return {"evidencia": evidencia, "red": red}

    return app


app = crear_app()
