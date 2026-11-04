# -*- coding: utf-8 -*-
"""Pannello delle impostazioni, scritto per chi non sa nulla di scontorno.

Sopra: tre domande semplici (cosa scontorni, per chi e' il video, quanta qualita') e i bordi a parole.
Sotto, chiuso: "Per esperti", con i nomi tecnici esatti (gli stessi del file progetto e del comando
da terminale), cosi' anche un programma o un'altra persona sa esattamente cosa e' stato impostato.
"""

from PySide6.QtCore import QSettings, Qt, Signal
from . import tema
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget, QDoubleSpinBox)

SOGGETTI = [("persona", "Una persona (anche con oggetti in mano)"),
            ("oggetto", "Un oggetto, un animale o altro")]
USI = [("personale", "Uso personale, prove, portfolio"),
       ("cliente", "Lavoro per un cliente (commerciale)")]
QUALITA = [("veloce", "Veloce"), ("bilanciata", "Bilanciata"), ("massima", "Massima")]
MOTORI = [("auto", "Automatico (consigliato)"),
          ("matanyone", "MatAnyone 2 — trasparenze vere, solo uso NON commerciale"),
          ("isnet", "ISNet — frame per frame, ok per lavori commerciali"),
          ("sagoma", "Sagoma — forma del tracciamento, qualsiasi soggetto, licenza AGPL-3.0")]

DESCRIZIONE_SOGGETTO = {
    "persona": "Il programma riconosce anche le mani e le rielabora a parte (dita piu' precise). "
               "Se tiene qualcosa in mano (telefono, tazza...), clicca anche sull'oggetto.",
    "oggetto": "Qualsiasi cosa: un prodotto, un animale, un'auto, un logo... Il programma segue la forma "
               "che selezioni con i clic.",
}

DESCRIZIONE_MOTORE = {
    "matanyone": ("Motore: <b>MatAnyone 2</b> — trasparenze vere (capelli, mani mosse), memoria tra i frame. "
                  "<span style='color:{avviso}'>Licenza: solo uso NON commerciale.</span>"),
    "isnet": "Motore: <b>ISNet</b> — calcola ogni frame da solo. Licenza libera: ok per i clienti.",
    "sagoma": ("Motore: <b>Sagoma</b> — la forma seguita dal tracciamento, rifinita sui bordi a piena "
               "risoluzione. Funziona con qualsiasi soggetto. Licenza AGPL-3.0 (Ultralytics): "
               "per i clienti verifica le condizioni."),
}

DESCRIZIONE_QUALITA = {
    "veloce": "Lavora a 720p, senza il passaggio sulle mani. Per provare in fretta.",
    "bilanciata": "Lavora a 1080p, con il passaggio dedicato alle mani. Va bene quasi sempre.",
    "massima": "Lavora a 1440p: piu' dettaglio su capelli e dita nei primi piani. Piu' lenta, ~12 GB di memoria.",
    "personalizzata": "Impostazioni cambiate a mano in 'Per esperti'.",
}

SPIEGA = {
    "motore": "Il 'cervello' che calcola la trasparenza. Automatico sceglie in base alle due domande sopra.",
    "risoluzione": "Risoluzione (lato corto) a cui lavora MatAnyone. L'alfa viene poi portata a piena risoluzione "
                   "agganciandola ai bordi. 1080 ~5 GB di memoria, 1440 ~12 GB.",
    "mani": "Rielabora ogni mano su un ritaglio a piena risoluzione che la segue: dita molto piu' precise.",
    "mani_risoluzione": "Lato in pixel del ritaglio con cui si rielabora ogni mano (768 e' il migliore provato).",
    "segui_modello": "Modello che segue il soggetto (SAM 2.1). t = veloce; s, b, l = piu' preciso e piu' lento.",
    "segui_risoluzione": "Precisione del tracciamento. 1024 e' il piu' sicuro; 512 e' molto piu' veloce ma sui "
                         "frame mossi a volte prende solo una parte del soggetto.",
    "no_cancello": "Spuntato: tutto cio' che il tracciamento non segue (altre persone, sfondo) viene tolto. "
                   "Toglilo solo se lo scontorno 'mangia' parti del soggetto.",
    "ingrandimento": "Come si porta l'alfa a piena risoluzione: 'colore' la aggancia ai bordi dell'immagine "
                     "(consigliato), 'semplice' la ingrandisce e basta.",
    "prores_hw": "Usa l'encoder ProRes del Mac (piu' veloce) se funziona anche con l'alfa; altrimenti prores_ks.",
    "contrasto": "Solo ISNet: rende il bordo piu' netto (1 = com'e').",
    "stabilizza": "Solo ISNet: riduce lo sfarfallio del bordo tra un frame e l'altro (0 = spento).",
    "bordo": "Sposta il contorno verso l'interno (se resta un alone di sfondo) o verso l'esterno "
             "(se il soggetto risulta 'mangiato'). In pixel del video.",
    "sfuma": "Sfuma il contorno di qualche pixel. 0 = come lo calcola il motore.",
    "durezza": "Capelli e mani in movimento sono in parte trasparenti. Verso destra il soggetto diventa piu' "
               "pieno (i capelli restano fini).",
    "colore": "Toglie il colore dello sfondo che si vede attraverso i bordi semitrasparenti (dita, capelli).",
}


