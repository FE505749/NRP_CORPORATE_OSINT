# Fuentes

## DENUE
Fuente estructurada mexicana para razón social, establecimiento, teléfono, correo y página de Internet.

- **Copia local** (`nrp_osint/denue_local.py`): descarga masiva de INEGI en `data/denue/denue.sqlite`. Sin token, sin red. Tiene prioridad sobre la API cuando existe.
- **API**: requiere `INEGI_DENUE_TOKEN`. Se usa solo si no hay copia local.

## Clearbit Autocomplete
Sin token en esta implementación. Se considera fuente auxiliar no autoritativa. Un dominio candidato debe ser validado contra el sitio.

## Wikidata
Usa búsqueda de entidades y propiedad de sitio oficial P856.

## Common Crawl
Se usa una vez conocido el dominio, para descubrir páginas históricas relevantes. No debe sobrecargarse el servidor de índice.

## DDGS / SearXNG
Fuentes de descubrimiento. Se colocan al final de la cascada para reducir tráfico y bloqueos.

## Web corporativa
Sitemap, páginas de contacto/equipo/directorio, JSON-LD y correos publicados.
