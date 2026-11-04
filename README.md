# ClickMatte

App per macOS che scontorna un soggetto da un video (una persona, un oggetto, un animale) e genera file
ProRes con canale alfa pronti per il montaggio. Basta cliccare sul soggetto: il programma lo segue per tutto
il tratto scelto e, se correggi un punto, rifà solo la parte cambiata.

- **Selezione a clic:** aggiungi o togli parti del soggetto e vedi subito il risultato.
- **Tracciamento con SAM 2:** segue il soggetto e segnala i punti da controllare.
- **Tre motori di scontorno:** per qualità massima, per oggetti generici o per lavori commerciali.
- **Esportazione incrementale:** dopo una correzione ricalcola solo la parte modificata.
- **Interfaccia grafica e terminale:** usano lo stesso motore e gli stessi dati di lavoro.

## File generati

| File | Formato | Uso |
|---|---|---|
| `<nome>_BASE.mov` | ProRes 422 HQ | Clip di base |
| `<nome>_SOGGETTO_alpha.mov` | ProRes 4444 con alfa | Sovraimpressione |
| `<nome>_ANTEPRIMA.mp4` | H.264 1080p su scacchiera | Solo per controllare |

I file hanno stessa risoluzione, frame rate e numero di frame, quindi combaciano al pixel. Sono pensati per un
template MOGRT di Premiere ("Collage Strisce", non incluso), ma funzionano in qualsiasi programma di montaggio
che legga ProRes.

## Requisiti

