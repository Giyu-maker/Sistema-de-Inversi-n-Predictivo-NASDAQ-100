"""Reportes financieros tabulares — RF-702 (y RF-201/RF-203 al alimentarlos).

Este módulo es **puro**: recibe un encabezado, columnas y filas, y devuelve
bytes o texto. No toca bases de datos ni HTTP, así que se prueba sin red y
sin contenedor; quién junta los datos es `src/endpoints/reportes.py`.

Tres decisiones que no son estéticas
-----------------------------------

1. **CSV con `;` y BOM.** El BOM (`\\ufeff`) es lo que hace que Excel abra
   los acentos y la ñ bien; el separador `;` es lo que hace que abra las
   **columnas**: Excel en español (es-CO) usa la coma como separador de
   decimal y el punto y coma como de lista, así que un CSV con `,` llega
   entero a la columna A. Con `;` los números en coma decimal se leen como
   números de verdad. Quien lo lea con pandas: `sep=";"` y `decimal=","`.
2. **Neutralización de fórmulas solo en texto.** Un campo de la base que
   empiece por `=`, `+`, `-` o `@` se prefija con apóstrofo para que Excel
   no lo ejecute (incyección de fórmulas). **No se aplica a los números**:
   un rendimiento de `-1,25 %` tiene que seguir siendo negativo.
3. **PDF con fuentes base (Latin-1).** `fpdf2` con Helvetica no tiene
   glifos para `−`, `≥`, `→`, `…` o comillas tipográficas: `_para_pdf()`
   los normaliza a su equivalente ASCII antes de componer, y como red de
   seguridad filtra a Latin-1. Tildes y ñ sí están (WinAnsi), y hay una
   prueba que lo comprueba generando un PDF con «Señales de la sesión,
   año 2026».

La cabecera la llevan **todos** los formatos, con la métrica honesta del
modelo: exactitud direccional **sin ventaja probada** (el IC 95 % incluye
el 50 %), que es lo que dice el informe de entrenamiento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from src.utils.fechas import ZONA_COLOMBIA, ZONA_MERCADO

#: Tipos de reporte aceptados por `GET /api/reportes/{tipo}`.
TIPOS_REPORTE: tuple[str, ...] = ("senal", "rendimiento", "kill-switch", "auditoria")

#: Formatos aceptados.
FORMATOS_REPORTE: tuple[str, ...] = ("csv", "pdf")

#: Techo de filas por archivo: un rango enorme no puede colgar el API.
LIMITE_FILAS = 5000

#: Líneas de cabecera antes de la fila de títulos (7 campos + la vacía).
#: Quien parsee el CSV programáticamente puede saltarlas con este número.
FILAS_CABECERA = 8

#: Lo que dice el SRS §4.2.2/CR-002 sobre la fuente, en una sola frase.
FUENTE = (
    "QQQ como proxy del NASDAQ-100; solo horario regular (09:30-16:00 ET); "
    "sin pre-market ni after-hours (CR-002)."
)

#: La nota que distingue un reporte de este sistema de uno con capital real.
NOTA = "Datos de mercado reales; broker simulado."

METRICA_SIN_DATOS = (
    "Métrica del modelo: no disponible (el servicio de M1 no respondió)."
)

#: Títulos legibles de cada tipo (los usa la API y la UI).
TITULOS: dict[str, str] = {
    "senal": "Señales de la IA por sesión",
    "rendimiento": "Rendimiento asimétrico por sesión",
    "kill-switch": "Eventos del Kill-Switch",
    "auditoria": "Resumen de auditoría",
}


# -- fechas -----------------------------------------------------------------


def fecha_ny(epoch_ms: int) -> str:
    """`DD/MM/YYYY` de la fecha de **sesión** (America/New_York)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=ZONA_MERCADO).strftime("%d/%m/%Y")


