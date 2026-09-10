"""YF Forest Planner - del censo forestal al plan de manejo.

Punto de entrada que QGIS invoca al cargar el complemento.
"""

from __future__ import annotations


def classFactory(iface):  # noqa: N802 - nombre exigido por la API de QGIS
    from .plugin import ForestPlannerPlugin

    return ForestPlannerPlugin(iface)
