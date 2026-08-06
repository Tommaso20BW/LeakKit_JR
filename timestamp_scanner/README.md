# Scanner timestamp asset Juventus

Questa cartella è indipendente dagli altri monitor di `LeakKit_JR` e usa un workflow separato:

```text
.github/workflows/timestamp-assets.yml
```

Controlla, secondo per secondo:

```text
https://store.juventus.com/images/juventus/categories/YYYYMMDDHHMMSS.webp
https://store.juventus.com/images/juventus/customizations/patch-overlay/YYYYMMDDHHMMSS.webp
```

## Logica dei workflow concatenati

1. Il primo run parte manualmente.
2. Appena parte, il workflow legge da GitHub il proprio `created_at`.
3. Quel momento viene congelato come limite superiore del run.
4. Lo scanner parte dal valore `next_timestamp` salvato in `state.json`.
5. Non controlla mai timestamp successivi all'avvio di quel run.
6. Alla fine salva e committa il nuovo `next_timestamp`.
7. Subito dopo richiede un nuovo run dello stesso workflow.
8. Il nuovo run congela un nuovo limite all'istante in cui viene creato.
9. La catena si ferma automaticamente dopo `20260808235959`.

Se un run raggiunge il proprio budget di esecuzione prima del cutoff, salva comunque il punto raggiunto e avvia il successivo. Questo evita il limite massimo dei runner GitHub senza saltare timestamp.

## JSON di avanzamento

`state.json` non contiene un elenco da oltre un milione di codici. Registra dopo ogni timestamp completato:

- `last_checked_timestamp`: ultimo codice completato;
- `next_timestamp`: primo codice da controllare al run successivo;
- `last_attempted_timestamp`: ultimo codice tentato;
- contatori e motivo di arresto dell'ultimo run.

Il valore predefinito `CHECKPOINT_EVERY: "1"` aggiorna localmente il JSON dopo ogni singolo timestamp. Alla fine del workflow il file viene committato nel repository.

## Telegram

Viene riutilizzato direttamente il file già presente nella root:

```text
telegram_client.py
```

Il workflow usa gli stessi secret già configurati:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Quando trova una foto:

1. scarica i byte reali dell'immagine;
2. la invia immediatamente su Telegram;
3. la registra in `found_assets.json` solo dopo l'invio riuscito;
4. non la invia nuovamente nei run successivi.

Se Telegram fallisce, quel timestamp non viene marcato come completato e viene riprovato.

## Installazione

Copia nella root di `LeakKit_JR` entrambe le cartelle contenute nel pacchetto, mantenendo i percorsi:

```text
.github/workflows/timestamp-assets.yml
timestamp_scanner/
```

Poi apri **Actions**, scegli **Scanner timestamp asset Juventus** e avvialo una sola volta con **Run workflow**. Da quel momento i run si concatenano automaticamente.

L'opzione `reset_state` riporta il cursore al 27 luglio, ma conserva `found_assets.json`, quindi le foto già inviate non vengono duplicate.
