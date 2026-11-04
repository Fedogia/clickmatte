# -*- coding: utf-8 -*-
"""Dove stanno i pesi dei modelli e i dati di lavoro: fuori dalle tue cartelle."""

import hashlib
import os
import sys

CARTELLA_MODELLI = os.path.expanduser("~/.cache/scontorno")


def percorso_modello(nome):
    """'sam2.1_t.pt' -> ~/.cache/scontorno/sam2.1_t.pt (ultralytics lo scarica li' se manca)."""
    if os.path.isabs(nome) or os.path.dirname(nome):
        return nome
    os.makedirs(CARTELLA_MODELLI, exist_ok=True)
    return os.path.join(CARTELLA_MODELLI, nome)


def cartella_lavoro(video):
    """Cartella di lavoro di un video (copia leggera, tracciamento, stato dei file esportati).

    Una per video, uguale per l'interfaccia e per il terminale: cosi' il lavoro fatto da una parte
    vale anche dall'altra. Sul Mac sta in ~/Library/Caches/Scontorno (si puo' cancellare quando vuoi:
    si perde solo il lavoro gia' calcolato, non i file esportati).
    """
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches/Scontorno")
    else:
        base = os.path.expanduser("~/.cache/scontorno-app")
    st = os.stat(video)
    h = hashlib.sha1(f"{os.path.abspath(video)}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()[:16]
    d = os.path.join(base, h)
    os.makedirs(d, exist_ok=True)
    return d
