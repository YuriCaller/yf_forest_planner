"""M3 - Patios de acopio y centros de acopio.

Dos niveles, distinguidos por funcion y no por tamano:

  PATIO   recibe arrastre. El fuste llega a traccion sobre pista, sin via
          construida. Es efimero y se abandona al terminar la parcela. Su
          ubicacion la manda la distancia maxima de arrastre del equipo.

  CENTRO  recibe transporte. De ahi sale en camion o en balsa, asi que exige
          via de saca con capacidad de carga, superficie estable de maniobra y
          acceso todo el ano. Es la interfaz entre el bosque y la cadena de
          custodia: ahi se cubica, se marca y se emite la guia. Puede
          sobrevivir varias zafras.

ALCANCE DECLARADO: todas las distancias de este modulo son euclidianas. La
distancia real por via de saca la resuelve M5 y puede ser bastante mayor donde
el relieve o los humedales obligan a rodear. Los resultados son una propuesta
de planificacion sujeta a verificacion de campo, no un diseno definitivo.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsRasterLayer,
    QgsSpatialIndex,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

# Valores de arranque para Amazonia peruana. Todos expuestos en la interfaz:
# dependen del equipo de arrastre y de lo que apruebe la autoridad forestal.
# Valores por defecto contrastados con literatura de la Amazonia:
#
#   Silva et al. (2018), Acre, Brasil. Programacion lineal entera de p-medianas
#   para localizar patios minimizando la distancia euclidiana arbol-patio.
#   Patio de 20 x 25 m, capacidad maxima 350 m3, distancia maxima de arrastre
#   400 m, y sitio excluido con pendiente mayor a 15% o en area de proteccion.
#   Acta Amazonica 48: 18-27.
#
#   Braz (1997), Embrapa Acre. Para 20 m3/ha en terreno plano obtuvo distancia
#   media de arrastre de 160 m. El modelo a 400 m de limite reproduce 162 m.
#
#   Johns et al. (1996), Paragominas. Los patios perturbaron 24 m2 por hectarea
#   aprovechada. El modelo a 400 m produce 23 m2/ha sobre el censo de Cocama.
#
# Son valores de arranque, NO constantes normativas: dependen del equipo de
# arrastre y de lo que apruebe la autoridad forestal de cada jurisdiccion.
DEFAULT_VOL_MAX_PATIO = 350.0
DEFAULT_VOL_MIN_PATIO = 40.0
DEFAULT_AREA_PATIO = 500.0
DEFAULT_DIST_MAX_ARRASTRE = 400.0
DEFAULT_PEND_MAX_PATIO = 15.0
DEFAULT_DIST_MIN_CAUCE = 30.0
DEFAULT_AREA_CENTRO = 5000.0
DEFAULT_PEND_MAX_CENTRO = 8.0
DEFAULT_PIEZA_GRANDE = 15.0

# Anchos e indices para estimar el impacto de la operacion. El reuso de pista
# es el parametro que decide si conviene mas patios o menos: por debajo del
# 70% aproximadamente, mas patios reducen el impacto total porque acortan el
# arrastre; por encima, la diferencia se desvanece. Es un dato operativo que
# depende del equipo y de la practica de aprovechamiento de impacto reducido.
DEFAULT_ANCHO_PISTA = 4.0
DEFAULT_ANCHO_RAMAL = 4.0
DEFAULT_REUSO_PISTA = 0.60

SNAP_STEP_M = 25.0
SNAP_RINGS = 8
CONVERGENCIA_M = 1.0


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------


@dataclass
class YardsConfig:
    volumen_max_patio: float = DEFAULT_VOL_MAX_PATIO
    # Por debajo de este volumen el patio se disuelve y sus arboles pasan al
    # vecino. Cero desactiva la fusion.
    volumen_min_patio: float = DEFAULT_VOL_MIN_PATIO
    area_patio_m2: float = DEFAULT_AREA_PATIO
    dist_max_arrastre: float = DEFAULT_DIST_MAX_ARRASTRE
    pendiente_max_patio: float = DEFAULT_PEND_MAX_PATIO
    dist_min_cauce: float = DEFAULT_DIST_MIN_CAUCE

    area_centro_m2: float = DEFAULT_AREA_CENTRO
    pendiente_max_centro: float = DEFAULT_PEND_MAX_CENTRO

    # Umbral de pieza sobredimensionada. No cambia el volumen del patio (doce
    # fustes chicos equivalen a uno grande) pero si la operacion: una pieza de
    # este porte no se arrastra entera, hay que trozarla en el tocon.
    pieza_grande_m3: float = DEFAULT_PIEZA_GRANDE

    # Penalizacion aplicada a la distancia efectiva cuando la recta
    # arbol-patio cruza una faja marginal o un area vetada. Silva et al. (2018)
    # senalan como limitacion de su modelo que algunos arboles quedaron
    # arrastrados a traves de areas de proteccion, y proponen exactamente esto:
    # sumar una constante a la distancia euclidiana para que el optimizador
    # prefiera otro patio. Cero desactiva la penalizacion.
    penalizacion_cruce_m: float = 250.0

    ancho_pista_m: float = DEFAULT_ANCHO_PISTA
    ancho_ramal_m: float = DEFAULT_ANCHO_RAMAL
    reuso_pista: float = DEFAULT_REUSO_PISTA

    escenarios_centros: tuple[int, ...] = (1, 2, 3)

    max_iteraciones: int = 30
    semilla: int = 20260812

    def validate(self) -> list[str]:
        problemas = []
        if self.volumen_max_patio <= 0:
            problemas.append("volumen maximo por patio debe ser mayor que cero")
        if self.dist_max_arrastre <= 0:
            problemas.append("distancia maxima de arrastre debe ser mayor que cero")
        if self.area_patio_m2 <= 0:
            problemas.append("area de patio debe ser mayor que cero")
        if not self.escenarios_centros:
            problemas.append("debe evaluarse al menos un escenario de centros")
        return problemas


# --------------------------------------------------------------------------
# Factibilidad del sitio
# --------------------------------------------------------------------------


class FeasibilityMask:
    """Decide si un punto puede alojar un patio o un centro.

    Cuatro criterios: dentro del area de trabajo, fuera de las geometrias
    excluidas (fajas marginales, vetos, restricciones), pendiente bajo el
    umbral, y separacion minima del cauce.

    Trabaja sobre geometrias con indice espacial en lugar de rasterizar: para
    una parcela de corta el numero de poligonos es bajo, y evita un paso de
    rasterizado con su propia perdida de precision.
    """

    def __init__(
        self,
        area_geom: Optional[QgsGeometry] = None,
        exclusions: Optional[list] = None,
        streams: Optional[list] = None,
        slope_layer: Optional[QgsRasterLayer] = None,
    ):
        self.area_geom = area_geom
        self.slope_layer = slope_layer
        self._slope = slope_layer.dataProvider() if slope_layer is not None else None
        self.sin_pendiente = 0

        self._excl_index, self._excl = self._index(exclusions)
        self._stream_index, self._streams = self._index(streams)

    @staticmethod
    def _index(geoms: Optional[list]):
        index = QgsSpatialIndex()
        store: dict[int, QgsGeometry] = {}
        for i, geom in enumerate(geoms or []):
            if geom is None or geom.isEmpty():
                continue
            feat = QgsFeature(i)
            feat.setGeometry(geom)
            index.addFeature(feat)
            store[i] = geom
        return index, store

    def slope_at(self, x: float, y: float) -> Optional[float]:
        if self._slope is None:
            return None
        value, ok = self._slope.sample(QgsPointXY(x, y), 1)
        if not ok or value is None or math.isnan(value):
            return None
        return float(value)

    def distance_to_stream(self, x: float, y: float, search: float) -> float:
        if not self._streams:
            return float("inf")
        point = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        box = point.boundingBox()
        box.grow(max(search, 1.0))
        best = float("inf")
        for cand in self._stream_index.intersects(box):
            best = min(best, self._streams[cand].distance(point))
        return best

    def is_feasible(
        self, x: float, y: float, max_slope: float, min_stream_dist: float
    ) -> bool:
        point = QgsGeometry.fromPointXY(QgsPointXY(x, y))

        if self.area_geom is not None and not self.area_geom.contains(point):
            return False

        for cand in self._excl_index.intersects(point.boundingBox()):
            if self._excl[cand].contains(point):
                return False

        if min_stream_dist > 0:
            if self.distance_to_stream(x, y, min_stream_dist * 2.0) < min_stream_dist:
                return False

        if max_slope > 0:
            slope = self.slope_at(x, y)
            if slope is None:
                # Sin dato de pendiente NO se descarta el sitio. Excluir por
                # ausencia de dato produce huecos que parecen restricciones
                # reales. Se cuenta para declararlo en el informe.
                self.sin_pendiente += 1
            elif slope > max_slope:
                return False

        return True

    def snap(
        self,
        x: float,
        y: float,
        max_slope: float,
        min_stream_dist: float,
        step: float = SNAP_STEP_M,
        rings: int = SNAP_RINGS,
    ) -> Optional[tuple[float, float, float]]:
        """Desplaza el punto al sitio factible mas cercano.

        Devuelve (x, y, desplazamiento) o None si no halla ninguno dentro del
        radio de busqueda. En ese caso el llamador debe reportarlo, no forzarlo.
        """
        if self.is_feasible(x, y, max_slope, min_stream_dist):
            return (x, y, 0.0)

        for ring in range(1, rings + 1):
            radius = step * ring
            n = max(8, ring * 8)
            for i in range(n):
                angle = 2.0 * math.pi * i / n
                cx = x + radius * math.cos(angle)
                cy = y + radius * math.sin(angle)
                if self.is_feasible(cx, cy, max_slope, min_stream_dist):
                    return (cx, cy, radius)
        return None


# --------------------------------------------------------------------------
# Estructuras
# --------------------------------------------------------------------------


@dataclass
class Yard:
    yid: int = 0
    x: float = 0.0
    y: float = 0.0
    trees: list = field(default_factory=list)
    volumen_m3: float = 0.0
    n_piezas_grandes: int = 0
    dist_media: float = 0.0
    dist_max: float = 0.0
    centro_id: int = -1
    dist_centro: float = 0.0
    desplazado_m: float = 0.0


@dataclass
class Center:
    cid: int = 0
    x: float = 0.0
    y: float = 0.0
    yards: list = field(default_factory=list)
    volumen_m3: float = 0.0
    n_patios: int = 0
    dist_media_saca: float = 0.0
    dist_max_saca: float = 0.0
    desplazado_m: float = 0.0
    ubicable: bool = True


@dataclass
class Scenario:
    n_centros: int = 0
    centers: list = field(default_factory=list)
    volumen_total: float = 0.0

    # Momento de transporte: suma de volumen por distancia patio-centro, en
    # m3 por kilometro. Es la magnitud que mide el esfuerzo de saca y la que
    # hay que contrastar contra el costo de habilitar un centro mas.
    momento_m3km: float = 0.0
    ahorro_pct: float = 0.0

    dist_media_saca: float = 0.0
    dist_max_saca: float = 0.0
    area_centros_ha: float = 0.0

    # Cruces de cauce mayor en las rectas patio-centro. Si un escenario con mas
    # centros los reduce mucho, la topografia esta partiendo el area y el
    # centro adicional no compite con el primero: lo exige el terreno.
    cruces_mayores: int = 0

    centros_no_ubicables: int = 0
    nota: str = ""


@dataclass
class YardsResult:
    patios: Optional[QgsVectorLayer] = None
    centros: Optional[QgsVectorLayer] = None
    arrastre: Optional[QgsVectorLayer] = None

    yards: list = field(default_factory=list)
    centers: list = field(default_factory=list)
    escenarios: list = field(default_factory=list)
    escenario_elegido: int = 0

    n_patios: int = 0
    volumen_asignado: float = 0.0
    volumen_no_asignado: float = 0.0
    n_arboles_no_asignados: int = 0
    n_piezas_grandes: int = 0
    dist_media_arrastre: float = 0.0
    dist_max_arrastre: float = 0.0
    area_patios_ha: float = 0.0
    long_arrastre_total_m: float = 0.0
    area_pistas_ha: float = 0.0
    area_ramales_ha: float = 0.0
    area_impacto_total_ha: float = 0.0
    volumen_medio_patio: float = 0.0
    iteraciones: int = 0
    patios_desplazados: int = 0
    patios_sembrados: int = 0
    patios_fusionados: int = 0
    arrastres_con_cruce: int = 0
    convergio: bool = False

    ok: bool = False
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    def resumen_lines(self) -> list[str]:
        lines = [
            f"Patios: {self.n_patios}",
            f"Volumen asignado: {self.volumen_asignado:,.2f} m3",
            f"Arrastre medio: {self.dist_media_arrastre:.0f} m "
            f"(maximo {self.dist_max_arrastre:.0f} m)",
            f"Piezas sobredimensionadas: {self.n_piezas_grandes}",
            f"Area de patios: {self.area_patios_ha:.2f} ha",
        ]
        lines.append(
            f"Volumen medio por patio: {self.volumen_medio_patio:,.1f} m3"
        )
        lines.append(
            f"Arrastre acumulado: {self.long_arrastre_total_m / 1000:,.1f} km"
        )
        lines.append(
            f"Impacto estimado: {self.area_impacto_total_ha:.2f} ha "
            f"(patios {self.area_patios_ha:.2f} + pistas "
            f"{self.area_pistas_ha:.2f} + ramales {self.area_ramales_ha:.2f})"
        )
        if self.n_arboles_no_asignados:
            lines.append(
                f"Sin asignar: {self.n_arboles_no_asignados} arboles "
                f"({self.volumen_no_asignado:,.2f} m3)"
            )
        return lines


# --------------------------------------------------------------------------
# Agrupamiento
# --------------------------------------------------------------------------


def _seed_weighted(
    xy: np.ndarray, w: np.ndarray, k: int, rng: np.random.Generator
) -> np.ndarray:
    """Siembra k centroides con k-means++ ponderado por volumen.

    La ponderacion importa: sembrar por conteo trata igual a un shihuahuaco de
    30 m3 y a uno de 3 m3, y el patio termina desplazado hacia donde hay muchos
    fustes chicos en vez de hacia donde esta el volumen.
    """
    n = len(xy)
    k = max(1, min(k, n))
    probs = w / w.sum() if w.sum() > 0 else None
    centers = [xy[rng.choice(n, p=probs)]]

    for _ in range(1, k):
        arr = np.asarray(centers)
        d2 = ((xy[:, None, :] - arr[None, :, :]) ** 2).sum(axis=2).min(axis=1)
        scored = d2 * w
        total = scored.sum()
        centers.append(
            xy[rng.choice(n, p=scored / total)] if total > 0 else xy[rng.choice(n)]
        )

    return np.asarray(centers, dtype=float)


def _crossing_penalty(
    xy: np.ndarray,
    centers: np.ndarray,
    barreras: Optional[list],
    penalizacion: float,
) -> Optional[np.ndarray]:
    """Matriz de penalizacion por cruce de area protegida, arbol x patio.

    Devuelve None si no hay barreras o penalizacion, para que el llamador use
    la distancia euclidiana sin costo extra de calculo.

    Solo se evalua para pares dentro de un radio razonable: probar todas las
    combinaciones seria cuadratico y la mayoria son irrelevantes.
    """
    if not barreras or penalizacion <= 0:
        return None

    index = QgsSpatialIndex()
    store: dict[int, QgsGeometry] = {}
    for i, geom in enumerate(barreras):
        if geom is None or geom.isEmpty():
            continue
        feat = QgsFeature(i)
        feat.setGeometry(geom)
        index.addFeature(feat)
        store[i] = geom
    if not store:
        return None

    penal = np.zeros((len(xy), len(centers)), dtype=np.float32)
    for j, (cx, cy) in enumerate(centers):
        destino = QgsPointXY(float(cx), float(cy))
        for i, (tx, ty) in enumerate(xy):
            linea = QgsGeometry.fromPolylineXY(
                [QgsPointXY(float(tx), float(ty)), destino]
            )
            caja = linea.boundingBox()
            for cand in index.intersects(caja):
                if linea.intersects(store[cand]):
                    penal[i, j] = penalizacion
                    break
    return penal


def _assign_capacitated(
    xy: np.ndarray,
    vols: np.ndarray,
    centers: np.ndarray,
    dist_max: float,
    vol_max: float,
    penal: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Asigna cada arbol a un patio respetando distancia y capacidad.

    Los arboles con menos patios al alcance se resuelven primero. En orden
    arbitrario, los del borde -que suelen tener un unico patio posible- quedan
    fuera porque ese patio ya se lleno con arboles del centro, que si tenian
    alternativas.
    """
    from scipy.spatial import cKDTree

    reach = cKDTree(centers).query_ball_point(xy, r=dist_max)
    n_opciones = np.array([len(r) for r in reach])
    orden = np.lexsort((-vols, n_opciones))

    asignacion = np.full(len(xy), -1, dtype=int)
    carga = np.zeros(len(centers), dtype=float)

    for idx in orden:
        candidatos = reach[idx]
        if not candidatos:
            continue
        cand = np.asarray(candidatos)
        d = np.linalg.norm(centers[cand] - xy[idx], axis=1)
        if penal is not None:
            # La penalizacion ordena la preferencia, pero no amplia el alcance:
            # el filtro de distancia real ya se aplico en query_ball_point.
            d = d + penal[idx, cand]
        for j in np.argsort(d):
            c = int(cand[j])
            if carga[c] + vols[idx] <= vol_max:
                asignacion[idx] = c
                carga[c] += vols[idx]
                break

    return asignacion, carga


