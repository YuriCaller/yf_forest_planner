"""Generador minimo de documentos Word, sin dependencias externas.

Un .docx es un ZIP con XML dentro. Escribirlo a mano evita depender de
python-docx, que no viene con QGIS y obligaria a resolver una instalacion pip
en OSGeo4W, frecuentemente detras de un proxy institucional.

El alcance es deliberadamente estrecho: titulos, parrafos, tablas y listas.
Es lo que un informe tecnico necesita y nada mas.

Parte de YF Forest Planner.
"""

from __future__ import annotations

import zipfile
# Se importa unicamente escape(), que escapa texto para insertarlo en XML.
# Bandit marca el modulo xml.sax por su parser, vulnerable a entidades
# expandibles; aqui no se parsea nada; solo se genera salida.
from xml.sax.saxutils import escape  # nosec B406 - solo escape, sin parseo

# El XML de estilos va literal: partir las lineas insertaria saltos dentro de
# los atributos y Word rechazaria el documento.
# flake8: noqa: E501
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _style(sid: str, name: str, size_half_pt: int, bold: bool, before: int,
           after: int, color: str = "000000") -> str:
    return (
        f'<w:style w:type="paragraph" w:styleId="{sid}">'
        f'<w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
        f'<w:pPr><w:spacing w:before="{before}" w:after="{after}"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        f'<w:sz w:val="{size_half_pt}"/><w:color w:val="{color}"/>'
        + ("<w:b/>" if bold else "")
        + "</w:rPr></w:style>"
    )


STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{W}">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="20"/>'
    '</w:rPr></w:rPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/><w:pPr><w:spacing w:after="120"/></w:pPr></w:style>'
    + _style("Title", "Title", 44, True, 0, 240, "1F3864")
    + _style("Heading1", "heading 1", 28, True, 320, 160, "2E5E3E")
    + _style("Heading2", "heading 2", 24, True, 240, 120, "3D6B4C")
    + _style("Caption", "caption", 16, False, 0, 160, "666666")
    + '<w:style w:type="table" w:styleId="TablaFP"><w:name w:val="TablaFP"/>'
    '<w:tblPr><w:tblBorders>'
    '<w:top w:val="single" w:sz="4" w:color="BFBFBF"/>'
    '<w:left w:val="none" w:sz="0" w:color="auto"/>'
    '<w:bottom w:val="single" w:sz="4" w:color="BFBFBF"/>'
    '<w:right w:val="none" w:sz="0" w:color="auto"/>'
    '<w:insideH w:val="single" w:sz="4" w:color="D9D9D9"/>'
    '<w:insideV w:val="none" w:sz="0" w:color="auto"/>'
    '</w:tblBorders></w:tblPr></w:style>'
    "</w:styles>"
)


class DocxBuilder:
    """Acumula bloques y los escribe como .docx."""

    def __init__(self):
        self._body: list[str] = []

    # ---------------------------------------------------------------- texto

    def title(self, text: str) -> "DocxBuilder":
        return self._para(text, "Title")

    def heading(self, text: str, level: int = 1) -> "DocxBuilder":
        return self._para(text, f"Heading{min(2, max(1, level))}")

    def paragraph(self, text: str, style: str = "Normal") -> "DocxBuilder":
        return self._para(text, style)

    def caption(self, text: str) -> "DocxBuilder":
        return self._para(text, "Caption")

    def bullets(self, items) -> "DocxBuilder":
        for item in items:
            self._body.append(
                '<w:p><w:pPr><w:spacing w:after="40"/>'
                '<w:ind w:left="360" w:hanging="180"/></w:pPr>'
                f"<w:r><w:t xml:space=\"preserve\">\u2022  {escape(str(item))}"
                "</w:t></w:r></w:p>"
            )
        return self

    def page_break(self) -> "DocxBuilder":
        self._body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        return self

    def _para(self, text: str, style: str) -> "DocxBuilder":
        self._body.append(
            f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
            f"<w:r><w:t xml:space=\"preserve\">{escape(str(text))}</w:t></w:r></w:p>"
        )
        return self

    # ---------------------------------------------------------------- tabla

    def table(self, headers, rows, widths=None) -> "DocxBuilder":
        """Tabla simple con encabezado en negrita."""
        n = len(headers)
        if widths is None:
            widths = [int(9000 / max(1, n))] * n

        cells = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
        parts = [
            '<w:tbl><w:tblPr><w:tblStyle w:val="TablaFP"/>'
            '<w:tblW w:w="0" w:type="auto"/><w:tblBorders>'
            '<w:top w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '<w:bottom w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '<w:insideH w:val="single" w:sz="4" w:color="D9D9D9"/>'
            "</w:tblBorders></w:tblPr>"
            f"<w:tblGrid>{cells}</w:tblGrid>"
        ]
        parts.append(self._row(headers, widths, bold=True))
        for row in rows:
            parts.append(self._row(row, widths, bold=False))
        parts.append("</w:tbl>")
        # Un parrafo vacio tras la tabla: sin el, dos tablas seguidas se
        # fusionan en una sola al abrir el documento.
        parts.append('<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')
        self._body.append("".join(parts))
        return self

    @staticmethod
    def _row(values, widths, bold: bool) -> str:
        out = ["<w:tr>"]
        for i, value in enumerate(values):
            width = widths[i] if i < len(widths) else widths[-1]
            run = "<w:rPr><w:b/><w:sz w:val=\"18\"/></w:rPr>" if bold else \
                  "<w:rPr><w:sz w:val=\"18\"/></w:rPr>"
            shading = (
                '<w:shd w:val="clear" w:color="auto" w:fill="EFEFEF"/>'
                if bold else ""
            )
            out.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}</w:tcPr>'
                f'<w:p><w:pPr><w:spacing w:after="20"/></w:pPr>'
                f"<w:r>{run}<w:t xml:space=\"preserve\">{escape(str(value))}"
                "</w:t></w:r></w:p></w:tc>"
            )
        out.append("</w:tr>")
        return "".join(out)

    # ---------------------------------------------------------------- salida

    def build_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{W}"><w:body>'
            + "".join(self._body)
            + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1418"/>'
            "</w:sectPr></w:body></w:document>"
        )

    def save(self, path: str) -> str:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", CONTENT_TYPES)
            zf.writestr("_rels/.rels", RELS)
            zf.writestr("word/_rels/document.xml.rels", DOC_RELS)
            zf.writestr("word/styles.xml", STYLES)
            zf.writestr("word/document.xml", self.build_xml())
        return path
