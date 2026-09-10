"""Simbologia de los rasters de salida.

Un raster cargado en gris lineal es ilegible: el estirado por minimo y maximo
aplasta toda la variacion util contra un extremo cuando hay pocas celdas
extremas, que es siempre el caso en estas mallas. Aqui se clasifica por
cuantiles y con rampas que dicen algo.

Criterio de color, siguiendo el proceso aditivo austero: rampa de un solo
sentido para magnitudes, y rampa divergente SOLO donde existe un umbral con
significado, como el limite normativo de intensidad. Nunca arcoiris.

Parte de YF Forest Planner.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from qgis.core import (
    Qgis,
    QgsMessageLog,
    QgsColorRampShader,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

from .constants import PLUGIN_NAME

# Rampas. Las magnitudes van de claro a intenso en un solo tono; la excedencia
# usa divergente porque el cero tiene significado normativo.
RAMPA_DENSIDAD = ["#f7f4ea", "#cfe0c3", "#93bf85", "#4f8f5b", "#1f5c3d"]
RAMPA_INTENSIDAD = ["#f8f4e8", "#f2d9a7", "#e3a55f", "#c96a2c", "#8f3a12"]
RAMPA_COSTO = ["#f2f6f8", "#bcd4e0", "#7fa8c0", "#48748f", "#22485e"]
RAMPA_HUMEDAD = ["#f7f7f2", "#cfe2e8", "#8fc0cf", "#4a8fa8", "#1f5c73"]


def _valores(layer: QgsRasterLayer, banda: int = 1):
    """Muestra de valores validos, para calcular cuantiles."""
    try:
        from osgeo import gdal

        ds = gdal.Open(layer.source())
        if ds is None:
            return None
        b = ds.GetRasterBand(banda)
        datos = b.ReadAsArray().astype(float)
        nodata = b.GetNoDataValue()
        ds = None
        valido = np.isfinite(datos)
        if nodata is not None:
            valido &= datos != nodata
        vals = datos[valido]
        return vals if vals.size else None
    except Exception:  # noqa: BLE001
        return None


def _aplicar(
    layer: QgsRasterLayer,
    cortes: list,
    colores: list,
    etiquetas: Optional[list] = None,
    discreto: bool = False,
) -> None:
    shader = QgsRasterShader()
    rampa = QgsColorRampShader()
    # El enumerado tiene ambito en QGIS 3.36 y posteriores; se resuelve por
    # atributo para no romper en las versiones anteriores.
    tipos = getattr(QgsColorRampShader, "Type", QgsColorRampShader)
    rampa.setColorRampType(tipos.Discrete if discreto else tipos.Interpolated)
    items = []
    for i, corte in enumerate(cortes):
        etiqueta = etiquetas[i] if etiquetas and i < len(etiquetas) else f"{corte:,.2f}"
        items.append(
            QgsColorRampShader.ColorRampItem(
                float(corte), QColor(colores[min(i, len(colores) - 1)]), etiqueta
            )
        )
    rampa.setColorRampItemList(items)
    shader.setRasterShaderFunction(rampa)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    layer.setRenderer(renderer)
    layer.setOpacity(0.85)
    layer.triggerRepaint()


def style_densidad(layer: QgsRasterLayer) -> None:
    """Individuos por hectarea, en clases enteras."""
    vals = _valores(layer)
    if vals is None:
        return
    positivos = vals[vals > 0]
    if positivos.size == 0:
        return
    maximo = float(np.percentile(positivos, 98))
    cortes = [0.0]
    etiquetas = ["sin aprovechamiento"]
    pasos = max(2, min(6, int(round(maximo))))
    for i in range(1, pasos + 1):
        valor = maximo * i / pasos
        cortes.append(valor)
        etiquetas.append(f"hasta {valor:,.1f} arb/ha")
    _aplicar(layer, cortes, ["#ffffff"] + RAMPA_DENSIDAD, etiquetas, discreto=True)


def style_intensidad(layer: QgsRasterLayer, limite: float = 0.0) -> None:
    """Volumen por hectarea. Si hay limite normativo, se marca como corte."""
    vals = _valores(layer)
    if vals is None:
        return
    positivos = vals[vals > 0]
    if positivos.size == 0:
        return

    if limite > 0:
        # El limite parte la rampa: por debajo tonos calidos suaves, por encima
        # rojo. El evaluador debe ver de un vistazo que hectareas se exceden.
        cortes = [0.0, limite * 0.5, limite * 0.85, limite,
                  limite * 1.25, float(max(positivos.max(), limite * 1.5))]
        colores = ["#ffffff", "#f8ecd2", "#f2d9a7", "#e8b878", "#d9534f", "#8f1f1a"]
        etiquetas = [
            "sin aprovechamiento",
            f"hasta {limite * 0.5:,.0f}",
            f"hasta {limite * 0.85:,.0f}",
            f"en el limite ({limite:,.0f} m3/ha)",
            f"excede hasta {limite * 1.25:,.0f}",
            "excede el limite",
        ]
        _aplicar(layer, cortes, colores, etiquetas, discreto=True)
        return

    cortes = [0.0]
    etiquetas = ["sin aprovechamiento"]
    for q in (25, 50, 75, 90, 98):
        valor = float(np.percentile(positivos, q))
        cortes.append(valor)
        etiquetas.append(f"hasta {valor:,.1f} m3/ha")
    _aplicar(layer, cortes, ["#ffffff"] + RAMPA_INTENSIDAD, etiquetas, discreto=True)


def style_excedencia(layer: QgsRasterLayer) -> None:
    """Cuanto se supera el limite, en fraccion. Cero es cumplimiento."""
    _aplicar(
        layer,
        [0.0, 0.001, 0.25, 0.5, 1.0],
        ["#ffffff", "#fde3c8", "#f0a35e", "#d9534f", "#7a1512"],
        ["cumple", "excede levemente", "excede 25%", "excede 50%", "excede 100%"],
        discreto=True,
    )


def style_costo(layer: QgsRasterLayer) -> None:
    """Superficie de costo, en cuantiles.

    Las celdas vetadas se dejan fuera del calculo de cuantiles: incluirlas
    aplasta toda la variacion util contra el extremo bajo, que es exactamente
    lo que hace ilegible el raster con el estirado por defecto.
    """
    vals = _valores(layer)
    if vals is None:
        return
    utiles = vals[vals < 1000.0]
    if utiles.size == 0:
        utiles = vals
    cortes = [float(np.percentile(utiles, q)) for q in (2, 25, 50, 75, 95)]
    cortes.append(float(vals.max()))
    etiquetas = [
        "muy transitable", "transitable", "medio", "costoso", "muy costoso",
        "vetado o impedido",
    ]
    _aplicar(layer, cortes, RAMPA_COSTO + ["#4a1414"], etiquetas)


def style_humedad(layer: QgsRasterLayer) -> None:
    """Penalizacion por humedad, de cero a uno."""
    _aplicar(
        layer,
        [0.0, 0.25, 0.5, 0.75, 1.0],
        RAMPA_HUMEDAD,
        ["seco", "humedad baja", "humedad media", "humedad alta", "saturado"],
    )


STYLERS = {
    "densidad": style_densidad,
    "intensidad": style_intensidad,
    "excedencia": style_excedencia,
    "superficie_costo": style_costo,
    "costo": style_costo,
    "humedad": style_humedad,
}


def _registrar(clave: str, exc: Exception) -> None:
    """Deja constancia del fallo sin interrumpir la corrida.

    Un estilo que no se aplica es un problema de presentacion: la capa queda
    cargada y utilizable. Interrumpir el analisis por eso seria peor, pero
    silenciarlo impediria diagnosticarlo.
    """
    QgsMessageLog.logMessage(
        f"No se pudo aplicar el estilo de raster '{clave}': {exc}",
        PLUGIN_NAME, Qgis.MessageLevel.Warning,
    )


def apply(clave: str, layer: QgsRasterLayer, **kwargs) -> None:
    """Aplica el estilo correspondiente. Un fallo no debe detener la corrida."""
    styler = STYLERS.get(clave)
    if styler is None or layer is None or not layer.isValid():
        return
    try:
        styler(layer, **kwargs)
        return
    except TypeError:
        # El estilo no acepta los argumentos extra: se reintenta sin ellos.
        pass
    except Exception as exc:  # noqa: BLE001
        _registrar(clave, exc)
        return

    try:
        styler(layer)
    except Exception as exc:  # noqa: BLE001
        _registrar(clave, exc)
