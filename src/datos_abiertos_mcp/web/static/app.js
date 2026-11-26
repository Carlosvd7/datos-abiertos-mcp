// Demo «Habla con los datos públicos de España». Sin dependencias externas.
// Todo el texto que llega del servidor se inserta como texto (nunca como HTML sin escapar).

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5"];
const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 3 });

function el(etiqueta, atributos = {}, hijos = []) {
  const nodo = document.createElement(etiqueta);
  for (const [k, v] of Object.entries(atributos)) {
    if (k === "texto") nodo.textContent = v;
    else if (k === "clase") nodo.className = v;
    else nodo.setAttribute(k, v);
  }
  for (const h of [].concat(hijos)) if (h != null) nodo.append(h);
  return nodo;
}

function svg(etiqueta, atributos = {}) {
  const nodo = document.createElementNS(SVG, etiqueta);
  for (const [k, v] of Object.entries(atributos)) nodo.setAttribute(k, v);
  return nodo;
}

// --- Markdown mínimo y seguro: párrafos, listas, tablas, negrita y código -------------------
function enLinea(texto) {
  const frag = document.createDocumentFragment();
  const partes = texto.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  for (const p of partes) {
    if (p.startsWith("**") && p.endsWith("**") && p.length > 4) frag.append(el("strong", { texto: p.slice(2, -2) }));
    else if (p.startsWith("`") && p.endsWith("`") && p.length > 2) frag.append(el("code", { texto: p.slice(1, -1) }));
    else if (p) frag.append(document.createTextNode(p));
  }
  return frag;
}

