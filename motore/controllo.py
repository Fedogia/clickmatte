# -*- coding: utf-8 -*-
"""Controllo del tracciamento: trova i frame "sospetti", dove conviene guardare (e magari correggere).

Per ogni frame tracciato si salva una piccola misura (area, centro, sagoma in miniatura, parti del corpo
rimaste fuori). Poi, confrontando frame vicini dello stesso pezzo, si segnalano:
  - la selezione che si riduce o cresce di colpo (un braccio perso, un pezzo di sfondo preso);
  - la forma che cambia di colpo anche a parita' di area (si e' "spostata" su un'altra cosa);
  - il soggetto che sparisce o ricompare a meta' di un pezzo;
  - (persone) una mano, un gomito, la testa... visti dal riconoscimento della posa ma fuori dalla
    selezione: e' il caso del braccio che rientra nell'inquadratura e non viene preso.
I segnali vicini si raggruppano: un segnale per ogni punto da controllare, sul suo primo frame.
"""

import cv2
import numpy as np

LATO_MISURA = 64
SOGLIA_CALO = 0.78          # area / area del frame prima: sotto -> "si riduce di colpo"
SOGLIA_CRESCITA = 1.28
SOGLIA_FORMA = 0.72         # somiglianza della sagoma (a centri allineati): sotto -> "la forma cambia di colpo"
SOGLIA_POSA = 0.6           # fiducia minima di un punto del corpo per dire "e' fuori dalla selezione"
NOMI_POSA = {0: "la testa", 7: "un gomito", 8: "un gomito", 9: "una mano", 10: "una mano",
             13: "un ginocchio", 14: "un ginocchio", 15: "un piede", 16: "un piede"}


def misura(m_png, kp=None):
    """Misura compatta di un frame tracciato (None se il soggetto non c'e').
    f = parti del corpo fuori dalla selezione: [(indice, x, y)] con x, y in frazione del frame."""
    if m_png is None:
        return None
    m = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE)
    if m is None:
        return None
    m = (m > 127).astype(np.uint8)
    h, w = m.shape
    mo = cv2.moments(m, binaryImage=True)
    if mo["m00"] <= 0:
        return None
    hh = max(1, int(round(LATO_MISURA * h / w)))
    piccola = cv2.resize(m * 255, (LATO_MISURA, hh), interpolation=cv2.INTER_AREA) > 127
    fuori = []
    if kp is not None:
        _, _, _, alto = cv2.boundingRect(m)
        r = max(6, int(0.04 * alto))
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        disco = (xx * xx + yy * yy) <= r * r
        for j in NOMI_POSA:
            x, y, c = (float(v) for v in kp[j])
            xi, yi = int(x), int(y)
            if c < SOGLIA_POSA or not (0 <= xi < w and 0 <= yi < h):
                continue
            y0, y1, x0, x1 = max(0, yi - r), min(h, yi + r + 1), max(0, xi - r), min(w, xi + r + 1)
            vicino = m[y0:y1, x0:x1].astype(bool) & disco[y0 - yi + r:y1 - yi + r, x0 - xi + r:x1 - xi + r]
            if not vicino.any():                         # nessun pixel selezionato vicino al punto
                fuori.append((int(j), round(x / w, 4), round(y / h, 4)))
    return {"a": float(mo["m00"] / (h * w)), "c": (float(mo["m10"] / mo["m00"] / w), float(mo["m01"] / mo["m00"] / h)),
            "p": np.packbits(piccola), "s": piccola.shape, "f": fuori}


def misure(maschere, pose):
    pose = pose or [None] * len(maschere)
    return [misura(m, k) for m, k in zip(maschere, pose)]


def _sagoma(mi):
    n = mi["s"][0] * mi["s"][1]
    return np.unpackbits(mi["p"])[:n].reshape(mi["s"]).astype(np.uint8)


def somiglianza(m1, m2):
    """Quanto due sagome si somigliano (0..1) dopo averle centrate: ignora gli spostamenti della camera."""
    a, b = _sagoma(m1), _sagoma(m2)
    if a.shape != b.shape:
        return 1.0
    h, w = a.shape
    dx = (m1["c"][0] - m2["c"][0]) * w
    dy = (m1["c"][1] - m2["c"][1]) * h
    b = cv2.warpAffine(b, np.float32([[1, 0, dx], [0, 1, dy]]), (w, h), flags=cv2.INTER_NEAREST)
    unione = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / unione) if unione else 1.0


def sospetti(misure_frame, inizi, pronto):
    """Punti da controllare: [(frame, ultimo_frame, motivo)], frame relativi al tratto."""
    n = len(misure_frame)
    inizi = set(inizi)
    segnali = {}                                         # frame -> [motivi]

    def segna(i, motivo):
        segnali.setdefault(i, [])
        if motivo not in segnali[i]:
            segnali[i].append(motivo)

    for i in range(n):
        mi = misure_frame[i]
        if not pronto[i]:
            continue
        if i > 0 and i not in inizi and pronto[i - 1]:
            mp = misure_frame[i - 1]
            if mp is not None and mi is None:
                segna(i, "il soggetto sparisce")
            elif mp is None and mi is not None:
                segna(i, "il soggetto ricompare")
            elif mp is not None and mi is not None:
                r = mi["a"] / max(mp["a"], 1e-6)
                if r < SOGLIA_CALO:
                    segna(i, f"la selezione si riduce di colpo ({(r - 1) * 100:+.0f}%)")
                elif r > SOGLIA_CRESCITA:
                    segna(i, f"la selezione cresce di colpo ({(r - 1) * 100:+.0f}%)")
                elif somiglianza(mi, mp) < SOGLIA_FORMA:
                    segna(i, "la forma cambia di colpo")
        if mi is not None and mi.get("f"):
            nomi = sorted({NOMI_POSA[f[0]] for f in mi["f"]})
            segna(i, " e ".join(nomi) + (" fuori dalla selezione" if len(nomi) == 1 else " fuori dalla selezione"))
    # raggruppo i segnali vicini (al massimo 2 frame di distanza): uno per punto da controllare
    out = []
    for i in sorted(segnali):
        if out and i - out[-1][1] <= 2:
            primo, _, motivi = out[-1]
            for mo in segnali[i]:
                base = mo.split(" (")[0]
                if not any(x.split(" (")[0] == base for x in motivi):
                    motivi.append(mo)
            out[-1] = (primo, i, motivi)
        else:
            out.append((i, i, list(segnali[i])))
    return [(a, b, "; ".join(m[:3])) for a, b, m in out]


__all__ = ["misura", "misure", "sospetti", "somiglianza", "NOMI_POSA"]
