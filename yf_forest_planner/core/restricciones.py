"""Tabla de restricciones territoriales.

Insumo compartido por M2 (humedad), M3 (patios) y M5 (caminos). Cada regla
declara una capa, el campo que contiene la clase, los valores seleccionados,
el rol que cumple la restriccion y su peso.

La distincion de rol es deliberada y no debe aplanarse: un veto legal y un
aguajal restringen por razones distintas y producen consecuencias distintas
en el informe. Un veto legal que corta la conectividad es un error de diseno
que hay que reportar; un aguajal atravesado es una decision que hay que
justificar.

Las capas tematicas de referencia (ZEE regional, Mapa Nacional de Ecosistemas,
cobertura vegetal) estan a escala 1:100 000 o menor, mientras que el trazo se
replantea en campo con GNSS. Por eso los bordes no se tratan como frontera
dura sino con una banda de incertidumbre y decaimiento de costo.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import os
import tempfile
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

gdal.UseExceptions()

# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

ROLE_VETO = "VETO_LEGAL"
ROLE_IMPEDIMENTO = "IMPEDIMENTO_FISICO"
ROLE_PREFERENCIA = "PREFERENCIA"

ROLES = (ROLE_VETO, ROLE_IMPEDIMENTO, ROLE_PREFERENCIA)

ROLE_LABELS = {
    ROLE_VETO: "Veto legal",
    ROLE_IMPEDIMENTO: "Impedimento fisico",
    ROLE_PREFERENCIA: "Preferencia",
}

# Peso por defecto segun rol. El veto no lleva peso: es exclusion absoluta.
DEFAULT_WEIGHTS = {
    ROLE_VETO: 1.0,
    ROLE_IMPEDIMENTO: 0.85,
    ROLE_PREFERENCIA: 0.35,
}

# --------------------------------------------------------------------------
# Sugerencias de mapeo
# --------------------------------------------------------------------------

# Nombres de campo que suelen contener la clase tematica.
CLASS_FIELD_HINTS = (
    "ecosistema",
    "nom_eco",
    "clase",
    "cobertura",
    "cob_veg",
    "categoria",
    "zona",
    "zee",
    "descripcion",
    "leyenda",
    "tipo",
    "uso",
)

# Palabras clave para proponer clases de humedal. Es una SUGERENCIA por
# coincidencia de texto, nunca una lista cerrada: la leyenda de la ZEE de
# Madre de Dios no es la de Loreto ni la del mapa nacional. Fijar nombres
# exactos haria que el plugin funcione en una region y falle en silencio en
# la vecina.
WETLAND_KEYWORDS = (
    "aguajal",
    "aguaje",
    "mauritia",
    "pantano",
    "panta",
    "humedal",
    "inundable",
    "inundacion",
    "hidromorf",
    "palmera",
    "palmar",
    "renacal",
    "tahuampa",
    "varzea",
    "bajial",
    "cocha",
)

PROTECTED_KEYWORDS = (
    "proteccion",
    "conservacion",
    "intangible",
    "area natural protegida",
    "anp",
    "reserva",
    "santuario",
    "parque nacional",
    "servidumbre",
    "no aprovechable",
)


def _strip_accents(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    return _strip_accents(str(text).strip().lower())


def suggest_class_field(layer: QgsVectorLayer) -> Optional[str]:
    """Propone el campo que contiene la clase tematica."""
    if layer is None or not layer.isValid():
        return None
    names = {normalize(f.name()): f.name() for f in layer.fields()}
    for hint in CLASS_FIELD_HINTS:
        for norm_name, real_name in names.items():
            if hint in norm_name:
                return real_name
    # Sin coincidencia: primer campo de texto con variedad razonable.
    for fld in layer.fields():
        if not fld.isNumeric():
            return fld.name()
    return None


def unique_values(layer: QgsVectorLayer, field_name: str, limit: int = 500):
    """Valores distintos de un campo, ordenados, para poblar la lista."""
    if layer is None or not layer.isValid() or not field_name:
        return []
    idx = layer.fields().indexOf(field_name)
    if idx < 0:
        return []
    values = layer.uniqueValues(idx, limit)
    return sorted(
        {str(v).strip() for v in values if v is not None and str(v).strip()}
    )


def suggest_values(values, keywords=WETLAND_KEYWORDS) -> list[str]:
    """Marca previamente los valores que coinciden con las palabras clave."""
    matched = []
    for value in values:
        norm = normalize(value)
        if any(key in norm for key in keywords):
            matched.append(value)
    return matched


# --------------------------------------------------------------------------
# Reglas
# --------------------------------------------------------------------------


@dataclass
class RestrictionRule:
    """Una fila de la tabla de restricciones."""

    layer: Optional[QgsVectorLayer] = None
    field_name: Optional[str] = None
    values: tuple = ()
    role: str = ROLE_IMPEDIMENTO
    weight: float = DEFAULT_WEIGHTS[ROLE_IMPEDIMENTO]

    # Banda de incertidumbre en metros. El costo decae desde el borde del
    # poligono hacia afuera a lo largo de esta distancia, en lugar de cortar
    # en seco. Debe escalar con el denominador de la fuente: a 1:100 000 una
    # banda de 100 a 300 m es realista.
    uncertainty_m: float = 150.0

    # Texto que aparece en el informe. Debe identificar la fuente.
    label: str = ""
    source: str = ""

    def is_veto(self) -> bool:
        return self.role == ROLE_VETO

    def display(self) -> str:
        if self.label:
            return self.label
        if self.layer is not None:
            return self.layer.name()
        return "Restriccion"

    def expression(self) -> Optional[str]:
        """Expresion de filtro para seleccionar los valores marcados."""
        if not self.field_name or not self.values:
            return None
        quoted = ",".join(
            "'" + str(v).replace("'", "''") + "'" for v in self.values
        )
        return f'"{self.field_name}" IN ({quoted})'

    def to_dict(self) -> dict:
        """Serializacion para el contexto y el hash de trazabilidad."""
        return {
            "capa": self.layer.name() if self.layer is not None else "",
            "campo": self.field_name or "",
            "valores": list(self.values),
            "rol": self.role,
            "peso": round(float(self.weight), 4),
            "incertidumbre_m": round(float(self.uncertainty_m), 2),
            "etiqueta": self.label,
            "fuente": self.source,
        }

    def problems(self) -> list[str]:
        issues = []
        if self.layer is None or not self.layer.isValid():
            issues.append("capa no valida")
        if not self.field_name:
            issues.append("campo no definido")
        if not self.values:
            issues.append("ningun valor seleccionado")
        if self.role not in ROLES:
            issues.append("rol desconocido")
        if not 0.0 <= self.weight <= 1.0:
            issues.append("peso fuera de 0-1")
        return issues


@dataclass
class RestrictionSet:
    """Conjunto ordenado de reglas."""

    rules: list[RestrictionRule] = field(default_factory=list)

    def by_role(self, role: str) -> list[RestrictionRule]:
        return [r for r in self.rules if r.role == role]

    def valid_rules(self) -> list[RestrictionRule]:
        return [r for r in self.rules if not r.problems()]

    def wetland_rules(self) -> list[RestrictionRule]:
        """Reglas cuyos valores sugieren humedal, para fusion con el HAND."""
        out = []
        for rule in self.valid_rules():
            if any(
                any(key in normalize(v) for key in WETLAND_KEYWORDS)
                for v in rule.values
            ):
                out.append(rule)
        return out

    def to_dict(self) -> list[dict]:
        return [r.to_dict() for r in self.rules]


# --------------------------------------------------------------------------
# Rasterizacion
# --------------------------------------------------------------------------


@dataclass
class RasterTemplate:
    """Grilla de referencia. Todas las salidas se alinean a ella."""

    width: int
    height: int
    geotransform: tuple
    projection: str
    cell_size: float

    @classmethod
    def from_path(cls, path: str) -> Optional["RasterTemplate"]:
        dataset = gdal.Open(path, gdal.GA_ReadOnly)
        if dataset is None:
            return None
        gt = dataset.GetGeoTransform()
        template = cls(
            width=dataset.RasterXSize,
            height=dataset.RasterYSize,
            geotransform=gt,
            projection=dataset.GetProjection(),
            cell_size=abs(gt[1]),
        )
        dataset = None
        return template

    def create(self, path: str, dtype=gdal.GDT_Float32, nodata=None):
        driver = gdal.GetDriverByName("GTiff")
        dataset = driver.Create(
            path,
            self.width,
            self.height,
            1,
            dtype,
            options=["COMPRESS=DEFLATE", "TILED=YES", "PREDICTOR=2"],
        )
        dataset.SetGeoTransform(self.geotransform)
        dataset.SetProjection(self.projection)
        if nodata is not None:
            dataset.GetRasterBand(1).SetNoDataValue(float(nodata))
        return dataset


def _export_filtered(
    rule: RestrictionRule,
    crs: QgsCoordinateReferenceSystem,
    workdir: str,
) -> Optional[str]:
    """Escribe a GPKG solo las entidades que coinciden con la regla.

    Se pasa por disco porque gdal.Rasterize no lee capas en memoria de QGIS.
    """
    expression = rule.expression()
    if expression is None or rule.layer is None:
        return None

    original = rule.layer.subsetString()
    if not rule.layer.setSubsetString(expression):
        rule.layer.setSubsetString(original)
        return None

    path = os.path.join(
        workdir, f"restriccion_{abs(hash(rule.display())) % 100000}.gpkg"
    )
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.fileEncoding = "UTF-8"
    options.ct = None

    try:
        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            rule.layer, path, rule.layer.transformContext(), options
        )
    finally:
        rule.layer.setSubsetString(original)

    ok = error[0] == QgsVectorFileWriter.WriterError.NoError
    return path if ok and os.path.exists(path) else None


def rasterize_rule(
    rule: RestrictionRule,
    template: RasterTemplate,
    crs: QgsCoordinateReferenceSystem,
    workdir: Optional[str] = None,
) -> Optional[np.ndarray]:
    """Devuelve una mascara 0/1 de la regla alineada a la grilla de referencia."""
    workdir = workdir or tempfile.mkdtemp(prefix="fp_restr_")
    vector_path = _export_filtered(rule, crs, workdir)
    if vector_path is None:
        return None

    raster_path = os.path.join(workdir, f"mask_{os.path.basename(vector_path)}.tif")
    dataset = template.create(raster_path, gdal.GDT_Byte, nodata=None)
    dataset.GetRasterBand(1).Fill(0)

    gdal.RasterizeLayer(
        dataset,
        [1],
        gdal.OpenEx(vector_path).GetLayer(0),
        burn_values=[1],
        options=["ALL_TOUCHED=TRUE"],
    )
    dataset.FlushCache()
    array = dataset.GetRasterBand(1).ReadAsArray().astype(np.uint8)
    dataset = None
    return array


def proximity_decay(
    mask: np.ndarray,
    template: RasterTemplate,
    band_m: float,
    workdir: Optional[str] = None,
) -> np.ndarray:
    """Convierte una mascara 0/1 en penalizacion 0-1 con decaimiento al borde.

    Interior del poligono: 1.0. Hacia afuera decae linealmente hasta 0 a lo
    largo de band_m. Evita que el trazo se pegue a un borde cartografico que
    en el terreno no esta donde lo dibujaron.

    Usa gdal.ComputeProximity y no scipy, para no agregar dependencias.
    """
    if band_m <= 0:
        return mask.astype(np.float32)

    workdir = workdir or tempfile.mkdtemp(prefix="fp_prox_")
    src_path = os.path.join(workdir, "mask_src.tif")
    dst_path = os.path.join(workdir, "mask_prox.tif")

    src = template.create(src_path, gdal.GDT_Byte, nodata=None)
    src.GetRasterBand(1).WriteArray(mask.astype(np.uint8))
    src.FlushCache()

    dst = template.create(dst_path, gdal.GDT_Float32, nodata=-9999.0)
    gdal.ComputeProximity(
        src.GetRasterBand(1),
        dst.GetRasterBand(1),
        ["VALUES=1", "DISTUNITS=GEO"],
    )
    dst.FlushCache()
    distance = dst.GetRasterBand(1).ReadAsArray().astype(np.float32)
    src = None
    dst = None

    decay = 1.0 - np.clip(distance / float(band_m), 0.0, 1.0)
    decay[mask.astype(bool)] = 1.0
    return decay.astype(np.float32)


def build_role_surfaces(
    restrictions: RestrictionSet,
    template: RasterTemplate,
    crs: QgsCoordinateReferenceSystem,
    workdir: Optional[str] = None,
) -> dict:
    """Combina todas las reglas en una superficie por rol.

    Devuelve un diccionario con claves de rol y arreglos float32 en 0-1, mas
    la lista de reglas que no pudieron rasterizarse.
    """
    workdir = workdir or tempfile.mkdtemp(prefix="fp_roles_")
    shape = (template.height, template.width)
    surfaces = {role: np.zeros(shape, dtype=np.float32) for role in ROLES}
    failed: list[str] = []

    for rule in restrictions.valid_rules():
        mask = rasterize_rule(rule, template, crs, workdir)
        if mask is None:
            failed.append(rule.display())
            continue

        if rule.is_veto():
            # El veto no decae: es exclusion absoluta dentro del poligono.
            surface = mask.astype(np.float32)
        else:
            surface = proximity_decay(
                mask, template, rule.uncertainty_m, workdir
            ) * float(rule.weight)

        np.maximum(surfaces[rule.role], surface, out=surfaces[rule.role])

    return {"surfaces": surfaces, "failed": failed, "workdir": workdir}
