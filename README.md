# EXO video labeler

Visualizzatore e annotatore locale per le triplette RGB in
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
vengono aggiunte nuove registrazioni o cambia `label_schema.json`.

La timeline usa i frame a 30 Hz. Alla selezione l'app controlla codec,
frequenza e numero di frame; se i video hanno lunghezze diverse, segnala la
differenza e limita la timeline al più corto. Righello e lettura dello slider
mostrano numeri di frame. Il pannello **Soggetto e sequenza** si apre e chiude
dal titolo; i comandi delle camere restano visibili. La riga di navigazione è
compatta. Righello e tracce delle label condividono lo sfondo grigio chiaro,
con le linee dei frame estese lungo tutte le tracce. Ogni finestra si
ridimensiona trascinando l'angolo in basso a destra; **↺ Dimensioni** ripristina
le dimensioni iniziali di tutte le inquadrature. Quando non entrano tutte nella pagina, si scorre
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

Aprire **Label** e cliccare una voce per iniziare l'annotazione al frame
visualizzato. Passando il mouse o usando la tastiera su una voce compare la
definizione presente in `label_schema.json`; per le label mid e low, prive di
definizione nello schema 0.4, compare un avviso. Le annotazioni sono comuni alle tre camere. Per ogni label,
raggiungere il frame finale e premere **End**. Le azioni con fasi diverse (per esempio `move · wind_up` e
`hold · waiting`) sono scelte distinte. La timeline ha righello in frame e
testina verticale; sotto il righello, ogni livello e nome di label ha una
traccia. Il righello mostra un segno ogni 10 frame e il numero ogni 50;
le linee corrispondenti attraversano tutte le tracce. I segmenti della stessa label condividono la traccia, anche se hanno
fasi diverse; quando si sovrappongono usano corsie nella stessa traccia.

Per una sequenza nuova, la guida propone prima le label high. Il pulsante
**Conferma e passa** abilita via via mid, low e atomic, saltando i livelli non
previsti dal task. La conferma permette di proseguire anche senza annotazioni
al livello corrente; un intervallo aperto va prima chiuso con **End**. Le label
dei livelli già raggiunti restano disponibili per le correzioni. Dopo la
conferma dell'ultimo livello, sono disponibili tutte le label, comprese le aux.
La fase raggiunta viene salvata insieme alle annotazioni nel campo
`workflow_stage` e ripresa da chi apre la stessa sequenza. I file già annotati
prima dell'introduzione della guida si aprono direttamente in modalità libera.

Ogni segmento mostra label, attributi e durata in frame, con i dettagli
completi al passaggio del mouse. Trascinare i bordi colorati per cambiare
inizio/fine: la testina segue il bordo e i video mostrano il frame selezionato. Con un bordo
selezionato si possono usare anche le frecce per variare di un frame. Cliccare
il nome della label nella colonna **FRAME** o il suo segmento per impostare
frame esatti, attributi dello schema,
eventuali label genitore tramite caselle da spuntare, nota, o per eliminare l'annotazione. Se sulla stessa
riga ci sono più annotazioni, il clic sul nome apre una scelta per frame. La palette
offre solo le label previste dal task della sequenza. `static_0` e `static_1`
non hanno ancora un task in `label_schema.json`, quindi non offrono label.
Lo schema 0.4 mantiene il livello `atomic` per i task che lo prevedono.
`rep` si imposta sul livello mid; `direction` su mid, low o atomic solo nei task
che la prevedono. `side` si imposta su mid e può essere raffinato su low e
atomic, secondo i valori ammessi dal task. `external_load` e `contact_points`
si impostano solo sul livello più dettagliato previsto dal task: atomic, oppure
low o mid quando i livelli inferiori non sono presenti.
`target` usa i valori chiusi dello schema: sul livello high è fissato dal task
quando previsto, e può essere specificato anche sui livelli inferiori.
`location` si imposta solo sulle label low che la prevedono; il menu mostra
i valori consentiti oppure il formato da compilare (per esempio `r1`).
`variant`, `kg` e `load_class` sono fissati dal task e compaiono sulla traccia
high quando valorizzati. Se `side` su un parent non coincide con l'unione
dei valori dei figli annotati, compare un avviso; il salvataggio
rimane possibile anche quando sono annotati solo alcuni figli.

**Salva** scrive `s*/label/<nome_sequenza>.json` in modo atomico. Le label
salvate sono caricate quando si riapre la sequenza. Se il file è stato
modificato da un'altra sessione, il salvataggio viene respinto per evitare di
sovrascriverlo; ricaricare la sequenza dopo aver conservato le proprie
modifiche. Un avviso compare se si cambia sequenza o si chiude la pagina con
modifiche non salvate. Non è possibile salvare un intervallo ancora aperto.

Il JSON contiene `schema_version`, `subject`, `sequence`, `fps`, `workflow_stage` e
`annotations`. Ogni annotazione conserva i campi del template dello schema,
più `start_frame`, `end_frame`, `direction` e `location`. Gli indici dei frame iniziano da
**1** e gli estremi dell'intervallo sono inclusivi. `t_start` è l'istante
iniziale del primo frame; `t_end` è il confine subito dopo l'ultimo frame
incluso.
I JSON 0.1, 0.2 e 0.3 vuoti si aprono come 0.4. I JSON 0.2 e 0.3 con
annotazioni vengono convertiti in memoria se tutti i valori rispettano le
nuove regole; ai record 0.2 viene aggiunto `location: null`. Il file originale
viene aggiornato alla prima modifica salvata. I JSON 0.1 con annotazioni e i
JSON 0.2/0.3 incompatibili richiedono una migrazione manuale: i video restano
visibili, ma l'editing è bloccato e il file non viene modificato.

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
lo spostamento rapido dello slider, l'aggiornamento simultaneo dei tre frame,
la scelta delle label al frame corrente, l'editing della timeline, il salvataggio e la
riapertura delle annotazioni.