def _descr(testo):
    lab = QLabel(testo)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    return tema.ruolo(lab, "descr")


def _etichetta(testo, chiave):
    lab = QLabel(f"{testo}<br><span style='font-family:Menlo,monospace; font-size:10px'>{chiave}</span>")
    lab.setTextFormat(Qt.RichText)
    lab.setToolTip(SPIEGA.get(chiave, ""))
    return lab


class _Cursore(QWidget):
    """Slider con le parole ai due estremi e il valore, piu' una riga di spiegazione sotto."""

    def __init__(self, titolo, sx, dx, minimo, massimo, scala, unita, chiave, decimali=0):
        super().__init__()
        self.scala, self.unita, self.decimali = scala, unita, decimali
        g = QGridLayout(self)
        g.setContentsMargins(0, 2, 0, 6)
        g.setVerticalSpacing(1)
        t = QLabel(f"<b>{titolo}</b>")
        self.val = QLabel()
        self.val.setAlignment(Qt.AlignRight)
        g.addWidget(t, 0, 0, 1, 2)
        g.addWidget(self.val, 0, 2)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(minimo, massimo)
        self.s.setToolTip(SPIEGA[chiave] + f"\n\n(nel file progetto: {chiave})")
        g.addWidget(self.s, 1, 0, 1, 3)
        a, b = QLabel(sx), QLabel(dx)
        for lab in (a, b):
            tema.ruolo(lab, "descr")
        b.setAlignment(Qt.AlignRight)
        g.addWidget(a, 2, 0)
        g.addWidget(b, 2, 2)
        g.addWidget(_descr(SPIEGA[chiave]), 3, 0, 1, 3)
        self.s.valueChanged.connect(self._mostra)
        self._mostra()

    def valore(self):
        return self.s.value() / self.scala

    def imposta(self, v):
        self.s.setValue(int(round(float(v) * self.scala)))

    def _mostra(self, *_):
        v = self.valore()
        self.val.setText(("+" if v > 0 and self.unita == " px" and self.s.minimum() < 0 else "")
                         + f"{v:.{self.decimali}f}{self.unita}")


class Sezione(QWidget):
    """Sezione richiudibile, come nell'Inspector delle app Apple: titolo con triangolino, contenuto sotto.
    Ricorda se l'avevi chiusa."""

    def __init__(self, titolo, aperta=True):
        super().__init__()
        self._chiave = "sezioni/" + titolo
        aperta = QSettings().value(self._chiave, aperta, type=bool)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)
        self.titolo = QToolButton()
        self.titolo.setText(titolo)
        self.titolo.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.titolo.setAutoRaise(True)
        f = self.titolo.font()
        f.setBold(True)
        self.titolo.setFont(f)
        self.titolo.clicked.connect(lambda: self.apri(not self.corpo.isVisible()))
        v.addWidget(self.titolo)
        self.corpo = QWidget()
        self.contenuto = QVBoxLayout(self.corpo)
        self.contenuto.setContentsMargins(14, 0, 2, 6)
        v.addWidget(self.corpo)
        linea = QFrame()
        linea.setFrameShape(QFrame.HLine)
        linea.setFrameShadow(QFrame.Sunken)
        v.addWidget(linea)
        self.apri(aperta, salva=False)

    def apri(self, si, salva=True):
        self.corpo.setVisible(si)
        self.titolo.setArrowType(Qt.DownArrow if si else Qt.RightArrow)
        if salva:
            QSettings().setValue(self._chiave, bool(si))

    def setChecked(self, si):            # compatibilita' con chi apriva "Per esperti" come un pulsante
        self.apri(si)


