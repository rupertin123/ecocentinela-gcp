"use strict";
const $ = id => document.getElementById(id);
const ESTRATOS = ["superior", "medio", "inferior"];
const NOMBRE_ESTRATO = {superior: "Upper canopy · 75 cm", medio: "Middle canopy · 45 cm", inferior: "Lower canopy · 15 cm"};
const CAM = {superior: "camsup", medio: "cammed", inferior: "caminf"};
const ESTADO = {
  RECOMENDAR:          {color: "rojo",  chip: "⚠ Spray recommended"},
  REVISAR_CONTROL:     {color: "rojo",  chip: "⚠ Above threshold despite protection — check control"},
  ESPERAR_EFECTO:      {color: "azul",  chip: "⏳ Product acting (first 72 h)"},
  PROTEGIDO:           {color: "verde", chip: "🛡 Protected"},
  VIGILAR:             {color: "ambar", chip: "👁 Watch: ≥ 70 % of threshold"},
  SIN_ACCION:          {color: "verde", chip: "✓ Below action threshold"},
  DATOS_INSUFICIENTES: {color: "gris",  chip: "… Not enough recent photos"},
};
let estrato = new URLSearchParams(location.search).get("estrato") || "medio";
if (!ESTRATOS.includes(estrato)) estrato = "medio";
let ws = null, grafDens = null, grafEf = null, ultimo = null, config = {productos: []}, token = null, usuario = null;

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (iso, conHora = true) => iso ? new Date(iso).toLocaleString("es-AR", Object.assign(
  {timeZone: "America/Argentina/Cordoba", day: "2-digit", month: "2-digit"}, conHora ? {hour: "2-digit", minute: "2-digit"} : {})) : "—";
const num = (v, d = 2) => v == null ? "—" : Number(v).toFixed(d);
const pct = v => v == null ? "—" : `${Math.round(v * 100)} %`;

function kpi(id, val, delta, alerta) {
  const k = $(id); k.querySelector(".val").textContent = val; k.querySelector(".delta").textContent = delta;
  k.classList.toggle("alerta", !!alerta);
}

// ------------------------------------------------------------------ resumen (KPIs + tarjetas de estrato)
function pintarResumen(r, det) {
  $("reloj").textContent = `Simulated season clock: ${r.estadio} · ${Math.round(r.dds)} days after sowing · agronomic date ${fmt(r.fecha_agronomica + "T12:00:00-03:00", false)}`;
  kpi("kEstadio", r.estadio, `${Math.round(r.dds)} DAS · ${r.de_dia ? "daytime: photo every hour" : "night: nodes asleep"}`);
  const nodos = det ? det.nodos : [];
  const todos = Object.values(ultimoNodos);
  const online = todos.filter(n => n.medido && (Date.now() - new Date(n.medido)) / 36e5 < (r.de_dia ? 2 : 14)).length;
  const rssi = todos.map(n => n.rssi).filter(v => v != null);
  kpi("kFlota", `${online}/12 online`, rssi.length ? `↑ Mean LoRa RSSI ${Math.round(rssi.reduce((a, b) => a + b, 0) / rssi.length)} dBm` : "—", online < 9);
  const temps = nodos.map(n => n.temperatura_c).filter(v => v != null);
  const t = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null;
  kpi("kTemp", t != null ? `${t.toFixed(1)} °C` : "—", t == null ? "—" : `RH ${num(nodos[0]?.humedad_rel_pct, 0)} % · ${NOMBRE_ESTRATO[estrato].split(" ·")[0].toLowerCase()}`);
  const bats = todos.map(n => n.bateria_v).filter(v => v != null);
  const b = bats.length ? Math.min(...bats) : null;
  kpi("kBat", b != null ? `${b.toFixed(2)} V` : "—", b == null ? "—" : (b < 3.5 ? "⚠ Low battery" : "↑ Solar charging OK"), b != null && b < 3.5);

  $("estratos").innerHTML = r.estratos.map(e => {
    const est = ESTADO[e.estado] || ESTADO.DATOS_INSUFICIENTES;
    const frac = e.densidad != null ? Math.min(1, e.densidad / e.umbral) : 0;
    const prot = e.proteccion && e.proteccion.vigente ? ` · 🛡 until ${fmt(e.proteccion.fecha_descubierto)}` : "";
    return `<button class="estrato ${est.color} ${e.estrato === estrato ? "sel" : ""}" data-estrato="${e.estrato}">
      <div><div class="nom">${esc(NOMBRE_ESTRATO[e.estrato])} — ${esc(e.nombre)} <i>(${esc(e.cientifico)})</i></div>
      <div class="det">${esc(est.chip)}${esc(prot)} · threshold ${num(e.umbral, 1)} ${esc(e.unidad)} (${esc(e.estadio)})</div></div>
      <div class="dens">${num(e.densidad)} <span class="det">${esc(e.unidad)}</span></div>
      <div class="barra"><i style="width:${Math.round(frac * 100)}%"></i></div></button>`;
  }).join("");
  document.querySelectorAll(".estrato").forEach(b => b.onclick = () => elegir(b.dataset.estrato));
  for (const e of r.estratos) {
    const c = $(CAM[e.estrato]); c.setAttribute("class", `cam ${(ESTADO[e.estado] || ESTADO.DATOS_INSUFICIENTES).color} ${e.estrato === estrato ? "sel" : ""}`);
  }
  pintarRecomendacion(r.estratos.find(e => e.estrato === estrato));
}

