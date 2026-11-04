# -*- coding: utf-8 -*-
"""Motore di Scontorno: tracciamento (SAM 2), scontorno (MatAnyone 2 / isnet / sagoma), file per il MOGRT."""

from .pipeline import (Config, Eventi, Annullato, ErroreMotore, analizza, suggerimenti, traccia, render,
                       leggi_cache, piano_render, crea_proxy, normalizza_tappe, descrivi_tappa, percorsi_file,
                       cartella_di, VERSIONE, MOTORI, SOGGETTI)

__all__ = ["Config", "Eventi", "Annullato", "ErroreMotore", "analizza", "suggerimenti", "traccia", "render",
           "leggi_cache", "piano_render", "crea_proxy", "normalizza_tappe", "descrivi_tappa", "percorsi_file",
           "cartella_di", "VERSIONE", "MOTORI", "SOGGETTI"]
