from pathlib import Path
import asyncio

import pandas as pd
import streamlit as st

from nrp_osint.config import load_tokens, Settings, tokens
from nrp_osint.paths import safe_upload_name
from nrp_osint.db import DB
from nrp_osint.io_stream import ingest
from nrp_osint.historical import import_history, rebuild_knowledge
from nrp_osint.local_reuse import apply_local_reuse
from nrp_osint.pipeline import Pipeline
from nrp_osint.exporter import export_all, RESULT_SQL
from nrp_osint.stats import counts
from nrp_osint.crossmatch import build_local_crossmatch, crossmatch_counts, write_crossmatch_csv
from nrp_osint.tabular import revisar_archivo
from nrp_osint import denue_local


st.set_page_config(
    page_title="NRP Intelligence",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_tokens()
settings = Settings.from_env()
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
Path(settings.output_dir).mkdir(parents=True, exist_ok=True)


@st.cache_resource(show_spinner=False)
def open_database(path: str):
    """Una sola conexión para toda la sesión. Sin esto Streamlit abre una nueva
    en cada interacción y nunca las cierra."""
    return DB(path)


db = open_database(settings.db_path)


def save_upload(uploaded, prefix=""):
    path = Path(settings.upload_dir) / (prefix + safe_upload_name(uploaded.name))
    path.write_bytes(uploaded.getbuffer())
    return path


def pending_uploads(files, marker):
    """Archivos aún no importados. Evita reimportar en cada rerun de Streamlit."""
    done = st.session_state.setdefault(marker, set())
    return [f for f in (files or []) if (f.name, f.size) not in done], done


def remember(marker, message, level="ok"):
    st.session_state[marker] = (level, message)


def show_remembered(marker):
    """Los mensajes viven en session_state para sobrevivir al cambio de pestaña."""
    entry = st.session_state.get(marker)
    if not entry:
        return
    level, message = entry
    {"ok": st.success, "warn": st.warning, "error": st.error}[level](message)

def resolver_archivo(fuente):
    """Lo que pasa al subir un archivo: histórico local y luego la copia local de
    DENUE, solo para los empleadores de ese archivo. No sale a Internet."""
    pasos = []
    if counts(db)["historicos"]:
        with st.spinner("Cruzando con el histórico local..."):
            reuso = apply_local_reuse(db, settings)
        pasos.append(f"histórico: {reuso['matched']:,} coincidencias, "
                     f"{reuso['direct_account_emails']:,} con correo directo por cuenta")
    if denue_local.info(Path(settings.denue_local_path).parent):
        pendientes = db.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('PENDING','RETRY') AND employer_key IN "
            "(SELECT employer_key FROM people WHERE ingest_source=?)", (fuente,)).fetchone()[0]
        barra = st.progress(0, text=f"DENUE local: 0 de {pendientes:,} empleadores")

        def avance(n, jobs, results):
            barra.progress(min(1, n / max(1, pendientes)), text=f"DENUE local: {n:,} de {pendientes:,} empleadores")

        local = Settings.from_env()
        local.denue_local_only = True
        pipe = Pipeline(db, local, tokens())
        hechos = asyncio.run(pipe.run(max_jobs=0, batch=200, progress=avance, source=fuente)) if pendientes else 0
        reutilizados = pipe.materialize_known(fuente)
        barra.empty()
        pasos.append(f"DENUE local: {hechos:,} empleadores buscados, {reutilizados:,} ya conocidos reutilizados")
    else:
        pasos.append("DENUE local no está descargado (Fuentes → DENUE local), se omitió")
    total, con_correo = db.conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN r.email<>'' THEN 1 ELSE 0 END) FROM people p "
        "LEFT JOIN results r USING(row_id) WHERE p.ingest_source=?", (fuente,)).fetchone()
    st.session_state["cruce_resumen"] = crossmatch_counts(db)
    return total or 0, con_correo or 0, pasos


