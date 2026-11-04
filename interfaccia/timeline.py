# -*- coding: utf-8 -*-
"""Timeline: miniature, tratto (IN/OUT), fotogrammi chiave, soggetto, mani, file esportati, punti da
controllare (⚠), testina. I colori seguono il tema di sistema (chiaro o scuro)."""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

RIGHE = ["chiavi", "soggetto", "mani", "file"]


def _colori(pal):
    finestra = pal.color(QPalette.Window)
    scuro = finestra.lightness() < 128
    sfondo = finestra.darker(135 if scuro else 108)
    return {
        "sfondo": sfondo,
        "riga": sfondo.lighter(135) if scuro else sfondo.darker(108),
        "testo": pal.color(QPalette.WindowText),
        "tenue": pal.color(QPalette.PlaceholderText),
        "accento": QColor(255, 196, 0) if scuro else QColor(225, 150, 0),
        "verde": QColor(40, 200, 90) if scuro else QColor(30, 150, 65),
        "rosso": QColor(150, 50, 50) if scuro else QColor(200, 90, 90),
        "grigio": QColor(120, 120, 128) if scuro else QColor(150, 150, 158),
        "vecchio": QColor(105, 105, 112) if scuro else QColor(175, 175, 182),
        "blu": QColor(70, 150, 255) if scuro else QColor(20, 110, 230),
        "arancio": QColor(255, 140, 40) if scuro else QColor(225, 110, 10),
        "ombra": QColor(0, 0, 0, 150) if scuro else QColor(255, 255, 255, 170),
    }


