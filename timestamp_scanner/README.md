Aggiornamento timestamp scanner

Sostituisci nel repository i tre file mantenendo esattamente questi percorsi:

.github/workflows/timestamp-assets.yml

timestamp_scanner/scanner.py

timestamp_scanner/tests/test_scanner.py

Comportamento nuovo

Quando viene trovata una nuova immagine:

viene inviata immediatamente su Telegram;

viene salvata in found_assets.json;

il processo resta aperto;

il client Telegram non viene chiuso;

la scansione attende 20 secondi;

riprende nello stesso run dal flusso già in corso.

La pausa è configurata nel workflow con:

PAUSE_AFTER_ASSET_SECONDS: "20"

Inserimento intervallo

Nel pulsante Run workflow compaiono quattro campi:

data iniziale: GG/MM/AAAA

ora iniziale: HH:MM:SS

data finale: GG/MM/AAAA

ora finale: HH:MM:SS

I run successivi mantengono automaticamente lo stesso intervallo.

GitHub Actions non offre un vero calendario con selettore dell'ora nei campiworkflow_dispatch, quindi questa è la soluzione nativa più semplice.

Pulizia run

Alla fine di ogni esecuzione vengono eliminati automaticamente tutti i vecchirun completati dello stesso workflow. Il run corrente non viene cancellatoperché, durante lo step di pulizia, risulta ancora in esecuzione.
