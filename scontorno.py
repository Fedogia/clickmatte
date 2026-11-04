#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCONTORNO AUTOMATICO -> MOGRT "Collage Strisce"   (versione 3.3, comando da terminale)
=====================================================================================

C'e' anche l'interfaccia grafica: python3 app.py  (o doppio clic su "Avvia Scontorno.command").
Questo comando usa lo stesso motore (cartella motore/) e da' risultati identici.

Prende un video, scontorna UN soggetto (una persona, anche con oggetti in mano, oppure qualsiasi
oggetto o animale) e produce, dallo stesso identico tratto:

  <nome>_BASE.mov            ProRes 422 HQ                 -> MEDIA · Clip base
  <nome>_SOGGETTO_alpha.mov  ProRes 4444 con canale alfa   -> SOVRAIMPR. 1 · Media
  <nome>_ANTEPRIMA.mp4       il soggetto su scacchiera (1080p), per controllare

Stessa risoluzione, stesso frame rate, stesso numero di frame: in Premiere la
sovraimpressione "Allineata alla clip base" combacia al pixel.

TRE MOTORI
  --motore matanyone  (default per le persone) SAM 2 capisce CHI e' il soggetto e lo segue, MatAnyone 2
                      calcola la trasparenza vera (capelli, bordi mossi) con memoria tra i frame.
                      LICENZA MatAnyone 2: S-Lab 1.0, solo uso NON commerciale.
  --motore sagoma     (default per --soggetto oggetto) la forma tracciata da SAM 2, agganciata ai bordi
                      a piena risoluzione: funziona con qualsiasi cosa. Licenza AGPL-3.0 (Ultralytics).
  --motore isnet      (default senza --segui) frame per frame, licenze libere, ok per lavori commerciali.

