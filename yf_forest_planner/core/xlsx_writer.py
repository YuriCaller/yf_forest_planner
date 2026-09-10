"""Generador minimo de libros de Excel, sin dependencias externas.

Un .xlsx es un ZIP con XML dentro, igual que un .docx. Escribirlo a mano evita
depender de openpyxl, que no viene garantizado con QGIS.

Alcance: varias hojas, encabezado en negrita con filtro y panel congelado,
numeros y texto, y ancho de columna. Es lo que necesita un cuadro de resumen.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import zipfile
# Se importa unicamente escape(), que escapa texto para insertarlo en XML.
# Bandit marca el modulo xml.sax por su parser, vulnerable a entidades
# expandibles; aqui no se parsea nada; solo se genera salida.
from xml.sax.saxutils import escape  # nosec B406 - solo escape, sin parseo

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CONTENT_TYPES_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

# El XML de estilos va literal: partir las lineas para cumplir el limite de
# longitud insertaria saltos dentro de los atributos y Excel rechazaria el
# archivo. Se exceptua del chequeo de longitud.
# flake8: noqa: E501
STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{NS}">
<numFmts count="2">
<numFmt numFmtId="164" formatCode="#,##0.00"/>
<numFmt numFmtId="165" formatCode="#,##0"/>
</numFmts>
<fonts count="3">
<font><sz val="10"/><name val="Calibri"/></font>
<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF2E5E3E"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"><color rgb="FFD9D9D9"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
</cellXfs>
</styleSheet>"""

ESTILO_TEXTO = 4
ESTILO_DECIMAL = 2
ESTILO_ENTERO = 3
ESTILO_ENCABEZADO = 1


def col_letter(index: int) -> str:
    """1 -> A, 27 -> AA."""
    letras = ""
    while index > 0:
        index, resto = divmod(index - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


class Sheet:
    """Una hoja: encabezados y filas."""

    def __init__(self, nombre: str, headers=None, anchos=None):
        # Excel limita el nombre a 31 caracteres y prohibe : \ / ? * [ ]
        limpio = str(nombre)[:31]
        for ch in ':\\/?*[]':
            limpio = limpio.replace(ch, "-")
        self.nombre = limpio or "Hoja"
        self.headers = list(headers or [])
        self.rows: list[list] = []
        self.anchos = anchos

    def add(self, fila) -> "Sheet":
        self.rows.append(list(fila))
        return self

    def extend(self, filas) -> "Sheet":
        for fila in filas:
            self.add(fila)
        return self

    def _xml(self) -> str:
        n_cols = max(
            [len(self.headers)] + [len(r) for r in self.rows] or [1]
        )
        n_filas = len(self.rows) + (1 if self.headers else 0)

        # El orden de los elementos dentro de <worksheet> lo fija el esquema
        # ECMA-376 y Excel lo exige de forma estricta, aunque un parser XML
        # generico acepte cualquier orden. La secuencia valida es:
        #   dimension, sheetViews, sheetFormatPr, cols, sheetData, autoFilter
        # Poner <cols> antes de <sheetViews> hace que Excel declare el archivo
        # danado y ofrezca repararlo.
        partes = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            f'<worksheet xmlns="{NS}">',
        ]

        ref_final = f"{col_letter(n_cols)}{max(1, n_filas)}"
        partes.append(f'<dimension ref="A1:{ref_final}"/>')

        if self.headers:
            # Congelar la fila de encabezado: en cuadros largos, perderla de
            # vista al desplazarse hace ilegible la tabla.
            partes.append(
                '<sheetViews><sheetView workbookViewId="0">'
                '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
                'state="frozen"/>'
                '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
                "</sheetView></sheetViews>"
            )
        else:
            partes.append(
                '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
            )

        partes.append('<sheetFormatPr defaultRowHeight="15"/>')

        if self.anchos:
            cols = "".join(
                f'<col min="{i + 1}" max="{i + 1}" width="{w}" '
                'customWidth="1"/>'
                for i, w in enumerate(self.anchos)
            )
            partes.append(f"<cols>{cols}</cols>")

        partes.append("<sheetData>")
        fila_n = 1
        if self.headers:
            partes.append(self._row_xml(fila_n, self.headers, encabezado=True))
            fila_n += 1
        for fila in self.rows:
            partes.append(self._row_xml(fila_n, fila))
            fila_n += 1
        partes.append("</sheetData>")

        if self.headers and self.rows:
            ref = f"A1:{col_letter(n_cols)}{fila_n - 1}"
            partes.append(f'<autoFilter ref="{ref}"/>')

        partes.append("</worksheet>")
        return "".join(partes)

    @staticmethod
    def _row_xml(numero: int, valores, encabezado: bool = False) -> str:
        celdas = []
        for i, valor in enumerate(valores, start=1):
            ref = f"{col_letter(i)}{numero}"
            if encabezado:
                celdas.append(
                    f'<c r="{ref}" s="{ESTILO_ENCABEZADO}" t="inlineStr">'
                    f"<is><t>{escape(str(valor))}</t></is></c>"
                )
                continue
            if isinstance(valor, bool):
                texto = "si" if valor else "no"
                celdas.append(
                    f'<c r="{ref}" s="{ESTILO_TEXTO}" t="inlineStr">'
                    f"<is><t>{escape(texto)}</t></is></c>"
                )
            elif isinstance(valor, int):
                celdas.append(f'<c r="{ref}" s="{ESTILO_ENTERO}"><v>{valor}</v></c>')
            elif isinstance(valor, float):
                celdas.append(
                    f'<c r="{ref}" s="{ESTILO_DECIMAL}"><v>{valor:.6f}</v></c>'
                )
            elif valor is None:
                celdas.append(f'<c r="{ref}" s="{ESTILO_TEXTO}"/>')
            else:
                celdas.append(
                    f'<c r="{ref}" s="{ESTILO_TEXTO}" t="inlineStr">'
                    f"<is><t>{escape(str(valor))}</t></is></c>"
                )
        return f'<row r="{numero}">' + "".join(celdas) + "</row>"


