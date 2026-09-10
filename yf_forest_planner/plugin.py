"""Registro del complemento en QGIS: menu, barra de herramientas y acciones.

Esta clase no contiene logica de analisis. Solo conecta la interfaz con los
modulos y verifica que el entorno tenga lo que la cadena necesita.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from .core.constants import MENU_TITLE, PLUGIN_NAME, PLUGIN_VERSION
from .core.i18n import desinstalar as _quitar_traduccion
from .core.i18n import instalar as _instalar_traduccion
from .core.i18n import tr

PLUGIN_DIR = os.path.dirname(__file__)


def _icon(name: str) -> QIcon:
    """Icono de resources/icons, probando SVG antes que PNG.

    El SVG escala sin perdida en pantallas de alta densidad, que es donde un
    PNG de 24 pixeles se ve borroso.
    """
    base = os.path.join(PLUGIN_DIR, "resources", "icons")
    raiz = os.path.splitext(name)[0]

    # Si hay variantes por tamano, se registran todas en el mismo QIcon: Qt
    # elige la que corresponda a la densidad de pantalla en lugar de reescalar
    # una sola, que a 24 pixeles se ve sucia.
    icono = QIcon()
    encontrado = False
    for lado in (16, 24, 32, 48, 64, 128):
        ruta = os.path.join(base, f"{raiz}_{lado}.png")
        if os.path.exists(ruta):
            icono.addFile(ruta, QSize(lado, lado))
            encontrado = True
    if encontrado:
        return icono

    for ext in (".svg", ".png"):
        ruta = os.path.join(base, raiz + ext)
        if os.path.exists(ruta):
            return QIcon(ruta)
    return QIcon()


class ForestPlannerPlugin:
    """Complemento principal."""

    def __init__(self, iface):
        self.iface = iface
        # La traduccion se instala antes de construir la interfaz: si se
        # cargara despues, los textos ya estarian fijados en espanol.
        _instalar_traduccion(PLUGIN_DIR)
        self.actions: list[QAction] = []
        self.toolbar = None
        self._dialog = None

    # ------------------------------------------------------------ ciclo QGIS

    def initGui(self):  # noqa: N802 - API de QGIS
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("YFForestPlannerToolbar")

        self._add_action(
            "forest_planner",
            tr("Planificar manejo forestal"),
            self.run_planner,
            tr("Abre el asistente de planificacion"),
        )
        self._add_action(
            "diagnostics.svg",
            tr("Diagnostico del entorno"),
            self.run_diagnostics,
            tr("Verifica que el entorno tenga lo necesario"),
            add_to_toolbar=False,
        )
        self._add_action(
            "about.svg",
            tr("Acerca de"),
            self.run_about,
            add_to_toolbar=False,
        )

    def unload(self):
        _quitar_traduccion()
        for action in self.actions:
            self.iface.removePluginMenu(MENU_TITLE, action)
            self.iface.removeToolBarIcon(action)
        self.actions.clear()
        if self.toolbar is not None:
            del self.toolbar
            self.toolbar = None
        if self._dialog is not None:
            self._dialog.deleteLater()
            self._dialog = None

    def _add_action(
        self,
        icon_name: str,
        text: str,
        callback,
        status_tip: str = "",
        add_to_toolbar: bool = True,
    ) -> QAction:
        action = QAction(_icon(icon_name), text, self.iface.mainWindow())
        action.triggered.connect(callback)
        if status_tip:
            action.setStatusTip(status_tip)
        if add_to_toolbar and self.toolbar is not None:
            self.toolbar.addAction(action)
        self.iface.addPluginToMenu(MENU_TITLE, action)
        self.actions.append(action)
        return action

    # -------------------------------------------------------------- acciones

    def run_planner(self):
        from .core.environment import check_environment

        env = check_environment()
        if env.blocking:
            QMessageBox.critical(
                self.iface.mainWindow(),
                PLUGIN_NAME,
                tr("El entorno no permite ejecutar la cadena de analisis:")
                + "\n\n"
                + "\n".join(f"- {issue}" for issue in env.blocking),
            )
            return

        from .ui.main_dialog import MainDialog

        if self._dialog is None:
            self._dialog = MainDialog(self.iface, self.iface.mainWindow())
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def run_diagnostics(self):
        from .core.environment import check_environment

        env = check_environment()
        QMessageBox.information(
            self.iface.mainWindow(), tr("Diagnostico del entorno"), env.report()
        )

    def run_about(self):
        QMessageBox.about(
            self.iface.mainWindow(),
            PLUGIN_NAME,
            tr(
                "<h3>{name} {version}</h3>"
                "<p>Del censo forestal al plan de manejo.</p>"
                "<p>Red hidrica y fajas marginales, humedales, patios y centros "
                "de acopio, vias de saca y areas de impacto, a partir de un "
                "modelo de elevacion y de la capa de censo.</p>"
                "<p><b>Los trazos y ubicaciones que genera son propuestas "
                "tecnicas sujetas a verificacion de campo, no disenos "
                "definitivos.</b></p>"
                "<p>Yuri Fabian Caller Cordova &middot; CIP 214377<br>"
                "Puerto Maldonado, Madre de Dios</p>"
            ).format(name=PLUGIN_NAME, version=PLUGIN_VERSION),
        )
