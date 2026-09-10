"""M6 - Areas de impacto y balance de volumen.

No calcula nada nuevo: consolida lo que M1, M3 y M5 produjeron por separado y
lo presenta como el cuadro que exige el plan de manejo, con la desagregacion
por componente y su participacion sobre el area de trabajo.

Dos criterios que gobiernan la consolidacion:

  SIN DOBLE CONTEO   las superficies de vias, patios y campamentos se suman
                     como areas nominales, pero las trazas se solapan en las
                     intersecciones. Se declara como estimacion y se ofrece el
                     area geometrica real cuando las capas estan disponibles.

  DISTINGUIR ESTIMACION DE CONTROL   la geometria de vias y pistas es una
                     propuesta sujeta a replanteo; los conteos de individuos y
                     el volumen son verificacion contra el censo. El cuadro los
                     separa en lugar de mezclarlos bajo un mismo encabezado.

Parte de YF Forest Planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from qgis.core import QgsGeometry, QgsVectorLayer

# Superficie habilitada por campamento, en metros cuadrados. Valor de arranque
# expuesto en la interfaz: depende del tamano de la cuadrilla y del tiempo de
# permanencia.
DEFAULT_AREA_CAMPAMENTO = 2500.0


@dataclass
class ImpactConfig:
    """Parametros de la consolidacion."""

    n_campamentos: int = 1
    area_campamento_m2: float = DEFAULT_AREA_CAMPAMENTO

    # Ancho de pista de arrastre y fraccion de traza compartida entre
    # arrastres sucesivos. El reuso es el parametro que mas pesa en el area de
    # pistas y es un dato operativo, no una constante.
    ancho_pista_m: float = 4.0
    reuso_pista: float = 0.60

    # Calcular el area geometrica real disolviendo las capas. Es mas exacto
    # que sumar areas nominales pero cuesta tiempo con muchas entidades.
    calcular_area_real: bool = True


@dataclass
class Componente:
    nombre: str = ""
    cantidad: str = ""
    area_ha: float = 0.0
    tipo: str = "estimacion"
    nota: str = ""


@dataclass
class ImpactResult:
    componentes: list = field(default_factory=list)
    area_total_ha: float = 0.0
    area_real_ha: float = 0.0
    area_trabajo_ha: float = 0.0
    pct_area_trabajo: float = 0.0

    volumen_censado: float = 0.0
    volumen_excluido_faja: float = 0.0
    volumen_excluido_dmc: float = 0.0
    volumen_excluido_especie: float = 0.0
    volumen_asignado: float = 0.0
    volumen_no_asignado: float = 0.0
    volumen_atrapado: float = 0.0

    ok: bool = False
    errores: list = field(default_factory=list)
    advertencias: list = field(default_factory=list)

    def tabla_impacto(self) -> tuple:
        headers = ["Componente", "Cantidad", "Area (ha)", "% del area", "Tipo"]
        filas = []
        for comp in self.componentes:
            filas.append([
                comp.nombre,
                comp.cantidad,
                round(comp.area_ha, 4),
                round(100.0 * comp.area_ha / self.area_trabajo_ha, 3)
                if self.area_trabajo_ha else 0.0,
                comp.tipo,
            ])
        filas.append([
            "TOTAL", "", round(self.area_total_ha, 4),
            round(self.pct_area_trabajo, 3), "",
        ])
        return headers, filas

    def tabla_volumen(self) -> tuple:
        headers = ["Concepto", "Volumen (m3)", "Tipo"]
        filas = [
            ["Censado", round(self.volumen_censado, 3), "control"],
            ["Excluido por DMC", round(self.volumen_excluido_dmc, 3), "control"],
            ["Excluido por especie", round(self.volumen_excluido_especie, 3), "control"],
            ["Excluido por faja marginal", round(self.volumen_excluido_faja, 3), "control"],
            ["Asignado a patios", round(self.volumen_asignado, 3), "estimacion"],
            ["Fuera de alcance de arrastre", round(self.volumen_no_asignado, 3), "estimacion"],
            ["Sin conexion vial", round(self.volumen_atrapado, 3), "estimacion"],
        ]
        return headers, [f for f in filas if f[1]]

    def resumen_lines(self) -> list:
        lines = [
            f"Area de impacto estimada: {self.area_total_ha:.2f} ha "
            f"({self.pct_area_trabajo:.2f}% del area de trabajo)",
        ]
        if self.area_real_ha:
            lines.append(
                f"Area geometrica real (sin solapes): {self.area_real_ha:.2f} ha"
            )
        for comp in self.componentes:
            lines.append(f"  {comp.nombre}: {comp.area_ha:.3f} ha ({comp.cantidad})")
        return lines


# --------------------------------------------------------------------------
# Ejecucion
# --------------------------------------------------------------------------


def _area_geom(capa: Optional[QgsVectorLayer], ancho: float = 0.0) -> float:
    """Area en hectareas de una capa, disolviendo para no contar solapes.

    Para lineas se genera un buffer de medio ancho a cada lado antes de
    disolver: dos vias que se cruzan comparten la interseccion y sumarla dos
    veces infla el area declarada.
    """
    if capa is None or not capa.featureCount():
        return 0.0
    geoms = []
    for feat in capa.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if ancho > 0:
            geom = geom.buffer(ancho / 2.0, 8)
        geoms.append(geom)
    if not geoms:
        return 0.0
    unido = QgsGeometry.unaryUnion(geoms)
    if unido is None or unido.isEmpty():
        return 0.0
    return unido.area() / 10000.0


def run(ctx, config: ImpactConfig) -> ImpactResult:
    """Consolida el impacto a partir de los resultados acumulados."""
    from ...core.constants import M1_HIDROLOGIA, M3_PATIOS, M5_CAMINOS

    result = ImpactResult()
    result.area_trabajo_ha = float(getattr(ctx, "pca_area_ha", 0.0) or 0.0)

    m1 = ctx.result(M1_HIDROLOGIA)
    m3 = ctx.result(M3_PATIOS)
    m5 = ctx.result(M5_CAMINOS)

    if not any([m1, m3, m5]):
        result.errores.append(
            "No hay resultados que consolidar. Ejecute al menos un modulo."
        )
        return result

    geometrias = []

    # --- vias
    if m5 is not None and m5.ok:
        metricas = m5.metrics
        principal = float(metricas.get("long_principal_km", 0.0))
        secundaria = float(metricas.get("long_secundaria_km", 0.0))
        area_vias = float(metricas.get("area_vias_ha", 0.0))
        if area_vias:
            result.componentes.append(Componente(
                nombre="Vias de saca",
                cantidad=f"{principal + secundaria:.2f} km "
                         f"({principal:.2f} principal, {secundaria:.2f} secundaria)",
                area_ha=area_vias,
                tipo="estimacion",
                nota="Trazo propuesto, sujeto a replanteo en campo.",
            ))
        capa_vias = m5.layers.get("vias")
        if capa_vias is not None and config.calcular_area_real:
            ancho = float(m5.params.get("ancho_principal_m", 6.0))
            geom = _area_geom(capa_vias, ancho)
            if geom:
                geometrias.append(("vias", geom))
        obras = int(metricas.get("obras_de_cruce", 0))
        if obras:
            result.componentes.append(Componente(
                nombre="Obras de cruce",
                cantidad=f"{obras} obras",
                area_ha=0.0,
                tipo="estimacion",
                nota="Alcantarillas, pontones y puentes. Sin area asignada.",
            ))
        result.volumen_atrapado = float(metricas.get("volumen_atrapado_m3", 0.0))

    # --- patios y centros
    if m3 is not None and m3.ok:
        metricas = m3.metrics
        n_patios = int(metricas.get("n_patios", 0))
        area_patios = float(metricas.get("area_patios_ha", 0.0))
        if area_patios:
            result.componentes.append(Componente(
                nombre="Patios de acopio",
                cantidad=f"{n_patios} patios",
                area_ha=area_patios,
                tipo="estimacion",
            ))
        area_pistas = float(metricas.get("area_pistas_ha", 0.0))
        arrastre_km = float(metricas.get("arrastre_acumulado_km", 0.0))
        if area_pistas:
            result.componentes.append(Componente(
                nombre="Pistas de arrastre",
                cantidad=f"{arrastre_km:.1f} km acumulados, "
                         f"reuso {config.reuso_pista:.0%}",
                area_ha=area_pistas,
                tipo="estimacion",
                nota="Las pistas no se disenan en gabinete: se abren en campo "
                     "siguiendo el terreno. El area depende del reuso, que es "
                     "un dato operativo.",
            ))
        centros = list(getattr(ctx, "centers", []) or [])
        if centros:
            area_centros = len(centros) * float(
                getattr(ctx.yards_config, "area_centro_m2", 5000.0)
            ) / 10000.0
            result.componentes.append(Componente(
                nombre="Centros de acopio",
                cantidad=f"{len(centros)} centros",
                area_ha=area_centros,
                tipo="estimacion",
            ))
        result.volumen_asignado = float(metricas.get("volumen_asignado_m3", 0.0))
        result.volumen_no_asignado = float(metricas.get("volumen_sin_asignar_m3", 0.0))

    # --- campamentos
    if config.n_campamentos > 0:
        result.componentes.append(Componente(
            nombre="Campamentos",
            cantidad=f"{config.n_campamentos} campamentos",
            area_ha=config.n_campamentos * config.area_campamento_m2 / 10000.0,
            tipo="estimacion",
        ))

    # --- balance de volumen
    reporte = getattr(ctx, "censo_report", None)
    if reporte is not None:
        result.volumen_censado = float(getattr(reporte, "volumen_total", 0.0))
        result.volumen_excluido_dmc = float(getattr(reporte, "volumen_bajo_dmc", 0.0))
        result.volumen_excluido_especie = float(
            getattr(reporte, "volumen_excluido_especie", 0.0)
        )
    if m1 is not None and m1.ok:
        result.volumen_excluido_faja = float(
            m1.metrics.get("volumen_en_faja_m3", 0.0)
        )

    result.area_total_ha = sum(c.area_ha for c in result.componentes)
    if result.area_trabajo_ha:
        result.pct_area_trabajo = 100.0 * result.area_total_ha / result.area_trabajo_ha

    if geometrias:
        # El area real solo cubre los componentes con geometria disponible; el
        # resto se suma nominal. Se declara para no presentarla como exacta.
        nominal_con_geom = sum(
            c.area_ha for c in result.componentes if c.nombre == "Vias de saca"
        )
        result.area_real_ha = (
            result.area_total_ha - nominal_con_geom + sum(g for _, g in geometrias)
        )

    result.advertencias.append(
        "Las areas de vias, patios y pistas son estimaciones sobre trazos "
        "propuestos. Los volumenes censados y las exclusiones por DMC, especie "
        "y faja marginal son verificaciones contra el censo, no estimaciones."
    )
    if result.pct_area_trabajo > 12.0:
        result.advertencias.append(
            f"El impacto estimado alcanza el {result.pct_area_trabajo:.1f}% del "
            "area de trabajo. La literatura reporta entre 8.9 y 11.2% en "
            "aprovechamiento convencional y entre 4.6 y 4.8% con tecnicas de "
            "impacto reducido: revise los anchos y el reuso de pista."
        )
    if result.volumen_atrapado > 0:
        result.advertencias.append(
            f"{result.volumen_atrapado:,.2f} m3 quedan sin conexion vial. Ese "
            "volumen no es aprovechable con la red trazada."
        )

    result.ok = True
    return result
