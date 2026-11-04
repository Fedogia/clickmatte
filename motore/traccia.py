# -*- coding: utf-8 -*-
"""Tracciamento: SAM 2 segue il soggetto (qualsiasi cosa), YOLO-pose trova le mani delle persone.

Il tratto e' diviso dai FOTOGRAMMI CHIAVE ("tappe"): ognuno dice da dove ripartire.
  - selezione:  clic "aggiungi"/"togli" (o una maschera gia' pronta) -> SAM 2 segue da li';
  - correzione: come sopra, ma partendo dalla maschera tracciata in quel punto;
  - assente:    da qui il soggetto non c'e' (frame trasparenti).
Ogni pezzo tra due chiavi si traccia da solo e si salva su disco appena finito: se aggiungi
una correzione a meta', si rifa' solo da li' in avanti (il pezzo prima e' gia' pronto).
"""

import hashlib
import json
import math
import os
import pickle
import shutil
import subprocess
import tempfile
import time

import cv2
import numpy as np

from .eventi import Annullato, ErroreMotore
from .controllo import misure
from .filtri import ellisse
from .modelli import percorso_modello
from .video import lettore_frame

VER_TRACCIA = "traccia-5"
LATO_PROXY = 1280


# ---------------------------------------------------------------------------
#  fotogrammi chiave
# ---------------------------------------------------------------------------
def parse_segui(valori, inizio, fps, n_tot):
    """Opzione --segui da terminale -> fotogrammi chiave.
    '42,70' (primo frame) | '8.3:40,60' (da 8.3 s riparti da li') | '2.9:-' (da 2.9 s non c'e')
    Piu' punti con '+'; 'no' davanti = "togli questo" (es. 25,75+40,30+no70,60)."""
    tappe = []
    for v in valori:
        if ":" in v:
            t_txt, p_txt = v.split(":", 1)
            try:
                f = int(round((float(t_txt) - inizio) * fps))
            except ValueError:
                raise ErroreMotore(f"--segui {v}: il tempo prima dei ':' deve essere in secondi (es. 8.3:40,60)")
        else:
            f, p_txt = 0, v
        f = max(0, min(n_tot - 1, f))
        if p_txt.strip() == "-":
            tappe.append({"frame": f, "assente": True})
            continue
        agg, tog = [], []
        for pt in p_txt.split("+"):
            pt = pt.strip()
            no = pt.lower().startswith("no")
            pt = pt[2:] if no else pt
            try:
                x, y = [float(c) for c in pt.split(",")]
            except ValueError:
                raise ErroreMotore(f"--segui {v}: servono x,y in % (es. 42,70), piu' punti con + (es. 25,75+40,30), "
                                   "tempo:x,y (es. 8.3:40,60) o tempo:-")
            (tog if no else agg).append((x, y))
        if not agg:
            raise ErroreMotore(f"--segui {v}: serve almeno un punto sul soggetto (non solo punti 'no')")
        tappe.append({"frame": f, "aggiungi": agg, "togli": tog})
    return normalizza_tappe(tappe)


def normalizza_tappe(tappe):
    """Forma canonica dei fotogrammi chiave (accetta anche il vecchio formato (frame, punti)).

    {"frame": int, "assente": bool, "aggiungi": [[x%, y%]], "togli": [[x%, y%]],
     "base": png | None, "maschera": png | None}
    """
    per_frame = {}
    for t in tappe or []:
        if isinstance(t, dict):
            f = int(t["frame"])
            ass = bool(t.get("assente"))
            agg = [[round(float(x), 3), round(float(y), 3)] for x, y, *_ in t.get("aggiungi") or []]
            tog = [[round(float(x), 3), round(float(y), 3)] for x, y, *_ in t.get("togli") or []]
            base, mas = t.get("base") or None, t.get("maschera") or None
        else:                                          # formato 3.1: (frame, [(x, y, 1|0)] | None)
            f, p = int(t[0]), t[1]
            ass = p is None
            agg = [[round(float(x), 3), round(float(y), 3)] for x, y, lab in (p or []) if int(lab) == 1]
            tog = [[round(float(x), 3), round(float(y), 3)] for x, y, lab in (p or []) if int(lab) == 0]
            base = mas = None
        if ass:
            per_frame[f] = {"frame": f, "assente": True, "aggiungi": [], "togli": [], "base": None, "maschera": None}
        elif agg or mas or base:
            per_frame[f] = {"frame": f, "assente": False, "aggiungi": agg, "togli": tog, "base": base,
                            "maschera": mas}
    out = [per_frame[f] for f in sorted(per_frame)]
    if not out or out[0]["frame"] != 0:
        out.insert(0, {"frame": 0, "assente": True, "aggiungi": [], "togli": [], "base": None, "maschera": None})
    return out


