# -*- coding: utf-8 -*-
"""Esportazione "a pezzi": se cambi solo una parte del tratto, si rifa' solo quella.

Come funziona
  * Ogni frame dello scontorno dipende da: impostazioni (uguali per tutti i frame) + cio' che il
    tracciamento dice in quel frame e nei frame precedenti dello stesso pezzo (MatAnyone ha
    memoria, che riparte da zero a ogni fotogramma chiave).
  * Per ogni frame calcolo un'impronta "a catena" (frame + tutti i precedenti del suo pezzo) e la
    salvo accanto all'elenco dei file esportati (nella cartella di lavoro, non nella tua).
  * Al render successivo confronto le impronte: i frame cambiati (allargati all'inizio del loro
    pezzo) si rifanno; il resto si copia dai file gia' esportati SENZA ricomprimere (ProRes e
    l'anteprima sono "tutti fotogrammi chiave": ogni frame si puo' staccare e riattaccare).
  * La clip BASE non dipende dallo scontorno: resta com'e'.
Se qualcosa non torna (file spostati o modificati, impostazioni diverse, unione non verificata)
si rifa' tutto da capo: piu' lento, mai sbagliato.
"""

import hashlib
import json
import os
import shutil
import subprocess
import uuid

import numpy as np

from .eventi import ErroreMotore

VER_RENDER = "render-2"

# campi della Config che cambiano TUTTI i frame (il tracciamento entra con le impronte dei frame)
CAMPI_GLOBALI = ["motore", "soggetto", "risoluzione", "ingrandimento", "no_cancello", "mani", "mani_risoluzione",
                 "escludi", "bordo", "durezza", "sfuma", "no_decontamina", "prores_hw", "modello", "zona",
                 "contrasto", "precisione", "pulizia", "stabilizza", "solo_persone", "solo_principale"]


def firma_globale(cfg, info, f0, n_tot, versione):
    st = os.stat(cfg.video)
    d = {k: getattr(cfg, k, None) for k in CAMPI_GLOBALI}
    return hashlib.sha1(json.dumps([VER_RENDER, versione, os.path.abspath(cfg.video), st.st_size,
                                    round(st.st_mtime, 3), f0, n_tot, info["w"], info["h"], info["fps_txt"], d],
                                   sort_keys=True, default=str).encode()).hexdigest()


def impronte(tracce, mani, tappe, n_tot, pronto=None):
    """Impronta a catena di ogni frame (vedi sopra). I frame non ancora tracciati hanno impronte
    che non coincidono mai con quelle salvate: risultano sempre da rifare."""
    attivi = {t["frame"]: not t["assente"] for t in (tappe or [])}
    out, prec = [], None
    for i in range(n_tot):
        if i in attivi or prec is None:
            prec = "S" + ("A" if attivi.get(i, True) else "-")
        if pronto is not None and not pronto[i]:
            prec = "?" + uuid.uuid4().hex
            out.append(prec)
            continue
        m = tracce[i] if tracce is not None and i < len(tracce) else None
        dm = hashlib.sha1(bytes(m)).hexdigest()[:20] if m is not None else "-"
        dh = ""
        if mani is not None and i < len(mani) and mani[i]:
            dh = repr([(int(t), round(cx, 4), round(cy, 4), round(l, 4)) for t, cx, cy, l in mani[i]])
        prec = hashlib.sha1((prec + dm + dh).encode()).hexdigest()[:20]
        out.append(prec)
    return out


# ---------------------------------------------------------------------------
#  elenco dei file esportati (nella cartella di lavoro)
# ---------------------------------------------------------------------------
def _file_elenco(cartella):
    return os.path.join(cartella, "esportazioni.json")


