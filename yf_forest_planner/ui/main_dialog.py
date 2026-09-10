"""Dialogo principal de Forest Planner.

Construido en codigo y no con archivos .ui, para que la estructura de campos
siga al modelo de datos en lugar de a un editor visual.

Estado: pestanas de entradas, censo e hidrologia operativas. Los modulos M2 a
M6 aparecen deshabilitados en lugar de ofrecerse y fallar.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import os
import time

from qgis.core import (
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsProject,
)
from qgis.gui import (
    QgsMapLayerComboBox,
    QgsMapToolEmitPoint,
    QgsProjectionSelectionWidget,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import censo as censo_mod
from ..core.i18n import tr
from ..core import estilos as estilos_mod
from ..core import estilos_raster as estilos_ras
from ..core import report as report_mod
from ..core import estadisticas as stats_mod
from ..core import srs as srs_mod
from ..core.constants import (
    DEFAULT_THRESHOLD_HA,
    M1_HIDROLOGIA,
    M3_PATIOS,
    M4_COSTO,
    M5_CAMINOS,
    M6_ARRASTRE,
    M7_INTENSIDAD,
    M8_TRANSPORTE,
    PRESETS,
    PRESET_POR_DEFECTO,
    docs_url,
    MODULE_LABELS,
    MODULE_ORDER,
    MODULES_IMPLEMENTED,
    PLUGIN_NAME,
)
from ..core.context import ModuleResult, PlanningContext
from ..modules.m1_hidrologia import hidrologia as hidro_mod
from ..modules.m3_patios import patios as patios_mod
from ..modules.m4_costo import costo as costo_mod
from ..modules.m5_caminos import caminos as caminos_mod
from ..core.router import GradeConfig
from ..modules.m6_arrastre import impacto as impacto_mod
from ..core import intensidad as intens_mod
from ..core import transporte as transp_mod


def _enum(clase, ambito: str, nombre: str):
    """Devuelve un valor de enumerado en Qt5 o en Qt6.

    Qt6 puso ambito a los enumerados: lo que era ESTIRAR pasa a ser
    QHeaderView.ResizeMode.Stretch. Resolverlo por atributo mantiene el
    complemento funcionando en las dos ramas sin duplicar codigo.
    """
    contenedor = getattr(clase, ambito, clase)
    return getattr(contenedor, nombre)


# Enumerados resueltos una sola vez al importar: repetir la llamada en cada uso
# alarga las lineas y no aporta nada.
ESTIRAR = _enum(QHeaderView, "ResizeMode", "Stretch")
SIN_EDICION = _enum(QAbstractItemView, "EditTrigger", "NoEditTriggers")
ROL_ACEPTAR = _enum(QDialogButtonBox, "ButtonRole", "AcceptRole")
ROL_RECHAZAR = _enum(QDialogButtonBox, "ButtonRole", "RejectRole")
SIN_MARCO = _enum(QScrollArea, "Shape", "NoFrame")

FILTRO_PUNTO = _enum(QgsMapLayerProxyModel, "Filter", "PointLayer")
FILTRO_POLIGONO = _enum(QgsMapLayerProxyModel, "Filter", "PolygonLayer")
FILTRO_RASTER = _enum(QgsMapLayerProxyModel, "Filter", "RasterLayer")
FILTRO_LINEA = _enum(QgsMapLayerProxyModel, "Filter", "LineLayer")


def _icono(nombre: str):
    """Icono del complemento, para reutilizarlo en el dialogo."""
    import os

    from qgis.PyQt.QtGui import QIcon

    base = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resources", "icons",
    )
    ruta = os.path.join(base, f"{nombre}.svg")
    return QIcon(ruta) if os.path.exists(ruta) else QIcon()


FIELD_ROLES = (
    ("id", "Identificador"),
    ("especie", "Especie"),
    ("categoria", "Categoria / tipo"),
    ("dap", "Diametro (DAP o CAP)"),
    ("altura", "Altura comercial"),
    ("volumen", "Volumen"),
    ("parcela", "Bloque / parcela"),
)


class MainDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(PLUGIN_NAME)
        # La ventana se dimensiona contra la pantalla disponible en lugar de
        # fijarse: con ocho modulos, las pestanas de parametros superan la
        # altura de una pantalla de portatil y el dialogo quedaba cortado sin
        # posibilidad de llegar a los botones.
        self.setMinimumSize(700, 420)
        self._ajustar_a_pantalla()
        self._ctx: PlanningContext | None = None

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.tabs.addTab(
            self._con_ayuda(self._build_inputs_tab(), "entradas"), "Entradas"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_censo_tab(), "censo"), "Censo"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_hydro_tab(), "hidrologia"), "Hidrologia"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_patios_tab(), "patios"), "Patios"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_roads_tab(), "vias"), "Vias"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_norms_tab(), "normativa"), "Normativa"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_modules_tab(), "modulos"), "Modulos"
        )
        self.tabs.addTab(
            self._con_ayuda(self._build_output_tab(), "salidas"), "Salidas"
        )

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(70)
        self.log.setMaximumHeight(140)
        self.log.setPlaceholderText("Registro de ejecucion")
        root.addWidget(self.log)

        buttons = QDialogButtonBox()
        self.btn_run = buttons.addButton(tr("Ejecutar"), ROL_ACEPTAR)
        buttons.addButton(tr("Cerrar"), ROL_RECHAZAR)
        self.btn_run.clicked.connect(self.run_chain)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_fields()

    # ------------------------------------------------------------- pestanas

    def _ajustar_a_pantalla(self):
        """Dimensiona el dialogo dejando margen para la barra de tareas."""
        try:
            from qgis.PyQt.QtWidgets import QApplication

            pantalla = QApplication.primaryScreen()
            if pantalla is None:
                self.resize(820, 700)
                return
            disponible = pantalla.availableGeometry()
            ancho = min(880, int(disponible.width() * 0.92))
            alto = min(820, int(disponible.height() * 0.90))
            self.resize(ancho, alto)
        except Exception:  # noqa: BLE001
            self.resize(820, 700)

    def _con_ayuda(self, page: QWidget, seccion: str) -> QScrollArea:
        """Antepone a la pestana una barra con su boton de ayuda.

        El manual se aloja en el repositorio y cada pestana enlaza a su
        seccion: se corrige sin publicar version nueva, y el usuario llega
        directo a lo que necesita en lugar de buscar dentro de un documento
        largo.
        """
        contenedor = QWidget()
        caja = QVBoxLayout(contenedor)
        caja.setContentsMargins(0, 0, 0, 0)
        caja.setSpacing(4)

        barra = QHBoxLayout()
        barra.addStretch()
        boton = QPushButton(tr("  Ayuda de esta pestana"))
        boton.setIcon(_icono("help"))
        boton.setFlat(True)
        boton.setCursor(Qt.CursorShape.PointingHandCursor)
        boton.clicked.connect(lambda: self._abrir_ayuda(seccion))
        barra.addWidget(boton)
        caja.addLayout(barra)
        caja.addWidget(page)

        return self._scrollable(contenedor)

    def _abrir_ayuda(self, seccion: str):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        url = docs_url(seccion)
        QDesktopServices.openUrl(QUrl(url))
        self._log(f"Manual: {url}")

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """Envuelve una pestana en un area desplazable.

        Sin esto, una pestana mas alta que la ventana queda cortada y sus
        controles inferiores son inalcanzables. El ancho sigue al contenedor,
        de modo que solo aparece la barra vertical.
        """
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(SIN_MARCO)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    def _build_inputs_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.cmb_censo = QgsMapLayerComboBox()
        self.cmb_censo.setFilters(FILTRO_PUNTO)
        self.cmb_censo.layerChanged.connect(self._on_censo_changed)
        form.addRow("Capa de censo:", self.cmb_censo)

        self.chk_selected = QCheckBox("Usar solo las entidades seleccionadas")
        form.addRow("", self.chk_selected)

        self.cmb_pca = QgsMapLayerComboBox()
        self.cmb_pca.setFilters(FILTRO_POLIGONO)
        form.addRow("Poligono de PCA / concesion:", self.cmb_pca)

        self.cmb_dem = QgsMapLayerComboBox()
        self.cmb_dem.setFilters(FILTRO_RASTER)
        self.cmb_dem.layerChanged.connect(self._on_dem_changed)
        form.addRow("Modelo de elevacion:", self.cmb_dem)

        self.lbl_dem = QLabel("")
        self.lbl_dem.setWordWrap(True)
        form.addRow("", self.lbl_dem)

        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsProject.instance().crs())
        self.crs_widget.crsChanged.connect(self._on_crs_changed)
        form.addRow("SRC de trabajo:", self.crs_widget)

        self.lbl_crs = QLabel("")
        self.lbl_crs.setWordWrap(True)
        form.addRow("", self.lbl_crs)

        # Panel de estado: el espacio libre de esta pestana se aprovecha para
        # que el usuario vea si las entradas estan completas ANTES de ejecutar,
        # en lugar de descubrirlo en el registro despues de una corrida larga.
        self.box_estado = QGroupBox("Estado de las entradas")
        estado = QVBoxLayout(self.box_estado)
        self.lbl_estado = QLabel("Seleccione las capas de entrada.")
        self.lbl_estado.setWordWrap(True)
        self.lbl_estado.setTextFormat(Qt.TextFormat.RichText)
        estado.addWidget(self.lbl_estado)
        form.addRow(self.box_estado)

        return page

    def _refresh_estado(self):
        """Resumen en vivo de si las entradas permiten ejecutar."""
        if not hasattr(self, "lbl_estado"):
            return

        lineas = []
        censo = self.cmb_censo.currentLayer()
        if censo is not None and censo.isValid():
            n = (
                censo.selectedFeatureCount()
                if self.chk_selected.isChecked()
                else censo.featureCount()
            )
            lineas.append(f"&#10003; Censo: {n:,} individuos")
        else:
            lineas.append("&#10007; Falta la capa de censo")

        pca = self.cmb_pca.currentLayer()
        if pca is not None and pca.isValid():
            area = 0.0
            for feat in pca.getFeatures():
                if feat.hasGeometry():
                    area += feat.geometry().area()
            lineas.append(f"&#10003; PCA: {area / 10000:,.1f} ha")
        else:
            lineas.append("&#10007; Falta el poligono de PCA")

        dem = self.cmb_dem.currentLayer()
        crs = self.crs_widget.crs()
        if dem is None or not dem.isValid():
            lineas.append("&#10007; Falta el modelo de elevacion")
        elif dem.crs().isGeographic():
            lineas.append(
                "&#10007; El DEM esta en grados: se reproyectara "
                f"automaticamente a {crs.authid()}"
            )
        else:
            lineas.append(
                f"&#10003; DEM: celda de {dem.rasterUnitsPerPixelX():.1f} m"
            )

        if crs is not None and crs.isValid() and not crs.isGeographic():
            lineas.append(f"&#10003; SRC de trabajo: {crs.authid()}")
        else:
            lineas.append("&#10007; El SRC de trabajo debe ser proyectado en metros")

        salida = self._salida_definida() if hasattr(self, "cmb_modo_salida") else None
        vias_activo = (
            hasattr(self, "module_checks")
            and M5_CAMINOS in self.module_checks
            and self.module_checks[M5_CAMINOS].isChecked()
        )
        if salida is not None:
            modo, valor = salida
            if modo == "xy":
                lineas.append(
                    f"&#10003; Punto de salida: {valor[0]:,.0f}, {valor[1]:,.0f}"
                )
            else:
                lineas.append(f"&#10003; Punto de salida: capa {valor.name()}")
        elif vias_activo:
            lineas.append(
                "&#10007; Falta el punto de salida y el modulo de vias esta "
                "activo: no podra trazarse la red"
            )

        activos = [
            MODULE_LABELS[m] for m, chk in self.module_checks.items()
            if chk.isChecked()
        ] if hasattr(self, "module_checks") else []
        if activos:
            lineas.append("Modulos: " + ", ".join(activos))

        self.lbl_estado.setText("<br>".join(lineas))

    def _build_censo_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        box = QGroupBox("Mapeo de campos")
        form = QFormLayout(box)
        self.field_combos: dict[str, QComboBox] = {}
        for role, label in FIELD_ROLES:
            combo = QComboBox()
            combo.currentIndexChanged.connect(self._validate_censo)
            self.field_combos[role] = combo
            form.addRow(f"{label}:", combo)
        outer.addWidget(box)

        box2 = QGroupBox("Calculo de volumen")
        form2 = QFormLayout(box2)

        self.cmb_diam = QComboBox()
        self.cmb_diam.addItem("DAP en metros", censo_mod.DIAM_DAP_M)
        self.cmb_diam.addItem("DAP en centimetros", censo_mod.DIAM_DAP_CM)
        self.cmb_diam.addItem("CAP en centimetros", censo_mod.DIAM_CAP_CM)
        self.cmb_diam.currentIndexChanged.connect(self._validate_censo)
        form2.addRow("Unidad del diametro:", self.cmb_diam)

        self.spn_ff = QDoubleSpinBox()
        self.spn_ff.setRange(0.10, 1.00)
        self.spn_ff.setDecimals(3)
        self.spn_ff.setSingleStep(0.01)
        self.spn_ff.setValue(censo_mod.DEFAULT_FORM_FACTOR)
        self.spn_ff.valueChanged.connect(self._validate_censo)
        form2.addRow("Factor de forma:", self.spn_ff)

        self.chk_recompute = QCheckBox(
            "Recalcular el volumen aunque el censo lo traiga"
        )
        self.chk_recompute.stateChanged.connect(self._validate_censo)
        form2.addRow("", self.chk_recompute)

        self.lst_harvest = QTableWidget(0, 2)
        self.lst_harvest.setHorizontalHeaderLabels(["Valor", "Aprovechable"])
        self.lst_harvest.horizontalHeader().setSectionResizeMode(
            0, ESTIRAR
        )
        self.lst_harvest.setEditTriggers(SIN_EDICION)
        self.lst_harvest.setMaximumHeight(110)
        form2.addRow("Categorias:", self.lst_harvest)
        outer.addWidget(box2)

        box3 = QGroupBox("Diametro minimo de corta (DMC)")
        v3 = QVBoxLayout(box3)
        nota_dmc = QLabel(
            "Un individuo por debajo del DMC de su especie no puede talarse, "
            "asi que no suma volumen al POA ni se asigna a un patio. El DMC lo "
            "fija la autoridad forestal por especie: cargue la tabla vigente. "
            "Dejarla vacia desactiva la verificacion."
        )
        nota_dmc.setWordWrap(True)
        v3.addWidget(nota_dmc)

        self.tbl_dmc = QTableWidget(0, 3)
        self.tbl_dmc.setHorizontalHeaderLabels(
            ["Especie", "DMC aplicado (cm)", "Normado / observaciones"]
        )
        self.tbl_dmc.horizontalHeader().setSectionResizeMode(0, ESTIRAR)
        self.tbl_dmc.horizontalHeader().setSectionResizeMode(2, ESTIRAR)
        self.tbl_dmc.setMaximumHeight(160)
        self.tbl_dmc.itemChanged.connect(self._on_dmc_changed)
        v3.addWidget(self.tbl_dmc)

        fila = QHBoxLayout()
        btn_esp = QPushButton(tr("Cargar especies del censo"))
        btn_esp.clicked.connect(self._fill_dmc_from_layer)
        btn_peru = QPushButton(tr("Cargar DMC oficial (Peru)"))
        btn_peru.clicked.connect(self._load_dmc_peru)
        btn_imp = QPushButton(tr("Importar CSV..."))
        btn_imp.clicked.connect(self._import_dmc)
        btn_exp = QPushButton(tr("Exportar CSV..."))
        btn_exp.clicked.connect(self._export_dmc)
        btn_lim = QPushButton("Limpiar")
        btn_lim.clicked.connect(lambda: self.tbl_dmc.setRowCount(0))
        for b in (btn_esp, btn_peru, btn_imp, btn_exp, btn_lim):
            fila.addWidget(b)
        fila.addStretch()
        v3.addLayout(fila)

        f3 = QFormLayout()
        self.spn_dmc_def = QDoubleSpinBox()
        self.spn_dmc_def.setRange(0.0, 300.0)
        self.spn_dmc_def.setValue(0.0)
        self.spn_dmc_def.setSuffix(" cm")
        self.spn_dmc_def.setSpecialValueText("no verificar")
        self.spn_dmc_def.valueChanged.connect(self._validate_censo)
        f3.addRow("DMC para especies no listadas:", self.spn_dmc_def)
        v3.addLayout(f3)
        outer.addWidget(box3)

        self.txt_censo = QPlainTextEdit()
        self.txt_censo.setReadOnly(True)
        self.txt_censo.setPlaceholderText(
            "Seleccione la capa de censo para ver la validacion"
        )
        outer.addWidget(self.txt_censo)
        return page

    def _build_hydro_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        box = QGroupBox("Extraccion de cauces")
        form = QFormLayout(box)

        self.spn_threshold = QDoubleSpinBox()
        self.spn_threshold.setRange(0.1, 10000.0)
        self.spn_threshold.setDecimals(1)
        self.spn_threshold.setValue(DEFAULT_THRESHOLD_HA)
        self.spn_threshold.setSuffix(" ha")
        self.spn_threshold.valueChanged.connect(self._update_threshold_hint)
        form.addRow("Area drenada minima:", self.spn_threshold)

        self.lbl_threshold = QLabel("")
        self.lbl_threshold.setWordWrap(True)
        form.addRow("", self.lbl_threshold)

        self.cmb_order = QComboBox()
        self.cmb_order.addItem("Strahler (normativa)", hidro_mod.ORDER_STRAHLER)
        self.cmb_order.addItem("Shreve (caudal)", hidro_mod.ORDER_SHREVE)
        self.cmb_order.currentIndexChanged.connect(self._reset_width_table)
        form.addRow("Criterio de faja:", self.cmb_order)

        self.spn_min_seg = QDoubleSpinBox()
        self.spn_min_seg.setRange(0.0, 500.0)
        self.spn_min_seg.setValue(20.0)
        self.spn_min_seg.setSuffix(" m")
        form.addRow("Longitud minima de tramo:", self.spn_min_seg)

        self.spn_smooth = QSpinBox()
        self.spn_smooth.setRange(0, 5)
        self.spn_smooth.setValue(1)
        form.addRow("Iteraciones de suavizado:", self.spn_smooth)

        outer.addWidget(box)

        box2 = QGroupBox("Anchos de faja marginal")
        v2 = QVBoxLayout(box2)
        nota = QLabel(
            "Los anchos son un punto de partida, no una constante normativa: "
            "la autoridad fija la faja marginal caso por caso. El informe "
            "declara la tabla efectivamente aplicada."
        )
        nota.setWordWrap(True)
        v2.addWidget(nota)

        self.tbl_widths = QTableWidget(0, 2)
        self.tbl_widths.setHorizontalHeaderLabels(
            ["Clase (desde)", "Semiancho (m)"]
        )
        self.tbl_widths.horizontalHeader().setSectionResizeMode(
            ESTIRAR
        )
        self.tbl_widths.setMaximumHeight(150)
        v2.addWidget(self.tbl_widths)

        row = QHBoxLayout()
        btn_add = QPushButton("Agregar clase")
        btn_add.clicked.connect(lambda: self._add_width_row(0, 10.0))
        btn_del = QPushButton("Quitar")
        btn_del.clicked.connect(self._remove_width_row)
        btn_reset = QPushButton("Restablecer")
        btn_reset.clicked.connect(self._reset_width_table)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addWidget(btn_reset)
        row.addStretch()
        v2.addLayout(row)

        self.spn_above = QDoubleSpinBox()
        self.spn_above.setRange(0.0, 1000.0)
        self.spn_above.setValue(50.0)
        self.spn_above.setSuffix(" m")
        f3 = QFormLayout()
        f3.addRow("Ancho sobre la clase mayor:", self.spn_above)
        v2.addLayout(f3)

        outer.addWidget(box2)
        self._reset_width_table()
        return page

    def _build_patios_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        box = QGroupBox("Patios de acopio")
        form = QFormLayout(box)

        self.spn_vol_max = QDoubleSpinBox()
        self.spn_vol_max.setRange(1.0, 10000.0)
        self.spn_vol_max.setValue(patios_mod.DEFAULT_VOL_MAX_PATIO)
        self.spn_vol_max.setSuffix(" m3")
        form.addRow("Volumen maximo por patio:", self.spn_vol_max)

        self.spn_vol_min = QDoubleSpinBox()
        self.spn_vol_min.setRange(0.0, 1000.0)
        self.spn_vol_min.setValue(patios_mod.DEFAULT_VOL_MIN_PATIO)
        self.spn_vol_min.setSuffix(" m3")
        form.addRow("Volumen minimo (fusion):", self.spn_vol_min)

        self.spn_dist_arr = QDoubleSpinBox()
        self.spn_dist_arr.setRange(10.0, 3000.0)
        self.spn_dist_arr.setValue(patios_mod.DEFAULT_DIST_MAX_ARRASTRE)
        self.spn_dist_arr.setSuffix(" m")
        form.addRow("Distancia maxima de arrastre:", self.spn_dist_arr)

        self.spn_area_patio = QDoubleSpinBox()
        self.spn_area_patio.setRange(50.0, 20000.0)
        self.spn_area_patio.setValue(patios_mod.DEFAULT_AREA_PATIO)
        self.spn_area_patio.setSuffix(" m2")
        form.addRow("Area habilitada por patio:", self.spn_area_patio)

        self.spn_pend_patio = QDoubleSpinBox()
        self.spn_pend_patio.setRange(0.0, 100.0)
        self.spn_pend_patio.setValue(patios_mod.DEFAULT_PEND_MAX_PATIO)
        self.spn_pend_patio.setSuffix(" %")
        form.addRow("Pendiente maxima del sitio:", self.spn_pend_patio)

        self.spn_dist_cauce = QDoubleSpinBox()
        self.spn_dist_cauce.setRange(0.0, 500.0)
        self.spn_dist_cauce.setValue(patios_mod.DEFAULT_DIST_MIN_CAUCE)
        self.spn_dist_cauce.setSuffix(" m")
        form.addRow("Distancia minima a cauce:", self.spn_dist_cauce)

        self.spn_pieza = QDoubleSpinBox()
        self.spn_pieza.setRange(1.0, 200.0)
        self.spn_pieza.setValue(patios_mod.DEFAULT_PIEZA_GRANDE)
        self.spn_pieza.setSuffix(" m3")
        form.addRow("Pieza sobredimensionada desde:", self.spn_pieza)

        self.spn_reuso = QDoubleSpinBox()
        self.spn_reuso.setRange(0.0, 0.95)
        self.spn_reuso.setDecimals(2)
        self.spn_reuso.setSingleStep(0.05)
        self.spn_reuso.setValue(patios_mod.DEFAULT_REUSO_PISTA)
        form.addRow("Reuso de pista de arrastre:", self.spn_reuso)

        aviso = QLabel(
            "El reuso de pista decide el intercambio: por debajo de 0.70, mas "
            "patios reducen el impacto total porque acortan el arrastre; por "
            "encima, la diferencia se desvanece."
        )
        aviso.setWordWrap(True)
        form.addRow("", aviso)
        outer.addWidget(box)

        box2 = QGroupBox("Centros de acopio")
        form2 = QFormLayout(box2)

        self.txt_escenarios = QLineEdit("1, 2, 3")
        form2.addRow("Escenarios a comparar:", self.txt_escenarios)

        self.spn_centro_elegido = QSpinBox()
        self.spn_centro_elegido.setRange(0, 20)
        self.spn_centro_elegido.setValue(0)
        self.spn_centro_elegido.setSpecialValueText("primero de la lista")
        form2.addRow("Escenario a aplicar:", self.spn_centro_elegido)

        self.spn_area_centro = QDoubleSpinBox()
        self.spn_area_centro.setRange(100.0, 100000.0)
        self.spn_area_centro.setValue(patios_mod.DEFAULT_AREA_CENTRO)
        self.spn_area_centro.setSuffix(" m2")
        form2.addRow("Area por centro:", self.spn_area_centro)

        self.spn_pend_centro = QDoubleSpinBox()
        self.spn_pend_centro.setRange(0.0, 100.0)
        self.spn_pend_centro.setValue(patios_mod.DEFAULT_PEND_MAX_CENTRO)
        self.spn_pend_centro.setSuffix(" %")
        form2.addRow("Pendiente maxima del centro:", self.spn_pend_centro)

        self.spn_orden_barrera = QSpinBox()
        self.spn_orden_barrera.setRange(0, 10)
        self.spn_orden_barrera.setValue(3)
        self.spn_orden_barrera.setSpecialValueText("no evaluar")
        form2.addRow("Cauce mayor desde orden:", self.spn_orden_barrera)
        outer.addWidget(box2)

        nota = QLabel(
            "Las distancias de este modulo son euclidianas. La distancia real "
            "por via de saca la resolvera el modulo de caminos y puede ser "
            "bastante mayor donde el relieve o los humedales obligan a rodear."
        )
        nota.setWordWrap(True)
        outer.addWidget(nota)
        outer.addStretch()
        return page

    def _build_roads_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        box0 = QGroupBox("Punto de salida")
        f0 = QFormLayout(box0)

        nota0 = QLabel(
            "La red converge hacia este punto: empalme con via existente, "
            "puerto sobre rio navegable o campamento. Es una decision de "
            "acceso real que el modelo no puede inferir del terreno, y sin "
            "ella el modulo de vias no puede ejecutarse."
        )
        nota0.setWordWrap(True)
        f0.addRow("", nota0)

        self.cmb_modo_salida = QComboBox()
        self.cmb_modo_salida.addItem("Marcar en el mapa", "mapa")
        self.cmb_modo_salida.addItem("Escribir coordenadas", "coordenadas")
        self.cmb_modo_salida.addItem("Tomar de una capa", "capa")
        self.cmb_modo_salida.currentIndexChanged.connect(self._on_modo_salida)
        f0.addRow("Como definirlo:", self.cmb_modo_salida)

        fila_xy = QHBoxLayout()
        self.spn_salida_x = QDoubleSpinBox()
        self.spn_salida_x.setRange(-10000000.0, 10000000.0)
        self.spn_salida_x.setDecimals(2)
        self.spn_salida_x.setPrefix("E ")
        self.spn_salida_x.valueChanged.connect(self._refresh_estado)
        self.spn_salida_y = QDoubleSpinBox()
        self.spn_salida_y.setRange(-10000000.0, 10000000.0)
        self.spn_salida_y.setDecimals(2)
        self.spn_salida_y.setPrefix("N ")
        self.spn_salida_y.valueChanged.connect(self._refresh_estado)
        self.btn_pick = QPushButton(tr("Marcar en el mapa"))
        self.btn_pick.setCheckable(True)
        self.btn_pick.clicked.connect(self._toggle_pick)
        fila_xy.addWidget(self.spn_salida_x)
        fila_xy.addWidget(self.spn_salida_y)
        fila_xy.addWidget(self.btn_pick)
        holder_xy = QWidget()
        holder_xy.setLayout(fila_xy)
        f0.addRow("Coordenadas:", holder_xy)

        self.cmb_salida = QgsMapLayerComboBox()
        self.cmb_salida.setFilters(FILTRO_PUNTO)
        try:
            self.cmb_salida.setAllowEmptyLayer(True)
        except AttributeError:
            pass  # no disponible en versiones antiguas de QGIS
        self.cmb_salida.layerChanged.connect(self._refresh_estado)
        f0.addRow("Capa de punto:", self.cmb_salida)

        self.lbl_salida = QLabel("")
        self.lbl_salida.setWordWrap(True)
        f0.addRow("", self.lbl_salida)
        outer.addWidget(box0)
        self._on_modo_salida()

        box = QGroupBox("Superficie de costo")
        form = QFormLayout(box)

        self.spn_pend_via = QDoubleSpinBox()
        self.spn_pend_via.setRange(1.0, 60.0)
        self.spn_pend_via.setValue(costo_mod.PENDIENTE_MAX_PRINCIPAL)
        self.spn_pend_via.setSuffix(" %")
        form.addRow("Pendiente del terreno de referencia:", self.spn_pend_via)

        nota_pend = QLabel(
            "Es la pendiente TRANSVERSAL del terreno, que determina el "
            "movimiento de tierras y puede llegar al 74% del costo de "
            "construccion. No es la rasante de la via: esa se configura abajo "
            "y se aplica solo con el motor de grafo."
        )
        nota_pend.setWordWrap(True)
        form.addRow("", nota_pend)

        self.spn_w_pend = QDoubleSpinBox()
        self.spn_w_pend.setRange(0.0, 10.0)
        self.spn_w_pend.setSingleStep(0.1)
        self.spn_w_pend.setValue(1.0)
        form.addRow("Peso de pendiente:", self.spn_w_pend)

        self.spn_w_hum = QDoubleSpinBox()
        self.spn_w_hum.setRange(0.0, 10.0)
        self.spn_w_hum.setSingleStep(0.1)
        self.spn_w_hum.setValue(1.5)
        form.addRow("Peso de humedad:", self.spn_w_hum)

        self.spn_w_restr = QDoubleSpinBox()
        self.spn_w_restr.setRange(0.0, 10.0)
        self.spn_w_restr.setSingleStep(0.1)
        self.spn_w_restr.setValue(1.0)
        form.addRow("Peso de restricciones:", self.spn_w_restr)

        self.spn_w_cruce = QDoubleSpinBox()
        self.spn_w_cruce.setRange(0.0, 10.0)
        self.spn_w_cruce.setSingleStep(0.1)
        self.spn_w_cruce.setValue(1.0)
        form.addRow("Peso de cruces:", self.spn_w_cruce)

        self.cmb_metodo = QComboBox()
        self.cmb_metodo.addItem("Divisorias de microcuenca (llanura)", "lomas")
        self.cmb_metodo.addItem("Menor costo por pendiente (montana)", "costo")
        self.cmb_metodo.currentIndexChanged.connect(self._on_metodo)
        form.addRow("Criterio de trazado:", self.cmb_metodo)

        self.spn_w_lomas = QDoubleSpinBox()
        self.spn_w_lomas.setRange(0.0, 20.0)
        self.spn_w_lomas.setSingleStep(0.5)
        self.spn_w_lomas.setValue(3.0)
        form.addRow("Peso de divisorias:", self.spn_w_lomas)

        self.spn_banda_lomas = QDoubleSpinBox()
        self.spn_banda_lomas.setRange(10.0, 1000.0)
        self.spn_banda_lomas.setValue(60.0)
        self.spn_banda_lomas.setSuffix(" m")
        form.addRow("Banda de atraccion:", self.spn_banda_lomas)

        self.spn_umbral_lomas = QDoubleSpinBox()
        self.spn_umbral_lomas.setRange(1.0, 2000.0)
        self.spn_umbral_lomas.setValue(150.0)
        self.spn_umbral_lomas.setSuffix(" ha")
        form.addRow("Umbral de divisoria:", self.spn_umbral_lomas)

        nota_met = QLabel(
            "El metodo de divisorias corre el analisis hidrologico sobre el DEM "
            "invertido: donde el agua acumularia en el modelo invertido esta, "
            "en el terreno, la linea mas alta. Es el trazado clasico en llanura "
            "amazonica. En relieve montanoso la divisoria pasa por las cumbres "
            "y el modulo lo detecta por el desnivel del area, desactivando el "
            "criterio en lugar de aplicarlo a ciegas."
        )
        nota_met.setWordWrap(True)
        form.addRow("", nota_met)

        self.spn_fuera_area = QDoubleSpinBox()
        self.spn_fuera_area.setRange(1.0, 100.0)
        self.spn_fuera_area.setValue(6.0)
        self.spn_fuera_area.setSuffix(" x")
        form.addRow("Penalizacion fuera de la PCA:", self.spn_fuera_area)

        nota_fuera = QLabel(
            "Salir del area no esta prohibido: cuando un aguajal o una quebrada "
            "encajonada bloquean el paso interno, rodear por fuera puede ser "
            "mas barato y de menor impacto. Un valor alto lo desalienta sin "
            "impedirlo; el modulo reporta los kilometros que quedan afuera para "
            "que el plan los declare y se verifique el derecho de paso."
        )
        nota_fuera.setWordWrap(True)
        form.addRow("", nota_fuera)

        self.spn_banda = QDoubleSpinBox()
        self.spn_banda.setRange(0.0, 2000.0)
        self.spn_banda.setValue(150.0)
        self.spn_banda.setSuffix(" m")
        form.addRow("Banda de incertidumbre:", self.spn_banda)

        nota = QLabel(
            "Los pesos son un juicio de ingenieria y no se deducen de los "
            "datos. Quedan registrados en el informe para sustentar la "
            "decision, no como un optimo objetivo."
        )
        nota.setWordWrap(True)
        form.addRow("", nota)
        outer.addWidget(box)

        box2 = QGroupBox("Trazado")
        f2 = QFormLayout(box2)

        self.cmb_vias_exist = QgsMapLayerComboBox()
        self.cmb_vias_exist.setFilters(FILTRO_LINEA)
        try:
            self.cmb_vias_exist.setAllowEmptyLayer(True)
            self.cmb_vias_exist.setLayer(None)
        except AttributeError:
            # setAllowEmptyLayer no existe en versiones antiguas de QGIS. Sin
            # el, el combo arranca con la primera capa de lineas seleccionada
            # en lugar de vacio, que es una molestia menor y no un fallo.
            pass
        f2.addRow("Vias existentes:", self.cmb_vias_exist)

        self.spn_factor_exist = QDoubleSpinBox()
        self.spn_factor_exist.setRange(0.0001, 1.0)
        self.spn_factor_exist.setDecimals(4)
        self.spn_factor_exist.setSingleStep(0.05)
        self.spn_factor_exist.setValue(0.0005)
        f2.addRow("Costo relativo de esa via:", self.spn_factor_exist)

        nota_exist = QLabel(
            "Si la PCA ya esta cruzada por un vial -tipicamente el de la "
            "parcela anterior-, declarelo aqui y la red convergira hacia el en "
            "lugar de abrir traza paralela. Use 0.01 a 0.05 si esta en buen "
            "estado y 0.10 a 0.30 si exige rehabilitacion. Su longitud se "
            "reporta aparte porque no es apertura nueva y no suma al impacto.\n"
            "Con una via existente que ya salga del area, el punto de salida "
            "deja de ser obligatorio."
        )
        nota_exist.setWordWrap(True)
        f2.addRow("", nota_exist)

        self.spn_rasante_fav = QDoubleSpinBox()
        self.spn_rasante_fav.setRange(1.0, 30.0)
        self.spn_rasante_fav.setValue(8.0)
        self.spn_rasante_fav.setSuffix(" %")
        f2.addRow("Rasante maxima bajando cargado:", self.spn_rasante_fav)

        self.spn_rasante_adv = QDoubleSpinBox()
        self.spn_rasante_adv.setRange(1.0, 30.0)
        self.spn_rasante_adv.setValue(4.0)
        self.spn_rasante_adv.setSuffix(" %")
        f2.addRow("Rasante maxima subiendo cargado:", self.spn_rasante_adv)

        self.spn_tramo_exc = QDoubleSpinBox()
        self.spn_tramo_exc.setRange(0.0, 2000.0)
        self.spn_tramo_exc.setValue(150.0)
        self.spn_tramo_exc.setSuffix(" m")
        f2.addRow("Tramo excepcional admitido:", self.spn_tramo_exc)

        self.spn_vecindario = QSpinBox()
        self.spn_vecindario.setRange(1, 4)
        self.spn_vecindario.setValue(3)
        f2.addRow("Orden de vecindario:", self.spn_vecindario)

        nota_rasante = QLabel(
            "La rasante es la pendiente de la via en el sentido de avance y no "
            "la del terreno: una via puede cruzar una ladera al 40% con "
            "rasante del 5% siguiendo la curva de nivel, pagando movimiento de "
            "tierras. Subir cargado es mas exigente que bajar, por eso los dos "
            "limites.\n\n"
            "El orden de vecindario decide que rasantes son representables. "
            "Sobre una ladera al 30%, el orden 1 solo permite bajar al 21%; el "
            "orden 3 llega al 9.5% y deja que la via se desarrolle. En terreno "
            "llano basta 1; en colinas hace falta 3 o 4, a costa de memoria."
        )
        nota_rasante.setWordWrap(True)
        f2.addRow("", nota_rasante)

        self.spn_ancho_pri = QDoubleSpinBox()
        self.spn_ancho_pri.setRange(2.0, 30.0)
        self.spn_ancho_pri.setValue(caminos_mod.ANCHO_PRINCIPAL)
        self.spn_ancho_pri.setSuffix(" m")
        f2.addRow("Ancho de via principal:", self.spn_ancho_pri)

        self.spn_ancho_sec = QDoubleSpinBox()
        self.spn_ancho_sec.setRange(2.0, 30.0)
        self.spn_ancho_sec.setValue(caminos_mod.ANCHO_SECUNDARIA)
        self.spn_ancho_sec.setSuffix(" m")
        f2.addRow("Ancho de via secundaria:", self.spn_ancho_sec)

        self.chk_patios_via = QCheckBox(
            "Conectar tambien los patios con via construida"
        )
        self.chk_patios_via.setChecked(True)
        f2.addRow("", self.chk_patios_via)

        self.spn_dist_patio_via = QDoubleSpinBox()
        self.spn_dist_patio_via.setRange(0.0, 3000.0)
        self.spn_dist_patio_via.setValue(500.0)
        self.spn_dist_patio_via.setSuffix(" m")
        f2.addRow("Patio servido por pista hasta:", self.spn_dist_patio_via)

        nota_patios = QLabel(
            "Un patio que queda a menos de esa distancia de una via ya trazada "
            "no recibe ramal propio: se alcanza por pista de arrastre. Asi la "
            "red crece por corredores que sirven grupos de patios, como en las "
            "redes reales, en lugar de un ramal por patio. Con cero se traza "
            "ramal a todos y el kilometraje se dispara."
        )
        nota_patios.setWordWrap(True)
        f2.addRow("", nota_patios)

        self.spn_simplificar = QDoubleSpinBox()
        self.spn_simplificar.setRange(0.0, 300.0)
        self.spn_simplificar.setValue(45.0)
        self.spn_simplificar.setSuffix(" m")
        f2.addRow("Tolerancia de alineacion:", self.spn_simplificar)

        self.spn_suavizado = QSpinBox()
        self.spn_suavizado.setRange(0, 4)
        self.spn_suavizado.setValue(2)
        f2.addRow("Iteraciones de suavizado:", self.spn_suavizado)

        nota_traza = QLabel(
            "La ruta sobre malla avanza en pasos discretos y produce diente de "
            "sierra cuando la direccion ideal no coincide con ningun vecino. "
            "El afinado quita los picos, simplifica y redondea, sin desplazar "
            "el eje fuera del corredor calculado."
        )
        nota_traza.setWordWrap(True)
        f2.addRow("", nota_traza)

        self.spn_max_dest = QSpinBox()
        self.spn_max_dest.setRange(0, 5000)
        self.spn_max_dest.setValue(0)
        self.spn_max_dest.setSpecialValueText("sin limite")
        f2.addRow("Maximo de destinos:", self.spn_max_dest)

        nota2 = QLabel(
            "Cada destino son dos llamadas a GRASS. Con muchos patios el "
            "trazado puede tardar varios minutos: use el limite para una "
            "prueba rapida antes de la corrida completa."
        )
        nota2.setWordWrap(True)
        f2.addRow("", nota2)
        outer.addWidget(box2)

        outer.addStretch()
        return page

    def _build_norms_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        box = QGroupBox("Jurisdiccion")
        form = QFormLayout(box)
        self.cmb_preset = QComboBox()
        for clave, datos in PRESETS.items():
            self.cmb_preset.addItem(datos["etiqueta"], clave)
        idx = self.cmb_preset.findData(PRESET_POR_DEFECTO)
        if idx >= 0:
            self.cmb_preset.setCurrentIndex(idx)
        self.cmb_preset.currentIndexChanged.connect(self._apply_preset)
        form.addRow("Preset:", self.cmb_preset)

        self.lbl_preset = QLabel("")
        self.lbl_preset.setWordWrap(True)
        form.addRow("", self.lbl_preset)
        outer.addWidget(box)

        box2 = QGroupBox("Limites de aprovechamiento")
        f2 = QFormLayout(box2)
        nota = QLabel(
            "Los limites se verifican sobre una malla de una hectarea y no "
            "sobre el promedio del predio: un area con media baja puede "
            "concentrar el aprovechamiento en pocas hectareas. Cero desactiva "
            "cada verificacion."
        )
        nota.setWordWrap(True)
        f2.addRow("", nota)

        self.spn_max_vol_ha = QDoubleSpinBox()
        self.spn_max_vol_ha.setRange(0.0, 500.0)
        self.spn_max_vol_ha.setSuffix(" m3/ha")
        self.spn_max_vol_ha.setSpecialValueText("no verificar")
        f2.addRow("Volumen maximo:", self.spn_max_vol_ha)

        self.spn_max_arb_ha = QDoubleSpinBox()
        self.spn_max_arb_ha.setRange(0.0, 200.0)
        self.spn_max_arb_ha.setSuffix(" arb/ha")
        self.spn_max_arb_ha.setSpecialValueText("no verificar")
        f2.addRow("Individuos maximos:", self.spn_max_arb_ha)

        self.spn_max_ab = QDoubleSpinBox()
        self.spn_max_ab.setRange(0.0, 100.0)
        self.spn_max_ab.setSuffix(" %")
        self.spn_max_ab.setSpecialValueText("no verificar")
        f2.addRow("Area basal removible:", self.spn_max_ab)

        self.spn_min_abund = QDoubleSpinBox()
        self.spn_min_abund.setRange(0.0, 20.0)
        self.spn_min_abund.setDecimals(3)
        self.spn_min_abund.setSingleStep(0.05)
        self.spn_min_abund.setSuffix(" ind/ha")
        self.spn_min_abund.setSpecialValueText("no verificar")
        f2.addRow("Abundancia minima por especie:", self.spn_min_abund)

        self.chk_censo_comercial = QCheckBox(
            "El insumo es un censo comercial, no un inventario estadistico"
        )
        self.chk_censo_comercial.setChecked(True)
        f2.addRow("", self.chk_censo_comercial)

        nota2 = QLabel(
            "Sobre un censo comercial, el area basal removida y la abundancia "
            "por especie no son concluyentes: solo se registran las especies de "
            "interes por encima del DMC, asi que el denominador no es el rodal. "
            "Con la casilla marcada esos dos criterios se informan como no "
            "verificables en lugar de reportar un incumplimiento."
        )
        nota2.setWordWrap(True)
        f2.addRow("", nota2)
        outer.addWidget(box2)

        box_t = QGroupBox("Rendimiento y transporte")
        ft = QFormLayout(box_t)
        nota_t = QLabel(
            "El volumen del censo es una estimacion geometrica sobre el arbol "
            "en pie. Lo que sale del bosque es menos: individuos no hallados, "
            "fustes huecos y rajaduras al derribar. Es el parametro con mayor "
            "efecto sobre el numero de viajes."
        )
        nota_t.setWordWrap(True)
        ft.addRow("", nota_t)

        self.spn_rendimiento = QDoubleSpinBox()
        self.spn_rendimiento.setRange(0.05, 1.0)
        self.spn_rendimiento.setDecimals(2)
        self.spn_rendimiento.setSingleStep(0.05)
        self.spn_rendimiento.setValue(transp_mod.DEFAULT_RENDIMIENTO)
        ft.addRow("Rendimiento censo a rollizo:", self.spn_rendimiento)

        self.spn_cap_vol = QDoubleSpinBox()
        self.spn_cap_vol.setRange(1.0, 200.0)
        self.spn_cap_vol.setValue(30.0)
        self.spn_cap_vol.setSuffix(" m3")
        ft.addRow("Capacidad del vehiculo:", self.spn_cap_vol)

        self.spn_cap_peso = QDoubleSpinBox()
        self.spn_cap_peso.setRange(1.0, 100.0)
        self.spn_cap_peso.setValue(30.0)
        self.spn_cap_peso.setSuffix(" t")
        ft.addRow("Carga util del vehiculo:", self.spn_cap_peso)

        self.spn_densidad = QDoubleSpinBox()
        self.spn_densidad.setRange(0.2, 2.0)
        self.spn_densidad.setDecimals(2)
        self.spn_densidad.setSingleStep(0.05)
        self.spn_densidad.setValue(1.0)
        self.spn_densidad.setSuffix(" t/m3")
        ft.addRow("Densidad de la madera verde:", self.spn_densidad)

        self.spn_estiba = QDoubleSpinBox()
        self.spn_estiba.setRange(0.4, 1.0)
        self.spn_estiba.setDecimals(2)
        self.spn_estiba.setValue(0.74)
        ft.addRow("Factor de estiba:", self.spn_estiba)

        nota_t2 = QLabel(
            "El vehiculo se llena por PESO o por VOLUMEN, lo que ocurra "
            "primero. Con especies densas manda el peso mucho antes que el "
            "cajon: estimar la flota solo por volumen subestima los viajes."
        )
        nota_t2.setWordWrap(True)
        ft.addRow("", nota_t2)
        outer.addWidget(box_t)

        box3 = QGroupBox("Impacto")
        f3 = QFormLayout(box3)
        self.spn_n_camp = QSpinBox()
        self.spn_n_camp.setRange(0, 50)
        self.spn_n_camp.setValue(1)
        f3.addRow("Numero de campamentos:", self.spn_n_camp)

        self.spn_area_camp = QDoubleSpinBox()
        self.spn_area_camp.setRange(100.0, 50000.0)
        self.spn_area_camp.setValue(impacto_mod.DEFAULT_AREA_CAMPAMENTO)
        self.spn_area_camp.setSuffix(" m2")
        f3.addRow("Area por campamento:", self.spn_area_camp)
        outer.addWidget(box3)

        outer.addStretch()
        self._apply_preset()
        return page

    def _on_metodo(self):
        """El peso de divisorias solo aplica al metodo de llanura."""
        usa_lomas = self.cmb_metodo.currentData() == "lomas"
        self.spn_w_lomas.setEnabled(usa_lomas)
        self.spn_umbral_lomas.setEnabled(usa_lomas)
        self.spn_banda_lomas.setEnabled(usa_lomas)
        if not usa_lomas:
            self.spn_w_lomas.setValue(0.0)
        elif self.spn_w_lomas.value() == 0.0:
            self.spn_w_lomas.setValue(3.0)

    def _apply_preset(self):
        """Carga los limites del preset seleccionado."""
        if not hasattr(self, "spn_max_vol_ha"):
            return
        clave = self.cmb_preset.currentData()
        datos = PRESETS.get(clave, {})
        self.lbl_preset.setText(datos.get("fuente", ""))
        self.spn_max_vol_ha.setValue(float(datos.get("max_volumen_ha", 0.0)))
        self.spn_max_arb_ha.setValue(float(datos.get("max_arboles_ha", 0.0)))
        self.spn_max_ab.setValue(float(datos.get("max_area_basal_pct", 0.0)))
        self.spn_min_abund.setValue(float(datos.get("min_abundancia_ha", 0.0)))

    def _build_modules_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.addWidget(QLabel("Modulos a ejecutar:"))

        self.module_checks: dict[str, QCheckBox] = {}
        for mid in MODULE_ORDER:
            chk = QCheckBox(f"{mid.upper()[:2]} - {MODULE_LABELS[mid]}")
            implemented = mid in MODULES_IMPLEMENTED
            chk.setChecked(implemented)
            chk.setEnabled(implemented)
            if not implemented:
                chk.setText(chk.text() + "   (en desarrollo)")
            self.module_checks[mid] = chk
            outer.addWidget(chk)

        outer.addStretch()
        nota = QLabel(
            "Los trazos y ubicaciones que genera este complemento son "
            "propuestas tecnicas sujetas a verificacion de campo, no disenos "
            "definitivos."
        )
        nota.setWordWrap(True)
        outer.addWidget(nota)
        return page

    def _build_output_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        box = QGroupBox("Capas")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.txt_gpkg = QLineEdit()
        self.txt_gpkg.setPlaceholderText("Dejar vacio para capas temporales")
        btn = QPushButton("...")
        btn.setMaximumWidth(32)
        btn.clicked.connect(self._pick_gpkg)
        row.addWidget(self.txt_gpkg)
        row.addWidget(btn)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("GeoPackage:", holder)

        self.chk_load = QCheckBox("Cargar las capas resultantes al proyecto")
        self.chk_load.setChecked(True)
        form.addRow("", self.chk_load)

        self.spn_seed = QSpinBox()
        self.spn_seed.setRange(0, 2147483647)
        self.spn_seed.setValue(20260812)
        form.addRow("Semilla aleatoria:", self.spn_seed)

        nota = QLabel(
            "La semilla fija hace reproducible el agrupamiento de patios. Sin "
            "ella, cada corrida daria un resultado distinto."
        )
        nota.setWordWrap(True)
        form.addRow("", nota)
        outer.addWidget(box)

        box2 = QGroupBox("Documentos")
        v2 = QVBoxLayout(box2)
        nota2 = QLabel(
            "Se generan despues de ejecutar, con los resultados en memoria. "
            "Se escriben en una carpeta temporal y se abren en la aplicacion "
            "correspondiente; guardelos desde ahi donde los necesite."
        )
        nota2.setWordWrap(True)
        v2.addWidget(nota2)

        self.btn_report = QPushButton(tr("GENERAR INFORME"))
        self.btn_report.setMinimumHeight(46)
        self.btn_report.clicked.connect(self.generate_report)
        v2.addWidget(self.btn_report)

        self.btn_stats = QPushButton(tr("CUADROS ESTADISTICOS EN EXCEL"))
        self.btn_stats.setMinimumHeight(46)
        self.btn_stats.clicked.connect(self.generate_stats)
        v2.addWidget(self.btn_stats)

        self.lbl_docs = QLabel("Ejecute primero la cadena de analisis.")
        self.lbl_docs.setWordWrap(True)
        v2.addWidget(self.lbl_docs)
        outer.addWidget(box2)

        outer.addStretch()
        self._update_doc_buttons()
        return page

    def _update_doc_buttons(self):
        listo = self._ctx is not None and bool(self._ctx.results)
        if hasattr(self, "btn_report"):
            self.btn_report.setEnabled(listo)
            self.btn_stats.setEnabled(listo)
            self.lbl_docs.setText(
                "Resultados disponibles: "
                + ", ".join(self._ctx.completed_modules())
                if listo
                else "Ejecute primero la cadena de analisis."
            )

    def _temp_path(self, extension: str) -> str:
        """Ruta temporal con marca de tiempo, para no pisar corridas previas."""
        import tempfile
        from datetime import datetime

        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        carpeta = os.path.join(tempfile.gettempdir(), "yf_forest_planner")
        os.makedirs(carpeta, exist_ok=True)
        return os.path.join(carpeta, f"forest_planner_{sello}{extension}")

    def _open(self, path: str):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def generate_report(self):
        if self._ctx is None or not self._ctx.results:
            QMessageBox.information(
                self, PLUGIN_NAME, "Ejecute primero la cadena de analisis."
            )
            return
        try:
            salidas = report_mod.write_reports(
                self._ctx, self._temp_path("")
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, PLUGIN_NAME, f"No se pudo generar el informe:\n{exc}"
            )
            return

        self._log("")
        for clave, valor in salidas.items():
            if clave == "docx_error":
                self._log(f"  ! Word no se genero: {valor}")
            else:
                self._log(f"  Informe {clave.upper()}: {valor}")

        destino = salidas.get("docx") or salidas.get("html")
        if destino:
            self._open(destino)

    def generate_stats(self):
        if self._ctx is None or not self._ctx.results:
            QMessageBox.information(
                self, PLUGIN_NAME, "Ejecute primero la cadena de analisis."
            )
            return
        path = self._temp_path(".xlsx")
        try:
            stats_mod.build_workbook(self._ctx, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, PLUGIN_NAME, f"No se pudieron generar los cuadros:\n{exc}"
            )
            return
        self._log("")
        self._log(f"  Cuadros estadisticos: {path}")
        self._open(path)

    # ------------------------------------------------------------- reaccion

    def _on_censo_changed(self, layer):
        self._refresh_fields()
        self._refresh_estado()

    def _on_dem_changed(self, layer):
        self._update_threshold_hint()
        if layer is None or not layer.isValid():
            self.lbl_dem.setText("")
            return
        cell = layer.rasterUnitsPerPixelX()
        msg = (
            f"{layer.width()} x {layer.height()} celdas, "
            f"{cell:.2f} m, {layer.crs().authid()}"
        )
        if layer.crs().isGeographic():
            msg += (
                "\nEl DEM esta en coordenadas geograficas. Reproyectelo a un "
                "SRC metrico antes de ejecutar."
            )
        elif cell < 15.0:
            msg += (
                "\nCelda muy fina. Si este raster proviene de remuestrear un "
                "DEM de 30 m, el detalle es del interpolador y no del terreno: "
                "para hidrologia conviene la resolucion nativa."
            )
        self.lbl_dem.setText(msg)
        self._refresh_estado()

    def _on_crs_changed(self, crs):
        layer = self.cmb_censo.currentLayer()
        extent = srs_mod.layer_extent_wgs84(layer) if layer else None
        validation = srs_mod.validate_working_crs(crs, extent)
        text = validation.mensaje()
        if validation.sugerencia is not None:
            text += f"\nSugerido: {srs_mod.describe(validation.sugerencia)}"
        self.lbl_crs.setText(text)
        self._update_threshold_hint()
        self._refresh_estado()

    def _update_threshold_hint(self):
        dem = self.cmb_dem.currentLayer()
        if dem is None or not dem.isValid():
            self.lbl_threshold.setText("")
            return
        area = abs(dem.rasterUnitsPerPixelX() * dem.rasterUnitsPerPixelY())
        if area <= 0:
            return
        cells = max(1, int(round(self.spn_threshold.value() * 10000.0 / area)))
        self.lbl_threshold.setText(
            f"Equivale a {cells} celdas con este DEM. El umbral se expresa en "
            "hectareas para que el valor calibrado siga siendo valido al "
            "cambiar de modelo de elevacion."
        )

    # ---------------------------------------------------------------- censo

    def _refresh_fields(self):
        layer = self.cmb_censo.currentLayer()
        names = [""] + (
            [f.name() for f in layer.fields()] if layer is not None else []
        )
        detected = (
            censo_mod.autodetect_fields(layer) if layer is not None else {}
        )
        for role, combo in self.field_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            suggestion = detected.get(role)
            if suggestion:
                combo.setCurrentText(suggestion)
            combo.blockSignals(False)

        if layer is not None and detected.get("dap"):
            mode = censo_mod.suggest_diameter_mode(detected["dap"], layer)
            index = self.cmb_diam.findData(mode)
            if index >= 0:
                self.cmb_diam.setCurrentIndex(index)

        self._refresh_categories()
        self._validate_censo()
        self._on_crs_changed(self.crs_widget.crs())

    def _refresh_categories(self):
        layer = self.cmb_censo.currentLayer()
        field_name = self.field_combos["categoria"].currentText()
        self.lst_harvest.setRowCount(0)
        if layer is None or not field_name:
            return
        index = layer.fields().indexOf(field_name)
        if index < 0:
            return
        values = sorted(
            str(v).strip()
            for v in layer.uniqueValues(index)
            if v is not None and str(v).strip()
        )
        for value in values[:30]:
            row = self.lst_harvest.rowCount()
            self.lst_harvest.insertRow(row)
            self.lst_harvest.setItem(row, 0, QTableWidgetItem(value))
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            marked = value.upper().startswith(("APROV", "COSE", "EXTR"))
            check.setCheckState(Qt.CheckState.Checked if marked else Qt.CheckState.Unchecked)
            self.lst_harvest.setItem(row, 1, check)

    def _harvestable_values(self) -> tuple:
        out = []
        for row in range(self.lst_harvest.rowCount()):
            check = self.lst_harvest.item(row, 1)
            if check is not None and check.checkState() == Qt.CheckState.Checked:
                out.append(self.lst_harvest.item(row, 0).text())
        return tuple(out)

    def _on_dmc_changed(self, _item):
        if not getattr(self, "_dmc_loading", False):
            self._validate_censo()

    def _fill_dmc_from_layer(self):
        """Puebla la tabla con las especies presentes en el censo.

        Se listan las grafias tal como vienen del censo para que el usuario las
        reconozca, pero la busqueda del DMC usa la forma normalizada: asi
        'Mashonaste' y 'mashonaste' comparten un unico valor.
        """
        layer = self.cmb_censo.currentLayer()
        campo = self.field_combos["especie"].currentText()
        if layer is None or not campo:
            QMessageBox.information(
                self, PLUGIN_NAME,
                "Seleccione la capa de censo y el campo de especie."
            )
            return

        index = layer.fields().indexOf(campo)
        if index < 0:
            return

        existentes = {}
        for row in range(self.tbl_dmc.rowCount()):
            nombre = self.tbl_dmc.item(row, 0)
            valor = self.tbl_dmc.item(row, 1)
            if nombre:
                existentes[censo_mod.normalize_species(nombre.text())] = (
                    valor.text() if valor else ""
                )

        vistas = {}
        for raw in layer.uniqueValues(index):
            texto = "" if raw is None else str(raw).strip()
            if not texto or texto.upper() in ("NULL", "NONE"):
                continue
            vistas.setdefault(censo_mod.normalize_species(texto), texto)

        self._dmc_loading = True
        self.tbl_dmc.setRowCount(0)
        for clave, mostrado in sorted(vistas.items(), key=lambda kv: kv[1]):
            row = self.tbl_dmc.rowCount()
            self.tbl_dmc.insertRow(row)
            self.tbl_dmc.setItem(row, 0, QTableWidgetItem(mostrado))
            self.tbl_dmc.setItem(row, 1, QTableWidgetItem(existentes.get(clave, "")))
            self.tbl_dmc.setItem(row, 2, QTableWidgetItem(""))
        self._dmc_loading = False
        self._validate_censo()

    def _load_dmc_peru(self):
        """Carga la tabla oficial peruana sobre las especies del censo."""
        from ..core.dmc_peru import (
            ADVERTENCIA, AVISO_CITES, CITES_APENDICE_II, DMC_PERU,
            DMC_PROPUESTO_REFERENCIA, FUENTE,
        )

        if not hasattr(self, "tbl_dmc"):
            return
        if self.tbl_dmc.rowCount() == 0:
            self._fill_dmc_from_layer()
        if self.tbl_dmc.rowCount() == 0:
            QMessageBox.information(
                self, PLUGIN_NAME,
                "Cargue primero la capa de censo y el campo de especie."
            )
            return

        self._dmc_loading = True
        halladas = 0
        faltantes = []
        cites = []
        for fila in range(self.tbl_dmc.rowCount()):
            nombre = self.tbl_dmc.item(fila, 0)
            if nombre is None:
                continue
            clave = censo_mod.normalize_species(nombre.text())
            valor = DMC_PERU.get(clave)
            nota = []
            if valor is not None:
                self.tbl_dmc.setItem(fila, 1, QTableWidgetItem(f"{valor:g}"))
                nota.append(f"normado {valor:g} cm (RJ 458-2002-INRENA)")
                halladas += 1
            else:
                faltantes.append(nombre.text())
                nota.append("sin DMC normado: complete con la norma vigente")

            propuesto = DMC_PROPUESTO_REFERENCIA.get(clave)
            if propuesto:
                nota.append(
                    f"concesiones certificadas aplican {propuesto:g} cm"
                )
            if clave in CITES_APENDICE_II:
                cites.append(nombre.text())
                nota.append(f"CITES Apendice II ({CITES_APENDICE_II[clave]})")
            self.tbl_dmc.setItem(fila, 2, QTableWidgetItem(" | ".join(nota)))
        self._dmc_loading = False
        self._validate_censo()

        mensaje = (
            f"Se asignaron {halladas} DMC de la tabla oficial.\n\n{FUENTE}"
        )
        if faltantes:
            mensaje += (
                f"\n\n{len(faltantes)} especies no figuran en la tabla y "
                "quedaron sin verificar. Complete su DMC con la norma vigente:\n"
                + ", ".join(faltantes[:12])
                + ("..." if len(faltantes) > 12 else "")
            )
        if cites:
            mensaje += (
                "\n\nESPECIES CITES APENDICE II en este censo: "
                + ", ".join(cites)
                + f"\n{AVISO_CITES}"
            )
        mensaje += f"\n\n{ADVERTENCIA}"
        QMessageBox.information(self, PLUGIN_NAME, mensaje)
        self._log(f"Tabla de DMC oficial aplicada: {halladas} especies.")

    def _import_dmc(self):
        """Importa una tabla especie;DMC desde CSV.

        Acepta coma, punto y coma o tabulador como separador, y salta una
        primera fila de encabezado si el segundo campo no es numerico.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Tabla de DMC", "", "CSV (*.csv *.txt);;Todos (*)"
        )
        if not path:
            return
        import csv

        filas = []
        try:
            with open(path, encoding="utf-8-sig", newline="") as handle:
                muestra = handle.read(2048)
                handle.seek(0)
                try:
                    dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
                except csv.Error:
                    dialecto = csv.excel
                for campos in csv.reader(handle, dialecto):
                    if len(campos) < 2:
                        continue
                    nombre = campos[0].strip()
                    crudo = campos[1].strip().replace(",", ".")
                    if not nombre:
                        continue
                    try:
                        valor = float(crudo)
                    except ValueError:
                        continue  # encabezado o fila invalida
                    filas.append((nombre, valor))
        except OSError as exc:
            QMessageBox.warning(self, PLUGIN_NAME, f"No se pudo leer: {exc}")
            return

        if not filas:
            QMessageBox.warning(
                self, PLUGIN_NAME,
                "No se encontraron filas validas. Se espera especie y DMC en "
                "centimetros, una por linea."
            )
            return

        self._dmc_loading = True
        self.tbl_dmc.setRowCount(0)
        for nombre, valor in filas:
            row = self.tbl_dmc.rowCount()
            self.tbl_dmc.insertRow(row)
            self.tbl_dmc.setItem(row, 0, QTableWidgetItem(nombre))
            self.tbl_dmc.setItem(row, 1, QTableWidgetItem(f"{valor:g}"))
        self._dmc_loading = False
        self._log(f"Tabla de DMC importada: {len(filas)} especies desde {path}")
        self._validate_censo()

    def _export_dmc(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar tabla de DMC", "dmc.csv", "CSV (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        import csv

        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                escritor = csv.writer(handle, delimiter=";")
                escritor.writerow(["especie", "dmc_cm"])
                for row in range(self.tbl_dmc.rowCount()):
                    nombre = self.tbl_dmc.item(row, 0)
                    valor = self.tbl_dmc.item(row, 1)
                    if nombre and nombre.text().strip():
                        escritor.writerow([
                            nombre.text().strip(),
                            valor.text().strip() if valor else "",
                        ])
        except OSError as exc:
            QMessageBox.warning(self, PLUGIN_NAME, f"No se pudo guardar: {exc}")
            return
        self._log(f"Tabla de DMC exportada a {path}")

    def _dmc_table(self) -> dict:
        """Tabla especie normalizada -> DMC en centimetros.

        El guardia protege del orden de construccion: los combos de la pestana
        de entradas pueden emitir su senal antes de que exista esta tabla, y
        sin el la ventana no llegaria a abrirse.
        """
        tabla = {}
        if not hasattr(self, "tbl_dmc"):
            return tabla
        for row in range(self.tbl_dmc.rowCount()):
            nombre = self.tbl_dmc.item(row, 0)
            valor = self.tbl_dmc.item(row, 1)
            if nombre is None or valor is None:
                continue
            texto = valor.text().strip().replace(",", ".")
            if not texto:
                continue
            try:
                dmc = float(texto)
            except ValueError:
                continue
            if dmc > 0:
                tabla[censo_mod.normalize_species(nombre.text())] = dmc
        return tabla

    def _build_censo_config(self):
        def get(role):
            return self.field_combos[role].currentText() or None

        return censo_mod.CensoConfig(
            field_id=get("id"),
            field_especie=get("especie"),
            field_categoria=get("categoria"),
            field_dap=get("dap"),
            field_altura=get("altura"),
            field_volumen=get("volumen"),
            field_parcela=get("parcela"),
            diameter_mode=self.cmb_diam.currentData(),
            form_factor=self.spn_ff.value(),
            harvestable_values=self._harvestable_values(),
            force_recompute=self.chk_recompute.isChecked(),
            dmc_por_especie=self._dmc_table(),
            dmc_por_defecto=(
                self.spn_dmc_def.value() if hasattr(self, "spn_dmc_def") else 0.0
            ),
        )

    def _validate_censo(self):
        if not hasattr(self, "txt_censo"):
            return
        layer = self.cmb_censo.currentLayer()
        if layer is None:
            self.txt_censo.setPlainText("")
            return
        if self.lst_harvest.rowCount() == 0:
            self._refresh_categories()
        config = self._build_censo_config()
        trees, report = censo_mod.load_trees(
            layer, config, self.chk_selected.isChecked()
        )
        lines = list(report.summary_lines())
        if report.advertencias:
            lines.append("")
            lines.append("Advertencias:")
            lines.extend(f"  - {w}" for w in report.advertencias)
        self.txt_censo.setPlainText("\n".join(lines))

    # ----------------------------------------------------------- tabla fajas

    def _reset_width_table(self):
        mode = self.cmb_order.currentData()
        table = (
            hidro_mod.DEFAULT_SHREVE_TABLE
            if mode == hidro_mod.ORDER_SHREVE
            else hidro_mod.DEFAULT_STRAHLER_TABLE
        )
        self.tbl_widths.setRowCount(0)
        for clase, ancho in sorted(table.items()):
            self._add_width_row(clase, ancho)

    def _add_width_row(self, clase: int, ancho: float):
        row = self.tbl_widths.rowCount()
        self.tbl_widths.insertRow(row)
        self.tbl_widths.setItem(row, 0, QTableWidgetItem(str(clase)))
        self.tbl_widths.setItem(row, 1, QTableWidgetItem(str(ancho)))

    def _remove_width_row(self):
        row = self.tbl_widths.currentRow()
        if row >= 0:
            self.tbl_widths.removeRow(row)

    def _width_table(self) -> dict:
        table = {}
        for row in range(self.tbl_widths.rowCount()):
            try:
                clase = int(self.tbl_widths.item(row, 0).text())
                ancho = float(self.tbl_widths.item(row, 1).text())
            except (AttributeError, ValueError):
                continue
            table[clase] = ancho
        return table

    # ------------------------------------------------------------- ejecucion

    def _pick_gpkg(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "GeoPackage de salida", "", "GeoPackage (*.gpkg)"
        )
        if path:
            if not path.lower().endswith(".gpkg"):
                path += ".gpkg"
            self.txt_gpkg.setText(path)

    def _log(self, text: str):
        self.log.appendPlainText(text)
        self.log.repaint()

    def _build_context(self) -> PlanningContext:
        ctx = PlanningContext(
            censo_layer=self.cmb_censo.currentLayer(),
            only_selected=self.chk_selected.isChecked(),
            pca_layer=self.cmb_pca.currentLayer(),
            dem_layer=self.cmb_dem.currentLayer(),
            crs=self.crs_widget.crs(),
            censo_config=self._build_censo_config(),
            output_gpkg=self.txt_gpkg.text().strip(),
            random_seed=self.spn_seed.value(),
        )
        ctx.hydro_config = hidro_mod.HydroConfig(
            threshold_ha=self.spn_threshold.value(),
            order_mode=self.cmb_order.currentData(),
            width_table=self._width_table(),
            width_above_table=self.spn_above.value(),
            min_segment_m=self.spn_min_seg.value(),
            smooth_iterations=self.spn_smooth.value(),
        )
        escenarios = []
        for chunk in self.txt_escenarios.text().replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit() and int(chunk) >= 1:
                escenarios.append(int(chunk))
        ctx.yards_config = patios_mod.YardsConfig(
            volumen_max_patio=self.spn_vol_max.value(),
            volumen_min_patio=self.spn_vol_min.value(),
            dist_max_arrastre=self.spn_dist_arr.value(),
            area_patio_m2=self.spn_area_patio.value(),
            pendiente_max_patio=self.spn_pend_patio.value(),
            dist_min_cauce=self.spn_dist_cauce.value(),
            pieza_grande_m3=self.spn_pieza.value(),
            reuso_pista=self.spn_reuso.value(),
            area_centro_m2=self.spn_area_centro.value(),
            pendiente_max_centro=self.spn_pend_centro.value(),
            escenarios_centros=tuple(escenarios) or (1, 2, 3),
            semilla=self.spn_seed.value(),
        )
        ctx.cost_config = costo_mod.CostConfig(
            peso_pendiente=self.spn_w_pend.value(),
            peso_humedad=self.spn_w_hum.value(),
            peso_restriccion=self.spn_w_restr.value(),
            peso_cruce=self.spn_w_cruce.value(),
            pendiente_max=self.spn_pend_via.value(),
            banda_restriccion_m=self.spn_banda.value(),
            factor_fuera_area=self.spn_fuera_area.value(),
            peso_lomas=(
                self.spn_w_lomas.value()
                if self.cmb_metodo.currentData() == "lomas" else 0.0
            ),
            umbral_lomas_ha=self.spn_umbral_lomas.value(),
            banda_lomas_m=self.spn_banda_lomas.value(),
        )
        ctx.roads_config = caminos_mod.RoadsConfig(
            ancho_principal_m=self.spn_ancho_pri.value(),
            ancho_secundaria_m=self.spn_ancho_sec.value(),
            conectar_patios=self.chk_patios_via.isChecked(),
            dist_patio_a_via_m=self.spn_dist_patio_via.value(),
            costo_via_existente=self.spn_factor_exist.value(),
            max_destinos=self.spn_max_dest.value(),
            simplificar_m=self.spn_simplificar.value(),
            suavizado=self.spn_suavizado.value(),
            grade_config=GradeConfig(
                rasante_favorable_pct=self.spn_rasante_fav.value(),
                rasante_adversa_pct=self.spn_rasante_adv.value(),
                tramo_excepcional_m=self.spn_tramo_exc.value(),
                orden_vecindario=self.spn_vecindario.value(),
            ),
        )
        ctx.intensity_config = intens_mod.IntensityConfig(
            max_volumen_ha=self.spn_max_vol_ha.value(),
            max_arboles_ha=self.spn_max_arb_ha.value(),
            max_area_basal_pct=self.spn_max_ab.value(),
            min_abundancia_ha=self.spn_min_abund.value(),
            es_censo_comercial=self.chk_censo_comercial.isChecked(),
        )
        ctx.impact_config = impacto_mod.ImpactConfig(
            n_campamentos=self.spn_n_camp.value(),
            area_campamento_m2=self.spn_area_camp.value(),
            reuso_pista=self.spn_reuso.value(),
        )
        ctx.transport_config = transp_mod.TransportConfig(
            rendimiento_general=self.spn_rendimiento.value(),
            capacidad_volumen_m3=self.spn_cap_vol.value(),
            capacidad_peso_t=self.spn_cap_peso.value(),
            densidad_verde_t_m3=self.spn_densidad.value(),
            factor_estiba=self.spn_estiba.value(),
        )
        ctx.preset = self.cmb_preset.currentData()
        return ctx

    def run_chain(self):
        ctx = self._build_context()
        ctx.start()

        missing = ctx.validate()
        if missing:
            QMessageBox.warning(
                self,
                PLUGIN_NAME,
                "Faltan entradas para ejecutar:\n\n"
                + "\n".join(f"- {m}" for m in missing),
            )
            return

        self.log.clear()
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.btn_run.setEnabled(False)

        try:
            self._prepare(ctx)
            if ctx.has_blocking_errors():
                self._log("Se detuvo por errores en la preparacion.")
                return
            if self.module_checks[M1_HIDROLOGIA].isChecked():
                self._run_m1(ctx)
            if self.module_checks[M3_PATIOS].isChecked():
                self._run_m3(ctx)
            if self.module_checks[M4_COSTO].isChecked():
                self._run_m4(ctx)
            if self.module_checks[M5_CAMINOS].isChecked():
                self._run_m5(ctx)
            if self.module_checks[M6_ARRASTRE].isChecked():
                self._run_m6(ctx)
            if self.module_checks[M7_INTENSIDAD].isChecked():
                self._run_m7(ctx)
            if self.module_checks[M8_TRANSPORTE].isChecked():
                self._run_m8(ctx)
        finally:
            self.progress.setVisible(False)
            self.btn_run.setEnabled(True)
            self._ctx = ctx
            self._update_doc_buttons()

    def _prepare(self, ctx: PlanningContext):
        self._log(f"SRC de trabajo: {srs_mod.describe(ctx.crs)}")

        ctx.register_source(ctx.censo_layer, "censo")
        ctx.register_source(ctx.dem_layer, "dem")
        ctx.register_source(ctx.pca_layer, "pca")

        pca = srs_mod.reproject_vector(ctx.pca_layer, ctx.crs, layer_name="pca")
        if pca is not None:
            geoms = [f.geometry() for f in pca.getFeatures() if f.hasGeometry()]
            if geoms:
                ctx.pca_geom = QgsGeometry.unaryUnion(geoms)
                ctx.pca_area_ha = ctx.pca_geom.area() / 10000.0
                self._log(f"PCA: {ctx.pca_area_ha:,.2f} ha")

        if ctx.dem_layer.crs().authid() != ctx.crs.authid():
            self._log(
                f"Reproyectando el DEM de {ctx.dem_layer.crs().authid()} "
                f"a {ctx.crs.authid()} (bilineal, resolucion nativa)..."
            )
            reprojected = srs_mod.reproject_raster(ctx.dem_layer, ctx.crs)
            if reprojected is None:
                ctx.errores.append("No fue posible reproyectar el DEM.")
                return
            ctx.dem_layer = reprojected
            self._log(
                f"  DEM: {reprojected.width()} x {reprojected.height()} celdas, "
                f"{reprojected.rasterUnitsPerPixelX():.2f} m"
            )
            self._update_threshold_hint()

        censo_layer = srs_mod.reproject_vector(
            ctx.censo_layer, ctx.crs, ctx.only_selected, "censo"
        )
        trees, report = censo_mod.load_trees(censo_layer, ctx.censo_config)
        ctx.trees = trees
        ctx.censo_report = report

        for line in report.summary_lines():
            self._log(f"  {line}")
        for warn in report.advertencias:
            self._log(f"  ! {warn}")
        if report.is_blocking():
            ctx.errores.append("El censo no permite continuar.")
            return

        # Capa de censo evaluado: muestra en el mapa que individuo puede
        # talarse y cual no, sin alterar el censo original.
        evaluada = censo_mod.build_evaluated_layer(ctx.trees, ctx.crs)
        if evaluada is not None:
            ctx.censo_evaluado = evaluada
            if self.chk_load.isChecked():
                estilos_mod.apply("censo_evaluado", evaluada)
                QgsProject.instance().addMapLayer(evaluada)
                self._log("  Capa de censo evaluado agregada.")

    def _run_m1(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M1 - {MODULE_LABELS[M1_HIDROLOGIA]}")
        started = time.time()

        result = hidro_mod.run(
            ctx.dem_layer, ctx.hydro_config, ctx.crs, clip_geom=ctx.pca_geom
        )

        module_result = ModuleResult(
            module_id=M1_HIDROLOGIA,
            ok=result.ok,
            errores=list(result.errores),
            advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "umbral_ha": ctx.hydro_config.threshold_ha,
                "umbral_celdas": result.umbral_celdas,
                "criterio": ctx.hydro_config.order_mode,
                "tabla_anchos": ctx.hydro_config.table(),
            },
            metrics={
                "tramos": result.n_tramos,
                "longitud_km": round(result.longitud_total_m / 1000.0, 3),
                "strahler_max": result.strahler_max,
                "shreve_max": result.shreve_max,
                "confluencias": result.n_confluencias,
                "componentes": result.n_componentes,
                "area_fajas_ha": round(result.area_fajas_ha, 4),
            },
        )
        if result.red_hidrica is not None:
            module_result.layers["red_hidrica"] = result.red_hidrica
        if result.fajas is not None:
            module_result.layers["fajas_marginales"] = result.fajas
        ctx.store(module_result)

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return

        for line in result.diagnostico_lines():
            self._log(f"  {line}")
        for warn in result.advertencias:
            self._log(f"  ! {warn}")
        self._log(f"  Fajas: {result.area_fajas_ha:,.2f} ha")

        self._cross_censo_fajas(ctx, result)

        if self.chk_load.isChecked():
            self._load_layers(module_result)
            self._log("  Capas agregadas al proyecto.")

    def _run_m3(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M3 - {MODULE_LABELS[M3_PATIOS]}")
        started = time.time()

        feasibility, barriers = self._build_feasibility(ctx)

        preferido = self.spn_centro_elegido.value() or None
        result = patios_mod.run(
            ctx.trees,
            ctx.yards_config,
            ctx.crs,
            feasibility=feasibility,
            barriers=barriers,
            escenario_preferido=preferido,
        )

        module_result = ModuleResult(
            module_id=M3_PATIOS,
            ok=result.ok,
            errores=list(result.errores),
            advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "volumen_max_patio": ctx.yards_config.volumen_max_patio,
                "volumen_min_patio": ctx.yards_config.volumen_min_patio,
                "dist_max_arrastre": ctx.yards_config.dist_max_arrastre,
                "escenarios": list(ctx.yards_config.escenarios_centros),
                "escenario_aplicado": result.escenario_elegido,
            },
            metrics={
                "n_patios": result.n_patios,
                "volumen_asignado_m3": round(result.volumen_asignado, 3),
                "volumen_sin_asignar_m3": round(result.volumen_no_asignado, 3),
                "arrastre_medio_m": round(result.dist_media_arrastre, 1),
                "arrastre_max_m": round(result.dist_max_arrastre, 1),
                "piezas_grandes": result.n_piezas_grandes,
                "area_patios_ha": round(result.area_patios_ha, 4),
                "area_pistas_ha": round(result.area_pistas_ha, 4),
                "area_ramales_ha": round(result.area_ramales_ha, 4),
                "impacto_total_ha": round(result.area_impacto_total_ha, 4),
                "arrastre_acumulado_km": round(result.long_arrastre_total_m / 1000, 2),
                "volumen_medio_patio_m3": round(result.volumen_medio_patio, 2),
                "patios_fusionados": result.patios_fusionados,
                "convergio": result.convergio,
            },
        )
        if result.patios is not None:
            module_result.layers["patios_acopio"] = result.patios
        if result.centros is not None:
            module_result.layers["centros_acopio"] = result.centros
        if result.arrastre is not None:
            module_result.layers["arrastre"] = result.arrastre
        ctx.store(module_result)

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return

        for line in result.resumen_lines():
            self._log(f"  {line}")
        self._log(
            f"  Sembrados {result.patios_sembrados}, fusionados "
            f"{result.patios_fusionados}, convergio: {result.convergio}"
        )

        ctx.escenarios_centros = result.escenarios
        ctx.yards = result.yards
        ctx.centers = result.centers
        if result.escenarios:
            self._log("")
            self._log("  Comparacion de escenarios de centros:")
            for line in patios_mod.scenario_table(result.escenarios):
                self._log(f"    {line}")
            self._log(f"  Escenario aplicado: {result.escenario_elegido} centro(s)")

        for warn in result.advertencias:
            self._log(f"  ! {warn}")

        if self.chk_load.isChecked():
            self._load_layers(module_result)

    def _load_layers(self, module_result):
        """Carga las capas con estilo aplicado.

        El orden importa: el arrastre va primero para quedar por debajo de los
        patios, de modo que las flechas no tapen los simbolos de destino.
        """
        project = QgsProject.instance()
        orden = ("arrastre", "fajas_marginales", "red_hidrica", "vias",
                 "patios_acopio", "centros_acopio", "cruces")
        claves = sorted(
            module_result.layers,
            key=lambda k: orden.index(k) if k in orden else 99,
        )
        for key in claves:
            layer = module_result.layers[key]
            estilos_mod.apply(key, layer)
            project.addMapLayer(layer)

    def _run_m4(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M4 - {MODULE_LABELS[M4_COSTO]}")
        started = time.time()

        exclusiones = []
        red = None
        m1 = ctx.result(M1_HIDROLOGIA)
        if m1 is not None:
            fajas = m1.layers.get("fajas_marginales")
            if fajas is not None:
                exclusiones = [
                    f.geometry() for f in fajas.getFeatures() if f.hasGeometry()
                ]
            red = m1.layers.get("red_hidrica")

        # Las fajas marginales entran como IMPEDIMENTO y no como veto: se
        # pueden cruzar con obra de arte, pero no recorrer en paralelo. Como
        # veto, el propio drenaje fragmentaria el area y ningun destino al otro
        # lado de una quebrada seria conectable.
        result = costo_mod.run(
            ctx.dem_layer, ctx.cost_config, ctx.crs,
            red_hidrica=red, exclusiones=exclusiones, vetos=None,
            clip_geom=ctx.pca_geom,
        )

        module_result = ModuleResult(
            module_id=M4_COSTO, ok=result.ok,
            errores=list(result.errores), advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "peso_pendiente": ctx.cost_config.peso_pendiente,
                "peso_humedad": ctx.cost_config.peso_humedad,
                "peso_restriccion": ctx.cost_config.peso_restriccion,
                "peso_cruce": ctx.cost_config.peso_cruce,
                "pendiente_max_pct": ctx.cost_config.pendiente_max,
                "banda_incertidumbre_m": ctx.cost_config.banda_restriccion_m,
            },
            metrics={
                "celdas": result.celdas,
                "celdas_impedidas": result.celdas_impedidas,
                "celdas_vetadas": result.celdas_vetadas,
                "pendiente_media_pct": round(result.pendiente_media, 2),
                "pct_sobre_limite": round(result.pct_sobre_limite, 2),
                "costo_medio": round(result.costo_medio, 2),
            },
        )
        if result.capa is not None:
            module_result.rasters["superficie_costo"] = result.costo_path
        ctx.store(module_result)
        ctx.costo_path = result.costo_path

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return
        for line in result.resumen_lines():
            self._log(f"  {line}")
        for warn in result.advertencias:
            self._log(f"  ! {warn}")

        if self.chk_load.isChecked() and result.capa is not None:
            estilos_ras.apply("superficie_costo", result.capa)
            QgsProject.instance().addMapLayer(result.capa)

    def _run_m5(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M5 - {MODULE_LABELS[M5_CAMINOS]}")
        started = time.time()

        if not getattr(ctx, "costo_path", ""):
            self._log("  ERROR: falta la superficie de costo. Ejecute M4.")
            ctx.store(ModuleResult(
                module_id=M5_CAMINOS, ok=False,
                errores=["Falta la superficie de costo del modulo M4."]))
            return

        salida = self._punto_salida(ctx)
        existente = self._vias_existentes(ctx)
        if salida is None and existente is not None:
            self._log(
                "  Sin punto de salida: la red convergira hacia la via "
                "existente declarada."
            )
            salida = ()
        elif salida is None:
            mensaje = (
                "Falta el punto de salida. La red tiene que converger hacia "
                "algun lado -el empalme con una via existente, un puerto o el "
                "campamento- y esa es una decision de acceso real que el "
                "modelo no puede deducir del terreno.\n\n"
                "Definalo en la pestana Vias: marcandolo en el mapa, "
                "escribiendo sus coordenadas o tomandolo de una capa de puntos."
            )
            self._log("  ERROR: " + mensaje.replace("\n\n", " "))
            QMessageBox.warning(self, PLUGIN_NAME, mensaje)
            ctx.store(ModuleResult(
                module_id=M5_CAMINOS, ok=False,
                errores=["Falta el punto de salida de la red."]))
            return

        # Un punto de salida muy lejos del area suele ser un error de unidades
        # o de sistema de referencia, no una decision.
        if ctx.pca_geom is not None and not ctx.pca_geom.isEmpty():
            from qgis.core import QgsGeometry as _G, QgsPointXY as _P

            dist = ctx.pca_geom.distance(_G.fromPointXY(_P(*salida)))
            if dist > 20000:
                self._log(
                    f"  ! El punto de salida esta a {dist / 1000:.1f} km del "
                    "area de trabajo. Verifique las coordenadas y el sistema "
                    "de referencia."
                )

        centros = []
        patios = []
        m3 = ctx.result(M3_PATIOS)
        if m3 is not None:
            centros = list(getattr(ctx, "centers", []) or [])
            patios = list(getattr(ctx, "yards", []) or [])
        if not centros and not patios:
            self._log("  ERROR: no hay centros ni patios. Ejecute M3.")
            ctx.store(ModuleResult(
                module_id=M5_CAMINOS, ok=False,
                errores=["No hay destinos que conectar."]))
            return

        red = None
        m1 = ctx.result(M1_HIDROLOGIA)
        if m1 is not None:
            red = m1.layers.get("red_hidrica")

        total = len(centros) + (len(patios) if ctx.roads_config.conectar_patios else 0)
        if ctx.roads_config.max_destinos:
            total = min(total, ctx.roads_config.max_destinos)
        self._log(f"  Trazando {total} destinos, dos llamadas a GRASS cada uno...")

        result = caminos_mod.run(
            ctx.costo_path, salida, centros, patios,
            ctx.roads_config, ctx.crs, red_hidrica=red,
            dem_layer=ctx.dem_layer,
            area_geom=ctx.pca_geom,
            vias_existentes=self._vias_existentes(ctx),
        )

        module_result = ModuleResult(
            module_id=M5_CAMINOS, ok=result.ok,
            errores=list(result.errores), advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "ancho_principal_m": ctx.roads_config.ancho_principal_m,
                "ancho_secundaria_m": ctx.roads_config.ancho_secundaria_m,
                "conectar_patios": ctx.roads_config.conectar_patios,
                "punto_salida": f"{salida[0]:.1f}, {salida[1]:.1f}",
                "rasante_favorable_pct": self.spn_rasante_fav.value(),
                "rasante_adversa_pct": self.spn_rasante_adv.value(),
            },
            metrics={
                "tramos": result.n_tramos,
                "long_principal_km": round(result.long_principal_m / 1000, 3),
                "long_secundaria_km": round(result.long_secundaria_m / 1000, 3),
                "long_total_km": round(result.long_total_m / 1000, 3),
                "area_vias_ha": round(result.area_vias_ha, 4),
                "obras_de_cruce": result.n_cruces,
                "rasante_max_pct": round(result.rasante_max_pct, 2),
                "destinos_conectados": result.conectados,
                "volumen_atrapado_m3": round(result.volumen_atrapado, 2),
                "long_fuera_area_km": round(result.long_fuera_area_m / 1000, 3),
                "long_existente_km": round(result.long_existente_m / 1000, 3),
                "apertura_nueva_km": round(result.long_total_m / 1000, 3),
                "densidad_m_ha": round(result.densidad_m_ha, 2),
            },
        )
        if result.vias is not None:
            module_result.layers["vias"] = result.vias
        if result.cruces is not None:
            module_result.layers["cruces"] = result.cruces
        ctx.store(module_result)

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return
        for line in result.resumen_lines():
            self._log(f"  {line}")
        for warn in result.advertencias:
            self._log(f"  ! {warn}")

        if self.chk_load.isChecked():
            self._load_layers(module_result)

    def _run_m6(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M6 - {MODULE_LABELS[M6_ARRASTRE]}")
        started = time.time()

        result = impacto_mod.run(ctx, ctx.impact_config)
        module_result = ModuleResult(
            module_id=M6_ARRASTRE, ok=result.ok,
            errores=list(result.errores), advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "n_campamentos": ctx.impact_config.n_campamentos,
                "area_campamento_m2": ctx.impact_config.area_campamento_m2,
                "reuso_pista": ctx.impact_config.reuso_pista,
            },
            metrics={
                "area_impacto_ha": round(result.area_total_ha, 4),
                "pct_area_trabajo": round(result.pct_area_trabajo, 3),
                "volumen_censado_m3": round(result.volumen_censado, 2),
                "volumen_asignado_m3": round(result.volumen_asignado, 2),
                "volumen_atrapado_m3": round(result.volumen_atrapado, 2),
            },
        )
        ctx.store(module_result)
        ctx.impacto = result

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return
        for line in result.resumen_lines():
            self._log(f"  {line}")
        for warn in result.advertencias:
            self._log(f"  ! {warn}")

    def _run_m7(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M7 - {MODULE_LABELS[M7_INTENSIDAD]}")
        started = time.time()

        if not ctx.trees:
            self._log("  ERROR: no hay individuos del censo.")
            return

        base = ""
        if self.txt_gpkg.text().strip():
            base = os.path.splitext(self.txt_gpkg.text().strip())[0]
        else:
            base = os.path.splitext(self._temp_path(""))[0]

        wkt = ctx.crs.toWkt() if ctx.crs else ""
        result = intens_mod.run(
            ctx.trees, ctx.intensity_config,
            area_ha=ctx.pca_area_ha, crs_wkt=wkt, salida_base=base,
        )

        module_result = ModuleResult(
            module_id=M7_INTENSIDAD, ok=result.ok,
            errores=list(result.errores), advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "preset": getattr(ctx, "preset", ""),
                "max_volumen_ha": ctx.intensity_config.max_volumen_ha,
                "max_arboles_ha": ctx.intensity_config.max_arboles_ha,
                "max_area_basal_pct": ctx.intensity_config.max_area_basal_pct,
                "min_abundancia_ha": ctx.intensity_config.min_abundancia_ha,
            },
            metrics={
                "celdas_con_arboles": result.celdas_con_arboles,
                "area_efectiva_ha": round(result.area_efectiva_ha, 2),
                "densidad_media_arb_ha": round(result.densidad_media, 3),
                "densidad_max_arb_ha": round(result.densidad_max, 1),
                "intensidad_media_m3_ha": round(result.intensidad_media, 3),
                "intensidad_max_m3_ha": round(result.intensidad_max, 3),
                "celdas_sobre_volumen": result.celdas_sobre_volumen,
                "celdas_sobre_arboles": result.celdas_sobre_arboles,
                "volumen_en_exceso_m3": round(result.volumen_en_exceso, 2),
            },
        )
        for clave, ruta in (
            ("densidad", result.densidad_path),
            ("intensidad", result.intensidad_path),
            ("excedencia", result.excedencia_path),
        ):
            if ruta:
                module_result.rasters[clave] = ruta
        ctx.store(module_result)
        ctx.intensidad = result

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return
        for line in result.resumen_lines():
            self._log(f"  {line}")
        for warn in result.advertencias:
            self._log(f"  ! {warn}")

        if self.chk_load.isChecked():
            from qgis.core import QgsRasterLayer

            for clave, ruta in module_result.rasters.items():
                capa = QgsRasterLayer(ruta, f"Malla {clave}")
                if not capa.isValid():
                    continue
                # La intensidad se clasifica contra el limite normativo cuando
                # existe: asi el mapa muestra directamente que hectareas se
                # exceden, en lugar de una rampa sin referencia.
                if clave == "intensidad":
                    estilos_ras.apply(
                        clave, capa,
                        limite=ctx.intensity_config.max_volumen_ha,
                    )
                else:
                    estilos_ras.apply(clave, capa)
                QgsProject.instance().addMapLayer(capa)

    def _run_m8(self, ctx: PlanningContext):
        self._log("")
        self._log(f"M8 - {MODULE_LABELS[M8_TRANSPORTE]}")
        started = time.time()

        if not ctx.trees:
            self._log("  ERROR: no hay individuos del censo.")
            return

        result = transp_mod.run(
            ctx.trees, ctx.transport_config,
            centros=list(getattr(ctx, "centers", []) or []),
        )
        ctx.transporte = result

        module_result = ModuleResult(
            module_id=M8_TRANSPORTE, ok=result.ok,
            errores=list(result.errores), advertencias=list(result.advertencias),
            duracion_s=time.time() - started,
            params={
                "rendimiento": ctx.transport_config.rendimiento_general,
                "capacidad_m3": ctx.transport_config.capacidad_volumen_m3,
                "capacidad_t": ctx.transport_config.capacidad_peso_t,
                "densidad_t_m3": ctx.transport_config.densidad_verde_t_m3,
                "factor_estiba": ctx.transport_config.factor_estiba,
            },
            metrics={
                "volumen_censo_m3": round(result.volumen_censo, 2),
                "volumen_rollizo_m3": round(result.volumen_rollizo, 2),
                "merma_pct": round(result.merma_pct, 2),
                "peso_total_t": round(result.peso_total_t, 1),
                "viajes": result.viajes_total,
                "limite_dominante": result.limite_dominante,
            },
        )
        ctx.store(module_result)

        for err in result.errores:
            self._log(f"  ERROR: {err}")
        if not result.ok:
            return
        for line in result.resumen_lines():
            self._log(f"  {line}")
        for warn in result.advertencias:
            self._log(f"  ! {warn}")

    # ------------------------------------------------------ punto de salida

    def _on_modo_salida(self):
        """Habilita solo los controles del modo elegido."""
        if not hasattr(self, "cmb_salida"):
            return
        modo = self.cmb_modo_salida.currentData()
        usa_xy = modo in ("mapa", "coordenadas")
        self.spn_salida_x.setEnabled(usa_xy)
        self.spn_salida_y.setEnabled(usa_xy)
        self.btn_pick.setEnabled(modo == "mapa")
        self.cmb_salida.setEnabled(modo == "capa")
        if modo != "mapa" and self.btn_pick.isChecked():
            self.btn_pick.setChecked(False)
            self._toggle_pick(False)
        self._refresh_estado()

    def _toggle_pick(self, activo: bool):
        """Activa la captura de un punto por clic en el lienzo.

        Se usa QgsMapToolEmitPoint sobre el lienzo principal. El dialogo se
        oculta mientras dura la captura: de otro modo tapa justamente la zona
        donde el usuario necesita hacer clic.
        """
        canvas = self.iface.mapCanvas()
        if not activo:
            if getattr(self, "_pick_tool", None) is not None:
                canvas.unsetMapTool(self._pick_tool)
                self._pick_tool = None
            if getattr(self, "_tool_previa", None) is not None:
                canvas.setMapTool(self._tool_previa)
                self._tool_previa = None
            return

        self._tool_previa = canvas.mapTool()
        self._pick_tool = QgsMapToolEmitPoint(canvas)
        self._pick_tool.canvasClicked.connect(self._on_click_mapa)
        canvas.setMapTool(self._pick_tool)
        self.hide()
        self.iface.messageBar().pushInfo(
            PLUGIN_NAME, "Haga clic en el mapa para fijar el punto de salida."
        )

    def _on_click_mapa(self, punto, boton):
        """Recibe el clic, lo reproyecta al SRC de trabajo y lo guarda."""
        destino = self.crs_widget.crs()
        origen = self.iface.mapCanvas().mapSettings().destinationCrs()
        xy = punto
        if (
            destino is not None and destino.isValid()
            and origen.isValid() and origen.authid() != destino.authid()
        ):
            from qgis.core import QgsCoordinateTransform, QgsProject as _P

            tr = QgsCoordinateTransform(origen, destino, _P.instance())
            xy = tr.transform(punto)

        self.spn_salida_x.setValue(xy.x())
        self.spn_salida_y.setValue(xy.y())

        self.btn_pick.setChecked(False)
        self._toggle_pick(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self._log(f"Punto de salida fijado en {xy.x():.1f}, {xy.y():.1f}")
        self._refresh_estado()

    def _vias_existentes(self, ctx):
        """Capa de vias existentes, reproyectada al SRC de trabajo."""
        if not hasattr(self, "cmb_vias_exist"):
            return None
        capa = self.cmb_vias_exist.currentLayer()
        if capa is None or not capa.isValid() or not capa.featureCount():
            return None
        return srs_mod.reproject_vector(capa, ctx.crs, layer_name="vias_exist")

    def _salida_definida(self):
        """Coordenadas del punto de salida segun el modo activo, o None."""
        if not hasattr(self, "cmb_modo_salida"):
            return None
        modo = self.cmb_modo_salida.currentData()
        if modo == "capa":
            capa = self.cmb_salida.currentLayer()
            if capa is None or not capa.isValid() or not capa.featureCount():
                return None
            return ("capa", capa)
        x = self.spn_salida_x.value()
        y = self.spn_salida_y.value()
        if abs(x) < 1e-6 and abs(y) < 1e-6:
            return None
        return ("xy", (x, y))

    def _punto_salida(self, ctx: PlanningContext):
        """Coordenadas del punto de salida en el SRC de trabajo, o None.

        Las coordenadas escritas o marcadas ya estan en el SRC de trabajo; las
        que vienen de una capa se reproyectan.
        """
        definido = self._salida_definida()
        if definido is None:
            return None
        modo, valor = definido
        if modo == "xy":
            return valor

        reproyectada = srs_mod.reproject_vector(valor, ctx.crs, layer_name="salida")
        if reproyectada is None:
            return None
        for feat in reproyectada.getFeatures():
            if feat.hasGeometry():
                punto = feat.geometry().centroid().asPoint()
                return (punto.x(), punto.y())
        return None

    def _build_feasibility(self, ctx: PlanningContext):
        """Arma la mascara de factibilidad con lo que M1 haya producido."""
        exclusions = []
        streams = []
        barriers = []

        m1 = ctx.result(M1_HIDROLOGIA)
        if m1 is not None:
            fajas = m1.layers.get("fajas_marginales")
            if fajas is not None:
                exclusions = [
                    f.geometry() for f in fajas.getFeatures() if f.hasGeometry()
                ]
            red = m1.layers.get("red_hidrica")
            if red is not None:
                orden_barrera = self.spn_orden_barrera.value()
                for feat in red.getFeatures():
                    if not feat.hasGeometry():
                        continue
                    streams.append(feat.geometry())
                    if orden_barrera and feat["strahler"] >= orden_barrera:
                        barriers.append(feat.geometry())

        slope = self._slope_layer(ctx)
        mask = patios_mod.FeasibilityMask(
            area_geom=ctx.pca_geom,
            exclusions=exclusions,
            streams=streams,
            slope_layer=slope,
        )
        self._log(
            f"  Factibilidad: {len(exclusions)} exclusiones, "
            f"{len(streams)} cauces, {len(barriers)} barreras, "
            f"pendiente: {'si' if slope else 'no disponible'}"
        )
        return mask, barriers

    def _slope_layer(self, ctx: PlanningContext):
        """Calcula la pendiente en porcentaje a partir del DEM."""
        try:
            import processing
            from qgis.core import QgsProcessing, QgsRasterLayer

            out = processing.run(
                "gdal:slope",
                {
                    "INPUT": ctx.dem_layer,
                    "BAND": 1,
                    "SCALE": 1.0,
                    "AS_PERCENT": True,
                    "COMPUTE_EDGES": True,
                    "ZEVENBERGEN": False,
                    "OPTIONS": "",
                    "EXTRA": "",
                    "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
                },
            )
            layer = QgsRasterLayer(out["OUTPUT"], "pendiente")
            return layer if layer.isValid() else None
        except Exception as exc:  # noqa: BLE001
            self._log(f"  ! No se pudo calcular la pendiente: {exc}")
            return None

    def _cross_censo_fajas(self, ctx: PlanningContext, result):
        """Volumen censado que cae dentro de faja marginal.

        Es la diferencia entre volumen censado y volumen realmente
        aprovechable, y es de lo primero que revisa un evaluador.
        """
        from qgis.core import QgsFeature, QgsPointXY, QgsSpatialIndex

        if result.fajas is None or not ctx.trees:
            return

        index = QgsSpatialIndex()
        lookup = {}
        for i, feat in enumerate(result.fajas.getFeatures()):
            temp = QgsFeature(i)
            temp.setGeometry(feat.geometry())
            index.addFeature(temp)
            lookup[i] = (feat["ancho_m"], feat.geometry())

        total_n = total_v = 0
        dentro_n = 0
        dentro_v = 0.0
        for tree in ctx.trees:
            if not tree.aprovechable:
                continue
            total_n += 1
            total_v += tree.volumen_m3
            point = QgsGeometry.fromPointXY(QgsPointXY(tree.x, tree.y))
            for cand in index.intersects(point.boundingBox()):
                _, geom = lookup[cand]
                if geom.contains(point):
                    dentro_n += 1
                    dentro_v += tree.volumen_m3
                    break

        if not total_n:
            return
        self._log("")
        self._log("  Individuos dentro de faja marginal:")
        self._log(f"    censado aprovechable : {total_n:5d} | {total_v:11,.2f} m3")
        self._log(f"    dentro de faja       : {dentro_n:5d} | {dentro_v:11,.2f} m3")
        self._log(
            f"    APROVECHABLE NETO    : {total_n - dentro_n:5d} | "
            f"{total_v - dentro_v:11,.2f} m3"
        )
        self._log(f"    exclusion: {100 * dentro_v / total_v:.2f}% del volumen")

        res = ctx.result(M1_HIDROLOGIA)
        if res is not None:
            res.metrics["arboles_en_faja"] = dentro_n
            res.metrics["volumen_en_faja_m3"] = round(dentro_v, 3)
            res.metrics["volumen_neto_m3"] = round(total_v - dentro_v, 3)