class Workbook:
    """Libro con varias hojas."""

    def __init__(self):
        self.sheets: list[Sheet] = []

    def sheet(self, nombre: str, headers=None, anchos=None) -> Sheet:
        hoja = Sheet(nombre, headers, anchos)
        self.sheets.append(hoja)
        return hoja

    def save(self, path: str) -> str:
        if not self.sheets:
            self.sheet("Vacio", ["Sin datos"])

        tipos = [CONTENT_TYPES_HEAD]
        for i in range(len(self.sheets)):
            tipos.append(
                f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.'
                'spreadsheetml.worksheet+xml"/>'
            )
        tipos.append("</Types>")

        hojas_xml = "".join(
            f'<sheet name="{escape(s.nombre)}" sheetId="{i + 1}" '
            f'r:id="rId{i + 1}"/>'
            for i, s in enumerate(self.sheets)
        )
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{NS}" xmlns:r="{R_NS}">'
            f"<sheets>{hojas_xml}</sheets></workbook>"
        )

        rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships">']
        for i in range(len(self.sheets)):
            rels.append(
                f'<Relationship Id="rId{i + 1}" Type="{R_NS}/worksheet" '
                f'Target="worksheets/sheet{i + 1}.xml"/>'
            )
        rels.append(
            f'<Relationship Id="rId{len(self.sheets) + 1}" '
            f'Type="{R_NS}/styles" Target="styles.xml"/>'
        )
        rels.append("</Relationships>")

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", "".join(tipos))
            zf.writestr("_rels/.rels", RELS)
            zf.writestr("xl/workbook.xml", workbook)
            zf.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
            zf.writestr("xl/styles.xml", STYLES)
            for i, hoja in enumerate(self.sheets):
                zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", hoja._xml())
        return path
