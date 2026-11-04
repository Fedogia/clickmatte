# -*- coding: utf-8 -*-
"""Il flusso completo: analisi del video -> tracciamento -> scontorno -> file.

Usato sia dal comando da terminale (scontorno.py) sia dall'interfaccia grafica.
Tutti i parametri stanno in Config; i messaggi passano da un oggetto Eventi.
"""

import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

from . import esporta
from .eventi import Annullato, ErroreMotore, Eventi
from .filtri import rect_pct, ellisse, decontamina
from .isnet import maschera_isnet, rifinisci_isnet, zona_da_sam, filtra_isnet_con_sam
from .modelli import cartella_lavoro
from .controllo import sospetti
from .traccia import assembla, normalizza_tappe, parse_segui, primo_frame, stato_traccia, traccia_sam
from .uscita import Uscita
from .video import controlla_ffmpeg, info_video, lettore_frame

VERSIONE = "3.3"

MOTORI = ("matanyone", "isnet", "sagoma")
SOGGETTI = ("persona", "oggetto")


@dataclass
class Config:
    """Tutti i parametri di un lavoro. I nomi coincidono con le opzioni da terminale
    (con '-' al posto di '_': segui_risoluzione -> --segui-risoluzione)."""
    video: str = ""
    uscita: str = ""                    # cartella di destinazione ("" = accanto al video)
    inizio: float = 0.0                 # secondi
    durata: float = 0.0                 # secondi (0 = fino alla fine)
    soggetto: str = "persona"           # "persona" (anche con oggetti in mano) | "oggetto" (qualsiasi altra cosa)
    motore: str = "matanyone"           # "matanyone" | "isnet" | "sagoma"
    # chi scontornare: o le stringhe --segui, o direttamente i fotogrammi chiave (frame relativi al tratto)
    segui: list = field(default_factory=list)
    tappe: list = None                  # [{"frame", "assente", "aggiungi", "togli", "base", "maschera"}, ...]
    segui_modello: str = "sam2.1_t.pt"
    segui_risoluzione: int = 1024
    # matanyone
    risoluzione: int = 1080
    ingrandimento: str = "colore"
    no_cancello: bool = False
    mani: bool = True
    mani_risoluzione: int = 768
    # comuni
    escludi: list = field(default_factory=list)   # stringhe "x,y,l,a" in %
    bordo: float = 0.0
    durezza: float = 0.0
    sfuma: float = 0.0
    no_decontamina: bool = False
    prores_hw: bool = False
    # isnet
    modello: str = "isnet-general-use"
    zona: str = ""
    contrasto: float = 1.0
    precisione: float = 4.0
    pulizia: float = 0.15
    stabilizza: float = 0.35
    solo_persone: bool = False
    solo_principale: bool = False
    # lavoro
    prova: bool = False
    frame_prova: list = None            # frame (relativi al tratto) da salvare in prova; None = automatici
    rifai_tutto: bool = False           # True: esporta tutto da capo anche se solo una parte e' cambiata
    cartella_cache: str = ""            # dati di lavoro ("" = ~/Library/Caches/Scontorno/<video>)

    def dizionario(self):
        return asdict(self)

    @staticmethod
    def da_dizionario(d):
        campi = Config.__dataclass_fields__
        c = Config(**{k: v for k, v in d.items() if k in campi})
        if c.tappe is not None:
            c.tappe = normalizza_tappe(c.tappe)
        return c