def _weighted_centroids(
    xy: np.ndarray, w: np.ndarray, asignacion: np.ndarray, k: int
) -> np.ndarray:
    out = np.full((k, 2), np.nan, dtype=float)
    for c in range(k):
        mask = asignacion == c
        if not mask.any():
            continue
        weights = w[mask]
        if weights.sum() <= 0:
            continue
        out[c] = (xy[mask] * weights[:, None]).sum(axis=0) / weights.sum()
    return out


def _merge_small_yards(
    xy: np.ndarray,
    vols: np.ndarray,
    centers: np.ndarray,
    config: "YardsConfig",
) -> tuple[np.ndarray, int]:
    """Elimina patios por debajo del volumen minimo y reasigna sus arboles.

    Se procesa del mas pequeno al mas grande y se recalcula en cada paso: al
    disolver un patio marginal su volumen puede empujar a un vecino por encima
    del umbral, y entonces ese vecino ya no debe eliminarse.

    Un patio solo se disuelve si TODOS sus arboles tienen otro patio dentro del
    radio de arrastre con capacidad libre. Si alguno quedaria huerfano, se
    conserva: perder volumen aprovechable es peor que habilitar un patio chico.
    """
    from scipy.spatial import cKDTree

    fusionados = 0
    # Patios que ya se probaron y no pueden disolverse sin dejar arboles sin
    # patio. Se recuerdan por posicion para no reevaluarlos en cada vuelta.
    intocables: set[tuple[float, float]] = set()

    for _ in range(len(centers) * 2):
        asignacion, carga = _assign_capacitated(
            xy, vols, centers, config.dist_max_arrastre, config.volumen_max_patio
        )
        activos = [c for c in range(len(centers)) if (asignacion == c).any()]
        if len(activos) <= 1:
            break

        candidatos = [
            c for c in activos
            if carga[c] < config.volumen_min_patio
            and (round(centers[c][0], 3), round(centers[c][1], 3)) not in intocables
        ]
        if not candidatos:
            break

        # Del mas pequeno al mas grande: disolver el menor primero puede
        # empujar a un vecino por encima del umbral, y entonces ese vecino ya
        # no debe eliminarse.
        candidatos.sort(key=lambda c: carga[c])
        disuelto = False

        for objetivo in candidatos:
            restantes = [c for c in activos if c != objetivo]
            if not restantes:
                continue

            sel = np.where(asignacion == objetivo)[0]
            alcance = cKDTree(centers[restantes]).query_ball_point(
                xy[sel], r=config.dist_max_arrastre
            )
            if any(len(a) == 0 for a in alcance):
                # Este no se puede disolver sin perder volumen aprovechable,
                # que es peor que habilitar un patio chico. Se prueba el
                # siguiente en lugar de abandonar la fusion entera.
                intocables.add(
                    (round(centers[objetivo][0], 3), round(centers[objetivo][1], 3))
                )
                continue

            centers = centers[np.asarray(restantes)]
            fusionados += 1
            disuelto = True
            break

        if not disuelto:
            break

    return centers, fusionados


