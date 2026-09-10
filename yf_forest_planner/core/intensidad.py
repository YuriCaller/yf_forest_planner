"""Densidad e intensidad de aprovechamiento sobre malla de una hectarea.

El promedio del area no basta como control. Un predio con 4.7 m3/ha de media
puede tener hectareas con 40, y es esa concentracion la que abre claros
grandes y la que la norma limita. Por eso el control se hace celda por celda.

La malla de 1 ha no es arbitraria: es la unidad con la que la literatura y las
normas expresan los limites de intensidad, y es directamente interpretable
("esta hectarea tiene 4 arboles y 28 m3") frente a una densidad kernel, que se
ve mejor pero no se puede contrastar contra un umbral.

Los limites varian por pais y no se embeben: se cargan desde un preset o los
fija el usuario. Cero desactiva cada verificacion.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .censo import normalize_species

# Lado de la celda en metros para una malla de 1 ha.
LADO_HECTAREA_M = 100.0


@dataclass
class IntensityConfig:
    """Limites de intensidad y umbrales de la malla."""

    celda_m: float = LADO_HECTAREA_M

    # Volumen maximo aprovechable por hectarea. Brasil admite hasta 30 m3/ha
    # con maquinaria pesada y 10 m3/ha en planes de bajo impacto sin
    # maquinaria; Ecuador reporta extracciones de 28 a 32 m3/ha.
    max_volumen_ha: float = 0.0

    # Numero maximo de individuos por hectarea. Ecuador fija entre 8 y 12
    # arboles por hectarea segun el tipo de bosque.
    max_arboles_ha: float = 0.0

    # Porcentaje maximo del area basal removible. Ecuador limita al 30% del
    # area basal de los arboles con DAP igual o mayor a 30 cm.
    max_area_basal_pct: float = 0.0
    dap_base_ab_cm: float = 30.0

    # Abundancia minima para que una especie pueda proponerse. Bolivia excluye
    # de la canasta las especies con abundancia menor a 0.25 por hectarea para
    # arboles con DAP igual o mayor a 20 cm.
    min_abundancia_ha: float = 0.0
    dap_abundancia_cm: float = 20.0

    # Un censo comercial solo registra las especies de interes por encima del
    # DMC. Contra esa base, el area basal removida y la abundancia por especie
    # no significan lo que la norma pide: el denominador no es el rodal sino
    # las mismas especies que se van a extraer. Ambos criterios exigen el
    # inventario estadistico. Con esta bandera activa se calculan igual pero se
    # informan como no concluyentes en lugar de como incumplimiento.
    es_censo_comercial: bool = True

    def alguna_activa(self) -> bool:
        return any([
            self.max_volumen_ha > 0,
            self.max_arboles_ha > 0,
            self.max_area_basal_pct > 0,
            self.min_abundancia_ha > 0,
        ])


@dataclass
class IntensityResult:
    densidad_path: str = ""
    intensidad_path: str = ""
    excedencia_path: str = ""

    celdas_con_arboles: int = 0
    area_efectiva_ha: float = 0.0

    densidad_media: float = 0.0
    densidad_max: float = 0.0
    intensidad_media: float = 0.0
    intensidad_max: float = 0.0

    celdas_sobre_volumen: int = 0
    celdas_sobre_arboles: int = 0
    volumen_en_exceso: float = 0.0

    area_basal_total: float = 0.0
    area_basal_removida: float = 0.0
    pct_area_basal: float = 0.0

    pct_area_basal_no_concluyente: bool = False
    abundancia_no_concluyente: bool = False
    especies_baja_abundancia: list = field(default_factory=list)
    abundancia_por_especie: dict = field(default_factory=dict)

    ok: bool = False
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def resumen_lines(self) -> list:
        lines = [
            f"Celdas con aprovechamiento: {self.celdas_con_arboles:,d} "
            f"({self.area_efectiva_ha:,.1f} ha efectivas)",
            f"Densidad: {self.densidad_media:.2f} arb/ha media, "
            f"{self.densidad_max:.0f} maxima",
            f"Intensidad: {self.intensidad_media:.2f} m3/ha media, "
            f"{self.intensidad_max:.2f} maxima",
        ]
        if self.pct_area_basal and not self.pct_area_basal_no_concluyente:
            lines.append(
                f"Area basal removida: {self.pct_area_basal:.2f}% "
                f"({self.area_basal_removida:.3f} de {self.area_basal_total:.3f} m2)"
            )
        elif self.pct_area_basal_no_concluyente:
            lines.append(
                "Area basal: no verificable con censo comercial "
                "(requiere inventario estadistico)"
            )
        if self.celdas_sobre_volumen:
            lines.append(
                f"Celdas sobre el limite de volumen: {self.celdas_sobre_volumen} "
                f"({self.volumen_en_exceso:,.2f} m3 en exceso)"
            )
        if self.celdas_sobre_arboles:
            lines.append(
                f"Celdas sobre el limite de individuos: {self.celdas_sobre_arboles}"
            )
        return lines


# --------------------------------------------------------------------------
# Malla
# --------------------------------------------------------------------------


def _escribir_raster(path, datos, origen, celda, crs_wkt, nodata=-9999.0):
    from osgeo import gdal

    filas, cols = datos.shape
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, cols, filas, 1, gdal.GDT_Float32,
                       options=["COMPRESS=DEFLATE", "TILED=YES"])
    # El origen de la geotransformacion es la esquina superior izquierda y el
    # paso en Y es negativo: la fila 0 corresponde al norte del area.
    ds.SetGeoTransform((origen[0], celda, 0.0, origen[1], 0.0, -celda))
    if crs_wkt:
        ds.SetProjection(crs_wkt)
    banda = ds.GetRasterBand(1)
    banda.SetNoDataValue(nodata)
    banda.WriteArray(np.where(np.isfinite(datos), datos, nodata))
    ds.FlushCache()
    ds = None
    return path


def build_grid(trees: list, config: IntensityConfig) -> tuple:
    """Acumula individuos, volumen y area basal por celda.

    Devuelve (conteo, volumen, area_basal, origen, celda).
    """
    usables = [
        t for t in trees
        if getattr(t, "aprovechable", True)
        and float(getattr(t, "volumen_m3", 0.0) or 0.0) > 0
    ]
    if not usables:
        return None, None, None, None, config.celda_m

    xs = np.array([float(t.x) for t in usables])
    ys = np.array([float(t.y) for t in usables])
    vols = np.array([float(t.volumen_m3) for t in usables])
    daps = np.array([float(getattr(t, "dap_m", 0.0) or 0.0) for t in usables])

    celda = max(1.0, config.celda_m)
    # La malla se alinea a multiplos de la celda para que dos corridas sobre el
    # mismo predio produzcan la misma cuadricula y los conteos sean comparables.
    x0 = math.floor(xs.min() / celda) * celda
    y1 = math.ceil(ys.max() / celda) * celda
    x1 = math.ceil(xs.max() / celda) * celda
    y0 = math.floor(ys.min() / celda) * celda

    cols = max(1, int(round((x1 - x0) / celda)))
    filas = max(1, int(round((y1 - y0) / celda)))

    ci = np.clip(((xs - x0) / celda).astype(int), 0, cols - 1)
    fi = np.clip(((y1 - ys) / celda).astype(int), 0, filas - 1)
    plano = fi * cols + ci

    conteo = np.bincount(plano, minlength=filas * cols).astype(np.float32)
    volumen = np.bincount(plano, weights=vols, minlength=filas * cols).astype(np.float32)
    ab = np.bincount(
        plano, weights=(math.pi / 4.0) * daps ** 2, minlength=filas * cols
    ).astype(np.float32)

    return (
        conteo.reshape(filas, cols),
        volumen.reshape(filas, cols),
        ab.reshape(filas, cols),
        (x0, y1),
        celda,
    )


def abundancia_por_especie(trees: list, area_ha: float, config: IntensityConfig) -> dict:
    """Individuos por hectarea de cada especie, sobre el DAP de referencia.

    La abundancia se calcula sobre TODOS los individuos censados por encima
    del DAP de referencia, no solo sobre los aprovechables: el criterio mide
    la presencia de la especie en el rodal, no cuanto se piensa extraer.
    """
    if area_ha <= 0:
        return {}
    conteo = defaultdict(int)
    nombres: dict = {}
    umbral_m = config.dap_abundancia_cm / 100.0

    for tree in trees:
        dap = float(getattr(tree, "dap_m", 0.0) or 0.0)
        if dap < umbral_m:
            continue
        clave = normalize_species(getattr(tree, "especie", "")) or "sin especie"
        nombres.setdefault(clave, getattr(tree, "especie", "") or "sin especie")
        conteo[clave] += 1

    return {
        nombres[c]: (n, n / area_ha) for c, n in conteo.items()
    }


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def run(
    trees: list,
    config: IntensityConfig,
    area_ha: float = 0.0,
    crs_wkt: str = "",
    salida_base: str = "",
) -> IntensityResult:
    """Construye la malla, escribe los rasters y verifica los limites."""
    result = IntensityResult()

    conteo, volumen, ab, origen, celda = build_grid(trees, config)
    if conteo is None:
        result.errores.append("No hay individuos aprovechables con volumen.")
        return result

    factor = (celda * celda) / 10000.0  # hectareas por celda
    dens = conteo / factor
    inten = volumen / factor

    ocupadas = conteo > 0
    result.celdas_con_arboles = int(ocupadas.sum())
    result.area_efectiva_ha = float(result.celdas_con_arboles * factor)
    result.densidad_media = float(dens[ocupadas].mean()) if ocupadas.any() else 0.0
    result.densidad_max = float(dens.max())
    result.intensidad_media = float(inten[ocupadas].mean()) if ocupadas.any() else 0.0
    result.intensidad_max = float(inten.max())

    # --- excedencia por celda
    excedencia = np.zeros_like(inten)
    if config.max_volumen_ha > 0:
        exceso = np.clip(inten - config.max_volumen_ha, 0.0, None)
        result.celdas_sobre_volumen = int((exceso > 0).sum())
        result.volumen_en_exceso = float((exceso * factor).sum())
        excedencia = np.maximum(excedencia, exceso / config.max_volumen_ha)
        if result.celdas_sobre_volumen:
            result.advertencias.append(
                f"{result.celdas_sobre_volumen} hectareas superan el limite de "
                f"{config.max_volumen_ha:.0f} m3/ha, con "
                f"{result.volumen_en_exceso:,.2f} m3 en exceso. El promedio del "
                "predio puede cumplir y aun asi concentrarse el aprovechamiento."
            )

    if config.max_arboles_ha > 0:
        exceso_n = np.clip(dens - config.max_arboles_ha, 0.0, None)
        result.celdas_sobre_arboles = int((exceso_n > 0).sum())
        excedencia = np.maximum(excedencia, exceso_n / config.max_arboles_ha)
        if result.celdas_sobre_arboles:
            result.advertencias.append(
                f"{result.celdas_sobre_arboles} hectareas superan el limite de "
                f"{config.max_arboles_ha:.0f} individuos por hectarea."
            )

    # --- area basal
    if config.max_area_basal_pct > 0:
        umbral_m = config.dap_base_ab_cm / 100.0
        total = 0.0
        removida = 0.0
        for tree in trees:
            dap = float(getattr(tree, "dap_m", 0.0) or 0.0)
            if dap < umbral_m:
                continue
            valor = (math.pi / 4.0) * dap ** 2
            total += valor
            if getattr(tree, "aprovechable", True):
                removida += valor
        result.area_basal_total = total
        result.area_basal_removida = removida
        result.pct_area_basal = 100.0 * removida / total if total else 0.0
        if config.es_censo_comercial:
            result.pct_area_basal_no_concluyente = True
            result.advertencias.append(
                "El porcentaje de area basal removida NO es concluyente: se "
                "calculo sobre un censo comercial, donde el denominador son "
                "solo las especies de interes por encima del DMC y no el rodal "
                "completo. Para verificar este criterio hace falta el "
                "inventario estadistico."
            )
        elif result.pct_area_basal > config.max_area_basal_pct:
            result.advertencias.append(
                f"La remocion de area basal es {result.pct_area_basal:.1f}%, "
                f"por encima del {config.max_area_basal_pct:.0f}% admitido "
                f"para arboles con DAP mayor o igual a {config.dap_base_ab_cm:.0f} cm."
            )

    # --- abundancia por especie
    if config.min_abundancia_ha > 0 and area_ha > 0:
        result.abundancia_por_especie = abundancia_por_especie(trees, area_ha, config)
        bajas = [
            (sp, n, ab_ha)
            for sp, (n, ab_ha) in result.abundancia_por_especie.items()
            if ab_ha < config.min_abundancia_ha
        ]
        result.especies_baja_abundancia = sorted(bajas, key=lambda x: x[2])
        if bajas and config.es_censo_comercial:
            result.abundancia_no_concluyente = True
            result.especies_baja_abundancia = []
            result.advertencias.append(
                "La abundancia por especie NO es concluyente sobre un censo "
                "comercial: solo registra individuos por encima del DMC de las "
                "especies de interes, asi que toda especie parece escasa. El "
                "criterio de abundancia minima exige el inventario estadistico, "
                "que censa desde un DAP menor y sobre todas las especies."
            )
        elif bajas:
            detalle = ", ".join(
                f"{sp} ({ab_ha:.3f}/ha)" for sp, _, ab_ha in result.especies_baja_abundancia[:6]
            )
            result.advertencias.append(
                f"{len(bajas)} especies tienen abundancia menor a "
                f"{config.min_abundancia_ha:.2f} individuos por hectarea sobre "
                f"{config.dap_abundancia_cm:.0f} cm de DAP: {detalle}. Algunas "
                "normas impiden proponerlas en la canasta de aprovechamiento."
            )

    # --- rasters
    if salida_base:
        try:
            result.densidad_path = _escribir_raster(
                f"{salida_base}_densidad.tif", dens, origen, celda, crs_wkt
            )
            result.intensidad_path = _escribir_raster(
                f"{salida_base}_intensidad.tif", inten, origen, celda, crs_wkt
            )
            if config.max_volumen_ha > 0 or config.max_arboles_ha > 0:
                result.excedencia_path = _escribir_raster(
                    f"{salida_base}_excedencia.tif", excedencia, origen, celda, crs_wkt
                )
        except Exception as exc:  # noqa: BLE001
            result.advertencias.append(f"No se pudieron escribir los rasters: {exc}")

    result.ok = True
    return result
