"""Contexto de planificacion: el objeto que atraviesa toda la cadena.

Cada modulo recibe un PlanningContext y devuelve un ModuleResult. El contexto
acumula capas, parametros y hashes, de modo que el informe final pueda
reconstruir exactamente que se ejecuto y con que datos.

Dos decisiones que sostienen la trazabilidad:

  SEMILLA FIJA   los modulos con componente aleatoria (agrupamiento de patios)
                 toman la semilla de aqui. Sin semilla fija, cada corrida da un
                 resultado distinto y el hash deja de significar nada.

  SRC UNICO      la reproyeccion se resuelve una sola vez, al construir el
                 contexto. Aguas abajo nadie vuelve a transformar coordenadas,
                 asi que el hash se calcula sobre las mismas coordenadas que
                 uso el analisis.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsMapLayer,
    QgsRasterLayer,
    QgsVectorLayer,
)

from .constants import MODULE_ORDER, PLUGIN_NAME, PLUGIN_VERSION


# --------------------------------------------------------------------------
# Resultado de un modulo
# --------------------------------------------------------------------------


@dataclass
class ModuleResult:
    """Salida uniforme de cualquier modulo de la cadena."""

    module_id: str = ""
    ok: bool = False
    layers: dict[str, QgsMapLayer] = field(default_factory=dict)
    rasters: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    duracion_s: float = 0.0

    def is_blocking(self) -> bool:
        return bool(self.errores) or not self.ok


# --------------------------------------------------------------------------
# Procedencia de los insumos
# --------------------------------------------------------------------------


@dataclass
class SourceRecord:
    """Registro de un insumo, para que el informe pueda declararlo.

    El hash se calcula sobre el archivo de origen cuando existe en disco. Para
    capas en memoria o servicios remotos queda vacio y el informe lo declara
    como no verificable, que es preferible a inventar un identificador.
    """

    rol: str = ""
    nombre: str = ""
    ruta: str = ""
    crs: str = ""
    entidades: Optional[int] = None
    sha256: str = ""
    tamano_bytes: Optional[int] = None
    modificado: str = ""
    nota: str = ""

    @classmethod
    def from_layer(cls, layer: QgsMapLayer, rol: str) -> "SourceRecord":
        if layer is None:
            return cls(rol=rol, nota="capa ausente")

        rec = cls(rol=rol, nombre=layer.name(), crs=layer.crs().authid() or "sin definir")

        source = layer.source().split("|")[0]
        rec.ruta = source

        if isinstance(layer, QgsVectorLayer):
            rec.entidades = layer.featureCount()

        if source and os.path.exists(source):
            try:
                stat = os.stat(source)
                rec.tamano_bytes = stat.st_size
                rec.modificado = datetime.fromtimestamp(
                    stat.st_mtime
                ).isoformat(timespec="seconds")
                rec.sha256 = _hash_file(source)
            except OSError as exc:
                rec.nota = f"no se pudo leer el archivo: {exc}"
        else:
            rec.nota = "capa en memoria o servicio remoto: sin hash verificable"

        return rec


def _hash_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Contexto
# --------------------------------------------------------------------------


@dataclass
class PlanningContext:
    """Estado compartido por toda la cadena de modulos."""

    # --- entradas ---
    censo_layer: Optional[QgsVectorLayer] = None
    only_selected: bool = False
    pca_layer: Optional[QgsVectorLayer] = None
    dem_layer: Optional[QgsRasterLayer] = None
    crs: Optional[QgsCoordinateReferenceSystem] = None

    # --- geometria de trabajo, resuelta al preparar ---
    pca_geom: Optional[QgsGeometry] = None
    pca_area_ha: float = 0.0

    # --- configuraciones por modulo ---
    censo_config: Any = None
    hydro_config: Any = None
    wetness_config: Any = None
    yards_config: Any = None
    cost_config: Any = None
    roads_config: Any = None
    restrictions: Any = None

    # --- datos derivados ---
    trees: list = field(default_factory=list)
    censo_report: Any = None

    # --- salidas ---
    output_gpkg: str = ""
    output_html: str = ""
    results: dict[str, ModuleResult] = field(default_factory=dict)
    escenarios_centros: list = field(default_factory=list)
    yards: list = field(default_factory=list)
    centers: list = field(default_factory=list)
    costo_path: str = ""
    intensity_config: Any = None
    impact_config: Any = None
    impacto: Any = None
    intensidad: Any = None
    preset: str = ""
    censo_evaluado: Any = None
    transport_config: Any = None
    transporte: Any = None
    sources: list[SourceRecord] = field(default_factory=list)

    # --- reproducibilidad ---
    # Los modulos con componente aleatoria toman la semilla de aqui. Sin
    # semilla fija el resultado cambia en cada corrida y el hash del informe
    # deja de garantizar nada.
    random_seed: int = 20260812
    started_at: str = ""
    plugin_version: str = PLUGIN_VERSION

    # --- diagnostico acumulado ---
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- estado

    def start(self) -> None:
        self.started_at = datetime.now().isoformat(timespec="seconds")

    def register_source(self, layer: QgsMapLayer, rol: str) -> None:
        if layer is not None:
            self.sources.append(SourceRecord.from_layer(layer, rol))

    def store(self, result: ModuleResult) -> None:
        self.results[result.module_id] = result
        self.errores.extend(result.errores)
        self.advertencias.extend(result.advertencias)

    def result(self, module_id: str) -> Optional[ModuleResult]:
        return self.results.get(module_id)

    def layer(self, module_id: str, key: str) -> Optional[QgsMapLayer]:
        res = self.results.get(module_id)
        return res.layers.get(key) if res else None

    def completed_modules(self) -> list[str]:
        return [m for m in MODULE_ORDER if m in self.results and self.results[m].ok]

    def has_blocking_errors(self) -> bool:
        return bool(self.errores)

    # ------------------------------------------------------------ validacion

    def validate(self) -> list[str]:
        """Requisitos minimos para arrancar la cadena.

        Devuelve la lista de faltantes. Vacia significa listo para ejecutar.
        """
        missing: list[str] = []

        if self.censo_layer is None or not self.censo_layer.isValid():
            missing.append("capa de censo forestal")
        if self.dem_layer is None or not self.dem_layer.isValid():
            missing.append("modelo digital de elevacion")
        if self.pca_layer is None or not self.pca_layer.isValid():
            missing.append("poligono de PCA o concesion")

        if self.crs is None or not self.crs.isValid():
            missing.append("sistema de referencia de trabajo")
        elif self.crs.isGeographic():
            missing.append(
                "un SRC proyectado en metros: con un sistema geografico las "
                "distancias y areas se calculan en grados"
            )

        if self.censo_config is None:
            missing.append("mapeo de campos del censo")
        elif hasattr(self.censo_config, "missing_requirements"):
            missing.extend(self.censo_config.missing_requirements())

        return missing

    # ------------------------------------------------------------ manifiesto

    def manifest(self) -> dict:
        """Manifiesto de la corrida, para el informe y para reproducirla."""
        return {
            "plugin": PLUGIN_NAME,
            "version": self.plugin_version,
            "ejecutado": self.started_at,
            "srs_trabajo": self.crs.authid() if self.crs else "",
            "semilla": self.random_seed,
            "area_pca_ha": round(self.pca_area_ha, 4),
            "insumos": [vars(s) for s in self.sources],
            "modulos": {
                mid: {
                    "ok": res.ok,
                    "duracion_s": round(res.duracion_s, 2),
                    "parametros": res.params,
                    "metricas": res.metrics,
                    "advertencias": res.advertencias,
                    "errores": res.errores,
                }
                for mid, res in self.results.items()
            },
        }

    def manifest_json(self) -> str:
        return json.dumps(self.manifest(), indent=2, ensure_ascii=False, default=str)