class Pannello(QWidget):
    cambiato = Signal()                   # un'impostazione e' cambiata
    cambiato_traccia = Signal()           # e' cambiato qualcosa che influenza il tracciamento
    copia_comando = Signal()
    mostra_progetto = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._carico = False
        self._info = None
        self._suggerita = None
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.info = QLabel("Nessun video")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        v.addWidget(self.info)

        # --- le tre domande
        g = Sezione("Cosa scontorni?")
        f = g.contenuto
        self.soggetto = QComboBox()
        for k, t in SOGGETTI:
            self.soggetto.addItem(t, k)
        f.addWidget(self.soggetto)
        self.desc_soggetto = _descr("")
        f.addWidget(self.desc_soggetto)
        v.addWidget(g)

        g = Sezione("Per chi è il video?")
        f = g.contenuto
        self.uso = QComboBox()
        for k, t in USI:
            self.uso.addItem(t, k)
        f.addWidget(self.uso)
        self.desc_motore = _descr("")
        f.addWidget(self.desc_motore)
        v.addWidget(g)

        g = Sezione("Qualità")
        f = g.contenuto
        self.qualita = QComboBox()
        for k, t in QUALITA:
            self.qualita.addItem(t, k)
        f.addWidget(self.qualita)
        self.desc_qualita = _descr("")
        f.addWidget(self.desc_qualita)
        self.consiglio = tema.ruolo(_descr(""), "info")
        f.addWidget(self.consiglio)
        self.applica = QPushButton("Usa il consiglio")
        self.applica.setVisible(False)
        f.addWidget(self.applica)
        v.addWidget(g)

        # --- bordi
        g = Sezione("Bordi")
        f = g.contenuto
        self.bordo = _Cursore("Bordo", "◀ più stretto", "più largo ▶", -20, 20, 1, " px", "bordo")
        self.sfuma = _Cursore("Morbidezza", "netta", "morbida", 0, 50, 5, " px", "sfuma", 1)
        self.durezza = _Cursore("Solidità", "trasparenze naturali", "tutto pieno", 0, 100, 100, "", "durezza", 2)
        for w in (self.bordo, self.sfuma, self.durezza):
            f.addWidget(w)
        self.colore = QCheckBox("Pulisci i colori dei bordi (consigliato)")
        self.colore.setChecked(True)
        self.colore.setToolTip(SPIEGA["colore"] + "\n\n(nel file progetto: no_decontamina)")
        f.addWidget(self.colore)
        f.addWidget(_descr(SPIEGA["colore"]))
        v.addWidget(g)

        # --- uscita
        g = Sezione("Dove salvo i file")
        f = g.contenuto
        r = QHBoxLayout()
        self.uscita = QLineEdit()
        self.uscita.setPlaceholderText("nella stessa cartella del video")
        b = QPushButton("Scegli…")
        b.clicked.connect(self._scegli_uscita)
        r.addWidget(self.uscita)
        r.addWidget(b)
        f.addLayout(r)
        self.nomi = _descr("")
        f.addWidget(self.nomi)
        v.addWidget(g)

        # --- per esperti (chiuso)
        self.av = Sezione("Per esperti", aperta=False)
        f = QFormLayout()
        self.av.contenuto.addLayout(f)
        f.addRow(_descr("Sotto ogni voce c'è il nome esatto usato nel file progetto e, con «--» davanti e «-» "
                        "al posto di «_», nel comando da terminale (es. segui_risoluzione → --segui-risoluzione)."))
        self.motore = QComboBox()
        for k, t in MOTORI:
            self.motore.addItem(t, k)
        f.addRow(_etichetta("Motore", "motore"), self.motore)
        self.avviso_licenza = tema.ruolo(_descr(""), "errore")
        f.addRow(self.avviso_licenza)
        self.risoluzione = QComboBox()
        self.risoluzione.addItems(["720", "1080", "1440", "2160"])
        self.risoluzione.setCurrentText("1080")
        f.addRow(_etichetta("Risoluzione di lavoro", "risoluzione"), self.risoluzione)
        self.mani = QCheckBox("attivo")
        self.mani.setChecked(True)
        f.addRow(_etichetta("Passaggio sulle mani", "mani"), self.mani)
        self.mani_ris = QSpinBox()
        self.mani_ris.setRange(384, 1024)
        self.mani_ris.setSingleStep(64)
        self.mani_ris.setValue(768)
        self.mani_ris.setSuffix(" px")
        f.addRow(_etichetta("Ritaglio delle mani", "mani_risoluzione"), self.mani_ris)
        self.modello_sam = QComboBox()
        self.modello_sam.addItems(["sam2.1_t.pt", "sam2.1_s.pt", "sam2.1_b.pt", "sam2.1_l.pt"])
        f.addRow(_etichetta("Modello di tracciamento", "segui_modello"), self.modello_sam)
        self.segui_ris = QComboBox()
        self.segui_ris.addItems(["1024", "768", "512"])
        f.addRow(_etichetta("Precisione del tracciamento", "segui_risoluzione"), self.segui_ris)
        self.cancello = QCheckBox("togli ciò che non è seguito")
        self.cancello.setChecked(True)
        f.addRow(_etichetta("Limite del tracciamento", "no_cancello"), self.cancello)
        self.ingrandimento = QComboBox()
        self.ingrandimento.addItems(["colore", "semplice"])
        f.addRow(_etichetta("Ingrandimento dell'alfa", "ingrandimento"), self.ingrandimento)
        self.prores_hw = QCheckBox("se disponibile")
        f.addRow(_etichetta("ProRes hardware", "prores_hw"), self.prores_hw)
        self.contrasto = QDoubleSpinBox()
        self.contrasto.setRange(0.5, 3)
        self.contrasto.setSingleStep(0.1)
        self.contrasto.setValue(1.6)
        self.lbl_contrasto = _etichetta("Nettezza del bordo (ISNet)", "contrasto")
        f.addRow(self.lbl_contrasto, self.contrasto)
        self.stabilizza = QDoubleSpinBox()
        self.stabilizza.setRange(0, 0.8)
        self.stabilizza.setSingleStep(0.05)
        self.stabilizza.setValue(0.35)
        self.lbl_stabilizza = _etichetta("Anti-sfarfallio (ISNet)", "stabilizza")
        f.addRow(self.lbl_stabilizza, self.stabilizza)
        for w, k in ((self.risoluzione, "risoluzione"), (self.mani, "mani"), (self.mani_ris, "mani_risoluzione"),
                     (self.modello_sam, "segui_modello"), (self.segui_ris, "segui_risoluzione"),
                     (self.cancello, "no_cancello"), (self.ingrandimento, "ingrandimento"),
                     (self.prores_hw, "prores_hw"), (self.contrasto, "contrasto"), (self.stabilizza, "stabilizza"),
                     (self.motore, "motore")):
            w.setToolTip(SPIEGA[k])
        r = QHBoxLayout()
        b1 = QPushButton("Copia comando da terminale")
        b1.setToolTip("Copia negli appunti il comando che rifà questo lavoro senza interfaccia "
                      "(usa il file progetto, che si salva da solo)")
        b1.clicked.connect(self.copia_comando)
        b2 = QPushButton("File progetto")
        b2.setToolTip("Mostra il file progetto.json: tutte le impostazioni e i fotogrammi chiave, leggibili "
                      "anche da un altro programma")
        b2.clicked.connect(self.mostra_progetto)
        r.addWidget(b1)
        r.addWidget(b2)
        f.addRow(r)
        v.addWidget(self.av)
        v.addStretch(1)
        for c in self.findChildren(QComboBox):          # le tendine non allargano il pannello
            c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(10)

        # collegamenti
        self.soggetto.currentIndexChanged.connect(self._cambio_soggetto)
        self.uso.currentIndexChanged.connect(self._cambio_motore)
        self.motore.currentIndexChanged.connect(self._cambio_motore)
        self.qualita.currentIndexChanged.connect(self._applica_qualita)
        self.applica.clicked.connect(self._applica_consiglio)
        for w in (self.risoluzione, self.ingrandimento):
            w.currentTextChanged.connect(self._cambio_esperto)
        self.mani.toggled.connect(self._cambio_esperto)
        self.mani_ris.valueChanged.connect(self._cambio_esperto)
        for w in (self.segui_ris, self.modello_sam):
            w.currentTextChanged.connect(self._cambio_traccia)
        for w in (self.colore, self.cancello, self.prores_hw):
            w.toggled.connect(self._cambio)
        for w in (self.bordo.s, self.sfuma.s, self.durezza.s):
            w.valueChanged.connect(self._cambio)
        for w in (self.contrasto, self.stabilizza):
            w.valueChanged.connect(self._cambio)
        self.uscita.textChanged.connect(self._cambio)
        self._cambio_soggetto()
        self._applica_qualita()
        tema.avvisi.cambiato.connect(self._cambio_motore)      # chiaro/scuro: ricoloro le descrizioni

    # --- comportamento ------------------------------------------------------
    def _scegli_uscita(self):
        d = QFileDialog.getExistingDirectory(self, "Dove salvo i file", self.uscita.text() or "")
        if d:
            self.uscita.setText(d)

    def _cambio(self, *_):
        if not self._carico:
            self._aggiorna_testi()
            self.cambiato.emit()

    def _cambio_traccia(self, *_):
        self._cambio()
        if not self._carico:
            self.cambiato_traccia.emit()

    def _cambio_esperto(self, *_):
        """Un'impostazione che dipende dalla qualita' cambiata a mano -> 'Personalizzata'."""
        if not self._carico and self.qualita.currentData() != "personalizzata":
            self._carico = True
            if self.qualita.findData("personalizzata") < 0:
                self.qualita.addItem("Personalizzata", "personalizzata")
            self.qualita.setCurrentIndex(self.qualita.findData("personalizzata"))
            self._carico = False
        self._cambio()

    def _cambio_soggetto(self, *_):
        self.desc_soggetto.setText(DESCRIZIONE_SOGGETTO[self.soggetto.currentData()])
        self._cambio_motore()
        if not self._carico:
            self.cambiato_traccia.emit()

    def _cambio_motore(self, *_):
        m = self.motore_effettivo()
        persona = self.soggetto.currentData() == "persona"
        self.desc_motore.setText(DESCRIZIONE_MOTORE[m].replace("{avviso}", tema.colore("avviso")))
        for w in (self.risoluzione, self.ingrandimento, self.qualita):
            w.setEnabled(m == "matanyone")
        for w in (self.mani, self.mani_ris):
            w.setEnabled(m == "matanyone" and persona)
        for w in (self.lbl_contrasto, self.contrasto, self.lbl_stabilizza, self.stabilizza):
            w.setVisible(m == "isnet")
        self.avviso_licenza.setText("Attenzione: MatAnyone 2 non si può usare per lavori commerciali."
                                    if (m == "matanyone" and self.uso.currentData() == "cliente") else "")
        self._cambio()

    def motore_effettivo(self):
        m = self.motore.currentData()
        if m != "auto":
            return m
        if self.soggetto.currentData() == "oggetto":
            return "sagoma"
        return "isnet" if self.uso.currentData() == "cliente" else "matanyone"

    def _applica_qualita(self, *_):
        q = self.qualita.currentData()
        if q in ("veloce", "bilanciata", "massima"):
            prima, self._carico = self._carico, True
            alta = 1440
            if self._info is not None:
                alta = min(1440, min(self._info["w"], self._info["h"]))
            self.risoluzione.setCurrentText({"veloce": "720", "bilanciata": "1080", "massima": str(alta)}[q])
            self.mani.setChecked(q != "veloce")
            self.mani_ris.setValue(768)
            i = self.qualita.findData("personalizzata")
            if i >= 0:
                self.qualita.removeItem(i)
            self._carico = prima
        self._cambio()

    def _aggiorna_testi(self):
        m = self.motore_effettivo()
        q = self.qualita.currentData()
        if m == "matanyone":
            self.desc_qualita.setText(DESCRIZIONE_QUALITA.get(q, ""))
        else:
            self.desc_qualita.setText(f"Con il motore {m.capitalize()} la qualità non cambia nulla "
                                      "(lavora sempre a piena risoluzione).")

    def imposta_video(self, nome, info):
        self._info = info
        righe = [f"<b>{nome}</b>",
                 f"{info['w']}×{info['h']} · {info['fps']:.3f} fps · {info['bit']} bit · "
                 f"{info['durata']:.2f} s · {info['colore']['cs']}"]
        for a in info.get("avvisi", []):
            righe.append(f"<span style='color:#c9a227'>⚠ {a}</span>")
        self.info.setText("<br>".join(righe))
        import os
        base = os.path.splitext(nome)[0]
        self.nomi.setText(f"Creo: <b>{base}_BASE.mov</b> (clip base) · <b>{base}_SOGGETTO_alpha.mov</b> "
                          f"(soggetto con trasparenza) · <b>{base}_ANTEPRIMA.mp4</b> (per controllare).<br>"
                          "In Premiere: BASE → «MEDIA · Clip base»; SOGGETTO → «SOVRAIMPR. 1 · Media», "
                          "Inquadratura «Allineata alla clip base».")
        self._applica_qualita()

    def suggerisci(self, sugg):
        """sugg: dizionario da motore.suggerimenti()."""
        self._suggerita = sugg.get("qualita")
        if (self._suggerita and self._suggerita != self.qualita.currentData() and sugg.get("motivo")
                and self.motore_effettivo() == "matanyone"):
            self.consiglio.setText("💡 " + sugg["motivo"])
            self.applica.setText(f"Usa «{dict(QUALITA)[self._suggerita]}»")
            self.applica.setVisible(True)
        else:
            self.consiglio.setText("")
            self.applica.setVisible(False)

    def _applica_consiglio(self):
        if self._suggerita:
            self.qualita.setCurrentIndex(self.qualita.findData(self._suggerita))
            self.applica.setVisible(False)
            self.consiglio.setText("")

    # --- verso il motore --------------------------------------------------------
    def parametri(self):
        m = self.motore_effettivo()
        persona = self.soggetto.currentData() == "persona"
        return dict(
            soggetto=self.soggetto.currentData(),
            motore=m,
            uscita=self.uscita.text().strip(),
            segui_modello=self.modello_sam.currentText(),
            segui_risoluzione=int(self.segui_ris.currentText()),
            risoluzione=int(self.risoluzione.currentText()),
            ingrandimento=self.ingrandimento.currentText(),
            no_cancello=not self.cancello.isChecked(),
            mani=(m == "matanyone") and persona and self.mani.isChecked(),
            mani_risoluzione=self.mani_ris.value(),
            bordo=float(self.bordo.valore()),
            durezza=float(self.durezza.valore()),
            sfuma=float(self.sfuma.valore()),
            no_decontamina=not self.colore.isChecked(),
            prores_hw=self.prores_hw.isChecked(),
            contrasto=float(self.contrasto.value()) if m == "isnet" else 1.0,
            stabilizza=float(self.stabilizza.value()),
        )

    # --- salvataggio dello stato (file progetto) --------------------------------
    def stato(self):
        return {"soggetto": self.soggetto.currentData(), "uso": self.uso.currentData(),
                "qualita": self.qualita.currentData(), "motore": self.motore.currentData(),
                "risoluzione": self.risoluzione.currentText(), "mani": self.mani.isChecked(),
                "mani_risoluzione": self.mani_ris.value(), "segui_modello": self.modello_sam.currentText(),
                "segui_risoluzione": self.segui_ris.currentText(), "cancello": self.cancello.isChecked(),
                "ingrandimento": self.ingrandimento.currentText(), "prores_hw": self.prores_hw.isChecked(),
                "contrasto": self.contrasto.value(), "stabilizza": self.stabilizza.value(),
                "bordo": self.bordo.valore(), "sfuma": self.sfuma.valore(), "durezza": self.durezza.valore(),
                "pulisci_colori": self.colore.isChecked(), "uscita": self.uscita.text()}

    def imposta_stato(self, d):
        if not d:
            return
        self._carico = True
        try:
            for combo, k in ((self.soggetto, "soggetto"), (self.uso, "uso"), (self.motore, "motore")):
                i = combo.findData(d.get(k))
                if i >= 0:
                    combo.setCurrentIndex(i)
            q = d.get("qualita")
            if q == "personalizzata" and self.qualita.findData(q) < 0:
                self.qualita.addItem("Personalizzata", "personalizzata")
            if self.qualita.findData(q) >= 0:
                self.qualita.setCurrentIndex(self.qualita.findData(q))
            for combo, k in ((self.risoluzione, "risoluzione"), (self.modello_sam, "segui_modello"),
                             (self.segui_ris, "segui_risoluzione"), (self.ingrandimento, "ingrandimento")):
                if d.get(k) is not None:
                    combo.setCurrentText(str(d[k]))
            for chk, k in ((self.mani, "mani"), (self.cancello, "cancello"), (self.prores_hw, "prores_hw"),
                           (self.colore, "pulisci_colori")):
                if k in d:
                    chk.setChecked(bool(d[k]))
            if "mani_risoluzione" in d:
                self.mani_ris.setValue(int(d["mani_risoluzione"]))
            for spin, k in ((self.contrasto, "contrasto"), (self.stabilizza, "stabilizza")):
                if k in d:
                    spin.setValue(float(d[k]))
            for cur, k in ((self.bordo, "bordo"), (self.sfuma, "sfuma"), (self.durezza, "durezza")):
                if k in d:
                    cur.imposta(d[k])
            self.uscita.setText(d.get("uscita", ""))
        finally:
            self._carico = False
        self.desc_soggetto.setText(DESCRIZIONE_SOGGETTO[self.soggetto.currentData()])
        self._cambio_motore()