function pintarRecomendacion(e) {
  if (!e) return;
  const est = ESTADO[e.estado] || ESTADO.DATOS_INSUFICIENTES;
  $("chip").textContent = est.chip; $("chip").className = `chip ${est.color}`;
  const p = e.proteccion;
  const base = `4-camera mean: <b>${num(e.densidad)} ${esc(e.unidad)}</b> of ${esc(e.nombre)} (<i>${esc(e.cientifico)}</i>). Action threshold at ${esc(e.estadio)}: ${num(e.umbral, 1)} ${esc(e.unidad)} — ${esc(e.fuente_umbral)}.`;
  const efic = p ? ` Last application: ${esc(p.principio_activo)} on ${fmt(p.aplicada_en)}; residual efficacy now <b>${pct(p.eficacia_actual)}</b> (${p.origen === "bigquery" ? `BigQuery fit on ${p.dias_ajuste} days of field data` : "literature prior, not enough field data yet"}); uncovered (E &lt; 50 %) around <b>${fmt(p.fecha_descubierto)}</b>.` : "";
  const textos = {
    RECOMENDAR: `${base} <b>Spraying is recommended.</b> Reference active ingredient: ${esc(e.producto_sugerido)} (confirm the prescription with a licensed agronomist).${efic} After spraying, register it below so the system starts tracking residual efficacy.`,
    REVISAR_CONTROL: `${base}${efic} The pest is above threshold although protection should still be active: check spray coverage, resistance or re-infestation before spraying again.`,
    ESPERAR_EFECTO: `${base}${efic} The product is still acting (first 72 h): no new decision until its effect is measured.`,
    PROTEGIDO: `${base}${efic}`,
    VIGILAR: `${base}${efic} Below threshold but close: keep monitoring.`,
    SIN_ACCION: `${base}${efic} No spraying needed.`,
    DATOS_INSUFICIENTES: `${base} Not enough recent photos (need ≥ 3 of 4 nodes with ≥ 6 photos in 24 h).`,
  };
  $("txtRec").innerHTML = textos[e.estado] || base;
  const productos = config.productos.filter(x => x.plaga === e.plaga);
  $("selProducto").innerHTML = productos.map(x => `<option value="${esc(x.codigo)}">${esc(x.principio_activo)}</option>`).join("");
  $("btnApl").disabled = !(token && config.registro_habilitado);
  $("btnApl").title = token ? "" : "Sign in with Google (authorized accounts only)";
}

