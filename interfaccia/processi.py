# -*- coding: utf-8 -*-
"""Processi in background: il motore gira fuori dall'interfaccia, che resta sempre fluida."""

import json
import os
import sys
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

CARTELLA_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _processo(parent):
    p = QProcess(parent)
    p.setProgram(sys.executable)
    p.setWorkingDirectory(CARTELLA_APP)
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUNBUFFERED", "1")
    env.insert("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    p.setProcessEnvironment(env)
    return p


class Lavoro(QObject):
    """Un lavoro del motore (anteprima, traccia, render) in un processo separato."""

    log = Signal(str)
    dettaglio = Signal(str)                      # righe "grezze" (librerie, ffmpeg)
    progresso = Signal(str, int, int, float, float, str)
    file = Signal(str, str)
    finito = Signal(dict)
    errore = Signal(str, str)
    annullato = Signal()
    terminato = Signal()                         # sempre, alla fine

    def __init__(self, parent=None):
        super().__init__(parent)
        self.p = None
        self._buf = b""
        self._esito = False
        self._annullando = False
        self._json = None

    def attivo(self):
        return self.p is not None and self.p.state() != QProcess.NotRunning

    def avvia(self, azione, **dati):
        if self.attivo():
            return False
        fd, self._json = tempfile.mkstemp(prefix="scontorno_lavoro_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"azione": azione, **dati}, fh)
        self._buf, self._esito, self._annullando = b"", False, False
        self.p = _processo(self)
        self.p.setArguments(["-m", "motore.lavoratore", "lavoro", self._json])
        self.p.readyReadStandardOutput.connect(self._leggi)
        self.p.readyReadStandardError.connect(self._leggi_err)
        self.p.finished.connect(self._fine)
        self.p.start()
        return True

    def annulla(self):
        if self.attivo():
            self._annullando = True
            self.p.terminate()                   # SIGTERM: il motore si ferma e pulisce
            QTimer.singleShot(15000, self._forza)

    def _forza(self):
        if self.attivo():
            self.p.kill()

    def _leggi(self):
        self._buf += bytes(self.p.readAllStandardOutput())
        while b"\n" in self._buf:
            riga, self._buf = self._buf.split(b"\n", 1)
            try:
                m = json.loads(riga.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            t = m.get("t")
            if t == "log":
                self.log.emit(m["msg"])
            elif t == "prog":
                self.progresso.emit(m["fase"], m["i"], m["n"], m["el"], m["eta"], m.get("extra", ""))
            elif t == "file":
                self.file.emit(m["tipo"], m["percorso"])
            elif t == "fine":
                self._esito = True
                self.finito.emit(m.get("risultato") or {})
            elif t == "errore":
                self._esito = True
                self.errore.emit(m["msg"], m.get("dettagli", ""))
            elif t == "annullato":
                self._esito = True
                self.annullato.emit()

    def _leggi_err(self):
        testo = bytes(self.p.readAllStandardError()).decode("utf-8", "replace")
        for r in testo.replace("\r", "\n").split("\n"):
            if r.strip():
                self.dettaglio.emit(r)

    def _fine(self, codice, stato):
        self._leggi()
        if not self._esito and self._annullando:
            self.annullato.emit()
        elif not self._esito:
            self.errore.emit(f"Il processo si e' chiuso in modo inatteso (codice {codice}).",
                             "Guarda i Dettagli per il messaggio completo.")
        try:
            os.remove(self._json)
        except OSError:
            pass
        self.terminato.emit()


class ServizioSAM(QObject):
    """SAM 2 sempre carico: a ogni clic restituisce in un attimo la selezione di quel fotogramma."""

    risposta = Signal(dict)
    log = Signal(str)
    pronto = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.p = None
        self._buf = b""
        self._id = 0
        self.in_attesa = {}                      # id -> dati della richiesta

    def _avvia(self):
        if self.p is not None and self.p.state() != QProcess.NotRunning:
            return
        self._buf = b""
        self.p = _processo(self)
        self.p.setArguments(["-m", "motore.lavoratore", "servizio"])
        self.p.readyReadStandardOutput.connect(self._leggi)
        self.p.readyReadStandardError.connect(
            lambda: self.log.emit(bytes(self.p.readAllStandardError()).decode("utf-8", "replace").strip()))
        self.p.finished.connect(self._chiuso)
        self.p.start()

    def chiedi(self, img, base, aggiungi, togli, out, modello, imgsz, **extra):
        """Chiede la selezione (aggiungi/togli in %, base = maschera di partenza o None). Restituisce l'id."""
        self._avvia()
        self._id += 1
        cmd = {"cmd": "selezione", "id": self._id, "img": img, "base": base, "aggiungi": aggiungi,
               "togli": togli, "out": out, "modello": modello, "imgsz": imgsz}
        self.in_attesa[self._id] = dict(cmd, **extra)
        self.p.write((json.dumps(cmd) + "\n").encode("utf-8"))
        return self._id

    def occupato(self):
        return bool(self.in_attesa)

    def chiudi(self):
        if self.p is not None and self.p.state() != QProcess.NotRunning:
            self.p.write(b'{"cmd": "esci"}\n')
            if not self.p.waitForFinished(2000):
                self.p.kill()

    def _chiuso(self, *_):
        """Il servizio si e' chiuso (errore grave): le richieste in attesa falliscono, al prossimo clic riparte."""
        for k, req in list(self.in_attesa.items()):
            self.in_attesa.pop(k, None)
            self.risposta.emit({"t": "errore_sam", "id": k, "msg": "il servizio di selezione si e' chiuso",
                                "richiesta": req, "dettagli": ""})

    def _leggi(self):
        self._buf += bytes(self.p.readAllStandardOutput())
        while b"\n" in self._buf:
            riga, self._buf = self._buf.split(b"\n", 1)
            try:
                m = json.loads(riga.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if m.get("t") == "pronto":
                self.pronto.emit()
            elif m.get("t") == "log":
                self.log.emit(m["msg"])
            elif m.get("t") in ("selezione", "errore_sam"):
                m["richiesta"] = self.in_attesa.pop(m.get("id"), {})
                self.risposta.emit(m)
