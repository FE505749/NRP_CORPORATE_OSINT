# NRP Intelligence

Motor local para **estimar el correo corporativo de una persona** a partir de su nombre y su empresa.
Busca la empresa en copias locales de **INEGI DENUE** y **Tukan**, saca el dominio de **correos reales**
y aprende el formato que usa cada empresa. Todo corre en el equipo: sin Internet y sin que NSS o CURP salgan de él.

---

## Qué se hizo en esta rama

| Cambio | Qué resuelve | Dónde |
|---|---|---|
| **Tukan como segunda fuente** | Base precargada de contactos por NRP. Se consulta después de DENUE y siempre gana el registro **ACTIVO** sobre CADUCO o REVOCADO. | `nrp_osint/tukan_local.py` |
| **Dominio = lo que va después de la @** | El dominio sale de **correos reales**, nunca del sitio web si hay correos, y **sin recortarlo**: `luis.felipe@bancomer.com.mx` → `bancomer.com.mx`. Ej.: la web de Profuturo es `profuturo.mx`, pero sus correos son `@profuturo.com.mx`. | `nrp_osint/domain.py` (`mail_domain`), `nrp_osint/employer.py` (paso D3) |
| **Correos de todo el grupo** | Se juntan los correos de todas las sucursales y razones sociales que contienen el nombre buscado ("BBVA" junta BBVA BANCOMER, GRUPO FINANCIERO BBVA…). Un nombre demasiado genérico no se usa. | `nrp_osint/denue_local.py` (`brand_email_hosts`) |
| **Detección de "nombres juntos"** | Si una empresa escribe todos los nombres juntos (`victorhugo.olivares@bbva.com`), el correo de Francisco Javier sale `franciscojavier.espinoza@`. Regla: al menos 3 casos y el 20 % de los nombre.apellido. | `nrp_osint/emailintel.py` (`nombres_juntos`) |
| **Búsqueda de una persona** | Formulario: primer y segundo nombre, apellidos y empresa. Muestra el correo, el desglose de formatos, el **veredicto** en palabras y los correos reales (con su estatus en Tukan). | `nrp_osint/persona.py` |
| **Búsqueda con un Excel** | Cada fila se busca **exactamente igual** que una persona. Devuelve un Excel con las pestañas **Correos** y **Detalle**. | `nrp_osint/lote.py` |
| **Interfaz nueva sin Streamlit** | Servidor **FastAPI + uvicorn** y página HTML/CSS/JS con el diseño de *skill-diagramas-visio* (navy y dorado Profuturo, Mulish, tema claro/oscuro). | `api/main.py`, `front/` |

Se quitaron `dashboard.py`, `.streamlit/` y Streamlit de las dependencias.

---

## Cómo arrancar

1. Instala (una vez): `setup_windows.bat` (o `setup_mac.command`).
2. Arranca: **`run_dashboard_windows.bat`** (o `run_mac.command`).
3. Se abre **http://127.0.0.1:8501**. Si el puerto está ocupado prueba del 8502 al 8510.

A mano: `.venv-windows\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8501`

> Con el proxy corporativo, las consultas a `127.0.0.1` pueden salir a la red y volver con 403.
> El lanzador ya lo evita; si pruebas la API desde otro programa, desactiva el proxy para localhost.

Requisitos: Python 3.11–3.13 de 64 bits. Las copias locales de DENUE y Tukan deben estar construidas (ver *Fuentes locales*).

---

## Cómo se construye un correo

Ejemplo: **Francisco Javier Espinoza Mendieta**, empresa **BBVA**.

1. **Empresa.** Se busca en DENUE (nombre normalizado, sin "SA DE CV") y luego en Tukan (por NRP o por nombre).
2. **Dominio.** Votan los **correos reales** de la empresa: los de todas sus sucursales en DENUE más los de Tukan (un contacto ACTIVO vale doble).
   - Un dominio ajeno con un solo correo (errata, socio, otro país) no basta.
   - El dominio de un despacho contable que aparece en muchas empresas no cuenta.
   - Solo si no hay ningún correo de empresa se usa el **sitio web** (confianza 0.85; 0.92 si hay correos con ese dominio).
3. **Formato.** Se cuentan todos los correos reales del dominio exacto, en DENUE y Tukan:
   `nombre.apellido`, `nombresjuntos.apellido`, `inicial.apellido`, `inicialapellido`…
   Los genéricos (`ventas@`, `info@`) no cuentan.
4. **Correo.** Se arma con el formato ganador → `franciscojavier.espinoza@bbva.com`; los demás formatos quedan como alternativas.
5. **Veredicto.** Explica en palabras por qué salió ese correo (cuántos correos lo respaldan y de qué tipo).

| Tipo de resultado | Significa |
|---|---|
| `DEDUCIDO_PATRON_DENUE_TUKAN` / `_DENUE` / `_TUKAN` | Formato aprendido de correos reales de esas fuentes |
| `CONFIRMADO_TUKAN` | El correo existe tal cual en Tukan |
| `DEDUCIDO_SIN_PATRON` | Hay dominio pero ningún correo de persona: se usó nombre.apellido (confianza baja) |
| `SIN_DOMINIO_DENUE` / `SIN_DOMINIO_TUKAN` / `SIN_COINCIDENCIA_DENUE` | No se pudo estimar el correo |

---

## Buscar con un Excel

**Entrada** (hoja activa, títulos en la primera fila; también CSV):

| cuenta | primer_nombre | segundo_nombre | primer_apellido | segundo_apellido | empresa |
|---|---|---|---|---|---|