def plan_yards(
    trees: list,
    config: YardsConfig,
    feasibility: Optional[FeasibilityMask],
    result: YardsResult,
    barreras: Optional[list] = None,
) -> list:
    """Ubica los patios y asigna cada arbol al suyo."""
    from scipy.spatial import cKDTree

    usable = [
        t for t in trees
        if getattr(t, "aprovechable", True) and getattr(t, "volumen_m3", 0.0) > 0
    ]
    if not usable:
        result.errores.append("No hay individuos aprovechables con volumen.")
        return []

    xy = np.array([[t.x, t.y] for t in usable], dtype=float)
    vols = np.array([t.volumen_m3 for t in usable], dtype=float)
    rng = np.random.default_rng(config.semilla)

    # Numero inicial: el mayor entre lo que exige la capacidad y lo que exige
    # cubrir el area con el radio de arrastre. En llanura amazonica manda casi
    # siempre la cobertura, no el volumen.
    k_vol = math.ceil(vols.sum() / config.volumen_max_patio)
    ancho = float(xy[:, 0].max() - xy[:, 0].min())
    alto = float(xy[:, 1].max() - xy[:, 1].min())
    k_cob = math.ceil(
        max(1.0, ancho * alto) / max(1.0, math.pi * config.dist_max_arrastre ** 2)
    )
    k = max(1, min(len(usable), max(k_vol, k_cob)))

    centers = _seed_weighted(xy, vols, k, rng)

    # FASE 1 - Siembra. Se agregan patios hasta que todo arbol alcanzable
    # queda cubierto. Va aparte del refinamiento: mezclar ambas fases impide
    # converger, porque cada patio nuevo reinicia la medida de estabilidad y
    # el bucle agota las iteraciones sin haber refinado nada.
    for _ in range(config.max_iteraciones):
        asignacion, _ = _assign_capacitated(
            xy, vols, centers, config.dist_max_arrastre, config.volumen_max_patio
        )
        huerfanos = np.where(asignacion < 0)[0]
        if not len(huerfanos):
            break
        nuevos = [xy[huerfanos[int(np.argmax(vols[huerfanos]))]]]
        if len(huerfanos) > 1:
            d, _ = cKDTree(centers).query(xy[huerfanos])
            nuevos.append(xy[huerfanos[int(np.argmax(d))]])
        antes = len(centers)
        centers = np.vstack([centers, np.asarray(nuevos)])
        if len(centers) == antes:
            break
    result.patios_sembrados = len(centers)

    # FASE 2 - Refinamiento. k ya no cambia, asi que el desplazamiento maximo
    # de los centroides es una medida valida de convergencia.
    for iteracion in range(1, config.max_iteraciones + 1):
        asignacion, _ = _assign_capacitated(
            xy, vols, centers, config.dist_max_arrastre, config.volumen_max_patio
        )
        result.iteraciones = iteracion
        actualizados = _weighted_centroids(xy, vols, asignacion, len(centers))
        vivos = ~np.isnan(actualizados[:, 0])
        actualizados = actualizados[vivos]
        if len(actualizados) == 0:
            break

        desplazados = 0
        if feasibility is not None:
            ajustados = []
            for cx, cy in actualizados:
                snapped = feasibility.snap(
                    cx, cy, config.pendiente_max_patio, config.dist_min_cauce
                )
                if snapped is None:
                    ajustados.append((cx, cy))
                else:
                    ajustados.append((snapped[0], snapped[1]))
                    if snapped[2] > 0:
                        desplazados += 1
            actualizados = np.asarray(ajustados, dtype=float)
        result.patios_desplazados = desplazados

        movimiento = None
        if len(actualizados) == len(centers):
            movimiento = float(np.linalg.norm(actualizados - centers, axis=1).max())
        centers = actualizados
        if movimiento is not None and movimiento < CONVERGENCIA_M:
            result.convergio = True
            break

    # FASE 3 - Fusion de patios marginales. Un patio de 600 m2 habilitado para
    # 15 m3 no lo abre nadie en campo: el costo de habilitarlo no se paga con
    # ese volumen. Se disuelven y sus arboles pasan al patio vecino con
    # capacidad, aceptando mas arrastre a cambio de menos infraestructura.
    if config.volumen_min_patio > 0:
        centers, fusionados = _merge_small_yards(
            xy, vols, centers, config
        )
        result.patios_fusionados = fusionados

    # La penalizacion por cruce se aplica en la asignacion definitiva y no en
    # cada iteracion del refinamiento: evaluar la interseccion de cada recta
    # arbol-patio es caro, y durante el refinamiento los centroides todavia se
    # estan moviendo.
    penal = _crossing_penalty(
        xy, centers, barreras, config.penalizacion_cruce_m
    )
    asignacion, _ = _assign_capacitated(
        xy, vols, centers, config.dist_max_arrastre,
        config.volumen_max_patio, penal,
    )
    if penal is not None:
        result.arrastres_con_cruce = int(
            sum(1 for i, c in enumerate(asignacion) if c >= 0 and penal[i, c] > 0)
        )

    yards: list[Yard] = []
    for c in range(len(centers)):
        mask = asignacion == c
        if not mask.any():
            continue
        sel = np.where(mask)[0]
        d = np.linalg.norm(xy[sel] - centers[c], axis=1)
        yard = Yard(
            yid=len(yards) + 1,
            x=float(centers[c][0]),
            y=float(centers[c][1]),
            trees=[usable[i] for i in sel],
            volumen_m3=float(vols[sel].sum()),
            n_piezas_grandes=int((vols[sel] > config.pieza_grande_m3).sum()),
            dist_media=float(d.mean()),
            dist_max=float(d.max()),
        )
        yards.append(yard)
        for pos, i in enumerate(sel):
            setattr(usable[i], "patio_id", yard.yid)
            setattr(usable[i], "dist_arrastre", float(d[pos]))
            setattr(
                usable[i], "_grande", bool(vols[i] > config.pieza_grande_m3)
            )

    huerfanos = np.where(asignacion < 0)[0]
    result.n_arboles_no_asignados = int(len(huerfanos))
    result.volumen_no_asignado = (
        float(vols[huerfanos].sum()) if len(huerfanos) else 0.0
    )
    if len(huerfanos):
        result.advertencias.append(
            f"{len(huerfanos)} individuos ({vols[huerfanos].sum():,.2f} m3) "
            "quedaron fuera del alcance de arrastre o sin capacidad de patio. "
            "Revise la distancia maxima de arrastre o el volumen por patio."
        )

    return yards


