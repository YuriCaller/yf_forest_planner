"""Lectura, validacion y normalizacion del censo forestal.

Modulo independiente de la interfaz: no importa PyQt ni conoce el dialogo.
Solo depende de la API de QGIS, sin numpy ni scipy, para que pueda correr
en cualquier instalacion OSGeo4W sin resolver dependencias externas.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from qgis.core import (
    QgsFeatureRequest,
    QgsVectorLayer,
    QgsWkbTypes,
)

# --------------------------------------------------------------------------
# Autodeteccion de campos
# --------------------------------------------------------------------------

# El orden dentro de cada lista importa: se devuelve la primera coincidencia.
# Los alias de una sola letra o de dos van al final para que no capturen antes
# que un nombre explicito.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": (
        "id_arbol", "n_arbol", "narbol", "cod_arbol", "codigo", "n", "no",
        "nro", "num", "id",
    ),
    "especie": (
        "especie", "nom_cient", "nombre_cientifico", "nom_comun",
        "cod_especi", "cod_especie", "sp",
    ),
    "categoria": (
        "tipo", "categoria", "cat", "condicion", "clase", "aprovecham",
    ),
    "dap": (
        "dap", "dap_m", "dap_cm", "d_a_p", "diametro", "diam", "df",
        "cap", "cap_cm",
    ),
    "altura": (
        "hc", "h_com", "hcom", "altura_comercial", "alt_com", "alt_comerc",
        "altura", "h",
    ),
    "volumen": (
        "vol3", "volumen", "vol", "vol_m3", "volumen_m3", "vol_com",
        "vol_total",
    ),
    "parcela": (
        "bq", "bloque", "faja", "parcela", "pca", "sector", "est_", "est",
    ),
}

# Campos que aparecen en censos peruanos y que conviene NO ofrecer como si
# fueran otra cosa. DF suele ser el mismo DAP expresado en centimetros, AB es
# area basal derivada del DAP, y HT es altura total y no comercial.
DERIVED_FIELD_HINTS = ("df", "ab", "area_basal", "ht", "h_total", "altura_total")

# Modos de interpretacion del campo de diametro.
DIAM_DAP_CM = "DAP_CM"
DIAM_DAP_M = "DAP_M"
DIAM_CAP_CM = "CAP_CM"

DIAM_MODES = (DIAM_DAP_CM, DIAM_DAP_M, DIAM_CAP_CM)

# Factor de forma por defecto de uso corriente en Amazonia peruana.
# Expuesto en la interfaz: no debe tratarse como constante normativa.
DEFAULT_FORM_FACTOR = 0.65

# Condicion de cada individuo en la capa de censo evaluado. Se simbolizan con
# marcadores distintos para que el mapa muestre sin ambiguedad que arbol puede
# talarse y cual no.
CONDICION_APROVECHABLE = "APROVECHABLE"
CONDICION_NO_APROVECHABLE = "NO APROVECHABLE"
CONDICION_SEMILLERO = "SEMILLERO"

# Limites de cordura para marcar registros sospechosos, no para descartarlos.
DAP_MIN_M = 0.05
DAP_MAX_M = 3.50
HC_MIN_M = 1.0
HC_MAX_M = 45.0


def build_evaluated_layer(trees: list, crs) -> "QgsVectorLayer":
    """Capa de censo evaluado, con la condicion de cada individuo.

    Se genera una capa nueva en lugar de modificar el censo original: el censo
    es el dato de campo y no debe alterarse. El campo condicion permite
    simbolizar aprovechables, no aprovechables y semilleros con simbolos
    distintos, de modo que en el mapa se lea de un vistazo que arbol puede
    talarse y cual no.
    """
    from qgis.core import (
        QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY,
        QgsVectorLayer,
    )
    from qgis.PyQt.QtCore import QMetaType

    capa = QgsVectorLayer(
        f"Point?crs={crs.authid()}", "Censo evaluado", "memory"
    )
    if not capa.isValid():
        return None

    campos = QgsFields()
    for nombre, tipo in (
        ("codigo", QMetaType.Type.QString),
        ("especie", QMetaType.Type.QString),
        ("condicion", QMetaType.Type.QString),
        ("motivo", QMetaType.Type.QString),
        ("dap_cm", QMetaType.Type.Double),
        ("dmc_cm", QMetaType.Type.Double),
        ("altura_m", QMetaType.Type.Double),
        ("volumen_m3", QMetaType.Type.Double),
        ("patio_id", QMetaType.Type.Int),
        ("arrastre_m", QMetaType.Type.Double),
    ):
        campos.append(QgsField(nombre, tipo))
    capa.dataProvider().addAttributes(campos.toList())
    capa.updateFields()

    feats = []
    for tree in trees:
        categoria = str(getattr(tree, "categoria", "") or "").upper()
        es_semillero = categoria.startswith(("SEM", "SEMI"))
        bajo = bool(getattr(tree, "bajo_dmc", False))
        aprovechable = bool(getattr(tree, "aprovechable", True))

        if es_semillero:
            condicion, motivo = CONDICION_SEMILLERO, "semillero: no se tala"
        elif bajo:
            dmc = float(getattr(tree, "dmc_aplicado", 0.0) or 0.0)
            condicion = CONDICION_NO_APROVECHABLE
            motivo = f"bajo el DMC de {dmc:.0f} cm"
        elif not aprovechable:
            condicion = CONDICION_NO_APROVECHABLE
            motivo = "excluido del aprovechamiento"
        else:
            condicion, motivo = CONDICION_APROVECHABLE, ""

        feat = QgsFeature(capa.fields())
        feat.setGeometry(
            QgsGeometry.fromPointXY(QgsPointXY(float(tree.x), float(tree.y)))
        )
        feat.setAttributes([
            str(getattr(tree, "codigo", "") or ""),
            getattr(tree, "especie", "") or "",
            condicion,
            motivo,
            round(float(getattr(tree, "dap_m", 0.0) or 0.0) * 100.0, 1),
            round(float(getattr(tree, "dmc_aplicado", 0.0) or 0.0), 1),
            round(float(getattr(tree, "altura_m", 0.0) or 0.0), 1),
            round(float(getattr(tree, "volumen_m3", 0.0) or 0.0), 3),
            int(getattr(tree, "patio_id", 0) or 0),
            round(float(getattr(tree, "dist_arrastre", 0.0) or 0.0), 1),
        ])
        feats.append(feat)

    capa.dataProvider().addFeatures(feats)
    capa.updateExtents()
    return capa


def normalize_species(name: str) -> str:
    """Normaliza un nombre de especie para agrupar y para buscar su DMC.

    Los censos traen la misma especie escrita de varias formas: mayuscula
    inicial o no, con y sin tilde, con espacios de mas. Sin normalizar, una
    misma especie aparece como dos en los conteos y genera dos capas en
    cualquier salida por especie.

    Devuelve la forma normalizada para COMPARAR, no para mostrar: el nombre
    que se presenta al usuario debe ser el que trae el censo.
    """
    if not name:
        return ""
    out = str(name).strip().lower()
    for src, dst in (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"),
    ):
        out = out.replace(src, dst)
    out = " ".join(out.split())
    return out


def normalize(name: str) -> str:
    """Normaliza un nombre de campo para comparacion tolerante."""
    out = name.strip().lower()
    for src, dst in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        out = out.replace(src, dst)
    # El indicador ordinal sobrevive a la normalizacion y rompe la deteccion:
    # el campo "N" de los censos peruanos suele venir como "Nº" en el DBF.
    for ch in ("º", "°", "ª", "#"):
        out = out.replace(ch, "")
    for ch in (" ", "-", ".", "/"):
        out = out.replace(ch, "_")
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def autodetect_fields(layer: QgsVectorLayer) -> dict[str, Optional[str]]:
    """Propone un mapeo de campos a partir de los nombres de la capa.

    Devuelve el nombre real del campo (no normalizado) o None por cada rol.
    El resultado es una propuesta: la interfaz debe permitir corregirla.
    """
    if layer is None:
        return {role: None for role in FIELD_ALIASES}

    lookup: dict[str, str] = {}
    for fld in layer.fields():
        lookup.setdefault(normalize(fld.name()), fld.name())

    result: dict[str, Optional[str]] = {}
    for role, aliases in FIELD_ALIASES.items():
        match = None
        for alias in aliases:
            if alias in lookup:
                match = lookup[alias]
                break
        result[role] = match
    return result


def suggest_diameter_mode(
    field_name: Optional[str],
    layer: Optional[QgsVectorLayer] = None,
) -> str:
    """Infiere el modo de diametro por nombre y, si se puede, por magnitud.

    El nombre solo no alcanza: un campo llamado DAP puede venir en metros o en
    centimetros segun quien armo la planilla. Cuando hay capa se muestrea el
    rango real, que es inequivoco: un arbol censable no mide 0.75 cm ni 75 m.
    """
    norm = normalize(field_name) if field_name else ""

    if norm.startswith("cap"):
        return DIAM_CAP_CM

    if layer is not None and field_name:
        index = layer.fields().indexOf(field_name)
        if index >= 0:
            sample = []
            for count, feat in enumerate(layer.getFeatures()):
                value = _as_float(feat[index])
                if value is not None and value > 0:
                    sample.append(value)
                if count >= 500:
                    break
            if sample:
                median = sorted(sample)[len(sample) // 2]
                if median < 10.0:
                    return DIAM_DAP_M
                if median > 150.0:
                    return DIAM_CAP_CM
                return DIAM_DAP_CM

    if norm.endswith("_cm"):
        return DIAM_DAP_CM
    # En censos amazonicos peruanos el DAP se registra habitualmente en metros.
    return DIAM_DAP_M


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------


@dataclass
class CensoConfig:
    """Mapeo de campos y parametros de calculo definidos por el usuario."""

    field_id: Optional[str] = None
    field_especie: Optional[str] = None
    field_categoria: Optional[str] = None
    field_dap: Optional[str] = None
    field_altura: Optional[str] = None
    field_volumen: Optional[str] = None
    field_parcela: Optional[str] = None

    diameter_mode: str = DIAM_DAP_CM
    form_factor: float = DEFAULT_FORM_FACTOR

    # Valores del campo categoria que se consideran aprovechables.
    # Vacio = todos los individuos aportan volumen al patio.
    harvestable_values: tuple[str, ...] = ()

    # Si es True se recalcula el volumen aunque exista campo de volumen.
    force_recompute: bool = False

    # Diametro minimo de corta por especie, en centimetros, con la clave
    # normalizada por normalize_species(). Un individuo por debajo de su DMC
    # NO puede talarse aunque el censo lo haya marcado como aprovechable, asi
    # que no debe sumar volumen al patio ni al POA.
    #
    # La tabla la carga el usuario: el DMC lo fija la autoridad forestal por
    # especie y cambia con la norma vigente. Vacia desactiva la verificacion.
    dmc_por_especie: dict = field(default_factory=dict)

    # Especies excluidas por baja abundancia. Se calculan en el modulo de
    # intensidad y se reinyectan aqui, porque la decision de que una especie no
    # es aprovechable debe reflejarse en el volumen del POA y no solo en una
    # advertencia del informe.
    especies_excluidas: tuple = ()

    # DMC aplicado a las especies que no figuran en la tabla. Cero significa
    # no verificar esas especies, que es preferible a inventarles un umbral.
    dmc_por_defecto: float = 0.0

    def esta_excluida(self, especie: str) -> bool:
        """True si la especie fue excluida de la canasta de aprovechamiento.

        Se usa para las especies de baja abundancia: la decision debe
        reflejarse en el volumen del POA y no solo en una advertencia.
        """
        if not self.especies_excluidas:
            return False
        clave = normalize_species(especie)
        return any(
            clave == normalize_species(e) for e in self.especies_excluidas
        )

    def dmc_for(self, especie: str) -> float:
        """DMC en centimetros para una especie, o cero si no aplica."""
        if not self.dmc_por_especie and self.dmc_por_defecto <= 0:
            return 0.0
        clave = normalize_species(especie)
        if clave in self.dmc_por_especie:
            return float(self.dmc_por_especie[clave])
        return float(self.dmc_por_defecto)

    def verifica_dmc(self) -> bool:
        return bool(self.dmc_por_especie) or self.dmc_por_defecto > 0

    def volume_is_computed(self) -> bool:
        return self.force_recompute or not self.field_volumen

    def missing_requirements(self) -> list[str]:
        """Campos obligatorios que faltan para poder ejecutar."""
        missing: list[str] = []
        if self.volume_is_computed():
            if not self.field_dap:
                missing.append("campo de diametro (DAP o CAP)")
            if not self.field_altura:
                missing.append("campo de altura comercial")
        if self.diameter_mode not in DIAM_MODES:
            missing.append("modo de diametro valido")
        if not 0.1 <= self.form_factor <= 1.0:
            missing.append("factor de forma entre 0.1 y 1.0")
        return missing


# --------------------------------------------------------------------------
# Conversion y volumen
# --------------------------------------------------------------------------


def to_diameter_m(raw: float, mode: str) -> float:
    """Convierte el valor crudo del campo de diametro a metros.

    CAP_CM interpreta el valor como circunferencia a la altura del pecho.
    """
    if mode == DIAM_DAP_M:
        return raw
    if mode == DIAM_CAP_CM:
        return (raw / math.pi) / 100.0
    return raw / 100.0


def compute_volume(diameter_m: float, height_m: float, form_factor: float) -> float:
    """Volumen comercial por el metodo del cilindro corregido.

    V = (pi / 4) * D^2 * Hc * ff
    """
    return (math.pi / 4.0) * (diameter_m ** 2) * height_m * form_factor


# --------------------------------------------------------------------------
# Registros y validacion
# --------------------------------------------------------------------------


@dataclass
class Tree:
    """Un individuo del censo, ya normalizado a unidades SI."""

    fid: int
    codigo: str
    x: float
    y: float
    especie: str = ""
    categoria: str = ""
    parcela: str = ""
    dap_m: float = 0.0
    altura_m: float = 0.0
    volumen_m3: float = 0.0
    aprovechable: bool = True
    volumen_calculado: bool = False
    # Individuo por debajo del diametro minimo de corta de su especie. No es
    # aprovechable aunque el censo lo haya marcado como tal.
    bajo_dmc: bool = False
    dmc_aplicado: float = 0.0


@dataclass
class ValidationReport:
    """Resumen de calidad del censo, para el panel en vivo y el informe."""

    total: int = 0
    validos: int = 0
    sin_geometria: int = 0
    dap_invalido: int = 0
    altura_invalida: int = 0
    volumen_cero: int = 0
    dap_fuera_rango: int = 0
    altura_fuera_rango: int = 0
    aprovechables: int = 0
    volumen_total: float = 0.0
    volumen_aprovechable: float = 0.0
    dap_min: Optional[float] = None
    dap_max: Optional[float] = None
    especies: int = 0
    volumen_calculado: bool = False
    ff_incoherentes: int = 0
    ff_moda: Optional[float] = None
    campos_texto: list[str] = field(default_factory=list)
    n_bajo_dmc: int = 0
    n_excluidos_especie: int = 0
    volumen_excluido_especie: float = 0.0
    volumen_bajo_dmc: float = 0.0
    bajo_dmc_por_especie: dict = field(default_factory=dict)
    especies_sin_dmc: list = field(default_factory=list)
    especies_duplicadas: list = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    def is_blocking(self) -> bool:
        """True si el censo no permite continuar con el analisis."""
        return self.validos == 0 or self.volumen_aprovechable <= 0.0

    def summary_lines(self) -> list[str]:
        """Texto compacto para el panel de validacion del dialogo."""
        origen = "calculado" if self.volumen_calculado else "campo del censo"
        lines = [
            f"Individuos leidos: {self.total}",
            f"Registros validos: {self.validos}",
            f"Aprovechables: {self.aprovechables}",
            f"Especies distintas: {self.especies}",
            f"Volumen total: {self.volumen_total:,.3f} m3 ({origen})",
            f"Volumen aprovechable: {self.volumen_aprovechable:,.3f} m3",
        ]
        if self.n_bajo_dmc:
            lines.append(
                f"Excluidos por DMC: {self.n_bajo_dmc} ind, "
                f"{self.volumen_bajo_dmc:,.3f} m3"
            )
        if self.n_excluidos_especie:
            lines.append(
                f"Excluidos por especie: {self.n_excluidos_especie} ind, "
                f"{self.volumen_excluido_especie:,.3f} m3"
            )
        if self.dap_min is not None and self.dap_max is not None:
            lines.append(
                f"DAP: {self.dap_min * 100:.1f} - {self.dap_max * 100:.1f} cm"
            )
        return lines


def _as_float(value) -> Optional[float]:
    """Convierte a float tolerando NULL, texto y coma decimal."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        val = float(value)
        return None if math.isnan(val) else val
    text = str(value).strip().replace(",", ".")
    if not text or text.upper() in ("NULL", "NONE", "NA", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.upper() in ("NULL", "NONE") else text


def load_trees(
    layer: QgsVectorLayer,
    config: CensoConfig,
    only_selected: bool = False,
) -> tuple[list[Tree], ValidationReport]:
    """Lee la capa de censo y devuelve individuos normalizados mas diagnostico.

    No reproyecta: asume que la capa ya esta en el SRC de trabajo. La
    reproyeccion se resuelve aguas arriba en core.srs para que el hash de
    trazabilidad se calcule sobre las coordenadas realmente usadas.
    """
    report = ValidationReport()
    trees: list[Tree] = []

    if layer is None or not layer.isValid():
        report.advertencias.append("La capa de censo no es valida.")
        return trees, report

    if layer.geometryType() != QgsWkbTypes.GeometryType.PointGeometry:
        report.advertencias.append("La capa de censo debe ser de puntos.")
        return trees, report

    missing = config.missing_requirements()
    if missing:
        report.advertencias.append("Faltan parametros: " + ", ".join(missing))
        return trees, report

    report.volumen_calculado = config.volume_is_computed()

    harvest_set = {v.strip().upper() for v in config.harvestable_values if v}
    especies: set[str] = set()
    ff_muestras: list[float] = []
    grafias: dict = {}
    sin_dmc: set = set()

    # Campos de medida tipados como texto. Ocurre cuando un error de Excel
    # (#REF!, #DIV/0!) contamina la columna al exportar: el shapefile la tipa
    # como cadena y un combo filtrado por campos numericos la ocultaria.
    for role, name in (
        ("volumen", config.field_volumen),
        ("dap", config.field_dap),
        ("altura", config.field_altura),
    ):
        if not name:
            continue
        idx = layer.fields().indexOf(name)
        if idx >= 0 and not layer.fields().at(idx).isNumeric():
            report.campos_texto.append(f"{name} ({role})")

    request = QgsFeatureRequest()
    if only_selected:
        features = layer.getSelectedFeatures(request)
        report.total = layer.selectedFeatureCount()
    else:
        features = layer.getFeatures(request)
        report.total = layer.featureCount()

    for feat in features:
        geom = feat.geometry()
        if geom is None or geom.isEmpty() or geom.isNull():
            report.sin_geometria += 1
            continue
        point = geom.centroid().asPoint()

        dap_m = 0.0
        altura_m = 0.0
        volumen = 0.0
        calculado = False

        if config.volume_is_computed():
            raw_dap = _as_float(feat[config.field_dap])
            raw_alt = _as_float(feat[config.field_altura])
            if raw_dap is None or raw_dap <= 0:
                report.dap_invalido += 1
                continue
            if raw_alt is None or raw_alt <= 0:
                report.altura_invalida += 1
                continue
            dap_m = to_diameter_m(raw_dap, config.diameter_mode)
            altura_m = raw_alt
            volumen = compute_volume(dap_m, altura_m, config.form_factor)
            calculado = True
        else:
            raw_vol = _as_float(feat[config.field_volumen])
            if raw_vol is None or raw_vol <= 0:
                report.volumen_cero += 1
                continue
            volumen = raw_vol
            if config.field_dap:
                raw_dap = _as_float(feat[config.field_dap])
                if raw_dap is not None and raw_dap > 0:
                    dap_m = to_diameter_m(raw_dap, config.diameter_mode)
            if config.field_altura:
                raw_alt = _as_float(feat[config.field_altura])
                if raw_alt is not None and raw_alt > 0:
                    altura_m = raw_alt
            # Coherencia: si el censo trae volumen Y dimensiones, el factor de
            # forma implicito debe ser constante. Una dispersion delata celdas
            # con la formula pisada a mano.
            if dap_m > 0 and altura_m > 0:
                cilindro = (math.pi / 4.0) * (dap_m ** 2) * altura_m
                if cilindro > 0:
                    ff_muestras.append(round(volumen / cilindro, 4))

        if dap_m and not DAP_MIN_M <= dap_m <= DAP_MAX_M:
            report.dap_fuera_rango += 1
        if altura_m and not HC_MIN_M <= altura_m <= HC_MAX_M:
            report.altura_fuera_rango += 1

        categoria = (
            _as_text(feat[config.field_categoria]) if config.field_categoria else ""
        )
        aprovechable = True
        if harvest_set:
            aprovechable = categoria.upper() in harvest_set
        aprovechable_dmc = True

        especie = _as_text(feat[config.field_especie]) if config.field_especie else ""
        if especie:
            clave = normalize_species(especie)
            especies.add(clave)
            grafias.setdefault(clave, set()).add(especie)

        # Especie excluida de la canasta, por ejemplo por baja abundancia.
        excluida = config.esta_excluida(especie) if especie else False
        if excluida:
            aprovechable_dmc = False
            report.n_excluidos_especie += 1
            report.volumen_excluido_especie += volumen

        # Verificacion de diametro minimo de corta. Va aqui y no en un modulo
        # posterior porque un individuo bajo DMC no debe talarse, y por tanto
        # no debe asignarse a un patio ni sumar volumen al POA.
        bajo_dmc = False
        dmc = 0.0
        if config.verifica_dmc() and dap_m > 0:
            dmc = config.dmc_for(especie)
            if dmc > 0:
                if dap_m * 100.0 < dmc:
                    bajo_dmc = True
                    aprovechable_dmc = False
            elif especie:
                sin_dmc.add(especie)

        if bajo_dmc:
            report.n_bajo_dmc += 1
            report.volumen_bajo_dmc += volumen
            acumulado = report.bajo_dmc_por_especie.setdefault(
                especie or "sin especie", [0, 0.0]
            )
            acumulado[0] += 1
            acumulado[1] += volumen

        codigo = (
            _as_text(feat[config.field_id]) if config.field_id else ""
        ) or str(feat.id())

        trees.append(
            Tree(
                fid=feat.id(),
                codigo=codigo,
                x=point.x(),
                y=point.y(),
                especie=especie,
                categoria=categoria,
                parcela=(
                    _as_text(feat[config.field_parcela])
                    if config.field_parcela
                    else ""
                ),
                dap_m=dap_m,
                altura_m=altura_m,
                volumen_m3=volumen,
                aprovechable=aprovechable and aprovechable_dmc,
                volumen_calculado=calculado,
                bajo_dmc=bajo_dmc,
                dmc_aplicado=dmc,
            )
        )

        report.validos += 1
        report.volumen_total += volumen
        if aprovechable and aprovechable_dmc:
            report.aprovechables += 1
            report.volumen_aprovechable += volumen

        if dap_m:
            if report.dap_min is None or dap_m < report.dap_min:
                report.dap_min = dap_m
            if report.dap_max is None or dap_m > report.dap_max:
                report.dap_max = dap_m

    report.especies = len(especies)
    report.especies_sin_dmc = sorted(sin_dmc)
    # Misma especie escrita de varias formas. Sin normalizar produciria dos
    # entradas en cualquier salida por especie.
    report.especies_duplicadas = sorted(
        " / ".join(sorted(v)) for v in grafias.values() if len(v) > 1
    )
    if ff_muestras:
        conteo: dict[float, int] = {}
        for value in ff_muestras:
            conteo[value] = conteo.get(value, 0) + 1
        moda = max(conteo, key=lambda k: conteo[k])
        if conteo[moda] >= max(3, int(0.5 * len(ff_muestras))):
            report.ff_moda = moda
            report.ff_incoherentes = len(ff_muestras) - conteo[moda]

    _append_warnings(report, config)
    return trees, report


def _append_warnings(report: ValidationReport, config: CensoConfig) -> None:
    """Agrega advertencias interpretadas al reporte de validacion."""
    if report.sin_geometria:
        report.advertencias.append(
            f"{report.sin_geometria} registros sin geometria fueron omitidos."
        )
    if report.dap_invalido:
        report.advertencias.append(
            f"{report.dap_invalido} registros con diametro nulo o cero."
        )
    if report.altura_invalida:
        report.advertencias.append(
            f"{report.altura_invalida} registros con altura comercial nula o cero."
        )
    if report.volumen_cero:
        report.advertencias.append(
            f"{report.volumen_cero} registros con volumen nulo o cero."
        )
    if report.dap_fuera_rango:
        report.advertencias.append(
            f"{report.dap_fuera_rango} individuos con DAP fuera del rango "
            f"{DAP_MIN_M * 100:.0f}-{DAP_MAX_M * 100:.0f} cm. "
            "Revise el modo de diametro seleccionado."
        )
    if report.altura_fuera_rango:
        report.advertencias.append(
            f"{report.altura_fuera_rango} individuos con altura comercial fuera "
            f"del rango {HC_MIN_M:.0f}-{HC_MAX_M:.0f} m."
        )
    if report.validos and report.volumen_total / report.validos > 40.0:
        report.advertencias.append(
            "El volumen promedio por individuo supera 40 m3. Es muy probable "
            "que las unidades del campo de diametro esten mal declaradas."
        )
    if report.validos and report.volumen_total / report.validos < 0.05:
        report.advertencias.append(
            "El volumen promedio por individuo es menor a 0.05 m3. Verifique "
            "las unidades del campo de diametro."
        )
    if report.campos_texto:
        report.advertencias.append(
            "Campos de medida tipados como texto: "
            + ", ".join(report.campos_texto)
            + ". Suele deberse a un error de formula (#REF!, #DIV/0!) que "
            "contamino la columna al exportar desde hoja de calculo. Se leen "
            "igual, pero revise los registros afectados."
        )
    if report.ff_moda is not None and report.ff_incoherentes:
        report.advertencias.append(
            f"{report.ff_incoherentes} individuos no siguen el factor de forma "
            f"dominante del censo ({report.ff_moda:.2f}). El volumen declarado "
            "no se deduce de su DAP y altura: probablemente son celdas con la "
            "formula sobrescrita."
        )
    if report.especies_duplicadas:
        report.advertencias.append(
            "Misma especie escrita de varias formas: "
            + "; ".join(report.especies_duplicadas[:5])
            + ". Se agruparon para el conteo, pero conviene uniformar el "
            "censo antes de generar salidas por especie."
        )
    if report.n_bajo_dmc:
        detalle = ", ".join(
            f"{sp} ({n} ind, {v:,.2f} m3)"
            for sp, (n, v) in sorted(
                report.bajo_dmc_por_especie.items(),
                key=lambda kv: -kv[1][0],
            )[:5]
        )
        report.advertencias.append(
            f"{report.n_bajo_dmc} individuos ({report.volumen_bajo_dmc:,.2f} m3) "
            f"estan por debajo del diametro minimo de corta: {detalle}. "
            "Fueron excluidos del volumen aprovechable: no pueden talarse."
        )
    if report.n_excluidos_especie:
        report.advertencias.append(
            f"{report.n_excluidos_especie} individuos "
            f"({report.volumen_excluido_especie:,.2f} m3) pertenecen a especies "
            "excluidas de la canasta de aprovechamiento y no suman volumen."
        )
    if config.verifica_dmc() and report.especies_sin_dmc:
        report.advertencias.append(
            f"{len(report.especies_sin_dmc)} especies no figuran en la tabla "
            "de DMC y no se verificaron: "
            + ", ".join(report.especies_sin_dmc[:8])
            + ("..." if len(report.especies_sin_dmc) > 8 else "")
        )
    if not config.verifica_dmc():
        report.advertencias.append(
            "No se declaro tabla de diametros minimos de corta. No se verifico "
            "si algun individuo esta por debajo del DMC de su especie."
        )
    if config.volume_is_computed():
        report.advertencias.append(
            f"Volumen calculado con factor de forma {config.form_factor:.2f}. "
            "Verifique que corresponda al aprobado por la autoridad forestal."
        )