def _sha_file(p):
    try:
        with open(p, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return None


def firma_tappa(t):
    """Impronta del contenuto di una chiave: se non cambia, il tracciamento che parte da li' e' valido."""
    if t["assente"]:
        return "assente"
    if t.get("maschera"):
        s = _sha_file(t["maschera"])
        if s:
            return "m:" + s
    from .selezione import VERSIONE_SELEZIONE
    return "p:" + hashlib.sha1(json.dumps([VERSIONE_SELEZIONE, t["aggiungi"], t["togli"],
                                           _sha_file(t["base"]) if t.get("base") else None]).encode()).hexdigest()


def segmenti(tappe, n_tot):
    """[(a, b, chiave)]: i pezzi del tratto, ognuno dalla sua chiave alla successiva."""
    out = []
    for k, t in enumerate(tappe):
        a = t["frame"]
        b = tappe[k + 1]["frame"] if k + 1 < len(tappe) else n_tot
        a, b = min(a, n_tot), min(b, n_tot)
        if b > a:
            out.append((a, b, t))
    return out


# ---------------------------------------------------------------------------
#  utilita'
# ---------------------------------------------------------------------------
def dispositivo_torch():
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def dim_proxy(w, h, lato=LATO_PROXY):
    """Dimensioni a cui si traccia (e della copia leggera dell'interfaccia): lato lungo 1280."""
    if w >= h:
        return lato, int(round(h * lato / w / 2)) * 2
    return int(round(w * lato / h / 2)) * 2, lato


def primo_frame(inizio, fps):
    """Indice (nel video) del primo frame del tratto che parte da 'inizio' secondi."""
    return max(0, int(math.ceil(inizio * fps - 1e-3)))


# ---------------------------------------------------------------------------
#  cache dei pezzi tracciati
# ---------------------------------------------------------------------------
def _usa_posa(cfg):
    return getattr(cfg, "soggetto", "persona") == "persona"


def chiave_segmento(cfg, f0, a, t):
    st = os.stat(cfg.video)
    dati = [VER_TRACCIA, os.path.abspath(cfg.video), st.st_size, round(st.st_mtime, 3), f0 + a, firma_tappa(t),
            cfg.segui_modello, int(cfg.segui_risoluzione)]
    return hashlib.sha1(json.dumps(dati).encode()).hexdigest()


def _file_segmento(cartella, chiave):
    return os.path.join(cartella, "traccia", chiave[:24] + ".pkl")


_MEMORIA = {}                                          # pezzi gia' letti dal disco (l'interfaccia li rilegge spesso)


def _carica(cartella, chiave, n, serve_posa=False):
    p = _file_segmento(cartella, chiave)
    try:
        st = os.stat(p)
    except OSError:
        return None
    k = (p, st.st_mtime, st.st_size)
    d = _MEMORIA.get(k)
    if d is None:
        try:
            with open(p, "rb") as fh:
                d = pickle.load(fh)
        except Exception:
            return None
        if len(_MEMORIA) > 64:
            _MEMORIA.pop(next(iter(_MEMORIA)))
        _MEMORIA[k] = d
    if serve_posa and not d.get("posa", d.get("mani") is not None):
        return None                                     # tracciato come oggetto: per una persona servono le mani
    if d.get("n", 0) >= n or d.get("eof"):            # un tracciamento piu' lungo vale anche per un pezzo piu' corto
        return d
    return None


def _salva(cartella, chiave, d):
    p = _file_segmento(cartella, chiave)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "wb") as fh:
        pickle.dump(d, fh)
    os.replace(p + ".tmp", p)


def stato_traccia(cfg, info, tappe, n_tot, cartella):
    """Per ogni pezzo: gia' tracciato (con i dati), da tracciare, o 'assente'."""
    f0 = primo_frame(cfg.inizio, info["fps"])
    out = []
    for a, b, t in segmenti(tappe, n_tot):
        s = {"a": a, "b": b, "tappa": t, "assente": t["assente"], "dati": None, "chiave": None}
        if not t["assente"]:
            s["chiave"] = chiave_segmento(cfg, f0, a, t)
            s["dati"] = _carica(cartella, s["chiave"], b - a, _usa_posa(cfg))
            if s["dati"] is not None and "misure" not in s["dati"]:     # pezzi tracciati da versioni vecchie
                s["dati"]["misure"] = misure(s["dati"]["maschere"], s["dati"].get("pose"))
                try:
                    _salva(cartella, s["chiave"], s["dati"])
                except OSError:
                    pass
        out.append(s)
    return out


