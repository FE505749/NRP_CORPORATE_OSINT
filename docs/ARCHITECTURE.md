# Arquitectura V4

## Principios

1. Employer-first y local-first.
2. `id_cuenta` es el identificador de reutilización más fuerte para contacto histórico.
3. NRP es una clave de empleador útil, pero no se asume una relación 1:1 empresa↔NRP.
4. La empresa canónica permite compartir dominio/patrón entre múltiples NRP.
5. NSS y CURP_2 nunca salen a proveedores externos.
6. Un resultado de red fallido nunca equivale a `NO_RESULTS`.
7. Un dominio de fuente auxiliar requiere validación independiente.
8. Correo deducido siempre se etiqueta como deducido.

## Capas

```text
Streamlit UI
   │
   ├── Upload principal
   ├── Históricos múltiples
   ├── Dashboard / resultados
   └── Tokens / salud de fuentes
   │
SQLite WAL
   │
   ├── people
   ├── historical_contacts
   ├── nrp_map
   ├── company_profiles
   ├── employers
   ├── results
   ├── evidence
   ├── source_health
   └── caches
   │
Local Matcher
   │
   ├── Cuenta exacta
   ├── NRP exacto
   ├── Empresa exacta
   └── Empresa fuzzy >= threshold
   │
Employer Resolver
   │
   ├── DENUE
   ├── Clearbit Autocomplete + verify
   ├── Wikidata
   ├── DDGS / SearXNG
   └── NRP exact search
   │
Domain Intelligence
   │
   ├── DNS/MX
   ├── site verification
   ├── sitemap
   ├── corporate crawl
   ├── JSON-LD
   └── Common Crawl
   │
Email Intelligence
   ├── published
   ├── historical account
   ├── observed pattern
   └── alternatives
```

## Portabilidad

- `scripts/setup_env.py` crea el mismo entorno y las mismas carpetas en macOS y Windows.
- `scripts/run_dashboard.py` inicia Streamlit mediante el intérprete del entorno virtual, abre una instancia existente y selecciona otro puerto local si 8501 está ocupado.
- Los archivos `.command`/`.sh` y `.bat` solo seleccionan el intérprete correcto de cada sistema.
- `nrp_osint.paths` resuelve almacenamiento y configuración desde la raíz del proyecto, independientemente del directorio de trabajo.
- Los datos auxiliares de `tldextract` se guardan en `data/cache`, no en el perfil global del usuario.
- Las variables `DB_PATH`, `UPLOAD_DIR`, `OUTPUT_DIR`, `NRP_HOST` y `NRP_PORT` permiten personalizar cada instalación.

## Escala

Para cientos de miles de personas, CSV es preferible como entrada/salida maestra. Excel puede subirse, pero la exportación completa utiliza CSV/Parquet para evitar límites físicos de Excel.
