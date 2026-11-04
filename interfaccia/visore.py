# -*- coding: utf-8 -*-
"""Visore del frame: clic per aggiungere/togliere parti, trascinamento per le zone ignorate, zoom con la rotella."""

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)

VERDE = QColor(40, 220, 90)
ROSSO = QColor(240, 60, 60)
GIALLO = QColor(255, 205, 40)


def qimage_rgb(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def qimage_rgba(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


class Visore(QGraphicsView):
    punto_aggiunto = Signal(float, float, int)      # x%, y%, tipo (1 = aggiungi, 0 = togli)
    punto_tolto = Signal(int)                        # indice del punto nella chiave corrente
    zona_aggiunta = Signal(float, float, float, float)
    zona_tolta = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setFrameShape(QGraphicsView.NoFrame)
        self._sfondo()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMouseTracking(True)
        self._img = QGraphicsPixmapItem()
        self._sovr = QGraphicsPixmapItem()
        self._sovr.setZValue(1)
        self.scene().addItem(self._img)
        self.scene().addItem(self._sovr)
        self._punti_items, self._zone_items, self._mani_items, self._segni_items = [], [], [], []
        self._w = self._h = 0
        self._zoom_utente = False
        self.strumento = "aggiungi"              # "aggiungi" | "togli" | "zona"
        self.viewport().setCursor(Qt.CrossCursor)
        self.modifica_attiva = True               # False durante la riproduzione / risultato
        self._trascina = None
        self._rett = None
        self._pan = None
        self._etichetta_fondo = QGraphicsRectItem()
        self._etichetta_fondo.setBrush(QBrush(QColor(0, 0, 0, 150)))
        self._etichetta_fondo.setPen(QPen(Qt.NoPen))
        self._etichetta_fondo.setZValue(5)
        self.scene().addItem(self._etichetta_fondo)
        self._etichetta = QGraphicsSimpleTextItem()
        self._etichetta.setBrush(QBrush(QColor(255, 255, 255)))
        self._etichetta.setZValue(6)
        self.scene().addItem(self._etichetta)

    def _sfondo(self):
        """Fondo attorno al video: scuro e neutro, che sia attivo il tema chiaro o quello scuro."""
        fin = self.palette().color(QPalette.Window)
        self.setBackgroundBrush(fin.darker(150) if fin.lightness() < 128 else fin.darker(118))

    def changeEvent(self, e):
        if e.type() == QEvent.PaletteChange:
            self._sfondo()
        super().changeEvent(e)

    # --- contenuto --------------------------------------------------------
    def mostra(self, rgb, sovrapposizione=None):
        """rgb: frame (H, W, 3). sovrapposizione: RGBA (H, W, 4) oppure None."""
        h, w = rgb.shape[:2]
        nuovo_formato = (w, h) != (self._w, self._h)
        self._w, self._h = w, h
        self._img.setPixmap(QPixmap.fromImage(qimage_rgb(rgb)))
        if sovrapposizione is not None:
            self._sovr.setPixmap(QPixmap.fromImage(qimage_rgba(sovrapposizione)))
            self._sovr.setVisible(True)
        else:
            self._sovr.setVisible(False)
        self.scene().setSceneRect(QRectF(0, 0, w, h))
        if nuovo_formato or not self._zoom_utente:
            self.adatta()

    def adatta(self):
        if self._w:
            self._zoom_utente = False
            self.fitInView(QRectF(0, 0, self._w, self._h), Qt.KeepAspectRatio)

    def scritta(self, testo):
        """Messaggio in alto a sinistra sul video, su un fondo semitrasparente (leggibile anche sul chiaro)."""
        self._etichetta.setText(testo)
        f = self._etichetta.font()
        f.setPointSizeF(max(10.0, self._h / 48))
        self._etichetta.setFont(f)
        m = f.pointSizeF() * 0.6
        self._etichetta.setPos(12 + m, 10 + m * 0.6)
        r = self._etichetta.boundingRect()
        self._etichetta_fondo.setRect(QRectF(12, 10, r.width() + 2 * m, r.height() + 1.2 * m))
        self._etichetta_fondo.setVisible(bool(testo))

    def disegna(self, punti, zone, mani=None, segni=None):
        """punti: [(x%, y%, lab)], zone: [(x%, y%, l%, a%)], mani: [(x0, y0, lato) in pixel],
        segni: [(x, y) in frazione del frame] = parti del corpo viste ma fuori dalla selezione (cerchio giallo)."""
        for it in self._punti_items + self._zone_items + self._mani_items + self._segni_items:
            self.scene().removeItem(it)
        self._punti_items, self._zone_items, self._mani_items, self._segni_items = [], [], [], []
        if not self._w:
            return
        r = max(6.0, self._w / 140)
        for x, y, lab in punti or []:
            px, py = x * self._w / 100, y * self._h / 100
            col = VERDE if lab else ROSSO
            it = QGraphicsEllipseItem(px - r, py - r, 2 * r, 2 * r)
            it.setBrush(QBrush(col))
            it.setPen(QPen(QColor(255, 255, 255), r / 3))
            it.setZValue(3)
            self.scene().addItem(it)
            self._punti_items.append(it)
            tratti = [((-1, 0), (1, 0))] if not lab else [((-1, 0), (1, 0)), ((0, -1), (0, 1))]   # "−" o "+"
            for (a0, b0), (a1, b1) in tratti:
                lin = self.scene().addLine(px + a0 * r * .6, py + b0 * r * .6, px + a1 * r * .6, py + b1 * r * .6,
                                           QPen(QColor(255, 255, 255), r / 3.5))
                lin.setZValue(4)
                self._punti_items.append(lin)
        for (x, y, lw, lh) in zone or []:
            it = QGraphicsRectItem(x * self._w / 100, y * self._h / 100, lw * self._w / 100, lh * self._h / 100)
            it.setBrush(QBrush(QColor(240, 60, 60, 60)))
            pen = QPen(ROSSO, max(2.0, self._w / 600), Qt.DashLine)
            it.setPen(pen)
            it.setZValue(2)
            self.scene().addItem(it)
            self._zone_items.append(it)
        for (x0, y0, lato) in mani or []:
            it = QGraphicsRectItem(x0, y0, lato, lato)
            it.setPen(QPen(GIALLO, max(2.0, self._w / 700), Qt.DashLine))
            it.setZValue(2)
            self.scene().addItem(it)
            self._mani_items.append(it)
        r2 = max(14.0, self._w / 45)
        for (fx, fy) in segni or []:
            it = QGraphicsEllipseItem(fx * self._w - r2, fy * self._h - r2, 2 * r2, 2 * r2)
            it.setPen(QPen(GIALLO, max(2.5, self._w / 400), Qt.DashLine))
            it.setBrush(QBrush(QColor(255, 205, 40, 40)))
            it.setZValue(3)
            self.scene().addItem(it)
            self._segni_items.append(it)
            q = QGraphicsSimpleTextItem("?")
            q.setBrush(QBrush(GIALLO))
            fq = q.font()
            fq.setPointSizeF(r2 * 0.9)
            fq.setBold(True)
            q.setFont(fq)
            br = q.boundingRect()
            q.setPos(fx * self._w - br.width() / 2, fy * self._h - r2 - br.height())
            q.setZValue(3)
            self.scene().addItem(q)
            self._segni_items.append(q)
        self._punti = list(punti or [])
        self._zone = list(zone or [])

    # --- mouse ------------------------------------------------------------
    def _perc(self, pos):
        p = self.mapToScene(pos)
        return (min(100.0, max(0.0, p.x() * 100 / self._w)), min(100.0, max(0.0, p.y() * 100 / self._h)), p)

    def _vicino(self, scena_p):
        r = max(10.0, self._w / 90)
        for k, (x, y, _) in enumerate(getattr(self, "_punti", [])):
            if (x * self._w / 100 - scena_p.x()) ** 2 + (y * self._h / 100 - scena_p.y()) ** 2 < r * r:
                return k
        return None

    def _zona_sotto(self, scena_p):
        for k, (x, y, lw, lh) in enumerate(getattr(self, "_zone", [])):
            if QRectF(x * self._w / 100, y * self._h / 100, lw * self._w / 100, lh * self._h / 100).contains(scena_p):
                return k
        return None

    def mousePressEvent(self, e):
        if not self._w:
            return
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.ShiftModifier
                                              and self.strumento != "zona"):
            self._pan = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if not self.modifica_attiva or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        x, y, sp = self._perc(e.position().toPoint())
        if self.strumento == "zona":
            k = self._zona_sotto(sp)
            if k is not None and e.modifiers() & Qt.AltModifier:
                self.zona_tolta.emit(k)
                return
            self._trascina = sp
            self._rett = QGraphicsRectItem(QRectF(sp, sp))
            self._rett.setPen(QPen(ROSSO, max(2.0, self._w / 600), Qt.DashLine))
            self._rett.setZValue(6)
            self.scene().addItem(self._rett)
            return
        k = self._vicino(sp)
        if k is not None:
            self.punto_tolto.emit(k)
        else:
            tipo = 1 if self.strumento == "aggiungi" else 0
            if e.modifiers() & Qt.AltModifier:          # ⌥ = lo strumento opposto, al volo
                tipo = 1 - tipo
            self.punto_aggiunto.emit(x, y, tipo)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self._trascina is not None and self._rett is not None:
            sp = self.mapToScene(e.position().toPoint())
            self._rett.setRect(QRectF(self._trascina, sp).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.viewport().setCursor(Qt.CrossCursor)
            return
        if self._trascina is not None and self._rett is not None:
            r = self._rett.rect().intersected(QRectF(0, 0, self._w, self._h))
            self.scene().removeItem(self._rett)
            self._trascina, self._rett = None, None
            if r.width() > self._w * 0.01 and r.height() > self._h * 0.01:
                self.zona_aggiunta.emit(r.x() * 100 / self._w, r.y() * 100 / self._h,
                                        r.width() * 100 / self._w, r.height() * 100 / self._h)
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if not self._w:
            return
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self._zoom_utente = True
        self.scale(f, f)
        if self.transform().m11() < 0.05:
            self.adatta()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self.adatta()
        else:
            super().mouseDoubleClickEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._zoom_utente:
            self.adatta()


def sovrapposizione_maschera(m, colore=(255, 60, 60), opacita=0.45, contorno=True):
    """Maschera 0/255 -> RGBA colorata semitrasparente con il contorno piu' marcato."""
    h, w = m.shape
    out = np.zeros((h, w, 4), np.uint8)
    on = m > 127
    out[on, :3] = colore
    out[on, 3] = int(255 * opacita)
    if contorno:
        bordo = cv2.morphologyEx(on.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        out[bordo, :3] = (255, 255, 255)
        out[bordo, 3] = 230
    return out


__all__ = ["Visore", "sovrapposizione_maschera", "qimage_rgb"]