def assembla(stato, n_tot, posa):
    """Unisce i pezzi. Restituisce un dizionario con, per ogni frame del tratto:
    tracce (PNG | None), mani, pronto (bool), misure (per il controllo), vecchie (PNG | None: il
    tracciamento di prima di una correzione, ancora da aggiornare: l'interfaccia lo mostra in grigio)."""
    tracce = [None] * n_tot
    mani = [[] for _ in range(n_tot)] if posa else None
    pronto = np.zeros(n_tot, bool)
    mis = [None] * n_tot
    vecchie = [None] * n_tot
    for s in stato:
        a, b = s["a"], s["b"]
        if s["assente"]:
            pronto[a:b] = True
            continue
        d = s["dati"]
        if d is None:
            continue
        pronto[a:b] = True
        dm = d.get("misure") or [None] * d["n"]
        for k in range(min(b - a, d["n"])):
            tracce[a + k] = d["maschere"][k]
            mis[a + k] = dm[k]
            if posa and d.get("mani") is not None:
                mani[a + k] = list(d["mani"][k])
    for s in stato:                                   # oltre la fine del suo pezzo un tracciamento piu' lungo
        d = s["dati"]                                 # e' "vecchio": vale fino a che non si ritraccia
        if s["assente"] or d is None:
            continue
        for k in range(s["b"] - s["a"], d["n"]):
            g = s["a"] + k
            if g < n_tot and not pronto[g] and vecchie[g] is None:
                vecchie[g] = d["maschere"][k]
    return {"tracce": tracce, "mani": mani, "pronto": pronto, "misure": mis, "vecchie": vecchie}


# ---------------------------------------------------------------------------
#  tracciamento
# ---------------------------------------------------------------------------
def _predittore_video():
    from ultralytics.models.sam import SAM2VideoPredictor

    class PredittoreVideo(SAM2VideoPredictor):
        """Come quello di ultralytics, ma accetta anche una maschera come punto di partenza
        (la versione originale passa la maschera con una dimensione in meno e si blocca)."""

        def add_new_prompts(self, obj_id, points=None, labels=None, masks=None, frame_idx=0, **kw):
            if masks is not None and masks.ndim == 3:
                masks = masks[:, None]
            return super().add_new_prompts(obj_id, points=points, labels=labels, masks=masks,
                                           frame_idx=frame_idx, **kw)

    return PredittoreVideo


def maschera_iniziale(t, primo_bgr, selettore):
    """La maschera da cui parte il pezzo: quella pronta (dall'interfaccia) o calcolata dai clic."""
    ph, pw = primo_bgr.shape[:2]
    if t.get("maschera") and os.path.isfile(t["maschera"]):
        m = cv2.imread(t["maschera"], cv2.IMREAD_GRAYSCALE)
        if m is not None:
            return cv2.resize(m, (pw, ph), interpolation=cv2.INTER_NEAREST) > 127
    base = cv2.imread(t["base"], cv2.IMREAD_GRAYSCALE) if t.get("base") else None
    return selettore().calcola(primo_bgr, base, t["aggiungi"], t["togli"]) > 127