# ---------------------------------------------------------------------------
#  analisi
# ---------------------------------------------------------------------------
def analizza(video):
    """Informazioni sul video + avvisi e suggerimenti (usati dall'interfaccia)."""
    controlla_ffmpeg()
    if not os.path.isfile(video):
        raise ErroreMotore(f"File non trovato: {video}")
    w, h, fps_txt, fps, dur, bit, colore = info_video(video)
    import json
    import subprocess
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=avg_frame_rate,nb_frames,codec_name", "-of", "json", video],
                                  capture_output=True, text=True).stdout)["streams"][0]
    try:
        n, dd = d.get("avg_frame_rate", "0/1").split("/")
        avg = float(n) / float(dd or 1)
    except (ValueError, ZeroDivisionError):
        avg = fps
    avvisi = []
    if colore["ct"] in ("arib-std-b67", "smpte2084"):
        avvisi.append("Video HDR: i file mantengono la codifica HDR del sorgente. Controlla i colori in Premiere.")
    if avg and abs(avg - fps) / fps > 0.01:
        avvisi.append(f"Frame rate variabile ({avg:.2f} medio contro {fps:.2f} nominale): "
                      "BASE e SOGGETTO restano allineati tra loro, ma usa sempre la BASE nel MOGRT.")
    if bit > 8:
        avvisi.append(f"Sorgente a {bit} bit: l'elaborazione passa automaticamente a 16 bit.")
    return {"w": w, "h": h, "fps_txt": fps_txt, "fps": fps, "durata": dur, "bit": bit, "colore": colore,
            "codec": d.get("codec_name", ""), "n_frame": int(d.get("nb_frames") or round(dur * fps)),
            "avvisi": avvisi}


def suggerimenti(info, altezza_soggetto=None):
    """Consigli sulla qualita'. altezza_soggetto: frazione del frame occupata (dalla selezione)."""
    s = {"qualita": "bilanciata", "motivo": ""}
    if altezza_soggetto is not None and altezza_soggetto > 0.7 and min(info["w"], info["h"]) >= 1440:
        s["qualita"] = "massima"
        s["motivo"] = (f"Primo piano (il soggetto occupa il {altezza_soggetto * 100:.0f}% dell'altezza): "
                       "'Massima' conserva meglio capelli e dita. Piu' lenta, ~12 GB di memoria.")
    return s


# ---------------------------------------------------------------------------
#  tracciamento
# ---------------------------------------------------------------------------
def _tratto(cfg, info):
    durata = cfg.durata if cfg.durata > 0 else max(0.0, info["durata"] - cfg.inizio)
    if cfg.inizio >= info["durata"] > 0:
        raise ErroreMotore(f"L'inizio ({cfg.inizio:.2f}s) e' oltre la fine del video ({info['durata']:.2f}s)")
    return durata, max(1, int(round(durata * info["fps"])))


def _cartella(cfg):
    out_dir = cfg.uscita or os.path.dirname(os.path.abspath(cfg.video))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def cartella_di(cfg):
    """Cartella di lavoro (tracciamento, elenco dei file esportati)."""
    d = cfg.cartella_cache or cartella_lavoro(cfg.video)
    os.makedirs(d, exist_ok=True)
    return d


def percorsi_file(cfg):
    nome = os.path.splitext(os.path.basename(cfg.video))[0]
    d = _cartella(cfg)
    return {"base": os.path.join(d, f"{nome}_BASE.mov"),
            "soggetto": os.path.join(d, f"{nome}_SOGGETTO_alpha.mov"),
            "anteprima": os.path.join(d, f"{nome}_ANTEPRIMA.mp4")}


def _tappe(cfg, info, n_tot):
    if cfg.tappe is not None:
        t = normalizza_tappe(cfg.tappe)
    elif cfg.segui:
        t = parse_segui(cfg.segui, cfg.inizio, info["fps"], n_tot)
    else:
        return None
    return [x for x in t if x["frame"] < n_tot] or t[:1]


def _usa_posa(cfg):
    return cfg.soggetto == "persona"


def descrivi_tappa(t):
    if t["assente"]:
        return "soggetto assente"
    tipo = "correzione" if t.get("base") else "selezione"
    parti = []
    if t["aggiungi"]:
        parti.append(f"{len(t['aggiungi'])} +")
    if t["togli"]:
        parti.append(f"{len(t['togli'])} -")
    return tipo + (f" ({', '.join(parti)})" if parti else "")


