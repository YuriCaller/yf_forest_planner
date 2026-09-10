"""M4 - Superficie de costo de transitabilidad.

Metodo: analisis de ruta de menor costo sobre raster, que es el enfoque
estandar en planificacion vial forestal. Aguiar et al. (2021) resolvieron el
trazado optimo de vias en la Amazonia brasilena con algoritmos de caminos
minimos considerando restricciones ecologicas, hidrograficas y topograficas;
el paquete roads (LandSciTech) implementa la misma familia de metodos con
penalizacion de pendiente para proyectar redes viales.

Las restricciones de diseno de la ingenieria vial forestal incluyen la
pendiente maxima segun el vehiculo y el angulo optimo de cruce de cauce para
proteccion del curso de agua. Este modulo traduce ambas a costo por celda: la
pendiente con una funcion no lineal con umbral, y el cruce mediante un
impedimento moderado en la faja que hace mas barato atravesarla en
perpendicular (dos celdas) que recorrerla en paralelo (veinte).

Produce el raster que M5 consume para trazar las vias. No tiene valor por si
solo: existe unicamente para alimentar el trazado.

El costo de cada celda combina cuatro terminos, todos expuestos como pesos en
la interfaz:

  PENDIENTE   funcion no lineal con umbral. Por debajo del limite de la
              jerarquia el costo crece suave; por encima se dispara. Un limite
              duro produciria trazos que serpentean pegados a la curva de
              nivel; un costo lineal ignoraria que una via al 20% no cuesta el
              doble que una al 10%, cuesta mucho mas.

  HUMEDAD     penalizacion del modulo M2, o cero si no se ejecuto.

  RESTRICCION distancia decaida a las areas excluidas, para que el trazo se
              aleje del borde en lugar de pegarse a el. Las capas tematicas
              regionales tienen incertidumbre de cientos de metros y su borde
              no esta donde lo dibujaron.

  CRUCE       costo adicional al atravesar un cauce, escalado por orden. Es
              finito y no infinito: cruzar es una obra que se presupuesta, no
              una prohibicion. Con costo infinito el algoritmo no distingue
              cruzar de recorrer en paralelo, y no pasa nunca.

Los pesos no se deducen de los datos: son un juicio de ingenieria. El modulo
los registra para que la decision quede sustentada, no para presentarlos como
un optimo objetivo.

Parte de YF Forest Planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import processing
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingUtils,
    QgsRasterLayer,
    QgsVectorLayer,
)

# Costo base de una celda transitable sin restricciones. Se usa como unidad
# para que los demas terminos se lean como multiplicadores.
COSTO_BASE = 1.0

# Costo asignado a celdas vetadas. Alto pero FINITO: un valor infinito
# desconecta el grafo y r.cost devuelve area inalcanzable en lugar de un trazo
# con una advertencia.
COSTO_VETO = 100000.0

# Pendiente maxima por jerarquia, en porcentaje. No hay regla absoluta para
# fijarla: la guia de ingenieria de vias forestales de Columbia Britanica
# senala que la pendiente maxima no puede establecerse sin analizar la mas
# economica para las condiciones del sitio. Los valores de arranque siguen los
# estandares de via primaria, del orden de 8% con tramos cortos hasta 12%.
PENDIENTE_MAX_PRINCIPAL = 8.0
PENDIENTE_MAX_SECUNDARIA = 12.0

# Costo de cruce por orden de Strahler, en unidades de celda equivalente. Una
# alcantarilla apenas pesa; un puente debe costar el equivalente a varios
# cientos de metros de via para que el algoritmo busque activamente el punto
# de cruce mas conveniente antes de resolverlo.
CRUCE_POR_ORDEN = {1: 20.0, 2: 60.0, 3: 200.0, 4: 800.0}
CRUCE_SOBRE_TABLA = 1500.0


@dataclass
class CostConfig:
    """Pesos y umbrales de la superficie de costo."""

    peso_pendiente: float = 1.0
    peso_humedad: float = 1.5
    peso_restriccion: float = 1.0
    peso_cruce: float = 1.0

    pendiente_max: float = PENDIENTE_MAX_PRINCIPAL

    # Exponente de la funcion de pendiente. Por encima de 1 el costo crece mas
    # rapido que la pendiente, que es el comportamiento real: el movimiento de
    # tierras y las obras de drenaje escalan de forma no lineal.
    exponente_pendiente: float = 2.0

    # Multiplicador aplicado al superar la pendiente maxima. No es infinito:
    # un tramo corto empinado puede ser preferible a un rodeo de kilometros.
    penalizacion_sobre_limite: float = 25.0

    # Banda de decaimiento alrededor de las areas restringidas, en metros.
    banda_restriccion_m: float = 150.0

    # Multiplicador aplicado DENTRO de una faja marginal o area de impedimento.
    # Moderado a proposito. El costo real de la obra de cruce lo lleva la tabla
    # cruce_por_orden; este factor solo desalienta RECORRER la faja en paralelo.
    #
    # La geometria hace el resto sin necesidad de reglas adicionales: cruzar en
    # perpendicular son dos o tres celdas, recorrer en paralelo son veinte. Es
    # el criterio de ingenieria vial forestal, donde el angulo optimo de cruce
    # es la restriccion de diseno para proteger el curso de agua.
    #
    # Con un factor alto el algoritmo rodea siempre y nunca cruza, y el drenaje
    # termina fragmentando el area en trozos incomunicados.
    factor_impedimento: float = 8.0

    # Multiplicador aplicado FUERA del area de trabajo. Un valor moderado deja
    # que el trazo salga cuando la fisiografia lo justifica, sin que se vuelva
    # la opcion por defecto. Ponerlo en 100000 equivale a prohibirlo.
    factor_fuera_area: float = 6.0

    cruce_por_orden: dict = field(default_factory=lambda: dict(CRUCE_POR_ORDEN))
    cruce_sobre_tabla: float = CRUCE_SOBRE_TABLA

    # Preferir divisorias de agua. Es el metodo clasico del trazado amazonico
    # en llanura: se corre el analisis de cuencas, cada divisoria de microcuenca
    # es la zona mas alta, y por ahi va el vial. Se evita el drenaje y el suelo
    # hidromorfico sin necesidad de modelar la humedad.
    #
    # NO sirve en relieve montanoso: alli la divisoria pasa por las cumbres y
    # trazar por lo alto seria absurdo. En ese caso manda la pendiente y el
    # costo de menor recorrido. Por eso el peso es configurable y el modulo
    # advierte cuando el relieve no corresponde al metodo.
    peso_lomas: float = 0.0
    # Banda de atraccion a la divisoria. Debe ser MUCHO menor que el
    # espaciamiento entre divisorias: si la banda las cubre todas, el descuento
    # se aplica en todas partes por igual y la superficie queda uniforme, sin
    # capacidad de guiar el trazado. Con celdas de 30 m, 60 m son dos celdas a
    # cada lado, que es lo que hace falta para que la ruta prefiera la cresta
    # sin borrar el resto del relieve.
    banda_lomas_m: float = 60.0

    # Umbral de area drenada, en hectareas, para extraer la red de divisorias
    # del DEM invertido. Un valor mayor produce menos divisorias y de mayor
    # jerarquia, que es lo apropiado para el vial principal.
    umbral_lomas_ha: float = 50.0

    # Desnivel por encima del cual el metodo de divisorias deja de aplicarse:
    # en relieve montanoso la divisoria pasa por las cumbres.
    desnivel_max_lomas_m: float = 120.0

    def costo_cruce(self, orden: int) -> float:
        if orden in self.cruce_por_orden:
            return float(self.cruce_por_orden[orden])
        if not self.cruce_por_orden:
            return float(self.cruce_sobre_tabla)
        if orden > max(self.cruce_por_orden):
            return float(self.cruce_sobre_tabla)
        return float(self.cruce_por_orden[min(self.cruce_por_orden)])


@dataclass
class CostResult:
    costo_path: str = ""
    pendiente_path: str = ""
    capa: Optional[QgsRasterLayer] = None

    celdas: int = 0
    celdas_vetadas: int = 0
    celdas_impedidas: int = 0
    celdas_fuera_area: int = 0
    costo_min: float = 0.0
    costo_max: float = 0.0
    costo_medio: float = 0.0
    pendiente_media: float = 0.0
    lomas_path: str = ""
    lomas_km: float = 0.0
    desnivel_m: float = 0.0
    metodo_lomas_aplicado: bool = False
    variacion_relativa: float = 0.0
    pendiente_max_hallada: float = 0.0
    pct_sobre_limite: float = 0.0

    ok: bool = False
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def resumen_lines(self) -> list:
        return [
            f"Celdas evaluadas: {self.celdas:,d}",
            f"Celdas fuera del area de trabajo: {self.celdas_fuera_area:,d}",
            f"Celdas con impedimento: {self.celdas_impedidas:,d} "
            f"({100 * self.celdas_impedidas / max(1, self.celdas):.2f}%)",
            f"Celdas vetadas: {self.celdas_vetadas:,d} "
            f"({100 * self.celdas_vetadas / max(1, self.celdas):.2f}%)",
            f"Pendiente media: {self.pendiente_media:.2f}% "
            f"(maxima {self.pendiente_max_hallada:.1f}%)",
            f"Superficie sobre el limite de pendiente: {self.pct_sobre_limite:.2f}%",
            f"Costo por celda: {self.costo_min:.2f} a {self.costo_max:,.0f} "
            f"(medio {self.costo_medio:.2f})",
            f"Variacion relativa: {self.variacion_relativa:.2f} "
            f"(bajo 0.15 no guia el trazado)",
        ]


# --------------------------------------------------------------------------
# Utilidades raster
# --------------------------------------------------------------------------


def _tmp(extension: str = ".tif") -> str:
    return QgsProcessingUtils.generateTempFilename(f"fp_costo{extension}")


def _leer(path: str):
    """Lee un raster de una banda como arreglo, con su geotransformacion."""
    from osgeo import gdal

    ds = gdal.Open(path)
    if ds is None:
        return None, None, None, None
    banda = ds.GetRasterBand(1)
    datos = banda.ReadAsArray().astype(np.float32)
    nodata = banda.GetNoDataValue()
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None
    return datos, nodata, gt, proj


def _escribir(path: str, datos: np.ndarray, gt, proj, nodata: float = -9999.0):
    from osgeo import gdal

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        path, datos.shape[1], datos.shape[0], 1, gdal.GDT_Float32,
        options=["COMPRESS=DEFLATE", "TILED=YES"],
    )
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    banda = ds.GetRasterBand(1)
    banda.SetNoDataValue(nodata)
    banda.WriteArray(np.nan_to_num(datos, nan=nodata))
    ds.FlushCache()
    ds = None
    return path


def _rasterizar(capa, template: QgsRasterLayer, quemar: float = 1.0,
                campo: Optional[str] = None, feedback=None) -> Optional[str]:
    """Rasteriza un vector sobre la grilla del template."""
    if capa is None:
        return None
    extent = template.extent()
    ext = (
        f"{extent.xMinimum()},{extent.xMaximum()},"
        f"{extent.yMinimum()},{extent.yMaximum()}"
        f" [{template.crs().authid()}]"
    )
    params = {
        "INPUT": capa,
        "BURN": quemar,
        "UNITS": 0,
        "WIDTH": template.width(),
        "HEIGHT": template.height(),
        "EXTENT": ext,
        "NODATA": 0,
        "DATA_TYPE": 5,
        "INIT": 0,
        "INVERT": False,
        "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
    }
    if campo:
        params["FIELD"] = campo
        params.pop("BURN")
    salida = processing.run("gdal:rasterize", params, feedback=feedback)
    return salida.get("OUTPUT")


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def run(
    dem_layer: QgsRasterLayer,
    config: CostConfig,
    crs: QgsCoordinateReferenceSystem,
    red_hidrica: Optional[QgsVectorLayer] = None,
    exclusiones: Optional[list] = None,
    vetos: Optional[list] = None,
    humedad_path: str = "",
    lomas: Optional[QgsVectorLayer] = None,
    clip_geom: Optional[QgsGeometry] = None,
    feedback=None,
) -> CostResult:
    """Construye la superficie de costo."""
    result = CostResult()

    if dem_layer is None or not dem_layer.isValid():
        result.errores.append("El DEM no es una capa raster valida.")
        return result
    if dem_layer.crs().authid() != crs.authid():
        result.errores.append(
            f"El DEM esta en {dem_layer.crs().authid()} y el sistema de "
            f"trabajo es {crs.authid()}. Reproyecte antes de ejecutar."
        )
        return result

    # --- pendiente en porcentaje
    try:
        pend = processing.run(
            "gdal:slope",
            {
                "INPUT": dem_layer, "BAND": 1, "SCALE": 1.0,
                "AS_PERCENT": True, "COMPUTE_EDGES": True,
                "ZEVENBERGEN": False, "OPTIONS": "", "EXTRA": "",
                "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
            },
            feedback=feedback,
        )
        result.pendiente_path = pend["OUTPUT"]
    except Exception as exc:  # noqa: BLE001
        result.errores.append(f"No se pudo calcular la pendiente: {exc}")
        return result

    pendiente, nodata_p, gt, proj = _leer(result.pendiente_path)
    if pendiente is None:
        result.errores.append("No se pudo leer el raster de pendiente.")
        return result

    valida = np.isfinite(pendiente)
    if nodata_p is not None:
        valida &= pendiente != nodata_p
    pendiente = np.where(valida, pendiente, 0.0)

    result.celdas = int(valida.sum())
    result.pendiente_media = float(pendiente[valida].mean()) if valida.any() else 0.0
    result.pendiente_max_hallada = float(pendiente[valida].max()) if valida.any() else 0.0

    # --- termino de pendiente
    limite = max(0.1, config.pendiente_max)
    ratio = pendiente / limite
    costo = COSTO_BASE + config.peso_pendiente * np.power(
        np.clip(ratio, 0.0, None), config.exponente_pendiente
    )
    sobre = pendiente > limite
    costo = np.where(
        sobre,
        costo * config.penalizacion_sobre_limite,
        costo,
    )
    result.pct_sobre_limite = float(
        100.0 * sobre[valida].sum() / max(1, valida.sum())
    )

    # --- humedad de M2
    if humedad_path:
        humedad, nd_h, _, _ = _leer(humedad_path)
        if humedad is not None and humedad.shape == pendiente.shape:
            humedad = np.nan_to_num(humedad, nan=0.0)
            if nd_h is not None:
                humedad = np.where(humedad == nd_h, 0.0, humedad)
            costo = costo * (1.0 + config.peso_humedad * np.clip(humedad, 0.0, 1.0))
        else:
            result.advertencias.append(
                "El raster de humedad no coincide en dimensiones con el DEM y "
                "no se aplico. Verifique que M2 corriera sobre el mismo DEM."
            )

    # --- restricciones con decaimiento
    if exclusiones:
        capa_excl = _lista_a_capa(exclusiones, crs)
        quemado = _rasterizar(capa_excl, dem_layer, 1.0, feedback=feedback)
        if quemado:
            # La mascara de "dentro" se toma del raster quemado y NO de la
            # distancia. gdal:proximity con MAX_DISTANCE acotado no devuelve
            # esa distancia a las celdas lejanas: les asigna cero o el nodata.
            # Deducir "dentro" de dist <= 0 vetaba entonces todo el territorio
            # mas alla de la banda, dejando transitables solo los corredores
            # pegados a las fajas.
            adentro_raster, nd_a, _, _ = _leer(quemado)
            dentro = np.zeros(pendiente.shape, dtype=bool)
            if adentro_raster is not None and adentro_raster.shape == pendiente.shape:
                adentro_raster = np.nan_to_num(adentro_raster, nan=0.0)
                dentro = adentro_raster > 0.5

            # La proximidad se pide SIN limite de distancia, y el recorte a la
            # banda se hace despues en numpy, donde el comportamiento es
            # explicito.
            prox = processing.run(
                "gdal:proximity",
                {
                    "INPUT": quemado, "BAND": 1, "VALUES": "1", "UNITS": 0,
                    "MAX_DISTANCE": 0, "REPLACE": 0,
                    "NODATA": -1, "DATA_TYPE": 5,
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                feedback=feedback,
            )
            dist, nd_d, _, _ = _leer(prox["OUTPUT"])
            if dist is not None and dist.shape == pendiente.shape:
                dist = np.nan_to_num(dist, nan=-1.0)
                # Cualquier valor negativo o nodata significa "sin dato de
                # distancia", que se trata como lejano y no como interior.
                lejano = dist < 0
                if nd_d is not None:
                    lejano |= dist == nd_d
                dist = np.where(
                    lejano, config.banda_restriccion_m * 10.0, dist
                )
                decaido = np.clip(
                    1.0 - dist / max(1.0, config.banda_restriccion_m), 0.0, 1.0
                )
                decaido = np.where(dentro, 0.0, decaido)
                costo = costo * (1.0 + config.peso_restriccion * 4.0 * decaido)

            # Impedimento, NO veto: la faja marginal encarece muchisimo el
            # recorrido paralelo pero deja pasar el cruce perpendicular. Con
            # veto absoluto, el propio drenaje fragmenta el area y ningun
            # destino al otro lado de una quebrada queda conectable.
            costo = np.where(dentro, costo * config.factor_impedimento, costo)
            result.celdas_impedidas = int(dentro[valida].sum())

            # Un impedimento que cubre casi todo el territorio es un error de datos,
            # no un resultado: sin esta comprobacion el trazado produce haces
            # de vias paralelas encajonadas en los pocos corredores libres.
            pct_veto = 100.0 * result.celdas_impedidas / max(1, int(valida.sum()))
            if pct_veto > 60.0:
                result.errores.append(
                    f"Las exclusiones cubren el {pct_veto:.0f}% del area. "
                    "Revise las capas de restriccion: con tan poco territorio "
                    "libre el trazado no puede producir una red razonable."
                )
                return result
            if pct_veto > 25.0:
                result.advertencias.append(
                    f"Las exclusiones cubren el {pct_veto:.0f}% del area. "
                    "Verifique que corresponda a las fajas previstas."
                )

    # --- salida del area de trabajo
    # Salir de la PCA se PENALIZA, no se prohibe. Cuando un aguajal o una
    # quebrada encajonada bloquean el paso interno, rodear por fuera puede ser
    # mas barato y de menor impacto que forzar el trazo adentro; la fisiografia
    # manda. Pero tampoco es gratis: fuera del area el titular puede no tener
    # derecho de paso y la via no computa en el impacto del plan, de modo que
    # solo debe usarse cuando de verdad convenga.
    if clip_geom is not None and not clip_geom.isEmpty():
        capa_area = _lista_a_capa([clip_geom], crs)
        quemado_area = _rasterizar(capa_area, dem_layer, 1.0, feedback=feedback)
        if quemado_area:
            dentro_area, _, _, _ = _leer(quemado_area)
            if dentro_area is not None and dentro_area.shape == pendiente.shape:
                fuera = np.nan_to_num(dentro_area, nan=0.0) <= 0.5
                if config.factor_fuera_area >= COSTO_VETO:
                    costo = np.where(fuera, COSTO_VETO, costo)
                else:
                    costo = np.where(
                        fuera, costo * config.factor_fuera_area, costo
                    )
                result.celdas_fuera_area = int(fuera[valida].sum())

    # --- vetos absolutos
    # Solo aqui el costo es infinito: areas naturales protegidas, monumentos
    # arqueologicos, servidumbres. No se puede pasar bajo ninguna circunstancia
    # y un trazo que las atraviesa no es un resultado, es un pasivo.
    if vetos:
        capa_veto = _lista_a_capa(vetos, crs)
        quemado_veto = _rasterizar(capa_veto, dem_layer, 1.0, feedback=feedback)
        if quemado_veto:
            mascara, _, _, _ = _leer(quemado_veto)
            if mascara is not None and mascara.shape == pendiente.shape:
                mascara = np.nan_to_num(mascara, nan=0.0) > 0.5
                costo = np.where(mascara, COSTO_VETO, costo)
                result.celdas_vetadas = int(mascara[valida].sum())
                pct = 100.0 * result.celdas_vetadas / max(1, int(valida.sum()))
                if pct > 40.0:
                    result.errores.append(
                        f"Los vetos absolutos cubren el {pct:.0f}% del area. "
                        "Con tan poco territorio disponible no hay red posible."
                    )
                    return result

    # --- preferencia por divisorias
    # Si no se entrega una capa de divisorias pero el peso es mayor que cero,
    # se extraen del propio DEM: es el metodo de trazado en llanura, y pedirle
    # al usuario que las prepare aparte seria trabajo evitable.
    if lomas is None and config.peso_lomas > 0:
        from ...core import divisorias as div_mod

        div = div_mod.run(
            dem_layer, crs,
            umbral_ha=config.umbral_lomas_ha,
            desnivel_max_m=config.desnivel_max_lomas_m,
            clip_geom=clip_geom,
            feedback=feedback,
        )
        result.desnivel_m = div.desnivel_m
        result.advertencias.extend(div.advertencias)
        if div.ok and div.capa is not None:
            lomas = div.capa
            result.lomas_km = div.longitud_km
            result.metodo_lomas_aplicado = div.aplicable
            if not div.aplicable:
                # El relieve no corresponde al metodo: se conserva la capa como
                # referencia pero no se aplica el descuento, porque en montana
                # la divisoria pasa por las cumbres.
                lomas = None
        else:
            result.advertencias.extend(div.errores)

    if lomas is not None and config.peso_lomas > 0:
        quemado = _rasterizar(lomas, dem_layer, 1.0, feedback=feedback)
        if quemado:
            prox = processing.run(
                "gdal:proximity",
                {
                    "INPUT": quemado, "BAND": 1, "VALUES": "1", "UNITS": 0,
                    "MAX_DISTANCE": 0, "REPLACE": 0,
                    "NODATA": -1, "DATA_TYPE": 5,
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
                feedback=feedback,
            )
            dist, _, _, _ = _leer(prox["OUTPUT"])
            if dist is not None and dist.shape == pendiente.shape:
                dist = np.nan_to_num(dist, nan=-1.0)
                dist = np.where(dist < 0, config.banda_lomas_m * 10.0, dist)
                cerca = np.clip(
                    1.0 - dist / max(1.0, config.banda_lomas_m), 0.0, 1.0
                )
                # Si el descuento alcanza a casi todo el territorio, la red de
                # divisorias es demasiado densa para la banda elegida y el
                # efecto se anula: la superficie queda plana y no guia nada.
                cobertura = float((cerca > 0.5)[valida].mean()) if valida.any() else 0.0
                if cobertura > 0.6:
                    result.advertencias.append(
                        f"El descuento por divisorias alcanza al "
                        f"{100 * cobertura:.0f}% del area, de modo que no "
                        "diferencia el terreno. Suba el umbral de divisoria "
                        "para quedarse con las principales, o reduzca la banda "
                        "de atraccion."
                    )
                else:
                    # Descuento, no penalizacion: acercarse a la divisoria
                    # abarata. Se aplica solo si discrimina.
                    costo = costo / (1.0 + config.peso_lomas * cerca)
                    result.metodo_lomas_aplicado = True

    # --- cruces de cauce
    if red_hidrica is not None and red_hidrica.featureCount():
        campo = "strahler" if red_hidrica.fields().indexOf("strahler") >= 0 else None
        quemado = _rasterizar(
            red_hidrica, dem_layer, 1.0, campo=campo, feedback=feedback
        )
        if quemado:
            orden, nd_o, _, _ = _leer(quemado)
            if orden is not None and orden.shape == pendiente.shape:
                orden = np.nan_to_num(orden, nan=0.0)
                extra = np.zeros_like(costo)
                valores = np.unique(orden[orden > 0]).astype(int)
                for valor in valores:
                    extra = np.where(
                        orden == valor,
                        config.peso_cruce * config.costo_cruce(int(valor)),
                        extra,
                    )
                costo = costo + extra

    costo = np.where(valida, costo, COSTO_VETO)
    costo = np.clip(costo, COSTO_BASE * 0.01, COSTO_VETO)

    # Una superficie sin variacion no guia el trazado: la ruta de menor costo
    # entre dos puntos seria practicamente cualquiera. Se mide antes de
    # escribir para poder advertirlo.
    if valida.any():
        finitos = costo[valida & (costo < COSTO_VETO)]
        if finitos.size:
            rango = float(finitos.max() - finitos.min())
            relativo = rango / max(1e-6, float(finitos.mean()))
            result.variacion_relativa = relativo
            if relativo < 0.15:
                result.advertencias.append(
                    f"La superficie de costo apenas varia (rango relativo "
                    f"{relativo:.2f}). Sin contraste, el trazado no tiene "
                    "criterio para preferir una ruta sobre otra: revise los "
                    "pesos y el umbral de divisoria."
                )

    result.costo_min = float(costo[valida].min()) if valida.any() else 0.0
    result.costo_max = float(costo[valida].max()) if valida.any() else 0.0
    result.costo_medio = float(costo[valida].mean()) if valida.any() else 0.0

    result.costo_path = _escribir(_tmp(), costo, gt, proj)
    capa = QgsRasterLayer(result.costo_path, "Superficie de costo")
    result.capa = capa if capa.isValid() else None

    if result.pct_sobre_limite > 40.0:
        result.advertencias.append(
            f"El {result.pct_sobre_limite:.0f}% de la superficie supera el "
            f"limite de pendiente de {config.pendiente_max:.0f}%. El trazo va "
            "a rodear mucho: revise si el limite corresponde al tipo de via."
        )
    result.advertencias.append(
        "Los pesos de la superficie de costo son un juicio de ingenieria y no "
        "se deducen de los datos. Quedan registrados para sustentar la "
        "decision, no como un optimo objetivo."
    )

    result.ok = True
    return result


def _lista_a_capa(geoms: list, crs) -> Optional[QgsVectorLayer]:
    """Capa en memoria a partir de una lista de geometrias."""
    from qgis.core import QgsFeature

    capa = QgsVectorLayer(f"Polygon?crs={crs.authid()}", "excl", "memory")
    if not capa.isValid():
        return None
    feats = []
    for geom in geoms:
        if geom is None or geom.isEmpty():
            continue
        feat = QgsFeature()
        feat.setGeometry(geom)
        feats.append(feat)
    capa.dataProvider().addFeatures(feats)
    capa.updateExtents()
    return capa
