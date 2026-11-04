# -*- coding: utf-8 -*-
"""Selezione su un fotogramma: da clic "aggiungi" / "togli" a una maschera.

E' la stessa funzione per l'interfaccia (clic nel visore) e per il motore (opzione --segui da
terminale): cio' che vedi selezionato e' esattamente cio' da cui parte il tracciamento.

Regole (pensate per qualsiasi soggetto: persona, oggetto, animale, persona con oggetto in mano):
  1. tutti i clic "aggiungi" insieme -> una selezione (SAM capisce meglio con piu' punti); in una
     correzione si aggiungono due punti della maschera di partenza, cosi' braccio e oggetto in mano
     vengono presi come parte dello stesso soggetto;
  2. ogni clic "aggiungi" viene selezionato anche da solo e unito se e' una parte piccola (il telefono
     in mano) o se era rimasto fuori: nessun clic va perso;
  3. ogni clic "togli" che cade dentro la selezione toglie la parte su cui sta (es. una borsa),
     chiedendo a SAM "questo, ma non il resto";
  4. si puo' partire da una maschera esistente (correzione di un fotogramma gia' tracciato):
     i clic aggiungono o tolgono parti a quella.
"""

import cv2
import numpy as np

from .modelli import percorso_modello

VERSIONE_SELEZIONE = "sel-2"          # cambia quando cambiano le regole: le selezioni salvate si ricalcolano


def _dentro(m, x, y, r=3):
    h, w = m.shape
    x, y = int(round(x)), int(round(y))
    return bool(m[max(0, y - r):min(h, y + r + 1), max(0, x - r):min(w, x + r + 1)].any())


def _punti_interni(m, lontano_da, k=2):
    """Fino a k punti ben dentro la maschera, lontani dal clic (servono come 'non questo')."""
    d = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
    h, w = m.shape
    raggio = 0.12 * max(h, w)
    yy, xx = np.ogrid[:h, :w]
    for (x, y) in lontano_da:
        d[(xx - x) ** 2 + (yy - y) ** 2 < raggio * raggio] = 0
    out = []
    for _ in range(k):
        i = int(np.argmax(d))
        y, x = divmod(i, w)
        if d[y, x] < 4:
            break
        out.append((float(x), float(y)))
        d[(xx - x) ** 2 + (yy - y) ** 2 < raggio * raggio] = 0
    return out


class Selettore:
    """SAM 2 su un'immagine. L'analisi dell'immagine si fa una volta sola: i clic successivi
    sullo stesso fotogramma costano pochi centesimi di secondo."""

    def __init__(self, modello="sam2.1_t.pt", imgsz=1024, dev=None):
        self.modello, self.imgsz = modello, int(imgsz)
        if dev is None:
            from .traccia import dispositivo_torch
            dev = dispositivo_torch()
        self.dev = dev
        self._p = None
        self._chiave = None
        self._dim = None

    def _crea(self):
        from ultralytics.models.sam import SAM2Predictor
        self._p = SAM2Predictor(overrides=dict(conf=0.25, task="segment", mode="predict", imgsz=self.imgsz,
                                               model=percorso_modello(self.modello), device=self.dev,
                                               verbose=False, save=False))

    def imposta(self, bgr, chiave=None):
        """Analizza l'immagine (BGR). Con la stessa chiave non rifa' il lavoro."""
        if chiave is not None and chiave == self._chiave and self._p is not None:
            return
        if self._p is None:
            self._crea()
        try:
            self._p.set_image(bgr)
        except Exception:
            if self.dev == "cpu":
                raise
            self.dev = "cpu"                          # qualcosa non va sulla GPU: ripiego sulla CPU
            self._crea()
            self._p.set_image(bgr)
        self._chiave = chiave
        self._dim = bgr.shape[:2]

    def _sam(self, pos, neg):
        h, w = self._dim
        pts = [list(p) for p in pos] + [list(p) for p in neg]
        lab = [1] * len(pos) + [0] * len(neg)
        r = self._p(points=[pts], labels=[lab])[0]
        if r.masks is None or not len(r.masks):
            return np.zeros((h, w), bool)
        m = r.masks.data[0].cpu().numpy() > 0.5
        if m.shape != (h, w):
            m = cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        return m

    def calcola(self, bgr, base=None, aggiungi=(), togli=(), chiave=None):
        """bgr: immagine. base: maschera di partenza (0/255, qualsiasi dimensione) o None.
        aggiungi / togli: punti in % [(x, y), ...]. Restituisce una maschera uint8 0/255."""
        self.imposta(bgr, chiave)
        h, w = self._dim
        px = lambda p: (float(p[0]) * w / 100.0, float(p[1]) * h / 100.0)   # noqa: E731
        add = [px(p) for p in aggiungi]
        rem = [px(p) for p in togli]
        if base is not None:
            m = base > 127
            if m.shape != (h, w):
                m = cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
            m = m.copy()
        else:
            m = np.zeros((h, w), bool)
        if add:
            # 1) tutti insieme; in una correzione anche due punti della maschera di partenza, cosi' SAM
            #    capisce che il clic fa parte dello stesso soggetto (es. braccio + telefono attaccati al corpo)
            contesto = _punti_interni(m, add + rem, k=2) if m.any() else []
            m |= self._sam(add + contesto, rem)
            for p in add:                             # 2) ogni clic anche da solo: le parti piccole (un
                parte = self._sam([p], rem)           #    oggetto in mano) si aggiungono intere; le grandi
                if not _dentro(m, *p) or parte.sum() < 0.5 * max(1, m.sum()):   # solo se erano rimaste fuori
                    m |= parte
        for t in rem:                                 # 3) togli la parte sotto ogni clic "togli"
            if not _dentro(m, *t, r=0):
                continue
            parte = self._sam([t], _punti_interni(m, [t]))
            if _dentro(parte, *t, r=1) and parte.sum() < 0.97 * m.sum():
                m &= ~parte
        return (m.astype(np.uint8) * 255)


def altezza_e_area(m):
    """Quanto e' alto il soggetto (frazione del frame) e quanta superficie occupa."""
    if not m.any():
        return 0.0, 0.0
    x, y, bw, bh = cv2.boundingRect((m > 127).astype(np.uint8))
    return round(bh / m.shape[0], 3), round(float((m > 127).mean()), 4)


__all__ = ["Selettore", "altezza_e_area"]
