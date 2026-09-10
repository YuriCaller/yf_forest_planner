"""Simbologia de las capas de salida.

Un complemento que devuelve capas en gris por defecto entrega datos; uno que
las devuelve simbolizadas entrega un mapa. El criterio sigue el proceso aditivo
austero: cada elemento se justifica, el dato tematico manda y nada queda por
omision.

Parte de YF Forest Planner.
"""

from __future__ import annotations

from qgis.core import (
    Qgis,
    QgsMessageLog,
    QgsArrowSymbolLayer,
    QgsFillSymbol,
    QgsGraduatedSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsRendererRange,
    QgsSimpleFillSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

from .constants import PLUGIN_NAME

# Paleta fria para el agua, calida para la operacion. Sin arcoiris.
AGUA = "#2b6a8f"
FAJA = "#7fb3c8"
PATIO = "#b5532a"
CENTRO = "#7a2e12"
ARRASTRE = "#c98a5e"
VIA_PRINCIPAL = "#8a3d18"
VIA_SECUNDARIA = "#c07340"
CRUCE = "#1f4a63"
VERDE_OK = "#2e7d32"
ROJO_NO = "#b3261e"
AZUL_SEM = "#1565a8"


def style_red_hidrica(layer: QgsVectorLayer) -> None:
    """Grosor graduado por orden de Strahler.

    Es la simbologia que hace legible el mapa de un plan de manejo sin
    necesidad de leyenda: la jerarquia del drenaje se lee sola.
    """
    if layer is None or not layer.isValid():
        return
    index = layer.fields().indexOf("strahler")
    if index < 0:
        return

    ordenes = sorted({f["strahler"] for f in layer.getFeatures() if f["strahler"]})
    if not ordenes:
        return

    ranges = []
    for orden in ordenes:
        ancho = 0.25 + 0.35 * (orden - 1)
        symbol = QgsLineSymbol.createSimple(
            {"color": AGUA, "width": str(round(ancho, 2))}
        )
        ranges.append(
            QgsRendererRange(orden - 0.5, orden + 0.5, symbol, f"Orden {orden}")
        )

    layer.setRenderer(QgsGraduatedSymbolRenderer("strahler", ranges))
    layer.triggerRepaint()


def style_fajas(layer: QgsVectorLayer) -> None:
    """Relleno translucido sin borde duro, para que no compita con la red."""
    if layer is None or not layer.isValid():
        return
    fill = QgsSimpleFillSymbolLayer()
    fill.setColor(QColor(FAJA))
    fill.setStrokeColor(QColor(FAJA))
    fill.setStrokeWidth(0.1)
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, fill)
    symbol.setOpacity(0.35)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def style_arrastre(layer: QgsVectorLayer) -> None:
    """Flechas convergentes hacia el patio.

    Es el diagrama de arrastre del plan: cada linea nace en el tocon y apunta
    al patio que lo recibe. El marcador de flecha es lo que convierte un haz de
    lineas en una figura que se entiende de un vistazo.
    """
    if layer is None or not layer.isValid():
        return

    try:
        arrow = QgsArrowSymbolLayer()
        arrow.setArrowWidth(0.4)
        arrow.setArrowStartWidth(0.15)
        arrow.setHeadLength(1.4)
        arrow.setHeadThickness(1.1)
        arrow.setIsCurved(False)
        symbol = QgsLineSymbol()
        symbol.changeSymbolLayer(0, arrow)
        sub = symbol.symbolLayer(0).subSymbol()
        if sub is not None:
            sub.setColor(QColor(ARRASTRE))
        symbol.setOpacity(0.75)
    except Exception:  # noqa: BLE001 - respaldo si la API cambia
        line = QgsSimpleLineSymbolLayer()
        line.setColor(QColor(ARRASTRE))
        line.setWidth(0.2)
        symbol = QgsLineSymbol()
        symbol.changeSymbolLayer(0, line)
        symbol.setOpacity(0.6)

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def style_patios(layer: QgsVectorLayer) -> None:
    """Tamano graduado por volumen acumulado."""
    if layer is None or not layer.isValid():
        return
    index = layer.fields().indexOf("volumen_m3")
    if index < 0:
        return

    valores = [f["volumen_m3"] for f in layer.getFeatures() if f["volumen_m3"]]
    if not valores:
        return
    vmin, vmax = min(valores), max(valores)
    if vmax <= vmin:
        vmax = vmin + 1.0

    ranges = []
    pasos = 4
    for i in range(pasos):
        low = vmin + (vmax - vmin) * i / pasos
        high = vmin + (vmax - vmin) * (i + 1) / pasos
        symbol = QgsMarkerSymbol.createSimple({
            "name": "square",
            "color": PATIO,
            "outline_color": "#3d1a0d",
            "outline_width": "0.2",
            "size": str(round(2.0 + 1.1 * i, 2)),
        })
        ranges.append(
            QgsRendererRange(low, high, symbol, f"{low:,.0f} - {high:,.0f} m3")
        )

    layer.setRenderer(QgsGraduatedSymbolRenderer("volumen_m3", ranges))
    layer.triggerRepaint()


