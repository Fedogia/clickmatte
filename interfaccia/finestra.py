# -*- coding: utf-8 -*-
"""Finestra principale di Scontorno (stile Mac: barra strumenti unita al titolo, menu con le scorciatoie ⌘)."""

import copy
import os
import time
import subprocess
import sys

import cv2
import numpy as np
from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QKeySequence, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

import motore
from . import tema
from .pannello import Pannello
from .processi import CARTELLA_APP, Lavoro, ServizioSAM
from .stato import Progetto
from .timeline import Timeline, miniatura
from .trascina import FileDaTrascinare
from .visore import Visore, sovrapposizione_maschera

ESTENSIONI = (".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".mts")
FASI = {"tracciamento": "Seguo il soggetto", "scontorno": "Scontorno", "download modello": "Scarico il modello",
        "preparazione anteprima": "Preparo il video per la timeline"}
PASSI = ["Scegli il tratto e clicca sul soggetto", "Segui il soggetto", "Controlla e correggi", "Esporta i file"]
MAX_ANNULLA = 100


class Finestra(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scontorno")
        self.resize(1520, 940)
        self.setAcceptDrops(True)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.prog = None
        self.cap = None
        self.cap_ris = None
        self.cur = 0
        self._ultimo_letto = -2
        self._cache_frame = {}
        self._mini_cache = {}
        self.vista = "originale"
        self.tr = None                         # stato del tracciamento (motore.leggi_cache)
        self.piano = None                      # cosa farebbe "Esporta" (motore.piano_render)
        self.azione = None                     # lavoro in corso
        self._dopo_selezione = None            # azione da fare appena le selezioni in corso sono pronte
        self._ricordo = {}                     # frame del video -> ultima maschera tracciata (per il "grigio")
        self._annulla, self._ripeti = [], []   # stati del progetto per ⌘Z / ⇧⌘Z
        self._icone = []                       # (oggetto, simbolo): si ricolorano quando cambia il tema
        self.lavoro = Lavoro(self)
        self.sam = ServizioSAM(self)
        self._timer_play = QTimer(self)
        self._timer_play.timeout.connect(self._avanza_play)
        self._timer_cache = QTimer(self)
        self._timer_cache.setSingleShot(True)
        self._timer_cache.timeout.connect(self._controlla_tracciamento)
        self._timer_mini = QTimer(self)
        self._timer_mini.setSingleShot(True)
        self._timer_mini.timeout.connect(self._miniature)
        self._timer_salva = QTimer(self)
        self._timer_salva.setSingleShot(True)
        self._timer_salva.timeout.connect(self._salva)
        self._costruisci()
        self._collega()
        self._abilita()
        self._aggiorna_passi()
        tema.avvisi.cambiato.connect(self._tema_cambiato)

    # =====================================================================
    #  costruzione
    # =====================================================================
    def _azione(self, testo, simbolo=None, scorciatoia=None, aiuto="", scelta=False, gruppo=None):
        a = QAction(testo, self)
        if simbolo:
            a.setIcon(tema.icona(simbolo))
            self._icone.append((a, simbolo))
        if scorciatoia:
            a.setShortcut(QKeySequence(scorciatoia) if isinstance(scorciatoia, str) else scorciatoia)
        if aiuto:
            a.setToolTip(aiuto + (f"  ({a.shortcut().toString(QKeySequence.NativeText)})" if scorciatoia else ""))
            a.setStatusTip(aiuto)
        if scelta:
            a.setCheckable(True)
        if gruppo is not None:
            gruppo.addAction(a)
        return a

    def _pulsante(self, simbolo, aiuto, testo=""):
        b = QToolButton()
        b.setIcon(tema.icona(simbolo))
        b.setIconSize(QSize(18, 18))
        self._icone.append((b, simbolo))
        if testo:
            b.setText(testo)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b.setToolTip(aiuto)
        b.setAutoRaise(True)
        return b

    def _costruisci(self):
        # --- azioni (stanno sia nella barra strumenti sia nei menu, con le stesse scorciatoie)
        g = QActionGroup(self)
        self.b_agg = self._azione("Aggiungi", "plus.circle", "A", "Clic su una parte del soggetto per includerla. "
                                  "Su un frame già seguito crea una correzione. ⌥ clic = Togli al volo", True, g)
        self.b_tog = self._azione("Togli", "minus.circle", "T", "Clic su una parte da escludere (un'altra persona, "
                                  "un pezzo di sfondo). ⌥ clic = Aggiungi al volo", True, g)
        self.b_zona = self._azione("Ignora zona", "rectangle.dashed", "R", "Trascina un rettangolo: lì non viene mai "
                                   "scontornato nulla, in tutto il tratto. ⌥ clic su una zona = toglila", True, g)
        self.b_agg.setChecked(True)
        gv = QActionGroup(self)
        self.b_orig = self._azione("Originale", "photo", "1", "Il frame com'è", True, gv)
        self.b_sel = self._azione("Selezione", "person.crop.rectangle", "2",
                                  "In rosso ciò che verrà scontornato; in grigio il tracciamento da aggiornare", True, gv)
        self.b_ris = self._azione("Risultato", "checkerboard.rectangle", "3",
                                  "Il soggetto scontornato, dai file esportati", True, gv)
        self.b_orig.setChecked(True)
        self.b_assente = self._azione("Assente da qui", "nosign", None, "Da questo frame il soggetto non è "
                                      "inquadrato: frame trasparenti fino al prossimo fotogramma chiave")
        self.b_elimina = self._azione("Elimina chiave", "trash", QKeySequence(Qt.Key_Backspace),
                                      "Elimina il fotogramma chiave di questo frame")
        self.b_adatta = self._azione("Adatta", "arrow.up.left.and.arrow.down.right", "Ctrl+0",
                                     "Adatta l'immagine alla finestra")
        self.a_annulla = self._azione("Annulla", "arrow.uturn.backward", QKeySequence.Undo, "Annulla l'ultima modifica")
        self.a_ripeti = self._azione("Ripeti", "arrow.uturn.forward", QKeySequence.Redo, "Ripeti")
        self.b_zoom = self._azione("Solo tratto", "magnifyingglass", "Z", "La timeline mostra solo il tratto", True)
        self.a_chiave_prec = self._azione("Fotogramma chiave precedente", None, "Alt+Left")
        self.a_chiave_succ = self._azione("Fotogramma chiave successivo", None, "Alt+Right")
        self.a_sosp_prec = self._azione("Punto da controllare precedente", None, "Shift+S")
        self.a_sosp_succ = self._azione("Punto da controllare successivo", None, "S")
        self.a_in = self._azione("Imposta IN qui", None, "I", "Il tratto da scontornare inizia qui")
        self.a_out = self._azione("Imposta OUT qui", None, "O", "Il tratto da scontornare finisce qui")
        self.a_traccia = self._azione("Segui il soggetto", None, "Ctrl+T")
        self.a_prova = self._azione("Prova questo frame", None, "Ctrl+Shift+P")
        self.a_esporta = self._azione("Esporta i file", None, "Ctrl+E")
        self.a_da_capo = self._azione("Esporta tutto da capo", None, "Ctrl+Shift+E")
        self.a_ferma = self._azione("Ferma il lavoro in corso", None, "Ctrl+.")
        self.a_dettagli = self._azione("Mostra i dettagli del lavoro", None, None, "", True)

        # --- menu
        m = self.menuBar().addMenu("File")
        a = m.addAction("Apri video…", self.scegli_video)
        a.setShortcut(QKeySequence.Open)
        self.menu_recenti = m.addMenu("Apri recenti")
        self.menu_recenti.aboutToShow.connect(self._riempi_recenti)
        m.addAction("Ricomincia da zero su questo video", self._ricomincia)
        m.addSeparator()
        m.addAction("Mostra i file esportati nel Finder", self._mostra_finder)
        m.addAction("File progetto…", self._mostra_progetto)
        m = self.menuBar().addMenu("Modifica")
        for a in (self.a_annulla, self.a_ripeti):
            m.addAction(a)
        m.addSeparator()
        for a in (self.b_agg, self.b_tog, self.b_zona):
            m.addAction(a)
        m.addSeparator()
        for a in (self.b_assente, self.b_elimina, self.a_in, self.a_out):
            m.addAction(a)
        m = self.menuBar().addMenu("Vista")
        for a in (self.b_orig, self.b_sel, self.b_ris):
            m.addAction(a)
        m.addSeparator()
        for a in (self.b_zoom, self.b_adatta, self.a_dettagli):
            m.addAction(a)
        m = self.menuBar().addMenu("Vai")
        for a in (self.a_chiave_prec, self.a_chiave_succ):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_sosp_prec, self.a_sosp_succ):
            m.addAction(a)
        m = self.menuBar().addMenu("Lavoro")
        for a in (self.a_traccia, self.a_prova):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_esporta, self.a_da_capo):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_ferma)
        m = self.menuBar().addMenu("Aiuto")
        m.addAction("Come si usa", self._aiuto)

        # --- barra strumenti (sul Mac unita alla barra del titolo)
        tb = QToolBar("Strumenti")
        tb.setObjectName("strumenti")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        for a in (self.b_agg, self.b_tog, self.b_zona):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.b_orig, self.b_sel, self.b_ris):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.b_assente, self.b_elimina, self.a_annulla):
            tb.addAction(a)
        spazio = QWidget()
        spazio.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spazio)
        tb.addAction(self.b_adatta)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.barra_strumenti = tb

        centro = QWidget()
        cv = QVBoxLayout(centro)
        cv.setContentsMargins(6, 4, 6, 6)
        cv.setSpacing(6)

        self.visore = Visore()
        self.visore.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cv.addWidget(self.visore, 1)

        # trasporto
        tr = QHBoxLayout()
        tr.setSpacing(2)
        self.b_vai_in = self._pulsante("backward.end.fill", "Vai all'inizio del tratto")
        self.b_chiave_prec = self._pulsante("chevron.left", "Fotogramma chiave precedente (⌥←)", "◆")
        self.b_indietro = self._pulsante("chevron.left", "Frame precedente (←, con ⇧ un secondo)")
        self.b_play = self._pulsante("play.fill", "Riproduci / pausa (spazio)")
        self.b_avanti = self._pulsante("chevron.right", "Frame successivo (→, con ⇧ un secondo)")
        self.b_chiave_succ = self._pulsante("chevron.right", "Fotogramma chiave successivo (⌥→)", "◆")
        self.b_vai_out = self._pulsante("forward.end.fill", "Vai alla fine del tratto")
        for b in (self.b_vai_in, self.b_chiave_prec, self.b_indietro, self.b_play, self.b_avanti,
                  self.b_chiave_succ, self.b_vai_out):
            tr.addWidget(b)
        tr.addSpacing(14)
        self.b_sosp = self._pulsante("exclamationmark.triangle", "Vai al prossimo punto da controllare (S; ⇧S il "
                                     "precedente)", "Da controllare")
        self.b_sosp.setVisible(False)
        tr.addWidget(self.b_sosp)
        tr.addStretch(1)
        self.b_set_in = QPushButton("Imposta IN")
        self.b_set_in.setToolTip("Il tratto da scontornare inizia qui (I)")
        self.b_set_out = QPushButton("Imposta OUT")
        self.b_set_out.setToolTip("Il tratto da scontornare finisce qui (O)")
        b_zoom = QToolButton()
        b_zoom.setDefaultAction(self.b_zoom)
        b_zoom.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        b_zoom.setAutoRaise(True)
        for b in (self.b_set_in, self.b_set_out, b_zoom):
            tr.addWidget(b)
        cv.addLayout(tr)

        self.lbl_suggerimento = tema.ruolo(QLabel("Apri un video per cominciare: File › Apri, oppure trascinalo "
                                                  "qui."), "suggerimento")
        self.lbl_suggerimento.setWordWrap(True)
        cv.addWidget(self.lbl_suggerimento)

        self.timeline = Timeline()
        cv.addWidget(self.timeline)

        # pannello destro: i passi in alto, le impostazioni (scorrevoli) in mezzo, le azioni sempre visibili in basso
        destra = QWidget()
        dv = QVBoxLayout(destra)
        dv.setContentsMargins(0, 4, 4, 4)
        self.lbl_passi = tema.ruolo(QLabel(), "passi")
        self.lbl_passi.setTextFormat(Qt.RichText)
        dv.addWidget(self.lbl_passi)

        self.pannello = Pannello()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.pannello)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # il pannello si adatta alla larghezza
        dv.addWidget(scroll, 1)

        riquadro = QFrame()
        riquadro.setFrameShape(QFrame.StyledPanel)
        rv = QVBoxLayout(riquadro)
        rv.addWidget(QLabel("<b>Fotogrammi chiave</b>"))
        spieg = tema.ruolo(QLabel("Da dove il programma riparte: la prima selezione, le correzioni (＋/− su un frame "
                                  "già seguito) e i punti in cui il soggetto esce. Doppio clic per andarci."), "descr")
        spieg.setWordWrap(True)
        rv.addWidget(spieg)
        self.lista_chiavi = QListWidget()
        self.lista_chiavi.setMaximumHeight(96)
        rv.addWidget(self.lista_chiavi)
        self.lbl_traccia = QLabel()
        self.lbl_traccia.setWordWrap(True)
        self.lbl_traccia.setTextFormat(Qt.RichText)
        rv.addWidget(self.lbl_traccia)

        az = QHBoxLayout()
        self.b_traccia = QPushButton("② Segui il soggetto")
        self.b_traccia.setToolTip("Segue il soggetto per tutto il tratto (⌘T). Rifà solo i pezzi cambiati: "
                                  "il resto è già salvato.")
        self.b_prova = QPushButton("Prova questo frame")
        self.b_prova.setToolTip("Scontorna fino a questo frame e mostra un'immagine di controllo (anche delle mani), "
                                "senza creare i video (⇧⌘P)")
        for b in (self.b_traccia, self.b_prova):
            az.addWidget(b)
        rv.addLayout(az)
        az = QHBoxLayout()
        self.b_render = QPushButton("④ Esporta i file")
        self.b_render.setToolTip("Crea BASE, SOGGETTO (con trasparenza) e ANTEPRIMA (⌘E). Se hai già esportato e "
                                 "hai cambiato solo una parte, rifà solo quella.")
        self.b_render.setDefault(True)
        self.b_render_menu = QToolButton()
        self.b_render_menu.setText("⋯")
        self.b_render_menu.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        menu.addAction(self.a_da_capo)
        self.b_render_menu.setMenu(menu)
        az.addWidget(self.b_render, 1)
        az.addWidget(self.b_render_menu)
        rv.addLayout(az)
        self.barra = QProgressBar()
        self.barra.setTextVisible(True)
        self.barra.setVisible(False)
        rv.addWidget(self.barra)
        r = QHBoxLayout()
        self.lbl_stato = QLabel("")
        self.lbl_stato.setWordWrap(True)
        r.addWidget(self.lbl_stato, 1)
        self.b_annulla = QPushButton("Ferma")
        self.b_annulla.setToolTip("Ferma il lavoro (⌘.). Quello che è già pronto resta salvato; i file esportati "
                                  "prima restano com'erano.")
        self.b_annulla.setVisible(False)
        r.addWidget(self.b_annulla)
        rv.addLayout(r)
        self.trascina = FileDaTrascinare()
        rv.addWidget(self.trascina)
        r = QHBoxLayout()
        self.b_finder = QPushButton("Mostra nel Finder")
        self.b_finder.setVisible(False)
        self.b_dettagli = QToolButton()
        self.b_dettagli.setDefaultAction(self.a_dettagli)
        self.b_dettagli.setText("Dettagli")
        self.b_dettagli.setAutoRaise(True)
        r.addWidget(self.b_finder)
        r.addStretch(1)
        r.addWidget(self.b_dettagli)
        rv.addLayout(r)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setVisible(False)
        self.log.setMinimumHeight(140)
        self.log.setMaximumHeight(220)
        rv.addWidget(self.log)
        dv.addWidget(riquadro)
        destra.setMinimumWidth(400)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(centro)
        split.addWidget(destra)
        split.setStretchFactor(0, 1)
        split.setSizes([1100, 420])
        self.setCentralWidget(split)

    def _collega(self):
        self.b_agg.toggled.connect(lambda on: on and setattr(self.visore, "strumento", "aggiungi"))
        self.b_tog.toggled.connect(lambda on: on and setattr(self.visore, "strumento", "togli"))
        self.b_zona.toggled.connect(lambda on: on and setattr(self.visore, "strumento", "zona"))
        self.b_orig.toggled.connect(lambda on: on and self._vista("originale"))
        self.b_sel.toggled.connect(lambda on: on and self._vista("selezione"))
        self.b_ris.toggled.connect(lambda on: on and self._vista("risultato"))
        self.b_adatta.triggered.connect(self.visore.adatta)
        self.b_assente.triggered.connect(self._assente)
        self.b_elimina.triggered.connect(self._elimina_chiave)
        self.a_annulla.triggered.connect(self._annulla_modifica)
        self.a_ripeti.triggered.connect(self._ripeti_modifica)
        self.a_in.triggered.connect(lambda: self._imposta_tratto(self.cur, None))
        self.a_out.triggered.connect(lambda: self._imposta_tratto(None, self.cur))
        self.a_chiave_prec.triggered.connect(lambda: self._salta_chiave(-1))
        self.a_chiave_succ.triggered.connect(lambda: self._salta_chiave(1))
        self.a_sosp_prec.triggered.connect(lambda: self._salta_sospetto(-1))
        self.a_sosp_succ.triggered.connect(lambda: self._salta_sospetto(1))
        self.a_traccia.triggered.connect(self.traccia)
        self.a_prova.triggered.connect(self.prova_qui)
        self.a_esporta.triggered.connect(lambda: self.render(False))
        self.a_da_capo.triggered.connect(lambda: self.render(True))
        self.a_ferma.triggered.connect(self.lavoro.annulla)
        self.a_dettagli.toggled.connect(self.log.setVisible)
        self.b_vai_in.clicked.connect(lambda: self.vai(self.prog.in_f) if self.prog else None)
        self.b_vai_out.clicked.connect(lambda: self.vai(self.prog.out_f) if self.prog else None)
        self.b_chiave_prec.clicked.connect(lambda: self._salta_chiave(-1))
        self.b_chiave_succ.clicked.connect(lambda: self._salta_chiave(1))
        self.b_indietro.clicked.connect(lambda: self.vai(self.cur - 1))
        self.b_avanti.clicked.connect(lambda: self.vai(self.cur + 1))
        self.b_play.clicked.connect(self._play)
        self.b_sosp.clicked.connect(lambda: self._salta_sospetto(1))
        self.b_set_in.clicked.connect(lambda: self._imposta_tratto(self.cur, None))
        self.b_set_out.clicked.connect(lambda: self._imposta_tratto(None, self.cur))
        self.b_zoom.toggled.connect(self._zoom)
        self.timeline.vai_a.connect(self.vai)
        self.timeline.ridimensionata.connect(lambda: self._timer_mini.start(250))
        self.timeline.tratto_cambiato.connect(lambda a, b: self._imposta_tratto(a, b))
        self.visore.punto_aggiunto.connect(self._punto_aggiunto)
        self.visore.punto_tolto.connect(self._punto_tolto)
        self.visore.zona_aggiunta.connect(self._zona_aggiunta)
        self.visore.zona_tolta.connect(self._zona_tolta)
        self.lista_chiavi.itemDoubleClicked.connect(lambda it: self.vai(it.data(Qt.UserRole)))
        self.pannello.cambiato.connect(self._impostazioni_cambiate)
        self.pannello.cambiato_traccia.connect(self._programma_controllo)
        self.pannello.copia_comando.connect(self._copia_comando)
        self.pannello.mostra_progetto.connect(self._mostra_progetto)
        self.b_traccia.clicked.connect(self.traccia)
        self.b_prova.clicked.connect(self.prova_qui)
        self.b_render.clicked.connect(lambda: self.render(False))
        self.b_annulla.clicked.connect(self.lavoro.annulla)
        self.b_finder.clicked.connect(self._mostra_finder)
        self.lavoro.log.connect(self._log)
        self.lavoro.dettaglio.connect(lambda r: self.log.appendPlainText("   " + r))
        self.lavoro.progresso.connect(self._progresso)
        self.lavoro.file.connect(lambda t, p: self._log(f"{t}: {p}"))
        self.lavoro.finito.connect(self._lavoro_finito)
        self.lavoro.errore.connect(self._lavoro_errore)
        self.lavoro.annullato.connect(self._lavoro_annullato)
        self.lavoro.terminato.connect(self._lavoro_terminato)
        self.sam.risposta.connect(self._risposta_sam)
        self.sam.log.connect(lambda t: t and self.log.appendPlainText("   [selezione] " + t))

    def _tema_cambiato(self):
        """Chiaro <-> scuro: ricoloro le icone e ridisegno cio' che disegniamo noi."""
        for ogg, simbolo in self._icone:
            if ogg is self.b_play:
                simbolo = "pause.fill" if self._timer_play.isActive() else "play.fill"
            ogg.setIcon(tema.icona(simbolo))
        self.timeline.update()
        self._aggiorna_passi()
        self._aggiorna_vista()

    # =====================================================================
    #  apertura video
    # =====================================================================
    def scegli_video(self):
        s = QSettings()
        f, _ = QFileDialog.getOpenFileName(self, "Apri video", s.value("ultima_cartella", ""),
                                           "Video (*.mov *.mp4 *.m4v *.mxf *.avi *.mkv *.mts)")
        if f:
            s.setValue("ultima_cartella", os.path.dirname(f))
            self.apri(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(ESTENSIONI) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(ESTENSIONI):
                self.apri(u.toLocalFile())
                break

    def apri(self, percorso):
        if self.lavoro.attivo():
            tema.avvisa(self, "C'è un lavoro in corso.", "Aspetta che finisca, oppure fermalo (⌘.).")
            return
        try:
            info = motore.analizza(percorso)
        except motore.ErroreMotore as e:
            tema.avvisa(self, "Non riesco ad aprire il video.", str(e), errore=True)
            return
        self._salva()
        self._chiudi_video()
        self.prog = Progetto(percorso, info)
        self.pannello.imposta_video(os.path.basename(percorso), info)
        self.setWindowTitle(os.path.basename(percorso))
        self.setWindowFilePath(os.path.abspath(percorso))       # sul Mac: icona del file nel titolo (⌘ clic = percorso)
        self._aggiungi_recente(percorso)
        self._log(f"Aperto {percorso} (Scontorno {motore.VERSIONE})")
        if os.path.isfile(self.prog.proxy):
            self._carica_proxy()
        else:
            self.azione = "proxy"
            self.lavoro.avvia("proxy", video=self.prog.video, dest=self.prog.proxy)
            self._in_corso("Preparo il video per la timeline (solo la prima volta)…")

    def _chiudi_video(self):
        self._timer_play.stop()
        for c in (self.cap, self.cap_ris):
            if c is not None:
                c.release()
        self.cap = self.cap_ris = None
        self._cache_frame.clear()
        self._mini_cache = {}
        self.tr = self.piano = None
        self._ricordo = {}
        self._annulla, self._ripeti = [], []
        self.timeline.trovato = self.timeline.mani = self.timeline.file = None
        self.timeline.tappe = {}
        self.timeline.sospetti = []
        self.timeline.miniature = []
        self.b_finder.setVisible(False)
        self.b_sosp.setVisible(False)
        self.trascina.imposta({})

    # =====================================================================
    #  file recenti, annulla / ripeti
    # =====================================================================
    def _aggiungi_recente(self, percorso):
        s = QSettings()
        lista = [p for p in (s.value("recenti", []) or []) if p != os.path.abspath(percorso)]
        s.setValue("recenti", [os.path.abspath(percorso)] + lista[:9])

    def _riempi_recenti(self):
        self.menu_recenti.clear()
        lista = [p for p in (QSettings().value("recenti", []) or []) if os.path.isfile(p)]
        for p in lista:
            self.menu_recenti.addAction(os.path.basename(p), lambda p=p: self.apri(p)).setToolTip(p)
        if lista:
            self.menu_recenti.addSeparator()
            self.menu_recenti.addAction("Cancella l'elenco",
                                        lambda: QSettings().setValue("recenti", []))
        else:
            self.menu_recenti.addAction("(nessun video recente)").setEnabled(False)

    def _istantanea(self):
        p = self.prog
        return copy.deepcopy({"chiavi": p.chiavi, "escludi": p.escludi, "in": p.in_f, "out": p.out_f})

    def _memorizza(self):
        """Da chiamare PRIMA di ogni modifica: lo stato attuale finisce nella pila di ⌘Z."""
        if not self.prog:
            return
        self._annulla.append(self._istantanea())
        del self._annulla[:-MAX_ANNULLA]
        self._ripeti.clear()
        self._abilita_annulla()

    def _ripristina(self, st):
        p = self.prog
        p.chiavi, p.escludi, p.in_f, p.out_f = st["chiavi"], st["escludi"], st["in"], st["out"]
        self.timeline.in_f, self.timeline.out_f = p.in_f, p.out_f
        self._aggiorna_chiavi()
        self._programma_controllo()
        self._programma_salvataggio()
        self._aggiorna_vista()
        self._abilita_annulla()

    def _annulla_modifica(self):
        if self.prog and self._annulla:
            self._ripeti.append(self._istantanea())
            self._ripristina(self._annulla.pop())
            self.lbl_stato.setText("Modifica annullata (⇧⌘Z per ripeterla).")

    def _ripeti_modifica(self):
        if self.prog and self._ripeti:
            self._annulla.append(self._istantanea())
            self._ripristina(self._ripeti.pop())

    def _abilita_annulla(self):
        self.a_annulla.setEnabled(bool(self._annulla))
        self.a_ripeti.setEnabled(bool(self._ripeti))

    def _carica_proxy(self):
        self.cap = cv2.VideoCapture(self.prog.proxy)
        n = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if n <= 0:
            tema.avvisa(self, "Non riesco a leggere la copia del video.",
                        "Prova File › Ricomincia da zero, oppure cancella la cartella di lavoro.", errore=True)
            return
        self.prog.n = n
        self.prog.in_f, self.prog.out_f = 0, n - 1
        ripreso = self.prog.carica()
        if ripreso:
            self.pannello.imposta_stato(self.prog.pannello)
        self.timeline.imposta(n, self.prog.fps)
        self.timeline.in_f, self.timeline.out_f = self.prog.in_f, self.prog.out_f
        self._miniature()
        self._aggiorna_chiavi()
        self._abilita()
        if ripreso:
            nk = len(self.prog.chiavi)
            self.lbl_stato.setText(f"Ho ripreso il lavoro salvato: tratto {self.prog.secondi(self.prog.in_f):.2f}–"
                                   f"{self.prog.secondi(self.prog.out_f + 1):.2f} s, {nk} fotogramm{'o' if nk == 1 else 'i'} "
                                   "chiave. (File › Ricomincia da zero per partire da capo)")
            primo = min(self.prog.chiavi) if self.prog.chiavi else self.prog.in_f
            self.vai(primo)
            if self.prog.con_soggetto():
                self.b_sel.setChecked(True)
        else:
            self.vai(0)
        self._controlla_tracciamento()

    def _ricomincia(self):
        if not self.prog or self.lavoro.attivo():
            return
        if not tema.chiedi(self, "Ricominciare da zero su questo video?",
                           "Dimentico tratto, fotogrammi chiave e zone ignorate. Il tracciamento già calcolato e i "
                           "file esportati non vengono cancellati. (Si può annullare con ⌘Z)", si="Ricomincia"):
            return
        self._memorizza()
        self.prog.dimentica()
        self.timeline.in_f, self.timeline.out_f = self.prog.in_f, self.prog.out_f
        self._aggiorna_chiavi()
        self._programma_controllo()
        self.vai(0)

    def _miniature(self):
        """Miniature della parte di timeline visibile (tutto il video, o solo il tratto se zoomata)."""
        if not self.prog or not self.prog.n:
            return
        v0, v1 = self.timeline._vista()
        larg = max(200, self.timeline.width() - Timeline.MARG_SX - Timeline.MARG_DX)
        k = max(4, min(80, larg // 72))
        out = []
        for i in range(k):
            pos = v0 + (i + 0.5) * (v1 - v0) / k            # posizione esatta: miniature senza buchi
            f = int(round(pos))
            if f not in self._mini_cache:
                rgb = self._leggi(f)
                if rgb is None:
                    continue
                self._mini_cache[f] = miniatura(rgb, Timeline.H_MINI)
            out.append((pos, self._mini_cache[f]))
        self.timeline.miniature = out
        self.timeline.update()

    # =====================================================================
    #  frame e viste
    # =====================================================================
    def _leggi(self, f, cap=None):
        cap = cap or self.cap
        chiave = (id(cap), f)
        if chiave in self._cache_frame:
            return self._cache_frame[chiave]
        sequenziale = cap is self.cap and f == self._ultimo_letto + 1
        if not sequenziale:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, bgr = cap.read()
        if not ok and sequenziale:                         # lettura di seguito non riuscita: riprovo saltando
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, bgr = cap.read()
        if not ok:
            if cap is self.cap:
                self._ultimo_letto = -2
            return None
        if cap is self.cap:
            self._ultimo_letto = f
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if len(self._cache_frame) > 40:
            self._cache_frame.pop(next(iter(self._cache_frame)))
        self._cache_frame[chiave] = rgb
        return rgb

    def vai(self, f):
        if not self.prog or not self.prog.n:
            return
        f = int(min(max(0, f), self.prog.n - 1))
        self.cur = f
        self.timeline.cur = f
        self.timeline.update()
        self._aggiorna_vista()

    def _vista(self, nome):
        self.vista = nome
        self._aggiorna_vista()

    def _maschera(self, m, rgb, vecchia=False):
        if m is None:
            return None
        if m.shape != rgb.shape[:2]:
            m = cv2.resize(m, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
        if vecchia:                                        # tracciamento da aggiornare: grigio, piu' tenue
            return sovrapposizione_maschera(m, colore=(175, 175, 182), opacita=0.42)
        return sovrapposizione_maschera(m)

    def _vecchia(self, f):
        """Maschera "vecchia" (PNG) del frame f: tracciamento di prima di una correzione, non ancora aggiornato."""
        k = f - self.prog.in_f
        tr = self.tr
        if tr is not None and 0 <= k < len(tr["vecchie"]) and tr["vecchie"][k] is not None:
            return tr["vecchie"][k]
        return self._ricordo.get(f)

    def _sospetto_a(self, f):
        """Il punto da controllare che comprende il frame f (o None)."""
        for a, b, motivo in self.timeline.sospetti:
            if a <= f <= b:
                return a, b, motivo
        return None

    def _aggiorna_vista(self):
        if not self.prog or self.cap is None:
            return
        f = self.cur
        rgb = self._leggi(f)
        if rgb is None:
            return
        sovr, mani_box, scritta = None, [], ""
        dentro = self.prog.dentro(f)
        k = f - self.prog.in_f
        ch = self.prog.chiavi.get(f)
        tr = self.tr
        tracciato = tr is not None and dentro and k < len(tr["tracce"]) and bool(tr["pronto"][k])
        segni = []
        if self.vista == "risultato":
            ris = self.prog.risultato.get("anteprima")
            if ris and dentro and os.path.isfile(ris):
                if self.cap_ris is None:
                    self.cap_ris = cv2.VideoCapture(ris)
                r = self._leggi(k, self.cap_ris)
                if r is not None:
                    rgb = cv2.resize(r, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_AREA)
                if self.piano is not None and k < len(self.piano["aggiornati"]) and not self.piano["aggiornati"][k]:
                    scritta = "Questo frame è cambiato dopo l'esportazione: premi ④ Esporta per aggiornarlo"
            else:
                scritta = "Nessun file esportato per questo frame: premi ④ Esporta" if dentro else "Fuori dal tratto"
        elif self.vista == "selezione":
            if ch is not None and not ch["assente"]:
                if self.prog.maschera_valida(f):
                    sovr = self._maschera(cv2.imread(ch["maschera"], cv2.IMREAD_GRAYSCALE), rgb)
                    scritta = "◆ Fotogramma chiave: da qui il soggetto viene seguito partendo da questa selezione"
                else:
                    if ch.get("base"):
                        sovr = self._maschera(cv2.imread(ch["base"], cv2.IMREAD_GRAYSCALE), rgb)
                    scritta = "◆ Calcolo la selezione…"
            elif tracciato:
                if tr["tracce"][k] is not None:
                    sovr = self._maschera(cv2.imdecode(tr["tracce"][k], cv2.IMREAD_GRAYSCALE), rgb)
                else:
                    scritta = "Qui il soggetto non c'è (o non è stato trovato): se c'è, cliccalo con ＋ Aggiungi"
                mis = tr["misure"][k] if k < len(tr["misure"]) else None
                if mis is not None:
                    segni = [(x, y) for _, x, y in mis.get("f", [])]
                sosp = self._sospetto_a(f)
                if sosp is not None:
                    scritta = (f"⚠ Da controllare: {sosp[2]}. Se manca qualcosa, ＋ Aggiungi qui; se c'è troppo, "
                               "− Togli")
            elif not dentro:
                scritta = "Fuori dal tratto"
            elif self._vecchia(f) is not None:
                sovr = self._maschera(cv2.imdecode(self._vecchia(f), cv2.IMREAD_GRAYSCALE), rgb, vecchia=True)
                scritta = ("Grigio = tracciamento di prima della correzione, da aggiornare con «Segui il soggetto». "
                           "Puoi già correggere anche qui")
            elif self.prog.con_soggetto():
                scritta = "Ancora da seguire: premi «② Segui il soggetto»"
            else:
                scritta = "Clicca sul soggetto con ＋ Aggiungi"
            if tracciato and tr["mani"] is not None and k < len(tr["mani"]):
                ph, pw = rgb.shape[:2]
                for _, cx, cy, lato in tr["mani"][k]:
                    S = lato * ph
                    mani_box.append((min(max(0, cx * ph - S / 2), pw - S), min(max(0, cy * ph - S / 2), ph - S), S))
        if ch is not None and ch["assente"]:
            scritta = "◆ Da qui il soggetto è assente (frame trasparenti fino alla prossima chiave)"
        self.visore.mostra(rgb, sovr)
        punti = []
        if ch is not None and not ch["assente"]:
            punti = [(x, y, 1) for x, y in ch["aggiungi"]] + [(x, y, 0) for x, y in ch["togli"]]
        self.visore.disegna(punti if self.vista != "risultato" else [],
                            self.prog.escludi if self.vista != "risultato" else [], mani_box,
                            segni if self.vista == "selezione" else [])
        self.visore.scritta(scritta)

    def _play(self):
        if self._timer_play.isActive():
            self._timer_play.stop()
            self.b_play.setIcon(tema.icona("play.fill"))
        elif self.prog:
            if self.cur >= self.prog.out_f:
                self.vai(self.prog.in_f)
            self._timer_play.start(int(1000 / self.prog.fps))
            self.b_play.setIcon(tema.icona("pause.fill"))

    def _avanza_play(self):
        if self.cur >= self.prog.out_f:
            self._play()
            return
        self.vai(self.cur + 1)

    def _salta_chiave(self, verso):
        if not self.prog or not self.prog.chiavi:
            return
        g = sorted(self.prog.chiavi)
        cand = [x for x in g if (x > self.cur if verso > 0 else x < self.cur)]
        if cand:
            self.vai(cand[0] if verso > 0 else cand[-1])

    def _salta_sospetto(self, verso):
        sosp = self.timeline.sospetti
        if not self.prog or not sosp:
            return
        inizi = [a for a, _, _ in sosp]
        cand = [x for x in inizi if (x > self.cur if verso > 0 else x < self.cur)]
        if not cand:                                      # ricomincia dal primo / dall'ultimo
            cand = inizi
            verso = -verso if len(inizi) > 1 else verso
        self.b_sel.setChecked(True)
        self.vai(cand[0] if verso > 0 else cand[-1])

    def keyPressEvent(self, e):
        """Tasti senza menu: spazio e frecce (gli altri sono nelle azioni dei menu, con le loro scorciatoie)."""
        k = e.key()
        passo = int(round(self.prog.fps)) if (self.prog and e.modifiers() & Qt.ShiftModifier) else 1
        if k == Qt.Key_Space:
            self._play()
        elif k == Qt.Key_Left and not e.modifiers() & Qt.AltModifier:
            self.vai(self.cur - passo)
        elif k == Qt.Key_Right and not e.modifiers() & Qt.AltModifier:
            self.vai(self.cur + passo)
        elif k == Qt.Key_Delete:
            self._elimina_chiave()
        else:
            super().keyPressEvent(e)

    # =====================================================================
    #  fotogrammi chiave, zone, tratto
    # =====================================================================
    def _snapshot_traccia(self, f):
        """Se il frame f e' gia' tracciato (anche "vecchio", in grigio), salva la maschera: e' la base della
        correzione. Restituisce (percorso, vecchia) oppure (None, False)."""
        k = f - self.prog.in_f
        tr = self.tr
        vecchia = False
        if tr is not None and 0 <= k < len(tr["tracce"]) and tr["pronto"][k] and tr["tracce"][k] is not None:
            dati = bytes(tr["tracce"][k])
        elif self._vecchia(f) is not None:
            dati, vecchia = bytes(self._vecchia(f)), True
        else:
            return None, False
        import hashlib
        p = os.path.join(self.prog.cache, f"base_{f:06d}_{hashlib.sha1(dati).hexdigest()[:10]}.png")
        if not os.path.isfile(p):
            with open(p, "wb") as fh:
                fh.write(dati)
        return p, vecchia

    def _punto_aggiunto(self, x, y, tipo):
        if not self.prog:
            return
        f = self.cur
        if not self.prog.dentro(f):
            self.lbl_stato.setText("Questo frame è fuori dal tratto: sposta IN/OUT oppure vai dentro il tratto.")
            return
        if self.vista != "selezione":
            self.b_sel.setChecked(True)                  # mostra subito la selezione (anche dalla vista Risultato)
        self._memorizza()
        if f not in self.prog.chiavi:
            base, vecchia = self._snapshot_traccia(f)
            self.prog.chiave(f, base)
            if base and vecchia:
                self.lbl_stato.setText(f"Correzione a {self.prog.secondi(f):.2f} s sul tracciamento vecchio (grigio): "
                                       "parto da quella selezione. Puoi correggere altri punti e poi seguire il "
                                       "soggetto una volta sola.")
            elif base:
                self.lbl_stato.setText(f"Correzione a {self.prog.secondi(f):.2f} s: parto da ciò che è stato "
                                       "seguito qui e aggiungo/tolgo. Da qui in avanti si rifà solo questo pezzo.")
        ch = self.prog.chiavi[f]
        if ch["assente"]:
            ch["assente"] = False
        (ch["aggiungi"] if tipo else ch["togli"]).append([round(x, 2), round(y, 2)])
        self._dopo_modifica(selezione=True)

    def _punto_tolto(self, k):
        ch = self.prog.chiavi.get(self.cur) if self.prog else None
        if ch is None or ch["assente"]:
            return
        self._memorizza()
        na = len(ch["aggiungi"])
        if k < na:
            ch["aggiungi"].pop(k)
        elif k - na < len(ch["togli"]):
            ch["togli"].pop(k - na)
        if not ch["aggiungi"] and not ch["togli"]:
            self.prog.chiavi.pop(self.cur)
            self.lbl_stato.setText("Nessun clic rimasto: fotogramma chiave eliminato.")
            self._dopo_modifica(selezione=False)
        else:
            self._dopo_modifica(selezione=True)

    def _assente(self):
        if not self.prog:
            return
        if not self.prog.dentro(self.cur):
            self.lbl_stato.setText("Questo frame è fuori dal tratto.")
            return
        self._memorizza()
        self.prog.chiavi[self.cur] = {"assente": True, "aggiungi": [], "togli": [], "base": None,
                                      "maschera": None, "firma": None, "altezza": 0.0}
        self._dopo_modifica(selezione=False)

    def _elimina_chiave(self):
        if self.prog and self.cur in self.prog.chiavi:
            self._memorizza()
            self.prog.chiavi.pop(self.cur)
            self._dopo_modifica(selezione=False)

    def _dopo_modifica(self, selezione=True):
        self._aggiorna_chiavi()
        ch = self.prog.chiavi.get(self.cur)
        if selezione and ch and not ch["assente"] and (ch["aggiungi"] or ch["togli"]):
            self._chiedi_selezione(self.cur)
        self._programma_controllo()
        self._programma_salvataggio()
        self._aggiorna_vista()

    def _zona_aggiunta(self, x, y, lw, lh):
        self._memorizza()
        self.prog.escludi.append((x, y, lw, lh))
        self.lbl_stato.setText("Zona ignorata: lì non verrà mai scontornato nulla (in tutto il tratto).")
        self._impostazioni_cambiate()
        self._aggiorna_vista()

    def _zona_tolta(self, k):
        if 0 <= k < len(self.prog.escludi):
            self._memorizza()
            self.prog.escludi.pop(k)
        self._impostazioni_cambiate()
        self._aggiorna_vista()

    def _imposta_tratto(self, a, b):
        if not self.prog:
            return
        nuovo_in = self.prog.in_f if a is None else int(min(a, self.prog.out_f))
        nuovo_out = self.prog.out_f if b is None else int(max(b, nuovo_in))
        if (nuovo_in, nuovo_out) != (self.prog.in_f, self.prog.out_f):
            self._memorizza()
        if a is not None:
            self.prog.in_f = int(min(a, self.prog.out_f))
        if b is not None:
            self.prog.out_f = int(max(b, self.prog.in_f))
        self.timeline.in_f, self.timeline.out_f = self.prog.in_f, self.prog.out_f
        if self.timeline.zoom_tratto:
            self._miniature()
        self.timeline.update()
        self._aggiorna_chiavi()
        self._programma_controllo()
        self._programma_salvataggio()
        self._aggiorna_vista()

    def _tipo_chiave(self, ch):
        if ch["assente"]:
            return "assente"
        return "correzione" if ch.get("base") else "selezione"

    def _aggiorna_chiavi(self):
        self.lista_chiavi.clear()
        if not self.prog:
            return
        self.timeline.tappe = {g: self._tipo_chiave(ch) for g, ch in self.prog.chiavi.items()}
        self.timeline.update()
        for g in sorted(self.prog.chiavi):
            ch = self.prog.chiavi[g]
            tipo = self._tipo_chiave(ch)
            t = f"◆ {self.prog.secondi(g):6.2f} s · "
            if tipo == "assente":
                t += "soggetto assente da qui"
            else:
                parti = []
                if ch["aggiungi"]:
                    parti.append(f"{len(ch['aggiungi'])} ＋")
                if ch["togli"]:
                    parti.append(f"{len(ch['togli'])} −")
                t += f"{tipo} ({', '.join(parti)})"
                if not self.prog.maschera_valida(g):
                    t += " …"
            if not self.prog.dentro(g):
                t += "   (fuori dal tratto: ignorato)"
            it = QListWidgetItem(t)
            it.setData(Qt.UserRole, g)
            self.lista_chiavi.addItem(it)
        self._prossimo_passo()

    # =====================================================================
    #  selezione istantanea (SAM su un frame)
    # =====================================================================
    def _chiedi_selezione(self, f):
        rgb = self._leggi(f)
        ch = self.prog.chiavi.get(f)
        if rgb is None or ch is None:
            return
        img = os.path.join(self.prog.cache, f"frame_{f:06d}.png")
        if not os.path.isfile(img):
            cv2.imwrite(img, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        firma = self.prog.firma(f)
        out = os.path.join(self.prog.cache, f"sel_{f:06d}_{firma}.png")
        if os.path.isfile(out):                           # gia' calcolata (es. un clic tolto e rimesso)
            m = cv2.imread(out, cv2.IMREAD_GRAYSCALE)
            from motore.selezione import altezza_e_area
            alt, area = altezza_e_area(m)
            self._applica_selezione(f, firma, out, alt, area)
            return
        par = self.pannello.parametri()
        self.sam.chiedi(img, ch.get("base"), ch["aggiungi"], ch["togli"], out, par["segui_modello"],
                        par["segui_risoluzione"], frame=f, firma=firma)
        self.lbl_stato.setText("Calcolo la selezione… (la prima volta carica il modello, qualche secondo)")

    def _risposta_sam(self, m):
        req = m.get("richiesta") or {}
        if m.get("t") == "errore_sam":
            self.lbl_stato.setText("Selezione non riuscita: " + m.get("msg", ""))
            if m.get("dettagli"):
                self.log.appendPlainText(m["dettagli"])
        else:
            self._applica_selezione(req.get("frame"), req.get("firma"), m["maschera"], m.get("altezza", 0),
                                    m.get("area", 0))
        if not self.sam.occupato() and self._dopo_selezione is not None:
            azione, self._dopo_selezione = self._dopo_selezione, None
            azione()

    def _applica_selezione(self, f, firma, maschera, altezza, area):
        if not self.prog or f not in self.prog.chiavi or self.prog.firma(f) != firma:
            return                                         # risposta vecchia: nel frattempo hai cliccato ancora
        ch = self.prog.chiavi[f]
        ch["maschera"], ch["firma"], ch["altezza"] = maschera, firma, altezza
        if area <= 0:
            self.lbl_stato.setText("La selezione è vuota: clicca più al centro del soggetto.")
        else:
            self.lbl_stato.setText(f"Selezione pronta (il soggetto occupa il {altezza * 100:.0f}% dell'altezza). "
                                   "Manca una parte? ＋ Aggiungi lì. C'è troppo? − Togli.")
        self.pannello.suggerisci(motore.suggerimenti(self.prog.info, altezza))
        self._aggiorna_chiavi()
        self._programma_controllo()
        self._programma_salvataggio()
        if f == self.cur:
            self._aggiorna_vista()

    # =====================================================================
    #  stato del tracciamento e dei file esportati
    # =====================================================================
    def _impostazioni_cambiate(self):
        self._programma_controllo()
        self._programma_salvataggio()

    def _programma_controllo(self):
        self._timer_cache.start(350)

    def _programma_salvataggio(self):
        self._timer_salva.start(600)

    def _salva(self):
        if self.prog and self.prog.n:
            try:
                self.prog.salva(self.pannello.stato(), self._cfg().dizionario())
            except Exception as e:  # il salvataggio non deve mai bloccare il lavoro
                self.log.appendPlainText(f"   (salvataggio progetto non riuscito: {e})")

    def _cfg(self):
        return self.prog.config(self.pannello.parametri())

    def _t(self, k):
        """Frame del tratto -> secondi del video."""
        return self.prog.secondi(self.prog.in_f + k)

    def _controlla_tracciamento(self):
        if not self.prog or not self.prog.n:
            return
        cfg = self._cfg()
        info = self.prog.info
        try:
            self.tr = motore.leggi_cache(cfg, info) if self.prog.con_soggetto() else None
        except Exception as e:
            self.tr = None
            self.log.appendPlainText(f"   (lettura del tracciamento: {e})")
        try:
            self.piano = motore.piano_render(cfg, info, self.tr) if self.tr is not None else None
        except Exception as e:
            self.piano = None
            self.log.appendPlainText(f"   (piano di esportazione: {e})")
        n, a0 = self.prog.n, self.prog.in_f
        tr = self.tr
        ok, avv = tema.colore("ok"), tema.colore("avviso")
        self.timeline.sospetti = []
        if tr is None:
            self.timeline.mani = self.timeline.file = None
            trov = np.full(n, -2, np.int8)
            for g in range(a0, self.prog.out_f + 1):          # anche senza chiavi: il vecchio tracciamento in grigio
                if g in self._ricordo:
                    trov[g] = 2
            self.timeline.trovato = trov if (trov == 2).any() else None
            self.lbl_traccia.setText("Tracciamento: <b>da fare</b>")
            self.b_traccia.setText("② Segui il soggetto")
            self.b_sosp.setVisible(False)
            self.trascina.imposta({})
        else:
            trov = np.full(n, -2, np.int8)                 # -2 = fuori dal tratto (non si disegna)
            mani = np.zeros(n, bool)
            m = min(len(tr["tracce"]), n - a0)
            trov[a0:a0 + m] = -1
            for k in range(m):
                g = a0 + k
                if tr["pronto"][k]:
                    trov[g] = 1 if tr["tracce"][k] is not None else 0
                elif self._vecchia(g) is not None:
                    trov[g] = 2                            # vecchio tracciamento, da aggiornare: grigio
                if tr["mani"] is not None and tr["mani"][k]:
                    mani[g] = True
            for k in range(m):                             # ricordo l'ultimo tracciamento valido di ogni frame
                if tr["pronto"][k]:
                    if tr["tracce"][k] is not None:
                        self._ricordo[a0 + k] = tr["tracce"][k]
                    else:
                        self._ricordo.pop(a0 + k, None)
            self.timeline.trovato = trov
            self.timeline.sospetti = [(a0 + a, a0 + b, motivo) for a, b, motivo in tr.get("sospetti", [])]
            ns = len(self.timeline.sospetti)
            self.b_sosp.setVisible(ns > 0)
            self.b_sosp.setText(f"{ns} da controllare")
            self.timeline.mani = mani if tr["mani"] is not None else None
            mani_on = self.pannello.parametri()["mani"]
            if not mani_on:
                self.timeline.mani = None
            if tr["completo"]:
                tot = int((trov == 1).sum())
                self.lbl_traccia.setText(f"Tracciamento: <b style='color:{ok}'>pronto ✓</b> · soggetto trovato in "
                                         f"{tot} frame su {self.prog.n_tratto()}" +
                                         (f" · mani in {int(mani.sum())} frame" if (tr["mani"] is not None and mani_on)
                                          else "") +
                                         (f" · <b style='color:{avv}'>⚠ {ns} da controllare</b>" if ns else ""))
                self.b_traccia.setText("✓ Soggetto seguito")
            else:
                da = tr["da_fare"]
                quanti = sum(b - a for a, b in da)
                fatto = self.prog.n_tratto() - quanti
                txt = ", ".join(f"{self._t(a):.2f}–{self._t(b):.2f} s" for a, b in da[:3]) + (" …" if len(da) > 3 else "")
                self.lbl_traccia.setText(f"Tracciamento: <b style='color:{avv}'>da fare</b> in {txt}"
                                         + (f" · il resto è già pronto ({fatto} frame)" if fatto > 0 else "")
                                         + (" · in grigio il tracciamento vecchio" if (trov == 2).any() else ""))
                self.b_traccia.setText("② Segui il soggetto" + (f" (da {self._t(da[0][0]):.2f} s)" if fatto > 0 else ""))
            # file esportati
            p = self.piano
            if p is not None and p.get("esistente"):
                arr = np.full(n, -1, np.int8)
                ag = p["aggiornati"]
                for k in range(min(len(ag), n - a0)):
                    arr[a0 + k] = 1 if ag[k] else 0
                self.timeline.file = arr
                self.prog.risultato = dict(p["percorsi"])
                self.b_finder.setVisible(True)
                self.trascina.imposta(self.prog.risultato)
            else:
                self.timeline.file = None
                self.trascina.imposta({})
        p = self.piano
        if p is None or p["modo"] == "tutto":
            self.b_render.setText("④ Esporta i file")
        elif p["modo"] == "parziale":
            txt = ", ".join(f"{self._t(a):.2f}–{self._t(b):.2f} s" for a, b in p["intervalli"][:2])
            self.b_render.setText(f"④ Esporta (rifaccio solo {txt}{' …' if len(p['intervalli']) > 2 else ''})")
        else:
            self.b_render.setText("✓ File aggiornati")
        self._abilita()
        self.timeline.update()
        self._aggiorna_vista()
        self._aggiorna_chiavi()
        self._aggiorna_passi()

    def _zoom(self, on):
        self.timeline.zoom_tratto = on
        self._miniature()
        self.vai(self.cur)

    # =====================================================================
    #  guida: i quattro passi e il suggerimento
    # =====================================================================
    def _passo_corrente(self):
        if not self.prog or not self.prog.n:
            return 0
        if not self.prog.con_soggetto():
            return 1
        if self.tr is None or not self.tr["completo"]:
            return 2
        if self.piano is None or self.piano["modo"] != "niente":
            return 3
        return 5

    def _aggiorna_passi(self):
        c = self._passo_corrente()
        righe = ["<b>Come si fa</b>"]
        ok, ora, poi = tema.colore("ok"), tema.colore("accento"), tema.colore("secondario")
        for i, t in enumerate(PASSI, 1):
            if i < c or c == 5:
                righe.append(f"<span style='color:{ok}'>✓ {i}. {t}</span>")
            elif i == c or (c == 3 and i == 4):
                righe.append(f"<span style='color:{ora}'><b>▶ {i}. {t}</b></span>")
            else:
                righe.append(f"<span style='color:{poi}'>○ {i}. {t}</span>")
        self.lbl_passi.setText("<br>".join(righe))
        self._prossimo_passo()

    def _prossimo_passo(self):
        """Il riquadro sotto il video dice sempre cosa fare adesso."""
        if not self.prog or not self.prog.n:
            return
        c = self._passo_corrente()
        if c == 1:
            testo = ("① Scegli il tratto (trascina IN e OUT sulla timeline), poi clicca sul soggetto con ＋ Aggiungi. "
                     "Più clic sulle varie parti = selezione più completa. Se tiene qualcosa in mano, clicca anche quello.")
        elif c == 2:
            avv = self.prog.avvisi()
            testo = "② Premi «Segui il soggetto»: viene seguito per tutto il tratto." + (f"  ⚠ {avv}" if avv else "")
            if self.tr is not None and self.tr["da_fare"] and self.tr["pronto"].any():
                testo = (f"② Premi «{self.b_traccia.text()[2:]}»: rifaccio solo i pezzi cambiati, il resto è già pronto.")
                if self.timeline.trovato is not None and (self.timeline.trovato == 2).any():
                    testo += (" Prima puoi correggere anche altri punti sul tracciamento vecchio (grigio): "
                              "poi lo segui una volta sola.")
        elif c == 3:
            testo = ("③ Scorri la timeline in vista Selezione. Manca una parte (un braccio, un oggetto)? ＋ Aggiungi su "
                     "quel frame. C'è qualcosa di troppo? − Togli. Si rifà solo da lì. Poi ④ Esporta.")
            ns = len(self.timeline.sospetti)
            if ns:
                testo = (f"③ ⚠ {ns} punt{'o' if ns == 1 else 'i'} da controllare sulla timeline: premi S per andarci. "
                         "Se manca una parte, ＋ Aggiungi lì; se c'è troppo, − Togli. Poi ④ Esporta.")
            if self.piano is not None and self.piano["modo"] == "parziale":
                testo = f"④ {self.b_render.text()[2:]}: il resto dei file è già pronto e non viene ricompresso."
        else:
            testo = ("Fatto ✓  Vista Risultato per controllare; trascina i file in Premiere dal riquadro a destra. Se "
                     "correggi qualcosa, l'esportazione rifà solo quella parte.")
            ns = len(self.timeline.sospetti)
            if ns:
                testo += f"  (⚠ {ns} punt{'o' if ns == 1 else 'i'} da controllare: tasto S)"
        self.lbl_suggerimento.setText(testo)

    # =====================================================================
    #  lavori: traccia, prova, esporta
    # =====================================================================
    def _pronto_per(self, azione):
        if not self.prog or not self.prog.n or self.lavoro.attivo():
            return False
        if not self.prog.con_soggetto():
            tema.avvisa(self, "Prima indica cosa scontornare.",
                        "Clicca sul soggetto con ＋ Aggiungi nel primo frame del tratto.")
            return False
        if self.sam.occupato():                          # aspetto le selezioni in corso, poi parto da solo
            self._dopo_selezione = azione
            self.lbl_stato.setText("Aspetto che la selezione sia pronta, poi parto…")
            return False
        self._salva()
        return True

    def traccia(self):
        if not self._pronto_per(self.traccia):
            return
        self.azione = "traccia"
        self.lavoro.avvia("traccia", config=self._cfg().dizionario())
        self._in_corso("Seguo il soggetto…")

    def prova_qui(self):
        if not self.prog or not self.prog.dentro(self.cur):
            tema.avvisa(self, "Questo frame è fuori dal tratto.", "Porta la testina tra IN e OUT.")
            return
        if not self._pronto_per(self.prova_qui):
            return
        cfg = self._cfg()
        cfg.prova = True
        cfg.frame_prova = [self.cur - self.prog.in_f]
        self.azione = "prova"
        self.lavoro.avvia("render", config=cfg.dizionario())
        self._in_corso("Prova su questo frame…")

    def render(self, da_capo=False):
        if not self._pronto_per(lambda: self.render(da_capo)):
            return
        avv = self.prog.avvisi()
        if avv and not tema.chiedi(self, "Esporto lo stesso?", avv, si="Esporta"):
            return
        if self.piano is not None and self.piano["modo"] == "niente" and not da_capo:
            tema.avvisa(self, "I file esportati sono già aggiornati.",
                        "Non c'è niente da rifare. Per rifarli comunque: Lavoro › Esporta tutto da capo (⇧⌘E).")
            return
        if self.cap_ris is not None:
            self.cap_ris.release()
            self.cap_ris = None
            self._cache_frame = {k: v for k, v in self._cache_frame.items() if k[0] == id(self.cap)}
        cfg = self._cfg()
        cfg.rifai_tutto = bool(da_capo)
        self.azione = "render"
        self.lavoro.avvia("render", config=cfg.dizionario())
        self._in_corso("Esporto…")

    def _in_corso(self, testo):
        self._t_inizio_lavoro = time.time()
        self.barra.setVisible(True)
        self.barra.setRange(0, 0)
        self.b_annulla.setVisible(True)
        self.lbl_stato.setText(testo)
        self._abilita()

    def _progresso(self, fase, i, n, el, eta, extra):
        self.barra.setRange(0, max(1, n))
        self.barra.setValue(i)
        nome = FASI.get(fase, fase.capitalize())
        if fase == "download modello":
            self.barra.setFormat(f"{nome}  %p%")
            self.lbl_stato.setText(f"{nome}…")
            return
        self.barra.setFormat(f"{nome}  {i}/{n}")
        self.lbl_stato.setText(f"{nome}: {i}/{n} · {el:.0f}s trascorsi · ~{eta:.0f}s rimanenti {extra}")
        if fase == "tracciamento" and i % 25 == 0:
            self._programma_controllo()                    # la timeline si riempie man mano che i pezzi finiscono

    def _log(self, testo):
        self.log.appendPlainText(testo)
        if not testo.startswith(" "):
            self.lbl_stato.setText(testo)

    def _lavoro_finito(self, ris):
        if self.azione == "proxy":
            self._carica_proxy()
            if not self.prog.chiavi:
                self.lbl_stato.setText("Pronto.")
        elif self.azione == "traccia":
            self._controlla_tracciamento()
            self.b_sel.setChecked(True)
            ns = len(self.timeline.sospetti)
            self.lbl_stato.setText("Soggetto seguito: scorri la timeline e controlla la selezione rossa. "
                                   + (f"⚠ {ns} punt{'o' if ns == 1 else 'i'} da controllare (tasto S)." if ns else
                                      "Se manca qualcosa, ＋ Aggiungi su quel frame."))
            self._notifica("Soggetto seguito", f"{ns} punti da controllare" if ns else "Pronto da controllare")
        elif self.azione == "prova":
            self._controlla_tracciamento()
            self._notifica("Prova pronta", "Le immagini di controllo sono pronte")
            self._mostra_prove(ris.get("prove", []))
        elif self.azione == "render":
            self.prog.risultato = {k: ris[k] for k in ("base", "soggetto", "anteprima") if k in ris}
            self._controlla_tracciamento()
            self.b_finder.setVisible(True)
            self.b_ris.setChecked(True)
            rif = ris.get("rifatti")
            n = self.prog.n_tratto()
            if rif == []:
                testo = "Niente da rifare: i file erano già aggiornati."
            elif rif and not (len(rif) == 1 and rif[0][0] == 0 and rif[0][1] >= n):
                testo = ("Esportazione aggiornata: rifatti solo " + ", ".join(
                    f"{self._t(a):.2f}–{self._t(b):.2f} s" for a, b in rif) + "; il resto era già pronto. "
                    "Se Premiere mostra ancora la versione vecchia, chiudi e riapri il progetto.")
            else:
                testo = ("Esportazione finita. Trascina BASE + SOGGETTO in Premiere dal riquadro qui sotto. Nel MOGRT: "
                         "BASE in «MEDIA · Clip base», SOGGETTO in «SOVRAIMPR. 1 · Media», Inquadratura «Allineata "
                         "alla clip base».")
            self.lbl_stato.setText(testo)
            self._notifica("Esportazione finita", testo.split(".")[0])

    def _notifica(self, titolo, testo):
        """Avviso di sistema solo per i lavori lunghi e se nel frattempo stai usando un'altra app."""
        if time.time() - getattr(self, "_t_inizio_lavoro", time.time()) > 15:
            tema.notifica(self, f"Scontorno · {titolo}", testo)

    def _lavoro_errore(self, msg, dettagli):
        self._notifica("Errore", msg)
        if dettagli:
            self.log.appendPlainText(dettagli)
        tema.avvisa(self, "Il lavoro si è fermato per un errore.", msg + "\n\n(Dettagli completi: Vista › "
                    "Mostra i dettagli del lavoro)", errore=True)
        self.lbl_stato.setText("Errore: " + msg)

    def _lavoro_annullato(self):
        self._log("Annullato. Quello che era già pronto resta salvato; i file esportati prima sono intatti.")

    def _lavoro_terminato(self):
        self.barra.setVisible(False)
        self.b_annulla.setVisible(False)
        azione = self.azione
        self.azione = None
        if azione in ("traccia", "render", "prova"):
            self._programma_controllo()
        self._abilita()

    def _abilita(self):
        occupato = self.lavoro.attivo()
        ok = self.prog is not None and self.prog.n > 0
        completo = self.tr is not None and self.tr["completo"]
        self.b_traccia.setEnabled(ok and not occupato and not completo)
        self.b_prova.setEnabled(ok and not occupato)
        self.b_render.setEnabled(ok and not occupato and not (self.piano is not None and self.piano["modo"] == "niente"))
        self.b_render_menu.setEnabled(ok and not occupato)
        self.a_traccia.setEnabled(self.b_traccia.isEnabled())
        self.a_prova.setEnabled(ok and not occupato)
        self.a_esporta.setEnabled(self.b_render.isEnabled())
        self.a_da_capo.setEnabled(ok and not occupato)
        self.a_ferma.setEnabled(occupato)
        for w in (self.b_assente, self.b_elimina, self.b_set_in, self.b_set_out, self.b_vai_in, self.b_vai_out,
                  self.b_play, self.b_indietro, self.b_avanti, self.b_chiave_prec, self.b_chiave_succ, self.a_in,
                  self.a_out, self.a_chiave_prec, self.a_chiave_succ, self.a_sosp_prec, self.a_sosp_succ):
            w.setEnabled(ok)
        self._abilita_annulla()

    # =====================================================================
    #  finestre di servizio
    # =====================================================================
    def _mostra_prove(self, prove):
        if not prove:
            tema.avvisa(self, "La prova non ha prodotto immagini.", "Il frame è fuori dal tratto?")
            return
        d = QDialog(self)
        d.setWindowTitle("Prova")
        d.resize(1300, 800)
        v = QVBoxLayout(d)
        area = QScrollArea()
        area.setWidgetResizable(True)
        cont = QWidget()
        cv = QVBoxLayout(cont)
        for p in prove:
            nome = os.path.basename(p)
            didascalia = ("originale | prima | dopo | trasparenza" if "_mano" in nome else
                          "originale | scontorno su scacchiera | trasparenza (bianco = pieno)")
            cv.addWidget(QLabel(f"<b>{nome}</b> — {didascalia}"))
            lab = QLabel()
            pm = QPixmap(p)
            if not pm.isNull():
                lab.setPixmap(pm.scaledToWidth(1240, Qt.SmoothTransformation))
            cv.addWidget(lab)
        area.setWidget(cont)
        v.addWidget(area)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Chiudi")
        apri = bb.addButton("Apri la cartella", QDialogButtonBox.ActionRole)
        apri.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(prove[0]))))
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        d.exec()

    def _mostra_finder(self):
        f = self.prog.risultato.get("soggetto") if self.prog else None
        if f and os.path.exists(f):
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", f])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(f)))

    def _comando(self):
        return (f'cd "{CARTELLA_APP}" && source ~/scontorno-env/bin/activate && '
                f'python3 scontorno.py --progetto "{self.prog.file_progetto}"')

    def _copia_comando(self):
        if not self.prog:
            return
        self._salva()
        QGuiApplication.clipboard().setText(self._comando())
        self.lbl_stato.setText("Comando copiato negli appunti: incollalo nel Terminale per rifare questo lavoro "
                               "senza interfaccia.")

    def _mostra_progetto(self):
        if not self.prog:
            return
        self._salva()
        d = QDialog(self)
        d.setWindowTitle("File progetto")
        d.resize(820, 640)
        v = QVBoxLayout(d)
        lab = QLabel(f"<b>{self.prog.file_progetto}</b><br>Si salva da solo a ogni modifica. Contiene tratto, "
                     "fotogrammi chiave, zone ignorate e tutte le impostazioni (in «config», con gli stessi nomi "
                     "del comando da terminale).")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(lab)
        t = QPlainTextEdit()
        t.setReadOnly(True)
        try:
            with open(self.prog.file_progetto, encoding="utf-8") as fh:
                t.setPlainText(fh.read())
        except OSError as e:
            t.setPlainText(str(e))
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Chiudi")
        b = bb.addButton("Copia comando da terminale", QDialogButtonBox.ActionRole)
        b.clicked.connect(self._copia_comando)
        if sys.platform == "darwin":
            b2 = bb.addButton("Mostra nel Finder", QDialogButtonBox.ActionRole)
            b2.clicked.connect(lambda: subprocess.run(["open", "-R", self.prog.file_progetto]))
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        d.exec()

    def _aiuto(self):
        tema.avvisa(self, "Come si usa", (
            "I QUATTRO PASSI\n"
            "1. Scegli il tratto (trascina IN e OUT) e clicca sul soggetto con ＋ Aggiungi. Più clic sulle varie "
            "parti = selezione più completa (busto + viso + l'oggetto in mano).\n"
            "2. Segui il soggetto (⌘T).\n"
            "3. Controlla in vista Selezione. I ⚠ sulla timeline segnano i punti sospetti (S = vai al prossimo). "
            "Se manca qualcosa, ＋ Aggiungi su quel frame; se c'è troppo, − Togli: nasce una correzione e si rifà "
            "solo da lì. Dopo una correzione il vecchio tracciamento resta in grigio: puoi correggere altri punti "
            "prima di aggiornare. Dove il soggetto esce: Assente da qui.\n"
            "4. Esporta (⌘E): dopo una correzione rifà solo la parte cambiata. Poi trascina i file in Premiere "
            "dal riquadro in basso a destra.\n\n"
            "TASTI\n"
            "A ＋ Aggiungi · T − Togli · R zona ignorata · ⌥ clic = strumento opposto · clic su un punto = toglilo\n"
            "1 / 2 / 3 vista Originale / Selezione / Risultato · rotella zoom · ⇧ trascina sposta · ⌘0 adatta\n"
            "← → un frame (⇧ un secondo) · spazio riproduci · ⌥← ⌥→ chiave prec./succ. · S / ⇧S punto da controllare\n"
            "I / O imposta IN / OUT · Z solo tratto · ⌫ elimina la chiave · ⌘Z / ⇧⌘Z annulla / ripeti\n\n"
            "Il lavoro si salva da solo: riaprendo lo stesso video riparti da dove eri."))

    def closeEvent(self, e):
        if self.lavoro.attivo():
            if not tema.chiedi(self, "C'è un lavoro in corso.", "Lo fermo ed esco? Quello che è già pronto resta "
                               "salvato.", si="Ferma ed esci"):
                e.ignore()
                return
            self.lavoro.annulla()
            self.lavoro.p.waitForFinished(15000)
        self._salva()
        self.sam.chiudi()
        self._chiudi_video()
        super().closeEvent(e)
