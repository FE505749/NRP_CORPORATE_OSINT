/**
 * NRP Intelligence — la página. Sin compilación: JavaScript de módulo que
 * habla con el servidor local (api/main.py) por /api.
 *
 * Regla: todo texto que viene de datos (Excel, DENUE, Tukan) se escribe con
 * textContent, nunca como HTML.
 */

const $ = (id) => document.getElementById(id);
const numero = new Intl.NumberFormat("es-MX");
const fmt = (n) => (n === null || n === undefined || n === "" ? "—" : numero.format(Number(n)));

function el(tag, props = {}, ...hijos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const h of hijos) if (h !== null && h !== undefined) e.append(h);
  return e;
}

async function api(url, opciones = {}) {
  const r = await fetch(url, opciones);
  let datos = null;
  try { datos = await r.json(); } catch { /* respuesta sin cuerpo */ }
  if (!r.ok) {
    const d = datos && datos.detail;
    throw new Error(typeof d === "string" ? d : `Error ${r.status}`);
  }
  return datos;
}
const postJSON = (url, cuerpo) => api(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo || {}),
});

function mostrar(id, texto) {
  const caja = $(id);
  caja.textContent = texto || "";
  caja.classList.toggle("visible", Boolean(texto));
}

/** Tabla genérica. columnas: [{clave, titulo, num, celda(fila) -> Node|string}] */
function tabla(id, columnas, filas, vacio = "Sin registros.") {
  const t = $(id);
  t.replaceChildren();
  const cabeza = el("tr");
  for (const c of columnas) cabeza.append(el("th", { text: c.titulo }));
  t.append(el("thead", {}, cabeza));
  const cuerpo = el("tbody");
  if (!filas.length) {
    cuerpo.append(el("tr", {}, el("td", { class: "vacio", colspan: String(columnas.length), text: vacio })));
  }
  for (const f of filas) {
    const tr = el("tr");
    for (const c of columnas) {
      const v = c.celda ? c.celda(f) : f[c.clave];
      const td = el("td", { class: c.num ? "num" : "" });
      if (v instanceof Node) td.append(v);
      else {
        const texto = c.num ? fmt(v) : (v === null || v === undefined || v === "" ? "—" : String(v));
        td.textContent = texto;
        td.title = texto;
      }
      tr.append(td);
    }
    cuerpo.append(tr);
  }
  t.append(cuerpo);
}
const columnasDe = (filas) => (filas[0] ? Object.keys(filas[0]).map((k) => ({ clave: k, titulo: k.replaceAll("_", " ") })) : []);
const marcaEstado = (estado) => (estado ? el("span", { class: `marca-estado estado-${estado}`, text: estado }) : "—");

/** Sigue un proceso largo del servidor hasta que termina. */
function seguir(tid, idAvance, idTexto, idRelleno) {
  const caja = $(idAvance);
  caja.hidden = false;
  return new Promise((resolver, rechazar) => {
    const paso = async () => {
      try {
        const t = await api(`/api/trabajos/${tid}`);
        $(idTexto).textContent = t.texto;
        if (idRelleno && t.total) {
          caja.classList.remove("indefinido");
          $(idRelleno).style.width = `${Math.min(100, (t.hecho / t.total) * 100)}%`;
        }
        if (t.estado === "corriendo") { setTimeout(paso, 800); return; }
        caja.hidden = true;
        if (t.estado === "error") rechazar(new Error(t.error)); else resolver(t.resultado);
      } catch (e) { caja.hidden = true; rechazar(e); }
    };
    paso();
  });
}

function zonaArchivos(idZona, idInput, alElegir) {
  const zona = $(idZona), input = $(idInput);
  input.addEventListener("change", () => { if (input.files.length) alElegir([...input.files]); input.value = ""; });
  zona.addEventListener("dragover", (e) => { e.preventDefault(); zona.classList.add("encima"); });
  zona.addEventListener("dragleave", () => zona.classList.remove("encima"));
  zona.addEventListener("drop", (e) => {
    e.preventDefault(); zona.classList.remove("encima");
    const archivos = [...e.dataTransfer.files];
    if (archivos.length) alElegir(input.multiple ? archivos : archivos.slice(0, 1));
  });
}

