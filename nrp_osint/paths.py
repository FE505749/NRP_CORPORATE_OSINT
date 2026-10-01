"""Rutas portables para ejecutar el proyecto desde macOS o Windows."""

from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _limit_utf8(value: str, max_bytes: int) -> str:
    while value and len(value.encode("utf-8")) > max_bytes:
        value = value[:-1]
    return value


def project_path(value: str | os.PathLike[str]) -> Path:
    """Resuelve rutas relativas contra el proyecto, no contra el shell actual."""
    expanded = os.path.expandvars(os.fspath(value))
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve(strict=False)


def env_path(name: str, default: str) -> Path:
    return project_path(os.getenv(name, default))


def safe_upload_name(filename: str, fallback: str = "archivo") -> str:
    """Obtiene el nombre final tanto de rutas POSIX como de rutas Windows."""
    name = Path(str(filename).replace("\\", "/")).name
    invalid = {'\x7f', '/', '\\', '<', '>', ':', '"', '|', '?', '*'}
    name = "".join(char for char in name if char >= " " and char not in invalid).strip().rstrip(". ")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if name and name.split(".", 1)[0].upper() in reserved:
        name = "_" + name
    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    suffix = _limit_utf8(suffix, 24)
    stem = _limit_utf8(stem, 180 - len(suffix.encode("utf-8")))
    name = stem + suffix
    return name or fallback
