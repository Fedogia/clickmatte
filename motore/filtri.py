# -*- coding: utf-8 -*-
"""Operazioni sulle maschere/alfa: filtri guidati, colore dei bordi, curve, finestre."""

import cv2
import numpy as np

from .eventi import ErroreMotore


def rect_pct(txt, w, h, nome):
    try:
        x, y, rw, rh = [float(v) for v in txt.split(",")]
    except Exception:
        raise ErroreMotore(f"--{nome} vuole 4 numeri in %: x,y,larghezza,altezza  (es. 25,5,55,95)")
    x0, y0 = int(round(w * x / 100)), int(round(h * y / 100))
    x1, y1 = int(round(w * (x + rw) / 100)), int(round(h * (y + rh) / 100))
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def ellisse(k):
    k = max(1, int(k))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def box_filter(a, r):
    return cv2.boxFilter(a, -1, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_REFLECT)


def guided_filter(guida, p, r, eps):
    """Guided filter (He et al.) con guida in scala di grigi."""
    I = guida
    mI, mp = box_filter(I, r), box_filter(p, r)
    cov = box_filter(I * p, r) - mI * mp
    var = box_filter(I * I, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return box_filter(a, r) * I + box_filter(b, r)


def su_guidato(I_hr, p_lr, r=2, eps=1e-3):
    """Porta l'alfa da bassa ad alta risoluzione agganciandola ai bordi A COLORI dell'immagine 4K.

    Fast guided filter (He & Sun 2015) con guida RGB: i coefficienti lineari si calcolano a
    bassa risoluzione, si ingrandiscono e si applicano all'immagine piena. Con la guida a colori
    distingue anche bordi con la stessa luminosita' (maglia rossa su fondo grigio).
    """
    hl, wl = p_lr.shape
    Hh, Wh = I_hr.shape[:2]
    I = cv2.resize(I_hr, (wl, hl), interpolation=cv2.INTER_AREA)
    m = [box_filter(I[..., c], r) for c in range(3)]
    mp = box_filter(p_lr, r)
    cv_ = [box_filter(I[..., c] * p_lr, r) - m[c] * mp for c in range(3)]

    def var(i, j):
        return box_filter(I[..., i] * I[..., j], r) - m[i] * m[j]

    s00, s01, s02 = var(0, 0) + eps, var(0, 1), var(0, 2)
    s11, s12, s22 = var(1, 1) + eps, var(1, 2), var(2, 2) + eps
    # inversa della matrice 3x3 simmetrica (cofattori), pixel per pixel
    i00 = s11 * s22 - s12 * s12
    i01 = s12 * s02 - s01 * s22
    i02 = s01 * s12 - s11 * s02
    i11 = s00 * s22 - s02 * s02
    i12 = s02 * s01 - s00 * s12
    i22 = s00 * s11 - s01 * s01
    det = s00 * i00 + s01 * i01 + s02 * i02
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    a0 = (i00 * cv_[0] + i01 * cv_[1] + i02 * cv_[2]) / det
    a1 = (i01 * cv_[0] + i11 * cv_[1] + i12 * cv_[2]) / det
    a2 = (i02 * cv_[0] + i12 * cv_[1] + i22 * cv_[2]) / det
    b = mp - a0 * m[0] - a1 * m[1] - a2 * m[2]
    out = np.zeros((Hh, Wh), np.float32)
    for a_c, c in ((a0, 0), (a1, 1), (a2, 2)):
        out += cv2.resize(box_filter(a_c, r), (Wh, Hh), interpolation=cv2.INTER_LINEAR) * I_hr[..., c]
    out += cv2.resize(box_filter(b, r), (Wh, Hh), interpolation=cv2.INTER_LINEAR)
    return np.clip(out, 0, 1)


def pulisci_isole(alpha, frac, area_frame=None):
    if frac <= 0:
        return alpha
    h, w = alpha.shape
    bin_ = (alpha > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bin_, 8)
    if n <= 2:
        return alpha
    soglia = frac / 100.0 * (area_frame or w * h)
    tieni = np.zeros(n, bool)
    tieni[1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))] = True
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= soglia:
            tieni[i] = True
    via = (~tieni[lab]) & (bin_ > 0)
    if via.any():
        via = cv2.dilate(via.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        alpha = alpha.copy()
        alpha[via] = 0
    return alpha


def solo_principale(alpha):
    """Tiene solo l'isola piu' grande + un margine morbido (via anche gli aloni sotto 0.5)."""
    bin_ = (alpha > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bin_, 8)
    if n <= 1:
        return alpha
    tieni = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    r = max(15, int(min(alpha.shape) * 0.02))
    tieni = cv2.dilate(tieni, ellisse(2 * r + 1))
    return alpha * cv2.GaussianBlur(tieni.astype(np.float32), (0, 0), r / 3.0)


def decontamina(rgb, alpha):
    """Colore vero del soggetto nei pixel semitrasparenti (rgb float 0..255 -> 0..255).

    "Blur fusion" (Forte & Pitie, 2021): stima localmente il colore medio del soggetto e
    quello dello sfondo e ricava il colore del soggetto pixel per pixel. Dove l'alfa e' 1
    il colore resta identico; sui bordi mossi (dita, capelli) non 'trasparisce' piu' lo sfondo.
    """
    h, w = alpha.shape
    r = int(np.clip(0.08 * max(h, w), 45, 151)) | 1
    a = alpha.astype(np.float32)
    I = rgb.astype(np.float32) / 255.0
    A = a[..., None]

    def passo(F, B, rr):
        ba = cv2.blur(a, (rr, rr))[..., None]
        bF = cv2.blur(F * A, (rr, rr)) / (ba + 1e-5)
        bB = cv2.blur(B * (1 - A), (rr, rr)) / ((1 - ba) + 1e-5)
        return np.clip(bF + A * (I - A * bF - (1 - A) * bB), 0, 1), bB

    F, bB = passo(I, I, r)
    F, _ = passo(F, bB, 7)
    return F * 255.0


def curva_durezza(alpha, d):
    """Curva a S: il corpo diventa piu' solido ma le sfumature (capelli) restano graduali."""
    if d <= 0:
        return alpha
    lo, hi = 0.35 * d, 1 - 0.35 * d
    t = np.clip((alpha - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def bordo_e_sfuma(alpha, args):
    k = int(round(abs(args.bordo)))
    if k > 0:
        alpha = cv2.erode(alpha, ellisse(2 * k + 1)) if args.bordo < 0 else cv2.dilate(alpha, ellisse(2 * k + 1))
    alpha = curva_durezza(alpha, args.durezza)
    if args.sfuma > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), args.sfuma)
    return alpha


def scacchiera(h, w, lato=24):
    yy, xx = np.mgrid[0:h, 0:w]
    c = ((yy // lato + xx // lato) % 2).astype(np.float32)
    s = np.empty((h, w, 3), np.float32)
    s[:] = 90
    s[c > 0] = 140
    return s


def finestra(S, margine=0.18):
    """Peso 1 al centro, 0 sui bordi del ritaglio: niente cuciture fra mano e corpo."""
    t = np.clip(np.arange(S, dtype=np.float32) / max(1.0, margine * S), 0, 1)
    t = np.minimum(t, t[::-1])
    t = t * t * (3 - 2 * t)
    return np.outer(t, t)
