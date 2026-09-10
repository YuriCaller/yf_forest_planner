"""Traduccion de la interfaz.

El idioma NO se elige al instalar: se toma del que tenga configurado QGIS, de
modo que quien lo usa en ingles ve el complemento en ingles sin configurar
nada. Es el mecanismo estandar de Qt.

Como agregar un idioma
----------------------
1. Extraer las cadenas marcadas con tr():

       pylupdate5 $(find . -name "*.py") -ts i18n/forest_planner_en.ts

2. Traducir el .ts con Qt Linguist.
3. Compilar:

       lrelease i18n/forest_planner_en.ts

4. El .qm resultante se carga solo al arrancar el complemento.

Que se traduce y que no
-----------------------
Se traducen las etiquetas, botones y mensajes de la interfaz. NO se traducen
los nombres de los campos de las capas de salida ni las claves del informe: son
identificadores que el usuario puede haber usado en expresiones, estilos o
scripts, y cambiarlos entre idiomas romperia proyectos existentes.

Tampoco se traducen los terminos normativos que no tienen equivalente: faja
marginal, DMC, semillero y POA existen en espanol y portugues porque describen
figuras de la legislacion amazonica.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QSettings, QTranslator

CONTEXTO = "ForestPlanner"

# Idiomas con traduccion disponible. El espanol es el idioma de origen y no
# necesita archivo.
IDIOMAS = ("en", "pt")

_traductor = None


def tr(texto: str, contexto: str = CONTEXTO) -> str:
    """Traduce una cadena de la interfaz."""
    return QCoreApplication.translate(contexto, texto)


def idioma_activo() -> str:
    """Codigo de dos letras del idioma configurado en QGIS."""
    valor = QSettings().value("locale/userLocale", "")
    if valor:
        return str(valor)[:2].lower()
    return QLocale.system().name()[:2].lower()


def instalar(directorio_plugin: str) -> bool:
    """Carga la traduccion que corresponda al idioma de QGIS.

    Devuelve True si se instalo alguna. Sin traduccion disponible el
    complemento queda en espanol, que es su idioma de origen.
    """
    global _traductor

    codigo = idioma_activo()
    if codigo not in IDIOMAS:
        return False

    ruta = os.path.join(
        directorio_plugin, "i18n", f"forest_planner_{codigo}.qm"
    )
    if not os.path.exists(ruta):
        return False

    traductor = QTranslator()
    if not traductor.load(ruta):
        return False

    QCoreApplication.installTranslator(traductor)
    # Se conserva la referencia: si el traductor se recolecta, Qt deja de
    # aplicarlo sin dar aviso.
    _traductor = traductor
    return True


def desinstalar() -> None:
    """Retira la traduccion al descargar el complemento."""
    global _traductor
    if _traductor is not None:
        QCoreApplication.removeTranslator(_traductor)
        _traductor = None