Obligatorias: `cuenta`, `primer_nombre`, `primer_apellido`, `empresa`. La página ofrece la plantilla vacía.
Las filas incompletas salen en el resultado con su motivo. Cada empresa distinta se busca una sola vez.

**Salida** (`data/exports/correos_estimados_AAAAMMDD_HHMMSS.xlsx`):

- **Correos**: Cuenta, Correo estimado, Patrón, Dominio.
- **Detalle**: datos de entrada, empresa encontrada, tipo, confianza del correo y del dominio, sitio web,
  alternativas, correos reales del dominio, desglose de formatos, ejemplos, veredicto y cómo se construyó.

---

## Fuentes locales

### INEGI DENUE
Descarga masiva de INEGI (33 archivos, ~630 MB) en `data/denue/denue.sqlite`, con índice de texto completo.
Construir o actualizar: **Fuentes → Descargar o actualizar DENUE**, o `cli.py denue-update`.

### Tukan
`Tukan.xlsx` (NRP, EMPRESA, ESTATUS, EMAIL) precargado en `data/tukan/tukan.sqlite`.
Precargar de nuevo: **Fuentes → Volver a precargar Tukan**, o `cli.py tukan-update [ruta]`
(por defecto toma `Tukan.xlsx` junto al proyecto). Prueba rápida: `cli.py tukan-buscar 01010279105`.

- ACTIVO primero en contacto y dominio.
- Correos gratuitos (incluidas erratas como `hotmai.com`) y de proveedores de Internet: contacto, nunca dominio.
- Un dominio con correos de más de 3 empresas que no se le parecen se trata como despacho contable.

---

## La página

| Sección | Qué hace |
|---|---|
| **Buscar** | Búsqueda de una persona y búsqueda con un Excel |
| **Resumen** | Conteos, cobertura y salud de fuentes de la base cargada |
| **Resultados** | Consulta y exporta la base cargada |
| **Histórico** | Archivos históricos cargados y carga de nuevos |
| **Fuentes** | Estado de DENUE y Tukan, credenciales y salud de fuentes externas |
| **Auditoría** | Evidencia de cada decisión y eventos de red |

La columna izquierda muestra qué fuentes locales están disponibles y lo guardado en la base.

---

## Estructura

```text
api/main.py            Servidor FastAPI: entrega la página y las funciones en /api
front/                 Página: index.html, styles/ (tokens.css = paleta Profuturo), js/app.js, img/, fonts/
nrp_osint/
  persona.py           Búsqueda de personas (una o muchas) + veredicto y desglose
  lote.py              Lectura del Excel y exportación con pestañas Correos y Detalle
  employer.py          Resolución de la empresa: DENUE → Tukan → dominio por correos → formato
  denue_local.py       Copia local de DENUE y búsqueda en todo el grupo
  tukan_local.py       Copia local de Tukan
  emailintel.py        Formatos de correo, nombres juntos y confianza
  domain.py            Dominio exacto tras la @ (mail_domain)
  pipeline.py, db.py, … Motor y base SQLite (WAL)
scripts/run_dashboard.py  Lanzador (uvicorn + navegador)
tests/                 Pruebas pytest
```

---

## Línea de comandos

```bat
run_cli_windows.bat denue-update
run_cli_windows.bat tukan-update
run_cli_windows.bat denue-buscar "PROFUTURO"
run_cli_windows.bat tukan-buscar "BBVA"
run_cli_windows.bat ingest-main "C:\datos\base.xlsx"
run_cli_windows.bat run --denue-local --batch 200
run_cli_windows.bat run --max-jobs 100 --batch 20     (búsqueda pública: Wikidata, web, DNS)
run_cli_windows.bat export --output-dir data\exports
```

En macOS, lo mismo con `./run_cli_mac.command`.

---

## Pruebas

```bat
.venv-windows\Scripts\python.exe -m pytest
.venv-windows\Scripts\python.exe scripts\run_dashboard.py --check
```

Las pruebas usan bases sintéticas; no necesitan DENUE ni Tukan reales.

---

## Configuración

Copia `tokens.example.env` a `tokens.env`. Opcionales: `INEGI_DENUE_TOKEN` (solo para la API; la copia
local no lo necesita), `SEARXNG_URL`, `DB_PATH`, `UPLOAD_DIR`, `OUTPUT_DIR`, `DENUE_LOCAL_PATH`,
`TUKAN_LOCAL_PATH`, `NRP_HOST`, `NRP_PORT`. Las rutas relativas se resuelven desde la carpeta del proyecto.

---

## Privacidad y Git

- NSS y CURP_2 se guardan solo en local y nunca se usan para buscar.
- El servidor escucha únicamente en `127.0.0.1`.
- No se versionan: `tokens.env`, las bases `.sqlite`, `data/denue/`, `data/tukan/`, `Tukan.xlsx`,
  `data/uploads/`, `data/exports/` ni `logs/`.

---

## Pendiente

- **Resumen, Resultados e Histórico** siguen funcionando sobre la base cargada, pero la página ya no la carga
  (la tarjeta "Subir una base" se reemplazó por "Buscar con un Excel"). Se puede cargar con `cli.py ingest-main`.
- La **búsqueda pública** (Wikidata, web corporativa, DNS) solo está disponible por línea de comandos.
- En Tukan, nombres cortos ("BBVA") no siempre coinciden con razones sociales largas; sus correos sí se usan para el formato.
- El histórico cargado antes de esta rama guardó el dominio recortado a su raíz; lo nuevo usa el dominio exacto.