class Timeline(QWidget):
    vai_a = Signal(int)                 # frame
    tratto_cambiato = Signal(int, int)  # in, out
    ridimensionata = Signal()

    H_RIGHELLO, H_MINI, H_RIGA = 20, 46, 14
    MARG_SX, MARG_DX = 62, 14

    SPIEGA_RIGHE = {
        "chiavi": "Fotogrammi chiave: da dove il programma riparte.\n◆ verde = selezione · ◆ arancio = correzione · "
                  "◆ grigio = soggetto assente",
        "soggetto": "Dove il soggetto e' stato seguito.\nverde = trovato · rosso = non trovato · grigio = tracciamento "
                    "vecchio, da aggiornare · linea sottile = da seguire\n⚠ = punto da controllare (tasto S per andarci)",
        "mani": "Dove ci sono mani rielaborate ad alta risoluzione (solo persone)",
        "file": "File esportati.\nblu = gia' esportato e aggiornato · arancio = da rifare (cambiato dopo l'esportazione)",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.H_RIGHELLO + self.H_MINI + len(RIGHE) * self.H_RIGA + 22)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.n, self.fps = 0, 25.0
        self.cur, self.in_f, self.out_f = 0, 0, 0
        self.tappe = {}                  # frame -> "selezione" | "correzione" | "assente"
        self.trovato = None              # int8 per frame: -2 fuori, -1 da seguire, 0 non trovato, 1 trovato, 2 vecchio
        self.mani = None                 # bool per frame
        self.file = None                 # int8 per frame: -1 niente, 0 da rifare, 1 esportato e aggiornato
        self.sospetti = []               # [(frame_da, frame_a, motivo)] (frame del video)
        self.miniature = []              # [(posizione, QImage)]
        self._trascina = None
        self.zoom_tratto = False         # True: la timeline mostra solo il tratto (con un po' di margine)

    # --- dati -------------------------------------------------------------
    def imposta(self, n, fps):
        self.n, self.fps = n, fps
        self.in_f, self.out_f = 0, max(0, n - 1)
        self.update()

    def _vista(self):
        if self.zoom_tratto and self.n > 1:
            m = max(3, int((self.out_f - self.in_f) * 0.08))
            return max(0, self.in_f - m), min(self.n - 1, self.out_f + m)
        return 0, max(1, self.n - 1)

    def _x(self, f):
        v0, v1 = self._vista()
        return self.MARG_SX + (self.width() - self.MARG_SX - self.MARG_DX) * ((f - v0) / max(1, v1 - v0))

    def _f(self, x):
        v0, v1 = self._vista()
        t = min(max(0.0, (x - self.MARG_SX) / max(1, self.width() - self.MARG_SX - self.MARG_DX)), 1.0)
        return int(round(v0 + t * (v1 - v0)))

    def _y_riga(self, k):
        return self.H_RIGHELLO + self.H_MINI + 2 + k * self.H_RIGA

    # --- disegno ----------------------------------------------------------
    def paintEvent(self, _):
        c = _colori(self.palette())
        p = QPainter(self)
        p.fillRect(self.rect(), c["sfondo"])
        if self.n <= 0:
            p.setPen(c["tenue"])
            p.drawText(self.rect(), Qt.AlignCenter, "Apri un video (File › Apri, oppure trascinalo nella finestra)")
            return
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        y0 = self.H_RIGHELLO
        nr = len(RIGHE)
        p.setFont(QFont(p.font().family(), 9))
        v0, v1 = self._vista()
        p.save()
        p.setClipRect(QRectF(self.MARG_SX - 2, 0, w - self.MARG_SX - self.MARG_DX + 4, self.height()))
        # righello in secondi
        dur = (v1 - v0) / self.fps
        passo = next((s for s in (0.04, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600)
                      if dur / s <= max(4, w // 90)), 600)
        t = (v0 / self.fps // passo) * passo
        while t <= v1 / self.fps:
            x = self._x(t * self.fps)
            p.setPen(c["grigio"])
            p.drawLine(int(x), y0 - 6, int(x), y0)
            p.setPen(c["tenue"])
            p.drawText(int(x) + 3, y0 - 7, f"{t:.2f}".rstrip("0").rstrip(".") + "s")
            t += passo
        # miniature
        p.fillRect(QRectF(self._x(v0), y0, self._x(v1) - self._x(v0), self.H_MINI), c["riga"])
        for f, img in self.miniature:
            iw = img.width() * self.H_MINI / max(1, img.height())
            p.drawImage(QRectF(self._x(f) - iw / 2, y0, iw, self.H_MINI), img)
        # righe
        for k in range(nr):
            p.fillRect(QRectF(self._x(v0), self._y_riga(k), self._x(v1) - self._x(v0), self.H_RIGA - 2), c["riga"])
        if self.trovato is not None:
            r = self._y_riga(1)
            self._barre(p, self.trovato == -1, r + 5, 2, c["grigio"])
            self._barre(p, self.trovato == 2, r + 2, self.H_RIGA - 6, c["vecchio"])
            self._barre(p, self.trovato == 0, r + 2, self.H_RIGA - 6, c["rosso"])
            self._barre(p, self.trovato == 1, r + 2, self.H_RIGA - 6, c["verde"])
        if self.mani is not None:
            self._barre(p, self.mani, self._y_riga(2) + 2, self.H_RIGA - 6, c["accento"])
        if self.file is not None:
            self._barre(p, self.file == 0, self._y_riga(3) + 2, self.H_RIGA - 6, c["arancio"])
            self._barre(p, self.file == 1, self._y_riga(3) + 2, self.H_RIGA - 6, c["blu"])
        # punti da controllare: triangolo sopra la riga "soggetto" + linea per la durata
        for a, b, _ in self.sospetti:
            xa, xb = self._x(a), self._x(b)
            ys = self._y_riga(1)
            p.setPen(QPen(c["arancio"], 2))
            p.drawLine(QPointF(xa, ys + self.H_RIGA - 3), QPointF(max(xb, xa + 2), ys + self.H_RIGA - 3))
            p.setBrush(c["arancio"])
            p.setPen(QPen(c["sfondo"], 1))
            p.drawPolygon(QPolygonF([QPointF(xa, ys - 1), QPointF(xa - 6, ys + 9), QPointF(xa + 6, ys + 9)]))
            p.setPen(QPen(QColor(20, 20, 20), 1.4))
            p.drawLine(QPointF(xa, ys + 2.5), QPointF(xa, ys + 6))
            p.drawPoint(QPointF(xa, ys + 7.6))
        # fotogrammi chiave
        colore_chiave = {"selezione": c["verde"], "correzione": c["arancio"], "assente": c["grigio"]}
        for f, tipo in sorted(self.tappe.items()):
            x = self._x(f)
            p.setBrush(colore_chiave.get(tipo, c["verde"]))
            p.setPen(QPen(c["sfondo"], 1))
            cy = self._y_riga(0) + (self.H_RIGA - 2) / 2
            p.drawPolygon(QPolygonF([QPointF(x, cy - 6), QPointF(x + 6, cy), QPointF(x, cy + 6), QPointF(x - 6, cy)]))
        # fuori dal tratto: velato
        p.fillRect(QRectF(0, y0, self._x(self.in_f), self.H_MINI + nr * self.H_RIGA + 6), c["ombra"])
        p.fillRect(QRectF(self._x(self.out_f), y0, w - self._x(self.out_f), self.H_MINI + nr * self.H_RIGA + 6),
                   c["ombra"])
        # maniglie IN / OUT
        for f, nome in ((self.in_f, "IN"), (self.out_f, "OUT")):
            x = self._x(f)
            p.setPen(QPen(c["blu"], 2))
            p.drawLine(int(x), y0, int(x), y0 + self.H_MINI + nr * self.H_RIGA)
            p.setBrush(c["blu"])
            p.setPen(Qt.NoPen)
            r = QRectF(x - (26 if nome == "OUT" else 0), y0 + self.H_MINI - 13, 26, 13)
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r.adjusted(0, -1, 0, -1), Qt.AlignCenter, nome)
        p.restore()
        # etichette delle righe (nel margine sinistro)
        p.setPen(c["tenue"])
        for k, nome in enumerate(RIGHE):
            p.drawText(QRectF(4, self._y_riga(k) - 1, self.MARG_SX - 8, self.H_RIGA), Qt.AlignVCenter | Qt.AlignRight,
                       nome)
        p.drawText(QRectF(4, y0, self.MARG_SX - 8, self.H_MINI), Qt.AlignVCenter | Qt.AlignRight,
                   "tratto" if self.zoom_tratto else "video")
        # testina
        x = self._x(self.cur)
        p.setPen(QPen(c["accento"], 2))
        p.drawLine(int(x), 2, int(x), self.height() - 4)
        p.setBrush(c["accento"])
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 2), QPointF(x + 6, 2), QPointF(x, 10)]))
        # tempo corrente
        p.setPen(c["testo"])
        testo = (f"{self.cur / self.fps:6.2f}s  ·  frame {self.cur}  ·  tratto {self.in_f / self.fps:.2f}–"
                 f"{(self.out_f + 1) / self.fps:.2f}s")
        if self.sospetti:
            testo += f"  ·  ⚠ {len(self.sospetti)} da controllare"
        p.drawText(QRectF(10, self.height() - 18, w - 20, 16), Qt.AlignLeft | Qt.AlignVCenter, testo)

    def _barre(self, p, arr, y, h, col):
        """Disegna i tratti consecutivi True come barre."""
        idx = np.flatnonzero(np.diff(np.concatenate([[0], np.asarray(arr).astype(np.int8), [0]])))
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        for a, b in zip(idx[::2], idx[1::2]):
            x0, x1 = self._x(a) - 0.5, self._x(b - 1) + 0.5
            p.drawRect(QRectF(x0, y, max(1.5, x1 - x0), h))

    # --- interazione --------------------------------------------------------
    def _maniglia(self, x, y):
        ym = self.H_RIGHELLO + self.H_MINI
        if not (ym - 18 <= y <= ym + 2):
            return None
        if abs(x - self._x(self.in_f) - 13) < 15:
            return "in"
        if abs(x - self._x(self.out_f) + 13) < 15:
            return "out"
        return None

    def _sospetto_vicino(self, x, y):
        ys = self._y_riga(1)
        if not (ys - 4 <= y <= ys + self.H_RIGA):
            return None
        for a, b, motivo in self.sospetti:
            if self._x(a) - 7 <= x <= max(self._x(b), self._x(a)) + 7:
                return a, b, motivo
        return None

    def mousePressEvent(self, e):
        if self.n <= 0:
            return
        x, y = e.position().x(), e.position().y()
        s = self._sospetto_vicino(x, y)
        if s is not None:                                # clic su un ⚠: vai al suo primo frame
            self._trascina = None
            self.vai_a.emit(s[0])
            return
        self._trascina = self._maniglia(x, y) or "testina"
        self._muovi(x)

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._trascina:
            self._muovi(x)
            return
        self.setCursor(Qt.SizeHorCursor if self._maniglia(x, y) else Qt.ArrowCursor)
        s = self._sospetto_vicino(x, y)
        if s is not None:
            QToolTip.showText(e.globalPosition().toPoint(), f"⚠ {s[2]}\n(clic per andarci)", self)
            return
        k = int((y - self.H_RIGHELLO - self.H_MINI - 2) // self.H_RIGA)
        testo = self.SPIEGA_RIGHE[RIGHE[k]] if (0 <= k < len(RIGHE) and y > self.H_RIGHELLO + self.H_MINI) else (
            "Trascina IN / OUT per scegliere il tratto da scontornare. Clic = sposta la testina." if y > self.H_RIGHELLO
            else "")
        if testo != self.toolTip():
            self.setToolTip(testo)

    def mouseReleaseEvent(self, e):
        if self._trascina in ("in", "out"):
            self.tratto_cambiato.emit(self.in_f, self.out_f)
        self._trascina = None

    def _muovi(self, x):
        f = self._f(x)
        if self._trascina == "in":
            self.in_f = min(f, self.out_f)
            self.update()
        elif self._trascina == "out":
            self.out_f = max(f, self.in_f)
            self.update()
        else:
            self.vai_a.emit(f)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ridimensionata.emit()

    def keyPressEvent(self, e):
        self.parent().keyPressEvent(e) if self.parent() else super().keyPressEvent(e)


def miniatura(rgb, altezza=46):
    h, w = rgb.shape[:2]
    import cv2
    piccola = cv2.resize(rgb, (max(1, int(w * altezza / h)), altezza), interpolation=cv2.INTER_AREA)
    piccola = np.ascontiguousarray(piccola)
    return QImage(piccola.data, piccola.shape[1], piccola.shape[0], 3 * piccola.shape[1],
                  QImage.Format_RGB888).copy()


__all__ = ["Timeline", "miniatura"]
