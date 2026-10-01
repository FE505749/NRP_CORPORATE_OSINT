# NRP Corporate OSINT V4

Motor OSINT local para construir contactos corporativos a partir de **Cuenta, NRP, empresa y nombre**, con prioridad absoluta a conocimiento histórico local antes de consultar Internet.

## Qué resuelve

Entrada principal esperada:

`cuenta (id_cuenta), NSS, Entidad, Curp_2, Edad, Edad_origen, nrp, nombre_empresa, blindaje, nombre, a_paterno, a_materno, sexo, instituto`

> NSS y CURP_2 se conservan únicamente como campos locales de unión/salida. **No se usan en búsquedas externas**.

### Orden de resolución

1. **id_cuenta exacto** en históricos → reutiliza correo y completa NRP/empresa/dominio si faltan.
2. **NRP exacto** → reutiliza empresa, dominio y patrón de cualquier cuenta del mismo patrón.
3. **Empresa exacta normalizada** → reutiliza perfil corporativo aunque tenga otro NRP.
4. **Empresa similar** → RapidFuzz con umbral alto (93 por defecto).
5. **DENUE** (copia local o API) → empresa, razón social, teléfono, correo y página web cuando estén disponibles.
6. **Clearbit Autocomplete** → candidato de dominio; nunca se acepta solo, se valida contra la web.
7. **Wikidata** → sitio oficial P856 cuando existe.
8. **Búsqueda pública DDGS/SearXNG** → último recurso.
9. **DNS/MX** → comprueba si el dominio está configurado para recibir correo.
10. **Sitemap + web corporativa + JSON-LD + Common Crawl** → aprende patrón de correo y contactos publicados.

## DENUE local (sin token y sin Internet)

En lugar de consultar la API de INEGI empresa por empresa, el motor puede usar una
**copia local de la descarga masiva del DENUE** (33 archivos CSV, uno por estado;
Estado de México viene en dos partes).

1. **Fuentes → DENUE local → Descargar / actualizar** (o `cli.py denue-update`).
   Descarga ~630 MB a `data/denue/raw/` y construye `data/denue/denue.sqlite`
   con índice de texto completo (FTS5) sobre razón social y nombre comercial.
2. **Buscar → Buscar en DENUE local** (o `cli.py run --denue-local`).
   Resuelve empresa y dominio solo con la copia local: no usa buscadores,
   Clearbit, Wikidata, rastreo web ni DNS.

Reglas del modo local:

- Búsqueda **nacional**: el estado del archivo es el del trabajador, no el del patrón.
- A igual parecido de nombre, gana el establecimiento que sí registró sitio web.
- Sitios de redes sociales (Facebook, Instagram…) y correos gratuitos no cuentan como dominio.
- Los correos que la empresa publicó en DENUE con forma de persona
  (`nombre.apellido@`, `n.apellido@`) se usan como **muestra débil del patrón**
  (confianza ≤ 0.66, por debajo de un patrón aprendido con nombre y correo).
  El resultado se etiqueta `DEDUCIDO_PATRON_DENUE`.
- Empresa encontrada sin sitio web → `SIN_DOMINIO_DENUE`; empresa no encontrada →
  `SIN_COINCIDENCIA_DENUE`. Ambos quedan como no resueltos para revisarlos después.

Prueba rápida: `cli.py denue-buscar "PROFUTURO GNP"`.

## La idea clave

No se investiga a cada persona desde cero.

```text
500,000 personas
       ↓
Cuenta / NRP / empresa
       ↓
conocimiento local
       ↓
NRP únicos y empresas únicas
       ↓
investigar empleador una vez
       ↓
reutilizar dominio y patrón
       ↓
generar correos para todos sus empleados
```

Una empresa puede tener múltiples NRP. Por eso existe `company_profiles`: al aprender el dominio/patrón con un NRP, otro NRP de la misma empresa puede reutilizarlo.

## Compatibilidad

El proyecto usa el mismo núcleo en **macOS y Windows**. Las rutas de la base, cargas, exportaciones y tokens se resuelven desde la carpeta del proyecto, aunque el programa se abra con doble clic o desde otro directorio. No es necesario activar manualmente el entorno virtual.

La carpeta `.venv` no debe copiarse entre sistemas ni incluirse al distribuir el proyecto. Si el instalador detecta un entorno creado por otro sistema, genera automáticamente `.venv-macos` o `.venv-windows` sin borrar el anterior.

Requisitos:

- Python 3.11, 3.12 o 3.13 de 64 bits.
- Acceso a Internet durante la instalación de dependencias.
- Un navegador moderno.

Las versiones directas verificadas están fijadas en `constraints.txt` para que la instalación sea consistente en los dos sistemas.

### Instalación en macOS

1. Instala Python 3.11, 3.12 o 3.13.
2. Descomprime el proyecto.
3. Abre `setup_mac.command` con doble clic, o ejecuta `./setup_mac.sh` desde Terminal.
4. Abre `run_mac.command`.

Si macOS bloquea el primer inicio, usa clic derecho → **Abrir**. Desde Terminal también puedes ejecutar:

```bash
chmod +x setup_mac.command setup_mac.sh run_mac.command run_cli_mac.command
./setup_mac.command
./run_mac.command
```

### Instalación en Windows

1. Instala Python 3.11, 3.12 o 3.13 y habilita **Add Python to PATH**.
2. Descomprime el proyecto en una carpeta local.
3. Ejecuta `setup_windows.bat`.
4. Ejecuta `run_dashboard_windows.bat`.

