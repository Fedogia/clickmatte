#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ClickMatte — interfaccia grafica.

Avvio:  python3 app.py  [video]     (oppure doppio clic su "Avvia ClickMatte.command")
"""

import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import sys

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    sys.exit("Manca PySide6. Installa con:  pip install PySide6-Essentials")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interfaccia.finestra import Finestra  # noqa: E402


def tema_scuro(app):
    """(nome storico) Stile dell'app: sul Mac quello di sistema, altrove il tema scuro Fusion."""
    from interfaccia import tema
    tema.applica(app)


def _nome_nel_menu():
    """Sul Mac il primo menu porterebbe il nome "Python": lo chiamo ClickMatte (serve PyObjC, facoltativo)."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "ClickMatte"
    except Exception:
        pass


def _icona_app():
    """Icona del Dock disegnata al volo: un soggetto scontornato su scacchiera."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
    pm = QPixmap(512, 512)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    fondo = QPainterPath()
    fondo.addRoundedRect(QRectF(40, 40, 432, 432), 96, 96)
    g = QLinearGradient(0, 40, 0, 472)
    g.setColorAt(0, QColor(255, 186, 40))
    g.setColorAt(1, QColor(232, 120, 20))
    p.fillPath(fondo, g)
    p.setClipPath(fondo)
    for i in range(6):                                   # scacchiera in basso a destra
        for j in range(6):
            if (i + j) % 2 == 0:
                p.fillRect(QRectF(256 + i * 36, 256 + j * 36, 36, 36), QColor(255, 255, 255, 70))
    sagoma = QPainterPath()
    sagoma.addEllipse(QRectF(196, 110, 120, 120))
    sagoma.addRoundedRect(QRectF(146, 245, 220, 260), 100, 100)
    p.fillPath(sagoma, QColor(255, 255, 255))
    p.end()
    return QIcon(pm)


def main():
    _nome_nel_menu()
    app = QApplication(sys.argv)
    app.setWindowIcon(_icona_app())
    app.setApplicationName("ClickMatte")
    app.setOrganizationName("ClickMatte")
    tema_scuro(app)
    f = Finestra()
    f.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        f.apri(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