// --------------------------------------------------------------------- tema
function aplicarTema(tema) {
  document.documentElement.dataset.theme = tema;
  $("tema-claro").setAttribute("aria-pressed", String(tema === "light"));
  $("tema-oscuro").setAttribute("aria-pressed", String(tema === "dark"));
  try { localStorage.setItem("tema-nrp", tema); } catch { /* sin persistencia */ }
}

// --------------------------------------------------------------------- estado general
async function refrescarEstado() {
  const e = await api("/api/estado");
  const c = e.conteos;
  $("pill-personas").textContent = fmt(c.personas);
  $("pill-correos").textContent = fmt(c.contactos_resueltos);
  $("lado-personas").textContent = fmt(c.personas);
  $("lado-contactos").textContent = fmt(c.contactos_resueltos);
  $("lado-empresas").textContent = fmt(c.empresas_unicas);
  $("lado-pendientes").textContent = fmt(c.jobs_pendientes);
  const lado = (id, texto) => { $(id).textContent = texto || "No cargado"; $(id).classList.toggle("apagado", !texto); };
  lado("lado-denue", e.denue && `${fmt(e.denue.establecimientos)} establecimientos`);
  lado("lado-tukan", e.tukan && `${fmt(e.tukan.contactos)} contactos`);
  lado("lado-tukan-activos", e.tukan && fmt(e.tukan.activos));
  lado("lado-historico", c.historicos && `${fmt(c.historicos)} registros`);
  $("punto-local").classList.toggle("apagado", !(e.denue || e.tukan));
  return e;
}

// --------------------------------------------------------------------- buscar
async function buscarPersona(ev) {
  ev.preventDefault();
  mostrar("persona-error", "");
  const boton = $("p-buscar");
  boton.disabled = true;
  boton.textContent = "Buscando…";
  try {
    const r = await postJSON("/api/persona", {
      primer_nombre: $("p-primer").value, segundo_nombre: $("p-segundo").value,
      apellido_paterno: $("p-paterno").value, apellido_materno: $("p-materno").value, empresa: $("p-empresa").value,
    });
    pintarPersona(r);
  } catch (e) {
    $("persona-resultado").hidden = true;
    mostrar("persona-error", e.message);
  } finally {
    boton.disabled = false;
    boton.textContent = "Buscar persona";
  }
}