LAVORO A PEZZI
  Il tracciamento e lo stato dei file esportati restano in ~/Library/Caches/Scontorno (uguale per
  l'interfaccia grafica). Se aggiungi una correzione (--segui T:...) e rilanci, si ritraccia e si
  riesporta solo da quel punto in avanti; il resto dei file viene riusato senza ricomprimere.
  --da-capo per rifare comunque tutto. --progetto file.json esegue un progetto salvato dall'app.

INSTALLAZIONE (Mac Apple Silicon, una volta sola)
    brew install ffmpeg python
    python3 -m venv ~/scontorno-env
    source ~/scontorno-env/bin/activate
    pip install "rembg[cpu]" opencv-python numpy ultralytics omegaconf hydra-core huggingface_hub safetensors imageio
    git clone https://github.com/pq-yang/MatAnyone2 ~/MatAnyone2
    pip install --no-deps -e ~/MatAnyone2      # "-e" serve: senza, il loro pacchetto da errore
  I modelli (SAM 2, MatAnyone 2) si scaricano da soli al primo uso.

USO
    # 1) prova: tracciamento completo + 3 PNG di controllo + <nome>_TRACCIA.mp4
    python3 scontorno.py video.mov --segui 42,70+45,40 --segui 3.25:- --segui 8.2:25,75+40,30 --prova
    # 2) render finale (riusa il tracciamento gia' calcolato)
    python3 scontorno.py video.mov --segui 42,70+45,40 --segui 3.25:- --segui 8.2:25,75+40,30

  --segui x,y          punto su di te (in % del frame) nel primo frame del tratto
  --segui T:x,y        dal secondo T (tempo del video originale) riparti da questo punto
  --segui T:-          dal secondo T non ci sei (frame trasparenti)
  Consiglio: DUE punti, uno sul busto e uno sul viso (25,75+40,30), su un frame nitido.
  Con un punto solo SAM a volte prende solo la maglia. 'no' davanti = "questo NON sono io":
  --segui 4.0:30,60+no72,70

MANI (automatico con il motore matanyone)
  Durante il tracciamento YOLO-pose trova i polsi; ogni mano viene poi rielaborata da
  MatAnyone su un ritaglio a risoluzione piena (768 px) che la segue, e fusa nell'alfa.
  Nei PNG di prova trovi anche <nome>_PROVA_frameXXXX_manoN.png: originale | prima | dopo | maschera.
  --no-mani per disattivarlo, --mani-risoluzione per cambiare il lato del ritaglio.

Qualita' / velocita' (python3 scontorno.py -h per tutte le opzioni):
  --risoluzione 1080   lato corto a cui lavora MatAnyone (1440 per primi piani, piu' lento)
  --segui-risoluzione  1024 (default) oppure 512: tracciamento ~8x piu' veloce
  --durezza 0..1       rende piu' solido il corpo senza assottigliare i capelli
  --prores-hw          ProRes con l'encoder hardware del Mac (se disponibile)
"""

import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")   # prima di importare torch

import argparse
import sys

from motore import Config, Eventi, Annullato, ErroreMotore, render
from motore.isnet import MODELLI_ISNET


def main():
    ap = argparse.ArgumentParser(description="Scontorno di un soggetto (persona o oggetto) in un video -> clip base + "
                                             "sovraimpressione con alfa (ProRes 4444).")
    ap.add_argument("video", nargs="?", default="", help="file video di partenza (mp4, mov...)")
    ap.add_argument("--inizio", type=float, default=0, help="secondo da cui partire (default 0)")
    ap.add_argument("--durata", type=float, default=0, help="secondi da usare (default: fino alla fine)")
    ap.add_argument("--soggetto", default="persona", choices=["persona", "oggetto"],
                    help="persona (default; anche con oggetti in mano: mani rielaborate a parte) "
                         "oppure oggetto (qualsiasi altra cosa: oggetti, animali, veicoli...)")
    ap.add_argument("--motore", default=None, choices=["matanyone", "isnet", "sagoma"],
                    help="matanyone (default per le persone): SAM 2 + MatAnyone 2, qualita' massima, licenza "
                         "NON commerciale. sagoma (default per gli oggetti): forma tracciata agganciata ai bordi, "
                         "licenza AGPL-3.0 (Ultralytics). isnet (default senza --segui): frame per frame, "
                         "ok uso commerciale")
    ap.add_argument("--segui", action="append", default=[],
                    help="x,y in %% di un punto su di te nel primo frame (SAM 2 ti segue). Ripetibile con un "
                         "tempo: 8.3:40,60 (riparti da li') oppure 2.9:- (da li' non ci sei)")
    ap.add_argument("--segui-modello", default="sam2.1_t.pt",
                    choices=["sam2.1_t.pt", "sam2.1_s.pt", "sam2.1_b.pt", "sam2.1_l.pt"],
                    help="modello SAM 2: t (veloce, default), s, b, l (piu' preciso e lento)")
    ap.add_argument("--segui-risoluzione", type=int, default=1024, choices=[512, 640, 768, 1024],
                    help="risoluzione del tracciamento: 1024 (default) o 512 (circa 8x piu' veloce)")
    # matanyone
    ap.add_argument("--risoluzione", type=int, default=1080,
                    help="lato corto a cui lavora MatAnyone: 1080 (default, ~5 GB di memoria) o 1440 per "
                         "primi piani (~12 GB, piu' lento). Oltre non conviene")
    ap.add_argument("--ingrandimento", default="colore", choices=["colore", "semplice"],
                    help="come portare l'alfa a piena risoluzione: agganciata ai bordi a colori (default) "
                         "o semplice (bicubica)")
    ap.add_argument("--no-cancello", action="store_true",
                    help="non usare la maschera SAM per togliere eventuali altre persone (solo matanyone)")
    ap.add_argument("--no-mani", action="store_true",
                    help="niente secondo passaggio ad alta risoluzione sulle mani (solo matanyone)")
    ap.add_argument("--mani-risoluzione", type=int, default=768,
                    help="lato del ritaglio con cui MatAnyone rielabora ogni mano (default 768)")
    # comuni
    ap.add_argument("--escludi", action="append", default=[],
                    help="x,y,larghezza,altezza in %%: togli questa zona (ripetibile)")
    ap.add_argument("--bordo", type=float, default=0, help="px: negativo stringe, positivo allarga (default 0)")
    ap.add_argument("--durezza", type=float, default=0,
                    help="0..1: corpo piu' solido senza assottigliare i capelli (default 0)")
    ap.add_argument("--sfuma", type=float, default=None, help="px di sfumatura del bordo "
                    "(default 0 con matanyone, 0.8 con isnet)")
    ap.add_argument("--no-decontamina", action="store_true", help="non togliere il colore dello sfondo dal bordo")
    ap.add_argument("--prores-hw", action="store_true",
                    help="usa l'encoder ProRes hardware del Mac se disponibile (altrimenti prores_ks)")
    # solo isnet
    g = ap.add_argument_group("solo motore isnet")
    g.add_argument("--modello", default="isnet-general-use", choices=MODELLI_ISNET, help="modello isnet/rembg")
    g.add_argument("--zona", default="", help="x,y,larghezza,altezza in %%: il soggetto sta qui dentro")
    g.add_argument("--contrasto", type=float, default=1.0, help="durezza lineare del bordo (default 1)")
    g.add_argument("--precisione", type=float, default=4, help="raggio aggancio bordi a 1080p (default 4)")
    g.add_argument("--pulizia", type=float, default=0.15, help="elimina isole sotto questa %% del frame")
    g.add_argument("--stabilizza", type=float, default=0.35, help="0..0.8 anti-sfarfallio (default 0.35)")
    g.add_argument("--solo-persone", action="store_true", help="tieni solo le persone")
    g.add_argument("--solo-principale", action="store_true", help="tieni solo il soggetto piu' grande")
    ap.add_argument("--prova", action="store_true", help="3 PNG di controllo (+ _TRACCIA.mp4), nessun video")
    ap.add_argument("--uscita", default="", help="cartella di destinazione (default: accanto al video)")
    ap.add_argument("--da-capo", action="store_true",
                    help="riesporta tutto anche se e' cambiata solo una parte (di solito non serve)")
    ap.add_argument("--progetto", default="",
                    help="esegue un progetto salvato dall'app (progetto.json): stesse impostazioni e chiavi")
    args = ap.parse_args()
    ev = Eventi()
    if args.progetto:
        import json
        with open(args.progetto, encoding="utf-8") as fh:
            cfg = Config.da_dizionario(json.load(fh)["config"])
        if args.uscita:
            cfg.uscita = args.uscita
        cfg.prova, cfg.rifai_tutto = args.prova, args.da_capo
    else:
        if not args.video:
            ap.error("serve il video (oppure --progetto)")
        if args.motore is None:
            if not args.segui:
                args.motore = "isnet"
                print("Nessun --segui: uso il motore isnet (per seguire un soggetto: --segui 42,70+45,40)")
            else:
                args.motore = "matanyone" if args.soggetto == "persona" else "sagoma"
        if args.sfuma is None:
            args.sfuma = 0.8 if args.motore == "isnet" else 0.0
        args.mani = args.motore == "matanyone" and args.soggetto == "persona" and not args.no_mani
        args.rifai_tutto = args.da_capo
        campi = Config.__dataclass_fields__
        cfg = Config(**{k: v for k, v in vars(args).items() if k in campi})
    try:
        render(cfg, ev)
        if cfg.prova and (cfg.segui or cfg.tappe):        # video di controllo del tracciamento
            from motore.pipeline import analizza, leggi_cache, percorsi_file
            from motore.traccia import video_traccia
            info = analizza(cfg.video)
            tr = leggi_cache(cfg, info)
            if tr and tr["completo"]:
                out = percorsi_file(cfg)["base"].replace("_BASE.mov", "_TRACCIA.mp4")
                video_traccia(cfg, info, tr["tracce"], tr["mani"], out)
                ev.file("TRACCIA", out)
    except ErroreMotore as e:
        sys.exit(str(e))
    except (KeyboardInterrupt, Annullato):
        sys.exit("\nInterrotto.")


if __name__ == "__main__":
    main()
