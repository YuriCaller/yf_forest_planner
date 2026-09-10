"""Constantes compartidas de YF Forest Planner."""

from __future__ import annotations

PLUGIN_NAME = "Forest Planner"
PLUGIN_PACKAGE = "yf_forest_planner"
PLUGIN_VERSION = "0.1.0"

MENU_TITLE = "&Forest Planner"

SETTINGS_GROUP = "yf_forest_planner"

# Identificadores de modulo, en orden de ejecucion. El orden importa: cada
# modulo consume salidas del anterior.
M1_HIDROLOGIA = "m1_hidrologia"
M2_HUMEDAD = "m2_humedad"
M3_PATIOS = "m3_patios"
M4_COSTO = "m4_costo"
M5_CAMINOS = "m5_caminos"
M6_ARRASTRE = "m6_arrastre"
M7_INTENSIDAD = "m7_intensidad"
M8_TRANSPORTE = "m8_transporte"

MODULE_ORDER = (
    M1_HIDROLOGIA,
    M2_HUMEDAD,
    M3_PATIOS,
    M4_COSTO,
    M5_CAMINOS,
    M6_ARRASTRE,
    M7_INTENSIDAD,
    M8_TRANSPORTE,
)

MODULE_LABELS = {
    M1_HIDROLOGIA: "Red hidrica y fajas marginales",
    M2_HUMEDAD: "Humedad, aguajales y zonas inundables",
    M3_PATIOS: "Patios y centros de acopio",
    M4_COSTO: "Superficie de costo",
    M5_CAMINOS: "Red de vias",
    M6_ARRASTRE: "Arrastre e impacto",
    M7_INTENSIDAD: "Densidad e intensidad de aprovechamiento",
    M8_TRANSPORTE: "Rendimiento y viajes de transporte",
}

# Modulos implementados. La interfaz deshabilita el resto en lugar de
# ofrecerlos y fallar.
MODULES_IMPLEMENTED = (
    M1_HIDROLOGIA, M3_PATIOS, M4_COSTO, M5_CAMINOS, M6_ARRASTRE, M7_INTENSIDAD,
    M8_TRANSPORTE,
)

# Calibrado contra la quebrada Shicopreto, concesion Cocama, Madre de Dios,
# sobre Copernicus GLO-30 a resolucion nativa: reproduce el 86.8% del cauce
# levantado en campo a 60 m y el 97.2% a 100 m. Bajar el umbral a 2 ha solo
# agrega 4 puntos de cobertura y triplica la longitud de red.
DEFAULT_THRESHOLD_HA = 22.5

LAYER_RED_HIDRICA = "red_hidrica"
LAYER_FAJAS = "fajas_marginales"
LAYER_PATIOS = "patios_acopio"
LAYER_CENTROS = "centros_acopio"
LAYER_VIAS = "vias"
LAYER_ARRASTRE = "pistas_arrastre"
LAYER_CRUCES = "puntos_cruce"


# --------------------------------------------------------------------------
# Presets por jurisdiccion
# --------------------------------------------------------------------------
#
# Los limites de aprovechamiento no son universales: los fija la norma de cada
# pais. Un preset carga el juego de valores y su fuente, pero NO reemplaza la
# verificacion: las normas cambian, y el DMC por especie debe cargarse aparte
# porque varia por especie y, en algunos paises, tambien por ecorregion.
#
# Cada preset declara la norma de la que proviene para que el informe pueda
# citarla. Un valor en cero significa que ese control no se verifica.

