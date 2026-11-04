# -*- coding: utf-8 -*-
"""Eventi del motore: messaggi, avanzamento, annullamento.

Il motore non stampa direttamente: chiama questi metodi. Il terminale usa la versione
di base (stampa a schermo), l'interfaccia grafica una versione che manda messaggi JSON.
"""

import sys
import threading
import time
from contextlib import contextmanager


class Annullato(Exception):
    """Lavoro interrotto dall'utente."""


class ErroreMotore(Exception):
    """Errore spiegabile all'utente (parametro sbagliato, libreria mancante...)."""


class Tempi:
    """Somma dei tempi per fase (anche da piu' thread): serve a capire dove va il tempo."""

    def __init__(self):
        self._t, self._n, self._lock = {}, {}, threading.Lock()

    @contextmanager
    def misura(self, nome):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.aggiungi(nome, time.perf_counter() - t0)

    def aggiungi(self, nome, secondi):
        with self._lock:
            self._t[nome] = self._t.get(nome, 0.0) + secondi
            self._n[nome] = self._n.get(nome, 0) + 1

    def azzera(self):
        with self._lock:
            self._t.clear()
            self._n.clear()

    def riepilogo(self, frame):
        """Una riga leggibile: millisecondi per frame di ogni fase, dalla piu' lenta."""
        with self._lock:
            voci = sorted(self._t.items(), key=lambda kv: -kv[1])
        if not voci or frame <= 0:
            return ""
        return " · ".join(f"{nome} {t / frame * 1000:.0f} ms" for nome, t in voci if t / frame >= 0.0005)

    def totale(self, nome):
        with self._lock:
            return self._t.get(nome, 0.0)


class Eventi:
    def __init__(self):
        self._annullato = False
        self._riga = False
        self._t0 = {}
        self.tempi = Tempi()

    # --- messaggi -------------------------------------------------------
    def log(self, msg):
        if self._riga:
            print()
            self._riga = False
        print(msg, flush=True)

    def progresso(self, fase, i, n, extra=""):
        """Avanzamento di una fase (i da 1 a n). Calcola da solo tempo trascorso e rimanente."""
        t0 = self._t0.setdefault(fase, time.time())
        el = time.time() - t0
        eta = el / max(1, i) * max(0, n - i)
        self._mostra(fase, i, n, el, eta, extra)

    def fine_fase(self, fase):
        self._t0.pop(fase, None)
        if self._riga:
            print()
            self._riga = False

    def file(self, tipo, percorso):
        self.log(f"  {tipo:18s} -> {percorso}")

    def _mostra(self, fase, i, n, el, eta, extra):
        sys.stdout.write(f"\r  {fase}: frame {i}/{n}   {el:5.0f}s trascorsi, ~{eta:4.0f}s rimanenti {extra}")
        sys.stdout.flush()
        self._riga = True

    # --- annullamento ---------------------------------------------------
    def annulla(self):
        self._annullato = True

    def controlla(self):
        if self._annullato:
            raise Annullato()
