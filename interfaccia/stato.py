# -*- coding: utf-8 -*-
"""Lo stato di un lavoro nell'interfaccia: video, tratto, fotogrammi chiave, zone ignorate, impostazioni.

Si salva da solo in progetto.json (nella cartella di lavoro del video) a ogni modifica: riaprendo lo
stesso video si riparte da dove eri. Lo stesso file si puo' dare al comando da terminale:
    python3 scontorno.py --progetto ".../progetto.json"
"""

import hashlib
import json
import os
import time

from motore import Config, VERSIONE
from motore.modelli import cartella_lavoro
from motore.selezione import VERSIONE_SELEZIONE


def cartella_cache(video):
    return cartella_lavoro(video)


def _firma(ch):
    """Impronta di cio' da cui dipende la selezione di una chiave (clic + maschera di partenza)."""
    return hashlib.sha1(json.dumps([VERSIONE_SELEZIONE, ch.get("aggiungi"), ch.get("togli"),
                                    ch.get("base")]).encode()).hexdigest()[:12]


class Progetto:
    """Un video aperto nell'interfaccia.

    chiavi: frame (del video) -> {
        "assente": bool          da qui il soggetto non c'e'
        "aggiungi": [[x%, y%]]   clic "aggiungi"
        "togli": [[x%, y%]]      clic "togli"
        "base": png | None       correzione: la maschera tracciata in quel frame, da cui si parte
        "maschera": png | None   la selezione risultante (calcolata dal servizio SAM)
        "firma": str | None      per quali clic e' stata calcolata 'maschera'
        "altezza": float         quanto e' alto il soggetto (frazione del frame)
    }
    """

    def __init__(self, video, info):
        self.video = os.path.abspath(video)
        self.info = info
        self.cache = cartella_cache(self.video)
        self.proxy = os.path.join(self.cache, "proxy2.mp4")
        self.file_progetto = os.path.join(self.cache, "progetto.json")
        self.n = 0                              # frame nella copia leggera
        self.fps = info["fps"]
        self.in_f = 0
        self.out_f = 0
        self.chiavi = {}
        self.escludi = []                       # [(x%, y%, l%, a%), ...]
        self.risultato = {}                     # percorsi dell'ultimo render
        self.pannello = {}                      # stato del pannello (per riaprire il progetto)

    # --- tempi ------------------------------------------------------------
    def secondi(self, f):
        return f / self.fps

    def n_tratto(self):
        return self.out_f - self.in_f + 1

    def dentro(self, f):
        return self.in_f <= f <= self.out_f

    def chiave_attiva(self, f):
        """La chiave che vale al frame f (l'ultima che inizia prima o su f)."""
        prima = [g for g in self.chiavi if g <= f]
        return max(prima) if prima else None

    # --- chiavi -----------------------------------------------------------
    def chiave(self, f, base=None):
        """La chiave del frame f, creata se manca (base = maschera da cui partire, per le correzioni)."""
        if f not in self.chiavi:
            self.chiavi[f] = {"assente": False, "aggiungi": [], "togli": [], "base": base,
                              "maschera": None, "firma": None, "altezza": 0.0}
        return self.chiavi[f]

    def firma(self, f):
        return _firma(self.chiavi[f])

    def maschera_valida(self, f):
        ch = self.chiavi.get(f)
        return (ch is not None and not ch["assente"] and ch.get("maschera") and ch.get("firma") == _firma(ch)
                and os.path.isfile(ch["maschera"]))

    def vuota(self, f):
        ch = self.chiavi.get(f)
        return ch is not None and not ch["assente"] and not ch["aggiungi"] and not ch["togli"]

    def con_soggetto(self):
        """Ci sono chiavi con il soggetto dentro il tratto?"""
        return any(self.dentro(g) and not ch["assente"] for g, ch in self.chiavi.items())

    # --- verso il motore -------------------------------------------------
    def config(self, parametri):
        """Config del motore a partire dallo stato dell'interfaccia."""
        c = Config.da_dizionario(parametri)
        c.video = self.video
        c.cartella_cache = self.cache
        # mezzo frame di anticipo: il motore parte esattamente dal frame in_f
        c.inizio = 0.0 if self.in_f == 0 else (self.in_f - 0.5) / self.fps
        c.durata = self.n_tratto() / self.fps
        tappe = []
        for g in sorted(self.chiavi):
            if not self.dentro(g):
                continue
            ch = self.chiavi[g]
            if ch["assente"]:
                tappe.append({"frame": g - self.in_f, "assente": True})
            elif ch["aggiungi"] or ch["togli"]:
                tappe.append({"frame": g - self.in_f, "aggiungi": ch["aggiungi"], "togli": ch["togli"],
                              "base": ch.get("base"),
                              "maschera": ch["maschera"] if self.maschera_valida(g) else None})
        c.tappe = tappe
        c.segui = []
        c.escludi = [",".join(f"{v:.2f}" for v in r) for r in self.escludi]
        return c

    def avvisi(self):
        """Controlli prima di tracciare/esportare (None = tutto ok)."""
        if not self.con_soggetto():
            return "Clicca sul soggetto (con ＋ Aggiungi) nel primo frame del tratto per dire cosa scontornare."
        primo = min(g for g, ch in self.chiavi.items() if self.dentro(g) and not ch["assente"])
        if primo > self.in_f and not any(self.dentro(g) and g < primo for g in self.chiavi):
            return (f"Il primo clic e' a {self.secondi(primo):.2f}s: prima di quel momento lo scontorno sara' "
                    "vuoto. Se il soggetto c'e' gia' dall'inizio del tratto, clicca anche sul primo frame.")
        return None

    # --- salvataggio -----------------------------------------------------
    def salva(self, pannello, config):
        self.pannello = pannello
        d = {"programma": "Scontorno", "versione": VERSIONE, "salvato": time.strftime("%Y-%m-%d %H:%M:%S"),
             "video": self.video,
             "tratto": {"in": self.in_f, "out": self.out_f, "fps": self.fps,
                        "inizio_s": round(self.secondi(self.in_f), 4), "fine_s": round(self.secondi(self.out_f + 1), 4)},
             "chiavi": [dict(ch, frame=g, tempo_s=round(self.secondi(g), 4)) for g, ch in sorted(self.chiavi.items())],
             "zone_ignorate": [list(z) for z in self.escludi],
             "pannello": pannello,
             "config": config,
             "nota": "Le chiavi hanno frame del video intero; in 'config' i frame sono relativi al tratto. "
                     "Da terminale: python3 scontorno.py --progetto <questo file>"}
        tmp = self.file_progetto + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.file_progetto)

    def carica(self):
        """Riprende il lavoro salvato per questo video. Restituisce True se c'era."""
        try:
            with open(self.file_progetto, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return False
        t = d.get("tratto", {})
        self.in_f = int(min(max(0, t.get("in", 0)), max(0, self.n - 1)))
        self.out_f = int(min(max(self.in_f, t.get("out", self.n - 1)), max(0, self.n - 1)))
        self.chiavi = {}
        for ch in d.get("chiavi", []):
            g = int(ch.pop("frame"))
            ch.pop("tempo_s", None)
            ch.setdefault("altezza", 0.0)
            if 0 <= g < self.n:
                self.chiavi[g] = ch
        self.escludi = [tuple(z) for z in d.get("zone_ignorate", [])]
        self.pannello = d.get("pannello", {})
        return True

    def dimentica(self):
        self.chiavi, self.escludi = {}, []
        self.in_f, self.out_f = 0, max(0, self.n - 1)
        try:
            os.remove(self.file_progetto)
        except OSError:
            pass