function markdown(texto) {
  const raiz = document.createDocumentFragment();
  const lineas = texto.replace(/\r/g, "").split("\n");
  let i = 0;
  while (i < lineas.length) {
    const linea = lineas[i].trim();
    if (!linea) { i++; continue; }
    if (linea.startsWith("|")) {
      const filas = [];
      while (i < lineas.length && lineas[i].trim().startsWith("|")) filas.push(lineas[i++].trim());
      const celdas = (f) => f.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const tabla = el("table");
      const cuerpo = filas.filter((f) => !/^\|?\s*:?-{2,}/.test(f));
      cuerpo.forEach((f, n) => {
        const tr = el("tr");
        for (const c of celdas(f)) tr.append(el(n === 0 ? "th" : "td", {}, enLinea(c)));
        tabla.append(tr);
      });
      raiz.append(el("div", { clase: "tabla-scroll" }, tabla));
      continue;
    }
    if (/^([-*]|\d+\.)\s+/.test(linea)) {
      const ordenada = /^\d+\./.test(linea);
      const lista = el(ordenada ? "ol" : "ul");
      while (i < lineas.length && /^([-*]|\d+\.)\s+/.test(lineas[i].trim())) {
        lista.append(el("li", {}, enLinea(lineas[i++].trim().replace(/^([-*]|\d+\.)\s+/, ""))));
      }
      raiz.append(lista);
      continue;
    }
    const parrafo = [];
    while (i < lineas.length && lineas[i].trim() && !/^(\||[-*]\s|\d+\.\s)/.test(lineas[i].trim())) {
      parrafo.push(lineas[i++].trim().replace(/^#+\s*/, ""));
    }
    raiz.append(el("p", {}, enLinea(parrafo.join(" "))));
  }
  return raiz;
}

// --- Gráfica SVG (líneas o barras) con tooltip -----------------------------------------------
const tooltip = el("div", { clase: "tooltip", role: "status" });
tooltip.hidden = true;
document.addEventListener("DOMContentLoaded", () => document.body.append(tooltip));

function color(i) {
  return getComputedStyle(document.documentElement).getPropertyValue(SERIES[i % SERIES.length]).trim();
}

function escalaBonita(min, max, pasos = 5) {
  if (min === max) { min -= 1; max += 1; }
  const bruto = (max - min) / pasos;
  const mag = 10 ** Math.floor(Math.log10(bruto));
  const paso = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((p) => p >= bruto);
  const ini = Math.floor(min / paso) * paso;
  const fin = Math.ceil(max / paso) * paso;
  const marcas = [];
  for (let v = ini; v <= fin + paso / 2; v += paso) marcas.push(+v.toFixed(10));
  return { ini, fin, marcas };
}

function mostrarTooltip(evento, titulo, filas) {
  tooltip.replaceChildren(el("strong", { texto: titulo }));
  for (const [nombre, valor, c] of filas) {
    const muestra = el("i");
    muestra.style.background = c;
    tooltip.append(el("div", {}, [muestra, `${nombre}: ${valor == null ? "—" : numero.format(valor)}`]));
  }
  tooltip.hidden = false;
  const x = Math.min(evento.clientX + 14, window.innerWidth - tooltip.offsetWidth - 8);
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${evento.clientY + 14}px`;
}

function trazoBarra(x0, y0, ancho, alto, redondeo, lado) {
  // Rectángulo con el extremo de datos redondeado (4px) y la base recta sobre el eje.
  const r = Math.max(0, Math.min(4, ancho / 2, alto / 2, redondeo));
  const x1 = x0 + ancho, y1 = y0 + alto;
  switch (lado) {
    case "arriba": return `M${x0},${y1}V${y0 + r}Q${x0},${y0} ${x0 + r},${y0}H${x1 - r}Q${x1},${y0} ${x1},${y0 + r}V${y1}Z`;
    case "abajo": return `M${x0},${y0}V${y1 - r}Q${x0},${y1} ${x0 + r},${y1}H${x1 - r}Q${x1},${y1} ${x1},${y1 - r}V${y0}Z`;
    case "derecha": return `M${x0},${y0}H${x1 - r}Q${x1},${y0} ${x1},${y0 + r}V${y1 - r}Q${x1},${y1} ${x1 - r},${y1}H${x0}Z`;
    default: return `M${x1},${y0}H${x0 + r}Q${x0},${y0} ${x0},${y0 + r}V${y1 - r}Q${x0},${y1} ${x0 + r},${y1}H${x1}Z`;
  }
}

function conectarTooltip(nodo, titulo, series, i) {
  nodo.addEventListener("mousemove", (ev) => mostrarTooltip(ev, titulo, series.map((s, k) => [s.nombre, s.valores[i], color(k)])));
  nodo.addEventListener("mouseleave", () => { tooltip.hidden = true; });
}

function dibujarBarrasHorizontales(etiquetas, series, valores, ancho) {
  const fila = Math.max(18, 10 + 8 * series.length), hueco = 2;
  const m = { arriba: 8, derecha: 48, abajo: 28, izquierda: Math.min(150, Math.round(ancho * 0.36)) };
  const alto = m.arriba + m.abajo + fila * etiquetas.length;
  const maxCar = Math.max(8, Math.floor(m.izquierda / 6.2));
  const { ini, fin, marcas } = escalaBonita(Math.min(0, ...valores), Math.max(0, ...valores));
  const iw = ancho - m.izquierda - m.derecha;
  const x = (v) => m.izquierda + ((v - ini) / (fin - ini)) * iw;
  const lienzo = svg("svg", { viewBox: `0 0 ${ancho} ${alto}`, role: "img" });
  for (const v of marcas) {
    lienzo.append(svg("line", { x1: x(v), x2: x(v), y1: m.arriba, y2: alto - m.abajo, class: v === 0 ? "eje" : "rejilla" }));
    const t = svg("text", { x: x(v), y: alto - m.abajo + 16, "text-anchor": "middle", class: "etiqueta" });
    t.textContent = numero.format(v);
    lienzo.append(t);
  }
  const grosor = Math.max(4, (fila - 6) / series.length - hueco);
  etiquetas.forEach((e, i) => {
    const yFila = m.arriba + i * fila;
    const t = svg("text", { x: m.izquierda - 8, y: yFila + fila / 2 + 4, "text-anchor": "end", class: "etiqueta" });
    t.textContent = e.length > maxCar ? `${e.slice(0, maxCar - 1)}…` : e;
    lienzo.append(t);
    series.forEach((s, k) => {
      const v = s.valores[i];
      if (typeof v !== "number") return;
      const y0 = yFila + 3 + k * (grosor + hueco);
      const xa = x(Math.min(0, v)), largo = Math.max(1, Math.abs(x(v) - x(0)));
      lienzo.append(svg("path", { d: trazoBarra(xa, y0, largo, grosor, 4, v >= 0 ? "derecha" : "izquierda"), fill: color(k) }));
      if (series.length === 1) {
        // Negativos: la cifra va al otro lado del eje 0 para no chocar con el nombre.
        const etiqueta = svg("text", { x: v >= 0 ? x(v) + 6 : x(0) + 6, y: y0 + grosor / 2 + 4, "text-anchor": "start", class: "directa" });
        etiqueta.textContent = numero.format(v);
        lienzo.append(etiqueta);
      }
    });
    const zona = svg("rect", { x: 0, y: yFila, width: ancho, height: fila, class: "zona" });
    conectarTooltip(zona, e, series, i);
    lienzo.append(zona);
  });
  return lienzo;
}

function dibujarGrafica(g) {
  const contenedor = $("grafica-svg");
  contenedor.replaceChildren();
  const etiquetas = g.etiquetas.map(String);
  const series = g.series.slice(0, SERIES.length);
  const valores = series.flatMap((s) => s.valores).filter((v) => typeof v === "number");
  if (!etiquetas.length || !valores.length) return false;
  // Se dibuja al ancho real del contenedor para que el texto no encoja en móvil.
  const ancho = Math.max(300, Math.min(900, Math.round(contenedor.clientWidth || 720)));
  if (g.tipo === "barras" && (etiquetas.length > 8 || etiquetas.some((e) => e.length > 10))) {
    contenedor.append(dibujarBarrasHorizontales(etiquetas, series, valores, ancho));
    dibujarLeyenda(series);
    return true;
  }

  const alto = ancho < 500 ? 240 : 320, m = { arriba: 16, derecha: 70, abajo: 44, izquierda: 44 };
  const esBarras = g.tipo === "barras";
  let min = Math.min(...valores), max = Math.max(...valores);
  if (esBarras) { min = Math.min(0, min); max = Math.max(0, max); }
  const { ini, fin, marcas } = escalaBonita(min, max);
  const iw = ancho - m.izquierda - m.derecha, ih = alto - m.arriba - m.abajo;
  const y = (v) => m.arriba + ih - ((v - ini) / (fin - ini)) * ih;
  const banda = iw / etiquetas.length;
  const x = (i) => m.izquierda + (esBarras ? banda * (i + 0.5) : (etiquetas.length === 1 ? iw / 2 : (iw * i) / (etiquetas.length - 1)));

  const lienzo = svg("svg", { viewBox: `0 0 ${ancho} ${alto}`, role: "img", "aria-label": g.titulo || "Gráfica" });
  for (const v of marcas) {
    lienzo.append(svg("line", { x1: m.izquierda, x2: ancho - m.derecha, y1: y(v), y2: y(v), class: v === 0 ? "eje" : "rejilla" }));
    const t = svg("text", { x: m.izquierda - 8, y: y(v) + 4, "text-anchor": "end", class: "etiqueta" });
    t.textContent = numero.format(v);
    lienzo.append(t);
  }
  // Etiquetas del eje X: una de cada N; la última siempre, quitando la anterior si quedaría pegada.
  const cadaN = Math.ceil(etiquetas.length / Math.max(3, Math.floor(ancho / 75)));
  const ultima = etiquetas.length - 1;
  const visibles = new Set(etiquetas.map((_, i) => i).filter((i) => i % cadaN === 0));
  if (!visibles.has(ultima)) {
    const previa = ultima - (ultima % cadaN);
    if (ultima - previa < cadaN * 0.75) visibles.delete(previa);
    visibles.add(ultima);
  }
  etiquetas.forEach((e, i) => {
    if (!visibles.has(i)) return;
    const t = svg("text", { x: x(i), y: alto - m.abajo + 18, "text-anchor": "middle", class: "etiqueta" });
    t.textContent = e.length > 12 ? `${e.slice(0, 11)}…` : e;
    lienzo.append(t);
  });

  if (esBarras) {
    const hueco = 2;
    const anchoBarra = Math.max(3, Math.min(40, (banda * 0.7) / series.length - hueco));
    series.forEach((s, k) => {
      s.valores.forEach((v, i) => {
        if (typeof v !== "number") return;
        const x0 = x(i) - (series.length * (anchoBarra + hueco)) / 2 + k * (anchoBarra + hueco);
        const y0 = y(Math.max(0, v)), h = Math.max(1, Math.abs(y(v) - y(0)));
        lienzo.append(svg("path", { d: trazoBarra(x0, y0, anchoBarra, h, 4, v >= 0 ? "arriba" : "abajo"), fill: color(k) }));
      });
    });
    etiquetas.forEach((e, i) => {
      const zona = svg("rect", { x: x(i) - banda / 2, y: m.arriba, width: banda, height: ih, class: "zona" });
      conectarTooltip(zona, e, series, i);
      lienzo.append(zona);
    });
  } else {
    series.forEach((s, k) => {
      let d = "";
      s.valores.forEach((v, i) => {
        if (typeof v !== "number") return;
        d += `${d && s.valores[i - 1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      });
      lienzo.append(svg("path", { d, fill: "none", stroke: color(k), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const ultimo = s.valores.map((v, i) => [v, i]).filter(([v]) => typeof v === "number").pop();
      if (ultimo && series.length <= 4) {
        const t = svg("text", { x: x(ultimo[1]) + 8, y: y(ultimo[0]) + 4, class: "directa" });
        t.textContent = series.length > 1 ? `${s.nombre.slice(0, 10)} ${numero.format(ultimo[0])}` : numero.format(ultimo[0]);
        lienzo.append(t);
      }
    });
    const cursor = svg("line", { y1: m.arriba, y2: m.arriba + ih, class: "cursor", visibility: "hidden" });
    const puntos = series.map((_, k) => svg("circle", { r: 4.5, fill: color(k), class: "punto", visibility: "hidden" }));
    lienzo.append(cursor, ...puntos);
    const zona = svg("rect", { x: m.izquierda, y: m.arriba, width: iw, height: ih, class: "zona" });
    zona.addEventListener("mousemove", (ev) => {
      const caja = lienzo.getBoundingClientRect();
      const px = ((ev.clientX - caja.left) / caja.width) * ancho;
      const i = Math.max(0, Math.min(etiquetas.length - 1, Math.round(((px - m.izquierda) / iw) * (etiquetas.length - 1))));
      cursor.setAttribute("x1", x(i)); cursor.setAttribute("x2", x(i)); cursor.setAttribute("visibility", "visible");
      series.forEach((s, k) => {
        const v = s.valores[i];
        puntos[k].setAttribute("visibility", typeof v === "number" ? "visible" : "hidden");
        if (typeof v === "number") { puntos[k].setAttribute("cx", x(i)); puntos[k].setAttribute("cy", y(v)); }
      });
      mostrarTooltip(ev, etiquetas[i], series.map((s, k) => [s.nombre, s.valores[i], color(k)]));
    });
    zona.addEventListener("mouseleave", () => {
      tooltip.hidden = true; cursor.setAttribute("visibility", "hidden");
      puntos.forEach((p) => p.setAttribute("visibility", "hidden"));
    });
    lienzo.append(zona);
  }
  contenedor.append(lienzo);
  dibujarLeyenda(series);
  return true;
}

function dibujarLeyenda(series) {
  // Con una sola serie el título la nombra; con dos o más, leyenda siempre.
  const leyenda = $("grafica-leyenda");
  leyenda.replaceChildren();
  if (series.length < 2) return;
  series.forEach((s, k) => {
    const item = el("span", { texto: s.nombre });
    item.style.setProperty("--muestra", color(k));
    leyenda.append(item);
  });
}

// --- Tabla de datos -------------------------------------------------------------------------
function dibujarTabla(t) {
  const tabla = $("tabla");
  tabla.replaceChildren();
  if (!t) { $("detalle-tabla").hidden = true; return; }
  $("detalle-tabla").hidden = false;
  tabla.append(el("tr", {}, t.columnas.map((c) => el("th", { texto: c }))));
  for (const fila of t.filas) {
    tabla.append(el("tr", {}, fila.map((v) => el("td", typeof v === "number" ? { texto: numero.format(v), clase: "num" } : { texto: v ?? "" }))));
  }
  $("tabla-nota").textContent = t.truncado ? "Se muestran solo las primeras filas." : "";
}

// --- Flujo principal ------------------------------------------------------------------------
function estado(texto, { error = false, cargando = false } = {}) {
  const caja = $("estado");
  caja.hidden = !texto;
  caja.className = `estado${error ? " error" : ""}`;
  caja.replaceChildren(...(cargando ? [el("span", { clase: "girando", "aria-hidden": "true" })] : []), texto || "");
}

function restantes(r) {
  if (!r) return;
  $("contador").textContent = r.dia > 0
    ? `Te ${r.dia === 1 ? "queda 1 pregunta propia" : `quedan ${r.dia} preguntas propias`} hoy · las de ejemplo son gratis`
    : "Ya has hecho tu pregunta de hoy · las de ejemplo siguen disponibles";
}

async function preguntar(texto) {
  const boton = $("enviar");
  boton.disabled = true;
  $("resultado").hidden = true;
  estado("Consultando los datos… (suele tardar entre 10 y 40 segundos)", { cargando: true });
  try {
    const resp = await fetch("/api/preguntar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pregunta: texto }),
    });
    const datos = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detalle = typeof datos.detail === "string" ? datos.detail : "La pregunta debe tener entre 3 y 300 caracteres.";
      estado(detalle, { error: true });
      if (resp.status === 429) restantes({ dia: 0, hora: 0 });
      return;
    }
    estado("");
    $("respuesta").replaceChildren(markdown(datos.respuesta || ""));
    // Se muestra antes de dibujar: la gráfica se ajusta al ancho visible del contenedor.
    $("resultado").hidden = false;
    $("grafica").hidden = !datos.grafica;
    const hayGrafica = Boolean(datos.grafica) && dibujarGrafica(datos.grafica);
    $("grafica").hidden = !hayGrafica;
    $("grafica-titulo").textContent = hayGrafica ? (datos.grafica.titulo || "") : "";
    $("sql").textContent = datos.sql.length ? datos.sql.map((s) => `${s};`).join("\n\n") : "(no se ejecutó ninguna consulta)";
    dibujarTabla(datos.tabla);
    $("fuentes").replaceChildren(
      ...(datos.fuentes.length
        ? datos.fuentes.map((f) => el("li", {}, [
            el("a", { href: f.url, texto: f.organismo, rel: "noopener", target: "_blank" }),
            ` — ${f.conjunto}. Datos hasta ${f.datos_hasta}. Licencia: ${f.licencia}.`,
          ]))
        : [el("li", { texto: "Sin fuentes: la respuesta no usó datos." })]),
    );
    const llamadas = datos.pasos.length;
    $("metadatos").textContent = `${llamadas} llamada${llamadas === 1 ? "" : "s"} a herramientas MCP · ${numero.format(datos.segundos)} s`
      + (datos.de_cache ? " · respuesta guardada, sin coste" : "");
    restantes(datos.restantes);
    $("resultado").hidden = false;
  } catch {
    estado("No se ha podido conectar con el servidor.", { error: true });
  } finally {
    boton.disabled = false;
  }
}