def style_centros(layer: QgsVectorLayer) -> None:
    """Un solo simbolo destacado: son pocos y deben dominar la jerarquia."""
    if layer is None or not layer.isValid():
        return
    symbol = QgsMarkerSymbol.createSimple({
        "name": "star",
        "color": CENTRO,
        "outline_color": "#1f0a04",
        "outline_width": "0.3",
        "size": "7.0",
    })
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def style_vias(layer: QgsVectorLayer) -> None:
    """Grosor y tono segun jerarquia: la principal debe dominar."""
    if layer is None or not layer.isValid():
        return
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory

    if layer.fields().indexOf("jerarquia") < 0:
        return
    categorias = []
    for valor, color, ancho, etiqueta in (
        ("principal", VIA_PRINCIPAL, "1.1", "Via principal"),
        ("secundaria", VIA_SECUNDARIA, "0.55", "Via secundaria"),
    ):
        simbolo = QgsLineSymbol.createSimple({"color": color, "width": ancho})
        categorias.append(QgsRendererCategory(valor, simbolo, etiqueta))
    layer.setRenderer(QgsCategorizedSymbolRenderer("jerarquia", categorias))
    layer.triggerRepaint()


def style_cruces(layer: QgsVectorLayer) -> None:
    """Simbolo por tipo de obra: la magnitud de la obra debe leerse sola."""
    if layer is None or not layer.isValid():
        return
    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory

    if layer.fields().indexOf("obra") < 0:
        return
    categorias = []
    for valor, forma, tam, etiqueta in (
        ("alcantarilla", "circle", "2.4", "Alcantarilla"),
        ("ponton", "diamond", "3.6", "Ponton"),
        ("puente", "triangle", "5.2", "Puente"),
    ):
        simbolo = QgsMarkerSymbol.createSimple({
            "name": forma, "color": CRUCE, "outline_color": "#ffffff",
            "outline_width": "0.4", "size": tam,
        })
        categorias.append(QgsRendererCategory(valor, simbolo, etiqueta))
    layer.setRenderer(QgsCategorizedSymbolRenderer("obra", categorias))
    layer.triggerRepaint()


def style_censo_evaluado(layer: QgsVectorLayer) -> None:
    """Marca cada individuo segun pueda talarse o no.

    Se usan marcadores de fuente con caracteres Unicode: el visto para el
    aprovechable, la cruz para el que no puede talarse y un rombo para el
    semillero. La lectura del mapa no debe depender del color, porque en
    impresion a escala de grises el color desaparece y la forma no.
    """
    if layer is None or not layer.isValid():
        return
    from qgis.core import (
        QgsCategorizedSymbolRenderer, QgsFontMarkerSymbolLayer,
        QgsMarkerSymbol, QgsRendererCategory,
    )

    from .censo import (
        CONDICION_APROVECHABLE, CONDICION_NO_APROVECHABLE, CONDICION_SEMILLERO,
    )

    if layer.fields().indexOf("condicion") < 0:
        return

    definicion = (
        (CONDICION_APROVECHABLE, "\u2713", VERDE_OK, 3.6, "Aprovechable"),
        (CONDICION_NO_APROVECHABLE, "\u2717", ROJO_NO, 3.6, "No aprovechable"),
        (CONDICION_SEMILLERO, "\u25c6", AZUL_SEM, 3.0, "Semillero (no se tala)"),
    )

    categorias = []
    for valor, caracter, color, tam, etiqueta in definicion:
        try:
            marcador = QgsFontMarkerSymbolLayer("DejaVu Sans", caracter)
            marcador.setSize(tam)
            marcador.setColor(QColor(color))
            marcador.setStrokeColor(QColor("#ffffff"))
            marcador.setStrokeWidth(0.3)
            simbolo = QgsMarkerSymbol()
            simbolo.changeSymbolLayer(0, marcador)
        except Exception:  # noqa: BLE001 - respaldo por forma
            formas = {
                CONDICION_APROVECHABLE: "circle",
                CONDICION_NO_APROVECHABLE: "cross2",
                CONDICION_SEMILLERO: "diamond",
            }
            simbolo = QgsMarkerSymbol.createSimple({
                "name": formas.get(valor, "circle"), "color": color,
                "outline_color": "#ffffff", "outline_width": "0.3",
                "size": str(tam),
            })
        categorias.append(QgsRendererCategory(valor, simbolo, etiqueta))

    layer.setRenderer(QgsCategorizedSymbolRenderer("condicion", categorias))
    layer.triggerRepaint()


STYLERS = {
    "censo_evaluado": style_censo_evaluado,
    "vias": style_vias,
    "cruces": style_cruces,
    "red_hidrica": style_red_hidrica,
    "fajas_marginales": style_fajas,
    "arrastre": style_arrastre,
    "patios_acopio": style_patios,
    "centros_acopio": style_centros,
}


def apply(key: str, layer: QgsVectorLayer) -> None:
    """Aplica el estilo correspondiente a una capa de salida."""
    styler = STYLERS.get(key)
    if styler is None or layer is None:
        return
    try:
        styler(layer)
    except Exception as exc:  # noqa: BLE001
        # Un estilo que falla no debe detener el analisis: las capas quedan
        # cargadas y el usuario puede simbolizarlas a mano. El motivo se
        # registra para poder diagnosticarlo.
        QgsMessageLog.logMessage(
            f"No se pudo aplicar el estilo '{key}': {exc}",
            PLUGIN_NAME, Qgis.MessageLevel.Warning,
        )