// ------------------------------------------------------------------ detalle del estrato
let ultimoNodos = {};
function pintarDetalle(d) {
  for (const n of d.nodos) ultimoNodos[n.id] = n;
  const e = ultimo?.resumen?.estratos.find(x => x.estrato === d.estrato) || {};
  $("tituloDens").textContent = `🐛 Population dynamics — ${e.nombre || ""} (${e.cientifico || ""}), ${NOMBRE_ESTRATO[d.estrato]}`;
  $("notaUmbral").textContent = `— Action threshold (${e.fuente_umbral || ""}). Density is estimated from on-device counts, averaged over the last 24 h of photos.`;
  $("nodos").innerHTML = d.nodos.map(n => `<div class="nodo"><b>${esc(n.id)}</b><br>${num(n.densidad_est)} ${esc(e.unidad || "")}
    <br>last photo: ${n.conteo_imagen ?? "—"} detected · conf ${num(n.confianza)}<br>${num(n.bateria_v)} V · RSSI ${n.rssi ?? "—"} dBm · ${fmt(n.medido)}
    <div class="mapa">${Array.from({length: 16}, (_, i) => `<div class="celda ${((n.mapa_ocupacion || 0) >> i) & 1 ? "on" : ""}"></div>`).join("")}</div></div>`).join("");
  const lineas = d.nodos.flatMap(n => (n.bitacora || []).map(l => [l.slice(l.indexOf(" ") + 1, l.indexOf("]")), `${n.id} ${l}`]));
  lineas.sort((a, b) => b[0].localeCompare(a[0]));
  $("consola").innerHTML = lineas.slice(0, 40).map(([, l]) => `<div class="${l.includes("[CRITICAL") ? "c" : l.includes("[WARN") ? "w" : "i"}">${esc(l)}</div>`).join("") || "<div class='i'>Waiting for uplinks…</div>";
  graficoDensidad(d, e);
  graficoEficacia(d.eficacia);
  const cruce = d.pronostico.find(p => p.densidad >= (e.umbral || Infinity));
  $("notaPron").textContent = d.pronostico.length ? (cruce ? `BigQuery ML forecast (ARIMA_PLUS, per stratum): threshold expected to be reached around ${fmt(cruce.hora)}.`
                                                           : "BigQuery ML forecast (ARIMA_PLUS, per stratum): no threshold crossing expected in the next 72 h.")
                                                  : "BigQuery ML forecast not available yet (the model retrains weekly).";
}

const ejeTiempo = {type: "linear", ticks: {color: "#8a96a3", maxTicksLimit: 8, callback: v => fmt(new Date(v).toISOString())}, grid: {color: "#1d232b"}};
function graficoDensidad(d, e) {
  const h = d.historia.map(x => ({x: Date.parse(x.hora), y: x.densidad}));
  const umb = d.historia.map(x => ({x: Date.parse(x.hora), y: x.umbral}));
  const pron = d.pronostico.map(x => ({x: Date.parse(x.hora), y: x.densidad}));
  if (h.length && pron.length) pron.unshift(h[h.length - 1]);
  if (umb.length && pron.length) umb.push({x: pron[pron.length - 1].x, y: e.umbral});
  const apl = d.aplicaciones.map(a => ({x: Date.parse(a.aplicada_en), y: e.umbral || 0}));
  const ds = [
    {label: "Action threshold", data: umb, borderColor: "#ff4d4d", borderDash: [6, 5], pointRadius: 0, borderWidth: 2, stepped: true},
    {label: `Stratum mean (${e.unidad || ""})`, data: h, borderColor: "#2cff05", backgroundColor: "rgba(44,255,5,.08)", fill: true, tension: .3, pointRadius: 0, borderWidth: 2},
    {label: "Forecast (BigQuery ML)", data: pron, borderColor: "#cfdbd5", borderDash: [2, 4], pointRadius: 0, borderWidth: 2},
    {label: "Application registered", data: apl, type: "scatter", pointStyle: "triangle", pointRadius: 9, backgroundColor: "#5ab0ff", borderColor: "#5ab0ff"},
  ];
  if (grafDens) { grafDens.data.datasets = ds; grafDens.update(); return; }
  grafDens = new Chart($("grafDens"), {type: "line", data: {datasets: ds},
    options: {responsive: true, maintainAspectRatio: false, animation: false, parsing: false, spanGaps: 3 * 36e5,
      plugins: {legend: {labels: {color: "#cfdbd5", boxWidth: 24}}},
      scales: {x: ejeTiempo, y: {beginAtZero: true, ticks: {color: "#8a96a3"}, grid: {color: "#1d232b"}}}}});
}

