# -*- coding: utf-8 -*-
"""Processo di lavoro per l'interfaccia grafica.

Due modi:
  python -m motore.lavoratore lavoro <lavoro.json>   un lavoro (anteprima, traccia, render) e poi esce
  python -m motore.lavoratore servizio               resta acceso: selezione SAM istantanea al clic

Comunica SOLO con righe JSON su stdout. Tutto il resto (messaggi delle librerie, ffmpeg) va su stderr,
che l'interfaccia mostra nei "Dettagli".
"""

import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import json
import signal
import sys
import time
import traceback

# canale riservato ai messaggi JSON: il vero stdout. Da qui in poi fd 1 -> stderr,
# cosi' nessuna libreria puo' sporcare i messaggi.
_CANALE = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr


def manda(**msg):
    _CANALE.write(json.dumps(msg, ensure_ascii=False) + "\n")
    _CANALE.flush()


from .eventi import Annullato, ErroreMotore, Eventi  # noqa: E402


class EventiJSON(Eventi):
    def __init__(self):
        super().__init__()
        self._ultimo = 0.0

    def log(self, msg):
        manda(t="log", msg=str(msg))

    def _mostra(self, fase, i, n, el, eta, extra):
        ora = time.time()
        if i >= n or ora - self._ultimo > 0.2:          # al massimo 5 aggiornamenti al secondo
            self._ultimo = ora
            manda(t="prog", fase=fase, i=int(i), n=int(n), el=round(el, 1), eta=round(eta, 1), extra=extra)

    def fine_fase(self, fase):
        self._t0.pop(fase, None)
        manda(t="fine_fase", fase=fase)

    def file(self, tipo, percorso):
        manda(t="file", tipo=tipo, percorso=percorso)


def lavoro(percorso_json):
    from .pipeline import Config, crea_proxy, render, traccia
    with open(percorso_json, encoding="utf-8") as fh:
        job = json.load(fh)
    ev = EventiJSON()
    signal.signal(signal.SIGTERM, lambda *_: ev.annulla())
    signal.signal(signal.SIGINT, lambda *_: ev.annulla())
    azione = job["azione"]
    try:
        if azione == "proxy":
            n = crea_proxy(job["video"], job["dest"], ev)
            manda(t="fine", risultato={"proxy": job["dest"], "n": n})
        else:
            cfg = Config.da_dizionario(job["config"])
            if azione == "traccia":
                r = traccia(cfg, ev)
                manda(t="fine", risultato={"cache": r["cache"], "completo": True})
            elif azione == "render":
                r = render(cfg, ev)
                manda(t="fine", risultato=r)
            else:
                raise ErroreMotore(f"Azione sconosciuta: {azione}")
        return 0
    except Annullato:
        manda(t="annullato")
        return 2
    except ErroreMotore as e:
        manda(t="errore", msg=str(e), dettagli="")
        return 1
    except Exception as e:
        manda(t="errore", msg=f"{type(e).__name__}: {e}", dettagli=traceback.format_exc())
        return 1


def servizio():
    """Selezione istantanea: SAM 2 su un fotogramma con i clic aggiungi/togli (vedi motore/selezione.py).

    Richiesta: {"cmd": "selezione", "id", "img", "base": png | null, "aggiungi": [[x%, y%]], "togli": [[x%, y%]],
                "out", "modello", "imgsz"}
    Risposta:  {"t": "selezione", "id", "maschera", "altezza", "area"}  oppure  {"t": "errore_sam", ...}
    """
    import cv2
    from .selezione import Selettore, altezza_e_area
    selettori = {}
    manda(t="pronto")
    for riga in sys.stdin:
        riga = riga.strip()
        if not riga:
            continue
        try:
            cmd = json.loads(riga)
        except json.JSONDecodeError:
            continue
        if cmd.get("cmd") == "esci":
            break
        if cmd.get("cmd") != "selezione":
            continue
        try:
            chiave_mod = (cmd.get("modello", "sam2.1_t.pt"), int(cmd.get("imgsz", 1024)))
            if chiave_mod not in selettori:
                manda(t="log", msg=f"Carico {chiave_mod[0]} per la selezione...")
                selettori[chiave_mod] = Selettore(*chiave_mod)
            sel = selettori[chiave_mod]
            img = cv2.imread(cmd["img"])
            if img is None:
                raise RuntimeError(f"immagine non leggibile: {cmd['img']}")
            base = cv2.imread(cmd["base"], cv2.IMREAD_GRAYSCALE) if cmd.get("base") else None
            chiave = (cmd["img"], os.path.getmtime(cmd["img"]))
            m = sel.calcola(img, base, cmd.get("aggiungi") or [], cmd.get("togli") or [], chiave=chiave)
            tmp = cmd["out"] + ".tmp.png"
            cv2.imwrite(tmp, m)
            os.replace(tmp, cmd["out"])
            alt, area = altezza_e_area(m)
            manda(t="selezione", id=cmd.get("id"), maschera=cmd["out"], altezza=alt, area=area)
        except Exception as e:
            manda(t="errore_sam", id=cmd.get("id"), msg=f"{type(e).__name__}: {e}", dettagli=traceback.format_exc())
    return 0


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "lavoro" and len(sys.argv) > 2:
        sys.exit(lavoro(sys.argv[2]))
    if modo == "servizio":
        sys.exit(servizio())
    manda(t="errore", msg="uso: python -m motore.lavoratore lavoro <file.json> | servizio", dettagli="")
    sys.exit(1)