function pintarPersona(r) {
  $("persona-resultado").hidden = false;
  $("r-persona").textContent = r.persona;
  $("r-correo").textContent = r.correo || "Sin correo";
  $("r-tipo").textContent = r.correo
    ? `${r.tipo_texto} · confianza ${Number(r.confianza).toFixed(1)}`
    : r.estado_texto;
  $("r-empresa").textContent = r.empresa || "—";
  $("r-dominio").textContent = r.dominio || "—";
  $("r-dominio-conf").textContent = r.dominio ? `Confianza ${Number(r.confianza_dominio).toFixed(1)}` : "";
  $("r-web").textContent = (r.sitio_web || "—").replace(/^https?:\/\//i, "").toLowerCase();

  const alt = $("r-alternativas");
  alt.replaceChildren();
  if (r.alternativas.length) {
    alt.append("Alternativas: ");
    r.alternativas.forEach((a, i) => { if (i) alt.append(" · "); alt.append(el("code", { text: a })); });
  }

  $("r-pasos").replaceChildren(...r.ruta_texto.map((p) => el("li", { text: p })));

  const reales = r.correos_reales || [];
  $("r-reales-bloque").hidden = !reales.length;
  $("r-tabla-caja").hidden = !reales.length;
  if (reales.length) {
    $("r-reales-titulo").textContent = `Correos reales con @${r.dominio}: ${fmt(reales.length)}`;
    $("r-desglose").replaceChildren(...r.desglose_texto.map((d) =>
      el("div", {}, el("span", { text: d.formato }), el("strong", { text: fmt(d.n) }))));
    tabla("r-tabla", [
      { clave: "correo", titulo: "Correo" },
      { clave: "fuente", titulo: "Fuente" },
      { titulo: "Estatus en Tukan", celda: (f) => marcaEstado(f.estatus_tukan) },
      { clave: "formato_texto", titulo: "Formato" },
    ], reales);
  }
  const ver = $("r-veredicto");
  ver.replaceChildren(el("b", { text: "Veredicto: " }), r.veredicto);
  $("persona-resultado").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function subirLote([archivo]) {
  mostrar("lote-error", "");
  $("lote").hidden = true;
  const datos = new FormData();
  datos.append("archivo", archivo);
  try {
    const { trabajo } = await api("/api/lote", { method: "POST", body: datos });
    const r = await seguir(trabajo, "avance-lote", "avance-lote-texto", "avance-lote-relleno");
    $("lote").hidden = false;
    $("lote-filas").textContent = fmt(r.filas);
    $("lote-con").textContent = fmt(r.con_correo);
    $("lote-sin").textContent = fmt(r.sin_correo);
    $("lote-pista").textContent = r.filas > r.vista.length
      ? `Vista previa de ${fmt(r.vista.length)} filas; el Excel trae todo` : archivo.name;
    const a = $("lote-descarga");
    a.href = `/api/descargas/${encodeURIComponent(r.archivo)}`;
    a.textContent = `Descargar ${r.archivo}`;
    tabla("tabla-lote", [
      { clave: "cuenta", titulo: "Cuenta" }, { clave: "correo", titulo: "Correo estimado" },
      { clave: "patron", titulo: "Patrón" }, { clave: "dominio", titulo: "Dominio" },
    ], r.vista);
    $("lote").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    mostrar("lote-error", `${archivo.name}: ${e.message}`);
  }
}

// --------------------------------------------------------------------- resumen
async function cargarResumen() {
  const r = await api("/api/resumen");
  const c = r.conteos;
  $("k-personas").textContent = fmt(c.personas);
  $("k-empresas").textContent = fmt(c.empresas_unicas);
  $("k-contactos").textContent = fmt(c.contactos_resueltos);
  $("k-pendientes").textContent = fmt(c.jobs_pendientes);
  for (const k of ["contactos", "empleadores", "cuenta"]) {
    $(`cob-${k}`).style.width = `${Math.min(100, r.cobertura[k])}%`;
    $(`cob-${k}-v`).textContent = `${r.cobertura[k].toFixed(1)}%`;
  }
  const caja = $("resumen-salud");
  caja.replaceChildren();
  if (!r.salud.length) caja.append(el("p", { class: "pista", text: "Sin actividad registrada: la búsqueda es local." }));
  for (const s of r.salud) {
    const ok = (s.estado || "CLOSED") === "CLOSED";
    caja.append(el("div", { class: "info-fila" }, el("span", { class: "info-et", text: s.fuente }),
      el("span", { class: `marca-estado estado-${ok ? "ACTIVO" : "CADUCO"}`, text: ok ? "Disponible" : "Pausada" })));
  }
}

// --------------------------------------------------------------------- resultados
async function cargarResultados(ev) {
  if (ev) ev.preventDefault();
  const q = $("q-resultados").value.trim();
  const { columnas, filas } = await api(`/api/resultados?q=${encodeURIComponent(q)}`);
  const objetos = filas.map((f) => Object.fromEntries(columnas.map((c, i) => [c, f[i]])));
  tabla("tabla-resultados", columnas.map((c) => ({ clave: c, titulo: c.replaceAll("_", " ") })), objetos,
    "Sin resultados. Sube una base en Buscar.");
  $("resultados-pie").textContent = filas.length >= 2000 ? "Se muestran las primeras 2,000 filas; la exportación trae todo." : `${fmt(filas.length)} filas`;
}

async function exportar() {
  mostrar("exportar-resultado", "Generando exportaciones…");
  try {
    const r = await postJSON("/api/exportar");
    const caja = $("exportar-resultado");
    caja.replaceChildren(`Exportación terminada: ${fmt(r.filas)} filas. `,
      el("a", { href: `/api/descargas/${encodeURIComponent(r.archivo)}`, text: `Descargar ${r.archivo}` }));
  } catch (e) { mostrar("exportar-resultado", e.message); }
}

// --------------------------------------------------------------------- histórico
async function cargarHistorico() {
  const h = await api("/api/historico");
  $("h-registros").textContent = fmt(h.registros);
  $("h-correo").textContent = fmt(h.con_correo);
  $("h-empresas").textContent = fmt(h.empresas);
  $("h-nrp").textContent = fmt(h.nrp);
  tabla("tabla-historico", [
    { clave: "archivo", titulo: "Archivo" }, { clave: "filas", titulo: "Filas", num: true },
    { clave: "con_correo", titulo: "Con correo", num: true }, { clave: "validados", titulo: "Validados", num: true },
  ], h.archivos, "Aún no hay histórico cargado.");
}

async function subirHistorico(archivos) {
  mostrar("historico-error", ""); mostrar("historico-resultado", "");
  const datos = new FormData();
  archivos.forEach((a) => datos.append("archivos", a));
  datos.append("validado", $("h-validado").checked ? "true" : "false");
  try {
    const { trabajo } = await api("/api/historico", { method: "POST", body: datos });
    const r = await seguir(trabajo, "avance-historico", "avance-historico-texto");
    const caja = $("historico-resultado");
    caja.replaceChildren(el("ul", {}, ...r.reporte.map((x) => el("li", {
      text: x.error ? `${x.archivo}: no se pudo leer. ${x.error}`
        : x.utilizables ? `${x.archivo}: ${fmt(x.filas)} filas guardadas, ${fmt(x.utilizables)} utilizables para cruzar.`
          : `${x.archivo}: ${fmt(x.filas)} filas, pero ninguna sirve para cruzar (falta cuenta, nrp o empresa).`,
    }))));
    caja.classList.add("visible");
    await Promise.all([cargarHistorico(), refrescarEstado()]);
  } catch (e) { mostrar("historico-error", e.message); }
}

// --------------------------------------------------------------------- fuentes
const kpi = (titulo, valor) => el("div", {}, el("span", { text: titulo }), el("strong", { text: valor }));

async function cargarFuentes() {
  const f = await api("/api/fuentes");
  const d = f.denue;
  $("f-denue-kpis").replaceChildren(...(d ? [
    kpi("Establecimientos", fmt(d.establecimientos)), kpi("Con sitio web", fmt(d.con_sitio_web)),
    kpi("Con correo de empresa", fmt(d.con_correo_empresa)), kpi("Descargado", d.descarga || "—"),
  ] : [kpi("Estado", "Sin copia local")]));
  $("f-denue-pista").textContent = d ? `Construida ${d.construido}` : "";
  const t = f.tukan;
  $("f-tukan-kpis").replaceChildren(...(t ? [
    kpi("Contactos", fmt(t.contactos)), kpi("NRP", fmt(t.nrp)), kpi("Activos", fmt(t.activos)),
    kpi("Con dominio de empresa", fmt(t.con_dominio_empresa)),
  ] : [kpi("Estado", "No precargado")]));
  $("f-tukan-pista").textContent = t ? `Desde ${t.archivo} · construida ${t.construido}` : "";
  $("f-tukan-precargar").disabled = !f.archivo_tukan;
  $("t-searx").value = f.searxng || "";
  $("t-denue").placeholder = f.token_denue ? "Ya hay un token guardado" : "Opcional: la copia local no lo necesita";
  tabla("tabla-salud", columnasDe(f.salud), f.salud, "Sin actividad registrada: la búsqueda es local.");
}

async function lanzar(url, idBoton, idError, idAvance, idTexto, idRelleno) {
  mostrar(idError, "");
  $(idBoton).disabled = true;
  try {
    const { trabajo } = await postJSON(url);
    await seguir(trabajo, idAvance, idTexto, idRelleno);
    await Promise.all([cargarFuentes(), refrescarEstado()]);
  } catch (e) { mostrar(idError, e.message); } finally { $(idBoton).disabled = false; }
}

async function guardarTokens(ev) {
  ev.preventDefault();
  try {
    await postJSON("/api/fuentes/tokens", { denue: $("t-denue").value, searxng: $("t-searx").value });
    $("t-denue").value = "";
    mostrar("tokens-resultado", "Configuración guardada. Reinicia la aplicación para que se use.");
  } catch (e) { mostrar("tokens-resultado", e.message); }
}

// --------------------------------------------------------------------- auditoría
async function cargarAuditoria() {
  const a = await api("/api/auditoria");
  tabla("tabla-evidencia", [
    { clave: "fecha", titulo: "Fecha" }, { clave: "tipo", titulo: "Tipo" }, { clave: "fuente", titulo: "Fuente" },
    { clave: "detalle", titulo: "Detalle" }, { clave: "puntaje", titulo: "Puntaje" },
  ], a.evidencia, "Sin evidencia todavía.");
  tabla("tabla-red", [
    { clave: "fecha", titulo: "Fecha" }, { clave: "fuente", titulo: "Fuente" },
    { clave: "evento", titulo: "Evento" }, { clave: "detalle", titulo: "Detalle" },
  ], a.red, "Sin eventos de red: la búsqueda es local.");
}

// --------------------------------------------------------------------- navegación
const CARGAS = {
  buscar: async () => {}, resumen: cargarResumen, resultados: cargarResultados,
  historico: cargarHistorico, fuentes: cargarFuentes, auditoria: cargarAuditoria,
};

function ir(nombre) {
  if (!CARGAS[nombre]) nombre = "buscar";
  for (const s of document.querySelectorAll(".seccion-pagina[id^='sec-']")) s.hidden = s.id !== `sec-${nombre}`;
  for (const b of document.querySelectorAll("[data-ir]")) {
    if (b.dataset.ir === nombre) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  const sec = $(`sec-${nombre}`);
  $("titulo-barra").textContent = sec.dataset.titulo;
  document.title = `${sec.dataset.titulo} · NRP Intelligence`;
  if (location.hash !== `#${nombre}`) history.replaceState(null, "", `#${nombre}`);
  CARGAS[nombre]().catch((e) => console.error(e));
}

function iniciar() {
  aplicarTema(document.documentElement.dataset.theme === "light" ? "light" : "dark");
  $("tema-claro").addEventListener("click", () => aplicarTema("light"));
  $("tema-oscuro").addEventListener("click", () => aplicarTema("dark"));
  for (const b of document.querySelectorAll("[data-ir]")) b.addEventListener("click", () => ir(b.dataset.ir));
  window.addEventListener("hashchange", () => ir(location.hash.slice(1)));

  $("forma-persona").addEventListener("submit", buscarPersona);
  zonaArchivos("soltar-lote", "archivo-lote", subirLote);
  $("forma-resultados").addEventListener("submit", cargarResultados);
  $("exportar").addEventListener("click", exportar);
  zonaArchivos("soltar-historico", "archivo-historico", subirHistorico);
  $("f-denue-actualizar").addEventListener("click", () => lanzar("/api/fuentes/denue", "f-denue-actualizar",
    "denue-error", "avance-denue", "avance-denue-texto", "avance-denue-relleno"));
  $("f-tukan-precargar").addEventListener("click", () => lanzar("/api/fuentes/tukan", "f-tukan-precargar",
    "tukan-error", "avance-tukan", "avance-tukan-texto"));
  $("forma-tokens").addEventListener("submit", guardarTokens);

  refrescarEstado().catch((e) => console.error(e));
  ir(location.hash.slice(1) || "buscar");
}

iniciar();