def leggi_cache(cfg, info=None):
    """Stato del tracciamento senza calcolare nulla: cosa e' gia' pronto e cosa manca.

    Restituisce None se non c'e' nessun fotogramma chiave con il soggetto, altrimenti
    {"tracce": [PNG | None per frame], "mani", "tappe", "pronto": bool per frame, "misure",
     "vecchie": [PNG | None] (tracciamento da aggiornare), "sospetti": [(da, a, motivo)],
     "da_fare": [(a, b)], "completo": bool}."""
    info = info or analizza(cfg.video)
    durata, n_tot = _tratto(cfg, info)
    tappe = _tappe(cfg, info, n_tot)
    if tappe is None or all(t["assente"] for t in tappe):
        return None
    stato = stato_traccia(cfg, info, tappe, n_tot, cartella_di(cfg))
    r = assembla(stato, n_tot, _usa_posa(cfg))
    da_fare = [(s["a"], s["b"]) for s in stato if not s["assente"] and s["dati"] is None]
    r.update(tappe=tappe, da_fare=da_fare, completo=not da_fare, cache=cartella_di(cfg),
             sospetti=sospetti(r["misure"], [t["frame"] for t in tappe], r["pronto"]))
    return r


def traccia(cfg, ev=None, info=None):
    """Tracciamento del tratto: si calcolano solo i pezzi che mancano (gli altri sono su disco)."""
    ev = ev or Eventi()
    info = info or analizza(cfg.video)
    durata, n_tot = _tratto(cfg, info)
    tappe = _tappe(cfg, info, n_tot)
    if tappe is None:
        raise ErroreMotore("Indica cosa scontornare: almeno un clic sul soggetto nel primo frame del tratto.")
    if all(t["assente"] for t in tappe):
        raise ErroreMotore("Tutti i fotogrammi chiave dicono 'soggetto assente': clicca sul soggetto.")
    f0 = primo_frame(cfg.inizio, info["fps"])
    ev.log(f"Tracciamento ({cfg.segui_modello}, {cfg.segui_risoluzione}): "
           + ", ".join(f"{(f0 + t['frame']) / info['fps']:.2f}s {descrivi_tappa(t)}" for t in tappe))
    traccia_sam(cfg, info, tappe, n_tot, cartella_di(cfg), ev)
    r = leggi_cache(cfg, info)
    tracce, mani = r["tracce"], r["mani"]
    ev.log(f"  soggetto trovato in {sum(m is not None for m in tracce)} frame su {len(tracce)}")
    if mani is not None and cfg.mani:
        ev.log(f"  mani presenti in {sum(1 for fr in mani if fr)} frame")
    for a, b, motivo in r["sospetti"]:
        ev.log(f"  da controllare a {(f0 + a) / info['fps']:.2f}s: {motivo}")
    return r


# ---------------------------------------------------------------------------
#  cosa c'e' da esportare
# ---------------------------------------------------------------------------
def _impronte(cfg, info, tr, n_tot):
    if tr is None:
        return esporta.impronte(None, None, None, n_tot)
    return esporta.impronte(tr["tracce"], tr["mani"] if cfg.mani else None, tr["tappe"], n_tot, tr["pronto"])


def _prepara(cfg):
    """Regole tra i parametri (uguali per terminale e interfaccia)."""
    if cfg.motore not in MOTORI:
        raise ErroreMotore(f"Motore sconosciuto: {cfg.motore} (scegli tra {', '.join(MOTORI)})")
    if cfg.motore != "matanyone" or cfg.soggetto != "persona":
        cfg.mani = False
    return cfg


def piano_render(cfg, info=None, tr=None):
    """Cosa farebbe adesso 'Esporta': tutto, solo alcune parti, o niente. Non calcola nulla di pesante."""
    cfg = _prepara(Config.da_dizionario(cfg.dizionario()))
    info = info or analizza(cfg.video)
    durata, n_tot = _tratto(cfg, info)
    if tr is None and (cfg.tappe is not None or cfg.segui):
        tr = leggi_cache(cfg, info)
    f0 = primo_frame(cfg.inizio, info["fps"])
    perc = percorsi_file(cfg)
    g = esporta.firma_globale(cfg, info, f0, n_tot, VERSIONE)
    imp = _impronte(cfg, info, tr, n_tot)
    inizi = [t["frame"] for t in tr["tappe"]] if tr else [0]
    p = esporta.piano(cartella_di(cfg), perc, g, imp, inizi)
    if cfg.rifai_tutto and p["modo"] != "tutto":
        p = {"modo": "tutto", "intervalli": [(0, n_tot)], "aggiornati": np.zeros(n_tot, bool),
             "motivo": "hai chiesto di rifare tutto"}
    p["percorsi"] = perc
    p["n"] = n_tot
    return p


