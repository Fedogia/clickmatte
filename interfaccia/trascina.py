# -*- coding: utf-8 -*-
"""File esportati da trascinare: in Premiere (pannello Progetto o timeline), nel Finder, ovunque."""

import os

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import tema


class _Etichetta(QLabel):
    """Un'etichetta che si puo' trascinare: porta con se' uno o piu' file (come trascinarli dal Finder)."""

    def __init__(self, testo, aiuto):
        super().__init__(testo)
        self.percorsi = []
        self._da = None
        self.setToolTip(aiuto)
        self.setCursor(Qt.OpenHandCursor)
        tema.ruolo(self, "file")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._da = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._da is None or not self.percorsi:
            return
        if (e.position().toPoint() - self._da).manhattanLength() < QApplication.startDragDistance():
            return
        self._da = None
        dati = QMimeData()
        dati.setUrls([QUrl.fromLocalFile(p) for p in self.percorsi if os.path.exists(p)])
        drag = QDrag(self)
        drag.setMimeData(dati)
        pm = QPixmap(self.size() * self.devicePixelRatioF())
        pm.setDevicePixelRatio(self.devicePixelRatioF())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setOpacity(0.85)
        self.render(p, QPoint())
        p.end()
        drag.setPixmap(pm)
        drag.exec(Qt.CopyAction)

    def mouseReleaseEvent(self, e):
        self._da = None


class FileDaTrascinare(QWidget):
    """Riquadro "Trascina in Premiere": BASE + SOGGETTO insieme, oppure un file alla volta."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(4)
        titolo = QLabel("<b>Trascina in Premiere</b> (o nel Finder)")
        v.addWidget(titolo)
        self.tutti = _Etichetta("⇪  BASE + SOGGETTO insieme", "Trascina nel pannello Progetto di Premiere: importa "
                                "la clip base e il soggetto con la trasparenza")
        v.addWidget(self.tutti)
        r = QHBoxLayout()
        r.setSpacing(4)
        self.singoli = {}
        for k, nome, aiuto in (("base", "BASE", "Clip base (ProRes 422 HQ) → «MEDIA · Clip base»"),
                               ("soggetto", "SOGGETTO", "Soggetto con trasparenza (ProRes 4444) → «SOVRAIMPR. 1»"),
                               ("anteprima", "ANTEPRIMA", "Anteprima su scacchiera, solo per controllare")):
            e = _Etichetta(nome, aiuto)
            e.setAlignment(Qt.AlignCenter)
            self.singoli[k] = e
            r.addWidget(e)
        v.addLayout(r)
        nota = tema.ruolo(QLabel("Le esportazioni successive sostituiscono i file con lo stesso nome: in Premiere "
                                 "restano collegati (se vedi la versione vecchia, chiudi e riapri il progetto)."),
                          "descr")
        nota.setWordWrap(True)
        v.addWidget(nota)
        self.setVisible(False)

    def imposta(self, percorsi):
        """percorsi: {"base", "soggetto", "anteprima"} oppure vuoto (nasconde il riquadro)."""
        ok = bool(percorsi) and all(os.path.isfile(percorsi.get(k, "")) for k in ("base", "soggetto"))
        self.setVisible(ok)
        if not ok:
            return
        self.tutti.percorsi = [percorsi["base"], percorsi["soggetto"]]
        for k, e in self.singoli.items():
            e.percorsi = [percorsi[k]] if percorsi.get(k) else []
            e.setEnabled(bool(e.percorsi) and os.path.isfile(e.percorsi[0]))


__all__ = ["FileDaTrascinare"]
