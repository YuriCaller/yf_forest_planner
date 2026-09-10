"""Diametros minimos de corta del Peru.

Fuente: Resolucion Jefatural 458-2002-INRENA, que establecio los DMC a nivel
nacional y sigue vigente. Los valores estan tomados de fuentes publicas que la
citan, incluido el Plan General de Manejo de Forestal Otorongo, elaborado bajo
la RDE 046-2016-SERFOR-DE.

TRES ADVERTENCIAS QUE EL USUARIO DEBE CONOCER
---------------------------------------------
1. La tabla es un PUNTO DE PARTIDA, no la lista definitiva. Una evaluacion del
   modelo de concesiones concluyo que los DMC de la RJ 458-2002-INRENA no
   contribuyen a la sostenibilidad, y que cada concesionario debe determinar y
   justificar los DMC de las especies que planea aprovechar. Varias empresas
   aplican DMC mayores que el normado: Forestal Otorongo corta shihuahuaco a
   70 cm cuando el legal es 51.

2. La correspondencia es por NOMBRE COMUN, que varia entre regiones. Un mismo
   nombre puede designar especies distintas en Loreto y en Madre de Dios. El
   mapeo definitivo debe hacerse por nombre cientifico.

3. Las especies que no figuran aqui NO se verifican por omision. Es preferible
   a inventarles un umbral: el complemento las lista para que el formulador
   complete la tabla con la norma vigente.

El DMC se mide a 1.30 m del suelo, altura del pecho.

Parte de YF Forest Planner.
"""

from __future__ import annotations

# Nombre comun normalizado -> DMC en centimetros.
DMC_PERU = {
    "ana caspi": 41.0,
    "bolaina": 41.0,
    "cachimbo": 41.0,
    "capirona": 41.0,
    "catuaba": 41.0,
    "topa": 41.0,
    "capinuri": 46.0,
    "marupa": 46.0,
    "moena": 46.0,
    "azucar huayo": 51.0,
    "shihuahuaco": 51.0,
    "shihuahuaco negro": 51.0,
    "pumaquiro": 53.0,
    "copaiba": 56.0,
    "ishpingo": 56.0,
    "catahua": 60.0,
    "lagarto caspi": 61.0,
    "tornillo": 61.0,
    "lupuna": 64.0,
    "cedro": 65.0,
    "caoba": 75.0,
}

# Nombre cientifico de las especies confirmadas, para el informe y para que el
# formulador verifique la correspondencia con su censo.
CIENTIFICO = {
    "ana caspi": "Apuleia leiocarpa",
    "azucar huayo": "Hymenaea oblongifolia",
    "bolaina": "Guazuma crinita",
    "cachimbo": "Cariniana decandra",
    "caoba": "Swietenia macrophylla",
    "capirona": "Calycophyllum spruceanum",
    "catahua": "Hura crepitans",
    "catuaba": "Qualea paraensis",
    "cedro": "Cedrela odorata",
    "shihuahuaco": "Dipteryx micrantha",
    "tornillo": "Cedrelinga cateniformis",
}

# Especies incluidas en el Apendice II de la CITES en la COP19 de noviembre de
# 2022, con 24 meses de plazo de implementacion. SERFOR aprobo el plan de
# accion por RDE D000040-2023-MIDAGRI-SERFOR-DE, cuyo primer componente son
# mejoras normativas para estas especies: verifique si de ahi resulto un DMC
# distinto del normado en 2002.
CITES_APENDICE_II = {
    "shihuahuaco": "Dipteryx spp.",
    "shihuahuaco negro": "Dipteryx spp.",
    "charapilla": "Dipteryx spp.",
    "tahuari": "Handroanthus spp.",
}

AVISO_CITES = (
    "Esta especie esta incluida en el Apendice II de la CITES desde la COP19 "
    "de noviembre de 2022. Su comercio internacional exige permiso CITES y "
    "dictamen de extraccion no perjudicial. Verifique las medidas vigentes: el "
    "plan de accion de SERFOR contempla mejoras normativas para estas especies."
)

# DMC propuestos por encima del normado, aplicados por concesiones certificadas
# como criterio de sostenibilidad. Son REFERENCIA, no obligacion: cada titular
# debe determinar y justificar los suyos.
DMC_PROPUESTO_REFERENCIA = {
    "shihuahuaco": 70.0,
}

FUENTE = (
    "Resolucion Jefatural 458-2002-INRENA, que fijo los diametros minimos de "
    "corta a nivel nacional y continua vigente. Verifique la norma actual y "
    "considere que el titular puede proponer y justificar DMC mayores."
)

ADVERTENCIA = (
    "Los DMC de esta tabla son el minimo legal, no necesariamente el "
    "sostenible: una evaluacion del modelo de concesiones concluyo que no "
    "contribuyen a la sostenibilidad y que cada titular debe determinar y "
    "justificar los suyos. La correspondencia es por nombre comun, que varia "
    "entre regiones; verifiquela contra el nombre cientifico."
)
