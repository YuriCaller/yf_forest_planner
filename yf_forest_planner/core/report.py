"""Informe de la corrida, en HTML y en Word.

Toma el PlanningContext, que viene acumulando parametros, metricas y hashes de
insumos desde el primer modulo, y lo rinde en dos formatos. Ambos salen del
mismo arbol de secciones, asi que no pueden divergir.

Dos criterios que gobiernan el contenido:

  TRAZABILIDAD  cada insumo se declara con su ruta, SRC y SHA-256 cuando existe
                en disco. Cuando no, se dice que no es verificable en lugar de
                inventar un identificador.

  ALCANCE       el informe declara en la portada y al cierre que los trazos y
                ubicaciones son propuestas tecnicas sujetas a verificacion de
                campo. Un documento que omite eso induce a error.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Optional

from .constants import (
    M1_HIDROLOGIA,
    M3_PATIOS,
    M7_INTENSIDAD,
    PRESETS,
    MODULE_LABELS,
    MODULE_ORDER,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)

NOTA_ALCANCE = (
    "Los trazos, ubicaciones y cantidades de este informe son propuestas "
    "tecnicas obtenidas por modelamiento a partir de un modelo digital de "
    "elevacion y del censo forestal. Estan sujetas a verificacion y "
    "replanteo en campo, y no constituyen un diseno definitivo."
)

NOTA_DISTANCIAS = (
    "Las distancias de arrastre y de saca son euclidianas. La distancia real "
    "por via puede ser mayor donde el relieve o los humedales obligan a rodear."
)


# --------------------------------------------------------------------------
# Modelo de secciones
# --------------------------------------------------------------------------


class Section:
    """Un bloque del informe, independiente del formato de salida."""

    def __init__(self, titulo: str, nivel: int = 1):
        self.titulo = titulo
        self.nivel = nivel
        self.bloques: list[tuple] = []

    def texto(self, valor: str) -> "Section":
        self.bloques.append(("p", valor))
        return self

    def nota(self, valor: str) -> "Section":
        self.bloques.append(("nota", valor))
        return self

    def lista(self, items) -> "Section":
        items = [i for i in items if i]
        if items:
            self.bloques.append(("ul", items))
        return self

    def tabla(self, headers, rows) -> "Section":
        if rows:
            self.bloques.append(("tabla", (headers, rows)))
        return self

    def pares(self, datos) -> "Section":
        """Tabla de dos columnas para parametros y resumenes."""
        filas = [(k, v) for k, v in datos if v not in (None, "")]
        if filas:
            self.bloques.append(("tabla", (["Concepto", "Valor"], filas)))
        return self


# --------------------------------------------------------------------------
# Construccion
# --------------------------------------------------------------------------


def _fmt(valor, decimales: int = 2) -> str:
    if isinstance(valor, bool):
        return "si" if valor else "no"
    if isinstance(valor, float):
        return f"{valor:,.{decimales}f}"
    if isinstance(valor, int):
        return f"{valor:,d}"
    return str(valor)


def build_sections(ctx) -> list:
    """Arma el arbol de secciones a partir del contexto."""
    secciones: list[Section] = []

    # ---- portada y alcance
    portada = Section("Resumen de la corrida")
    portada.pares([
        ("Complemento", f"{PLUGIN_NAME} {PLUGIN_VERSION}"),
        ("Ejecutado", ctx.started_at or datetime.now().isoformat(timespec="seconds")),
        ("Sistema de referencia", ctx.crs.authid() if ctx.crs else "no definido"),
        ("Area de trabajo", f"{_fmt(ctx.pca_area_ha)} ha" if ctx.pca_area_ha else ""),
        ("Semilla aleatoria", ctx.random_seed),
        ("Modulos ejecutados", ", ".join(
            MODULE_LABELS.get(m, m) for m in ctx.completed_modules()
        ) or "ninguno"),
    ])
    portada.nota(NOTA_ALCANCE)
    secciones.append(portada)

    # ---- insumos con trazabilidad
    if ctx.sources:
        insumos = Section("Insumos y trazabilidad")
        filas = []
        for src in ctx.sources:
            filas.append([
                src.rol,
                src.nombre,
                src.crs or "sin definir",
                _fmt(src.entidades, 0) if src.entidades is not None else "-",
                (src.sha256[:16] + "..." if src.sha256 else "no verificable"),
            ])
        insumos.tabla(
            ["Rol", "Capa", "SRC", "Entidades", "SHA-256"], filas
        )
        insumos.nota(
            "El resumen criptografico se calcula sobre el archivo de origen. "
            "Las capas en memoria o servidas de forma remota no tienen hash "
            "verificable y se declaran como tales."
        )
        secciones.append(insumos)

    # ---- censo
    if ctx.censo_report is not None:
        rep = ctx.censo_report
        censo = Section("Censo forestal")
        if hasattr(rep, "summary_lines"):
            censo.lista(rep.summary_lines())

        por_especie = getattr(rep, "bajo_dmc_por_especie", None) or {}
        if getattr(rep, "n_bajo_dmc", 0) and por_especie:
            filas = [
                [sp, _fmt(n, 0), _fmt(v)]
                for sp, (n, v) in sorted(
                    por_especie.items(), key=lambda kv: -kv[1][0]
                )
            ]
            censo.texto(
                "Individuos por debajo del diametro minimo de corta. No pueden "
                "talarse, por lo que fueron excluidos del volumen aprovechable "
                "y no se asignaron a ningun patio."
            )
            censo.tabla(["Especie", "Individuos", "Volumen (m3)"], filas)

        if getattr(rep, "advertencias", None):
            censo.texto("Observaciones sobre la calidad del dato:")
            censo.lista(rep.advertencias)
        secciones.append(censo)

    # ---- modulos
    for mid in MODULE_ORDER:
        res = ctx.result(mid)
        if res is None:
            continue
        seccion = Section(MODULE_LABELS.get(mid, mid))

        if res.params:
            seccion.texto("Parametros aplicados:")
            seccion.pares([(k, _fmt(v)) for k, v in res.params.items()])

        if res.metrics:
            seccion.texto("Resultados:")
            seccion.pares([(k, _fmt(v)) for k, v in res.metrics.items()])

        if mid == M1_HIDROLOGIA:
            seccion.nota(
                "El umbral de area drenada se expresa en hectareas para que el "
                "valor calibrado siga siendo valido al cambiar de modelo de "
                "elevacion. Las fajas marginales se generaron con la tabla "
                "declarada arriba, que no constituye una constante normativa: "
                "la autoridad competente las fija caso por caso."
            )
        if mid == M3_PATIOS:
            seccion.nota(NOTA_DISTANCIAS)
        if mid == M7_INTENSIDAD:
            seccion.nota(
                "Los limites se verifican sobre una malla de una hectarea y no "
                "sobre el promedio del predio. Un area cuya media cumple puede "
                "concentrar el aprovechamiento en pocas hectareas, y es esa "
                "concentracion la que abre claros grandes."
            )

        if res.advertencias:
            seccion.texto("Advertencias:")
            seccion.lista(res.advertencias)
        if res.errores:
            seccion.texto("Errores:")
            seccion.lista(res.errores)

        secciones.append(seccion)

    # ---- normativa aplicada
    preset = getattr(ctx, "preset", "")
    if preset and preset in PRESETS:
        datos = PRESETS[preset]
        norma = Section("Marco normativo aplicado")
        norma.pares([("Jurisdiccion", datos.get("etiqueta", preset))])
        norma.nota(datos.get("fuente", ""))
        cfg = getattr(ctx, "intensity_config", None)
        if cfg is not None:
            norma.texto("Limites verificados:")
            norma.pares([
                ("Volumen maximo por hectarea",
                 f"{cfg.max_volumen_ha:g} m3/ha" if cfg.max_volumen_ha else "no verificado"),
                ("Individuos maximos por hectarea",
                 f"{cfg.max_arboles_ha:g}" if cfg.max_arboles_ha else "no verificado"),
                ("Area basal removible",
                 f"{cfg.max_area_basal_pct:g} %" if cfg.max_area_basal_pct else "no verificado"),
                ("Abundancia minima por especie",
                 f"{cfg.min_abundancia_ha:g} ind/ha" if cfg.min_abundancia_ha else "no verificado"),
            ])
            if getattr(cfg, "es_censo_comercial", False):
                norma.nota(
                    "El insumo es un censo comercial. El area basal removida y "
                    "la abundancia por especie no son verificables con ese dato: "
                    "solo registra las especies de interes por encima del DMC, "
                    "de modo que el denominador no es el rodal. Ambos criterios "
                    "requieren el inventario estadistico."
                )
        secciones.append(norma)

    # ---- impacto consolidado
    impacto = getattr(ctx, "impacto", None)
    if impacto is not None and getattr(impacto, "ok", False):
        imp = Section("Areas de impacto")
        headers, filas = impacto.tabla_impacto()
        imp.tabla(headers, filas)
        imp.nota(
            "La columna Tipo distingue estimacion de control: la geometria de "
            "vias, patios y pistas es una propuesta sujeta a replanteo, "
            "mientras que los volumenes y exclusiones son verificaciones "
            "contra el censo."
        )
        headers, filas = impacto.tabla_volumen()
        if filas:
            imp.texto("Balance de volumen:")
            imp.tabla(headers, filas)
        for comp in impacto.componentes:
            if comp.nota:
                imp.lista([f"{comp.nombre}: {comp.nota}"])
        secciones.append(imp)

    # ---- escenarios de centros
    escenarios = getattr(ctx, "escenarios_centros", None)
    if escenarios:
        esc = Section("Comparacion de escenarios de centros de acopio")
        filas = []
        for e in escenarios:
            filas.append([
                _fmt(e.n_centros, 0),
                _fmt(e.momento_m3km, 1),
                f"{e.ahorro_pct:.1f} %" if e.ahorro_pct else "-",
                f"{e.dist_media_saca:,.0f} m",
                f"{e.dist_max_saca:,.0f} m",
                _fmt(e.cruces_mayores, 0),
                _fmt(e.area_centros_ha),
            ])
        esc.tabla(
            ["Centros", "Momento (m3-km)", "Ahorro", "Saca media",
             "Saca maxima", "Cruces", "Area (ha)"],
            filas,
        )
        esc.nota(
            "El momento de transporte es la suma del volumen por la distancia "
            "patio-centro. La eleccion entre escenarios depende del costo de "
            "habilitar un centro frente al ahorro de saca, que es una decision "
            "del formulador y no del modelo."
        )
        notas = [e.nota for e in escenarios if e.nota]
        esc.lista(notas)
        secciones.append(esc)

    # ---- cierre
    cierre = Section("Alcance y limitaciones")
    cierre.lista([
        NOTA_ALCANCE,
        NOTA_DISTANCIAS,
        "La red hidrica deriva del modelo de elevacion. Sobre bosque cerrado, "
        "los modelos de superficie sobreestiman la cota y desplazan los cauces; "
        "la red debe contrastarse con hidrografia levantada en campo.",
        "La ausencia de sitios arqueologicos o de otras restricciones en las "
        "capas consultadas no acredita su inexistencia en el terreno.",
    ])
    if ctx.advertencias:
        cierre.texto("Advertencias acumuladas durante la corrida:")
        cierre.lista(sorted(set(ctx.advertencias)))
    secciones.append(cierre)

    return secciones


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

CSS = """
:root { --tinta:#1f2a24; --verde:#2e5e3e; --agua:#2b6a8f; --tierra:#b5532a;
        --suave:#f7f6f2; --linea:#d8d5cc; }
* { box-sizing:border-box; }
body { font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:var(--tinta);
       margin:0; padding:0 0 60px; background:#fff; line-height:1.55; font-size:15px; }
.wrap { max-width:900px; margin:0 auto; padding:0 28px; }
header { background:var(--verde); color:#fff; padding:34px 0 26px; margin-bottom:28px; }
header .wrap { padding:0 28px; }
h1 { margin:0 0 6px; font-size:26px; font-weight:600; letter-spacing:-.2px; }
header .sub { opacity:.85; font-size:14px; }
h2 { color:var(--verde); font-size:19px; margin:34px 0 12px;
     padding-bottom:6px; border-bottom:2px solid var(--linea); }
h3 { font-size:15px; margin:20px 0 8px; color:var(--agua); }
p { margin:10px 0; }
ul { margin:8px 0 14px; padding-left:20px; }
li { margin:3px 0; }
table { border-collapse:collapse; width:100%; margin:12px 0 20px; font-size:13.5px; }
th { background:var(--suave); text-align:left; font-weight:600; color:var(--verde);
     padding:8px 10px; border-bottom:2px solid var(--linea); }
td { padding:7px 10px; border-bottom:1px solid #ececec; vertical-align:top; }
tr:last-child td { border-bottom:1px solid var(--linea); }
td:nth-child(n+2) { font-variant-numeric:tabular-nums; }
.nota { background:var(--suave); border-left:3px solid var(--tierra);
        padding:11px 14px; margin:14px 0; font-size:13.5px; color:#4a4a44; }
.pie { margin-top:44px; padding-top:16px; border-top:1px solid var(--linea);
       font-size:12.5px; color:#7a7a72; }
code { font-family:Consolas,monospace; font-size:12.5px; color:#555; }
@media print { header { background:#fff; color:var(--tinta);
                        border-bottom:3px solid var(--verde); }
               h2 { page-break-after:avoid; } table { page-break-inside:avoid; } }
"""


def render_html(ctx, secciones: Optional[list] = None) -> str:
    secciones = secciones or build_sections(ctx)
    e = html.escape
    out = [
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>",
        f"<title>Informe {e(PLUGIN_NAME)}</title>",
        f"<style>{CSS}</style></head><body>",
        "<header><div class='wrap'>",
        "<h1>Informe de planificacion forestal</h1>",
        f"<div class='sub'>{e(PLUGIN_NAME)} {e(PLUGIN_VERSION)} &middot; "
        f"{e(ctx.started_at or '')}</div>",
        "</div></header><div class='wrap'>",
    ]

    for sec in secciones:
        tag = "h2" if sec.nivel == 1 else "h3"
        out.append(f"<{tag}>{e(sec.titulo)}</{tag}>")
        for tipo, dato in sec.bloques:
            if tipo == "p":
                out.append(f"<p>{e(str(dato))}</p>")
            elif tipo == "nota":
                out.append(f"<div class='nota'>{e(str(dato))}</div>")
            elif tipo == "ul":
                out.append("<ul>")
                out.extend(f"<li>{e(str(i))}</li>" for i in dato)
                out.append("</ul>")
            elif tipo == "tabla":
                headers, rows = dato
                out.append("<table><thead><tr>")
                out.extend(f"<th>{e(str(h))}</th>" for h in headers)
                out.append("</tr></thead><tbody>")
                for row in rows:
                    out.append("<tr>")
                    out.extend(f"<td>{e(str(c))}</td>" for c in row)
                    out.append("</tr>")
                out.append("</tbody></table>")

    out.append(
        "<div class='pie'>Generado por "
        f"{e(PLUGIN_NAME)} {e(PLUGIN_VERSION)}. "
        "Documento de apoyo a la formulacion; no reemplaza la verificacion "
        "de campo ni el criterio del profesional responsable.</div>"
    )
    out.append("</div></body></html>")
    return "".join(out)


# --------------------------------------------------------------------------
# Word
# --------------------------------------------------------------------------


def render_docx(ctx, path: str, secciones: Optional[list] = None) -> str:
    from .docx_writer import DocxBuilder

    secciones = secciones or build_sections(ctx)
    doc = DocxBuilder()
    doc.title("Informe de planificacion forestal")
    doc.caption(
        f"{PLUGIN_NAME} {PLUGIN_VERSION} - {ctx.started_at or ''}"
    )

    for sec in secciones:
        doc.heading(sec.titulo, sec.nivel)
        for tipo, dato in sec.bloques:
            if tipo == "p":
                doc.paragraph(str(dato))
            elif tipo == "nota":
                doc.paragraph(str(dato), "Caption")
            elif tipo == "ul":
                doc.bullets(dato)
            elif tipo == "tabla":
                headers, rows = dato
                doc.table(headers, rows)

    doc.paragraph(
        "Documento de apoyo a la formulacion. No reemplaza la verificacion de "
        "campo ni el criterio del profesional responsable.",
        "Caption",
    )
    return doc.save(path)


def write_reports(ctx, base_path: str) -> dict:
    """Escribe informe HTML y Word. Devuelve las rutas efectivamente creadas."""
    base, _ = os.path.splitext(base_path)
    secciones = build_sections(ctx)
    salidas = {}

    ruta_html = base + ".html"
    with open(ruta_html, "w", encoding="utf-8") as handle:
        handle.write(render_html(ctx, secciones))
    salidas["html"] = ruta_html

    try:
        salidas["docx"] = render_docx(ctx, base + ".docx", secciones)
    except Exception as exc:  # noqa: BLE001 - el HTML ya quedo escrito
        salidas["docx_error"] = str(exc)

    return salidas
