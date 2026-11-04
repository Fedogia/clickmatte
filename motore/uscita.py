# -*- coding: utf-8 -*-
"""Rifinitura finale e scrittura dei file (thread separato, in parallelo al modello)."""

import os
import queue
import threading

import cv2
import numpy as np

from .filtri import rect_pct, scacchiera, su_guidato, finestra, bordo_e_sfuma, decontamina
from .traccia import cancello_sam, cancello_ritaglio
from .video import scrittore, prova_prores_hw


class Uscita:
    """percorsi: {"base": ..., "soggetto": ..., "anteprima": ...}; una voce None = quel file non si scrive.
    In prova (args.prova) non si scrive nessun video, solo i PNG di controllo."""

    def __init__(self, args, w, h, fps_txt, colore, bit16, out_dir, nome, idx_prova, ev, percorsi=None):
        self.args, self.w, self.h, self.bit16, self.ev = args, w, h, bit16, ev
        self.prove = []                               # PNG di prova salvati
        self.out_dir, self.nome, self.idx_prova = out_dir, nome, idx_prova
        self.escl = [rect_pct(e, w, h, "escludi") for e in args.escludi]
        s = min(1.0, 1920 / max(w, h))
        self.pw, self.ph = int(round(w * s / 2)) * 2, int(round(h * s / 2)) * 2
        self.check = scacchiera(self.ph, self.pw, max(12, self.pw // 80))
        self.wr = None
        self.percorsi = {}
        if not args.prova:
            hw = prova_prores_hw() if args.prores_hw else {}
            if args.prores_hw:
                ev.log("ProRes hardware: " + (", ".join(hw) if hw else "non disponibile, uso prores_ks"))
            self.percorsi = {k: v for k, v in (percorsi or {}).items() if v}
            p = self.percorsi
            self.wr = (scrittore(p["base"], w, h, fps_txt, "base", colore, bit16, hw) if "base" in p else None,
                       scrittore(p["soggetto"], w, h, fps_txt, "alpha", colore, bit16, hw) if "soggetto" in p else None,
                       scrittore(p["anteprima"], self.pw, self.ph, fps_txt, "prev", colore) if "anteprima" in p else None)
        self.coda = queue.Queue(maxsize=4)
        self.errore = None
        self.th = threading.Thread(target=self._ciclo, daemon=True)
        self.th.start()

    def metti(self, lavoro):
        while True:
            if self.errore:
                raise self.errore
            try:
                self.coda.put(lavoro, timeout=0.5)
                return
            except queue.Full:
                continue

    def chiudi(self):
        self.coda.put(None)
        self.th.join()
        if self.errore:
            raise self.errore
        if self.wr:
            for p in self.wr:
                if p is not None:
                    p.stdin.close()
                    p.wait()
            if any(p is not None and p.returncode != 0 for p in self.wr):
                raise RuntimeError("ffmpeg non e' riuscito a scrivere i file (spazio su disco?)")

    def interrompi(self):
        """Annullamento: ferma tutto e cancella i file a meta'."""
        self.errore = self.errore or RuntimeError("interrotto")
        try:
            while True:
                self.coda.get_nowait()
        except queue.Empty:
            pass
        self.coda.put(None)
        self.th.join(timeout=10)
        if self.wr:
            for p in self.wr:
                if p is None:
                    continue
                try:
                    p.stdin.close()
                except Exception:
                    pass
                p.kill()
                p.wait()
        for f in self.percorsi.values():
            try:
                os.remove(f)
            except OSError:
                pass

    def _ciclo(self):
        try:
            while True:
                lav = self.coda.get()
                if lav is None:
                    return
                self._elabora(lav)
        except Exception as e:  # lo rilancio nel thread principale
            self.errore = e

    def _rifinisci_matting(self, lav, scala):
        """alfa MatAnyone (bassa ris.) -> cancello SAM -> riquadro -> alfa 4K agganciata ai bordi."""
        a_lr = lav["alpha_lr"]
        if lav.get("m_png") is not None and not self.args.no_cancello:
            a_lr = a_lr * cancello_sam(lav["m_png"], a_lr.shape[1], a_lr.shape[0])
        x, y, bw, bh = cv2.boundingRect((a_lr > 0.004).astype(np.uint8))
        if bw == 0 or bh == 0:
            return None, None, None
        hl, wl = a_lr.shape
        mg = 8
        lx0, ly0, lx1, ly1 = max(0, x - mg), max(0, y - mg), min(wl, x + bw + mg), min(hl, y + bh + mg)
        sx, sy = self.w / wl, self.h / hl
        for (hx, hy, S, _) in lav.get("mani", []):      # il riquadro deve contenere anche le mani
            lx0, ly0 = min(lx0, int(np.floor(hx / sx))), min(ly0, int(np.floor(hy / sy)))
            lx1, ly1 = max(lx1, min(wl, int(np.ceil((hx + S) / sx)))), max(ly1, min(hl, int(np.ceil((hy + S) / sy))))
        X0, Y0 = int(round(lx0 * sx)), int(round(ly0 * sy))
        X1, Y1 = int(round(lx1 * sx)), int(round(ly1 * sy))
        p = a_lr[ly0:ly1, lx0:lx1].astype(np.float32)
        colc = lav["rgb"][Y0:Y1, X0:X1].astype(np.float32) / scala      # solo il riquadro, non tutto il 4K
        if self.args.ingrandimento == "colore":
            a = su_guidato(colc / 255.0, p)
        else:
            a = np.clip(cv2.resize(p, (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_CUBIC), 0, 1)
        a[a < 0.004] = 0
        return (X0, Y0, X1, Y1), a, colc

    def _unisci_mani(self, lav, box_, ac, colc):
        """Fonde l'alfa ad alta risoluzione delle mani dentro quella del soggetto (in place)."""
        X0, Y0, X1, Y1 = box_
        self._mani_prima = []
        for (x0, y0, S, aP) in lav["mani"]:
            P = aP.shape[0]
            rx, ry = x0 - X0, y0 - Y0
            if rx < 0 or ry < 0 or rx + S > X1 - X0 or ry + S > Y1 - Y0:
                continue
            if S > P * 1.15 and self.args.ingrandimento == "colore":
                ah = su_guidato(colc[ry:ry + S, rx:rx + S] / 255.0, aP)
            else:
                ah = np.clip(cv2.resize(aP, (S, S), interpolation=cv2.INTER_CUBIC), 0, 1)
            if lav.get("m_png") is not None and not self.args.no_cancello:
                ah = ah * cancello_ritaglio(lav["m_png"], self.w, self.h, x0, y0, S)
            reg = ac[ry:ry + S, rx:rx + S]
            if self.args.prova:
                self._mani_prima.append((x0, y0, S, reg.copy()))
            f = finestra(S)
            ac[ry:ry + S, rx:rx + S] = f * ah + (1 - f) * reg

    def _png_mani(self, i, rgb8, colc, box_, ac):
        """Prova: per ogni mano, originale | prima | dopo (su scacchiera) | alfa dopo, a risoluzione piena."""
        X0, Y0 = box_[0], box_[1]
        for k, (x0, y0, S, prima) in enumerate(getattr(self, "_mani_prima", [])):
            rx, ry = x0 - X0, y0 - Y0
            dopo = ac[ry:ry + S, rx:rx + S]
            col = colc[ry:ry + S, rx:rx + S]
            chk = scacchiera(S, S, max(8, S // 24))
            comp = [np.clip(chk * (1 - a_[..., None]) + col * a_[..., None], 0, 255).astype(np.uint8)
                    for a_ in (prima, dopo)]
            riga = np.concatenate([rgb8[y0:y0 + S, x0:x0 + S], comp[0], comp[1],
                                   cv2.cvtColor((dopo * 255).astype(np.uint8), cv2.COLOR_GRAY2RGB)], axis=1)
            png = os.path.join(self.out_dir, f"{self.nome}_PROVA_frame{i:04d}_mano{k + 1}.png")
            cv2.imwrite(png, cv2.cvtColor(riga, cv2.COLOR_RGB2BGR))
            self.prove.append(png)
            self.ev.file("prova mano", png)

    def _elabora(self, lav):
        import time as _t
        t_inizio = _t.perf_counter()
        a, w, h = self.args, self.w, self.h
        i, rgb = lav["i"], lav["rgb"]                     # rgb: uint8 o uint16, piena risoluzione
        scala = 257.0 if self.bit16 else 1.0
        if "alpha_crop" in lav:                           # motore isnet: gia' rifinito
            box_, ac, colc = lav["box"], lav["alpha_crop"], lav["col_crop"]
        elif lav.get("alpha_lr") is not None:             # motore matanyone
            box_, ac, colc = self._rifinisci_matting(lav, scala)
            if box_ is not None and lav.get("mani"):
                self._unisci_mani(lav, box_, ac, colc)
            if box_ is not None:
                ac = bordo_e_sfuma(ac, a)
                if not a.no_decontamina:
                    colc = decontamina(colc, ac)
        else:
            box_, ac, colc = None, None, None

        a8 = np.zeros((h, w), np.uint8)
        if box_ is not None:
            X0, Y0, X1, Y1 = box_
            for (x0, y0, x1, y1) in self.escl:            # zone escluse (coordinate piene)
                ix0, iy0, ix1, iy1 = max(x0, X0), max(y0, Y0), min(x1, X1), min(y1, Y1)
                if ix1 > ix0 and iy1 > iy0:
                    ac[iy0 - Y0:iy1 - Y0, ix0 - X0:ix1 - X0] = 0
            a8[Y0:Y1, X0:X1] = np.clip(ac * 255 + 0.5, 0, 255).astype(np.uint8)

        if a.prova:
            if i in self.idx_prova:
                rgb8 = (rgb >> 8).astype(np.uint8) if self.bit16 else rgb
                af = a8.astype(np.float32)[..., None] / 255.0
                col = rgb8.astype(np.float32)
                if box_ is not None:
                    col[Y0:Y1, X0:X1] = colc
                chk = scacchiera(h, w, max(12, w // 80))
                comp = np.clip(chk * (1 - af) + col * af, 0, 255).astype(np.uint8)
                riga = np.concatenate([rgb8, comp, cv2.cvtColor(a8, cv2.COLOR_GRAY2RGB)], axis=1)
                png = os.path.join(self.out_dir, f"{self.nome}_PROVA_frame{i:04d}.png")
                cv2.imwrite(png, cv2.cvtColor(riga, cv2.COLOR_RGB2BGR))
                self.prove.append(png)
                self.ev.file("prova", png)
                if box_ is not None and lav.get("mani"):
                    self._png_mani(i, rgb8, colc, box_, ac)
            self.ev.tempi.aggiungi("rifinitura 4K", _t.perf_counter() - t_inizio)
            return

        t_scrivi = _t.perf_counter()
        self.ev.tempi.aggiungi("rifinitura 4K", t_scrivi - t_inizio)
        wb, wa, wp = self.wr
        if wb is not None:
            wb.stdin.write(rgb.tobytes())
        if self.bit16:
            rgba = np.zeros((h, w, 4), np.uint16)
            if box_ is not None:
                rgba[Y0:Y1, X0:X1, :3] = np.clip(colc * 257.0 + 0.5, 0, 65535).astype(np.uint16)
                rgba[Y0:Y1, X0:X1, 3] = np.clip(ac * 65535 + 0.5, 0, 65535).astype(np.uint16)
                rgba[a8 == 0, :3] = 0
        else:
            rgba = np.zeros((h, w, 4), np.uint8)
            if box_ is not None:
                rgba[Y0:Y1, X0:X1, :3] = np.clip(colc + 0.5, 0, 255).astype(np.uint8)
                rgba[..., 3] = a8
                rgba[a8 == 0, :3] = 0                     # nero dove e' trasparente: file molto piu' leggero
        if wa is not None:
            wa.stdin.write(rgba.tobytes())
        if wp is None:
            self.ev.tempi.aggiungi("scrittura file", _t.perf_counter() - t_scrivi)
            return
        # anteprima a 1080p: si compone gia' ridotta (molto piu' veloce che in 4K)
        col8 = ((rgb >> 8).astype(np.uint8) if self.bit16 else rgb).copy()
        if box_ is not None:
            col8[Y0:Y1, X0:X1] = np.clip(colc + 0.5, 0, 255).astype(np.uint8)
        col_s = cv2.resize(col8, (self.pw, self.ph), interpolation=cv2.INTER_AREA).astype(np.float32)
        af_s = cv2.resize(a8, (self.pw, self.ph), interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255
        comp = self.check * (1 - af_s) + col_s * af_s
        wp.stdin.write(np.clip(comp, 0, 255).astype(np.uint8).tobytes())
        self.ev.tempi.aggiungi("scrittura file", _t.perf_counter() - t_scrivi)