# --------------------------------------------------------------------------
# Centros y escenarios
# --------------------------------------------------------------------------


def _count_crossings(
    a: tuple[float, float],
    b: tuple[float, float],
    barriers: Optional[list],
) -> int:
    """Cuenta cruces de cauce mayor en la recta patio-centro.

    Es una aproximacion: la via real no va en linea recta y M5 la trazara. Pero
    basta para detectar cuando la topografia parte el area, que es el caso en
    que un centro adicional no compite con el primero sino que lo exige el
    terreno.
    """
    if not barriers:
        return 0
    line = QgsGeometry.fromPolylineXY([QgsPointXY(*a), QgsPointXY(*b)])
    total = 0
    for barrier in barriers:
        inter = line.intersection(barrier)
        if inter is None or inter.isEmpty():
            continue
        if inter.isMultipart():
            total += len(inter.asGeometryCollection())
        else:
            total += 1
    return total


def evaluate_scenario(
    n_centros: int,
    yards: list,
    config: YardsConfig,
    feasibility: Optional[FeasibilityMask] = None,
    barriers: Optional[list] = None,
    apply: bool = False,
) -> Scenario:
    """Construye y mide un escenario con n centros.

    Si apply es True, escribe la asignacion en los patios. Se usa solo para el
    escenario finalmente elegido, de modo que evaluar no altere el estado.
    """
    scenario = Scenario(n_centros=n_centros)
    if not yards:
        scenario.nota = "sin patios que agrupar"
        return scenario

    xy = np.array([[y.x, y.y] for y in yards], dtype=float)
    vols = np.array([y.volumen_m3 for y in yards], dtype=float)
    rng = np.random.default_rng(config.semilla + n_centros)

    k = max(1, min(n_centros, len(yards)))
    centers = _seed_weighted(xy, vols, k, rng)
    asignacion = np.zeros(len(yards), dtype=int)

    for _ in range(config.max_iteraciones):
        d = np.linalg.norm(xy[:, None, :] - centers[None, :, :], axis=2)
        asignacion = d.argmin(axis=1)
        actualizados = _weighted_centroids(xy, vols, asignacion, len(centers))
        vacios = np.isnan(actualizados[:, 0])
        if vacios.any():
            centers = actualizados[~vacios]
            if len(centers) == 0:
                scenario.nota = "no quedaron centros con patios asignados"
                return scenario
            continue
        movimiento = float(np.linalg.norm(actualizados - centers, axis=1).max())
        centers = actualizados
        if movimiento < CONVERGENCIA_M:
            break

    desplazamientos = np.zeros(len(centers))
    no_ubicables = 0
    if feasibility is not None:
        ajustados = []
        for i, (cx, cy) in enumerate(centers):
            snapped = feasibility.snap(
                cx, cy,
                config.pendiente_max_centro,
                config.dist_min_cauce,
                step=SNAP_STEP_M * 2,
                rings=SNAP_RINGS * 2,
            )
            if snapped is None:
                no_ubicables += 1
                ajustados.append((cx, cy))
            else:
                ajustados.append((snapped[0], snapped[1]))
                desplazamientos[i] = snapped[2]
        centers = np.asarray(ajustados, dtype=float)
    scenario.centros_no_ubicables = no_ubicables
    if no_ubicables:
        scenario.nota = (
            f"{no_ubicables} centro(s) sin sitio factible dentro del radio de "
            "busqueda; se conservo la posicion teorica"
        )

    momento = 0.0
    todas = []
    cruces = 0
    built: list[Center] = []

    for c in range(len(centers)):
        mask = asignacion == c
        if not mask.any():
            continue
        sel = np.where(mask)[0]
        d = np.linalg.norm(xy[sel] - centers[c], axis=1)
        momento += float((vols[sel] * d).sum())
        todas.extend(d.tolist())

        for i in sel:
            cruces += _count_crossings(
                (float(xy[i][0]), float(xy[i][1])),
                (float(centers[c][0]), float(centers[c][1])),
                barriers,
            )

        center = Center(
            cid=len(built) + 1,
            x=float(centers[c][0]),
            y=float(centers[c][1]),
            yards=[yards[i] for i in sel],
            volumen_m3=float(vols[sel].sum()),
            n_patios=int(mask.sum()),
            dist_media_saca=float(d.mean()),
            dist_max_saca=float(d.max()),
            desplazado_m=float(desplazamientos[c]) if c < len(desplazamientos) else 0.0,
            ubicable=True,
        )
        built.append(center)

        if apply:
            for pos, i in enumerate(sel):
                yards[i].centro_id = center.cid
                yards[i].dist_centro = float(d[pos])

    scenario.centers = built
    scenario.volumen_total = float(vols.sum())
    scenario.momento_m3km = momento / 1000.0
    scenario.dist_media_saca = float(np.mean(todas)) if todas else 0.0
    scenario.dist_max_saca = float(np.max(todas)) if todas else 0.0
    scenario.area_centros_ha = len(built) * config.area_centro_m2 / 10000.0
    scenario.cruces_mayores = cruces
    return scenario