# ---------------------------------------------------------------------------
#  scontorno completo
# ---------------------------------------------------------------------------
def render(cfg, ev=None):
    """Lavoro completo. Restituisce i percorsi dei file creati (o dei PNG di prova)."""
    ev = ev or Eventi()
    _prepara(cfg)
    if cfg.motore in ("matanyone", "sagoma") and not (cfg.segui or cfg.tappe):
        raise ErroreMotore(f"Con il motore {cfg.motore} serve un clic sul soggetto nel primo frame "
                           "(--segui x,y oppure un clic nell'interfaccia).")
    info = analizza(cfg.video)
    w, h, fps_txt, fps, bit, colore = info["w"], info["h"], info["fps_txt"], info["fps"], info["bit"], info["colore"]
    bit16 = bit > 8
    durata, n_tot = _tratto(cfg, info)
    f0 = primo_frame(cfg.inizio, fps)
    nome = os.path.splitext(os.path.basename(cfg.video))[0]
    out_dir = _cartella(cfg)

    ev.log(f"Video: {w}x{h} @ {fps:.3f} fps, {bit} bit, colore {colore['cs']}/{colore['ct']}  |  "
           f"tratto {f0 / fps:.2f}s + {n_tot / fps:.2f}s ({n_tot} frame)  |  motore {cfg.motore}, "
           f"soggetto {cfg.soggetto}")
    for a in info["avvisi"]:
        ev.log("  Nota: " + a)

    # --- 1) tracciamento (solo i pezzi che mancano) ---
    tr = traccia(cfg, ev, info) if (cfg.segui or cfg.tappe) else None
    tracce = tr["tracce"] if tr else None
    mani = tr["mani"] if (tr and cfg.mani) else None
    tappe = tr["tappe"] if tr else None
    inizio_pezzo = {t["frame"]: not t["assente"] for t in tappe} if tappe else {}
    inizi = sorted(inizio_pezzo) or [0]

    def inizio_di(k):
        return max(s for s in inizi if s <= k)

    # --- 2) cosa fare ---
    perc = percorsi_file(cfg)
    if cfg.prova:
        idx_prova = _frame_prova(cfg, tracce, mani, n_tot)
        if cfg.motore == "matanyone":                  # MatAnyone ha memoria: si parte dall'inizio del pezzo
            intervalli = _unisci([(inizio_di(k), k + 1) for k in sorted(idx_prova)])
        else:
            intervalli = [(k, k + 1) for k in sorted(idx_prova)]
        modo = "prova"
    else:
        g = esporta.firma_globale(cfg, info, f0, n_tot, VERSIONE)
        imp = _impronte(cfg, info, tr, n_tot)
        p = esporta.piano(cartella_di(cfg), perc, g, imp, inizi)
        if cfg.rifai_tutto:
            p = dict(p, modo="tutto", intervalli=[(0, n_tot)], motivo="hai chiesto di rifare tutto")
        modo, intervalli = p["modo"], p["intervalli"]
        if modo == "niente":
            ev.log("Niente da rifare: i file esportati sono gia' aggiornati.")
            for k, tipo in (("base", "CLIP BASE"), ("soggetto", "SOGGETTO (alpha)"), ("anteprima", "ANTEPRIMA")):
                ev.file(tipo, perc[k])
            return dict(perc, rifatti=[])
        if modo == "parziale":
            ev.log("Rifaccio solo: " + ", ".join(f"{(f0 + a) / fps:.2f}-{(f0 + b) / fps:.2f}s" for a, b in intervalli)
                   + f" ({sum(b - a for a, b in intervalli)} frame su {n_tot}); il resto e' gia' pronto.")
        else:
            ev.log(f"Esporto tutto il tratto ({p['motivo']}).")
        idx_prova = set()

    # --- 3) modelli ---
    ctx = _Contesto(cfg, ev, info, f0, tracce, mani, inizio_pezzo, inizio_di)
    ev.tempi.azzera()
    tot = sum(b - a for a, b in intervalli)
    t0 = time.time()

    if modo == "prova":
        uscita = Uscita(cfg, w, h, fps_txt, colore, bit16, out_dir, nome, idx_prova, ev)
        ctx.esegui(intervalli, uscita, tot, ultimo_utile=max(idx_prova) if idx_prova else None)
        ev.fine_fase("scontorno")
        ev.log("Prova finita: guarda i PNG, regola le opzioni e rilancia senza prova.")
        return {"prove": list(uscita.prove)}

    if modo == "parziale":
        try:
            return _render_parziale(cfg, ev, ctx, info, perc, intervalli, tot, g, imp, n_tot, out_dir, nome, t0)
        except (Annullato, KeyboardInterrupt):
            raise
        except ErroreMotore as e:
            ev.log(f"  Unione dei pezzi non riuscita ({e}): rifaccio tutto da capo.")
            intervalli, tot = [(0, n_tot)], n_tot

    # --- tutto da capo: si scrive in file temporanei, che sostituiscono i vecchi solo alla fine ---
    tmp = {k: _temporaneo(p) for k, p in perc.items()}
    uscita = Uscita(cfg, w, h, fps_txt, colore, bit16, out_dir, nome, set(), ev, percorsi=tmp)
    ctx.esegui([(0, n_tot)], uscita, n_tot)
    for k in perc:
        os.replace(tmp[k], perc[k])
    ev.fine_fase("scontorno")
    esporta.scrivi_elenco(cartella_di(cfg), perc, esporta.firma_globale(cfg, info, f0, n_tot, VERSIONE),
                          _impronte(cfg, info, tr, n_tot))
    ev.log(f"Fatto: {n_tot} frame in {time.time() - t0:.0f}s")
    _log_tempi(ev, n_tot, time.time() - t0)
    for k, tipo in (("base", "CLIP BASE"), ("soggetto", "SOGGETTO (alpha)"), ("anteprima", "ANTEPRIMA")):
        ev.file(tipo, perc[k])
    return dict(perc, rifatti=[(0, n_tot)])