def hora_ny(epoch_ms: int) -> str:
    """`HH:MM` en hora de Nueva York (la sesión se identifica en ET)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=ZONA_MERCADO).strftime("%H:%M")


def fecha_colombia(epoch_ms: int) -> str:
    """`DD/MM/YYYY HH:mm:ss` en hora de Colombia (marcas de auditoría)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=ZONA_COLOMBIA).strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def fecha_de_epoch_ny(epoch_ms: int) -> date:
    """Fecha de sesión (ET) de un epoch ms, para agrupar por sesión."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=ZONA_MERCADO).date()


def rango_texto(desde: date, hasta: date) -> str:
    """Rango en la cabecera, con la zona declarada (ET, no Colombia)."""
    return (
        f"del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')} "
        "(fechas de sesión en America/New_York)"
    )


# -- métrica del modelo -----------------------------------------------------


def metrica_de_modelo(calidad: Any | None) -> str:
    """Exactitud direccional en lenguaje llano, con su honestidad.

    `calidad` es el cuerpo de `GET /modelo` (o `None` si no llegó). La frase
    cambia según `ventaja_demostrada`: decir «sin ventaja probada» cuando el
    IC incluye el 50 % es exactamente lo que exige el informe de
    entrenamiento, y no se puede escribir a mano en cada reporte.
    """
    if calidad is None or not getattr(calidad, "disponible", False):
        return METRICA_SIN_DATOS
    acierto = getattr(calidad, "acierto_direccional", None)
    if acierto is None or acierto.ratio is None:
        return "Métrica del modelo: sin exactitud direccional disponible."
    porcentaje = _porcentaje(acierto.ratio)
    base = f"Métrica del modelo: exactitud direccional {porcentaje}"
    if acierto.n is not None:
        base += f" sobre {acierto.n} predicciones"
    if acierto.ventaja_demostrada is False:
        return base + "; sin ventaja direccional probada (el IC 95 % incluye el 50 %)."
    if acierto.ventaja_demostrada is True:
        return base + "; ventaja direccional demostrada en la muestra (informe de entrenamiento)."
    return base + "; ventaja direccional sin determinar."


def _porcentaje(valor: float) -> str:
    """0.4728 → «47,3 %» (coma decimal, convención del proyecto)."""
    return f"{valor * 100:.1f}".replace(".", ",") + " %"


# -- cabecera ---------------------------------------------------------------


@dataclass(frozen=True)
class EncabezadoReporte:
    """Lo que todo reporte lleva arriba, en ambos formatos."""

    titulo: str
    generado_en: str  #: DD/MM/YYYY HH:mm:ss en hora de Colombia
    rango: str
    metrica: str
    filas: int
    nota: str = NOTA
    fuente: str = FUENTE

    def campos(self) -> list[tuple[str, str]]:
        return [
            ("Reporte", self.titulo),
            ("Generado", self.generado_en),
            ("Rango", self.rango),
            ("Fuente", self.fuente),
            ("Nota", self.nota),
            ("Métrica del modelo", self.metrica),
            ("Filas", str(self.filas)),
        ]


# -- números y celdas -------------------------------------------------------


def _numero(valor: float, decimales: int = 2) -> str:
    """Formato numérico con coma decimal (convención es-CO, ver docstring)."""
    if isinstance(valor, bool):  # antes que int: bool es subclase de int
        return "Sí" if valor else "No"
    if isinstance(valor, int):
        return str(valor)
    if valor != valor:  # NaN
        return ""
    texto = f"{valor:.{decimales}f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def neutralizar(texto: str) -> str:
    """Prefija `'` a un texto que empezaría por un carácter de fórmula.

    Solo para **texto**: aplicarla a un número convertiría `-1,25` en texto
    y el rendimiento dejaría de ser negativo en Excel.
    """
    if texto[:1] in ("=", "+", "-", "@"):
        return "'" + texto
    return texto


def celda_csv(valor: Any) -> str:
    """Una celda del CSV: número en coma decimal, texto neutralizado y citado."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, (int, float)):
        return _numero(valor)  # sin neutralizar: los negativos son números
    texto = neutralizar(str(valor))
    if any(c in texto for c in (_SEPARADOR, _COMILLA, "\n", "\r")):
        return _COMILLA + texto.replace(_COMILLA, _COMILLA * 2) + _COMILLA
    return texto


# -- CSV --------------------------------------------------------------------

