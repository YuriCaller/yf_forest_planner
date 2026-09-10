"""Manejo de sistemas de referencia para Forest Planner.

Todo el analisis posterior trabaja en distancias y areas metricas: agrupamiento
de patios, distancia de arrastre, superficie de costo, anchos de faja y calculo
de impacto. Un SRC geografico produce resultados plausibles y equivocados, asi
que aqui se bloquea antes de que llegue a los modulos.

La reproyeccion se resuelve una sola vez en este modulo. Aguas abajo nadie
vuelve a transformar coordenadas, de modo que el hash de trazabilidad se
calcula sobre las mismas coordenadas que uso el analisis.

Parte de YF Forest Planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)

CRS_WGS84 = "EPSG:4326"

# Zonas UTM que cubren territorio peruano continental.
PERU_UTM_ZONES = (17, 18, 19)


def _meters_unit():
    """Devuelve el enumerador de metros compatible con QGIS 3.28 a 4.x.

    En QGIS 3.30 la enumeracion migro a Qgis.DistanceUnit.Meters; las versiones
    anteriores solo exponen QgsUnitTypes.DistanceUnit.DistanceMeters. Se
    resuelve por atributo para no romper en ninguna de las dos ramas.
    """
    try:
        from qgis.core import Qgis

        return Qgis.DistanceUnit.Meters
    except (ImportError, AttributeError):
        return QgsUnitTypes.DistanceUnit.DistanceMeters


# --------------------------------------------------------------------------
# Validacion
# --------------------------------------------------------------------------


@dataclass
class CrsValidation:
    """Resultado de validar un SRC como sistema de trabajo."""

    crs: Optional[QgsCoordinateReferenceSystem] = None
    valido: bool = False
    bloqueante: bool = False
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    sugerencia: Optional[QgsCoordinateReferenceSystem] = None

    def mensaje(self) -> str:
        partes = list(self.errores) + list(self.advertencias)
        return "\n".join(partes)


def validate_working_crs(
    crs: QgsCoordinateReferenceSystem,
    extent_wgs84: Optional[QgsRectangle] = None,
) -> CrsValidation:
    """Valida que un SRC sirva como sistema de trabajo metrico.

    extent_wgs84 es opcional: si se entrega, se contrasta la zona UTM del SRC
    contra la extension real de los datos y se sugiere la zona correcta.
    """
    result = CrsValidation(crs=crs)

    if crs is None or not crs.isValid():
        result.errores.append("El sistema de referencia no es valido.")
        result.bloqueante = True
        return result

    if crs.isGeographic():
        result.errores.append(
            "El sistema de trabajo es geografico (grados). Forest Planner "
            "requiere un SRC proyectado en metros: todas las distancias, areas "
            "y superficies de costo se calculan en unidades lineales."
        )
        result.bloqueante = True
        if extent_wgs84 is not None:
            result.sugerencia = suggest_utm_crs(extent_wgs84)
        return result

    if crs.mapUnits() != _meters_unit():
        result.errores.append(
            "El sistema de trabajo no usa metros como unidad de mapa. "
            "Seleccione un SRC proyectado metrico, por ejemplo UTM."
        )
        result.bloqueante = True
        if extent_wgs84 is not None:
            result.sugerencia = suggest_utm_crs(extent_wgs84)
        return result

    if extent_wgs84 is not None and not extent_wgs84.isEmpty():
        sugerido = suggest_utm_crs(extent_wgs84)
        if sugerido is not None and sugerido.authid() != crs.authid():
            result.sugerencia = sugerido
            result.advertencias.append(
                f"El SRC seleccionado ({crs.authid()}) no corresponde a la zona "
                f"UTM de los datos. Se sugiere {sugerido.authid()}. Trabajar "
                "fuera de la zona aumenta la distorsion de distancias y areas."
            )
        if crosses_utm_zones(extent_wgs84):
            result.advertencias.append(
                "Los datos cruzan mas de una zona UTM. El analisis se ejecutara "
                "por completo en el SRC seleccionado; verifique que la "
                "distorsion sea aceptable para el area de trabajo."
            )

    result.valido = True
    return result


def utm_zone_from_longitude(longitude: float) -> int:
    """Zona UTM que contiene una longitud dada."""
    zone = int((longitude + 180.0) / 6.0) + 1
    return max(1, min(60, zone))


def suggest_utm_crs(
    extent_wgs84: QgsRectangle,
) -> Optional[QgsCoordinateReferenceSystem]:
    """Sugiere el SRC UTM WGS84 correspondiente al centro de la extension."""
    if extent_wgs84 is None or extent_wgs84.isEmpty():
        return None

    center = extent_wgs84.center()
    zone = utm_zone_from_longitude(center.x())
    hemisphere = 700 if center.y() < 0 else 600
    epsg = 32000 + hemisphere + zone

    crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
    return crs if crs.isValid() else None


def crosses_utm_zones(extent_wgs84: QgsRectangle) -> bool:
    """True si la extension abarca mas de una zona UTM."""
    if extent_wgs84 is None or extent_wgs84.isEmpty():
        return False
    return utm_zone_from_longitude(
        extent_wgs84.xMinimum()
    ) != utm_zone_from_longitude(extent_wgs84.xMaximum())


# --------------------------------------------------------------------------
# Transformacion
# --------------------------------------------------------------------------


def transform_context() -> QgsCoordinateTransformContext:
    return QgsProject.instance().transformContext()


def layer_extent_wgs84(layer) -> Optional[QgsRectangle]:
    """Extension de una capa expresada en WGS84, para inferir la zona UTM."""
    if layer is None or not layer.isValid():
        return None

    source_crs = layer.crs()
    if not source_crs.isValid():
        return None

    extent = layer.extent()
    if extent.isEmpty():
        return None

    target = QgsCoordinateReferenceSystem(CRS_WGS84)
    if source_crs.authid() == target.authid():
        return extent

    transform = QgsCoordinateTransform(source_crs, target, transform_context())
    return transform.transformBoundingBox(extent)


def reproject_vector(
    layer: QgsVectorLayer,
    target_crs: QgsCoordinateReferenceSystem,
    only_selected: bool = False,
    layer_name: str = "reproyectada",
) -> Optional[QgsVectorLayer]:
    """Reproyecta una capa vectorial a una capa en memoria en el SRC destino.

    Conserva todos los atributos. Si la capa ya esta en el SRC destino y no se
    filtra por seleccion, devuelve la capa original sin copiarla.
    """
    if layer is None or not layer.isValid() or target_crs is None:
        return None

    same_crs = layer.crs().authid() == target_crs.authid()
    if same_crs and not only_selected:
        return layer

    geom_type = QgsWkbTypes.displayString(layer.wkbType())
    uri = f"{geom_type}?crs={target_crs.authid()}"
    mem = QgsVectorLayer(uri, layer_name, "memory")
    if not mem.isValid():
        return None

    provider = mem.dataProvider()
    provider.addAttributes(layer.fields().toList())
    mem.updateFields()

    transform = None
    if not same_crs:
        transform = QgsCoordinateTransform(
            layer.crs(), target_crs, transform_context()
        )

    source = (
        layer.getSelectedFeatures() if only_selected else layer.getFeatures()
    )

    buffer: list[QgsFeature] = []
    for feat in source:
        geom = feat.geometry()
        if geom is None or geom.isEmpty() or geom.isNull():
            continue
        if transform is not None:
            geom = QgsGeometry(geom)
            if geom.transform(transform) != 0:
                continue

        new_feat = QgsFeature(mem.fields())
        new_feat.setGeometry(geom)
        new_feat.setAttributes(feat.attributes())
        buffer.append(new_feat)

        if len(buffer) >= 5000:
            provider.addFeatures(buffer)
            buffer.clear()

    if buffer:
        provider.addFeatures(buffer)

    mem.updateExtents()
    return mem


def describe(crs: QgsCoordinateReferenceSystem) -> str:
    """Descripcion compacta de un SRC para el informe y el panel."""
    if crs is None or not crs.isValid():
        return "SRC no definido"
    return f"{crs.description()} ({crs.authid()})"


def reproject_raster(
    layer,
    target_crs: QgsCoordinateReferenceSystem,
    resampling: int = 1,
    feedback=None,
):
    """Reproyecta un raster al SRC destino conservando su resolucion nativa.

    Devuelve la capa original si ya esta en el SRC destino. El remuestreo por
    defecto es bilineal: el cubico introduce sobreoscilacion cerca de los
    cambios bruscos de pendiente y crea depresiones artificiales, que es
    justamente lo que no debe hacerse antes de un analisis de flujo.

    La resolucion destino se estima como la diagonal de una celda medida en el
    SRC destino, para no inventar detalle ni degradar el dato.
    """
    import math

    import processing
    from qgis.core import QgsProcessing, QgsRasterLayer

    if layer is None or not layer.isValid() or target_crs is None:
        return None
    if layer.crs().authid() == target_crs.authid():
        return layer

    extent = layer.extent()
    transform = QgsCoordinateTransform(
        layer.crs(), target_crs, transform_context()
    )
    projected = transform.transformBoundingBox(extent)

    res_x = projected.width() / max(1, layer.width())
    res_y = projected.height() / max(1, layer.height())
    resolution = round(math.sqrt(abs(res_x * res_y)), 3)

    result = processing.run(
        "gdal:warpreproject",
        {
            "INPUT": layer,
            "SOURCE_CRS": layer.crs(),
            "TARGET_CRS": target_crs,
            "RESAMPLING": resampling,
            "TARGET_RESOLUTION": resolution,
            "NODATA": -9999,
            "DATA_TYPE": 6,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        feedback=feedback,
    )

    out = QgsRasterLayer(result["OUTPUT"], f"{layer.name()} [{target_crs.authid()}]")
    return out if out.isValid() else None
