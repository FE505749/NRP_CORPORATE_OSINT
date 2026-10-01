import hashlib, re, unicodedata
from functools import lru_cache

# Estas funciones se invocan millones de veces sobre un universo pequeño de
# valores repetidos (miles de empresas, cientos de NRP). Se memorizan sobre la
# cadena ya limpia para que el resultado sea idéntico al del cálculo directo.
_CACHE = 1 << 18

LEGAL_SUFFIXES = re.compile(
    r"\b(SA DE CV|S\.?A\.?\s+DE\s+C\.?V\.?|S DE RL DE CV|S\.? DE R\.?L\.? DE C\.?V\.?|"
    r"SC|S\.?C\.?|AC|A\.?C\.?|SAPI DE CV|SAS|SPR DE RL|SRL|LLC|INC|LTD|LIMITED|PLC)\b",
    re.I
)

def clean_text(v):
    if v is None:
        return ""
    try:
        if v != v:
            return ""
    except Exception:
        pass
    return str(v).strip()

@lru_cache(maxsize=_CACHE)
def _repair(s: str) -> str:
    return re.sub(r"(?<=[A-Za-zÁÉÍÓÚáéíóú])!(?=[A-Za-zÁÉÍÓÚáéíóú])", "Ñ", s)

def repair_legacy_text(s):
    return _repair(clean_text(s))

@lru_cache(maxsize=_CACHE)
def _fold(s: str) -> str:
    s = _repair(s)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def fold(s):
    return _fold(clean_text(s))

@lru_cache(maxsize=_CACHE)
def _norm(s: str) -> str:
    s = _fold(s).upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def norm(s):
    return _norm(clean_text(s))

@lru_cache(maxsize=_CACHE)
def _nrp(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", s).upper()
    if s in {"", "NAN", "NONE", "NULL", "0"}:
        return ""
    return s

def normalize_nrp(v):
    return _nrp(clean_text(v))

@lru_cache(maxsize=_CACHE)
def _strip_suffixes(s: str) -> str:
    s = LEGAL_SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def normalize_company(v):
    return _strip_suffixes(_norm(clean_text(v)))

def canonical_person(first_names="", paternal="", maternal=""):
    maternal = clean_text(maternal)
    if maternal.upper() in {"X","NA","N/A","SIN APELLIDO"}:
        maternal = ""
    return " ".join(x for x in [
        repair_legacy_text(first_names),
        repair_legacy_text(paternal),
        repair_legacy_text(maternal)
    ] if x).strip()

def _sha1(raw: str) -> str:
    # usedforsecurity=False evita ValueError en equipos Windows con FIPS activo.
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()

@lru_cache(maxsize=_CACHE)
def _employer_key(nrp: str, company: str) -> str:
    n = _nrp(nrp)
    if n:
        return "NRP:" + n
    c = _strip_suffixes(_norm(company))
    if c:
        return "COMPANY:" + _sha1(c)[:20]
    return ""

def employer_key(nrp="", company=""):
    return _employer_key(clean_text(nrp), clean_text(company))

@lru_cache(maxsize=_CACHE)
def _person_key(name: str, entity: str) -> str:
    return _sha1(_norm(name) + "|" + _norm(entity))

def person_key(name, entity=""):
    return _person_key(clean_text(name), clean_text(entity))