def traccia_sam(cfg, info, tappe, n_tot, cartella, ev):
    """Traccia i pezzi che mancano (gli altri li riprende dal disco). Restituisce (tracce, mani, stato)."""
    try:
        PredittoreVideo = _predittore_video()
    except ImportError:
        raise ErroreMotore("Per seguire il soggetto serve ultralytics:  pip install ultralytics")
    stato = stato_traccia(cfg, info, tappe, n_tot, cartella)
    posa_on = _usa_posa(cfg)
    da_fare = [s for s in stato if not s["assente"] and s["dati"] is None]
    if not da_fare:
        r = assembla(stato, n_tot, posa_on)
        return r["tracce"], r["mani"], stato

    posa = None
    if posa_on:
        from ultralytics import YOLO
        posa = YOLO(percorso_modello("yolo11s-pose.pt"))
    pw, ph = dim_proxy(info["w"], info["h"])
    f0 = primo_frame(cfg.inizio, info["fps"])
    n_prog = max(1, sum(s["b"] - s["a"] for s in da_fare))
    dev = [dispositivo_torch()]
    sel = [None]

    def selettore():
        if sel[0] is None:
            from .selezione import Selettore
            sel[0] = Selettore(cfg.segui_modello, cfg.segui_risoluzione, dev[0])
        return sel[0]

    fatti = 0
    ev.tempi.azzera()
    t_tot = time.perf_counter()
    try:
        for s in da_fare:
            a, b, t = s["a"], s["b"], s["tappa"]
            ev.log(f"  traccio {(f0 + a) / info['fps']:.2f}s -> {(f0 + b) / info['fps']:.2f}s "
                   f"({b - a} frame)")
            d = _traccia_pezzo(cfg, info, f0, a, b, t, pw, ph, PredittoreVideo, posa, dev, selettore,
                               ev, fatti, n_prog)
            _salva(cartella, s["chiave"], d)
            s["dati"] = d
            fatti += b - a
    finally:
        ev.fine_fase("tracciamento")
    if fatti:
        ev.log(f"  tempi del tracciamento ({fatti / max(1e-6, time.perf_counter() - t_tot):.1f} frame/s): "
               + ev.tempi.riepilogo(fatti))
    r = assembla(stato, n_tot, posa_on)
    return r["tracce"], r["mani"], stato


def _traccia_pezzo(cfg, info, f0, a, b, t, pw, ph, PredittoreVideo, posa, dev, selettore, ev, base, n_prog):
    n_chiesti = b - a
    tmp = tempfile.mkdtemp(prefix="scontorno_")
    try:
        clip = os.path.join(tmp, "pezzo.mp4")
        rd = lettore_frame(cfg.video, f0 + a, n_chiesti, info["fps_txt"], pw, ph, info["colore"], scala=True)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-s", f"{pw}x{ph}", "-r", info["fps_txt"], "-i", "-", "-c:v", "libx264",
                                "-crf", "14", "-pix_fmt", "yuv420p", clip], stdin=subprocess.PIPE)
        fsize, n, primo = pw * ph * 3, 0, None
        t_prep = time.perf_counter()
        try:
            while n < n_chiesti:
                buf = rd.stdout.read(fsize)
                if len(buf) < fsize:
                    break
                if primo is None:
                    primo = cv2.cvtColor(np.frombuffer(buf, np.uint8).reshape(ph, pw, 3), cv2.COLOR_RGB2BGR)
                enc.stdin.write(buf)
                n += 1
                if n % 10 == 0:
                    ev.controlla()
        finally:
            rd.kill()
            rd.stdout.close()
            rd.wait()
            enc.stdin.close()
            enc.wait()
            ev.tempi.aggiungi("preparazione video", time.perf_counter() - t_prep)
        vuoto = {"n": n, "eof": n < n_chiesti, "maschere": [None] * n, "pose": [None] * n,
                 "mani": [[] for _ in range(n)] if posa is not None else None, "posa": posa is not None,
                 "misure": [None] * n}
        if n == 0:
            return vuoto
        m0 = maschera_iniziale(t, primo, selettore)
        if not m0.any():
            ev.log("    la selezione di partenza e' vuota: in questo pezzo il soggetto non ci sara'")
            return vuoto

        def esegui(device):
            pred = PredittoreVideo(overrides=dict(conf=0.25, task="segment", mode="predict",
                                                  imgsz=cfg.segui_risoluzione,
                                                  model=percorso_modello(cfg.segui_modello),
                                                  device=device, verbose=False, save=False))
            maschere, pose = [], []
            T = ev.tempi
            t_prec = time.perf_counter()
            for k, r in enumerate(pred(source=clip, masks=[m0.astype(np.uint8)], stream=True)):
                T.aggiungi("SAM 2", time.perf_counter() - t_prec)
                m = None
                if r.masks is not None and len(r.masks):
                    mm = (r.masks.data[0].cpu().numpy() > 0.5).astype(np.uint8) * 255
                    if mm.shape != (ph, pw):
                        mm = cv2.resize(mm, (pw, ph), interpolation=cv2.INTER_NEAREST)
                    if mm.any():
                        m = mm
                t_posa = time.perf_counter()
                kp = posa_della_persona(posa, r.orig_img, m, device) if (posa is not None and m is not None) else None
                if posa is not None:
                    T.aggiungi("posa (mani)", time.perf_counter() - t_posa)
                maschere.append(None if m is None else cv2.imencode(".png", m)[1])
                pose.append(kp)
                ev.progresso("tracciamento", base + k + 1, n_prog, f"({device})")
                ev.controlla()
                t_prec = time.perf_counter()
            return maschere, pose

        try:
            maschere, pose = esegui(dev[0])
        except Annullato:
            raise
        except Exception as e:  # qualche operazione non supportata su MPS: ripiego sulla CPU
            if dev[0] == "cpu":
                raise
            ev.log(f"  {dev[0]} non ha funzionato ({e}); riprovo sulla CPU (piu' lento)")
            dev[0] = "cpu"
            maschere, pose = esegui("cpu")
        maschere = (maschere + [None] * n)[:n]
        pose = (pose + [None] * n)[:n]
        mani = scatole_mani(maschere, pose, pw, ph) if posa is not None else None
        return {"n": n, "eof": n < n_chiesti, "maschere": maschere, "pose": pose, "mani": mani,
                "posa": posa is not None, "misure": misure(maschere, pose)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def video_traccia(cfg, info, tracce, mani, out_path):
    """Video di controllo del tracciamento (soggetto in rosso, polsi in verde): solo da terminale."""
    pw, ph = dim_proxy(info["w"], info["h"])
    f0 = primo_frame(cfg.inizio, info["fps"])
    n = len(tracce)
    rd = lettore_frame(cfg.video, f0, n, info["fps_txt"], pw, ph, info["colore"], scala=True)
    wr = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{pw}x{ph}",
                           "-r", info["fps_txt"], "-i", "-", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                           out_path], stdin=subprocess.PIPE)
    try:
        for i in range(n):
            buf = rd.stdout.read(pw * ph * 3)
            if len(buf) < pw * ph * 3:
                break
            img = np.frombuffer(buf, np.uint8).reshape(ph, pw, 3)
            vis = (img * 0.55).astype(np.uint8)
            if tracce[i] is not None:
                m = cv2.imdecode(tracce[i], cv2.IMREAD_GRAYSCALE) > 127
                vis[m] = (0.45 * img[m] + 0.55 * np.array([255, 60, 60])).astype(np.uint8)
            for _, cx, cy, lato in (mani[i] if mani else []):
                S = lato * ph
                x0, y0 = int(cx * ph - S / 2), int(cy * ph - S / 2)
                cv2.rectangle(vis, (x0, y0), (int(x0 + S), int(y0 + S)), (255, 205, 40), 2)
            wr.stdin.write(vis.tobytes())
    finally:
        rd.kill()
        rd.stdout.close()
        rd.wait()
        wr.stdin.close()
        wr.wait()


