"""Cruce estrictamente local entre la base objetivo y contactos históricos.

Se resuelve con una sola consulta SQL de solo lectura. La versión anterior
lanzaba entre una y tres consultas por persona, lo que a 500 mil registros
significaba más de un millón de consultas.
"""

import csv

# Orden de prioridad: cuenta exacta, luego NRP, luego empresa normalizada.
_RUTAS = (
    ("ha", "ID_CUENTA_EXACTO"),
    ("hn", "NRP_EXACTO"),
    ("hc", "EMPRESA_EXACTA"),
)

# Filas viejas pueden no tener company_norm precalculado; norm_company() se
# registra en la conexión y COALESCE la evalúa solo cuando hace falta.
_CN_PERSONA = "COALESCE(NULLIF(p.company_norm,''),norm_company(p.company_input))"


def _mejor_id(columna, valor):
    """Id de la fila ganadora para una clave. Con el índice compuesto
    (clave, verified DESC, id DESC) es una sola búsqueda, no un recorrido."""
    return (
        f"(SELECT h.id FROM historical_contacts h WHERE h.{columna}={valor}"
        f" ORDER BY h.verified DESC,h.id DESC LIMIT 1)"
    )


def _cascada(campo):
    ramas = " ".join(f"WHEN b.id_{a} IS NOT NULL THEN {a}.{campo}" for a, _ in _RUTAS)
    return f"CASE {ramas} ELSE '' END"


def _consulta(orden="ORDER BY b.row_id"):
    tipo = " ".join(f"WHEN b.id_{a} IS NOT NULL THEN '{r}'" for a, r in _RUTAS)
    hay = " OR ".join(f"b.id_{a} IS NOT NULL" for a, _ in _RUTAS)
    # Cada nivel solo se evalúa si el anterior no encontró nada.
    return f"""
WITH pa AS (
  SELECT p.row_id,p.account_id,p.nrp,p.company_input,p.full_name,
         {_CN_PERSONA} AS cn,
         CASE WHEN p.account_id<>'' THEN {_mejor_id('account_id','p.account_id')} END AS id_ha
  FROM people p
),
pn AS (
  SELECT pa.*,CASE WHEN pa.id_ha IS NULL AND pa.nrp<>''
              THEN {_mejor_id('nrp','pa.nrp')} END AS id_hn
  FROM pa
),
b AS (
  SELECT pn.*,CASE WHEN pn.id_ha IS NULL AND pn.id_hn IS NULL AND pn.cn<>''
              THEN {_mejor_id('company_norm','pn.cn')} END AS id_hc
  FROM pn
)
SELECT b.account_id     AS "ID_Cuenta",
       b.nrp            AS "NRP_base",
       b.company_input  AS "Empresa_base",
       b.full_name      AS "Nombre",
       CASE WHEN {hay} THEN 'Sí' ELSE 'No' END AS "Coincidencia",
       CASE {tipo} ELSE 'SIN_COINCIDENCIA' END AS "Tipo_coincidencia",
       {_cascada('account_id')}   AS "ID_Cuenta_histórico",
       {_cascada('nrp')}          AS "NRP_histórico",
       {_cascada('company_name')} AS "Empresa_histórica",
       {_cascada('email')}        AS "Correo_histórico",
       {_cascada('domain')}       AS "Dominio_histórico",
       {_cascada('source_file')}  AS "Archivo_origen"
FROM b
LEFT JOIN historical_contacts ha ON ha.id=b.id_ha
LEFT JOIN historical_contacts hn ON hn.id=b.id_hn
LEFT JOIN historical_contacts hc ON hc.id=b.id_hc
{orden}
"""


def build_local_crossmatch(db, limit=None, offset=0):
    """Una fila por persona. Sin red y sin escrituras en la base."""
    sql = _consulta()
    if limit is not None:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return [dict(r) for r in db.conn.execute(sql)]


def crossmatch_counts(db):
    """Totales sin materializar el resultado completo."""
    sql = f'SELECT "Coincidencia" c,COUNT(*) n FROM ({_consulta(orden="")}) GROUP BY c'
    filas = {r[0]: r[1] for r in db.conn.execute(sql)}
    con = filas.get("Sí", 0)
    sin = filas.get("No", 0)
    return {"total": con + sin, "con_coincidencia": con, "sin_coincidencia": sin}


def write_crossmatch_csv(db, path, chunk=50000):
    """Exporta el cruce completo en streaming, sin cargarlo en memoria."""
    cursor = db.conn.execute(_consulta())
    columnas = [d[0] for d in cursor.description]
    escritas = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columnas)
        while True:
            filas = cursor.fetchmany(chunk)
            if not filas:
                break
            writer.writerows(filas)
            escritas += len(filas)
    return {"filas": escritas, "ruta": str(path)}
