"""Motor de rutas sobre grafo, con pendiente longitudinal real.

Por que un grafo y no un raster de costo
----------------------------------------
Un raster de costo es ISOTROPICO: cada celda vale lo mismo sin importar por
donde se entre ni hacia donde se salga. Eso basta para el terreno, pero no para
la vialidad, porque confunde dos pendientes que la ingenieria trata por
separado:

  TRANSVERSAL   la del terreno atravesado. Determina el movimiento de tierras,
                que puede llegar al 74% del costo de construccion. Es un costo
                creciente, no un limite.

  LONGITUDINAL  la rasante de la via, medida en el sentido de avance. Es la que
                limita si el camion sube cargado, y es un limite duro.

Son independientes: una via puede cruzar una ladera al 40% con rasante del 5%
siguiendo la curva de nivel, pagando mucha obra de tierra. Un raster de costo
no puede distinguirlo porque no sabe en que direccion se mueve.

En un grafo, en cambio, el peso vive en la ARISTA entre dos celdas vecinas, y
ahi si se conoce el desnivel y por tanto la rasante de ese tramo. Es el enfoque
del paquete roads (LandSciTech), cuyo peso de arista es una funcion de las
diferencias de elevacion entre celdas adyacentes que penaliza las pendientes
fuertes.

Ventaja adicional: el grafo es DIRIGIDO, asi que subir y bajar pueden tener
limites distintos. Los estandares de via forestal primaria admiten del orden
del 8% bajando cargado y la mitad subiendo cargado; como se sabe hacia donde
sale la madera, esa asimetria se puede aplicar.

Depende solo de numpy y scipy, ambos presentes en QGIS. No requiere GRASS.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Vecindarios disponibles. El orden importa mas de lo que parece: en una malla
# regular, las rasantes ALCANZABLES estan cuantizadas por los movimientos
# permitidos. Sobre una ladera al 30%, con vecindario de 8 celdas solo se puede
# avanzar al 0%, al 21.2% (diagonal) o al 30% (recto): una rasante del 8% no es
# representable, y la ruta baja mas empinada de lo admisible por pura geometria
# de la malla, no por costo.
#
# Ampliar el vecindario resuelve el problema. Sobre esa misma ladera al 30%:
#   orden 1 (8 vecinos)   -> minima rasante no nula: 21.2%
#   orden 2 (16 vecinos)  -> 13.4%
#   orden 3 (24 vecinos)  -> 9.5%
#   orden 4 (32 vecinos)  -> 7.3%
# El costo es mas aristas y por tanto mas memoria y tiempo, aproximadamente
# proporcional al numero de vecinos.


def _vecindario(orden: int) -> tuple:
    """Desplazamientos del vecindario, sin repetir direcciones colineales.

    Se descartan los pares cuyo maximo comun divisor es mayor que uno: (2, 2)
    es la misma direccion que (1, 1) y solo agregaria aristas redundantes.
    """
    orden = max(1, min(4, int(orden)))
    fuera = []
    for df in range(-orden, orden + 1):
        for dc in range(-orden, orden + 1):
            if df == 0 and dc == 0:
                continue
            if math.gcd(abs(df), abs(dc)) != 1:
                continue
            fuera.append((df, dc))
    return tuple(fuera)


VECINOS = _vecindario(1)

COSTO_PROHIBIDO = 1.0e7


@dataclass
class GradeConfig:
    """Limites y pesos de rasante."""

    # Rasante maxima en el sentido FAVORABLE, es decir bajando cargado, que es
    # el sentido en que viaja la madera hacia la salida.
    rasante_favorable_pct: float = 8.0

    # Rasante maxima en el sentido ADVERSO, subiendo cargado. Los estandares de
    # via primaria admiten aproximadamente la mitad que en favorable.
    rasante_adversa_pct: float = 4.0

    # Longitud de tramo excepcional, en metros, que puede superar la rasante
    # maxima. Sin esta holgura el trazado nunca aceptaria una contrapendiente
    # corta para salvar un collado, y rodearia el cerro entero.
    tramo_excepcional_m: float = 150.0

    # Multiplicador de la rasante maxima admitido en el tramo excepcional.
    factor_excepcional: float = 1.5

    # Peso de la rasante dentro del costo de arista. Cero desactiva el criterio
    # y deja mandar solo al costo de terreno.
    # Orden del vecindario. Uno da 8 vecinos y rasantes muy cuantizadas; tres
    # da 24 vecinos y permite rasantes finas, a costa de mas memoria. En
    # terreno llano el orden 1 basta; en colinas hace falta 3 o 4 para que la
    # via pueda desarrollarse a rasante suave.
    orden_vecindario: int = 3

    peso_rasante: float = 1.0

    # Exponente de la penalizacion por rasante. Por encima de 1 el costo crece
    # mas rapido que la pendiente, que es el comportamiento real del consumo y
    # del desgaste.
    exponente_rasante: float = 2.0

    def validate(self) -> list:
        problemas = []
        if self.rasante_favorable_pct <= 0:
            problemas.append("la rasante favorable debe ser mayor que cero")
        if self.rasante_adversa_pct <= 0:
            problemas.append("la rasante adversa debe ser mayor que cero")
        if self.rasante_adversa_pct > self.rasante_favorable_pct:
            problemas.append(
                "la rasante adversa no deberia superar a la favorable: subir "
                "cargado es mas exigente que bajar"
            )
        return problemas


@dataclass
class RouteResult:
    puntos: list = field(default_factory=list)
    costo: float = 0.0
    longitud_m: float = 0.0
    rasante_max_pct: float = 0.0
    rasante_media_pct: float = 0.0
    desnivel_m: float = 0.0
    tramos_sobre_limite: int = 0
    ok: bool = False
    nota: str = ""


class TerrainGraph:
    """Grafo de la superficie, con pesos que combinan terreno y rasante.

    El grafo se construye una sola vez y se reutiliza para todos los destinos.
    Marcar una via ya trazada solo modifica los pesos, sin reconstruirlo.
    """

    def __init__(
        self,
        costo: np.ndarray,
        dem: np.ndarray,
        geotransform,
        config: GradeConfig,
        valido: Optional[np.ndarray] = None,
    ):
        self.costo = costo.astype(np.float64)
        self.dem = dem.astype(np.float64)
        self.gt = geotransform
        self.config = config
        self.vecinos = _vecindario(getattr(config, "orden_vecindario", 1))
        self.filas, self.cols = costo.shape
        self.n = self.filas * self.cols

        self.px = abs(geotransform[1])
        self.py = abs(geotransform[5])

        if valido is None:
            valido = np.isfinite(costo) & np.isfinite(dem)
        self.valido = valido

        self._construir()

    # ------------------------------------------------------------ estructura

    def _construir(self) -> None:
        """Arma los indices de aristas y su geometria, una sola vez."""
        filas, cols = self.filas, self.cols
        idx = np.arange(self.n).reshape(filas, cols)

        origenes = []
        destinos = []
        distancias = []
        desniveles = []

        for df, dc in self.vecinos:
            f0, f1 = max(0, -df), filas - max(0, df)
            c0, c1 = max(0, -dc), cols - max(0, dc)
            if f0 >= f1 or c0 >= c1:
                continue

            origen = idx[f0:f1, c0:c1]
            destino = idx[f0 + df:f1 + df, c0 + dc:c1 + dc]

            v_origen = self.valido[f0:f1, c0:c1]
            v_destino = self.valido[f0 + df:f1 + df, c0 + dc:c1 + dc]
            usable = v_origen & v_destino

            z_origen = self.dem[f0:f1, c0:c1]
            z_destino = self.dem[f0 + df:f1 + df, c0 + dc:c1 + dc]

            dist = math.hypot(df * self.py, dc * self.px)

            origenes.append(origen[usable])
            destinos.append(destino[usable])
            distancias.append(np.full(usable.sum(), dist))
            desniveles.append((z_destino - z_origen)[usable])

        self.e_origen = np.concatenate(origenes)
        self.e_destino = np.concatenate(destinos)
        self.e_dist = np.concatenate(distancias)
        self.e_desnivel = np.concatenate(desniveles)

        # Rasante del tramo en porcentaje, con signo: positiva es subida en el
        # sentido origen -> destino.
        self.e_rasante = 100.0 * self.e_desnivel / np.maximum(1e-6, self.e_dist)

    # ---------------------------------------------------------------- pesos

    def pesos(self, sentido_carga: str = "hacia_salida") -> np.ndarray:
        """Costo de cada arista.

        sentido_carga indica hacia donde viaja la madera. El trazado se calcula
        desde la salida hacia el destino, de modo que recorrer una arista en
        ese sentido equivale a que el camion la recorra al reves, cargado. Por
        eso el signo se invierte al evaluar el limite.
        """
        cfg = self.config

        # Costo de terreno: media del costo de las dos celdas, por la longitud
        # del tramo. Usar la media y no el valor de destino evita que la ruta
        # se cuele por una celda barata rodeada de caras.
        c_origen = self.costo.ravel()[self.e_origen]
        c_destino = self.costo.ravel()[self.e_destino]
        base = 0.5 * (c_origen + c_destino) * self.e_dist

        if cfg.peso_rasante <= 0:
            return base

        # Rasante que experimenta el vehiculo cargado. Si viaja hacia la
        # salida y el trazado se construye desde la salida, el camion recorre
        # cada arista en sentido contrario al de construccion.
        rasante_carga = -self.e_rasante if sentido_carga == "hacia_salida" else self.e_rasante

        subida = rasante_carga > 0
        limite = np.where(subida, cfg.rasante_adversa_pct, cfg.rasante_favorable_pct)
        limite = np.maximum(0.1, limite)

        magnitud = np.abs(rasante_carga)
        ratio = magnitud / limite

        peso = base * (
            1.0 + cfg.peso_rasante * np.power(np.clip(ratio, 0.0, None),
                                              cfg.exponente_rasante)
        )

        # Por encima del limite el costo se dispara, pero sigue siendo finito:
        # un tramo corto empinado puede ser preferible a rodear un cerro. El
        # control de longitud del tramo excepcional se verifica despues, sobre
        # la ruta ya obtenida.
        excede = magnitud > limite * cfg.factor_excepcional
        peso = np.where(excede, peso * 50.0, peso)

        return peso

    # --------------------------------------------------------------- rutas

    def ruta(
        self,
        origen_rc: tuple,
        destinos_rc: list,
        sentido_carga: str = "hacia_salida",
    ) -> Optional[RouteResult]:
        """Ruta de menor costo desde un origen hasta el destino mas cercano.

        origen_rc puede ser una lista de celdas: Dijkstra multiorigen permite
        conectar contra TODA la red ya construida en una sola pasada, en lugar
        de repetir el calculo contra cada tramo.
        """
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra

        pesos = self.pesos(sentido_carga)
        grafo = csr_matrix(
            (pesos, (self.e_origen, self.e_destino)), shape=(self.n, self.n)
        )

        origenes = origen_rc if isinstance(origen_rc, list) else [origen_rc]
        indices = [f * self.cols + c for f, c in origenes]
        indices = [i for i in indices if 0 <= i < self.n]
        if not indices:
            return None

        dist, predecesor, fuente = dijkstra(
            grafo, directed=True, indices=indices,
            return_predecessors=True, min_only=True,
        )

        objetivos = [f * self.cols + c for f, c in destinos_rc]
        objetivos = [i for i in objetivos if 0 <= i < self.n and np.isfinite(dist[i])]
        if not objetivos:
            return None

        meta = min(objetivos, key=lambda i: dist[i])
        if not np.isfinite(dist[meta]):
            return None

        # Reconstruccion hacia atras desde el destino.
        camino = []
        actual = meta
        vistos = set()
        while actual >= 0 and actual not in vistos:
            vistos.add(actual)
            camino.append(actual)
            actual = predecesor[actual]
        camino.reverse()

        if len(camino) < 2:
            return None

        return self._medir(camino, dist[meta], sentido_carga)

    def _medir(self, camino: list, costo: float, sentido_carga: str) -> RouteResult:
        """Convierte el camino en coordenadas y calcula sus metricas."""
        resultado = RouteResult(costo=float(costo), ok=True)

        puntos = []
        for nodo in camino:
            fila, col = divmod(int(nodo), self.cols)
            x = self.gt[0] + (col + 0.5) * self.gt[1]
            y = self.gt[3] + (fila + 0.5) * self.gt[5]
            puntos.append((x, y))
        resultado.puntos = puntos

        rasantes = []
        largo = 0.0
        for i in range(len(camino) - 1):
            f0, c0 = divmod(int(camino[i]), self.cols)
            f1, c1 = divmod(int(camino[i + 1]), self.cols)
            d = math.hypot((c1 - c0) * self.px, (f1 - f0) * self.py)
            dz = self.dem[f1, c1] - self.dem[f0, c0]
            largo += d
            if d > 0:
                rasantes.append(100.0 * dz / d)

        if rasantes:
            arr = np.abs(np.array(rasantes))
            resultado.rasante_max_pct = float(arr.max())
            resultado.rasante_media_pct = float(arr.mean())
            cfg = self.config
            limite = max(cfg.rasante_favorable_pct, cfg.rasante_adversa_pct)
            resultado.tramos_sobre_limite = int((arr > limite).sum())

        resultado.longitud_m = largo
        resultado.desnivel_m = float(
            self.dem[divmod(int(camino[-1]), self.cols)]
            - self.dem[divmod(int(camino[0]), self.cols)]
        )

        # La holgura de tramo excepcional se verifica sobre la ruta completa:
        # se admite superar la rasante siempre que sea en un trecho corto.
        cfg = self.config
        if resultado.tramos_sobre_limite:
            metros_excedidos = resultado.tramos_sobre_limite * max(self.px, self.py)
            if metros_excedidos > cfg.tramo_excepcional_m:
                resultado.nota = (
                    f"{metros_excedidos:.0f} m de la ruta superan la rasante "
                    f"maxima, por encima de los {cfg.tramo_excepcional_m:.0f} m "
                    "admitidos como tramo excepcional."
                )

        return resultado

    # ------------------------------------------------------------ marcado

    def marcar_via(self, puntos: list, ancho_m: float, factor: float = 0.001) -> None:
        """Abarata las celdas ocupadas por una via ya trazada.

        Se aplica un halo de aproximacion decreciente: sin el, unirse al
        corredor exige un salto de costo y las rutas siguientes corren en
        paralelo en lugar de converger.
        """
        radio = max(2, int(round(ancho_m / max(1.0, self.px) / 2.0)))
        for x, y in puntos:
            col = int((x - self.gt[0]) / self.gt[1])
            fila = int((y - self.gt[3]) / self.gt[5])
            f0, f1 = max(0, fila - radio), min(self.filas, fila + radio + 1)
            c0, c1 = max(0, col - radio), min(self.cols, col + radio + 1)
            if f0 < f1 and c0 < c1:
                self.costo[f0:f1, c0:c1] = np.minimum(
                    self.costo[f0:f1, c0:c1], factor
                )
            h0, h1 = max(0, fila - radio * 3), min(self.filas, fila + radio * 3 + 1)
            g0, g1 = max(0, col - radio * 3), min(self.cols, col + radio * 3 + 1)
            if h0 < h1 and g0 < g1:
                bloque = self.costo[h0:h1, g0:g1]
                self.costo[h0:h1, g0:g1] = np.where(
                    bloque > factor, bloque * 0.4, bloque
                )

    def celda(self, x: float, y: float) -> Optional[tuple]:
        """Fila y columna de una coordenada, o None si cae fuera."""
        col = int((x - self.gt[0]) / self.gt[1])
        fila = int((y - self.gt[3]) / self.gt[5])
        if 0 <= fila < self.filas and 0 <= col < self.cols:
            return (fila, col)
        return None

    def celdas_de(self, puntos: list) -> list:
        salida = []
        for x, y in puntos:
            rc = self.celda(x, y)
            if rc is not None:
                salida.append(rc)
        return salida
