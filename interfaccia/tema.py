# -*- coding: utf-8 -*-
"""Aspetto dell'app: stile del Mac, colori di sistema (chiaro/scuro), icone SF Symbols, fogli, notifiche.

Sul Mac si usa lo stile "macos" di Qt (pulsanti, tendine e cursori disegnati da AppKit) senza palette
fissa: chiaro/scuro e colore di accento seguono le Impostazioni di Sistema, anche ad app aperta.
Altrove (e nei collaudi senza schermo) resta il tema scuro Fusion.
"""

import functools
import subprocess
import sys

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

MAC = sys.platform == "darwin"


class _Avvisi(QObject):
    cambiato = Signal()          # chiaro/scuro cambiato: chi disegna da se' si ridisegna


avvisi = _Avvisi()


# ---------------------------------------------------------------------------
#  stile e colori
# ---------------------------------------------------------------------------
def applica(app):
    if MAC and "macos" in [k.lower() for k in QStyleFactory.keys()]:
        app.setStyle("macos")
    else:
        app.setStyle("Fusion")
        app.setPalette(_palette_scura())
    aggiorna(app)
    try:
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: aggiorna(app))
    except AttributeError:                                  # Qt piu' vecchio: niente cambio al volo
        pass


def aggiorna(app=None):
    app = app or QApplication.instance()
    _icona_png.cache_clear()
    app.setStyleSheet(_foglio())
    avvisi.cambiato.emit()


def scuro():
    return QApplication.palette().color(QPalette.Window).lightness() < 128


def colore(nome):
    """Colori con un significato, adatti al tema chiaro o scuro."""
    s = scuro()
    pal = QApplication.palette()
    tavola = {
        "secondario": pal.color(QPalette.PlaceholderText).name() if MAC else "#9a9aa2",
        "avviso": "#e0a030" if s else "#a8640a",
        "errore": "#ff6b6b" if s else "#c62828",
        "ok": "#5fd38d" if s else "#1e8a4c",
        "info": "#7fc8ff" if s else "#0a62c2",
        "accento": pal.color(QPalette.Highlight).name(),
        "riquadro": "#2b3340" if s else "#e4edf8",
        "riquadro_testo": "#e8eef7" if s else "#1d2a3a",
        "passi": "#24272d" if s else "#eef0f3",
    }
    return tavola[nome]


def _foglio():
    """Regole solo per le etichette con un 'ruolo': il resto resta disegnato dal sistema."""
    regole = f"""
        QLabel[ruolo="descr"] {{ color: {colore('secondario')}; font-size: 11px; }}
        QLabel[ruolo="info"] {{ color: {colore('info')}; font-size: 11px; }}
        QLabel[ruolo="avviso"] {{ color: {colore('avviso')}; font-size: 11px; }}
        QLabel[ruolo="errore"] {{ color: {colore('errore')}; font-size: 11px; }}
        QLabel[ruolo="suggerimento"] {{ background: {colore('riquadro')}; color: {colore('riquadro_testo')};
                                        padding: 6px 10px; border-radius: 6px; }}
        QLabel[ruolo="passi"] {{ background: {colore('passi')}; padding: 8px; border-radius: 6px; }}
        QLabel[ruolo="file"] {{ border: 1px solid {colore('secondario')}; border-radius: 5px; padding: 4px 8px; }}
    """
    if not MAC:                                             # tema Fusion: qualche ritocco in piu'
        regole += """
        QToolButton:checked { background:#ffb000; color:#141414; border-radius:4px; }
        QToolButton { padding:4px 8px; } QPushButton { padding:5px 12px; }
        """
    return regole


def _palette_scura():
    p = QPalette()
    for ruolo, col in ((QPalette.Window, (37, 37, 40)), (QPalette.WindowText, (225, 225, 230)),
                       (QPalette.Base, (26, 26, 29)), (QPalette.AlternateBase, (44, 44, 48)),
                       (QPalette.ToolTipBase, (50, 50, 55)), (QPalette.ToolTipText, (235, 235, 240)),
                       (QPalette.Text, (225, 225, 230)), (QPalette.Button, (52, 52, 57)),
                       (QPalette.ButtonText, (225, 225, 230)), (QPalette.BrightText, (255, 90, 90)),
                       (QPalette.Highlight, (255, 176, 0)), (QPalette.HighlightedText, (20, 20, 20)),
                       (QPalette.PlaceholderText, (130, 130, 138))):
        p.setColor(ruolo, QColor(*col))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 110, 116))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 110, 116))
    return p


def ruolo(widget, nome):
    """Assegna un ruolo (descr, info, avviso, errore, suggerimento, passi, file) a un'etichetta."""
    widget.setProperty("ruolo", nome)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget


