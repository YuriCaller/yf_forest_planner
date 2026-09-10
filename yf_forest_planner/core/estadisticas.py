"""Cuadros de resumen para el plan de manejo.

Los cuadros siguen la estructura que pide un Plan Operativo de concesion
maderable: resumen por especie, distribucion diametrica, semilleros, no
aprovechables con su motivo, y resumen por patio.

ADVERTENCIA DE ALCANCE: la estructura se aproxima a lo habitual en un POA,
pero el formato exigible lo fija la autoridad. En Peru rige la Resolucion de
Direccion Ejecutiva 046-2016-SERFOR-DE, que aprueba los lineamientos para la
elaboracion del Plan Operativo para concesiones forestales con fines
maderables. El formulador debe contrastar estos cuadros con el formato vigente
de su jurisdiccion antes de presentarlos.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .censo import normalize_species

# Amplitud de clase diametrica, en centimetros. Diez centimetros es la
# convencion habitual en inventarios forestales tropicales.
CLASE_DIAMETRICA_CM = 10.0

# Proporcion minima de semilleros que suelen exigir los planes de manejo.
# Es un valor de referencia para alertar, NO una constante normativa: el
# porcentaje y su base de calculo dependen del tipo de plan y de la norma
# vigente.
SEMILLEROS_REFERENCIA = 0.20


def _clase(dap_cm: float) -> str:
    if dap_cm <= 0:
        return "sin dato"
    piso = math.floor(dap_cm / CLASE_DIAMETRICA_CM) * CLASE_DIAMETRICA_CM
    return f"{piso:.0f} - {piso + CLASE_DIAMETRICA_CM:.0f}"


def _orden_clase(etiqueta: str) -> float:
    try:
        return float(etiqueta.split(" - ")[0])
    except (ValueError, IndexError):
        return -1.0


# --------------------------------------------------------------------------
# Cuadros
# --------------------------------------------------------------------------


def resumen_por_especie(trees: list, area_ha: float = 0.0) -> tuple:
    """Individuos, volumen y participacion por especie."""
    datos = defaultdict(lambda: {"n": 0, "vol": 0.0, "nombre": "", "daps": []})
    for tree in trees:
        if not getattr(tree, "aprovechable", True):
            continue
        clave = normalize_species(getattr(tree, "especie", "")) or "sin especie"
        registro = datos[clave]
        registro["n"] += 1
        registro["vol"] += float(getattr(tree, "volumen_m3", 0.0) or 0.0)
        registro["nombre"] = registro["nombre"] or (
            getattr(tree, "especie", "") or "sin especie"
        )
        dap = float(getattr(tree, "dap_m", 0.0) or 0.0)
        if dap > 0:
            registro["daps"].append(dap * 100.0)

    total_n = sum(r["n"] for r in datos.values())
    total_v = sum(r["vol"] for r in datos.values())

    filas = []
    for registro in sorted(datos.values(), key=lambda r: -r["vol"]):
        daps = registro["daps"]
        filas.append([
            registro["nombre"],
            registro["n"],
            round(100.0 * registro["n"] / total_n, 2) if total_n else 0.0,
            round(registro["vol"], 3),
            round(100.0 * registro["vol"] / total_v, 2) if total_v else 0.0,
            round(registro["vol"] / registro["n"], 3) if registro["n"] else 0.0,
            round(min(daps), 1) if daps else 0.0,
            round(max(daps), 1) if daps else 0.0,
            round(registro["n"] / area_ha, 4) if area_ha else 0.0,
            round(registro["vol"] / area_ha, 4) if area_ha else 0.0,
        ])

    if filas:
        filas.append([
            "TOTAL", total_n, 100.0, round(total_v, 3), 100.0,
            round(total_v / total_n, 3) if total_n else 0.0, "", "",
            round(total_n / area_ha, 4) if area_ha else 0.0,
            round(total_v / area_ha, 4) if area_ha else 0.0,
        ])

    headers = [
        "Especie", "N arboles", "% arboles", "Volumen (m3)", "% volumen",
        "Vol medio (m3)", "DAP min (cm)", "DAP max (cm)",
        "Arb/ha", "m3/ha",
    ]
    return headers, filas


def distribucion_diametrica(trees: list) -> tuple:
    """Individuos y volumen por clase diametrica y especie."""
    por_clase = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    clases: set = set()
    especies: dict = {}

    for tree in trees:
        if not getattr(tree, "aprovechable", True):
            continue
        dap = float(getattr(tree, "dap_m", 0.0) or 0.0) * 100.0
        etiqueta = _clase(dap)
        clases.add(etiqueta)
        clave = normalize_species(getattr(tree, "especie", "")) or "sin especie"
        especies.setdefault(clave, getattr(tree, "especie", "") or "sin especie")
        registro = por_clase[clave][etiqueta]
        registro[0] += 1
        registro[1] += float(getattr(tree, "volumen_m3", 0.0) or 0.0)

    ordenadas = sorted(clases, key=_orden_clase)
    headers = ["Especie"] + [f"{c} cm" for c in ordenadas] + ["Total"]

    filas = []
    for clave, nombre in sorted(especies.items(), key=lambda kv: kv[1]):
        fila = [nombre]
        total = 0
        for etiqueta in ordenadas:
            n = por_clase[clave][etiqueta][0]
            fila.append(n)
            total += n
        fila.append(total)
        filas.append(fila)

    if filas:
        totales = ["TOTAL"]
        for i in range(1, len(headers)):
            totales.append(sum(f[i] for f in filas))
        filas.append(totales)

    return headers, filas


def cuadro_semilleros(trees: list, todos: list) -> tuple:
    """Semilleros por especie y proporcion frente a los aprovechables.

    La proporcion de semilleros es un requisito de los planes de manejo. El
    umbral aplicable depende del tipo de plan y de la norma vigente, asi que
    aqui solo se reporta el valor obtenido y se compara contra una referencia.
    """
    aprov = defaultdict(int)
    sem = defaultdict(int)
    nombres: dict = {}
    vol_sem = defaultdict(float)

    for tree in todos:
        clave = normalize_species(getattr(tree, "especie", "")) or "sin especie"
        nombres.setdefault(clave, getattr(tree, "especie", "") or "sin especie")
        categoria = str(getattr(tree, "categoria", "") or "").upper()
        es_semillero = categoria.startswith(("SEM", "SEMI"))
        if es_semillero:
            sem[clave] += 1
            vol_sem[clave] += float(getattr(tree, "volumen_m3", 0.0) or 0.0)
        elif getattr(tree, "aprovechable", True):
            aprov[clave] += 1

    filas = []
    for clave, nombre in sorted(nombres.items(), key=lambda kv: kv[1]):
        n_ap = aprov[clave]
        n_sm = sem[clave]
        base = n_ap + n_sm
        filas.append([
            nombre, n_ap, n_sm,
            round(100.0 * n_sm / base, 2) if base else 0.0,
            round(vol_sem[clave], 3),
        ])

    total_ap = sum(aprov.values())
    total_sm = sum(sem.values())
    base = total_ap + total_sm
    if filas:
        filas.append([
            "TOTAL", total_ap, total_sm,
            round(100.0 * total_sm / base, 2) if base else 0.0,
            round(sum(vol_sem.values()), 3),
        ])

    headers = [
        "Especie", "Aprovechables", "Semilleros", "% semilleros",
        "Volumen semilleros (m3)",
    ]
    return headers, filas


def cuadro_no_aprovechables(trees: list) -> tuple:
    """Individuos excluidos del aprovechamiento, con el motivo."""
    filas = []
    for tree in trees:
        motivos = []
        if getattr(tree, "bajo_dmc", False):
            dmc = getattr(tree, "dmc_aplicado", 0.0)
            motivos.append(f"bajo DMC ({dmc:.0f} cm)")
        categoria = str(getattr(tree, "categoria", "") or "").upper()
        if categoria.startswith(("SEM", "SEMI")):
            motivos.append("semillero")
        if getattr(tree, "en_faja", False):
            motivos.append("dentro de faja marginal")
        if not motivos and not getattr(tree, "aprovechable", True):
            motivos.append("categoria no aprovechable")
        if not motivos:
            continue
        filas.append([
            str(getattr(tree, "codigo", "") or ""),
            getattr(tree, "especie", "") or "",
            round(float(getattr(tree, "dap_m", 0.0) or 0.0) * 100.0, 1),
            round(float(getattr(tree, "altura_m", 0.0) or 0.0), 1),
            round(float(getattr(tree, "volumen_m3", 0.0) or 0.0), 3),
            "; ".join(motivos),
            round(float(getattr(tree, "x", 0.0)), 2),
            round(float(getattr(tree, "y", 0.0)), 2),
        ])

    headers = [
        "Codigo", "Especie", "DAP (cm)", "Hc (m)", "Volumen (m3)",
        "Motivo de exclusion", "Este", "Norte",
    ]
    return headers, filas


def cuadro_por_patio(yards: list) -> tuple:
    """Resumen de cada patio con sus especies."""
    filas = []
    for yard in yards:
        especies = defaultdict(float)
        for tree in yard.trees:
            especies[getattr(tree, "especie", "") or "sin especie"] += float(
                getattr(tree, "volumen_m3", 0.0) or 0.0
            )
        principales = sorted(especies.items(), key=lambda kv: -kv[1])[:3]
        filas.append([
            yard.yid,
            round(yard.x, 2),
            round(yard.y, 2),
            len(yard.trees),
            round(yard.volumen_m3, 3),
            yard.n_piezas_grandes,
            len(especies),
            "; ".join(f"{sp} ({v:.1f})" for sp, v in principales),
            round(yard.dist_media, 1),
            round(yard.dist_max, 1),
            yard.centro_id,
            round(yard.dist_centro, 1),
        ])

    headers = [
        "Patio", "Este", "Norte", "N arboles", "Volumen (m3)",
        "Piezas grandes", "N especies", "Especies principales (m3)",
        "Arrastre medio (m)", "Arrastre max (m)", "Centro", "Saca (m)",
    ]
    return headers, filas


def cuadro_arboles(trees: list) -> tuple:
    """Padron de individuos a aprovechar, con su patio de destino."""
    filas = []
    for tree in trees:
        if not getattr(tree, "aprovechable", True):
            continue
        filas.append([
            str(getattr(tree, "codigo", "") or ""),
            getattr(tree, "especie", "") or "",
            getattr(tree, "parcela", "") or "",
            round(float(getattr(tree, "dap_m", 0.0) or 0.0) * 100.0, 1),
            round(float(getattr(tree, "altura_m", 0.0) or 0.0), 1),
            round(float(getattr(tree, "volumen_m3", 0.0) or 0.0), 3),
            round(float(getattr(tree, "x", 0.0)), 2),
            round(float(getattr(tree, "y", 0.0)), 2),
            int(getattr(tree, "patio_id", 0) or 0),
            round(float(getattr(tree, "dist_arrastre", 0.0) or 0.0), 1),
        ])

    headers = [
        "Codigo", "Especie", "Bloque/parcela", "DAP (cm)", "Hc (m)",
        "Volumen (m3)", "Este", "Norte", "Patio", "Arrastre (m)",
    ]
    return headers, filas


# --------------------------------------------------------------------------
# Libro completo
# --------------------------------------------------------------------------


def build_workbook(ctx, path: str) -> str:
    """Escribe el libro de cuadros a partir del contexto."""
    from .constants import M3_PATIOS, PLUGIN_NAME, PLUGIN_VERSION
    from .xlsx_writer import Workbook

    wb = Workbook()
    trees = list(ctx.trees or [])
    area = float(ctx.pca_area_ha or 0.0)

    # --- portada
    portada = wb.sheet("Resumen", ["Concepto", "Valor"], [42, 30])
    portada.add(["Complemento", f"{PLUGIN_NAME} {PLUGIN_VERSION}"])
    portada.add(["Ejecutado", ctx.started_at or ""])
    portada.add(["Sistema de referencia", ctx.crs.authid() if ctx.crs else ""])
    portada.add(["Area de trabajo (ha)", round(area, 4)])
    portada.add(["Semilla aleatoria", int(ctx.random_seed)])
    portada.add(["", ""])

    rep = ctx.censo_report
    if rep is not None:
        portada.add(["Individuos leidos", int(getattr(rep, "total", 0))])
        portada.add(["Registros validos", int(getattr(rep, "validos", 0))])
        portada.add(["Aprovechables", int(getattr(rep, "aprovechables", 0))])
        portada.add([
            "Volumen aprovechable (m3)",
            round(float(getattr(rep, "volumen_aprovechable", 0.0)), 3),
        ])
        if getattr(rep, "n_bajo_dmc", 0):
            portada.add(["Excluidos por DMC", int(rep.n_bajo_dmc)])
            portada.add([
                "Volumen excluido por DMC (m3)",
                round(float(getattr(rep, "volumen_bajo_dmc", 0.0)), 3),
            ])
        if getattr(rep, "n_excluidos_especie", 0):
            portada.add(["Excluidos por especie", int(rep.n_excluidos_especie)])

    m3 = ctx.result(M3_PATIOS)
    if m3 is not None and m3.metrics:
        portada.add(["", ""])
        for clave, valor in m3.metrics.items():
            portada.add([clave.replace("_", " "), valor])

    portada.add(["", ""])
    portada.add([
        "ALCANCE",
        "Propuesta tecnica sujeta a verificacion de campo. Contraste el "
        "formato de los cuadros con la norma vigente de su jurisdiccion.",
    ])

    # --- cuadros
    headers, filas = resumen_por_especie(trees, area)
    wb.sheet("Por especie", headers, [26, 11, 10, 14, 11, 13, 12, 12, 10, 10]).extend(filas)

    headers, filas = distribucion_diametrica(trees)
    if filas:
        wb.sheet("Distribucion diametrica", headers).extend(filas)

    headers, filas = cuadro_semilleros(trees, trees)
    if filas:
        hoja = wb.sheet("Semilleros", headers, [26, 14, 12, 13, 20])
        hoja.extend(filas)
        total = filas[-1]
        if isinstance(total[3], float) and total[3] < SEMILLEROS_REFERENCIA * 100:
            hoja.add(["", "", "", "", ""])
            hoja.add([
                f"AVISO: la proporcion de semilleros es {total[3]:.1f}%, por "
                f"debajo del {SEMILLEROS_REFERENCIA * 100:.0f}% de referencia. "
                "Verifique el umbral aplicable a su tipo de plan.",
                "", "", "", "",
            ])

    headers, filas = cuadro_no_aprovechables(trees)
    if filas:
        wb.sheet("No aprovechables", headers, [12, 24, 11, 9, 12, 34, 13, 13]).extend(filas)

    if m3 is not None:
        yards = getattr(ctx, "yards", None) or []
        if yards:
            headers, filas = cuadro_por_patio(yards)
            wb.sheet("Por patio", headers,
                     [8, 13, 13, 11, 13, 13, 11, 40, 15, 14, 9, 11]).extend(filas)

    impacto = getattr(ctx, "impacto", None)
    if impacto is not None and getattr(impacto, "ok", False):
        headers, filas = impacto.tabla_impacto()
        wb.sheet("Areas de impacto", headers, [26, 34, 12, 12, 14]).extend(filas)
        headers, filas = impacto.tabla_volumen()
        if filas:
            wb.sheet("Balance de volumen", headers, [32, 16, 14]).extend(filas)

    intens = getattr(ctx, "intensidad", None)
    if intens is not None and getattr(intens, "ok", False):
        hoja = wb.sheet("Intensidad", ["Concepto", "Valor"], [46, 18])
        hoja.add(["Celdas con aprovechamiento", int(intens.celdas_con_arboles)])
        hoja.add(["Area efectiva (ha)", round(intens.area_efectiva_ha, 2)])
        hoja.add(["Densidad media (arb/ha)", round(intens.densidad_media, 3)])
        hoja.add(["Densidad maxima (arb/ha)", round(intens.densidad_max, 1)])
        hoja.add(["Intensidad media (m3/ha)", round(intens.intensidad_media, 3)])
        hoja.add(["Intensidad maxima (m3/ha)", round(intens.intensidad_max, 3)])
        if intens.celdas_sobre_volumen:
            hoja.add(["Celdas sobre el limite de volumen", int(intens.celdas_sobre_volumen)])
            hoja.add(["Volumen en exceso (m3)", round(intens.volumen_en_exceso, 2)])
        if intens.celdas_sobre_arboles:
            hoja.add(["Celdas sobre el limite de individuos", int(intens.celdas_sobre_arboles)])
        hoja.add(["", ""])
        for adv in intens.advertencias:
            hoja.add(["OBSERVACION", adv])

        if intens.abundancia_por_especie and not intens.abundancia_no_concluyente:
            filas = [
                [sp, int(n), round(ab, 4)]
                for sp, (n, ab) in sorted(
                    intens.abundancia_por_especie.items(), key=lambda kv: -kv[1][1]
                )
            ]
            wb.sheet("Abundancia", ["Especie", "Individuos", "Ind/ha"],
                     [26, 14, 12]).extend(filas)

    transp = getattr(ctx, "transporte", None)
    if transp is not None and getattr(transp, "ok", False):
        hoja = wb.sheet("Transporte", ["Concepto", "Valor"], [42, 20])
        for etiqueta, valor in (
            ("Volumen de censo (m3)", round(transp.volumen_censo, 3)),
            ("Volumen rollizo estimado (m3)", round(transp.volumen_rollizo, 3)),
            ("Merma (m3)", round(transp.merma_m3, 3)),
            ("Merma (%)", round(transp.merma_pct, 2)),
            ("Peso total (t)", round(transp.peso_total_t, 2)),
            ("Viajes estimados", int(transp.viajes_total)),
            ("Limite dominante", transp.limite_dominante),
            ("Referencia aserrio (m3)", round(transp.volumen_aserrado_ref, 3)),
        ):
            hoja.add([etiqueta, valor])
        hoja.add(["", ""])
        for adv in transp.advertencias:
            hoja.add(["OBSERVACION", adv])

        headers, filas = transp.tabla_especies()
        wb.sheet("Viajes por especie", headers,
                 [24, 10, 14, 12, 15, 12, 9, 10]).extend(filas)
        headers, filas = transp.tabla_centros()
        if filas:
            wb.sheet("Viajes por centro", headers, [10, 15, 16, 12, 10]).extend(filas)

    headers, filas = cuadro_arboles(trees)
    if filas:
        wb.sheet("Padron de arboles", headers,
                 [12, 24, 14, 11, 9, 12, 13, 13, 8, 13]).extend(filas)

    return wb.save(path)