st.markdown(
    """
<style>
:root {
  --ink: #202426;
  --muted: #747b7e;
  --line: #dfe3e4;
  --surface: #ffffff;
  --canvas: #eef1f1;
  --nav: #191c1d;
  --nav-soft: #25292a;
  --teal: #2c9a91;
  --teal-dark: #237c75;
  --teal-soft: #e8f5f3;
  --amber: #9b6a12;
  --amber-soft: #fff6df;
}

html, body, [class*="css"], [data-testid="stAppViewContainer"] {
  font-family: "Inter", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.stApp { background: var(--canvas); color: var(--ink); }
.block-container { max-width: 1380px; padding: 2rem 2.6rem 4rem; }
header[data-testid="stHeader"] { background: var(--canvas); }
#MainMenu, footer { visibility: hidden; }

[data-testid="stSidebar"] {
  background: var(--nav);
  border-right: 0;
  min-width: 232px;
  box-shadow: 8px 0 28px rgba(20, 24, 25, .08);
}
[data-testid="stSidebar"] > div { padding-top: 1.45rem; }
[data-testid="stSidebar"] * { color: #b9bfc1; }
[data-testid="stSidebar"] .stRadio > label {
  color: #747c7e !important; font-size: .66rem; font-weight: 600;
  letter-spacing: .14em; text-transform: uppercase; margin: 1.15rem 0 .5rem;
}
[data-testid="stSidebar"] .stRadio [role="radiogroup"] { gap: .42rem; }
[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"] {
  min-height: 2.65rem; padding: .7rem .9rem; border-radius: 5px; transition: .15s ease;
  background: transparent;
}
[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"]:hover { background: var(--nav-soft); }
[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"] > div:first-child { display: none; }
[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"]:has(input:checked) {
  background: var(--teal); box-shadow: none;
}
[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"]:has(input:checked) * { color:#ffffff !important; }
[data-testid="stSidebar"] hr { border-color: #303536; margin-top:1.4rem; }

.brand { padding:.28rem .52rem 1.45rem; border-bottom:1px solid #303536; }
.brand-name { color:#f6f7f7; font-size:1.08rem; font-weight:700; letter-spacing:.015em; }
.brand-name span { color:var(--teal); }
.brand-version { color:#747c7e; font-size:.64rem; font-weight:600; letter-spacing:.12em; margin-top:.25rem; }
.privacy-note { color:#858d8f; font-size:.7rem; line-height:1.55; padding:.8rem .5rem; }
.privacy-note b { color:#d7dbdc !important; font-weight:600; }

h1, h2, h3 { color: var(--ink); letter-spacing: -.035em; }
h1 { font-size: 1.62rem !important; font-weight:700 !important; margin: .08rem 0 .18rem !important; }
h2 { font-size: 1.28rem !important; font-weight:600 !important; }
h3 { font-size: 1rem !important; font-weight:600 !important; margin-top:1.65rem !important; }
.page-header {
  display:flex; align-items:flex-end; justify-content:space-between; gap:2rem;
  padding:.05rem 0 1.3rem; margin-bottom:1.45rem; border-bottom:1px solid #d7dcdd;
}
.eyebrow { color:var(--teal); font-size:.66rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
.page-subtitle { color:var(--muted); font-size:.78rem; line-height:1.55; max-width:590px; text-align:right; }

.metric-card {
  position:relative; overflow:hidden; background:var(--surface); border:1px solid #e5e8e9; border-radius:8px;
  padding:1.15rem 1.18rem 1.05rem; min-height:124px; box-shadow:0 8px 22px rgba(31, 42, 44, .035);
}
.metric-card:before { content:""; position:absolute; left:0; top:0; width:100%; height:3px; background:var(--teal); }
.metric-top { display:flex; align-items:center; justify-content:space-between; }
.metric-label { color:var(--muted); font-size:.74rem; font-weight:500; }
.metric-value { color:var(--ink); font-size:1.65rem; font-weight:700; margin-top:.82rem; letter-spacing:-.045em; }
.metric-foot { color:#9aa0a2; font-size:.66rem; margin-top:.28rem; }

.panel {
  background:var(--surface); border:1px solid #e5e8e9; border-radius:8px;
  padding:1.3rem; margin:1.05rem 0; box-shadow:0 8px 22px rgba(31, 42, 44, .035);
}
.panel-title { font-size:.88rem; font-weight:600; color:var(--ink); margin-bottom:.3rem; }
.panel-copy { color:var(--muted); font-size:.72rem; margin-bottom:1.15rem; }
.coverage-row { display:grid; grid-template-columns:130px 1fr 54px; align-items:center; gap:.8rem; margin:.9rem 0; font-size:.77rem; }
.coverage-track { height:7px; background:#e8ecec; border-radius:8px; overflow:hidden; }
.coverage-fill { height:100%; background:var(--teal); border-radius:8px; }
.coverage-value { color:var(--teal); font-weight:700; text-align:right; }
.source-row { display:grid; grid-template-columns:1fr auto; align-items:center; padding:.68rem 0; border-bottom:1px solid #eef0f0; font-size:.75rem; }
.source-row:last-child { border:0; }
.status-ok, .status-warn, .status-off { padding:.25rem .52rem; border-radius:999px; font-size:.67rem; font-weight:700; }
.status-ok { color:var(--teal-dark); background:var(--teal-soft); }
.status-warn { color:var(--amber); background:var(--amber-soft); }
.status-off { color:#667085; background:#f2f4f7; }

.callout { border:1px solid #cfe7e4; background:#f1faf8; border-radius:7px; padding:.88rem 1rem; color:#365f5b; font-size:.77rem; margin:.75rem 0 1.25rem; }
.warning { border-color:#f0ddb2; background:var(--amber-soft); color:#79520a; }

.stored { padding:.9rem .55rem .2rem; }
.stored-title { color:#747c7e !important; font-size:.62rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; margin-bottom:.55rem; }
.stored-row { display:flex; justify-content:space-between; align-items:baseline; padding:.24rem 0; font-size:.74rem; }
.stored-row span:first-child { color:#858d8f !important; }
.stored-row b { color:#e6e9ea !important; font-weight:700; }
.stored-row.empty b { color:#c98a2e !important; }

[data-testid="stFileUploader"] { background:white; border:1px dashed #cbd2d3; border-radius:8px; padding:.35rem; }
[data-testid="stFileUploaderDropzone"] { background:#f9fafa; }
[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:7px; overflow:hidden; }
.stButton > button, .stDownloadButton > button {
  border-radius:5px; min-height:2.65rem; font-weight:600; border:1px solid var(--teal);
  background:var(--teal); color:white; box-shadow:none; transition:.15s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover { background:var(--teal-dark); border-color:var(--teal-dark); color:white; }
.stButton > button:disabled { background:#c9d2d2; border-color:#c9d2d2; color:#ffffff; }
.stTextInput input, .stNumberInput input { border-radius:6px; background:#fff; border-color:#d6dcdd; }
[data-testid="stMetric"] { background:white; border:1px solid #e5e8e9; border-radius:7px; padding:1rem; box-shadow:0 8px 22px rgba(31,42,44,.03); }
[data-testid="stMetricLabel"] { color:var(--muted); }
[data-testid="stMetricValue"] { color:var(--ink); font-weight:700; }
[data-baseweb="select"] > div, [data-baseweb="input"] > div { border-radius:6px; }
[data-testid="stCheckbox"] label, [data-testid="stFileUploader"] label, .stTextInput label, .stNumberInput label { color:#4b5355; }

@media (max-width: 900px) {
  .block-container { padding:1.3rem 1rem 3rem; }
  .coverage-row { grid-template-columns:100px 1fr 48px; }
  .page-header { display:block; }
  .page-subtitle { text-align:left; margin-top:.55rem; }
}
</style>
""",
    unsafe_allow_html=True,
)


