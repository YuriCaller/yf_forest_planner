"""M2 - Humedad, aguajales y capa de confianza.

Combina dos fuentes que fallan de forma distinta y por eso se complementan:

  - El HAND derivado del DEM acierta en la geometria fina pero no distingue
    un aguajal de una quebrada encajonada.
  - El mapa tematico (ZEE, Mapa Nacional de Ecosistemas, cobertura vegetal)
    acierta en el que es pero no en el donde exacto, porque viene de escalas
    del orden de 1:100 000.

La fusion produce una capa de confianza de tres niveles que es informacion
operativa real: le dice al ingeniero donde el trazo automatico es confiable y
donde tiene que caminar antes de firmar.

El HAND se calcula sobre el raster de direccion de drenaje que ya produjo M1,
por salto de punteros en numpy. No usa r.stream.distance, que es addon de
GRASS sin garantia de estar presente.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from osgeo import gdal
from qgis.core import QgsCoordinateReferenceSystem

from ...core.restricciones import (
    RasterTemplate,
    RestrictionSet,
    proximity_decay,
    rasterize_rule,
)

gdal.UseExceptions()

# Codificacion de direccion de r.watershed: 1 a 8 en sentido antihorario
# arrancando en NE. Los valores negativos indican flujo fuera del mapa y el
# cero indica depresion sin salida.
DIRECTION_OFFSETS = {
    1: (-1, 1),
    2: (-1, 0),
    3: (-1, -1),
    4: (0, -1),
    5: (1, -1),
    6: (1, 0),
    7: (1, 1),
    8: (0, 1),
}

# Iteraciones de salto de punteros. Cada una duplica el alcance, asi que 40
# cubre trayectorias de longitud 2^40 celdas con holgura absurda. El limite
# real lo pone la deteccion de no convergencia.
MAX_JUMPS = 40

NIVEL_NINGUNO = 0
NIVEL_SOLO_HAND = 1
NIVEL_SOLO_TEMATICO = 2
NIVEL_AMBOS = 3

NIVEL_LABELS = {
    NIVEL_NINGUNO: "Sin indicio de humedal",
    NIVEL_SOLO_HAND: "Solo terreno: posible depresion menor",
    NIVEL_SOLO_TEMATICO: "Solo mapa tematico: verificar en campo",
    NIVEL_AMBOS: "Coincidencia de terreno y mapa: alta confianza",
}


@dataclass
class WetnessConfig:
    """Parametros de M2."""

    # Por debajo de hand_saturado_m el terreno se considera plenamente
    # inundable; por encima de hand_libre_m, libre. Entre ambos, rampa lineal.
    hand_saturado_m: float = 2.0
    hand_libre_m: float = 8.0

    # TWI es complementario y opcional: en llanura amazonica el HAND ya captura
    # casi toda la senal y el TWI agrega ruido en zonas planas.
    usar_twi: bool = False
    twi_umbral: float = 10.0
    twi_peso: float = 0.3

    # Peso relativo de cada fuente al construir la penalizacion combinada.
    peso_hand: float = 0.6
    peso_tematico: float = 1.0

    # Penalizacion asignada cuando solo una de las dos fuentes marca humedal.
    factor_solo_hand: float = 0.55
    factor_solo_tematico: float = 0.80


@dataclass
class WetnessResult:
    """Salidas de M2."""

    penalizacion_path: Optional[str] = None
    confianza_path: Optional[str] = None
    hand_path: Optional[str] = None
    twi_path: Optional[str] = None

    area_ha_por_nivel: dict = field(default_factory=dict)
    hand_mediana_m: Optional[float] = None
    celdas_sin_hand: int = 0

    ok: bool = False
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# HAND
# --------------------------------------------------------------------------


def _read_band(path: str):
    dataset = gdal.Open(path, gdal.GA_ReadOnly)
    if dataset is None:
        return None, None
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    dataset = None
    return array, nodata


def compute_hand(
    dem: np.ndarray,
    drainage: np.ndarray,
    stream: np.ndarray,
    dem_nodata: Optional[float] = None,
) -> tuple:
    """Altura sobre el drenaje mas cercano siguiendo la direccion de flujo.

    Implementado por salto de punteros: en lugar de recorrer celda por celda
    hasta el cauce, se construye el indice del vecino aguas abajo para toda la
    grilla y se compone ese mapeo consigo mismo. Cada composicion duplica el
    alcance, de modo que el costo es logaritmico en la longitud de la
    trayectoria y no lineal.

    Devuelve el arreglo HAND y el numero de celdas sin resolver.
    """
    height, width = dem.shape
    total = height * width

    flat_index = np.arange(total, dtype=np.int64).reshape(height, width)
    next_index = flat_index.copy()

    rows, cols = np.indices((height, width))

    for code, (drow, dcol) in DIRECTION_OFFSETS.items():
        selection = drainage == code
        if not selection.any():
            continue
        target_r = rows[selection] + drow
        target_c = cols[selection] + dcol
        inside = (
            (target_r >= 0)
            & (target_r < height)
            & (target_c >= 0)
            & (target_c < width)
        )
        source_flat = flat_index[selection]
        valid_source = source_flat[inside]
        next_index.flat[valid_source] = (
            target_r[inside] * width + target_c[inside]
        )

    # Los cauces y las celdas sin dato son puntos fijos del mapeo, pero solo
    # los cauces cuentan como destino valido. Si se mezclan, una celda que
    # drena fuera del area hacia una celda sin dato queda marcada como
    # resuelta y su HAND se calcula contra el valor nodata del DEM, lo que
    # produce alturas de miles de metros que parecen datos legitimos.
    is_stream = stream > 0
    is_nodata = (
        np.zeros(dem.shape, dtype=bool)
        if dem_nodata is None
        else (dem == dem_nodata)
    )
    fixed = is_stream | is_nodata
    next_index[fixed] = flat_index[fixed]

    pointer = next_index.ravel()
    stream_flat = is_stream.ravel()

    for _ in range(MAX_JUMPS):
        updated = pointer[pointer]
        if np.array_equal(updated, pointer):
            pointer = updated
            break
        pointer = updated

    resolved = stream_flat[pointer]
    outlet_z = dem.ravel()[pointer]

    hand = dem.ravel() - outlet_z
    hand[~resolved] = np.nan
    if dem_nodata is not None:
        hand[dem.ravel() == dem_nodata] = np.nan
    hand = np.maximum(hand, 0.0)

    unresolved = int((~resolved).sum())
    return hand.reshape(height, width).astype(np.float32), unresolved


def compute_twi(
    accumulation: np.ndarray,
    dem: np.ndarray,
    cell_size: float,
) -> np.ndarray:
    """Indice topografico de humedad: ln(a / tan(beta))."""
    gy, gx = np.gradient(dem.astype(np.float64), cell_size)
    slope = np.sqrt(gx ** 2 + gy ** 2)
    slope = np.maximum(slope, 0.001)

    specific_area = np.maximum(np.abs(accumulation), 1.0) * cell_size
    return np.log(specific_area / slope).astype(np.float32)


def _ramp(values: np.ndarray, full: float, none: float) -> np.ndarray:
    """Rampa lineal: 1 por debajo de full, 0 por encima de none."""
    if none <= full:
        return (values <= full).astype(np.float32)
    scaled = (none - values) / (none - full)
    return np.clip(scaled, 0.0, 1.0).astype(np.float32)


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def run(
    dem_path: str,
    drainage_path: str,
    stream_path: str,
    accumulation_path: Optional[str],
    restrictions: RestrictionSet,
    config: WetnessConfig,
    crs: QgsCoordinateReferenceSystem,
    workdir: Optional[str] = None,
) -> WetnessResult:
    """Ejecuta M2 sobre las salidas de M1 y la tabla de restricciones."""
    result = WetnessResult()
    workdir = workdir or tempfile.mkdtemp(prefix="fp_m2_")

    template = RasterTemplate.from_path(dem_path)
    if template is None:
        result.errores.append("No fue posible abrir el DEM de trabajo.")
        return result

    dem, dem_nodata = _read_band(dem_path)
    drainage, _ = _read_band(drainage_path)
    stream, _ = _read_band(stream_path)

    if dem is None or drainage is None or stream is None:
        result.errores.append(
            "Faltan salidas de M1. Ejecute primero el modulo de hidrologia."
        )
        return result

    if drainage.shape != dem.shape or stream.shape != dem.shape:
        result.errores.append(
            "Las salidas de M1 no comparten grilla con el DEM. Verifique que "
            "la region de GRASS se haya fijado a la extension del DEM."
        )
        return result

    dem = dem.astype(np.float32)
    hand, unresolved = compute_hand(dem, drainage, stream, dem_nodata)
    result.celdas_sin_hand = unresolved

    if unresolved:
        proporcion = 100.0 * unresolved / hand.size
        result.advertencias.append(
            f"{proporcion:.1f} % de las celdas no alcanzaron un cauce "
            "siguiendo la direccion de flujo. Suele ocurrir en los bordes del "
            "DEM y en depresiones cerradas; esas celdas quedan sin HAND."
        )
        if proporcion > 25.0:
            result.advertencias.append(
                "La proporcion sin resolver es alta. Amplie el recorte del DEM "
                "mas alla del limite de la concesion para que las cuencas "
                "cierren dentro de la extension de analisis."
            )

    valid_hand = hand[np.isfinite(hand)]
    if valid_hand.size:
        result.hand_mediana_m = float(np.median(valid_hand))

    hand_wet = np.where(
        np.isfinite(hand),
        _ramp(hand, config.hand_saturado_m, config.hand_libre_m),
        0.0,
    ).astype(np.float32)

    if config.usar_twi and accumulation_path:
        accumulation, _ = _read_band(accumulation_path)
        if accumulation is not None and accumulation.shape == dem.shape:
            twi = compute_twi(accumulation, dem, template.cell_size)
            twi_wet = _ramp(
                -twi, -config.twi_umbral - 4.0, -config.twi_umbral
            )
            hand_wet = np.clip(
                hand_wet + config.twi_peso * twi_wet, 0.0, 1.0
            ).astype(np.float32)
            result.twi_path = _write(twi, template, workdir, "twi.tif")
        else:
            result.advertencias.append(
                "No se pudo usar el TWI: falta el raster de acumulacion o no "
                "comparte grilla."
            )

    # Fuente tematica: solo las reglas cuyas clases sugieren humedal.
    thematic = np.zeros(dem.shape, dtype=np.float32)
    wetland_rules = restrictions.wetland_rules()
    for rule in wetland_rules:
        mask = rasterize_rule(rule, template, crs, workdir)
        if mask is None:
            result.advertencias.append(
                f"No fue posible rasterizar la restriccion '{rule.display()}'."
            )
            continue
        decayed = proximity_decay(mask, template, rule.uncertainty_m, workdir)
        np.maximum(thematic, decayed * float(rule.weight), out=thematic)

    if not wetland_rules:
        result.advertencias.append(
            "No se declaro ninguna capa tematica de humedales. La deteccion de "
            "aguajales se apoya solo en el terreno, que no distingue un "
            "aguajal de una quebrada encajonada."
        )

    hand_flag = hand_wet >= 0.5
    them_flag = thematic >= 0.5

    confidence = np.zeros(dem.shape, dtype=np.uint8)
    confidence[hand_flag & ~them_flag] = NIVEL_SOLO_HAND
    confidence[~hand_flag & them_flag] = NIVEL_SOLO_TEMATICO
    confidence[hand_flag & them_flag] = NIVEL_AMBOS

    combined = np.maximum(
        hand_wet * config.peso_hand, thematic * config.peso_tematico
    )
    combined = np.where(
        confidence == NIVEL_AMBOS,
        np.maximum(combined, 1.0),
        combined,
    )
    combined = np.where(
        confidence == NIVEL_SOLO_TEMATICO,
        np.maximum(combined, config.factor_solo_tematico),
        combined,
    )
    combined = np.where(
        confidence == NIVEL_SOLO_HAND,
        np.maximum(combined, config.factor_solo_hand),
        combined,
    )
    combined = np.clip(combined, 0.0, 1.0).astype(np.float32)

    cell_ha = (template.cell_size ** 2) / 10000.0
    result.area_ha_por_nivel = {
        nivel: round(float((confidence == nivel).sum()) * cell_ha, 3)
        for nivel in (
            NIVEL_NINGUNO,
            NIVEL_SOLO_HAND,
            NIVEL_SOLO_TEMATICO,
            NIVEL_AMBOS,
        )
    }

    result.hand_path = _write(hand, template, workdir, "hand.tif", nodata=np.nan)
    result.penalizacion_path = _write(
        combined, template, workdir, "humedad_penalizacion.tif"
    )
    result.confianza_path = _write(
        confidence, template, workdir, "humedad_confianza.tif",
        dtype=gdal.GDT_Byte,
    )

    result.ok = True
    return result


def _write(
    array: np.ndarray,
    template: RasterTemplate,
    workdir: str,
    name: str,
    dtype=gdal.GDT_Float32,
    nodata=None,
) -> str:
    path = os.path.join(workdir, name)
    dataset = template.create(path, dtype, nodata=nodata)
    dataset.GetRasterBand(1).WriteArray(array)
    dataset.FlushCache()
    dataset = None
    return path
