"""M5 - Red de vias por corredor acumulado.

No es un problema de ruta de menor costo: es diseno de red. Un solo trazo
conecta dos puntos, pero aqui hay que conectar N destinos a una salida
reutilizando tramos, que es un problema tipo Steiner.

El trazado corre sobre un grafo de la superficie, no sobre un raster de costo.
La diferencia importa: un raster es isotropico y confunde la pendiente del
terreno con la rasante de la via, mientras que en un grafo el peso vive en la
arista entre dos celdas y ahi si se conoce el desnivel, y por tanto la rasante
real de ese tramo.

La heuristica es la que usa la ingenieria forestal y si es computable:
construccion incremental por corredor acumulado.

  1. Los destinos se ordenan por volumen descendente.
  2. El primero se traza hacia el punto de salida.
  3. Ese trazo pasa a costo casi nulo en la superficie.
  4. El siguiente busca la RED YA CONSTRUIDA, no el punto de salida.
  5. Se repite.

La red se ramifica sola, en el patron de espina de pescado que se observa en
la Amazonia, pero siguiendo el terreno en lugar de una retícula sistematica.

ADVERTENCIA: el trazo es una PROPUESTA, no un diseno. Se replantea en campo y
la autoridad lo revisa contra el terreno. El modulo lo declara en su salida.

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

from ..m4_costo.costo import _leer
from ...core.router import GradeConfig, TerrainGraph

JERARQUIA_PRINCIPAL = "principal"
JERARQUIA_SECUNDARIA = "secundaria"

# Costo de una celda ya ocupada por via construida. No es cero: reutilizar la
# via tiene un costo operativo, y un cero exacto genera rutas degeneradas que
# recorren la red entera antes de salir.
COSTO_VIA_EXISTENTE = 0.001

# Ancho minimo, en celdas a cada lado, con el que se marca la via ya trazada.
# Marcar una sola celda no basta: la ruta siguiente puede pasar por la celda
# contigua sin llegar a tocarla, y el resultado son haces de vias paralelas en
# lugar de una red ramificada. Con un radio de dos celdas el corredor es lo
# bastante ancho para capturar la ruta vecina.
RADIO_MIN_CELDAS = 2

# Anchos de faja de rodadura, en metros.
ANCHO_PRINCIPAL = 6.0
ANCHO_SECUNDARIA = 4.0

# Tipo de obra sugerida segun el orden del cauce cruzado.
OBRA_POR_ORDEN = {1: "alcantarilla", 2: "alcantarilla", 3: "ponton", 4: "puente"}
OBRA_MAYOR = "puente"


@dataclass
class RoadsConfig:
    """Parametros del trazado."""

    ancho_principal_m: float = ANCHO_PRINCIPAL
    ancho_secundaria_m: float = ANCHO_SECUNDARIA

    # Conectar tambien los patios, no solo los centros. Con muchos patios el
    # tiempo crece: cada destino es un Dijkstra sobre el grafo completo.
    # Los patios reciben arrastre, no transporte: se alcanzan por pista y no
    # por via construida. Conectarlos TODOS multiplica la red y produce un
    # kilometraje irreal; no conectar ninguno deja la mayor parte del volumen
    # sin acceso. La practica real esta en medio: las vias secundarias sirven
    # GRUPOS de patios, y los que quedan cerca de una via ya trazada se
    # alcanzan por pista.
    conectar_patios: bool = True

    # Distancia hasta la cual un patio se considera servido por una via ya
    # existente. Por debajo de ella no se traza ramal: se llega por pista de
    # arrastre. Es el parametro que convierte la red de un ramal por patio en
    # una red de corredores que sirven grupos.
    #
    # Referencia: en Paujil PCA 08, 22.65 km de secundaria sirven del orden de
    # 35 patios sobre 2056 ha, lo que equivale a corredores separados unos
    # cientos de metros y no a un ramal por patio.
    dist_patio_a_via_m: float = 500.0

    # Tope de destinos a conectar. Cero significa sin limite.
    max_destinos: int = 0

    # Orden de Strahler desde el cual se registra el cruce como obra de arte.
    orden_minimo_obra: int = 1

    grade_config: object = None

    # Tolerancia de simplificacion. Sobre celdas de 30 m, un valor cercano a
    # 45 elimina el escalonado sin desplazar el eje de forma apreciable.
    simplificar_m: float = 45.0

    # Iteraciones de suavizado de Chaikin sobre la traza ya simplificada.
    suavizado: int = 2

    # Giro por encima del cual un vertice se considera pico y se elimina,
    # siempre que uno de sus lados sea corto.
    giro_max_grados: float = 110.0
    lado_corto_m: float = 75.0
    # Costo de una via YA EXISTENTE. Menor que el de una recien trazada: una
    # via abierta de zafras anteriores no solo es barata de recorrer, es que no
    # hay que construirla. El trazado debe preferirla siempre que sirva.
    # Costo de una celda ocupada por via preexistente, respecto del costo base.
    # No es cero: una trocha vieja hay que rehabilitarla, y un cero exacto
    # produce rutas degeneradas que recorren la red entera antes de salir.
    #
    #   0.0005 - 0.01  via de saca en buen estado, se usa tal cual
    #   0.05   - 0.30  trocha que exige rehabilitacion
    costo_via_existente: float = 0.0005


@dataclass
class Crossing:
    x: float = 0.0
    y: float = 0.0
    orden: int = 0
    obra: str = ""
    jerarquia: str = ""

    def tipo(self) -> str:
        return self.obra or OBRA_POR_ORDEN.get(self.orden, OBRA_MAYOR)


@dataclass
class RoadsResult:
    vias: Optional[QgsVectorLayer] = None
    cruces: Optional[QgsVectorLayer] = None

    n_tramos: int = 0
    long_principal_m: float = 0.0
    long_secundaria_m: float = 0.0
    long_total_m: float = 0.0
    area_vias_ha: float = 0.0

    n_cruces: int = 0
    cruces_por_tipo: dict = field(default_factory=dict)

    destinos: int = 0
    conectados: int = 0
    no_conectados: list = field(default_factory=list)
    volumen_atrapado: float = 0.0
    rasante_max_pct: float = 0.0
    tramos_sobre_rasante: int = 0
    area_trabajo_ha: float = 0.0
    densidad_m_ha: float = 0.0
    long_fuera_area_m: float = 0.0
    servidos_por_pista: int = 0
    long_existente_m: float = 0.0
    n_tramos_existentes: int = 0

    ok: bool = False
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def resumen_lines(self) -> list:
        lines = [
            f"Tramos trazados: {self.n_tramos}",
            f"Via principal: {self.long_principal_m / 1000:.2f} km",
            f"Via secundaria: {self.long_secundaria_m / 1000:.2f} km",
            f"Longitud total: {self.long_total_m / 1000:.2f} km",
            f"Area de vias: {self.area_vias_ha:.2f} ha",
            f"Destinos conectados: {self.conectados} de {self.destinos}",
        ]
        if self.long_existente_m > 1.0:
            lines.append(
                f"Via existente aprovechada: {self.long_existente_m / 1000:.2f} km "
                f"en {self.n_tramos_existentes} tramos (no computa como impacto)"
            )
        if self.long_existente_m > 1.0:
            lines.append(
                f"Via existente aprovechada: "
                f"{self.long_existente_m / 1000:.2f} km (no computa como "
                "apertura nueva)"
            )
        if self.servidos_por_pista:
            lines.append(
                f"Patios servidos por pista, sin ramal propio: "
                f"{self.servidos_por_pista}"
            )
        if self.long_fuera_area_m > 1.0:
            lines.append(
                f"Traza fuera del area: {self.long_fuera_area_m / 1000:.2f} km"
            )
        if self.densidad_m_ha:
            lines.append(f"Densidad de red: {self.densidad_m_ha:.1f} m/ha")
        if self.rasante_max_pct:
            lines.append(f"Rasante maxima del trazo: {self.rasante_max_pct:.1f}%")
        if self.cruces_por_tipo:
            lines.append(
                "Obras de cruce: "
                + ", ".join(f"{k} {v}" for k, v in sorted(self.cruces_por_tipo.items()))
            )
        return lines


# --------------------------------------------------------------------------
# Afinado de la traza
# --------------------------------------------------------------------------


def _despiker(puntos: list, umbral_grados: float = 110.0,
              lado_corto_m: float = 75.0) -> list:
    """Elimina picos y retrocesos de la traza.

    La ruta sobre malla avanza en pasos discretos, y cuando la direccion ideal
    no coincide con ninguno de los vecinos disponibles alterna entre dos, lo
    que produce diente de sierra. Douglas-Peucker no lo corrige: solo descarta
    vertices proximos a la recta, y un zigzag no lo esta.

    Se quita el vertice intermedio cuando el giro supera el umbral Y alguno de
    los dos lados es corto: un giro cerrado entre tramos largos puede ser una
    curva real del trazado y no debe tocarse.
    """
    salida = list(puntos)
    cambio = True
    while cambio and len(salida) > 2:
        cambio = False
        i = 1
        while i < len(salida) - 1:
            a, b, c = salida[i - 1], salida[i], salida[i + 1]
            v1 = (b.x() - a.x(), b.y() - a.y())
            v2 = (c.x() - b.x(), c.y() - b.y())
            n1 = math.hypot(*v1)
            n2 = math.hypot(*v2)

            quitar = n1 < 1e-6
            if not quitar and n1 > 0 and n2 > 0:
                coseno = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
                giro = math.degrees(math.acos(max(-1.0, min(1.0, coseno))))
                if giro > umbral_grados and min(n1, n2) < lado_corto_m:
                    quitar = True
            if quitar:
                salida.pop(i)
                cambio = True
            else:
                i += 1
    return salida


def _chaikin(puntos: list, iteraciones: int = 2) -> list:
    """Suavizado de Chaikin, que redondea los quiebres sin desplazar el eje.

    Se prefiere a un ajuste por splines porque no sobrepasa la envolvente de
    la poligonal: la traza suavizada nunca se aleja del corredor calculado.
    """
    for _ in range(max(0, iteraciones)):
        if len(puntos) < 3:
            break
        nuevo = [puntos[0]]
        for i in range(len(puntos) - 1):
            a, b = puntos[i], puntos[i + 1]
            nuevo.append(QgsPointXY(
                a.x() * 0.75 + b.x() * 0.25, a.y() * 0.75 + b.y() * 0.25
            ))
            nuevo.append(QgsPointXY(
                a.x() * 0.25 + b.x() * 0.75, a.y() * 0.25 + b.y() * 0.75
            ))
        nuevo.append(puntos[-1])
        puntos = nuevo
    return puntos


def afinar_traza(geom: QgsGeometry, config: "RoadsConfig") -> QgsGeometry:
    """Convierte la traza escalonada en una alineacion utilizable.

    Tres pasos, en este orden: quitar picos, simplificar y suavizar. Invertir
    el orden no funciona, porque simplificar antes de despicar conserva el
    diente de sierra y suavizar antes de simplificar solo lo redondea.

    Medido sobre una red real de 94 tramos y 61 km: los giros mayores a 60
    grados bajaron del 42% al 0.4%, desaparecieron los mayores a 90 grados y
    la longitud cayo a 53 km, es decir casi 8 km que eran zigzag artificial.
    """
    if geom is None or geom.isEmpty():
        return geom
    puntos = (
        geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
    )
    if len(puntos) < 3:
        return geom

    puntos = _despiker(puntos, config.giro_max_grados, config.lado_corto_m)
    simplificada = QgsGeometry.fromPolylineXY(puntos)
    if config.simplificar_m > 0:
        aux = simplificada.simplify(config.simplificar_m)
        if aux is not None and not aux.isEmpty():
            simplificada = aux

    puntos = simplificada.asPolyline()
    if len(puntos) > 2 and config.suavizado > 0:
        puntos = _chaikin(puntos, config.suavizado)
    return QgsGeometry.fromPolylineXY(puntos)


def _avanzar(feedback, indice: int, total: int, result) -> bool:
    """Informa el avance y devuelve False si el usuario cancelo.

    Comun a los dos motores. El objeto de feedback puede no implementar la
    interfaz esperada, en cuyo caso se deja de informar y el trazado sigue: la
    barra de progreso no es parte del resultado.
    """
    if feedback is None:
        return True
    try:
        if feedback.isCanceled():
            result.advertencias.append("Trazado cancelado por el usuario.")
            return False
        feedback.setProgress(int(100 * indice / max(1, total)))
    except AttributeError:
        pass
    return True


def _ancho_de(jerarquia: str, config: "RoadsConfig") -> float:
    """Ancho de faja de rodadura segun la jerarquia del tramo."""
    return (
        config.ancho_principal_m
        if jerarquia == JERARQUIA_PRINCIPAL
        else config.ancho_secundaria_m
    )


def _ya_servido(xy: tuple, jerarquia: str, trazadas: list, config) -> bool:
    """True si el destino queda a tiro de una via ya trazada.

    Un patio a esa distancia se alcanza por pista de arrastre y no necesita
    ramal propio. Sin esta comprobacion la red crece un ramal por patio y el
    kilometraje se dispara muy por encima de lo que se observa en campo.
    """
    if (
        jerarquia != JERARQUIA_SECUNDARIA
        or config.dist_patio_a_via_m <= 0
        or not trazadas
    ):
        return False
    punto = QgsGeometry.fromPointXY(QgsPointXY(xy[0], xy[1]))
    return any(
        g.distance(punto) <= config.dist_patio_a_via_m for g in trazadas
    )


def _trazar_con_grafo(
    costo_path: str,
    dem_layer,
    salida_xy: tuple,
    destinos: list,
    config: RoadsConfig,
    result: "RoadsResult",
    feedback=None,
    vias_existentes: Optional[QgsVectorLayer] = None,
) -> list:
    """Traza toda la red con el motor de grafo.

    A diferencia del motor por raster, la red ya construida se ofrece como
    conjunto de ORIGENES del Dijkstra multiorigen: cada destino se conecta
    contra la red completa en una sola pasada, en lugar de recalcular una
    superficie acumulada por destino.
    """
    costo, _, gt, _ = _leer(costo_path)
    dem, nodata_dem, gt_dem, _ = _leer(dem_layer.source())
    if costo is None or dem is None:
        result.errores.append("No se pudieron leer la superficie de costo y el DEM.")
        return []
    if costo.shape != dem.shape:
        result.errores.append(
            f"La superficie de costo {costo.shape} y el DEM {dem.shape} no "
            "coinciden en dimensiones. Ambos deben derivar del mismo raster."
        )
        return []

    valido = np.isfinite(costo) & np.isfinite(dem)
    if nodata_dem is not None:
        valido &= dem != nodata_dem
    dem = np.where(valido, dem, 0.0)

    grade = config.grade_config or GradeConfig()
    grafo = TerrainGraph(costo, dem, gt, grade, valido)

    hay_existentes = (
        vias_existentes is not None and vias_existentes.featureCount() > 0
    )
    inicio = grafo.celda(*salida_xy) if salida_xy else None
    if inicio is None and not hay_existentes:
        result.errores.append(
            "El punto de salida cae fuera de la superficie de costo y no se "
            "declararon vias existentes que puedan servir de red inicial."
        )
        return []
    inicio = [inicio] if inicio is not None else []

    red_celdas = list(inicio) if isinstance(inicio, list) else [inicio]
    tramos = []
    trazadas: list = []

    # --- red preexistente
    # Una via ya abierta se incorpora como parte de la red desde el inicio: sus
    # celdas pasan a costo casi nulo y se agregan como origen del Dijkstra
    # multiorigen. Asi los destinos convergen a ella igual que a un corredor
    # recien trazado, pero sin computar como impacto nuevo: ya esta construida.
    if vias_existentes is not None and vias_existentes.featureCount():
        largo_existente = 0.0
        for feat in vias_existentes.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            partes = (
                geom.asMultiPolyline() if geom.isMultipart()
                else [geom.asPolyline()]
            )
            for parte in partes:
                if len(parte) < 2:
                    continue
                puntos = [(pt.x(), pt.y()) for pt in parte]
                grafo.marcar_via(
                    puntos, config.ancho_principal_m,
                    factor=config.costo_via_existente,
                )
                red_celdas.extend(grafo.celdas_de(puntos))
                trazadas.append(QgsGeometry.fromPolylineXY(parte))
            largo_existente += geom.length()
        result.long_existente_m = largo_existente
        result.n_tramos_existentes = vias_existentes.featureCount()

    for indice, (xy, jerarquia, etiqueta, volumen) in enumerate(destinos, start=1):
        if not _avanzar(feedback, indice, len(destinos), result):
            break

        if _ya_servido(xy, jerarquia, trazadas, config):
            result.servidos_por_pista += 1
            continue

        meta = grafo.celda(*xy)
        if meta is None:
            result.no_conectados.append(f"{etiqueta} (fuera del area)")
            result.volumen_atrapado += float(volumen or 0.0)
            continue

        ruta = grafo.ruta(red_celdas, [meta])
        if ruta is None or not ruta.ok or len(ruta.puntos) < 2:
            result.no_conectados.append(etiqueta)
            result.volumen_atrapado += float(volumen or 0.0)
            continue

        geom = afinar_traza(
            QgsGeometry.fromPolylineXY(
                [QgsPointXY(x, y) for x, y in ruta.puntos]
            ),
            config,
        )

        ancho = _ancho_de(jerarquia, config)
        tramos.append((geom, jerarquia, etiqueta, ancho))
        trazadas.append(geom)

        result.rasante_max_pct = max(result.rasante_max_pct, ruta.rasante_max_pct)
        if ruta.tramos_sobre_limite:
            result.tramos_sobre_rasante += ruta.tramos_sobre_limite
        if ruta.nota:
            result.advertencias.append(f"{etiqueta}: {ruta.nota}")

        # La traza se abarata y sus celdas pasan a ser origen valido: el
        # siguiente destino se conectara contra la red y no contra la salida.
        grafo.marcar_via(ruta.puntos, ancho)
        red_celdas.extend(grafo.celdas_de(ruta.puntos))
        result.conectados += 1

    return tramos


def _detectar_cruces(
    geom: QgsGeometry,
    red_index: QgsSpatialIndex,
    red: dict,
    jerarquia: str,
    orden_minimo: int,
) -> list:
    """Puntos donde la via cruza un cauce, con la obra sugerida."""
    cruces = []
    if geom is None or geom.isEmpty():
        return cruces
    for cand in red_index.intersects(geom.boundingBox()):
        tramo, orden = red[cand]
        if orden < orden_minimo:
            continue
        inter = geom.intersection(tramo)
        if inter is None or inter.isEmpty():
            continue
        puntos = []
        if inter.type() == 0:
            if inter.isMultipart():
                puntos = inter.asMultiPoint()
            else:
                puntos = [inter.asPoint()]
        else:
            centro = inter.centroid()
            if centro and not centro.isEmpty():
                puntos = [centro.asPoint()]
        for punto in puntos:
            cruces.append(
                Crossing(
                    x=punto.x(), y=punto.y(), orden=int(orden),
                    obra=OBRA_POR_ORDEN.get(int(orden), OBRA_MAYOR),
                    jerarquia=jerarquia,
                )
            )
    return cruces


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def run(
    costo_path: str,
    salida_xy: tuple,
    centros: list,
    patios: list,
    config: RoadsConfig,
    crs: QgsCoordinateReferenceSystem,
    red_hidrica: Optional[QgsVectorLayer] = None,
    dem_layer: Optional[QgsRasterLayer] = None,
    area_geom: Optional[QgsGeometry] = None,
    vias_existentes: Optional[QgsVectorLayer] = None,
    feedback=None,
) -> RoadsResult:
    """Traza la red completa por corredor acumulado."""
    result = RoadsResult()

    if area_geom is not None and not area_geom.isEmpty():
        result.area_trabajo_ha = area_geom.area() / 10000.0

    # Geometrias de la red preexistente, si se declaro.
    existentes = []
    if vias_existentes is not None and vias_existentes.isValid():
        for feat in vias_existentes.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            if area_geom is not None and not area_geom.isEmpty():
                # Solo interesa el tramo util: la porcion que discurre dentro
                # del area mas un margen para alcanzar el borde.
                recorte = area_geom.buffer(1000.0, 8)
                geom = geom.intersection(recorte)
                if geom is None or geom.isEmpty():
                    continue
            existentes.append(geom)

    if dem_layer is None:
        result.errores.append(
            "Falta el modelo de elevacion. El trazado necesita la cota de cada "
            "celda para calcular la rasante de la via, que es distinta de la "
            "pendiente del terreno."
        )
        return result
    if not costo_path:
        result.errores.append("Falta la superficie de costo del modulo M4.")
        return result
    if not salida_xy and vias_existentes is None:
        result.errores.append(
            "Falta el punto de salida. La red necesita un destino hacia donde "
            "converger: empalme con via existente, puerto o campamento. Como "
            "alternativa, declare una capa de vias existentes que ya salga del "
            "area y la red convergira hacia ella."
        )
        return result

    costo, nodata, gt, proj = _leer(costo_path)
    if costo is None:
        result.errores.append("No se pudo leer la superficie de costo.")
        return result

    # indice de la red hidrica para detectar cruces
    red_index = QgsSpatialIndex()
    red: dict = {}
    if red_hidrica is not None:
        campo = red_hidrica.fields().indexOf("strahler")
        for i, feat in enumerate(red_hidrica.getFeatures()):
            if not feat.hasGeometry():
                continue
            temp = QgsFeature(i)
            temp.setGeometry(feat.geometry())
            red_index.addFeature(temp)
            red[i] = (feat.geometry(), feat[campo] if campo >= 0 else 1)

    # --- orden de conexion: primero los centros, por volumen descendente
    destinos = []
    for centro in sorted(centros or [], key=lambda c: -c.volumen_m3):
        destinos.append(((centro.x, centro.y), JERARQUIA_PRINCIPAL,
                         f"centro {centro.cid}", centro.volumen_m3))
    if config.conectar_patios:
        # Se ordenan por volumen descendente. Durante el trazado, cada patio se
        # omite si ya quedo servido por una via anterior, de modo que la red
        # crece por corredores en lugar de por ramales individuales.
        for patio in sorted(patios or [], key=lambda p: -p.volumen_m3):
            destinos.append(((patio.x, patio.y), JERARQUIA_SECUNDARIA,
                             f"patio {patio.yid}", patio.volumen_m3))
    if config.max_destinos > 0:
        destinos = destinos[: config.max_destinos]
    result.destinos = len(destinos)

    if not destinos:
        result.errores.append("No hay centros ni patios que conectar.")
        return result

    tramos = []
    cruces: list = []

    tramos = _trazar_con_grafo(
        costo_path, dem_layer, salida_xy, destinos, config, result,
        feedback, vias_existentes,
    )

    # La traza NO se recorta: salir del area puede ser la solucion correcta
    # cuando la fisiografia bloquea el paso interno. Solo se mide, para que el
    # plan pueda declararlo y sustentar el derecho de paso.
    if area_geom is not None and not area_geom.isEmpty():
        result.long_fuera_area_m = _medir_fuera(tramos, area_geom)

    for geom, jerarquia, _, _ in tramos:
        cruces.extend(
            _detectar_cruces(
                geom, red_index, red, jerarquia, config.orden_minimo_obra
            )
        )

    return _finalizar(result, tramos, cruces, crs, config)


def _medir_fuera(tramos: list, area: QgsGeometry) -> float:
    """Longitud de traza que discurre fuera del area de trabajo, en metros.

    No se recorta: la via puede tener que salir por razones fisiograficas. Se
    mide para que el plan lo declare y para que el area de impacto distinga lo
    que cae dentro del predio de lo que no.
    """
    if area is None or area.isEmpty():
        return 0.0
    total = 0.0
    for geom, _, _, _ in tramos:
        exceso = geom.difference(area)
        if exceso is not None and not exceso.isEmpty():
            total += exceso.length()
    return total


def _finalizar(result, tramos, cruces, crs, config):
    """Construye las capas y las metricas, comun a ambos motores."""
    if not tramos:
        if not result.errores:
            result.errores.append(
                "No se pudo trazar ninguna via. Verifique que el punto de "
                "salida este dentro de la superficie de costo y que no haya "
                "vetos que aislen el area."
            )
        return result

    result.vias = _capa_vias(tramos, crs)
    result.cruces = _capa_cruces(cruces, crs)

    for geom, jerarquia, _, ancho in tramos:
        largo = geom.length()
        result.long_total_m += largo
        if jerarquia == JERARQUIA_PRINCIPAL:
            result.long_principal_m += largo
        else:
            result.long_secundaria_m += largo
        result.area_vias_ha += largo * ancho / 10000.0

    result.n_tramos = len(tramos)
    result.n_cruces = len(cruces)
    for cruce in cruces:
        tipo = cruce.tipo()
        result.cruces_por_tipo[tipo] = result.cruces_por_tipo.get(tipo, 0) + 1

    if result.no_conectados:
        result.advertencias.append(
            f"{len(result.no_conectados)} destinos no se pudieron conectar "
            f"({result.volumen_atrapado:,.2f} m3 atrapados): "
            + ", ".join(result.no_conectados[:6])
            + ("..." if len(result.no_conectados) > 6 else "")
            + ". Revise si un veto aisla esa zona o si el punto de salida es "
            "el adecuado."
        )
    puentes = result.cruces_por_tipo.get(OBRA_MAYOR, 0)
    if puentes >= 3:
        result.advertencias.append(
            f"El trazo requiere {puentes} puentes. Es indicio de que el punto "
            "de salida esta mal elegido o de que conviene un centro de acopio "
            "adicional al otro lado del cauce mayor."
        )
    # Contraste con densidades de redes reales. Braz (1997) obtuvo 16 m/ha como
    # optimo economico en Acre; las redes de Paujil, PCA 07 y 08 en Madre de
    # Dios, dan 13.2 y 15.3 m/ha. Muy por encima de ese rango indica que se
    # esta construyendo via donde bastaria pista de arrastre.
    if result.area_trabajo_ha > 0:
        result.densidad_m_ha = result.long_total_m / result.area_trabajo_ha
        if result.densidad_m_ha > 20.0:
            result.advertencias.append(
                f"La red alcanza {result.densidad_m_ha:.1f} m/ha, por encima "
                "de los 13 a 16 m/ha que se observan en redes reales de la "
                "Amazonia. Suele deberse a conectar cada patio con via "
                "construida: el patio recibe arrastre y se alcanza por pista. "
                "Desactive la conexion de patios o reduzca su numero."
            )

    if result.long_existente_m > 0:
        nueva = result.long_total_m
        total = nueva + result.long_existente_m
        result.advertencias.append(
            f"Se aprovecharon {result.long_existente_m / 1000:.2f} km de via "
            f"existente. La red resultante suma {total / 1000:.2f} km, de los "
            f"cuales {nueva / 1000:.2f} km son de construccion nueva y solo "
            "esos computan en el area de impacto del plan."
        )

    if result.long_fuera_area_m > 100.0:
        result.advertencias.append(
            f"{result.long_fuera_area_m / 1000:.2f} km de via discurren fuera "
            "del area de trabajo. Puede ser la solucion correcta cuando la "
            "fisiografia bloquea el paso interno, pero verifique el derecho de "
            "paso y declare esos tramos aparte: no computan en el impacto del "
            "predio."
        )

    if result.long_principal_m > 0:
        razon = result.long_secundaria_m / result.long_principal_m
        if razon > 5.0:
            result.advertencias.append(
                f"La razon entre via secundaria y principal es 1 a {razon:.1f}. "
                "En redes reales ronda 1 a 3: una proporcion mucho mayor indica "
                "que la principal esta subdimensionada frente al ramaje."
            )

    result.advertencias.append(
        "El trazo es una propuesta tecnica obtenida por modelamiento. Debe "
        "replantearse en campo y no constituye un diseno definitivo."
    )

    result.ok = True
    return result


# --------------------------------------------------------------------------
# Capas
# --------------------------------------------------------------------------


def _capa_vias(tramos: list, crs) -> Optional[QgsVectorLayer]:
    capa = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Vias", "memory")
    if not capa.isValid():
        return None
    campos = QgsFields()
    for nombre, tipo in (
        ("id_via", QMetaType.Type.Int),
        ("jerarquia", QMetaType.Type.QString),
        ("destino", QMetaType.Type.QString),
        ("long_m", QMetaType.Type.Double),
        ("ancho_m", QMetaType.Type.Double),
        ("area_ha", QMetaType.Type.Double),
    ):
        campos.append(QgsField(nombre, tipo))
    capa.dataProvider().addAttributes(campos.toList())
    capa.updateFields()

    feats = []
    for i, (geom, jerarquia, etiqueta, ancho) in enumerate(tramos, start=1):
        largo = geom.length()
        feat = QgsFeature(capa.fields())
        feat.setGeometry(geom)
        feat.setAttributes([
            i, jerarquia, etiqueta, round(largo, 1), ancho,
            round(largo * ancho / 10000.0, 4),
        ])
        feats.append(feat)
    capa.dataProvider().addFeatures(feats)
    capa.updateExtents()
    return capa


def _capa_cruces(cruces: list, crs) -> Optional[QgsVectorLayer]:
    capa = QgsVectorLayer(f"Point?crs={crs.authid()}", "Obras de cruce", "memory")
    if not capa.isValid():
        return None
    campos = QgsFields()
    for nombre, tipo in (
        ("id_cruce", QMetaType.Type.Int),
        ("orden", QMetaType.Type.Int),
        ("obra", QMetaType.Type.QString),
        ("jerarquia", QMetaType.Type.QString),
        ("este", QMetaType.Type.Double),
        ("norte", QMetaType.Type.Double),
    ):
        campos.append(QgsField(nombre, tipo))
    capa.dataProvider().addAttributes(campos.toList())
    capa.updateFields()

    feats = []
    for i, cruce in enumerate(cruces, start=1):
        feat = QgsFeature(capa.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(cruce.x, cruce.y)))
        feat.setAttributes([
            i, cruce.orden, cruce.tipo(), cruce.jerarquia,
            round(cruce.x, 2), round(cruce.y, 2),
        ])
        feats.append(feat)
    capa.dataProvider().addFeatures(feats)
    capa.updateExtents()
    return capa
