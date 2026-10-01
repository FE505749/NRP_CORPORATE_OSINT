"""Lectura tolerante de CSV exportados por Excel en macOS y Windows."""

from pathlib import Path
import csv
import zipfile


def revisar_archivo(path):
    """Devuelve un mensaje si el archivo no se puede leer, o None si está sano.

    Un .xlsx es un ZIP. Cuando la copia se interrumpe, el archivo conserva su
    encabezado pero pierde el directorio final y ninguna herramienta puede
    abrirlo. Conviene detectarlo antes de intentar importar.
    """
    ruta = Path(path)
    if not ruta.exists():
        return f"No se encontró el archivo {ruta.name}."
    if ruta.stat().st_size == 0:
        return f"{ruta.name} está vacío (0 bytes)."
    if ruta.suffix.lower() not in {".xlsx", ".xlsm"}:
        return None
    try:
        with zipfile.ZipFile(ruta) as z:
            nombres = z.namelist()
    except zipfile.BadZipFile:
        return (
            f"**{ruta.name} está incompleto o dañado.** Es un Excel truncado: conserva el "
            f"inicio pero le falta el cierre, así que ni Excel ni el Explorador de Windows "
            f"pueden abrirlo. Vuelve a copiarlo desde su origen y súbelo otra vez."
        )
    except Exception as exc:
        return f"**{ruta.name}** no se pudo abrir: {exc}"
    if not any(n.startswith("xl/worksheets/") for n in nombres):
        return f"**{ruta.name}** no contiene ninguna hoja de cálculo legible."
    return None


def csv_incrustado(headers):
    """Detecta un CSV pegado dentro de una sola columna de Excel.

    Algunos exportadores vuelcan la fila completa —separada por comas— en una
    única celda. Excel muestra una sola columna cuyo encabezado es
    'CUENTA,NSS,ENTIDAD,...'. Devuelve (columnas, dialecto) o None.
    """
    if len(headers) != 1:
        return None
    linea = str(headers[0] or "")
    if not any(sep in linea for sep in ",;\t|"):
        return None
    try:
        dialecto = csv.Sniffer().sniff(linea, delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel
    campos = next(csv.reader([linea], dialecto))
    return (campos, dialecto) if len(campos) > 1 else None


def csv_format(path: str | Path) -> tuple[str, csv.Dialect]:
    source = Path(path)
    sample_bytes = source.read_bytes()[:65536]
    if sample_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        try:
            sample_bytes.decode("utf-8-sig")
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            encoding = "cp1252"

    sample = sample_bytes.decode(encoding, errors="strict")
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return encoding, dialect
