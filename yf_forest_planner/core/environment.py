"""Verificacion del entorno de ejecucion.

Comprueba en tiempo real lo que la cadena necesita, en lugar de asumirlo. Las
comprobaciones no son teoricas: cada una corresponde a algo que fallo o que
resulto distinto de lo esperado al probar sobre una instalacion real.

  - El prefijo del proveedor GRASS cambio de 'grass7' a 'grass' entre
    versiones, asi que se resuelve consultando el registro.
  - r.stream.order es un addon y NO esta presente en una instalacion estandar
    de QGIS. Por eso los ordenes de Strahler y Shreve se calculan en el plugin.
  - scikit-image NO viene con QGIS. El trazado de rutas de menor costo debe
    apoyarse en r.cost y r.drain de GRASS, no en skimage.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field

from qgis.core import Qgis, QgsApplication

# Algoritmos indispensables para la cadena.
REQUIRED_GRASS = ("r.watershed", "r.to.vect")

# Necesarios para los modulos de costo y vias, todavia en desarrollo.
OPTIONAL_GRASS = ("r.cost", "r.drain", "r.slope.aspect")

REQUIRED_PROVIDERS = ("native", "gdal", "grass")


def grass_algorithm_id(name: str) -> str | None:
    """Identificador del algoritmo GRASS segun la version de QGIS."""
    registry = QgsApplication.processingRegistry()
    for prefix in ("grass", "grass7"):
        alg_id = f"{prefix}:{name}"
        if registry.algorithmById(alg_id) is not None:
            return alg_id
    return None


@dataclass
class EnvironmentReport:
    """Resultado del diagnostico."""

    qgis_version: str = ""
    providers: dict[str, int] = field(default_factory=dict)
    grass_required: dict[str, str] = field(default_factory=dict)
    grass_optional: dict[str, str] = field(default_factory=dict)
    libraries: dict[str, str] = field(default_factory=dict)

    blocking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.blocking

    def report(self) -> str:
        lines = [f"QGIS {self.qgis_version}", ""]

        lines.append("Proveedores de procesamiento:")
        for name, count in sorted(self.providers.items()):
            estado = f"{count} algoritmos" if count else "NO DISPONIBLE"
            lines.append(f"  {name}: {estado}")

        lines.append("")
        lines.append("Algoritmos GRASS indispensables:")
        for name, found in self.grass_required.items():
            lines.append(f"  {name}: {found or 'NO DISPONIBLE'}")

        lines.append("")
        lines.append("Algoritmos GRASS para modulos en desarrollo:")
        for name, found in self.grass_optional.items():
            lines.append(f"  {name}: {found or 'no disponible'}")

        lines.append("")
        lines.append("Librerias:")
        for name, version in sorted(self.libraries.items()):
            lines.append(f"  {name}: {version}")

        if self.notes:
            lines.append("")
            lines.append("Notas:")
            lines.extend(f"  - {note}" for note in self.notes)

        if self.blocking:
            lines.append("")
            lines.append("IMPIDEN EJECUTAR:")
            lines.extend(f"  - {issue}" for issue in self.blocking)
        else:
            lines.append("")
            lines.append("El entorno permite ejecutar la cadena.")

        return "\n".join(lines)


def check_environment() -> EnvironmentReport:
    """Ejecuta el diagnostico completo."""
    env = EnvironmentReport(qgis_version=Qgis.QGIS_VERSION)
    registry = QgsApplication.processingRegistry()

    available = {p.id(): len(p.algorithms()) for p in registry.providers()}
    for name in REQUIRED_PROVIDERS:
        if name == "grass":
            count = available.get("grass") or available.get("grass7") or 0
        else:
            count = available.get(name, 0)
        env.providers[name] = count
        if not count:
            env.blocking.append(
                f"El proveedor de procesamiento '{name}' no esta disponible. "
                "Activelo en Complementos - Administrar e instalar complementos."
            )

    for name in REQUIRED_GRASS:
        found = grass_algorithm_id(name)
        env.grass_required[name] = found or ""
        if not found:
            env.blocking.append(f"El algoritmo GRASS {name} no esta disponible.")

    for name in OPTIONAL_GRASS:
        env.grass_optional[name] = grass_algorithm_id(name) or ""

    for name in ("numpy", "scipy"):
        try:
            module = importlib.import_module(name)
            env.libraries[name] = getattr(module, "__version__", "presente")
        except ImportError:
            env.libraries[name] = "NO INSTALADA"
            env.blocking.append(
                f"La libreria {name} no esta instalada en el entorno de QGIS."
            )

    try:
        from osgeo import gdal

        env.libraries["gdal"] = gdal.__version__
    except ImportError:
        env.libraries["gdal"] = "NO DISPONIBLE"

    # scikit-image no viene con QGIS. Se registra solo como informacion: la
    # cadena no depende de ella y no debe hacerlo.
    try:
        import skimage

        env.libraries["scikit-image"] = getattr(skimage, "__version__", "presente")
    except ImportError:
        env.libraries["scikit-image"] = "no instalada (no se requiere)"

    if not grass_algorithm_id("r.stream.order"):
        env.notes.append(
            "r.stream.order no esta disponible; es un addon de GRASS. Los "
            "ordenes de Strahler y Shreve los calcula el propio complemento."
        )

    return env