def _render_parziale(cfg, ev, ctx, info, perc, intervalli, tot, g, imp, n_tot, out_dir, nome, t0):
    """Rifa' solo gli intervalli cambiati e li cuce nei file gia' esportati (senza ricomprimere il resto)."""
    w, h, fps_txt, colore, bit16 = info["w"], info["h"], info["fps_txt"], info["colore"], info["bit"] > 8
    tmp_dir = os.path.join(cartella_di(cfg), "pezzi", uuid.uuid4().hex[:8])
    os.makedirs(tmp_dir, exist_ok=True)
    nuovi = {}
    try:
        pezzi = {"soggetto": [], "anteprima": []}
        fatti = 0
        for a, b in intervalli:
            pp = {"soggetto": os.path.join(tmp_dir, f"soggetto_{a:06d}.mov"),
                  "anteprima": os.path.join(tmp_dir, f"anteprima_{a:06d}.mp4")}
            uscita = Uscita(cfg, w, h, fps_txt, colore, bit16, out_dir, nome, set(), ev, percorsi=pp)
            ctx.esegui([(a, b)], uscita, tot, gia_fatti=fatti)
            fatti += b - a
            for k in pezzi:
                pezzi[k].append((a, b, pp[k]))
        ev.fine_fase("scontorno")
        ev.log("Unisco i pezzi nuovi ai file esportati (senza ricomprimere)...")
        nuovi = {}
        for k in ("soggetto", "anteprima"):
            nuovi[k] = _temporaneo(perc[k])
            esporta.cuci(perc[k], pezzi[k], n_tot, info["fps"], tmp_dir, nuovi[k])
        ev.controlla()
        for k in list(nuovi):
            os.replace(nuovi.pop(k), perc[k])
    finally:
        for p in nuovi.values():
            try:
                os.remove(p)
            except OSError:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
    esporta.scrivi_elenco(cartella_di(cfg), perc, g, imp)
    ev.log(f"Fatto: rifatti {tot} frame su {n_tot} in {time.time() - t0:.0f}s")
    _log_tempi(ev, tot, time.time() - t0)
    for k, tipo in (("base", "CLIP BASE (invariata)"), ("soggetto", "SOGGETTO (alpha)"), ("anteprima", "ANTEPRIMA")):
        ev.file(tipo, perc[k])
    return dict(perc, rifatti=list(intervalli))


