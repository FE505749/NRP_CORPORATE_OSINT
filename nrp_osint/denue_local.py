"""Copia local del DENUE de INEGI.

Se descarga la descarga masiva (un CSV por estado), se guarda en una base SQLite
aparte con indice FTS5 y se consulta sin token y sin Internet. Asi la busqueda de
decenas de miles de empresas toma minutos en vez de horas de llamadas a la API.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import time
import zipfile
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

from .domain import emails_in, is_blocked_domain, mail_domain, root_domain
from .emailintel import pattern_from_local_part
from .normalize import clean_text, norm, normalize_company
from .paths import PROJECT_ROOT

BASE_URL = "https://www.inegi.org.mx/contenidos/masiva/denue/"
# Estado de Mexico se publica partido en dos archivos.
STATE_FILES = [f"{i:02d}" for i in range(1, 15)] + ["15_1", "15_2"] + [f"{i:02d}" for i in range(16, 33)]
DEFAULT_DIR = PROJECT_ROOT / "data" / "denue"

# Correos de proveedores gratuitos: no identifican el dominio de la empresa.
FREE_MAIL = {
    "gmail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "live.com", "live.com.mx",
    "yahoo.com", "yahoo.com.mx", "yahoo.es", "icloud.com", "me.com", "msn.com", "aol.com",
    "prodigy.net.mx", "protonmail.com", "proton.me", "gmx.com", "terra.com.mx", "infinitummail.com",
}

# Formas legales que normalize_company no quita cuando vienen con puntos
# ("S.A. de C.V." queda como "S A DE C V"). Se aplica solo dentro de DENUE local
# para no alterar las llaves company_norm ya guardadas en la base principal.
_LEGAL_EXTRA = re.compile(
    r"(?:^|\s)(S A P I DE C V|S A B DE C V|S A DE C V|S DE R L DE C V|S DE R L|S DE P R DE R L|S C DE R L|"
    r"S A P I|S A B|S A|S C|A C|I A P|S P R|SAB DE CV|SAPI|SOFOM|ENR|E N R|DE C V|DE CV|SA)\s*$")


def denue_norm(value) -> str:
    s = normalize_company(value)
    prev = None
    while s and s != prev:
        prev = s
        s = _LEGAL_EXTRA.sub("", s).strip()
    return s or normalize_company(value)


SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
CREATE TABLE est(
  id INTEGER PRIMARY KEY,
  clee TEXT, nom_estab TEXT, raz_social TEXT, codigo_act TEXT, nombre_act TEXT, per_ocu TEXT,
  telefono TEXT, correoelec TEXT, www TEXT, cve_ent TEXT, entidad TEXT, municipio TEXT,
  fecha_alta TEXT,
  raz_norm TEXT, nom_norm TEXT,
  dominio TEXT,          -- dominio del sitio web registrado
  dominio_correo TEXT    -- dominio del correo, si no es de proveedor gratuito
);
CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT);
"""
POST = """
CREATE INDEX idx_est_raz ON est(raz_norm);
CREATE INDEX idx_est_nom ON est(nom_norm);
CREATE INDEX idx_est_domc ON est(dominio_correo);
CREATE VIRTUAL TABLE fts USING fts5(raz_norm, nom_norm, content='est', content_rowid='id', tokenize='unicode61');
INSERT INTO fts(fts) VALUES('rebuild');
ANALYZE;
"""


def db_path(base_dir: Path | str = DEFAULT_DIR) -> Path:
    return Path(base_dir) / "denue.sqlite"


# --------------------------------------------------------------------------- descarga

