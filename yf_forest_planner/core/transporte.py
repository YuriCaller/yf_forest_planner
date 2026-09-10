"""Rendimiento del censo al rollizo y estimacion de viajes de transporte.

El volumen del censo NO es el que sale del bosque. Entre uno y otro hay tres
mermas sucesivas que el plan debe declarar por separado:

  CENSO      volumen comercial en pie, calculado como DAP2 x Hc x factor de
             forma. Es una estimacion geometrica sobre el arbol vivo.

  ROLLIZO    lo que efectivamente se troza y sale del patio. Se pierde por
             individuos no hallados en campo, fustes huecos o podridos,
             rajaduras al derribar, y trozas que no alcanzan dimension
             comercial. Forestal Otorongo declara aprovechar el 70% de los
             arboles aprovechables, y solo el 55% en tornillo, porque al
             momento de la tumba se hacen pruebas de integridad del arbol.

  ASERRADO   lo que rinde el rollizo en el aserradero. Los lineamientos de
             SERFOR fijan 52% como promedio ponderado nacional; un estudio en
             Tahuamanu, Madre de Dios, midio 51.09% con rango de 46.03 a
             56.15% entre especies.

Para contar camiones importa el ROLLIZO, no el aserrado: lo que viaja del
centro de acopio al aserradero es madera en troza.

LIMITE DE CARGA: el camion se llena por PESO o por VOLUMEN, lo que ocurra
primero. Con especies densas como el shihuahuaco manda el peso mucho antes que
el cajon, de modo que estimar viajes solo por volumen los subestima.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .censo import normalize_species

# Rendimiento de censo a rollizo. Valor de arranque conservador tomado de la
# practica declarada por concesiones certificadas. Es MUY variable entre
# especies y operaciones: debe ajustarse con los registros propios.
DEFAULT_RENDIMIENTO = 0.70

# Rendimientos por especie donde hay dato publicado. El tornillo es notorio por
# su alta merma al momento de la tumba.
RENDIMIENTO_ESPECIE = {
    "tornillo": 0.55,
}

# Rendimiento de rollizo a aserrado. Solo informativo: no interviene en el
# conteo de viajes de troza.
RENDIMIENTO_ASERRIO = 0.52


@dataclass
class TransportConfig:
    """Parametros de rendimiento y de la flota."""

    rendimiento_general: float = DEFAULT_RENDIMIENTO
    rendimiento_por_especie: dict = field(
        default_factory=lambda: dict(RENDIMIENTO_ESPECIE)
    )

    # Capacidad del vehiculo. Se aplican ambos limites y manda el menor.
    capacidad_volumen_m3: float = 30.0
    capacidad_peso_t: float = 30.0

    # Densidad de la madera verde, en toneladas por metro cubico. Es la que
    # decide si el camion se llena por peso o por volumen. Las especies densas
    # de la Amazonia superan la tonelada por metro cubico en verde.
    densidad_verde_t_m3: float = 1.0
    densidad_por_especie: dict = field(default_factory=dict)

    # Factor de estiba: fraccion del cajon que ocupa madera solida. Las trozas
    # dejan huecos entre si. Ecuador usa 0.74 como factor unico de conversion
    # de volumen estereo a volumen solido en el control de transporte.
    factor_estiba: float = 0.74

    # Si es True, el volumen declarado en la guia se calcula sobre volumen
    # solido y el cajon se evalua con el factor de estiba.
    aplicar_estiba: bool = True

    def rendimiento(self, especie: str) -> float:
        clave = normalize_species(especie)
        return float(
            self.rendimiento_por_especie.get(clave, self.rendimiento_general)
        )

    def densidad(self, especie: str) -> float:
        clave = normalize_species(especie)
        return float(
            self.densidad_por_especie.get(clave, self.densidad_verde_t_m3)
        )

    def capacidad_efectiva_m3(self, densidad: float) -> tuple:
        """Volumen solido por viaje y cual limite manda.

        Devuelve (m3, motivo).
        """
        por_volumen = self.capacidad_volumen_m3
        if self.aplicar_estiba:
            por_volumen *= max(0.1, min(1.0, self.factor_estiba))
        por_peso = self.capacidad_peso_t / max(0.05, densidad)
        if por_peso < por_volumen:
            return por_peso, "peso"
        return por_volumen, "volumen"

    def validate(self) -> list:
        problemas = []
        if not 0.05 <= self.rendimiento_general <= 1.0:
            problemas.append("el rendimiento debe estar entre 0.05 y 1.0")
        if self.capacidad_volumen_m3 <= 0 or self.capacidad_peso_t <= 0:
            problemas.append("las capacidades deben ser mayores que cero")
        return problemas


@dataclass
class TransportResult:
    volumen_censo: float = 0.0
    volumen_rollizo: float = 0.0
    merma_m3: float = 0.0
    merma_pct: float = 0.0
    volumen_aserrado_ref: float = 0.0

    viajes_total: int = 0
    peso_total_t: float = 0.0
    limite_dominante: str = ""

    por_especie: list = field(default_factory=list)
    por_centro: list = field(default_factory=list)

    ok: bool = False
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def resumen_lines(self) -> list:
        return [
            f"Volumen de censo: {self.volumen_censo:,.2f} m3",
            f"Rollizo estimado: {self.volumen_rollizo:,.2f} m3 "
            f"(merma {self.merma_pct:.1f}%, {self.merma_m3:,.2f} m3)",
            f"Peso estimado: {self.peso_total_t:,.1f} t",
            f"Viajes estimados: {self.viajes_total} "
            f"(limite dominante: {self.limite_dominante})",
            f"Referencia de aserrio: {self.volumen_aserrado_ref:,.2f} m3 "
            f"al {RENDIMIENTO_ASERRIO:.0%}",
        ]

    def tabla_especies(self) -> tuple:
        headers = [
            "Especie", "Arboles", "Vol censo (m3)", "Rendimiento",
            "Vol rollizo (m3)", "Peso (t)", "Viajes", "Limite",
        ]
        return headers, self.por_especie

    def tabla_centros(self) -> tuple:
        headers = [
            "Centro", "Vol censo (m3)", "Vol rollizo (m3)", "Peso (t)", "Viajes",
        ]
        return headers, self.por_centro


def run(trees: list, config: TransportConfig, centros=None) -> TransportResult:
    """Estima rollizo y viajes a partir del censo evaluado."""
    result = TransportResult()

    problemas = config.validate()
    if problemas:
        result.errores.append("Parametros invalidos: " + ", ".join(problemas))
        return result

    usables = [
        t for t in trees
        if getattr(t, "aprovechable", True)
        and float(getattr(t, "volumen_m3", 0.0) or 0.0) > 0
    ]
    if not usables:
        result.errores.append("No hay individuos aprovechables.")
        return result

    agregado = {}
    for tree in usables:
        especie = getattr(tree, "especie", "") or "sin especie"
        clave = normalize_species(especie)
        datos = agregado.setdefault(
            clave, {"nombre": especie, "n": 0, "censo": 0.0}
        )
        datos["n"] += 1
        datos["censo"] += float(tree.volumen_m3)

    limites = {"peso": 0, "volumen": 0}
    for clave, datos in sorted(agregado.items(), key=lambda kv: -kv[1]["censo"]):
        rend = config.rendimiento(datos["nombre"])
        dens = config.densidad(datos["nombre"])
        rollizo = datos["censo"] * rend
        peso = rollizo * dens
        cap, motivo = config.capacidad_efectiva_m3(dens)
        viajes = math.ceil(rollizo / max(0.01, cap))
        limites[motivo] = limites.get(motivo, 0) + viajes

        result.volumen_censo += datos["censo"]
        result.volumen_rollizo += rollizo
        result.peso_total_t += peso
        result.viajes_total += viajes

        result.por_especie.append([
            datos["nombre"], datos["n"], round(datos["censo"], 3),
            f"{rend:.0%}", round(rollizo, 3), round(peso, 2), viajes, motivo,
        ])

    result.merma_m3 = result.volumen_censo - result.volumen_rollizo
    if result.volumen_censo:
        result.merma_pct = 100.0 * result.merma_m3 / result.volumen_censo
    result.volumen_aserrado_ref = result.volumen_rollizo * RENDIMIENTO_ASERRIO
    result.limite_dominante = max(limites, key=lambda k: limites[k])

    # --- por centro de acopio
    if centros:
        for centro in centros:
            censo_c = 0.0
            rollizo_c = 0.0
            peso_c = 0.0
            viajes_c = 0
            for patio in getattr(centro, "yards", []):
                por_esp = {}
                for tree in getattr(patio, "trees", []):
                    if not getattr(tree, "aprovechable", True):
                        continue
                    especie = getattr(tree, "especie", "") or "sin especie"
                    por_esp[especie] = por_esp.get(especie, 0.0) + float(
                        getattr(tree, "volumen_m3", 0.0) or 0.0
                    )
                for especie, vol in por_esp.items():
                    rend = config.rendimiento(especie)
                    dens = config.densidad(especie)
                    rollizo = vol * rend
                    cap, _ = config.capacidad_efectiva_m3(dens)
                    censo_c += vol
                    rollizo_c += rollizo
                    peso_c += rollizo * dens
                    viajes_c += math.ceil(rollizo / max(0.01, cap))
            result.por_centro.append([
                getattr(centro, "cid", 0), round(censo_c, 3),
                round(rollizo_c, 3), round(peso_c, 2), viajes_c,
            ])

    # --- advertencias
    result.advertencias.append(
        "El volumen del censo es una estimacion geometrica sobre el arbol en "
        f"pie. El rollizo aplica un rendimiento del {config.rendimiento_general:.0%} "
        "que recoge individuos no hallados, fustes huecos y rajaduras al "
        "derribar. Ajustelo con los registros de sus propias operaciones: es el "
        "parametro con mayor efecto sobre el numero de viajes."
    )
    if result.limite_dominante == "peso":
        result.advertencias.append(
            "La mayoria de los viajes se llenan por PESO antes que por volumen. "
            "Estimar la flota solo con la capacidad volumetrica del vehiculo "
            "subestimaria el numero de viajes."
        )
    if not config.densidad_por_especie:
        result.advertencias.append(
            f"Se aplico una densidad unica de {config.densidad_verde_t_m3:.2f} "
            "t/m3 a todas las especies. Las densidades de la Amazonia varian "
            "mucho, y el shihuahuaco supera con holgura ese valor en verde: "
            "cargue densidades por especie para afinar el conteo de viajes."
        )

    result.ok = True
    return result