async function iniciar() {
  $("formulario").addEventListener("submit", (e) => {
    e.preventDefault();
    const texto = $("pregunta").value.trim();
    if (texto.length >= 3) preguntar(texto);
  });
  $("pregunta").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("formulario").requestSubmit(); }
  });
  try {
    const info = await (await fetch("/api/info")).json();
    $("lista-ejemplos").replaceChildren(...info.ejemplos.map((ej) => {
      const chip = el("button", { type: "button", clase: "chip", texto: ej });
      chip.addEventListener("click", () => { $("pregunta").value = ej; preguntar(ej); });
      return chip;
    }));
    $("tablas").replaceChildren(...info.tablas.filter((t) => t.fuente).map((t) =>
      el("li", {}, [el("code", { texto: t.tabla }), ` — ${t.descripcion.split(". ")[0].replace(/\.$/, "")}. Hasta ${t.datos_hasta}.`])));
    restantes(info.restantes);
    // Enlaces compartibles: /?q=pregunta rellena la pregunta; si es un ejemplo (gratis, en caché), la lanza.
    const q = new URLSearchParams(location.search).get("q");
    if (q) {
      $("pregunta").value = q.slice(0, 300);
      if (info.ejemplos.includes(q)) preguntar(q);
    }
  } catch {
    estado("No se ha podido cargar la información de la demo.", { error: true });
  }
}

document.addEventListener("DOMContentLoaded", iniciar);