def _log_tempi(ev, frame, secondi):
    """Dove e' andato il tempo, per frame, e qual e' il collo di bottiglia (per decidere cosa accelerare)."""
    T = ev.tempi
    riga = T.riepilogo(frame)
    if not riga:
        return
    ev.log(f"Tempi per frame ({frame / max(1e-6, secondi):.1f} frame/s): {riga}")
    attese = {"la decodifica del video": T.totale("attesa decodifica"),
              "rifinitura e scrittura dei file": T.totale("attesa rifinitura/scrittura")}
    calcolo = sum(T.totale(k) for k in ("MatAnyone", "mani (MatAnyone)", "ISNet"))
    nome, att = max(attese.items(), key=lambda kv: kv[1])
    collo = nome if att > 0.25 * max(calcolo, 1e-6) else "il modello di scontorno"
    ev.log(f"  collo di bottiglia: {collo}")


def _temporaneo(p):
    base, ext = os.path.splitext(p)
    return f"{base}.in-corso{ext}"


def _unisci(intervalli):
    out = []
    for a, b in sorted(intervalli):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _frame_prova(cfg, tracce, mani, n_tot):
    if cfg.frame_prova:
        return {int(k) for k in cfg.frame_prova if 0 <= int(k) < n_tot}
    if os.environ.get("SCONTORNO_FRAMES"):             # solo per test
        return {int(x) for x in os.environ["SCONTORNO_FRAMES"].split(",")}
    ok = list(range(n_tot)) if tracce is None else [k for k, m in enumerate(tracce) if m is not None]
    ok = ok or list(range(n_tot))
    idx = {ok[0], ok[len(ok) // 2], ok[-1]}
    if mani:                                           # + fino a 3 frame con la mano piu' grande
        con_mani = [k for k, fr in enumerate(mani) if fr]
        if con_mani:
            for g in np.array_split(np.array(con_mani), min(3, len(con_mani))):
                idx.add(int(max(g, key=lambda k: max(t[3] for t in mani[k]))))
    return idx


# ---------------------------------------------------------------------------
#  il ciclo sui frame
# ---------------------------------------------------------------------------
class _Contesto:
    """Modelli caricati una volta sola + dati del tracciamento; elabora uno o piu' intervalli di frame."""

    def __init__(self, cfg, ev, info, f0, tracce, mani, inizio_pezzo, inizio_di):
        self.cfg, self.ev, self.info, self.f0 = cfg, ev, info, f0
        self.tracce, self.mani, self.inizio_pezzo, self.inizio_di = tracce, mani, inizio_pezzo, inizio_di
        w, h = info["w"], info["h"]
        self.w, self.h, self.bit16 = w, h, info["bit"] > 8
        self.zona_utente = rect_pct(cfg.zona, w, h, "zona") if cfg.zona else None
        self.mat = self.isn = None
        if cfg.motore == "matanyone":
            from .matting import Matting
            self.mat = Matting(cfg, ev)
            s = min(1.0, cfg.risoluzione / min(w, h))
            self.wl, self.hl = int(round(w * s / 2)) * 2, int(round(h * s / 2)) * 2
            ev.log(f"MatAnyone lavora a {self.wl}x{self.hl}, poi l'alfa viene portata a {w}x{h} agganciata ai bordi")
        elif cfg.motore == "isnet":
            try:
                from rembg import new_session, remove
            except ImportError:
                raise ErroreMotore('Manca rembg:  pip install "rembg[cpu]"')
            ev.log(f"Modello: {cfg.modello}")
            session = new_session(cfg.modello)
            sess_p = new_session("u2net_human_seg") if cfg.solo_persone else None
            self.isn = (session, sess_p, remove, max(64, int(min(w, h) * 0.04)))
        else:
            ev.log("Motore sagoma: la forma del tracciamento, agganciata ai bordi a piena risoluzione")

    def esegui(self, intervalli, uscita, tot, gia_fatti=0, ultimo_utile=None):
        fatti = gia_fatti
        try:
            for a, b in intervalli:
                fatti = self._intervallo(a, b, uscita, fatti, tot, ultimo_utile)
        except BaseException:
            uscita.interrompi()
            raise
        uscita.chiudi()

    def _intervallo(self, a, b, uscita, fatti, tot, ultimo_utile):
        cfg, w, h, bit16 = self.cfg, self.w, self.h, self.bit16
        rd = lettore_frame(cfg.video, self.f0 + a, b - a, self.info["fps_txt"], w, h, self.info["colore"], bit16)
        fsize = w * h * (6 if bit16 else 3)
        q_in = queue.Queue(maxsize=4)
        n_vuole = b - a

        def leggi():
            k = 0
            while k < n_vuole:
                buf = rd.stdout.read(fsize)
                if len(buf) < fsize:
                    break
                q_in.put((a + k, np.frombuffer(buf, np.uint16 if bit16 else np.uint8).reshape(h, w, 3)))
                k += 1
            q_in.put(None)

        th = threading.Thread(target=leggi, daemon=True)
        th.start()
        # stato all'inizio dell'intervallo (che e' sempre l'inizio di un pezzo, o un frame isolato)
        st = {"attivo": self.inizio_pezzo.get(self.inizio_di(a), True) if self.inizio_pezzo else True,
              "prev": None, "mani": {}}
        if self.mat is not None:
            self.mat.ferma()
        try:
            while True:
                T = self.ev.tempi
                with T.misura("attesa decodifica"):
                    item = q_in.get()
                if item is None:
                    break
                self.ev.controlla()
                i, rgb = item
                lav = self._frame(i, rgb, st, a)
                with T.misura("attesa rifinitura/scrittura"):
                    uscita.metti(lav)
                fatti += 1
                self.ev.progresso("scontorno", fatti, tot)
                if ultimo_utile is not None and i >= ultimo_utile:
                    break
        finally:
            _ferma_lettura(rd, q_in, th)
        return fatti

    def _frame(self, i, rgb, st, a):
        cfg, w, h, bit16 = self.cfg, self.w, self.h, self.bit16
        rgb8 = (rgb >> 8).astype(np.uint8) if bit16 else rgb
        tracce = self.tracce
        m_png = None if tracce is None or i >= len(tracce) else tracce[i]
        lav = {"i": i, "rgb": rgb}
        nuovo_pezzo = i in self.inizio_pezzo or i == a
        if nuovo_pezzo:                                # nuovo fotogramma chiave: la memoria riparte
            st["attivo"] = self.inizio_pezzo.get(self.inizio_di(i), True) if self.inizio_pezzo else True
            st["prev"] = None
            st["mani"].clear()
            if self.mat is not None:
                self.mat.ferma()

        T = self.ev.tempi
        if cfg.motore == "matanyone":
            mat, wl, hl = self.mat, self.wl, self.hl
            T0 = time.perf_counter()
            if st["attivo"] and m_png is not None and mat.proc is None:
                ms = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE)
                lr = cv2.resize(rgb8, (wl, hl), interpolation=cv2.INTER_AREA)
                lav["alpha_lr"] = mat.avvia(lr, cv2.resize(ms, (wl, hl), interpolation=cv2.INTER_NEAREST))
            elif st["attivo"] and mat.proc is not None:
                lr = cv2.resize(rgb8, (wl, hl), interpolation=cv2.INTER_AREA)
                lav["alpha_lr"] = mat.passo(lr)
            if "alpha_lr" in lav:
                if m_png is None and not cfg.no_cancello:
                    lav["alpha_lr"] = None             # SAM dice che qui il soggetto non c'e'
                lav["m_png"] = m_png
            T.aggiungi("MatAnyone", time.perf_counter() - T0)
            if self.mani is not None:
                from .matting import passo_mani
                with T.misura("mani (MatAnyone)"):
                    lav["mani"] = passo_mani(mat, st["mani"], self.mani[i] if i < len(self.mani) else [], lav,
                                             rgb8, w, h, wl, hl, cfg)
        elif cfg.motore == "sagoma":
            if st["attivo"] and m_png is not None:
                ms = cv2.imdecode(m_png, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
                lav["alpha_lr"] = cv2.GaussianBlur(ms, (0, 0), 0.8)
                lav["m_png"] = m_png
        else:
            T0 = time.perf_counter()
            self._isnet(i, rgb, rgb8, m_png, st, lav)
            T.aggiungi("ISNet", time.perf_counter() - T0)
        return lav

    def _isnet(self, i, rgb, rgb8, m_png, st, lav):
        cfg, w, h, bit16, tracce = self.cfg, self.w, self.h, self.bit16, self.tracce
        session, sess_p, remove, pad = self.isn
        if tracce is not None:
            zona = zona_da_sam(m_png, w, h, self.zona_utente) if (m_png is not None and st["attivo"]) else None
        else:
            zona = self.zona_utente or (0, 0, w, h)
        if zona is not None:
            alpha = maschera_isnet(rgb8, session, zona, remove)
            if m_png is not None:
                alpha = filtra_isnet_con_sam(alpha, m_png, zona, w, h)
            if sess_p is not None:
                mp = maschera_isnet(rgb8, sess_p, zona, remove)
                k = max(3, int((zona[3] - zona[1]) * 0.012)) | 1
                mp = cv2.GaussianBlur(cv2.dilate(mp, ellisse(k)), (0, 0), k / 3.0)
                alpha = alpha * np.clip(mp * 1.5, 0, 1)
            X0, Y0 = max(0, zona[0] - pad), max(0, zona[1] - pad)
            X1, Y1 = min(w, zona[2] + pad), min(h, zona[3] + pad)
            prev = st["prev"]
            sub = rifinisci_isnet(rgb8[Y0:Y1, X0:X1], alpha[Y0:Y1, X0:X1].copy(),
                                  None if (cfg.prova or prev is None) else prev[Y0:Y1, X0:X1], cfg, w, h)
            st["prev"] = np.zeros((h, w), np.float32)
            st["prev"][Y0:Y1, X0:X1] = sub
            col = rgb[Y0:Y1, X0:X1].astype(np.float32) / (257.0 if bit16 else 1.0)
            if not cfg.no_decontamina:
                col = decontamina(col, sub)
            lav.update(box=(X0, Y0, X1, Y1), alpha_crop=sub, col_crop=col)
        else:
            st["prev"] = None


def _ferma_lettura(rd, q_in, th_r):
    """Ferma ffmpeg e svuota la coda finche' il thread di lettura non ha finito."""
    rd.kill()
    t_lim = time.time() + 15
    while th_r.is_alive() and time.time() < t_lim:
        try:
            q_in.get(timeout=0.5)
        except queue.Empty:
            pass
    th_r.join(timeout=1)
    try:
        rd.stdout.close()
    except Exception:
        pass
    rd.wait()


# ---------------------------------------------------------------------------
#  copia leggera per l'interfaccia (scorrimento fluido della timeline)
# ---------------------------------------------------------------------------
def crea_proxy(video, dest, ev=None, lato=1280):
    """Copia ridotta, tutta a fotogrammi chiave (salto preciso a qualsiasi frame), con gli stessi
    frame che legge il motore. Restituisce il numero di frame."""
    import subprocess
    from .traccia import dim_proxy
    ev = ev or Eventi()
    info = analizza(video)
    pw, ph = dim_proxy(info["w"], info["h"], lato)
    rd = lettore_frame(video, 0, 0, info["fps_txt"], pw, ph, info["colore"], scala=True)
    tmp = dest + ".part.mp4"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{pw}x{ph}", "-r", info["fps_txt"], "-i", "-", "-c:v", "libx264", "-g", "1",
                            "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    n, fsize = 0, pw * ph * 3
    try:
        while True:
            buf = rd.stdout.read(fsize)
            if len(buf) < fsize:
                break
            enc.stdin.write(buf)
            n += 1
            ev.progresso("preparazione anteprima", n, max(n, info["n_frame"]))
            if n % 5 == 0:
                ev.controlla()
    except BaseException:
        rd.kill()
        enc.kill()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    finally:
        try:
            rd.stdout.close()
        except Exception:
            pass
        rd.wait()
    enc.stdin.close()
    enc.wait()
    os.replace(tmp, dest)
    ev.fine_fase("preparazione anteprima")
    return n


__all__ = ["Config", "Eventi", "Annullato", "ErroreMotore", "analizza", "suggerimenti", "traccia", "render",
           "leggi_cache", "piano_render", "crea_proxy", "normalizza_tappe", "descrivi_tappa", "percorsi_file",
           "cartella_di", "VERSIONE", "MOTORI", "SOGGETTI"]