def page_header(kicker, title, subtitle):
    st.markdown(
        f'<div class="page-header"><div><div class="eyebrow">{kicker}</div><h1>{title}</h1></div>'
        f'<div class="page-subtitle">{subtitle}</div></div>',
        unsafe_allow_html=True,
    )


def metric_card(label, value, icon, foot):
    st.markdown(
        f'<div class="metric-card"><div class="metric-top"><div class="metric-label">{label}</div>'
        f'</div><div class="metric-value">{value:,}</div>'
        f'<div class="metric-foot">{foot}</div></div>',
        unsafe_allow_html=True,
    )


with st.sidebar:
    st.markdown(
        '<div class="brand"><div class="brand-name"><span>NRP</span> Intelligence</div>'
        '<div class="brand-version">CORPORATE OSINT · V4</div></div>',
        unsafe_allow_html=True,
    )
    page = st.radio(
        "Navegación",
        ["Buscar", "Resumen", "Resultados", "Histórico cargado", "Fuentes", "Auditoría"],
        label_visibility="visible",
    )
    st.markdown("---")
    st.markdown(
        '<div class="privacy-note"><b>Privacidad local</b><br>NSS y CURP_2 permanecen en este equipo y no forman parte de las consultas externas.</div>',
        unsafe_allow_html=True,
    )

