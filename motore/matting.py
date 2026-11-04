# -*- coding: utf-8 -*-
"""MatAnyone 2: alfa video con memoria, per il soggetto intero e per le mani."""

import os
import sys
import urllib.request

import cv2
import numpy as np

from .eventi import ErroreMotore
from .filtri import ellisse
from .modelli import CARTELLA_MODELLI
from .traccia import cancello_sam, dispositivo_torch


URL_MATANYONE = "https://github.com/pq-yang/MatAnyone2/releases/download/v1.0.0/matanyone2.pth"




def scarica(url, dest, ev):
    if os.path.isfile(dest) and os.path.getsize(dest) > 1_000_000:
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    ev.log(f"Scarico {os.path.basename(dest)} (solo la prima volta)...")

    def avanz(blocchi, dim, tot):
        if tot > 0:
            ev.progresso("download modello", min(tot, blocchi * dim), tot)

    urllib.request.urlretrieve(url, dest + ".part", avanz)
    os.replace(dest + ".part", dest)
    ev.fine_fase("download modello")
    return dest


def trova_matanyone():
    """Se matanyone2 non e' installato con pip, lo cerca in una cartella clonata da GitHub."""
    import importlib.util
    spec = importlib.util.find_spec("matanyone2")
    if spec is not None and spec.origin:          # pacchetto vero (non una cartella vuota rimasta)
        return
    qui = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cartella dell'app
    for d in (os.environ.get("MATANYONE2_DIR"), os.path.join(qui, "MatAnyone2"),
              os.path.expanduser("~/MatAnyone2")):
        if d and os.path.isfile(os.path.join(d, "matanyone2", "__init__.py")):
            sys.path.insert(0, d)
            return


class Matting:
    """MatAnyone 2: alfa video con memoria. Un 'tratto' = un'inizializzazione con la maschera SAM."""

    def __init__(self, args, ev):
        trova_matanyone()
        try:
            import torch
            from hydra import compose, initialize_config_module
            from hydra.core.global_hydra import GlobalHydra
            from omegaconf import open_dict
            from matanyone2.model.matanyone2 import MatAnyone2
            from matanyone2.inference.inference_core import InferenceCore
        except ImportError as e:
            raise ErroreMotore(f"Manca MatAnyone 2 ({e.name}). Installa:\n"
                     "  pip install ultralytics omegaconf hydra-core huggingface_hub safetensors imageio\n"
                     "  git clone https://github.com/pq-yang/MatAnyone2 ~/MatAnyone2\n"
                     "  pip install --no-deps -e ~/MatAnyone2")
        self.torch = torch
        self.InferenceCore = InferenceCore
        self.dev = torch.device(dispositivo_torch())
        ckpt = scarica(URL_MATANYONE, os.path.join(CARTELLA_MODELLI, "matanyone2.pth"), ev)
        GlobalHydra.instance().clear()
        with initialize_config_module(version_base="1.3.2", config_module="matanyone2.config"):
            cfg = compose(config_name="eval_matanyone_config")
        with open_dict(cfg):
            cfg.model.pretrained_resnet = False      # i pesi arrivano dal checkpoint
            cfg.weights = ckpt
        self.net = MatAnyone2(cfg, single_object=True).to(self.dev).eval()
        self.net.load_weights(torch.load(ckpt, map_location=self.dev))
        self.warmup = 10
        self.proc = None
        # prova lampo sulla GPU (MPS): se qualcosa non e' supportato, passo subito alla CPU
        if self.dev.type != "cpu":
            try:
                img = np.full((256, 256, 3), 128, np.uint8)
                msk = np.zeros((256, 256), np.uint8)
                cv2.circle(msk, (128, 128), 60, 255, -1)
                self.warmup, w0 = 1, self.warmup
                self.avvia(img, msk)
                self.passo(img)
                self.warmup = w0
            except Exception as e:
                ev.log(f"  {self.dev} non ha funzionato con MatAnyone ({e}); uso la CPU (piu' lento)")
                self.dev = torch.device("cpu")
                self.net = MatAnyone2(cfg, single_object=True).to(self.dev).eval()
                self.net.load_weights(torch.load(ckpt, map_location=self.dev))
                self.warmup = 10
            self.proc = None
        ev.log(f"MatAnyone 2 pronto su {self.dev}")

    def _tensore(self, img_u8):
        t = self.torch.from_numpy(np.ascontiguousarray(img_u8)).permute(2, 0, 1).float().div_(255.0)
        return t.to(self.dev)

    def ferma(self):
        self.proc = None

    def nuovo(self, img_u8, maschera_u8, chiusura=10):
        """Nuova 'memoria' (un processore) inizializzata con una maschera 0/255: restituisce (proc, alfa).
        Lo stesso modello serve sia il soggetto intero sia i ritagli ad alta risoluzione delle mani."""
        with self.torch.inference_mode():
            proc = self.InferenceCore(self.net, cfg=self.net.cfg, device=self.dev)
            m = (maschera_u8 > 127).astype(np.float32)
            if chiusura > 0:                                    # chiusura come nello script ufficiale
                m = cv2.erode(cv2.dilate(m, ellisse(chiusura)), ellisse(chiusura))
            img = self._tensore(img_u8)
            proc.step(img, self.torch.from_numpy(m * 255.0).to(self.dev), objects=[1])
            out = None
            for _ in range(self.warmup):
                out = proc.step(img, first_frame_pred=True)
            return proc, proc.output_prob_to_mask(out).float().cpu().numpy()

    def avanti(self, proc, img_u8):
        with self.torch.inference_mode():
            out = proc.step(self._tensore(img_u8))
            return proc.output_prob_to_mask(out).float().cpu().numpy()

    def avvia(self, img_u8, maschera_u8):
        """Primo frame del tratto: la maschera (0/255) dice chi scontornare."""
        self.proc, a = self.nuovo(img_u8, maschera_u8)
        return a

    def passo(self, img_u8):
        return self.avanti(self.proc, img_u8)