def _leggi_elenco(cartella):
    try:
        with open(_file_elenco(cartella), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _stato_file(p):
    st = os.stat(p)
    return [st.st_size, round(st.st_mtime, 3)]


def scrivi_elenco(cartella, percorsi, globale, impr):
    tutti = _leggi_elenco(cartella)
    tutti[os.path.abspath(percorsi["soggetto"])] = {
        "globale": globale, "n": len(impr), "impronte": impr,
        "file": {k: [os.path.abspath(p)] + _stato_file(p) for k, p in percorsi.items()}}
    tmp = _file_elenco(cartella) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(tutti, fh)
    os.replace(tmp, _file_elenco(cartella))


def dimentica(cartella, percorsi):
    tutti = _leggi_elenco(cartella)
    if tutti.pop(os.path.abspath(percorsi["soggetto"]), None) is not None:
        with open(_file_elenco(cartella), "w", encoding="utf-8") as fh:
            json.dump(tutti, fh)


def piano(cartella, percorsi, globale, impr, inizi):
    """Cosa rifare. modo: 'niente' | 'parziale' | 'tutto'; intervalli [(a, b)]; aggiornati[i] = il frame
    i nei file esportati e' gia' giusto."""
    n = len(impr)
    tutto = {"modo": "tutto", "intervalli": [(0, n)], "aggiornati": np.zeros(n, bool)}
    e = _leggi_elenco(cartella).get(os.path.abspath(percorsi["soggetto"]))
    if e is None:
        return dict(tutto, motivo="non ci sono ancora file esportati", esistente=False)
    for k, p in percorsi.items():
        reg = e["file"].get(k)
        if reg is None or not os.path.isfile(p) or _stato_file(p) != reg[1:]:
            return dict(tutto, motivo="i file esportati sono stati spostati, cancellati o modificati",
                        esistente=False)
    tutto["esistente"] = True
    if e["globale"] != globale or e["n"] != n:
        return dict(tutto, motivo="sono cambiate le impostazioni o il tratto")
    vecchie = e["impronte"]
    agg = np.array([impr[i] == vecchie[i] for i in range(n)], bool)
    if agg.all():
        return {"modo": "niente", "intervalli": [], "aggiornati": agg, "motivo": "i file sono gia' aggiornati",
                "esistente": True}
    inizi = sorted(set(inizi) | {0})
    intervalli = []
    i = 0
    while i < n:
        if agg[i]:
            i += 1
            continue
        j = i
        while j < n and not agg[j]:
            j += 1
        a = max(s for s in inizi if s <= i)              # la memoria riparte dall'inizio del pezzo
        if intervalli and a <= intervalli[-1][1]:
            intervalli[-1] = (intervalli[-1][0], max(intervalli[-1][1], j))
        else:
            intervalli.append((a, j))
        i = j
    agg_eff = np.ones(n, bool)
    for a, b in intervalli:
        agg_eff[a:b] = False
    if (~agg_eff).sum() >= n:
        return dict(tutto, motivo="e' cambiato tutto il tratto")
    return {"modo": "parziale", "intervalli": intervalli, "aggiornati": agg_eff,
            "motivo": "rifaccio solo le parti cambiate", "esistente": True}


# ---------------------------------------------------------------------------
#  taglia e cuci senza ricomprimere
# ---------------------------------------------------------------------------
def _sonda(p):
    """time_base, pts del primo frame, tick per frame, numero di frame."""
    d = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#2",
         "-show_entries", "stream=time_base,nb_frames:packet=pts", "-of", "json", p],
        capture_output=True, text=True).stdout or "{}")
    s = d["streams"][0]
    num, den = [int(x) for x in s["time_base"].split("/")]
    pts = [int(pk["pts"]) for pk in d.get("packets", [])[:2]]
    if len(pts) < 2 or pts[1] <= pts[0]:
        raise ErroreMotore(f"non riesco a leggere i tempi di {os.path.basename(p)}")
    return {"tb": (num, den), "p0": pts[0], "T": pts[1] - pts[0], "n": int(s.get("nb_frames") or 0)}


def _estrai(sorgente, K, M, s, fps, dest):
    """Copia i frame K..M-1 di 'sorgente' (tutti fotogrammi chiave) senza ricomprimere."""
    lo, hi = s["p0"] + (K - 0.5) * s["T"], s["p0"] + (M - 0.5) * s["T"]
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if K > 2:
        cmd += ["-ss", f"{(K - 2) / fps:.6f}"]
    cmd += ["-copyts", "-i", sorgente, "-map", "0:v:0", "-c", "copy",
            "-bsf:v", f"noise=drop=lt(pts\\,{lo:.1f})+gte(pts\\,{hi:.1f})", "-frames:v", str(M - K), dest]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ErroreMotore("estrazione non riuscita: " + r.stderr.strip()[-300:])


def cuci(vecchio, pezzi, n, fps, cartella_tmp, dest):
    """Nuovo file = vecchio con i frame degli intervalli sostituiti dai pezzi nuovi.
    pezzi: [(a, b, file con i frame a..b-1)]. Scrive 'dest' e controlla il risultato."""
    s = _sonda(vecchio)
    if s["n"] and s["n"] != n:
        raise ErroreMotore(f"il file esportato ha {s['n']} frame invece di {n}")
    parti, pos = [], 0
    for k, (a, b, f) in enumerate(pezzi):
        if a > pos:
            p = os.path.join(cartella_tmp, f"vecchio_{k}{os.path.splitext(vecchio)[1]}")
            _estrai(vecchio, pos, a, s, fps, p)
            parti.append(p)
        parti.append(f)
        pos = b
    if pos < n:
        p = os.path.join(cartella_tmp, f"vecchio_fine{os.path.splitext(vecchio)[1]}")
        _estrai(vecchio, pos, n, s, fps, p)
        parti.append(p)
    lista = os.path.join(cartella_tmp, "lista.txt")
    with open(lista, "w", encoding="utf-8") as fh:
        for p in parti:
            fh.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lista,
                        "-map", "0:v:0", "-c", "copy", "-bsf:v", f"setts=ts=N*{s['T']}",
                        "-video_track_timescale", str(s["tb"][1] // max(1, s["tb"][0])), dest],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ErroreMotore("unione non riuscita: " + r.stderr.strip()[-300:])
    c = _sonda(dest)
    conta = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_frames,duration_ts", "-of", "json", dest],
                           capture_output=True, text=True).stdout
    st = json.loads(conta)["streams"][0]
    nf, dts = int(st.get("nb_frames") or 0), int(st.get("duration_ts") or 0)
    if nf != n or c["p0"] != 0 or c["T"] != s["T"] or (dts and dts != n * s["T"]):
        raise ErroreMotore(f"verifica non riuscita ({nf} frame invece di {n})")


def pulisci(cartella_tmp):
    shutil.rmtree(cartella_tmp, ignore_errors=True)


__all__ = ["firma_globale", "impronte", "piano", "scrivi_elenco", "dimentica", "cuci", "pulisci"]
