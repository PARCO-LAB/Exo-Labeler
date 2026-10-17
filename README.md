# EXO video viewer

Visualizzatore locale, in sola lettura, per le triplette RGB in
`~/nas/MAEVE/EXO_RECORD/s*/aligned/camera_{body,env,aria}/`.
Mostra solo le sequenze presenti in tutte e tre le camere. `camera_env` è la
vista esterna. La camera body viene ruotata di 180° nel browser; i file non
vengono modificati.

Richiede Python 3.9+, `ffmpeg` con `libx264`, `ffprobe` e un browser che
riproduca H.264 MP4 e supporti `requestVideoFrameCallback`.
Non servono pacchetti Python aggiuntivi.

```bash
python app.py --port 8765
```

Aprire `http://127.0.0.1:8765/`. Se il programma gira su un server remoto,
con VS Code Remote-SSH su Windows aprire il pannello **Ports** (Terminale →
Ports), scegliere **Forward a Port**, inserire `8765` e aprire l'indirizzo
**Forwarded Address** mostrato da VS Code. Il server deve restare in esecuzione
sulla macchina remota: lasciare aperto il terminale in cui è stato avviato.

In alternativa, aprire un tunnel SSH **dal proprio computer**:

```bash
ssh -L 8765:127.0.0.1:8765 utente@server
```

Poi aprire lo stesso indirizzo nel browser locale. La porta rimane privata
all'host che esegue il server. `--root` permette di usare un'altra cartella
con la stessa struttura; `--port` cambia la porta. Riavviare il server se
vengono aggiunte nuove registrazioni.

La timeline usa i frame a 30 Hz. Alla selezione l'app controlla codec,
frequenza e numero di frame; se i video hanno lunghezze diverse, segnala la
differenza e limita la timeline al più corto. Righello e lettura dello slider
mostrano numeri di frame. Ogni finestra si ridimensiona trascinando l'angolo
in basso a destra; quando non entrano tutte nella pagina, si scorre
orizzontalmente. Durante la riproduzione il browser
prepara un frame per ciascuna camera prima di mostrare il video, poi mantiene
i canali vicini al tempo della camera body regolando leggermente la velocità,
senza ricercare un nuovo frame di continuo. Per esaminare un frame
preciso mettere in pausa e usare slider o frecce. Il server assegna ai proxy
timestamp `n/30` e li verifica uno per uno; il browser conferma il timestamp
del fotogramma presentato da tutte e tre le camere prima di mostrarlo. Durante
lo spostamento compare un'attesa, poi i tre frame cambiano insieme. La
numerazione mostrata parte da 1; il primo frame del file è «Frame 1». In
riproduzione l'indicazione `≈ Frame` è approssimata: i tre decoder del browser
possono presentare frame vicini in istanti diversi. La corrispondenza fra gli
istanti reali delle camere dipende dalla sincronizzazione dei file originali.

Quando si seleziona una sequenza, il server crea versioni H.264 con un
fotogramma chiave per ogni frame e le tiene in RAM. La camera Aria viene
ridimensionata a una larghezza massima di 1280 pixel per ridurre il carico di
rete e decodifica; gli MP4 originali sul NAS restano intatti. La prima
preparazione può richiedere alcuni secondi (circa 14 s nella prova sulla
sequenza `s5/drilling_vertical_0`); le richieste successive usano la cache.
Sono conservate fino a quattro sequenze preparate per evitare di ricodificarle
quando si torna indietro. Durante la riproduzione il browser controlla anche
i fotogrammi effettivamente presentati e trattiene le camere in anticipo se
una resta indietro.

```bash
python -m unittest -v test_app
```

Con Firefox e geckodriver installati, `python test_browser.py` verifica anche
lo spostamento rapido dello slider e l'aggiornamento simultaneo dei tre frame.