# ---------------------------------------------------------------------------
#  mani (solo persone)
# ---------------------------------------------------------------------------
def posa_della_persona(posa, img_bgr, m, device):
    """Punti del corpo (YOLO pose) della persona che coincide con la maschera SAM."""
    try:
        r = posa.predict(img_bgr, imgsz=max(img_bgr.shape[:2]), conf=0.25, device=device, verbose=False)[0]
    except Exception:
        r = posa.predict(img_bgr, imgsz=max(img_bgr.shape[:2]), conf=0.25, device="cpu", verbose=False)[0]
    if r.keypoints is None or r.keypoints.data is None or len(r.keypoints.data) == 0:
        return None
    kps = r.keypoints.data.cpu().numpy()
    dentro = cv2.dilate(m, ellisse(25)) > 0
    hh, ww = m.shape
    migliore, punti = None, 0
    for p in kps:
        n = sum(1 for x, y, c in p if c > 0.3 and 0 <= int(y) < hh and 0 <= int(x) < ww and dentro[int(y), int(x)])
        if n > punti:
            migliore, punti = p, n
    return migliore.astype(np.float32) if punti >= 3 else None


def scatole_mani(maschere, pose, pw, ph):
    """Per ogni frame: lista di (id_traccia, cx, cy, lato) delle mani, normalizzati sull'altezza.

    La mano sta oltre il polso, nella direzione gomito -> polso: prendo i pixel della persona
    (maschera SAM) vicini al polso da quel lato, poi liscio posizione e dimensione nel tempo.
    """
    n = len(maschere)
    grezzi = {9: [None] * n, 10: [None] * n}
    for i in range(n):
        kp = pose[i]
        if kp is None or maschere[i] is None:
            continue
        m = cv2.imdecode(maschere[i], cv2.IMREAD_GRAYSCALE) > 127
        ys, xs = np.nonzero(m)
        if len(ys) < 50:
            continue
        Hp = float(ys.max() - ys.min() + 1)
        for wi, ei in ((9, 7), (10, 8)):
            xw, yw, cw = kp[wi]
            if cw < 0.5:
                continue
            xe, ye, ce = kp[ei]
            d = np.array([xw - xe, yw - ye], np.float32)
            nd = float(np.hypot(*d))
            R = 0.30 * Hp if (ce >= 0.3 and nd > 1) else 0.18 * Hp
            sel = (xs - xw) ** 2 + (ys - yw) ** 2 < R * R
            if ce >= 0.3 and nd > 1:
                d /= nd
                sel &= ((xs - xw) * d[0] + (ys - yw) * d[1]) > -0.03 * Hp
            if sel.sum() < 20:
                continue
            bx0, bx1 = min(xs[sel].min(), xw), max(xs[sel].max(), xw)
            by0, by1 = min(ys[sel].min(), yw), max(ys[sel].max(), yw)
            lato = max(bx1 - bx0, by1 - by0) * 1.25 + 0.03 * Hp
            lato = float(np.clip(lato, 0.10 * Hp, 0.45 * Hp))
            grezzi[wi][i] = ((bx0 + bx1) / 2 / ph, (by0 + by1) / 2 / ph, lato / ph, float(cw))
        a_, b_ = grezzi[9][i], grezzi[10][i]          # due polsi sulla stessa mano: ne tengo uno
        if a_ is not None and b_ is not None:
            dist = np.hypot(a_[0] - b_[0], a_[1] - b_[1])
            if dist < 0.5 * min(a_[2], b_[2]):
                if a_[3] < b_[3]:
                    grezzi[9][i] = None
                else:
                    grezzi[10][i] = None

    mani = [[] for _ in range(n)]
    tid = 0
    for wi in (9, 10):
        serie = grezzi[wi]
        i = 0
        while i < n:
            if serie[i] is None:
                i += 1
                continue
            j, ultimo = i, i                      # allungo la traccia saltando buchi di max 3 frame
            while j + 1 < n and (serie[j + 1] is not None or
                                 any(serie[k] is not None for k in range(j + 2, min(n, j + 5)))):
                j += 1
                if serie[j] is not None:
                    ultimo = j
            j = ultimo
            if j - i + 1 >= 4:
                idx = np.arange(i, j + 1)
                buoni = [k for k in idx if serie[k] is not None]
                val = np.array([serie[k][:3] for k in buoni], np.float32)
                arr = np.stack([np.interp(idx, buoni, val[:, c]) for c in range(3)], 1)
                g = np.exp(-0.5 * (np.arange(-3, 4) / 1.2) ** 2)
                pad = np.pad(arr, ((3, 3), (0, 0)), mode="edge")
                lis = np.stack([np.convolve(pad[:, c], g / g.sum(), mode="valid") for c in range(3)], 1)
                for k, v in zip(idx, lis):
                    mani[k].append((tid, float(v[0]), float(v[1]), float(v[2])))
                tid += 1
            i = j + 1
    return mani


