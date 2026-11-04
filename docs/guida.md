# Guida completa

Per installazione e primi passi vedi il [README](../README.md).

## Selezione e correzioni

Sul primo frame clicca sul soggetto con **＋ Aggiungi**. Più clic sulle varie parti danno una selezione più
completa, per esempio busto, viso e l'oggetto che tiene in mano. **− Togli** esclude una parte, come un'altra
persona o una borsa.

Dopo il tracciamento, in vista *Selezione* scorri la timeline:

- **Manca qualcosa** (per esempio il braccio che rientra nell'inquadratura): vai su quel frame e clicca con
  **＋ Aggiungi**. Nasce una **correzione** (◆ arancione) che parte da ciò che era già seguito lì e aggiunge il clic.
- **C'è troppo:** usa **− Togli**.
- **Il soggetto esce dall'inquadratura:** usa **⦸ Soggetto assente da qui**. Dove rientra, basta un clic con ＋.

Si ritraccia solo da quel punto in avanti.

### Più correzioni di fila

Dopo una correzione, il tracciamento successivo resta visibile **in grigio** finché non lo aggiorni. Puoi correggere
anche sul grigio e poi premere **Segui il soggetto** una volta sola: rifà tutti i pezzi cambiati.

### Annullare un lavoro

**Annulla** non perde nulla: i pezzi già seguiti restano salvati e i file esportati prima restano intatti.

## Timeline

| Riga | Significato |
|---|---|
| **Chiavi** | ◆ verde selezione, ◆ arancio correzione, ◆ grigio assente |
| **Soggetto** | Verde trovato, rosso non trovato, grigio tracciamento da aggiornare, linea sottile da seguire |
| **Mani** | Tratti in cui le mani sono state individuate |
| **File** | Blu esportato e aggiornato, arancio da rifare |

Passa il mouse sulle righe per vedere la spiegazione.

## Punti da controllare (⚠)

Dopo **Segui il soggetto** il programma segna sulla timeline i punti sospetti e li spiega nel visore:

- la selezione si riduce o cresce di colpo (un braccio perso, un pezzo di sfondo preso);
- la forma cambia di colpo;
- il soggetto sparisce o ricompare a metà;
- per le persone, una mano, un gomito o la testa sono fuori dalla selezione (cerchio giallo con «?»).

Sono suggerimenti: guardali e correggi solo se serve. Per spostarti usa **S** e **⇧S**, oppure fai clic sul ⚠.

## Esportazione verso Premiere

Se hai già esportato e poi correggi qualcosa, l'esportazione rifà solo la parte cambiata e la ricuce nei file
esistenti senza ricomprimere il resto (la BASE non viene toccata). Per rifare tutto usa *⋯ › Esporta tutto da capo*.

Dopo l'esportazione, dal riquadro **Trascina in Premiere** puoi trascinare BASE e SOGGETTO, insieme o uno alla volta,
nel pannello Progetto o nella timeline. Le esportazioni successive sostituiscono i file con lo stesso nome, quindi
in Premiere restano collegati.

Con il MOGRT "Collage Strisce" (non incluso):

- `<nome>_BASE.mov` va in *MEDIA · Clip base*;
- `<nome>_SOGGETTO_alpha.mov` va in *SOVRAIMPR. 1 · Media*;
- *SOVRAIMPR. 1 · Inquadratura* va impostato su **Allineata alla clip base**.

## Scorciatoie da tastiera

| Tasto | Azione |
|---|---|
| A / T / R | Strumento ＋ Aggiungi / − Togli / ▭ Ignora zona (⌥ clic = strumento opposto al volo) |
| 1 / 2 / 3 | Vista Originale / Selezione / Risultato |
| ← → (⇧ = 1 s) | Frame precedente / successivo |
| ⌥← ⌥→ | Fotogramma chiave precedente / successivo |
| S / ⇧S | Punto da controllare successivo / precedente |
| Spazio | Riproduci |
| I / O / Z | Imposta IN / imposta OUT / timeline solo sul tratto |
| ⌫ | Elimina la chiave del frame |
| ⌘Z / ⇧⌘Z | Annulla / ripeti (chiavi, clic, zone, tratto) |
| ⌘T / ⇧⌘P | Segui il soggetto / prova questo frame |
| ⌘E / ⇧⌘E | Esporta / esporta tutto da capo |
| ⌘. / ⌘0 | Ferma il lavoro / adatta l'immagine |

## Impostazioni

Il pannello a destra propone tre domande semplici; sotto, chiusa, c'è la sezione **Per esperti**. Ogni impostazione
ha un nome tecnico, uguale nel file di progetto (`config`) e nel comando da terminale (`--nome-con-trattini`).

| Nel pannello | Nome tecnico | Valori | Effetto |
|---|---|---|---|
| Cosa scontorni? | `soggetto` | `persona` · `oggetto` | Con le persone le mani vengono rielaborate a parte |
| Per chi è il video? | — | personale · cliente | Sceglie il motore in base all'uso previsto |
| Motore (esperti) | `motore` | `matanyone` · `isnet` · `sagoma` | Vedi il [README](../README.md#motori) |
| Qualità | `risoluzione`, `mani` | Veloce 720 / Bilanciata 1080 + mani / Massima 1440 + mani | Solo MatAnyone |
| Bordo | `bordo` | −20…+20 px | Stringe o allarga il contorno |
| Morbidezza | `sfuma` | 0…10 px | Sfuma il contorno |
| Solidità | `durezza` | 0…1 | Rende il soggetto più pieno senza assottigliare i capelli |
| Pulisci i colori dei bordi | `no_decontamina` | sì/no (invertito) | Toglie lo sfondo che traspare dai bordi |
| Zone ignorate (▭) | `escludi` | `x,y,l,a` in % | Lì non viene mai scontornato nulla |
| Ritaglio delle mani | `mani_risoluzione` | 384…1024 | Lato del ritaglio per ogni mano (768 consigliato) |
| Modello di tracciamento | `segui_modello` | `sam2.1_t/s/b/l.pt` | t veloce, l più preciso |
| Precisione del tracciamento | `segui_risoluzione` | 512 · 768 · 1024 | 1024 il più sicuro |
| Limite del tracciamento | `no_cancello` | sì/no (invertito) | Toglie tutto ciò che il tracciamento non segue |
| Ingrandimento dell'alfa | `ingrandimento` | `colore` · `semplice` | Come si porta l'alfa a piena risoluzione |
| ProRes hardware | `prores_hw` | sì/no | Usa l'encoder del Mac, se funziona con l'alfa |
| Nettezza / Anti-sfarfallio | `contrasto`, `stabilizza` | | Solo ISNet |

## Terminale e file di progetto

Il progetto si salva in `progetto.json` nella cartella di lavoro del video (*Per esperti › File progetto* lo mostra).
Contiene tratto, fotogrammi chiave, zone ignorate e la `config` completa, e si può rieseguire senza interfaccia.

```bash
python3 scontorno.py --progetto percorso/progetto.json
```

Sintassi di `--segui`:

| Forma | Significato |
|---|---|
| `--segui x,y` | Punto sul soggetto, in % del frame, nel primo frame del tratto |
| `--segui 42,70+45,40` | Più punti nello stesso frame |
| `--segui T:x,y` | Aggiunge una chiave al secondo T |
| `--segui T:-` | Dal secondo T il soggetto è assente |
| `--segui 40,60+no70,50` | `no` davanti a un punto significa «togli» |

`--da-capo` riesporta tutto. `--prova` genera PNG di controllo e un video del tracciamento, senza i file finali.

## Tempi per fase

A fine tracciamento e a fine esportazione il log (*Vista › Mostra i dettagli del lavoro*) riporta i millisecondi per
frame di ogni fase (SAM 2, MatAnyone, rifinitura 4K, scrittura dei file, attese) e il **collo di bottiglia**: serve a
decidere cosa accelerare per primo.

## Integrazione con macOS

L'app usa lo stile di sistema, segue chiaro/scuro e colore di accento delle Impostazioni di Sistema (anche ad app
aperta) e offre barra strumenti con icone SF Symbols, menu con scorciatoie, avvisi a foglio, sezioni richiudibili e
*File › Apri recenti*. Quando un lavoro lungo finisce mentre usi un'altra app, invia una notifica e fa saltellare
l'icona nel Dock.

`pyobjc-framework-Cocoa` serve solo per le icone di sistema e per il nome «Scontorno» nel menu: senza, l'app funziona
con icone semplici.