En ambos sistemas el dashboard usa `http://127.0.0.1:8501`. Si ese puerto está ocupado por otro programa, el lanzador prueba automáticamente hasta el 8510. Si el dashboard ya está activo, simplemente lo abre en el navegador.

Antes de mover una base SQLite entre equipos, cierra el dashboard por completo. No copies únicamente los archivos `-wal` o `-shm`, ni trabajes directamente desde una carpeta de red o sincronización mientras la aplicación está abierta.

## Uso desde la interfaz

### Base objetivo
Sube tu Excel o CSV con las columnas indicadas.

### Históricos
Sube todos los Excel/CSV anteriores que puedan contener:

- Cuenta / id_cuenta
- NRP
- empresa / razón social
- dominio / website
- correo
- patrón de correo
- nombre y apellidos

Puedes subir múltiples archivos. El motor crea una base de conocimiento consolidada.

### Reutilización local
Ejecuta **Buscar coincidencias Cuenta → NRP → Empresa** antes de usar Internet.

### Cruce local

La sección **Cruce** compara la base objetivo con los históricos por `ID_Cuenta`, `NRP` y empresa exacta normalizada. Es un proceso completamente local: no usa INEGI, DENUE, DNS, buscadores ni sitios web.

### Enriquecimiento externo
Procesa solo los empleadores que sigan pendientes.

### Exportación
Genera:

- `data/exports/resultados_contactos.csv`
- `data/exports/parquet/*.parquet`
- `data/exports/empresas_resueltas.csv`
- `data/exports/evidencia.csv`
- `data/exports/historico_consolidado.csv`
- `data/exports/resumen_ejecutivo.xlsx`

## Tokens

Copia `tokens.example.env` a `tokens.env` o usa la sección **Fuentes & Tokens** de la interfaz.

### Recomendado

`INEGI_DENUE_TOKEN=`

### Opcional

`SEARXNG_URL=http://localhost:8080`

Clearbit Autocomplete, Wikidata, Common Crawl y DNS no requieren token en esta implementación.

### Rutas y puerto opcionales

En `tokens.env` puedes cambiar:

```dotenv
DB_PATH=corporate_osint.sqlite
UPLOAD_DIR=data/uploads
OUTPUT_DIR=data/exports
NRP_HOST=127.0.0.1
NRP_PORT=8501
```

Las rutas relativas se interpretan desde la carpeta del proyecto en macOS y Windows. También se aceptan rutas absolutas propias de cada sistema.

## Por qué Clearbit no es la base del motor

La API legacy Name-to-Domain ya no es una dependencia gratuita estable para cuentas nuevas. Se utiliza únicamente **Autocomplete** como fuente auxiliar de candidatos y el dominio debe superar una validación independiente.

## Patrones de correo

Incluye, entre otros:

- `first.paternal` → maria.hernandez@
- `f_paternal` → mhernandez@
- `f.paternal` → m.hernandez@
- `first_paternal` → maria_hernandez@
- `first-paternal` → maria-hernandez@
- `firstpaternal` → mariahernandez@
- `first.last` → maria.garcia@
- `first.paternalmaternal` → maria.hernandezgarcia@
- `given.paternal` → mariaguadalupe.hernandez@

Los candidatos se ordenan según muestras observadas en históricos o correos publicados en la web corporativa.

## Confianza

La confianza del correo combina:

- empresa;
- dominio;
- patrón;
- estado MX.

`PUBLICADO` y `HISTORICO_ID_CUENTA` tienen prioridad sobre cualquier correo deducido.

## Protección de IP / bloqueos

Existe un `NetworkGovernor` central:

- límite global;
- límite por host;
- intervalo mínimo por fuente;
- clasificación de 429/403/503;
- circuit breaker;
- cooldown;
- `Retry-After`;
- eventos de red y salud por fuente;
- los errores no se cachean como resultados vacíos.

No incluye rotación de proxies/IP para evadir controles.

## CLI en Windows

```bat
run_cli_windows.bat init
run_cli_windows.bat ingest-history "C:\datos\historico.xlsx"
run_cli_windows.bat ingest-main "C:\datos\base.xlsx"
run_cli_windows.bat local-match
run_cli_windows.bat status
run_cli_windows.bat run --max-jobs 100 --batch 20
run_cli_windows.bat run --max-jobs 0 --batch 25
run_cli_windows.bat denue-update
run_cli_windows.bat run --denue-local --batch 200
run_cli_windows.bat export --output-dir data\exports
```

## CLI en macOS

```bash
./run_cli_mac.command init
./run_cli_mac.command ingest-history "/Users/usuario/datos/historico.xlsx"
./run_cli_mac.command ingest-main "/Users/usuario/datos/base.xlsx"
./run_cli_mac.command local-match
./run_cli_mac.command status
./run_cli_mac.command run --max-jobs 100 --batch 20
./run_cli_mac.command export --output-dir data/exports
```

## Verificación técnica

Después de instalar, puedes comprobar el entorno sin iniciar el servidor:

```bash
# macOS
.venv/bin/python scripts/run_dashboard.py --check

# Windows
.venv\Scripts\python.exe scripts\run_dashboard.py --check
```

## Git

El proyecto incluye:

- `.gitignore`
- `pyproject.toml`
- pruebas `pytest`
- GitHub Actions
- archivos de tokens fuera de control de versiones
- estructura `data/uploads`, `data/exports`, `logs`

Antes de subir a Git, confirma que `tokens.env`, la base SQLite y archivos reales de clientes no estén versionados.