PRESETS = {
    "generico": {
        "etiqueta": "Generico (sin limites)",
        "fuente": "Ningun limite aplicado. Configure los valores manualmente.",
        "max_volumen_ha": 0.0,
        "max_arboles_ha": 0.0,
        "max_area_basal_pct": 0.0,
        "dap_base_ab_cm": 30.0,
        "min_abundancia_ha": 0.0,
        "dap_abundancia_cm": 20.0,
        "semilleros_pct": 0.0,
    },
    "peru": {
        "etiqueta": "Peru",
        "fuente": (
            "Lineamientos del Plan Operativo para concesiones forestales con "
            "fines maderables, RDE 046-2016-SERFOR-DE. El DMC por especie y "
            "las fajas marginales los fijan SERFOR y la ANA respectivamente: "
            "cargue la tabla vigente."
        ),
        "max_volumen_ha": 0.0,
        "max_arboles_ha": 0.0,
        "max_area_basal_pct": 0.0,
        "dap_base_ab_cm": 30.0,
        "min_abundancia_ha": 0.0,
        "dap_abundancia_cm": 20.0,
        "semilleros_pct": 20.0,
    },
    "brasil": {
        "etiqueta": "Brasil",
        "fuente": (
            "La legislacion admite hasta 30 m3/ha con maquinaria pesada y "
            "10 m3/ha en planes de bajo impacto sin maquinaria."
        ),
        "max_volumen_ha": 30.0,
        "max_arboles_ha": 0.0,
        "max_area_basal_pct": 0.0,
        "dap_base_ab_cm": 30.0,
        "min_abundancia_ha": 0.0,
        "dap_abundancia_cm": 20.0,
        "semilleros_pct": 0.0,
    },
    "brasil_bajo_impacto": {
        "etiqueta": "Brasil (bajo impacto, sin maquinaria)",
        "fuente": "Limite de 10 m3/ha para planes de manejo sin maquinaria.",
        "max_volumen_ha": 10.0,
        "max_arboles_ha": 0.0,
        "max_area_basal_pct": 0.0,
        "dap_base_ab_cm": 30.0,
        "min_abundancia_ha": 0.0,
        "dap_abundancia_cm": 20.0,
        "semilleros_pct": 0.0,
    },
    "bolivia": {
        "etiqueta": "Bolivia",
        "fuente": (
            "Ley Forestal 1700. El DMC esta establecido por especie y por "
            "ecorregion. Las especies con abundancia menor a 0.25 por hectarea "
            "para DAP mayor o igual a 20 cm no pueden proponerse en la canasta "
            "de aprovechamiento."
        ),
        "max_volumen_ha": 0.0,
        "max_arboles_ha": 0.0,
        "max_area_basal_pct": 0.0,
        "dap_base_ab_cm": 30.0,
        "min_abundancia_ha": 0.25,
        "dap_abundancia_cm": 20.0,
        "semilleros_pct": 0.0,
    },
    "ecuador": {
        "etiqueta": "Ecuador",
        "fuente": (
            "Normas para el manejo forestal sostenible para aprovechamiento de "
            "madera en bosque humedo, Acuerdo Ministerial 039 de 2004. Remocion "
            "de hasta el 30% del area basal de arboles con DAP mayor o igual a "
            "30 cm, entre 8 y 12 arboles por hectarea segun el tipo de bosque."
        ),
        "max_volumen_ha": 32.0,
        "max_arboles_ha": 12.0,
        "max_area_basal_pct": 30.0,
        "dap_base_ab_cm": 30.0,
        "min_abundancia_ha": 0.0,
        "dap_abundancia_cm": 20.0,
        "semilleros_pct": 0.0,
    },
}

PRESET_POR_DEFECTO = "peru"


# --------------------------------------------------------------------------
# Documentacion
# --------------------------------------------------------------------------
#
# El manual vive en el repositorio y no dentro del complemento: asi se corrige
# sin publicar una version nueva, y el usuario siempre lee la vigente. Cada
# pestana enlaza a su seccion, porque un manual que hay que buscar entero no
# se consulta.

DOCS_BASE = "https://github.com/YuriCaller/yf_forest_planner/blob/main/docs"

DOCS_SECCIONES = {
    "entradas": "01-entradas.md",
    "censo": "02-censo.md",
    "hidrologia": "03-hidrologia.md",
    "patios": "04-patios.md",
    "vias": "05-vias.md",
    "normativa": "06-normativa.md",
    "modulos": "07-modulos.md",
    "salidas": "08-salidas.md",
    "indice": "README.md",
}


def docs_url(seccion: str = "indice") -> str:
    """URL del manual para una pestana."""
    archivo = DOCS_SECCIONES.get(seccion, DOCS_SECCIONES["indice"])
    return f"{DOCS_BASE}/{archivo}"