def download(base_dir: Path | str = DEFAULT_DIR, progress=None, timeout: float = 900.0) -> list[Path]:
    """Descarga los 33 ZIP. Los que ya estan completos (mismo tamano) se omiten."""
    import httpx

    raw = Path(base_dir) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    out = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for i, code in enumerate(STATE_FILES, 1):
            name = f"denue_{code}_csv.zip"
            url = BASE_URL + name
            dest = raw / name
            want = int(client.head(url).headers.get("content-length") or 0)
            if not (dest.exists() and dest.stat().st_size == want):
                part = dest.with_suffix(".part")
                with client.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(part, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                part.replace(dest)
            if not zipfile.is_zipfile(dest):
                raise RuntimeError(f"{name} no es un ZIP valido; INEGI pudo cambiar la ruta de descarga")
            out.append(dest)
            if progress:
                progress(i, len(STATE_FILES), name)
    return out


# --------------------------------------------------------------------------- carga

def _decode_stream(raw_bytes):
    """INEGI publica en latin-1; si algun archivo viene en UTF-8 tambien se lee."""
    head = raw_bytes.peek(1 << 20) if hasattr(raw_bytes, "peek") else b""
    try:
        head.decode("utf-8")
        enc = "utf-8-sig"
    except UnicodeDecodeError:
        enc = "latin-1"
    return io.TextIOWrapper(raw_bytes, encoding=enc, newline="")


def _rows_from_zip(path: Path):
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            low = info.filename.lower()
            if not low.endswith(".csv") or "conjunto_de_datos" not in low:
                continue
            with z.open(info) as fh:
                reader = csv.DictReader(_decode_stream(io.BufferedReader(fh)))
                for row in reader:
                    yield row


def _email_domain(email: str) -> str:
    if "@" not in email:
        return ""
    d = root_domain(email.split("@", 1)[1])
    return "" if not d or d in FREE_MAIL else d


def _site_domain(www: str) -> str:
    d = root_domain(www) if www else ""
    return "" if not d or is_blocked_domain(d) or d in FREE_MAIL else d


def build(base_dir: Path | str = DEFAULT_DIR, progress=None) -> dict:
    """Construye denue.sqlite desde los ZIP descargados. Escribe en un archivo
    temporal y lo reemplaza al final, asi una carga interrumpida no deja la
    base a medias."""
    base = Path(base_dir)
    zips = sorted((base / "raw").glob("denue_*_csv.zip"))
    if not zips:
        raise FileNotFoundError("No hay archivos DENUE descargados. Ejecuta primero la descarga.")
    final = db_path(base)
    tmp = final.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.executescript(SCHEMA)
    total = con_web = con_correo = 0
    for i, z in enumerate(zips, 1):
        lote = []
        for r in _rows_from_zip(z):
            g = lambda k: clean_text(r.get(k))
            raz, nom, www, correo = g("raz_social"), g("nom_estab"), g("www"), g("correoelec").lower()
            dom, domc = _site_domain(www), _email_domain(correo)
            con_web += bool(dom)
            con_correo += bool(domc)
            lote.append((g("clee"), nom, raz, g("codigo_act"), g("nombre_act"), g("per_ocu"), g("telefono"),
                         correo, www, g("cve_ent"), g("entidad"), g("municipio"), g("fecha_alta"),
                         denue_norm(raz), denue_norm(nom), dom, domc))
            if len(lote) >= 50000:
                conn.executemany("INSERT INTO est(clee,nom_estab,raz_social,codigo_act,nombre_act,per_ocu,telefono,correoelec,www,cve_ent,entidad,municipio,fecha_alta,raz_norm,nom_norm,dominio,dominio_correo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lote)
                total += len(lote)
                lote = []
        if lote:
            conn.executemany("INSERT INTO est(clee,nom_estab,raz_social,codigo_act,nombre_act,per_ocu,telefono,correoelec,www,cve_ent,entidad,municipio,fecha_alta,raz_norm,nom_norm,dominio,dominio_correo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lote)
            total += len(lote)
        conn.commit()
        if progress:
            progress(i, len(zips) + 1, z.name, total)
    if progress:
        progress(len(zips), len(zips) + 1, "indices", total)
    conn.executescript(POST)
    edicion = max((time.strftime("%Y-%m-%d", time.localtime(z.stat().st_mtime)) for z in zips))
    meta = {"establecimientos": total, "con_sitio_web": con_web, "con_correo_empresa": con_correo,
            "archivos": len(zips), "construido": time.strftime("%Y-%m-%d %H:%M"), "descarga": edicion}
    conn.executemany("INSERT INTO meta VALUES(?,?)", [(k, str(v)) for k, v in meta.items()])
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    final.unlink(missing_ok=True)
    tmp.replace(final)
    if progress:
        progress(len(zips) + 1, len(zips) + 1, "listo", total)
    return meta


def update(base_dir: Path | str = DEFAULT_DIR, progress=None) -> dict:
    """Descarga y construye en un solo paso."""
    download(base_dir, (lambda i, n, name: progress("descarga", i, n, name)) if progress else None)
    return build(base_dir, (lambda i, n, name, total: progress("carga", i, n, f"{name} · {total:,} registros")) if progress else None)


def info(base_dir: Path | str = DEFAULT_DIR) -> dict | None:
    p = db_path(base_dir)
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            return {k: v for k, v in conn.execute("SELECT clave,valor FROM meta")}
        finally:
            conn.close()
    except sqlite3.Error:
        return None


# --------------------------------------------------------------------------- consulta

def _fts_query(target: str, any_token: bool = False) -> str:
    tokens = [t for t in target.split() if len(t) > 1 or t.isdigit()]
    if not tokens:
        return ""
    quoted = [f'"{t}"' for t in tokens]
    return " OR ".join(quoted) if any_token else " ".join(quoted)


def _score(target: str, raz: str, nom: str) -> float:
    """Sin partial_ratio: 'PROFUTURO' no debe dar 100 contra 'PROFUTURO GNP AFORE'."""
    best = 0.0
    for cand in (raz, nom):
        if not cand:
            continue
        if cand == target:
            return 1.0
        best = max(best, fuzz.ratio(target, cand), fuzz.token_sort_ratio(target, cand))
    return best / 100


class DenueLocalSource:
    """Misma interfaz que DenueSource (API), pero leyendo la copia local."""
    name = "DENUE_LOCAL"

    def __init__(self, path: Path | str | None = None, candidates: int = 300):
        self.path = Path(path) if path else db_path()
        ensure_indexes(self.path)
        self.conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.candidates = candidates
        self._cache: dict[str, list] = {}
        self._emails: dict[str, set] = {}

    @staticmethod
    def available(path: Path | str | None = None) -> bool:
        return (Path(path) if path else db_path()).exists()

    def _candidates(self, target: str):
        # 1) igualdad exacta normalizada (usa indice, instantaneo)
        rows = self.conn.execute("SELECT * FROM est WHERE raz_norm=? OR nom_norm=? LIMIT ?",
                                 (target, target, self.candidates)).fetchall()
        if rows:
            return rows
        # 2) todos los terminos; 3) cualquier termino, ordenado por relevancia
        for any_token in (False, True):
            q = _fts_query(target, any_token)
            if not q:
                return []
            try:
                rows = self.conn.execute(
                    "SELECT est.* FROM fts JOIN est ON est.id=fts.rowid WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                    (q, self.candidates)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                return rows
        return []

    def search(self, name: str, limit: int = 20) -> list[dict]:
        target = denue_norm(name)
        if not target:
            return []
        if target in self._cache:
            return self._cache[target][:limit]
        rows = self._candidates(target)
        scored = []
        for r in rows:
            s = _score(target, r["raz_norm"], r["nom_norm"])
            scored.append((round(s, 2), bool(r["dominio"]), bool(r["dominio_correo"]), r, s))
        # A igual parecido, primero el establecimiento que si registro sitio web.
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        out = []
        for _, _, _, r, s in scored:
            out.append({
                "company": r["raz_social"] or r["nom_estab"], "trade_name": r["nom_estab"],
                "website": r["www"], "domain": r["dominio"], "email": r["correoelec"],
                "email_domain": r["dominio_correo"], "phone": r["telefono"],
                "location": f"{r['municipio']}, {r['entidad']}".strip(", "),
                "activity": r["nombre_act"], "staff": r["per_ocu"], "score": s,
                "url": f"denue-local://{r['clee']}", "source": self.name,
                "raz_norm": r["raz_norm"],
            })
        self._cache[target] = out
        return out[:limit]

    async def company(self, name, entity="", limit=20):
        # Se ignora la entidad: el estado del archivo es el del trabajador, no el del patron.
        return self.search(name, limit)

    def _hosts(self, filas) -> dict[str, set]:
        """{dominio exacto tras la @: correos}. Solo correos de empresa."""
        out = defaultdict(set)
        for (campo,) in filas:
            for e in emails_in(campo):
                if _email_domain(e):
                    out[mail_domain(e)].add(e)
        out.pop("", None)
        return out

    def email_domain_votes(self, raz_norm: str) -> dict[str, int]:
        """Dominios (exactos, lo que va tras la @) de los correos de una razon social.
        Cuenta correos distintos: el mismo correo en cuatro sucursales vale uno."""
        if not raz_norm:
            return {}
        h = self._hosts(self.conn.execute(
            "SELECT DISTINCT correoelec FROM est WHERE raz_norm=? AND dominio_correo<>''", (raz_norm,)))
        return {d: len(s) for d, s in h.items()}

    def brand_email_hosts(self, name: str, max_establecimientos: int = 3000) -> dict[str, set]:
        """Correos de empresa de TODOS los establecimientos cuyo nombre contiene todas
        las palabras buscadas: 'BBVA' junta BBVA BANCOMER, GRUPO FINANCIERO BBVA...
        Un nombre demasiado generico ('SERVICIOS') daria miles de empresas ajenas:
        en ese caso no se usa."""
        q = _fts_query(denue_norm(name))
        if not q:
            return {}
        try:
            n = self.conn.execute("SELECT COUNT(*) FROM fts WHERE fts MATCH ?", (q,)).fetchone()[0]
            if not n or n > max_establecimientos:
                return {}
            filas = self.conn.execute(
                "SELECT DISTINCT est.correoelec FROM fts JOIN est ON est.id=fts.rowid "
                "WHERE fts MATCH ? AND est.dominio_correo<>''", (q,)).fetchall()
        except sqlite3.OperationalError:
            return {}
        return dict(self._hosts(filas))

    def domain_emails(self, domain: str) -> set[str]:
        """Correos de DENUE cuyo dominio es exactamente ese, de cualquier sucursal."""
        dom = mail_domain(domain)
        if not dom:
            return set()
        if dom not in self._emails:
            filas = self.conn.execute("SELECT DISTINCT correoelec FROM est WHERE dominio_correo=?",
                                      (root_domain(dom),))
            self._emails[dom] = self._hosts(filas).get(dom, set())
        return self._emails[dom]

    def email_pattern_votes(self, raz_norm: str, domain: str) -> tuple[dict, int]:
        """Patrones que sugieren los correos de ese dominio en DENUE (el formato es
        del dominio, no del establecimiento). Solo cuentan los que tienen forma de
        persona; 'ventas@' o 'info@' no. raz_norm queda por compatibilidad."""
        votes = Counter()
        for local in {e.split("@", 1)[0] for e in self.domain_emails(domain)}:
            pat = pattern_from_local_part(local, allow_single=True)
            if pat:
                votes[pat] += 1
        return dict(votes), sum(votes.values())


def ensure_indexes(path: Path | str) -> None:
    """Bases construidas antes no tenian indice por dominio de correo; sin el,
    cada consulta recorre 6 millones de filas. Se crea una sola vez."""
    p = Path(path)
    if not p.exists():
        return
    conn = sqlite3.connect(p)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_est_domc ON est(dominio_correo)")
        conn.commit()
    except sqlite3.Error:
        pass  # solo lectura o bloqueada: funciona igual, mas lento
    finally:
        conn.close()


@lru_cache(maxsize=1)
def default_source() -> DenueLocalSource | None:
    return DenueLocalSource() if DenueLocalSource.available() else None