# ---------------------------------------------------------------------------
#  "cancello": la maschera SAM toglie cio' che non e' il soggetto
# ---------------------------------------------------------------------------
def cancello_sam(m_png, wl, hl):
    """'Cancello' morbido dalla maschera SAM, calcolato a bassa risoluzione (veloce)."""
    m = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    x, y, bw, bh = cv2.boundingRect((m > 0.5).astype(np.uint8))
    k = max(5, int(bh * 0.05)) | 1
    g = cv2.GaussianBlur(cv2.dilate(m, ellisse(k)), (0, 0), k / 3.0)
    g = cv2.resize(g, (wl, hl), interpolation=cv2.INTER_LINEAR)
    return np.clip(g * 1.5, 0, 1)


def cancello_ritaglio(m_png, w, h, x0, y0, S):
    """Cancello SAM (vedi cancello_sam) solo sul quadrato (x0, y0, S) a piena risoluzione."""
    m = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE)
    ph_, pw_ = m.shape
    g = cancello_sam(m_png, pw_, ph_)
    scx, scy = pw_ / w, ph_ / h
    M = np.float32([[1 / scx, 0, -x0], [0, 1 / scy, -y0]])
    return cv2.warpAffine(g, M, (S, S), flags=cv2.INTER_LINEAR)