def riquadro_mano(t, w, h):
    """(id, cx, cy, lato) normalizzati sull'altezza -> quadrato in pixel pieni, dentro il frame."""
    _, cx, cy, lato = t
    S = int(round(min(lato * h, h, w)))
    x0 = min(max(0, int(round(cx * h - S / 2))), w - S)
    y0 = min(max(0, int(round(cy * h - S / 2))), h - S)
    return x0, y0, S


def passo_mani(mat, stati, attuali, lav, rgb8, w, h, wl, hl, args):
    """Per ogni mano: ritaglio 4K -> lato fisso P -> MatAnyone con memoria propria."""
    ids = {t[0] for t in attuali}
    for tid in list(stati):
        if tid not in ids:
            del stati[tid]                              # traccia finita: libero la memoria
    a_lr = lav.get("alpha_lr")
    if a_lr is None or not attuali:
        return []
    P = args.mani_risoluzione
    out = []
    for t in attuali:
        x0, y0, S = riquadro_mano(t, w, h)
        if S < 48:
            continue
        crop = rgb8[y0:y0 + S, x0:x0 + S]
        cP = cv2.resize(crop, (P, P), interpolation=cv2.INTER_AREA if S > P else cv2.INTER_CUBIC)
        if t[0] not in stati:
            # la maschera di partenza e' l'alfa del soggetto intero (filtrata da SAM) dentro il ritaglio
            a0 = a_lr
            if lav.get("m_png") is not None and not args.no_cancello:
                a0 = a0 * cancello_sam(lav["m_png"], wl, hl)
            kx, ky = P / (S * wl / w), P / (S * hl / h)
            M = np.float32([[kx, 0, -x0 * wl / w * kx], [0, ky, -y0 * hl / h * ky]])
            m0 = cv2.warpAffine(a0.astype(np.float32), M, (P, P), flags=cv2.INTER_LINEAR)
            if (m0 > 0.5).mean() < 0.01:
                continue                                 # nel ritaglio non c'e' (quasi) niente di te
            stati[t[0]], aP = mat.nuovo(cP, (m0 > 0.5).astype(np.uint8) * 255, chiusura=3)
        else:
            aP = mat.avanti(stati[t[0]], cP)
        out.append((x0, y0, S, aP.astype(np.float32)))
    return out
