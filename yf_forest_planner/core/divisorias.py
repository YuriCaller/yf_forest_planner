"""Red de divisorias de microcuenca, para guiar el trazado en llanura.

Es el metodo clasico del trazado vial amazonico y no requiere teoria nueva: se
corre el mismo analisis hidrologico sobre el DEM INVERTIDO. Donde el agua
acumularia en el modelo invertido esta, en el terreno real, la linea de mayor
altura local, es decir la divisoria entre dos microcuencas. Por ahi va el vial:
se evita el drenaje, el suelo hidromorfico y la mayor parte de los cruces.

LIMITE DEL METODO: solo vale en relieve suave. En terreno montanoso la
divisoria pasa por las cumbres, y trazar por lo alto seria absurdo; alli manda
la pendiente y el costo de recorrido. El modulo mide el desnivel del area y
advierte cuando el metodo no corresponde, en lugar de aplicarlo a ciegas.

Validado contra las redes reales de Paujil, PCA 07 y 08, en Madre de Dios:
13.2 y 15.3 m/ha de densidad y sinuosidad de 1.17 y 1.30, coherentes con un
trazado que sigue divisorias y no lineas rectas.

Parte de YF Forest Planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import processing
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingUtils,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType


@dataclass
class RidgeResult:
    capa: Optional[QgsVectorLayer] = None
    raster_invertido: str = ""

    n_tramos: int = 0
    longitud_km: float = 0.0
    densidad_m_ha: float = 0.0
    desnivel_m: float = 0.0
    aplicable: bool = True

    ok: bool = False
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def resumen_lines(self) -> list:
        return [
            f"Divisorias: {self.n_tramos} tramos, {self.longitud_km:.2f} km",
            f"Densidad potencial: {self.densidad_m_ha:.1f} m/ha",
            f"Desnivel del area: {self.desnivel_m:.1f} m",
            f"Metodo aplicable: {'si' if self.aplicable else 'no'}",
        ]


def _alg(nombre: str) -> Optional[str]:
    registro = QgsApplication.processingRegistry()
    for prefijo in ("grass", "grass7"):
        if registro.algorithmById(f"{prefijo}:{nombre}") is not None:
            return f"{prefijo}:{nombre}"
    return None


def _invertir_dem(dem: QgsRasterLayer) -> Optional[str]:
    """Escribe el DEM invertido: cota_max - cota.

    La inversion se hace respecto del maximo y no cambiando el signo, para que
    el resultado siga siendo positivo y r.watershed no tropiece con valores
    negativos ni confunda el nodata.
    """
    from osgeo import gdal

    ds = gdal.Open(dem.source())
    if ds is None:
        return None
    banda = ds.GetRasterBand(1)
    datos = banda.ReadAsArray().astype(np.float32)
    nodata = banda.GetNoDataValue()

    valida = np.isfinite(datos)
    if nodata is not None:
        valida &= datos != nodata
    if not valida.any():
        return None

    techo = float(datos[valida].max())
    invertido = np.where(valida, techo - datos, -9999.0).astype(np.float32)

    salida = QgsProcessingUtils.generateTempFilename("fp_dem_invertido.tif")
    driver = gdal.GetDriverByName("GTiff")
    out = driver.Create(
        salida, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"],
    )
    out.SetGeoTransform(ds.GetGeoTransform())
    out.SetProjection(ds.GetProjection())
    out.GetRasterBand(1).SetNoDataValue(-9999.0)
    out.GetRasterBand(1).WriteArray(invertido)
    out.FlushCache()
    out = None
    ds = None
    return salida


def run(
    dem_layer: QgsRasterLayer,
    crs: QgsCoordinateReferenceSystem,
    umbral_ha: float = 50.0,
    desnivel_max_m: float = 120.0,
    clip_geom: Optional[QgsGeometry] = None,
    area_ha: float = 0.0,
    feedback=None,
) -> RidgeResult:
    """Extrae la red de divisorias corriendo la hidrologia sobre el DEM invertido."""
    result = RidgeResult()

    if dem_layer is None or not dem_layer.isValid():
        result.errores.append("El DEM no es una capa raster valida.")
        return result

    alg_ws, alg_vect = _alg("r.watershed"), _alg("r.to.vect")
    if not alg_ws or not alg_vect:
        result.errores.append("El proveedor GRASS no esta disponible.")
        return result

    estad = dem_layer.dataProvider().bandStatistics(1)
    result.desnivel_m = float(estad.maximumValue - estad.minimumValue)
    result.aplicable = result.desnivel_m <= desnivel_max_m

    if not result.aplicable:
        result.advertencias.append(
            f"El area tiene {result.desnivel_m:.0f} m de desnivel, por encima "
            f"del limite de {desnivel_max_m:.0f} m para el metodo de "
            "divisorias. En relieve montanoso la divisoria pasa por las "
            "cumbres y trazar por lo alto no corresponde: use pendiente y "
            "costo de recorrido como criterio principal."
        )

    invertido = _invertir_dem(dem_layer)
    if not invertido:
        result.errores.append("No fue posible invertir el modelo de elevacion.")
        return result
    result.raster_invertido = invertido

    capa_inv = QgsRasterLayer(invertido, "dem_invertido")
    if not capa_inv.isValid():
        result.errores.append("El DEM invertido no se pudo cargar.")
        return result

    celda = abs(
        dem_layer.rasterUnitsPerPixelX() * dem_layer.rasterUnitsPerPixelY()
    )
    umbral_celdas = max(1, int(round(umbral_ha * 10000.0 / celda)))

    extent = dem_layer.extent()
    region = (
        f"{extent.xMinimum()},{extent.xMaximum()},"
        f"{extent.yMinimum()},{extent.yMaximum()} [{dem_layer.crs().authid()}]"
    )

    try:
        ws = processing.run(
            alg_ws,
            {
                "elevation": capa_inv, "threshold": umbral_celdas, "memory": 1000,
                "-s": False, "-b": False, "-4": False, "-a": False, "-m": False,
                "stream": QgsProcessing.TEMPORARY_OUTPUT,
                "accumulation": QgsProcessing.TEMPORARY_OUTPUT,
                "drainage": QgsProcessing.TEMPORARY_OUTPUT,
                "GRASS_REGION_PARAMETER": region,
                "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                "GRASS_RASTER_FORMAT_OPT": "",
                "GRASS_RASTER_FORMAT_META": "",
            },
            feedback=feedback,
        )
        vect = processing.run(
            alg_vect,
            {
                # El enum es 0=line, 1=point, 2=area.
                "input": ws["stream"], "type": 0, "column": "value",
                "-s": True, "-v": False, "-z": False, "-b": False, "-t": False,
                "output": QgsProcessing.TEMPORARY_OUTPUT,
                "GRASS_REGION_PARAMETER": region,
                "GRASS_OUTPUT_TYPE_PARAMETER": 2,
                "GRASS_VECTOR_DSCO": "", "GRASS_VECTOR_LCO": "",
                "GRASS_VECTOR_EXPORT_NOCAT": False,
            },
            feedback=feedback,
        )
    except Exception as exc:  # noqa: BLE001
        result.errores.append(f"Fallo la extraccion de divisorias: {exc}")
        return result

    crudo = vect.get("output")
    if isinstance(crudo, str):
        crudo = QgsVectorLayer(crudo, "divisorias_crudas", "ogr")
    if crudo is None or not crudo.isValid() or not crudo.featureCount():
        result.errores.append(
            "No se obtuvieron divisorias. Pruebe con un umbral menor."
        )
        return result

    capa = QgsVectorLayer(
        f"LineString?crs={crs.authid()}", "Divisorias de microcuenca", "memory"
    )
    campos = QgsFields()
    campos.append(QgsField("id_divisoria", QMetaType.Type.Int))
    campos.append(QgsField("long_m", QMetaType.Type.Double))
    capa.dataProvider().addAttributes(campos.toList())
    capa.updateFields()

    feats = []
    total = 0.0
    for i, feat in enumerate(crudo.getFeatures(), start=1):
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        # El suavizado va aqui y no antes: la vectorizacion desde raster deja
        # bordes escalonados que en una via se leerian como quiebres.
        suave = geom.smooth(1, 0.25)
        if suave is not None and not suave.isEmpty():
            geom = suave
        if clip_geom is not None and not clip_geom.isEmpty():
            geom = geom.intersection(clip_geom)
            if geom is None or geom.isEmpty():
                continue
        largo = geom.length()
        total += largo
        nuevo = QgsFeature(capa.fields())
        nuevo.setGeometry(geom)
        nuevo.setAttributes([i, round(largo, 2)])
        feats.append(nuevo)

    capa.dataProvider().addFeatures(feats)
    capa.updateExtents()

    result.capa = capa
    result.n_tramos = len(feats)
    result.longitud_km = total / 1000.0
    if area_ha > 0:
        result.densidad_m_ha = total / area_ha
        # Referencias reales: Braz (1997) obtuvo 16 m/ha como densidad optima
        # en Acre; las redes de Paujil, PCA 07 y 08, dan 13.2 y 15.3 m/ha.
        if result.densidad_m_ha > 40.0:
            result.advertencias.append(
                f"La red de divisorias alcanza {result.densidad_m_ha:.0f} m/ha, "
                "muy por encima de los 13 a 16 m/ha que se observan en redes "
                "reales. Suba el umbral de area para quedarse con las "
                "divisorias principales."
            )

    result.ok = True
    return result