- macOS su Apple Silicon: è l'unica piattaforma su cui l'app è stata provata
- Python 3 (sviluppata con Python 3.14)
- [ffmpeg](https://ffmpeg.org/)
- Memoria: circa 5 GB per il motore MatAnyone a 1080p, circa 12 GB a 1440p

## Installazione

```bash
brew install ffmpeg python
```

```bash
git clone https://github.com/Fedogia/clickmatte.git
```

```bash
cd clickmatte
```

```bash
python3 -m venv ~/clickmatte-env
```

```bash
source ~/clickmatte-env/bin/activate
```

```bash
pip install -r requirements.txt
```

Il motore MatAnyone 2 è facoltativo e si installa a parte. Serve solo per scontornare le persone con la massima
qualità, ed è **solo per uso non commerciale**.

```bash
git clone https://github.com/pq-yang/MatAnyone2 ~/MatAnyone2
```

```bash
pip install --no-deps -e ~/MatAnyone2
```

I modelli di intelligenza artificiale si scaricano da soli al primo utilizzo.

## Avvio

Fai doppio clic su **`Avvia ClickMatte.command`** (la prima volta: clic destro › Apri). In alternativa, da terminale:

```bash
python3 app.py
```

Il launcher usa l'ambiente in `~/clickmatte-env`, se esiste.

## Come si usa

Il riquadro **Come si fa** e la riga azzurra sotto il video indicano sempre il passo successivo.

1. **Scegli tratto e soggetto.** Trascina IN e OUT sulla timeline, poi clicca sul soggetto nel primo frame.
   Con **＋ Aggiungi** includi altre parti, con **− Togli** ne escludi. Il rosso mostra la selezione.
2. **Segui il soggetto.** Il programma lo traccia per tutto il tratto.
3. **Controlla e correggi.** Scorri la timeline: i punti sospetti sono segnati con ⚠. Dove manca qualcosa,
   clicca su quel frame: il tracciamento viene rifatto solo da lì in avanti.
4. **Esporta i file.** Se avevi già esportato, viene rifatta solo la parte cambiata, senza ricomprimere il resto.

Dal riquadro **Trascina in Premiere** puoi portare i file esportati direttamente nel progetto. Il lavoro si salva
da solo: riaprendo lo stesso video riparti da dove eri.

La [guida completa](docs/guida.md) descrive timeline, scorciatoie e impostazioni.

## Motori

| Motore | Ideale per | Licenza dei componenti principali |
|---|---|---|
| `matanyone` | Persone: trasparenze vere su capelli e mani in movimento | MatAnyone 2: S-Lab 1.0, **solo non commerciale**. Tracciamento: Ultralytics, AGPL-3.0 |
| `sagoma` | Oggetti, animali, qualsiasi soggetto: forma tracciata e rifinita sui bordi | Ultralytics, AGPL-3.0 |
| `isnet` | Persone, frame per frame, senza tracciamento | rembg, MIT (ma verifica anche la licenza del modello) |

> [!IMPORTANT]
> Per i lavori commerciali controlla sempre le licenze dei componenti che usi. Nell'interfaccia, la selezione a
> clic usa SAM 2 tramite Ultralytics (AGPL-3.0) con qualsiasi motore. Questa tabella è un riepilogo e non è una
> consulenza legale.

## Da terminale

```bash
# Prova: tracciamento completo, PNG di controllo e video del tracciamento
python3 clickmatte.py video.mov --segui 42,70+45,40 --prova

# Esportazione finale: riusa il tracciamento già calcolato
python3 clickmatte.py video.mov --segui 42,70+45,40

# Oggetto generico: motore sagoma
python3 clickmatte.py prodotto.mov --soggetto oggetto --segui 50,50

# Riesegue un progetto salvato dall'app
python3 clickmatte.py --progetto percorso/progetto.json
```

Le coordinate di `--segui` sono in percentuale del frame. Tutte le opzioni sono elencate da:

```bash
python3 clickmatte.py -h
```

## Privacy e dati locali

ClickMatte elabora i video sul tuo Mac e non li carica su nessun servizio. La rete serve per scaricare i modelli.
La libreria Ultralytics può inviare statistiche d'uso anonime: per disattivarle usa il comando qui sotto.

```bash
yolo settings sync=False
```

| Percorso | Contenuto |
|---|---|
| `~/Library/Caches/ClickMatte/<video>/` | Tracciamento, selezioni, `progetto.json` e copia leggera del video. Si può cancellare: i file esportati restano |
| `~/.cache/clickmatte/` | Modelli scaricati |

## Struttura del progetto

```text
.
├── app.py                     avvio dell'interfaccia grafica
├── clickmatte.py              comando da terminale
├── Avvia ClickMatte.command   launcher con doppio clic
├── requirements.txt           dipendenze Python
├── LICENSE                    testo della licenza AGPL-3.0
├── NOTICE.md                  avvisi e componenti di terzi
├── docs/
│   └── guida.md               guida completa
├── motore/                    elaborazione video, senza interfaccia
│   ├── pipeline.py            flusso completo e configurazione
│   ├── selezione.py           dai clic alla maschera
│   ├── traccia.py             SAM 2 e posa delle mani
│   ├── controllo.py           punti da controllare
│   ├── matting.py             motore MatAnyone 2
│   ├── isnet.py               motore ISNet
│   ├── uscita.py              rifinitura e scrittura dei file
│   ├── esporta.py             esportazione incrementale
│   ├── video.py               lettura e scrittura con ffmpeg
│   ├── filtri.py              filtri sulle maschere
│   ├── modelli.py             percorsi di modelli e cache
│   ├── eventi.py              avanzamento ed errori
│   └── lavoratore.py          processo separato usato dall'app
└── interfaccia/               app PySide6
    ├── finestra.py            finestra principale
    ├── visore.py              visore del video
    ├── timeline.py            timeline
    ├── pannello.py            pannello delle impostazioni
    ├── stato.py               stato del progetto
    ├── processi.py            comunicazione con il motore
    ├── tema.py                stile macOS, icone e notifiche
    └── trascina.py            trascinamento verso Premiere
```

## Licenza

Il codice di ClickMatte è distribuito con licenza [GNU AGPL-3.0](LICENSE), senza alcuna garanzia e nei limiti di
responsabilità previsti dalla licenza.

I componenti di terzi non sono inclusi in questo repository e restano soggetti alle proprie licenze: chi li installa
e li usa è tenuto a rispettarle. In particolare, **MatAnyone 2 è facoltativo e utilizzabile solo per scopi non
commerciali**. Dettagli e permesso aggiuntivo per MatAnyone 2 sono in [NOTICE.md](NOTICE.md).