function graficoEficacia(ef) {
  const nota = $("notaEf");
  if (!ef) {
    nota.textContent = "No application registered for this pest in the current season.";
    if (grafEf) { grafEf.destroy(); grafEf = null; }
    return;
  }
  const curva = (p, n = 22) => Array.from({length: n * 2}, (_, i) => ({x: i / 2, y: p.e0 * Math.exp(-p.k * i / 2)}));
  const hoy = (Date.now() - Date.parse(ef.aplicada_en)) / 864e5;
  const ds = [
    {label: "Uncovered below 50 %", data: [{x: 0, y: ef.e_min}, {x: 21, y: ef.e_min}], borderColor: "#ff4d4d", borderDash: [6, 5], pointRadius: 0, borderWidth: 2},
    {label: "Literature prior", data: curva(ef.bibliografia), borderColor: "#8a96a3", borderDash: [3, 3], pointRadius: 0, borderWidth: 2},
    {label: "Observed (vs. virtual untreated control)", data: ef.observada.map(p => ({x: p.dia + 0.5, y: p.e_obs})), type: "scatter", pointRadius: 4, backgroundColor: "#f4f5f0"},
    {label: "Today", data: [{x: hoy, y: 0}, {x: hoy, y: 1}], borderColor: "#5ab0ff", pointRadius: 0, borderWidth: 1},
  ];
  if (ef.campo) ds.splice(2, 0, {label: `BigQuery fit (R² ${num(ef.campo.r2)})`, data: curva(ef.campo), borderColor: "#2cff05", pointRadius: 0, borderWidth: 2.5});
  const t50 = p => Math.log(p.e0 / ef.e_min) / p.k;
  const d0 = Date.parse(ef.aplicada_en);
  const fecha = p => p.e0 > ef.e_min ? fmt(new Date(d0 + t50(p) * 864e5).toISOString()) : "already below 50 %";
  nota.textContent = `${ef.principio_activo}, applied ${fmt(ef.aplicada_en)}. E(t) = E0·e^(−k·t). Prior (${ef.fuente}): E0 ${pct(ef.bibliografia.e0)}, half-life ${num(Math.log(2) / ef.bibliografia.k, 1)} d → uncovered ${fecha(ef.bibliografia)}. `
    + (ef.campo ? `Field fit in BigQuery (${ef.campo.dias} days of data): E0 ${pct(ef.campo.e0)}, half-life ${num(Math.log(2) / ef.campo.k, 1)} d → uncovered ${fecha(ef.campo)}.`
               : "Field fit pending: BigQuery starts measuring 48 h after the application and needs ≥ 3 days of data.");
  if (grafEf) grafEf.destroy();
  grafEf = new Chart($("grafEf"), {type: "line", data: {datasets: ds},
    options: {responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
      plugins: {legend: {labels: {color: "#cfdbd5", boxWidth: 20}}},
      scales: {x: {type: "linear", min: 0, max: 21, title: {display: true, text: "Days since application", color: "#8a96a3"}, ticks: {color: "#8a96a3"}, grid: {color: "#1d232b"}},
               y: {min: 0, max: 1, ticks: {color: "#8a96a3", callback: v => `${Math.round(v * 100)} %`}, grid: {color: "#1d232b"}}}}});
}

// ------------------------------------------------------------------ datos
function pintar(datos) {
  ultimo = datos;
  pintarDetalle(datos.detalle);
  pintarResumen(datos.resumen, datos.detalle);
}

async function recargar() {
  const [r, d] = await Promise.all([fetch("/api/resumen").then(x => x.json()), fetch("/api/estrato/" + estrato).then(x => x.json())]);
  // los nodos de los otros estratos sólo se usan para los KPI de flota
  await Promise.all(ESTRATOS.filter(e => e !== estrato).map(e => fetch("/api/estrato/" + e).then(x => x.json()).then(o => o.nodos.forEach(n => ultimoNodos[n.id] = n))));
  pintar({resumen: r, detalle: d});
}

function elegir(e) {
  estrato = e; history.replaceState(null, "", "?estrato=" + e);
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({estrato}));
  fetch("/api/estrato/" + e).then(x => x.json()).then(d => pintar({resumen: ultimo.resumen, detalle: d}));
}
Object.entries(CAM).forEach(([e, id]) => $(id).addEventListener("click", () => elegir(e)));