def compare_scenarios(
    yards: list,
    config: YardsConfig,
    feasibility: Optional[FeasibilityMask] = None,
    barriers: Optional[list] = None,
) -> list:
    """Evalua cada escenario y calcula el ahorro marginal.

    NO devuelve un ganador. La eleccion depende del costo de habilitar un
    centro frente al ahorro de saca, y ese costo lo conoce el ingeniero, no el
    modelo. El modulo entrega las magnitudes para que la decision quede
    sustentada.
    """
    salidas = []
    for n in sorted({n for n in config.escenarios_centros if n >= 1}):
        salidas.append(evaluate_scenario(n, yards, config, feasibility, barriers))

    for i in range(1, len(salidas)):
        previo = salidas[i - 1].momento_m3km
        if previo > 0:
            salidas[i].ahorro_pct = 100.0 * (previo - salidas[i].momento_m3km) / previo

    # Si un escenario con mas centros reduce a la mitad o menos los cruces de
    # cauce mayor, no es una mejora marginal: la topografia esta partiendo el
    # area y ese centro lo exige el terreno.
    if salidas:
        base = salidas[0]
        for esc in salidas[1:]:
            if base.cruces_mayores >= 2 and esc.cruces_mayores <= base.cruces_mayores // 2:
                esc.nota = (
                    (esc.nota + " | " if esc.nota else "")
                    + f"La topografia parte el area: evita "
                    f"{base.cruces_mayores - esc.cruces_mayores} cruces de cauce "
                    "mayor respecto del escenario base."
                )
                break

    return salidas


