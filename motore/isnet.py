# -*- coding: utf-8 -*-
"""Motore isnet (rembg), frame per frame: licenze libere, ok per uso commerciale."""

import cv2
import numpy as np

from .filtri import ellisse, guided_filter, pulisci_isole, solo_principale, curva_durezza


MODELLI_ISNET = ["isnet-general-use", "birefnet-general", "birefnet-general-lite", "birefnet-portrait",
                 "u2net_human_seg", "u2net", "birefnet-massive"]


def maschera_isnet(rgb, session, zona, remove):
    """Maschera 0..1 a risoluzione piena; isnet lavora solo dentro la zona (piu' dettaglio)."""
    from PIL import Image
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = zona
    crop = rgb[y0:y1, x0:x1]
    m = remove(Image.fromarray(crop), session=session, only_mask=True, post_process_mask=False)
    m = np.asarray(m, dtype=np.float32) / 255.0
    if m.shape[:2] != crop.shape[:2]:
        m = cv2.resize(m, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
    full = np.zeros((h, w), np.float32)
    full[y0:y1, x0:x1] = m
    return full


def rifinisci_isnet(rgb, alpha, prev, args, w, h):
    if args.precisione > 0:
        r = max(2, int(round(min(w, h) / 1080 * args.precisione)))
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        alpha = np.clip(guided_filter(g, alpha, r, 1e-4), 0, 1)
    alpha = pulisci_isole(alpha, args.pulizia, w * h)
    if args.solo_principale:
        alpha = solo_principale(alpha)
    k = int(round(abs(args.bordo)))
    if k > 0:
        alpha = cv2.erode(alpha, ellisse(2 * k + 1)) if args.bordo < 0 else cv2.dilate(alpha, ellisse(2 * k + 1))
    if args.contrasto != 1:
        alpha = np.clip((alpha - 0.5) * args.contrasto + 0.5, 0, 1)
    alpha = curva_durezza(alpha, args.durezza)
    if args.sfuma > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), args.sfuma)
    if prev is not None and args.stabilizza > 0:
        d = np.abs(alpha - prev)
        k = args.stabilizza * np.clip(1 - d * 4, 0, 1)
        alpha = alpha * (1 - k) + prev * k
    return alpha


def zona_da_sam(m_png, w, h, zona_utente):
    """Per il motore isnet: riquadro (a piena risoluzione) attorno al soggetto trovato da SAM."""
    m = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE)
    x, y, bw, bh = cv2.boundingRect((m > 127).astype(np.uint8))
    if bw == 0 or bh == 0:
        return None
    sx, sy = w / m.shape[1], h / m.shape[0]
    x, y, bw, bh = x * sx, y * sy, bw * sx, bh * sy
    mx, my = bw * 0.15 + 24, bh * 0.15 + 24
    z = [int(max(0, x - mx)), int(max(0, y - my)), int(min(w, x + bw + mx)), int(min(h, y + bh + my))]
    if zona_utente is not None:
        z = [max(z[0], zona_utente[0]), max(z[1], zona_utente[1]),
             min(z[2], zona_utente[2]), min(z[3], zona_utente[3])]
        if z[2] - z[0] < 16 or z[3] - z[1] < 16:
            return None
    return tuple(z)


def filtra_isnet_con_sam(alpha, m_png, zona, w, h):
    """Motore isnet + --segui: via cio' che non e' il soggetto, niente buchi su di lui."""
    ms = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    bh = (zona[3] - zona[1]) * ms.shape[1] / w
    k = max(3, int(bh * 0.03)) | 1
    gate = cv2.GaussianBlur(cv2.dilate(ms, ellisse(k)), (0, 0), k / 3.0)
    interno = cv2.erode((ms > 0.5).astype(np.float32), ellisse(max(1, int(bh * 0.015)) | 1))
    x0, y0, x1, y1 = zona
    g = cv2.resize(gate, (w, h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
    i_ = cv2.resize(interno, (w, h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
    out = np.zeros_like(alpha)
    out[y0:y1, x0:x1] = np.maximum(alpha[y0:y1, x0:x1] * np.clip(g * 1.5, 0, 1), (i_ > 0.5).astype(np.float32))
    return out