function conectar() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws/telemetria?estrato=${encodeURIComponent(estrato)}`;
  ws = new WebSocket(url);
  ws.onopen = () => { $("punto").className = "punto ok"; $("estadoWs").textContent = `FastAPI WebSocket connected · ${url.split("?")[0]}`; };
  ws.onmessage = m => pintar(JSON.parse(m.data));
  ws.onclose = ev => {
    $("punto").className = "punto mal";
    if (ev.code === 4000 || document.hidden) {
      $("estadoWs").innerHTML = 'Live session paused to stay within the free tier. <a href="#" id="reanudar">Resume</a>';
      $("reanudar").onclick = e => { e.preventDefault(); conectar(); };
    } else { $("estadoWs").textContent = "Disconnected, retrying…"; setTimeout(conectar, 5000); }
  };
}
document.addEventListener("visibilitychange", () => { if (document.hidden && ws) ws.close(); else if (!document.hidden && ws && ws.readyState === 3) conectar(); });

// ------------------------------------------------------------------ inicio de sesión con Google (sólo para registrar)
function iniciarGoogle() {
  if (!config.oauth_client_id) { $("sesion").textContent = "Read-only demo"; return; }
  const s = document.createElement("script");
  s.src = "https://accounts.google.com/gsi/client"; s.async = true;
  s.onload = () => {
    google.accounts.id.initialize({client_id: config.oauth_client_id, callback: r => {
      token = r.credential;
      try { usuario = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); } catch { usuario = {}; }
      $("sesion").innerHTML = `Signed in: ${esc(usuario.name || usuario.email || "")} · <a href="#" id="salir" style="color:#8a96a3">sign out</a>`;
      $("salir").onclick = ev => { ev.preventDefault(); token = null; google.accounts.id.disableAutoSelect(); iniciarGoogle(); };
      if (ultimo) pintarRecomendacion(ultimo.resumen.estratos.find(e => e.estrato === estrato));
    }});
    $("sesion").innerHTML = "";
    google.accounts.id.renderButton($("sesion"), {theme: "filled_black", size: "medium", text: "signin_with"});
  };
  document.head.appendChild(s);
}

$("btnApl").onclick = async () => {
  const e = ultimo.resumen.estratos.find(x => x.estrato === estrato);
  if (!["RECOMENDAR", "REVISAR_CONTROL"].includes(e.estado) && !confirm("The system is not recommending a spray for this pest right now. Register the application anyway?")) return;
  const local = $("fechaApl").value;
  const cuerpo = {estrato, producto: $("selProducto").value, nota: $("notaApl").value,
                  aplicada_en: local ? new Date(local).toISOString() : null};
  $("btnApl").disabled = true; $("msgApl").textContent = "Registering…";
  const r = await fetch("/api/aplicaciones", {method: "POST", headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token}, body: JSON.stringify(cuerpo)});
  const j = await r.json().catch(() => ({}));
  $("msgApl").textContent = r.ok ? `Application registered. Residual efficacy tracking started; estimated uncovered date (literature prior): ${fmt(j.descubierto_estimado)}. The BigQuery fit replaces it once there are 3 days of field data (from day 2 onwards).`
                                 : (j.detail || "Error");
  $("btnApl").disabled = false;
  if (r.ok) recargar();
};

$("formAgente").onsubmit = async ev => {
  ev.preventDefault();
  const pregunta = $("pregunta").value.trim(); if (pregunta.length < 3) return;
  $("respAgente").textContent = "Thinking… (the agent queries Firestore and BigQuery through tools)"; $("pasosAgente").textContent = "";
  const r = await fetch("/api/agente", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({estrato, pregunta})});
  const j = await r.json().catch(() => ({}));
  $("respAgente").textContent = r.ok ? j.respuesta : (j.detail || "Error");
  if (r.ok && j.pasos.length) $("pasosAgente").textContent = "Tools used: " + j.pasos.map(p => p.herramienta).join(" → ") + (j.modelo ? ` · ${j.modelo}` : "");
};

$("btnSpecs").onclick = () => $("specs").showModal();
$("btnCerrarSpecs").onclick = () => $("specs").close();

window.addEventListener("load", async () => {
  config = await fetch("/api/config").then(x => x.json());
  iniciarGoogle();
  await recargar();
  conectar();
});