def scenario_table(escenarios: list) -> list[str]:
    """Tabla comparativa para el registro y el informe."""
    lines = [
        f"{'centros':>7} {'momento m3km':>13} {'ahorro':>8} "
        f"{'saca media':>11} {'saca max':>9} {'cruces':>7} {'area ha':>8}"
    ]
    for esc in escenarios:
        ahorro = f"{esc.ahorro_pct:6.1f}%" if esc.ahorro_pct else "     --"
        lines.append(
            f"{esc.n_centros:7d} {esc.momento_m3km:13,.1f} {ahorro:>8} "
            f"{esc.dist_media_saca:10,.0f}m {esc.dist_max_saca:8,.0f}m "
            f"{esc.cruces_mayores:7d} {esc.area_centros_ha:8.2f}"
        )
        if esc.nota:
            lines.append(f"         nota: {esc.nota}")
    return lines


# --------------------------------------------------------------------------
# Capas
# --------------------------------------------------------------------------


def _build_yards_layer(yards: list, crs: QgsCoordinateReferenceSystem):
    layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "Patios de acopio", "memory")
    if not layer.isValid():
        return None
    fields = QgsFields()
    for name, kind in (
        ("id_patio", QMetaType.Type.Int),
        ("volumen_m3", QMetaType.Type.Double),
        ("n_arboles", QMetaType.Type.Int),
        ("n_grandes", QMetaType.Type.Int),
        ("arr_medio_m", QMetaType.Type.Double),
        ("arr_max_m", QMetaType.Type.Double),
        ("id_centro", QMetaType.Type.Int),
        ("saca_m", QMetaType.Type.Double),
    ):
        fields.append(QgsField(name, kind))
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()

    feats = []
    for yard in yards:
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(yard.x, yard.y)))
        feat.setAttributes([
            yard.yid, round(yard.volumen_m3, 3), len(yard.trees),
            yard.n_piezas_grandes, round(yard.dist_media, 1),
            round(yard.dist_max, 1), yard.centro_id, round(yard.dist_centro, 1),
        ])
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def _build_haul_layer(yards: list, crs: QgsCoordinateReferenceSystem):
    """Lineas arbol -> patio, una por individuo asignado.

    Es el diagrama de arrastre convergente del plan: cada linea nace en el
    tocon y termina en el patio que lo recibe. Simbolizada con marcador de
    flecha, muestra de un vistazo el sentido de la operacion y permite ver
    donde el arrastre cruza cauce o se alarga de mas.

    La linea es recta: representa la ASIGNACION, no la traza real de la pista.
    La pista efectiva se adapta al terreno y se replantea en campo.
    """
    layer = QgsVectorLayer(
        f"LineString?crs={crs.authid()}", "Arrastre a patios", "memory"
    )
    if not layer.isValid():
        return None

    fields = QgsFields()
    for name, kind in (
        ("id_arbol", QMetaType.Type.QString),
        ("especie", QMetaType.Type.QString),
        ("dap_m", QMetaType.Type.Double),
        ("volumen_m3", QMetaType.Type.Double),
        ("pieza_grande", QMetaType.Type.Int),
        ("id_patio", QMetaType.Type.Int),
        ("id_centro", QMetaType.Type.Int),
        ("dist_m", QMetaType.Type.Double),
        ("azimut", QMetaType.Type.Double),
    ):
        fields.append(QgsField(name, kind))
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()

    feats = []
    for yard in yards:
        destino = QgsPointXY(yard.x, yard.y)
        for tree in yard.trees:
            origen = QgsPointXY(tree.x, tree.y)
            dx = yard.x - tree.x
            dy = yard.y - tree.y
            distancia = math.hypot(dx, dy)
            if distancia <= 0:
                continue
            azimut = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0

            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPolylineXY([origen, destino]))
            feat.setAttributes([
                str(getattr(tree, "codigo", "") or ""),
                str(getattr(tree, "especie", "") or ""),
                round(float(getattr(tree, "dap_m", 0.0) or 0.0), 3),
                round(float(getattr(tree, "volumen_m3", 0.0) or 0.0), 3),
                1 if getattr(tree, "_grande", False) else 0,
                yard.yid,
                yard.centro_id,
                round(distancia, 1),
                round(azimut, 1),
            ])
            feats.append(feat)

    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def _build_centers_layer(centers: list, crs: QgsCoordinateReferenceSystem):
    layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "Centros de acopio", "memory")
    if not layer.isValid():
        return None
    fields = QgsFields()
    for name, kind in (
        ("id_centro", QMetaType.Type.Int),
        ("volumen_m3", QMetaType.Type.Double),
        ("n_patios", QMetaType.Type.Int),
        ("saca_media_m", QMetaType.Type.Double),
        ("saca_max_m", QMetaType.Type.Double),
        ("desplaz_m", QMetaType.Type.Double),
    ):
        fields.append(QgsField(name, kind))
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()

    feats = []
    for center in centers:
        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(center.x, center.y)))
        feat.setAttributes([
            center.cid, round(center.volumen_m3, 3), center.n_patios,
            round(center.dist_media_saca, 1), round(center.dist_max_saca, 1),
            round(center.desplazado_m, 1),
        ])
        feats.append(feat)
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def run(
    trees: list,
    config: YardsConfig,
    crs: QgsCoordinateReferenceSystem,
    feasibility: Optional[FeasibilityMask] = None,
    barriers: Optional[list] = None,
    escenario_preferido: Optional[int] = None,
) -> YardsResult:
    """Ejecuta M3 completo."""
    result = YardsResult()

    problemas = config.validate()
    if problemas:
        result.errores.append("Parametros invalidos: " + ", ".join(problemas))
        return result
    if not trees:
        result.errores.append("No se recibieron individuos del censo.")
        return result

    yards = plan_yards(trees, config, feasibility, result, barriers)
    if not yards:
        if not result.errores:
            result.errores.append("No fue posible ubicar ningun patio.")
        return result

    result.yards = yards
    result.n_patios = len(yards)
    result.volumen_asignado = sum(y.volumen_m3 for y in yards)
    result.n_piezas_grandes = sum(y.n_piezas_grandes for y in yards)
    result.area_patios_ha = len(yards) * config.area_patio_m2 / 10000.0
    result.dist_media_arrastre = float(np.mean([y.dist_media for y in yards]))
    result.dist_max_arrastre = max(y.dist_max for y in yards)
    result.volumen_medio_patio = result.volumen_asignado / max(1, len(yards))

    # Impacto estimado. La pista de arrastre suele dominar sobre el patio en
    # si, asi que reportar solo el area de patios subestima el efecto real y
    # hace invisible el intercambio entre numero de patios y longitud de
    # arrastre.
    result.long_arrastre_total_m = float(
        sum(getattr(t, "dist_arrastre", 0.0) for y in yards for t in y.trees)
    )
    result.area_pistas_ha = (
        result.long_arrastre_total_m
        * config.ancho_pista_m
        * max(0.0, 1.0 - config.reuso_pista)
        / 10000.0
    )
    # Ramal de acceso por patio, estimado con la separacion media entre patios.
    xs = [y.x for y in yards]
    ys_ = [y.y for y in yards]
    envolvente = max(1.0, (max(xs) - min(xs)) * (max(ys_) - min(ys_)))
    separacion = math.sqrt(envolvente / max(1, len(yards)))
    result.area_ramales_ha = (
        len(yards) * separacion * config.ancho_ramal_m / 10000.0
    )
    result.area_impacto_total_ha = (
        result.area_patios_ha + result.area_pistas_ha + result.area_ramales_ha
    )

    result.escenarios = compare_scenarios(yards, config, feasibility, barriers)

    elegido = escenario_preferido
    if elegido is None and result.escenarios:
        elegido = result.escenarios[0].n_centros
    result.escenario_elegido = elegido or 0

    seleccion = next(
        (e for e in result.escenarios if e.n_centros == result.escenario_elegido), None
    )
    if seleccion is not None:
        # Reaplicar el escenario elegido: evaluar no altera el estado de los
        # patios, asi que la asignacion definitiva se escribe una sola vez.
        aplicado = evaluate_scenario(
            seleccion.n_centros, yards, config, feasibility, barriers, apply=True
        )
        result.centers = aplicado.centers

    result.patios = _build_yards_layer(yards, crs)
    result.centros = _build_centers_layer(result.centers, crs)
    result.arrastre = _build_haul_layer(yards, crs)

    if feasibility is not None and feasibility.sin_pendiente:
        result.advertencias.append(
            f"En {feasibility.sin_pendiente} evaluaciones no habia dato de "
            "pendiente y el criterio no pudo aplicarse en ese punto."
        )
    if result.arrastres_con_cruce:
        result.advertencias.append(
            f"{result.arrastres_con_cruce} arrastres cruzan faja marginal o "
            "area vetada pese a la penalizacion. Son los casos donde no habia "
            "patio alternativo al alcance: requieren revision en campo."
        )
    if result.patios_desplazados:
        result.advertencias.append(
            f"{result.patios_desplazados} patios se desplazaron de su centroide "
            "teorico para caer en sitio factible."
        )

    result.advertencias.append(
        "Las distancias de este modulo son euclidianas. La distancia real por "
        "via de saca la resuelve el modulo de caminos y puede ser bastante "
        "mayor donde el relieve o los humedales obligan a rodear."
    )

    result.ok = True
    return result
