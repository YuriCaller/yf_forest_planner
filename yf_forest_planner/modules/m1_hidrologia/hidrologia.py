"""M1 - Red hidrica, ordenes de flujo y fajas marginales.

Motor hidrologico: GRASS r.watershed, que viene con la instalacion estandar de
QGIS y maneja depresiones internamente sin necesidad de un relleno previo.

Los ordenes se calculan aqui y no con r.stream.order, que es un addon de GRASS
sin garantia de estar presente. Se resuelven sobre la topologia de la red ya
vectorizada, en Python puro y sin dependencias externas.

Se calculan los dos ordenes:

  STRAHLER  orden topologico. Solo sube cuando confluyen dos tramos del mismo
            orden, por lo que satura: un rio puede recibir veinte quebradas de
            orden 1 y seguir siendo orden 2. Es el que reconocen la normativa
            y los evaluadores.
  SHREVE    magnitud aditiva. Suma las magnitudes de todos los tramos que
            confluyen, por lo que correlaciona mucho mejor con el caudal real.
            Es el indicado cuando el criterio de faja es hidrologico.

La direccion de flujo de cada tramo se infiere muestreando el DEM en ambos
extremos: el orden de vertices que devuelve r.to.vect no es confiable como
indicador de sentido.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import processing
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

ORDER_STRAHLER = "STRAHLER"
ORDER_SHREVE = "SHREVE"

# Fraccion del tamano de celda usada como tolerancia de union de nodos cuando
# el usuario no la fija. Con celdas de 30 m da 10 m, que es lo razonable para
# coser una red vectorizada desde raster: r.to.vect no garantiza que los tramos
# queden partidos exactamente en las confluencias, y dos extremos separados por
# unos centimetros bastan para que la confluencia nunca se detecte y el orden
# no suba jamas.
# Verificado en QGIS 3.44: r.to.vect con la bandera -s ya entrega la red
# correctamente nodada y el resultado es identico con tolerancia de 1 m o de
# 30 m. La tolerancia importa para redes de otras procedencias.
NODE_TOLERANCE_CELL_FRACTION = 1.0 / 3.0
NODE_TOLERANCE_MIN_M = 0.5

BUFFER_SEGMENTS = 12

# Tablas de arranque, NO constantes normativas. La ANA fija las fajas
# marginales caso por caso y el usuario debe poder editarlas completas.
# La clave es el valor minimo de orden o magnitud al que aplica el ancho.
DEFAULT_STRAHLER_TABLE: dict[int, float] = {1: 10.0, 2: 15.0, 3: 25.0, 4: 50.0}
DEFAULT_SHREVE_TABLE: dict[int, float] = {1: 10.0, 5: 15.0, 20: 25.0, 60: 50.0}


# --------------------------------------------------------------------------
# Configuracion y resultado
# --------------------------------------------------------------------------


@dataclass
class HydroConfig:
    """Parametros del modulo de hidrologia."""

    # Umbral de area drenada para considerar que existe cauce, en hectareas.
    # Se expresa en hectareas y no en celdas para que el valor calibrado siga
    # siendo valido al cambiar de DEM: 250 celdas son 22.5 ha sobre Copernicus
    # de 30 m y 1 ha sobre un raster remuestreado a 2 m, con la misma cifra en
    # pantalla y resultados que no se parecen en nada.
    threshold_ha: float = 22.5

    # Orden usado para asignar el ancho de faja.
    order_mode: str = ORDER_STRAHLER

    width_table: Optional[dict[int, float]] = None
    width_above_table: float = 50.0

    # Tolerancia de union de nodos en metros. None la deriva del tamano de
    # celda, que es lo recomendable.
    node_tolerance_m: Optional[float] = None

    min_segment_m: float = 20.0

    # Suavizado de la red vectorizada. Se aplica DESPUES de resolver la
    # topologia y de asignar ordenes, nunca antes: el suavizado desplaza los
    # extremos de cada tramo y romperia la union de nodos.
    smooth_iterations: int = 1
    smooth_offset: float = 0.25

    single_flow_direction: bool = False
    grass_memory_mb: int = 1000

    def table(self) -> dict[int, float]:
        if self.width_table:
            return self.width_table
        if self.order_mode == ORDER_SHREVE:
            return dict(DEFAULT_SHREVE_TABLE)
        return dict(DEFAULT_STRAHLER_TABLE)

    def width_for(self, value: int) -> float:
        """Ancho de faja para un orden o magnitud dados.

        Se resuelve como el ancho de la mayor clave menor o igual al valor,
        de modo que la misma tabla sirva para Strahler (claves contiguas) y
        para Shreve (claves como umbrales de magnitud).
        """
        table = self.table()
        if not table:
            return float(self.width_above_table)
        keys = sorted(table)
        if value > keys[-1]:
            return float(self.width_above_table)
        chosen = keys[0]
        for key in keys:
            if key <= value:
                chosen = key
            else:
                break
        return float(table[chosen])


@dataclass
class HydroResult:
    """Salidas y diagnostico de M1."""

    red_hidrica: Optional[QgsVectorLayer] = None
    fajas: Optional[QgsVectorLayer] = None
    acumulacion: Optional[str] = None
    direccion: Optional[str] = None

    n_tramos: int = 0
    longitud_total_m: float = 0.0
    strahler_max: int = 0
    shreve_max: int = 0
    longitud_por_orden: dict[int, float] = field(default_factory=dict)
    area_fajas_ha: float = 0.0
    umbral_celdas: int = 0
    tamano_celda_m: float = 0.0

    # Diagnostico topologico. Si estos numeros estan mal, los ordenes estan
    # mal, y sin reportarlos el error pasaria en silencio.
    tolerancia_nodo_m: float = 0.0
    n_nodos: int = 0
    n_confluencias: int = 0
    n_terminales: int = 0
    n_componentes: int = 0
    n_ciclos: int = 0
    n_sin_cota: int = 0

    ok: bool = False
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    def diagnostico_lines(self) -> list[str]:
        """Resumen topologico para el panel y el informe."""
        return [
            f"Tramos: {self.n_tramos}",
            f"Nodos: {self.n_nodos} (tolerancia {self.tolerancia_nodo_m:.2f} m)",
            f"Confluencias: {self.n_confluencias}",
            f"Tramos terminales: {self.n_terminales}",
            f"Componentes conexas: {self.n_componentes}",
            f"Strahler maximo: {self.strahler_max}",
            f"Shreve maximo: {self.shreve_max}",
            f"Longitud total: {self.longitud_total_m / 1000.0:.2f} km",
        ]


# --------------------------------------------------------------------------
# Proveedor GRASS
# --------------------------------------------------------------------------


def grass_algorithm_id(name: str) -> Optional[str]:
    """Resuelve el identificador GRASS segun la version de QGIS.

    El prefijo del proveedor cambio de 'grass7' a 'grass' entre versiones.
    """
    registry = QgsApplication.processingRegistry()
    for prefix in ("grass", "grass7"):
        alg_id = f"{prefix}:{name}"
        if registry.algorithmById(alg_id) is not None:
            return alg_id
    return None


def grass_is_available() -> bool:
    return grass_algorithm_id("r.watershed") is not None


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def run(
    dem_layer: QgsRasterLayer,
    config: HydroConfig,
    crs: QgsCoordinateReferenceSystem,
    clip_geom: Optional[QgsGeometry] = None,
    feedback=None,
) -> HydroResult:
    """Ejecuta la cadena completa de M1.

    dem_layer debe llegar ya reproyectado al SRC de trabajo. Debe ser el DEM a
    resolucion nativa: remuestrear a mayor detalle antes de la hidrologia no
    agrega informacion topografica y si multiplica el costo y los artefactos.
    """
    result = HydroResult()

    if dem_layer is None or not dem_layer.isValid():
        result.errores.append("El DEM no es una capa raster valida.")
        return result

    alg_watershed = grass_algorithm_id("r.watershed")
    alg_tovect = grass_algorithm_id("r.to.vect")
    if alg_watershed is None or alg_tovect is None:
        result.errores.append(
            "El proveedor GRASS no esta disponible en esta instalacion de "
            "QGIS. Activelo en Complementos - Administrar e instalar."
        )
        return result

    cell_area = abs(
        dem_layer.rasterUnitsPerPixelX() * dem_layer.rasterUnitsPerPixelY()
    )
    if cell_area <= 0:
        result.errores.append("No fue posible determinar el tamano de celda.")
        return result

    result.tamano_celda_m = math.sqrt(cell_area)
    result.umbral_celdas = max(
        1, int(round(config.threshold_ha * 10000.0 / cell_area))
    )

    tolerance = config.node_tolerance_m
    if tolerance is None or tolerance <= 0:
        tolerance = max(
            NODE_TOLERANCE_MIN_M,
            result.tamano_celda_m * NODE_TOLERANCE_CELL_FRACTION,
        )
    result.tolerancia_nodo_m = tolerance

    if result.tamano_celda_m > 35.0:
        result.advertencias.append(
            f"El DEM tiene celdas de {result.tamano_celda_m:.1f} m. A esa "
            "resolucion las quebradas menores no se resuelven y las fajas de "
            "orden 1 y 2 seran aproximadas."
        )

    # La region se declara en el SRC DEL DEM, no en el de trabajo. Etiquetar
    # la extension del raster con otro SRC hace que GRASS busque una region
    # que no intersecta el dato: no produce salida, y el algoritmo siguiente
    # falla buscando un archivo que nunca se creo.
    dem_crs = dem_layer.crs()
    if dem_crs.authid() != crs.authid():
        result.errores.append(
            f"El DEM esta en {dem_crs.authid() or 'un SRC sin definir'} y el "
            f"sistema de trabajo es {crs.authid()}. Reproyecte el modelo de "
            "elevacion al SRC de trabajo antes de ejecutar."
        )
        return result
    if dem_crs.isGeographic():
        result.errores.append(
            "El DEM esta en coordenadas geograficas. La hidrologia requiere "
            "un raster proyectado en metros."
        )
        return result

    extent = dem_layer.extent()
    region = (
        f"{extent.xMinimum()},{extent.xMaximum()},"
        f"{extent.yMinimum()},{extent.yMaximum()}"
        f" [{dem_crs.authid()}]"
    )

    try:
        ws = processing.run(
            alg_watershed,
            {
                "elevation": dem_layer,
                "threshold": result.umbral_celdas,
                "memory": config.grass_memory_mb,
                "-s": config.single_flow_direction,
                "-b": False,
                "-4": False,
                "-a": False,
                "-m": False,
                "accumulation": QgsProcessing.TEMPORARY_OUTPUT,
                "drainage": QgsProcessing.TEMPORARY_OUTPUT,
                "stream": QgsProcessing.TEMPORARY_OUTPUT,
                "GRASS_REGION_PARAMETER": region,
                "GRASS_REGION_CELLSIZE_PARAMETER": 0,
                "GRASS_RASTER_FORMAT_OPT": "",
                "GRASS_RASTER_FORMAT_META": "",
            },
            feedback=feedback,
        )
    except Exception as exc:  # noqa: BLE001 - se reporta al usuario
        result.errores.append(f"Fallo r.watershed: {exc}")
        return result

    result.acumulacion = ws.get("accumulation")
    result.direccion = ws.get("drainage")
    stream_raster = ws.get("stream")

    if not stream_raster:
        result.errores.append(
            "r.watershed no genero red de cauces. Pruebe con un umbral menor."
        )
        return result

    try:
        vect = processing.run(
            alg_tovect,
            {
                "input": stream_raster,
                # El enum es 0=line, 1=point, 2=area. Verificado en QGIS 3.44:
                # con type=1 la salida son puntos y el filtro de tipo linea
                # los descarta todos, devolviendo una capa valida pero vacia.
                "type": 0,
                "column": "value",
                "-s": True,
                "-v": False,
                "-z": False,
                "-b": False,
                "-t": False,
                "output": QgsProcessing.TEMPORARY_OUTPUT,
                "GRASS_REGION_PARAMETER": region,
                "GRASS_OUTPUT_TYPE_PARAMETER": 2,
                "GRASS_VECTOR_DSCO": "",
                "GRASS_VECTOR_LCO": "",
                "GRASS_VECTOR_EXPORT_NOCAT": False,
            },
            feedback=feedback,
        )
    except Exception as exc:  # noqa: BLE001 - se reporta al usuario
        result.errores.append(f"Fallo r.to.vect: {exc}")
        return result

    raw_lines = vect.get("output")
    if isinstance(raw_lines, str):
        raw_lines = QgsVectorLayer(raw_lines, "cauces_crudos", "ogr")
    if raw_lines is None or not raw_lines.isValid():
        result.errores.append("La vectorizacion de cauces no produjo geometria.")
        return result

    segments = _read_segments(raw_lines, config.min_segment_m)
    if not segments:
        result.errores.append(
            "No se obtuvieron tramos de cauce. Revise el umbral de area "
            "drenada, probablemente sea demasiado alto para el area."
        )
        return result

    _orient_segments(segments, dem_layer, tolerance, result)
    _diagnose_topology(segments, result)
    _assign_orders(segments, result)

    # El suavizado va aqui: la topologia ya esta resuelta y los ordenes
    # asignados, asi que desplazar los vertices ya no puede romper nada.
    if config.smooth_iterations > 0:
        _smooth_segments(segments, config)

    result.red_hidrica = _build_network_layer(segments, crs, clip_geom)
    result.fajas = _build_buffer_layer(segments, config, crs, clip_geom)

    _summarize(segments, config, result)
    result.ok = True
    return result


# --------------------------------------------------------------------------
# Topologia
# --------------------------------------------------------------------------


@dataclass
class _Segment:
    sid: int
    geom: QgsGeometry
    start: QgsPointXY
    end: QgsPointXY
    length: float
    up_node: tuple = ()
    dn_node: tuple = ()
    strahler: int = 1
    shreve: int = 1


def _node_key(point: QgsPointXY, tolerance: float) -> tuple:
    return (round(point.x() / tolerance), round(point.y() / tolerance))


def _read_segments(layer: QgsVectorLayer, min_length: float) -> list[_Segment]:
    """Extrae tramos simples descartando astillas por debajo de min_length."""
    segments: list[_Segment] = []
    sid = 0
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty() or geom.isNull():
            continue
        parts = (
            geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
        )
        for part in parts:
            if len(part) < 2:
                continue
            part_geom = QgsGeometry.fromPolylineXY(part)
            length = part_geom.length()
            if length < min_length:
                continue
            sid += 1
            segments.append(
                _Segment(
                    sid=sid,
                    geom=part_geom,
                    start=QgsPointXY(part[0]),
                    end=QgsPointXY(part[-1]),
                    length=length,
                )
            )
    return segments


def _orient_segments(
    segments: list[_Segment],
    dem_layer: QgsRasterLayer,
    tolerance: float,
    result: HydroResult,
) -> None:
    """Define aguas arriba y aguas abajo muestreando cotas en los extremos."""
    provider = dem_layer.dataProvider()
    sin_cota = 0

    for seg in segments:
        z_start, ok_start = provider.sample(seg.start, 1)
        z_end, ok_end = provider.sample(seg.end, 1)

        invert = False
        if not ok_start or not ok_end or z_start is None or z_end is None:
            sin_cota += 1
        elif z_end > z_start:
            invert = True

        if invert:
            seg.geom = QgsGeometry.fromPolylineXY(
                list(reversed(seg.geom.asPolyline()))
            )
            seg.start, seg.end = seg.end, seg.start

        seg.up_node = _node_key(seg.start, tolerance)
        seg.dn_node = _node_key(seg.end, tolerance)

    result.n_sin_cota = sin_cota
    if sin_cota:
        result.advertencias.append(
            f"{sin_cota} tramos quedaron sin cota en algun extremo. Su sentido "
            "de flujo se asumio desde el orden de vertices y su orden puede "
            "ser incorrecto."
        )


def _diagnose_topology(segments: list[_Segment], result: HydroResult) -> None:
    """Mide la calidad del cosido de la red antes de calcular ordenes.

    Una red mal cosida produce ordenes uniformemente bajos sin ningun sintoma
    visible: si los tramos no se encuentran en las confluencias, el orden
    simplemente nunca sube. Estos numeros lo delatan.
    """
    incoming: dict[tuple, int] = {}
    outgoing: dict[tuple, int] = {}
    nodes: set = set()

    for seg in segments:
        nodes.add(seg.up_node)
        nodes.add(seg.dn_node)
        incoming[seg.dn_node] = incoming.get(seg.dn_node, 0) + 1
        outgoing[seg.up_node] = outgoing.get(seg.up_node, 0) + 1

    result.n_nodos = len(nodes)
    result.n_confluencias = sum(1 for count in incoming.values() if count >= 2)
    result.n_terminales = sum(
        1 for seg in segments if outgoing.get(seg.dn_node, 0) == 0
    )

    # Componentes conexas sobre el grafo no dirigido, por union-find.
    parent: dict[tuple, tuple] = {node: node for node in nodes}

    def find(node: tuple) -> tuple:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for seg in segments:
        ra, rb = find(seg.up_node), find(seg.dn_node)
        if ra != rb:
            parent[ra] = rb

    result.n_componentes = len({find(node) for node in nodes})

    if result.n_confluencias == 0 and len(segments) > 1:
        result.advertencias.append(
            "No se detecto ninguna confluencia. La red esta mal cosida y todos "
            "los tramos quedaran en orden 1. Aumente la tolerancia de nodo."
        )
    elif result.n_confluencias < len(segments) * 0.05:
        result.advertencias.append(
            f"Solo {result.n_confluencias} confluencias para "
            f"{len(segments)} tramos. Es indicio de que los extremos no se "
            "estan encontrando: pruebe una tolerancia de nodo mayor."
        )

    # Varias componentes son normales: cada drenaje que sale por el borde del
    # area de analisis es una. Solo alarma cuando la fragmentacion domina.
    if result.n_componentes > max(10, len(segments) * 0.25):
        result.advertencias.append(
            f"La red quedo partida en {result.n_componentes} componentes para "
            f"{len(segments)} tramos. Revise la tolerancia de nodo y la "
            "longitud minima de tramo."
        )


def _assign_orders(segments: list[_Segment], result: HydroResult) -> None:
    """Calcula Strahler y Shreve en un solo recorrido aguas arriba.

    Strahler: un tramo sin afluentes es orden 1. Con afluentes toma el mayor
    orden entrante y lo incrementa en uno solo si ese maximo se repite en dos
    o mas afluentes.

    Shreve: un tramo sin afluentes es magnitud 1. Con afluentes es la suma de
    las magnitudes entrantes.

    El recorrido es iterativo con pila explicita. Una red de concesion puede
    encadenar miles de tramos y la recursion fallaria; y en llanura amazonica
    los meandros y brazos anastomosados producen circuitos cerrados reales,
    asi que la deteccion de ciclos no es defensiva sino necesaria.
    """
    incoming: dict[tuple, list[_Segment]] = {}
    for seg in segments:
        incoming.setdefault(seg.dn_node, []).append(seg)

    strahler: dict[int, int] = {}
    shreve: dict[int, int] = {}
    ciclos = 0

    for seed in segments:
        if seed.sid in strahler:
            continue

        stack = [(seed, False)]
        visiting: set = set()

        while stack:
            current, expanded = stack.pop()

            if expanded:
                visiting.discard(current.sid)
                parents = incoming.get(current.up_node, [])
                orders = [strahler[p.sid] for p in parents if p.sid in strahler]
                mags = [shreve[p.sid] for p in parents if p.sid in shreve]

                if not orders:
                    strahler[current.sid] = 1
                    shreve[current.sid] = 1
                else:
                    top = max(orders)
                    strahler[current.sid] = (
                        top + 1 if orders.count(top) >= 2 else top
                    )
                    shreve[current.sid] = sum(mags)
                continue

            if current.sid in strahler:
                continue
            if current.sid in visiting:
                strahler[current.sid] = 1
                shreve[current.sid] = 1
                ciclos += 1
                continue

            visiting.add(current.sid)
            stack.append((current, True))
            for parent in incoming.get(current.up_node, []):
                if parent.sid not in strahler:
                    stack.append((parent, False))

    for seg in segments:
        seg.strahler = strahler.get(seg.sid, 1)
        seg.shreve = shreve.get(seg.sid, 1)

    result.n_ciclos = ciclos
    if ciclos:
        result.advertencias.append(
            f"Se detectaron {ciclos} tramos en circuito cerrado, propios de "
            "zonas planas o de meandros. Se les asigno orden 1."
        )


def _smooth_segments(segments: list[_Segment], config: HydroConfig) -> None:
    """Suaviza la geometria escalonada que deja la vectorizacion desde raster."""
    for seg in segments:
        smoothed = seg.geom.smooth(
            config.smooth_iterations, config.smooth_offset
        )
        if smoothed is not None and not smoothed.isEmpty():
            seg.geom = smoothed


# --------------------------------------------------------------------------
# Construccion de capas
# --------------------------------------------------------------------------


def _build_network_layer(
    segments: list[_Segment],
    crs: QgsCoordinateReferenceSystem,
    clip_geom: Optional[QgsGeometry],
) -> Optional[QgsVectorLayer]:
    layer = QgsVectorLayer(
        f"LineString?crs={crs.authid()}", "Red hidrica", "memory"
    )
    if not layer.isValid():
        return None

    fields = QgsFields()
    fields.append(QgsField("id_tramo", QMetaType.Type.Int))
    fields.append(QgsField("strahler", QMetaType.Type.Int))
    fields.append(QgsField("shreve", QMetaType.Type.Int))
    fields.append(QgsField("long_m", QMetaType.Type.Double))
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()

    feats = []
    for seg in segments:
        geom = seg.geom
        if clip_geom is not None and not clip_geom.isEmpty():
            geom = geom.intersection(clip_geom)
            if geom is None or geom.isEmpty():
                continue
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geom)
        feat.setAttributes(
            [seg.sid, seg.strahler, seg.shreve, round(geom.length(), 2)]
        )
        feats.append(feat)

    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def _build_buffer_layer(
    segments: list[_Segment],
    config: HydroConfig,
    crs: QgsCoordinateReferenceSystem,
    clip_geom: Optional[QgsGeometry],
) -> Optional[QgsVectorLayer]:
    """Genera las fajas marginales disueltas por ancho aplicado.

    Se disuelve por ancho y no en una sola pieza para que el mapa pueda
    diferenciar la faja de una quebrada de la de un rio principal, y para que
    la tabla de areas del informe salga desglosada.
    """
    layer = QgsVectorLayer(
        f"Polygon?crs={crs.authid()}", "Fajas marginales", "memory"
    )
    if not layer.isValid():
        return None

    fields = QgsFields()
    fields.append(QgsField("criterio", QMetaType.Type.QString))
    fields.append(QgsField("clase_min", QMetaType.Type.Int))
    fields.append(QgsField("ancho_m", QMetaType.Type.Double))
    fields.append(QgsField("area_ha", QMetaType.Type.Double))
    layer.dataProvider().addAttributes(fields.toList())
    layer.updateFields()

    use_shreve = config.order_mode == ORDER_SHREVE
    grouped: dict[float, list[QgsGeometry]] = {}
    class_by_width: dict[float, int] = {}

    for seg in segments:
        value = seg.shreve if use_shreve else seg.strahler
        width = config.width_for(value)
        buffered = seg.geom.buffer(width, BUFFER_SEGMENTS)
        if buffered is None or buffered.isEmpty():
            continue
        grouped.setdefault(width, []).append(buffered)
        prev = class_by_width.get(width)
        class_by_width[width] = value if prev is None else min(prev, value)

    # Las clases se resuelven de mayor a menor ancho y cada una se recorta
    # contra las ya emitidas. Sin esto, la faja de un cauce de orden alto se
    # solapa con las de sus tributarias en cada confluencia: el mapa muestra
    # poligonos superpuestos y la suma de areas cuenta dos veces el solape,
    # inflando el area de proteccion declarada en el plan.
    #
    # Gana el ancho mayor, que es el criterio correcto: donde concurren dos
    # fajas rige la mas exigente.
    feats = []
    acumulado: Optional[QgsGeometry] = None

    for width in sorted(grouped, reverse=True):
        merged = QgsGeometry.unaryUnion(grouped[width])
        if merged is None or merged.isEmpty():
            continue

        if acumulado is not None and not acumulado.isEmpty():
            merged = merged.difference(acumulado)
            if merged is None or merged.isEmpty():
                continue

        nuevo = (
            merged
            if acumulado is None
            else QgsGeometry.unaryUnion([acumulado, merged])
        )
        acumulado = nuevo if nuevo is not None and not nuevo.isEmpty() else acumulado

        if clip_geom is not None and not clip_geom.isEmpty():
            merged = merged.intersection(clip_geom)
            if merged is None or merged.isEmpty():
                continue

        # La diferencia puede dejar geometrias invalidas en las confluencias.
        if not merged.isGeosValid():
            reparado = merged.makeValid()
            if reparado is not None and not reparado.isEmpty():
                merged = reparado

        feat = QgsFeature(layer.fields())
        feat.setGeometry(merged)
        feat.setAttributes(
            [
                config.order_mode,
                class_by_width.get(width, 1),
                width,
                round(merged.area() / 10000.0, 4),
            ]
        )
        feats.append(feat)

    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer


def _summarize(
    segments: list[_Segment],
    config: HydroConfig,
    result: HydroResult,
) -> None:
    result.n_tramos = len(segments)
    result.longitud_total_m = sum(s.length for s in segments)
    result.strahler_max = max((s.strahler for s in segments), default=0)
    result.shreve_max = max((s.shreve for s in segments), default=0)

    use_shreve = config.order_mode == ORDER_SHREVE
    por_clase: dict[int, float] = {}
    for seg in segments:
        key = seg.shreve if use_shreve else seg.strahler
        por_clase[key] = por_clase.get(key, 0.0) + seg.length
    result.longitud_por_orden = dict(sorted(por_clase.items()))

    if result.fajas is not None:
        # Las clases son mutuamente excluyentes por construccion, asi que la
        # suma directa ya no cuenta dos veces los solapes de confluencia.
        total = 0.0
        for feat in result.fajas.getFeatures():
            geom = feat.geometry()
            if geom is not None and not geom.isEmpty():
                total += geom.area()
        result.area_fajas_ha = total / 10000.0

    if result.strahler_max <= 1:
        result.advertencias.append(
            "Toda la red quedo en orden Strahler 1. Puede deberse a un umbral "
            "de area drenada muy alto, o a que la red no esta cosida: revise "
            "el numero de confluencias del diagnostico topologico."
        )