#: Separador de columna y comilla de citado, como constantes: escribirlos
#: como literales dentro de una tupla fue la forma más fácil de perder una
#: comilla y dejar de compilar el archivo entero.
_SEPARADOR = ";"
_COMILLA = '"'



def a_csv(encabezado: EncabezadoReporte, columnas: Sequence[str], filas: Iterable[Sequence[Any]]) -> str:
    """CSV en UTF-8 **con BOM**, separador `;`, cabecera incluida.

    El BOM va como carácter `\\ufeff` al inicio: al codificar en UTF-8
    resulta en los bytes `EF BB BF`, que es lo que Excel busca para decidir
    la codificación.
    """
    lineas: list[str] = ["\ufeff"]  # BOM (EF BB BF en UTF-8)
    for clave, valor in encabezado.campos():
        lineas.append(f"{clave};{celda_csv(valor)}\n")
    lineas.append("\n")  # separa la cabecera de la tabla
    lineas.append(";".join(celda_csv(c) for c in columnas) + "\n")
    for fila in filas:
        lineas.append(";".join(celda_csv(c) for c in fila) + "\n")
    return "".join(lineas)


# -- PDF --------------------------------------------------------------------

#: Sustituciones para las fuentes base (Latin-1/WinAnsi) de `fpdf2`.
_PARA_PDF: dict[str, str] = {
    "\u2212": "-",  # menos signo
    "\u2013": "-",  # raya corta
    "\u2014": "-",  # raya larga
    "\u2018": "'",  # comillas simples
    "\u2019": "'",
    "\u201c": '"',  # comillas dobles
    "\u201d": '"',
    "\u2264": "<=",
    "\u2265": ">=",
    "\u2192": "->",
    "\u2190": "<-",
    "\u2026": "...",
    "\u00a0": " ",  # espacio duro
    "\u2022": "-",  # viñeta
    "\u00d7": "x",
}


def para_pdf(texto: str) -> str:
    """Normaliza a Latin-1 lo que la fuente base no tiene (ver docstring)."""
    salida = str(texto)
    for origen, destino in _PARA_PDF.items():
        salida = salida.replace(origen, destino)
    # Red de seguridad: cualquier otro glifo fuera de Latin-1 pasa a '?'
    # en lugar de reventar la generación a mitad del reporte.
    return salida.encode("latin-1", "replace").decode("latin-1")


def a_pdf(encabezado: EncabezadoReporte, columnas: Sequence[str], filas: Sequence[Sequence[Any]]) -> bytes:
    """PDF A4 con la misma cabecera que el CSV.

    `fpdf2` se importa dentro de la función: quien solo necesita CSV no
    paga el import de pillow/fonttools, y la dependencia vive en el grupo
    `ai` (solo la imagen de la API la instala).
    """
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("helvetica", "B", 13)
    pdf.multi_cell(0, 7, para_pdf(encabezado.titulo), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("helvetica", "", 8)
    for clave, valor in encabezado.campos():
        pdf.multi_cell(0, 4.5, para_pdf(f"{clave}: {valor}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    pdf.set_font("helvetica", "B", 9)
    filas_pdf: list[list[str]] = [[para_pdf(c) for c in columnas]]
    pdf.set_font("helvetica", "", 8)
    for fila in filas:
        filas_pdf.append([para_pdf(_a_texto(c)) for c in fila])

    # Pesos de columna: los textos largos (motivo, XAI) necesitan más ancho
    # que las fechas. `fpdf2` reparte el ancho de la página proporcionalmente.
    anchos = [max(12, min(60, len(c) * 2)) for c in columnas]
    with pdf.table(col_widths=anchos, text_align="LEFT", line_height=4.5) as tabla:
        for fila in filas_pdf:
            tabla.row(fila)

    return bytes(pdf.output())


def _a_texto(valor: Any) -> str:
    """Celda del PDF: números con coma decimal, `None` como vacío."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, (int, float)):
        return _numero(valor)
    return str(valor)


# -- utilidades de presentación ---------------------------------------------


def si_no(valor: bool | None) -> str:
    if valor is None:
        return ""
    return "Sí" if valor else "No"