# ---------------------------------------------------------------------------
#  icone: SF Symbols sul Mac, un simbolo di testo altrove
# ---------------------------------------------------------------------------
RISERVA = {"plus.circle": "＋", "minus.circle": "−", "rectangle.dashed": "▭", "photo": "▣",
           "person.crop.rectangle": "◐", "checkerboard.rectangle": "▦", "nosign": "⦸", "trash": "✕",
           "arrow.up.left.and.arrow.down.right": "⤢", "backward.end.fill": "⏮", "forward.end.fill": "⏭",
           "chevron.left": "◀", "chevron.right": "▶", "play.fill": "▶", "pause.fill": "⏸",
           "diamond": "◆", "exclamationmark.triangle": "⚠", "magnifyingglass": "🔍",
           "arrow.uturn.backward": "↶", "arrow.uturn.forward": "↷", "figure.walk": "➜",
           "square.and.arrow.up": "⇪", "eye": "◉", "film": "▤", "bracket.left": "[", "bracket.right": "]"}


def icona(nome, lato=18):
    """QIcon del simbolo di sistema 'nome' (SF Symbols), colorato come il testo dei pulsanti."""
    col = QApplication.palette().color(QPalette.ButtonText).name()
    dati = _icona_png(nome, lato, col)
    pm = QPixmap()
    if dati and pm.loadFromData(dati):
        pm.setDevicePixelRatio(2.0)
        return QIcon(pm)
    return QIcon()


@functools.lru_cache(maxsize=256)
def _icona_png(nome, lato, colore_hex):
    """PNG (2x) del simbolo: da SF Symbols se c'e' PyObjC, altrimenti disegnato con un carattere."""
    pm = _sf_symbol(nome, lato) if MAC else None
    if pm is None:
        pm = _glifo(RISERVA.get(nome, "•"), lato)
    # colora: tengo solo la forma (alfa) e la riempio con il colore del testo
    col = QPixmap(pm.size())
    col.fill(Qt.transparent)
    p = QPainter(col)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(col.rect(), QColor(colore_hex))
    p.end()
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    col.save(buf, "PNG")
    return bytes(ba)


def _sf_symbol(nome, lato):
    try:
        import AppKit
        from AppKit import NSBitmapImageRep, NSImage, NSImageSymbolConfiguration
        tipo_png = getattr(AppKit, "NSBitmapImageFileTypePNG", getattr(AppKit, "NSPNGFileType", 4))
    except Exception:
        return None
    try:
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(nome, None)
        if img is None:
            return None
        conf = NSImageSymbolConfiguration.configurationWithPointSize_weight_(lato * 2, 0.0)
        img = img.imageWithSymbolConfiguration_(conf)
        rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
        png = rep.representationUsingType_properties_(tipo_png, {})
        pm = QPixmap()
        if not pm.loadFromData(bytes(png)):
            return None
        # centro in un quadrato, cosi' tutte le icone hanno la stessa misura
        q = QPixmap(lato * 2, lato * 2)
        q.fill(Qt.transparent)
        pm = pm.scaled(q.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p = QPainter(q)
        p.drawPixmap((q.width() - pm.width()) // 2, (q.height() - pm.height()) // 2, pm)
        p.end()
        return q
    except Exception:
        return None


def _glifo(testo, lato):
    q = QPixmap(lato * 2, lato * 2)
    q.fill(Qt.transparent)
    p = QPainter(q)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont()
    f.setPixelSize(int(lato * 1.5))
    p.setFont(f)
    p.setPen(QColor(0, 0, 0))
    p.drawText(QRect(QPoint(0, 0), q.size()), Qt.AlignCenter, testo)
    p.end()
    return q


# ---------------------------------------------------------------------------
#  fogli (finestre che scendono dalla barra del titolo) e notifiche
# ---------------------------------------------------------------------------
def _box(parent, testo, info, icona_box):
    box = QMessageBox(parent)
    box.setIcon(icona_box)
    box.setText(testo)
    if info:
        box.setInformativeText(info)
    box.setWindowModality(Qt.WindowModal)          # sul Mac: un foglio attaccato alla finestra
    return box


def chiedi(parent, testo, info="", si="Continua", no="Annulla"):
    """Domanda con due risposte; restituisce True se si sceglie 'si'."""
    box = _box(parent, testo, info, QMessageBox.Question)
    b_si = box.addButton(si, QMessageBox.AcceptRole)
    box.addButton(no, QMessageBox.RejectRole)
    box.setDefaultButton(b_si)
    box.exec()
    return box.clickedButton() is b_si


def avvisa(parent, testo, info="", errore=False):
    """Messaggio da leggere (non blocca il programma)."""
    box = _box(parent, testo, info, QMessageBox.Warning if errore else QMessageBox.Information)
    box.setAttribute(Qt.WA_DeleteOnClose)
    box.open()
    return box


def notifica(finestra, titolo, testo):
    """Fine di un lavoro lungo: notifica di sistema e icona nel Dock che salta (se sei su un'altra app)."""
    if finestra.isActiveWindow():
        return
    QApplication.alert(finestra, 0)
    if MAC:
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')   # noqa: E731
        try:
            subprocess.Popen(["osascript", "-e", f'display notification "{esc(testo)}" with title "{esc(titolo)}"'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


__all__ = ["MAC", "applica", "aggiorna", "avvisi", "scuro", "colore", "ruolo", "icona", "chiedi", "avvisa",
           "notifica"]