c = counts(db)

if page.endswith("Resumen"):
    page_header("Panel operativo", "Centro de inteligencia", "Visión general del proceso de resolución corporativa y la calidad de sus fuentes.")
    cols = st.columns(4)
    cards = [
        ("Personas", c["personas"], "", "Registros en la base objetivo"),
        ("Empresas", c["empresas_unicas"], "", "Perfiles corporativos conocidos"),
        ("Contactos resueltos", c["contactos_resueltos"], "", "Correos disponibles"),
        ("Jobs pendientes", c["jobs_pendientes"], "", "En cola o por reintentar"),
    ]
    for col, card in zip(cols, cards):
        with col:
            metric_card(*card)

    left, right = st.columns([1.18, 1])
    coverage = (c["contactos_resueltos"] / c["personas"] * 100) if c["personas"] else 0
    company_coverage = (c["jobs_done"] / max(1, c["jobs_done"] + c["jobs_pendientes"]) * 100)
    with left:
        st.markdown('<div class="panel"><div class="panel-title">Cobertura de resolución</div><div class="panel-copy">Avance sobre la base y los empleadores registrados.</div>', unsafe_allow_html=True)
        for label, value in [("Contactos", coverage), ("Empleadores", company_coverage), ("Cuenta exacta", (c["resueltos_id_cuenta"] / max(1, c["personas"]) * 100))]:
            st.markdown(f'<div class="coverage-row"><span>{label}</span><div class="coverage-track"><div class="coverage-fill" style="width:{min(100,value):.1f}%"></div></div><span class="coverage-value">{value:.1f}%</span></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        health = pd.read_sql_query("SELECT source_name,state,last_status FROM source_health ORDER BY source_name LIMIT 5", db.conn)
        st.markdown('<div class="panel"><div class="panel-title">Salud de fuentes</div><div class="panel-copy">Disponibilidad reciente de servicios externos.</div>', unsafe_allow_html=True)
        if health.empty:
            st.markdown('<div class="source-row"><span>Sin actividad registrada</span><span class="status-off">En espera</span></div>', unsafe_allow_html=True)
        else:
            for _, row in health.iterrows():
                state = str(row["state"] or "CLOSED")
                css, label = ("status-ok", "Disponible") if state == "CLOSED" else ("status-warn", "Pausada")
                st.markdown(f'<div class="source-row"><span>{row["source_name"]}</span><span class="{css}">{label}</span></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("### Ruta de resolución")
    st.markdown('<div class="callout"><b>Cuenta exacta</b> → NRP → empresa normalizada → DENUE (copia local) → dominio validado → MX → patrón de correo. La red solo interviene cuando el conocimiento local no basta.</div>', unsafe_allow_html=True)

elif page.endswith("Buscar"):
    page_header(
        "Búsqueda de contactos",
        "Sube tu base y busca",
        "Al subir tu base se busca sola: primero el histórico y luego la copia local de INEGI DENUE.",
    )

    disponible = counts(db)
    if not disponible["historicos"]:
        st.markdown(
            '<div class="callout warning"><b>No hay histórico cargado.</b> '
            'Revisa la sección <b>Histórico cargado</b> para incorporarlo.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="callout"><b>Histórico disponible:</b> {disponible["historicos"]:,} registros · '
            f'{disponible["empresas_unicas"]:,} empresas. Se buscará por '
            '<b>Cuenta → NRP → Empresa</b>, en ese orden.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("### 1. Tu base a cruzar")
    f = st.file_uploader(
        "Excel o CSV con la población que quieres resolver",
        type=["xlsx", "xlsm", "csv"],
        key="main",
        help="Mínimo: una columna de cuenta (cuenta / id_cuenta / IDCUENTA) y una de nombre.",
    )
    nuevos, done = pending_uploads([f] if f else [], "base_importada")
    if nuevos:
        subido = nuevos[0]
        done.add((subido.name, subido.size))
        ruta = save_upload(subido)
        problema = revisar_archivo(ruta)
        if problema:
            remember("base_msg", problema, "error")
        else:
            try:
                with st.spinner("Importando tu base..."):
                    info = ingest(db, ruta)
            except ValueError as exc:
                remember("base_msg", f"**{subido.name}** no se pudo importar: {exc}", "error")
            except Exception as exc:
                remember(
                    "base_msg",
                    f"**{subido.name}** no se pudo leer: {type(exc).__name__}: {exc}",
                    "error",
                )
            else:
                st.session_state["base_mapping"] = info["mapping"]
                total, con_correo, pasos = resolver_archivo(ruta.name)
                remember(
                    "base_msg",
                    f"**{subido.name}**: {info['rows']:,} registros cargados · "
                    f"**{con_correo:,} de {total:,} con correo**.\n\n" + "\n".join(f"- {x}" for x in pasos),
                )
    show_remembered("base_msg")
    if st.session_state.get("base_mapping"):
        with st.expander("Columnas que reconoció de tu base"):
            st.json(st.session_state["base_mapping"])

    st.markdown("### 2. Volver a buscar o ampliar")
    denue_info = denue_local.info(Path(settings.denue_local_path).parent)
    izq, centro, der = st.columns(3)

    with izq:
        st.markdown(
            '<div class="panel"><div class="panel-title">🔒 LOCAL</div>'
            '<div class="panel-copy">Cruza tu base contra el histórico ya cargado. '
            'Cuenta exacta → NRP → empresa. No sale a Internet.</div></div>',
            unsafe_allow_html=True,
        )
        buscar_local = st.button("Buscar LOCAL", use_container_width=True, type="primary")

    with centro:
        if denue_info:
            detalle = (f'{int(denue_info["establecimientos"]):,} establecimientos · '
                       f'{int(denue_info["con_sitio_web"]):,} con sitio web. Sin Internet.')
        else:
            detalle = 'Aún no descargado. Ve a <b>Fuentes</b> → DENUE local.'
        st.markdown(
            '<div class="panel"><div class="panel-title">🗂 DENUE LOCAL</div>'
            f'<div class="panel-copy">Resuelve dominio con la copia de INEGI guardada en el equipo. {detalle}</div></div>',
            unsafe_allow_html=True,
        )
        max_denue = st.number_input("Máximo de empleadores (0 = todos)", min_value=0, value=0, step=500, key="max_denue")
        buscar_denue = st.button("Buscar en DENUE local", use_container_width=True, disabled=not denue_info)

    with der:
        st.markdown(
            '<div class="panel"><div class="panel-title">🌐 PÚBLICO</div>'
            '<div class="panel-copy">Solo para lo que quedó sin resolver: DENUE, Wikidata, '
            'Common Crawl, DNS y web corporativa.</div></div>',
            unsafe_allow_html=True,
        )
        max_jobs = st.number_input("Máximo de empleadores (0 = todos)", min_value=0, value=100, step=50)
        buscar_publico = st.button("Buscar PÚBLICO", use_container_width=True)

    if buscar_local:
        estado = counts(db)
        if not estado["personas"]:
            remember("busqueda_msg", "Primero sube tu base arriba.", "warn")
        elif not estado["historicos"]:
            remember("busqueda_msg", "No hay histórico cargado contra el cual comparar.", "warn")
        else:
            with st.spinner("Buscando coincidencias en el histórico local..."):
                reuso = apply_local_reuse(db, settings)
                resumen = crossmatch_counts(db)
            st.session_state["cruce_resumen"] = resumen
            remember(
                "busqueda_msg",
                f"**{resumen['con_coincidencia']:,} de {resumen['total']:,}** registros encontraron coincidencia "
                f"· {reuso['direct_account_emails']:,} con correo histórico directo por cuenta.",
            )

    if buscar_publico:
        estado = counts(db)
        if not estado["personas"]:
            remember("busqueda_msg", "Primero sube tu base arriba.", "warn")
        elif not estado["jobs_pendientes"]:
            remember("busqueda_msg", "No hay empleadores pendientes. Corre primero la búsqueda LOCAL.", "warn")
        else:
            barra = st.progress(0, text="Preparando...")
            aviso = st.empty()

            def progreso(n, jobs, results):
                aviso.info(f"Empleadores procesados: {n:,}")
                barra.progress(min(1, n / max(1, max_jobs or n)), text=f"{n:,} procesados")

            with st.spinner("Consultando DENUE, Wikidata y sitios corporativos..."):
                pipe = Pipeline(db, settings, tokens())
                n = asyncio.run(pipe.run(max_jobs=int(max_jobs), batch=20, progress=progreso))
            remember("busqueda_msg", f"Búsqueda pública terminada: {n:,} empleadores procesados.")

    if buscar_denue:
        estado = counts(db)
        if not estado["personas"]:
            remember("busqueda_msg", "Primero sube tu base arriba.", "warn")
        elif not estado["jobs_pendientes"]:
            remember("busqueda_msg", "No hay empleadores pendientes. Corre primero la búsqueda LOCAL.", "warn")
        else:
            objetivo = int(max_denue) or estado["jobs_pendientes"]
            barra = st.progress(0, text="Preparando...")

            def progreso_denue(n, jobs, results):
                barra.progress(min(1, n / max(1, objetivo)), text=f"{n:,} de {objetivo:,} empleadores")

            local_settings = Settings.from_env()
            local_settings.denue_local_only = True
            pipe = Pipeline(db, local_settings, tokens())
            n = asyncio.run(pipe.run(max_jobs=int(max_denue), batch=200, progress=progreso_denue))
            remember("busqueda_msg", f"DENUE local terminado: {n:,} empleadores procesados. "
                     "Los que no tienen sitio web quedan como SIN_DOMINIO_DENUE.")

    show_remembered("busqueda_msg")

    resumen = st.session_state.get("cruce_resumen")
    if resumen:
        m1, m2, m3 = st.columns(3)
        m1.metric("Comparados", f"{resumen['total']:,}")
        m2.metric("Con coincidencia", f"{resumen['con_coincidencia']:,}")
        m3.metric("Sin coincidencia", f"{resumen['sin_coincidencia']:,}")

        st.markdown("### 3. Resultado del cruce")
        vista = build_local_crossmatch(db, limit=500)
        if vista:
            st.caption(f"Vista previa de las primeras {len(vista):,} filas. La descarga trae todo.")
            st.dataframe(pd.DataFrame(vista), use_container_width=True, height=460, hide_index=True)
        if st.button("Generar CSV completo del cruce", use_container_width=True):
            destino = Path(settings.output_dir) / "cruce_local.csv"
            with st.spinner("Escribiendo el archivo completo..."):
                info = write_crossmatch_csv(db, destino)
            st.success(f"{info['filas']:,} filas escritas en {info['ruta']}")
            st.session_state["cruce_csv"] = str(destino)
        if st.session_state.get("cruce_csv") and Path(st.session_state["cruce_csv"]).exists():
            ruta = Path(st.session_state["cruce_csv"])
            if ruta.stat().st_size < 200 * 1024 * 1024:
                st.download_button(
                    "Descargar cruce CSV",
                    ruta.read_bytes(),
                    file_name=ruta.name,
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.info(f"El archivo pesa {ruta.stat().st_size/1024/1024:.0f} MB. Ábrelo desde: {ruta}")

elif page.endswith("Histórico cargado"):
    page_header(
        "Conocimiento local",
        "Histórico cargado",
        "Lo que el motor ya conoce y usa para cruzar. Puedes añadir más archivos.",
    )
    h = counts(db)
    cols = st.columns(4)
    con_correo = db.conn.execute("SELECT COUNT(*) FROM historical_contacts WHERE email<>''").fetchone()[0]
    for col, card in zip(cols, [
        ("Registros históricos", h["historicos"], "", "Filas cargadas"),
        ("Con correo", con_correo, "", "Listos para reutilizar"),
        ("Empresas", h["empresas_unicas"], "", "Perfiles conocidos"),
        ("NRP conocidos", h["nrp_unicos"], "", "Registros patronales"),
    ]):
        with col:
            metric_card(*card)

    st.markdown("### Archivos incorporados")
    origen = pd.read_sql_query(
        "SELECT source_file AS archivo, COUNT(*) AS filas,"
        " SUM(CASE WHEN email<>'' THEN 1 ELSE 0 END) AS con_correo,"
        " SUM(verified) AS validados"
        " FROM historical_contacts GROUP BY source_file ORDER BY filas DESC",
        db.conn,
    )
    st.dataframe(origen, use_container_width=True, hide_index=True)

    st.markdown("### Añadir más histórico")
    verified = st.checkbox(
        "Marcar como validada la información de los archivos que suba ahora",
        help="Márcalo antes de subir. Da prioridad sobre cualquier correo deducido.",
    )
    hs = st.file_uploader("Excel o CSV", type=["xlsx", "xlsm", "csv"], accept_multiple_files=True, key="hist")
    nuevos, done = pending_uploads(hs, "hist_importados")
    if nuevos:
        reporte = []
        with st.spinner("Guardando históricos..."):
            for archivo in nuevos:
                done.add((archivo.name, archivo.size))
                ruta = save_upload(archivo, "hist_")
                problema = revisar_archivo(ruta)
                if problema:
                    reporte.append((archivo.name, None, problema))
                    continue
                try:
                    info = import_history(db, ruta, rebuild=False)
                except Exception as exc:
                    reporte.append((archivo.name, None, f"{type(exc).__name__}: {exc}"))
                    continue
                if verified:
                    db.conn.execute(
                        "UPDATE historical_contacts SET verified=1 WHERE source_file=?",
                        (info["source_file"],),
                    )
                    db.conn.commit()
                reporte.append((archivo.name, info, None))
            rebuild_knowledge(db)
        st.session_state["hist_reporte"] = reporte

    for nombre, info, error in st.session_state.get("hist_reporte", []):
        if error:
            st.error(f"**{nombre}**: no se pudo leer. {error}")
        elif info["rows_usable"]:
            st.success(
                f"**{nombre}**: {info['rows_imported']:,} filas guardadas, "
                f"{info['rows_usable']:,} utilizables para cruzar."
            )
        else:
            st.error(
                f"**{nombre}**: se leyeron {info['rows_imported']:,} filas pero **ninguna sirve para cruzar**. "
                "Necesita al menos una de: `cuenta`/`id_cuenta`/`IDCUENTA`, `nrp`, o `nombre_empresa`/`empresa`."
            )
        if info:
            with st.expander(f"Columnas que reconoció de {nombre}"):
                st.json(info["columns"])

elif page.endswith("Resultados"):
    page_header("Contactos", "Resultados", "Consulta, filtra y exporta la información consolidada.")
    q = st.text_input("Buscar", placeholder="Cuenta, NRP, empresa o correo")
    sql = RESULT_SQL.replace("ORDER BY p.row_id", "")
    params = []
    if q:
        sql += " WHERE p.account_id LIKE ? OR p.nrp LIKE ? OR p.company_input LIKE ? OR r.email LIKE ?"
        params = ["%" + q + "%"] * 4
    sql += " ORDER BY p.row_id LIMIT 2000"
    try:
        df = pd.read_sql_query(sql, db.conn, params=params)
        st.dataframe(df, use_container_width=True, height=560, hide_index=True)
    except Exception as exc:
        st.error(str(exc))
    if st.button("Generar exportaciones", use_container_width=True):
        try:
            info = export_all(db, settings.output_dir)
            st.success("Exportación terminada.")
            csv_path = Path(info["csv"])
            if csv_path.exists():
                st.download_button("Descargar resultados CSV", csv_path.read_bytes(), file_name=csv_path.name, mime="text/csv", use_container_width=True)
        except PermissionError as exc:
            st.error(str(exc))

elif page.endswith("Fuentes"):
    page_header("Configuración", "Fuentes y credenciales", "Administra proveedores externos y revisa su disponibilidad.")
    denue = st.text_input("INEGI_DENUE_TOKEN", type="password", placeholder="Token de DENUE")
    searx = st.text_input("SEARXNG_URL", placeholder="Opcional · http://localhost:8080")
    if st.button("Guardar configuración", use_container_width=True):
        env = Path(settings.tokens_path)
        current = {}
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    current[k] = v
        if denue:
            current["INEGI_DENUE_TOKEN"] = denue
        if searx:
            current["SEARXNG_URL"] = searx
        defaults = {"ENABLE_CLEARBIT_AUTOCOMPLETE":"true", "ENABLE_WIKIDATA":"true", "ENABLE_COMMON_CRAWL":"true", "ENABLE_DDGS":"true", "ENABLE_DNS":"true", "GLOBAL_CONCURRENCY":"12", "PER_HOST_CONCURRENCY":"2"}
        for key, value in defaults.items():
            current.setdefault(key, value)
        env.write_text("\n".join(f"{key}={value}" for key, value in current.items()) + "\n", encoding="utf-8")
        st.success("Configuración guardada. Reinicia la aplicación para recargarla.")
    st.markdown("### DENUE local")
    base_denue = Path(settings.denue_local_path).parent
    di = denue_local.info(base_denue)
    if di:
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Establecimientos", f'{int(di["establecimientos"]):,}')
        d2.metric("Con sitio web", f'{int(di["con_sitio_web"]):,}')
        d3.metric("Con correo de empresa", f'{int(di["con_correo_empresa"]):,}')
        d4.metric("Descargado", di.get("descarga", ""))
        st.caption(f"Base: {denue_local.db_path(base_denue)} · construida {di.get('construido', '')}")
    else:
        st.markdown('<div class="callout warning"><b>Sin copia local de DENUE.</b> Descárgala para buscar sin token ni Internet.</div>', unsafe_allow_html=True)
    st.caption("Descarga masiva de INEGI: 33 archivos (~630 MB) y una base local de varios GB. Puede tardar 20–40 minutos.")
    if st.button("Descargar / actualizar DENUE local", use_container_width=True):
        barra = st.progress(0, text="Preparando...")

        def avance(fase, i, n, nombre):
            barra.progress(min(1, i / max(1, n)), text=f"{fase.capitalize()} {i}/{n} · {nombre}")

        try:
            meta = denue_local.update(base_denue, avance)
            denue_local.default_source.cache_clear()
            st.success(f"DENUE local listo: {int(meta['establecimientos']):,} establecimientos.")
        except Exception as exc:
            st.error(f"No se pudo actualizar DENUE: {type(exc).__name__}: {exc}")

    health = pd.read_sql_query("SELECT source_name,state,success_count,failure_count,throttle_count,last_status,last_error,datetime(updated_at,'unixepoch','localtime') AS updated FROM source_health ORDER BY source_name", db.conn)
    st.markdown("### Salud de fuentes")
    st.dataframe(health, use_container_width=True, hide_index=True)

else:
    page_header("Trazabilidad", "Auditoría", "Revisa evidencia, rutas de decisión y eventos de red.")
    ev = pd.read_sql_query("SELECT kind,source_name,url,score,text,datetime(observed_at,'unixepoch','localtime') AS fecha FROM evidence ORDER BY id DESC LIMIT 1000", db.conn)
    st.markdown("### Evidencia")
    st.dataframe(ev, use_container_width=True, height=420, hide_index=True)
    net = pd.read_sql_query("SELECT source_name,event_type,detail,datetime(observed_at,'unixepoch','localtime') AS fecha FROM network_events ORDER BY id DESC LIMIT 500", db.conn)
    st.markdown("### Eventos de red")
    st.dataframe(net, use_container_width=True, height=300, hide_index=True)


# Se dibuja al final del script para que refleje lo que se acaba de guardar en
# esta misma corrida, no el estado previo.
guardado = counts(db)
filas = [
    ("Base objetivo", guardado["personas"], "personas"),
    ("Históricos", guardado["historicos"], "filas"),
    ("Empresas", guardado["empresas_unicas"], "perfiles"),
    ("Contactos", guardado["contactos_resueltos"], "con correo"),
]
marcado = "".join(
    f'<div class="stored-row{"" if valor else " empty"}"><span>{etiqueta}</span>'
    f'<b>{valor:,}</b></div>'
    for etiqueta, valor, _ in filas
)
with st.sidebar:
    st.markdown("---")
    st.markdown(
        f'<div class="stored"><div class="stored-title">Guardado en la base</div>{marcado}</div>',
        unsafe_allow_html=True,
    )
