<div align="center">

# ⏱️ LeakKit Timestamp Scanner

**Scanner al secondo degli asset pubblici dello store Juventus.**

[![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GitHub Actions](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/timestamp-assets.yml/badge.svg)](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/timestamp-assets.yml)

</div>

> [!IMPORTANT]
> Lo scanner controlla soltanto URL pubblici già descritti in `targets.json`. Non enumera aree riservate e non aggira autenticazioni.

## Come funziona

Gli asset cercati usano un nome nel formato:

```text
YYYYMMDDHHMMSS
```

Per ogni secondo dell'intervallo vengono costruiti gli URL dei target configurati. Il workflow limita il run all'istante esatto in cui è stato avviato, così lo scanner non tenta timestamp futuri.

Quando trova un nuovo asset:

1. lo invia immediatamente su Telegram;
2. registra URL, target, timestamp, tipo di contenuto e modalità di invio in `found_assets.json`;
3. mantiene aperto il client Telegram;
4. sospende le richieste per 20 secondi;
5. riprende dallo stesso flusso senza terminare il processo.

Se Telegram non accetta il contenuto come foto, il client prova l'invio come documento.

## Target

`targets.json` contiene attualmente:

| Nome | Modello URL |
| --- | --- |
| `categories` | `/images/juventus/categories/{timestamp}.webp` |
| `patch-overlay` | `/images/juventus/customizations/patch-overlay/{timestamp}.webp` |

Ogni target aggiuntivo deve avere un nome univoco e un `url_template` contenente `{timestamp}`.

## Intervallo

Il pulsante **Run workflow** richiede quattro campi in ora italiana:

| Campo | Formato |
| --- | --- |
| Data iniziale | `GG/MM/AAAA` |
| Ora iniziale | `HH:MM:SS` |
| Data finale | `GG/MM/AAAA` |
| Ora finale | `HH:MM:SS` |

`reset_state` riparte dalla data iniziale conservando l'archivio degli asset già inviati. `dry_run` mostra soltanto ampiezza dell'intervallo, cutoff e numero di URL previsti.

## Stato

| File | Ruolo |
| --- | --- |
| `state.json` | Cursore, contatori, intervallo, run e motivo dell'ultima interruzione |
| `found_assets.json` | Registro degli URL già notificati |
| `targets.json` | Elenco dei modelli URL |

Il cursore avanza soltanto dopo aver completato tutti i target del secondo corrente. In caso di errore HTTP o Telegram resta sul primo secondo incompleto, che verrà ritentato.

## Parametri operativi

Il workflow imposta questi valori:

| Variabile | Valore | Effetto |
| --- | ---: | --- |
| `REQUESTS_PER_SECOND` | `20` | Limite globale delle richieste |
| `CONCURRENCY` | `30` | Numero massimo di worker |
| `RETRIES` | `3` | Tentativi HTTP per URL |
| `HTTP_TIMEOUT_SECONDS` | `25` | Timeout per richiesta |
| `CHUNK_TIMESTAMPS` | `60` | Secondi elaborati per blocco |
| `CHECKPOINT_EVERY` | `1` | Frequenza di salvataggio del cursore |
| `MAX_RUNTIME_SECONDS` | `3600` | Budget massimo dello scanner nel run |
| `PAUSE_AFTER_ASSET_SECONDS` | `20` | Pausa dopo ogni scoperta |

## Avvio locale

Lo scanner riusa le dipendenze e il client Telegram del progetto principale.

```bash
python -m pip install -r requirements.txt
```

Imposta `SCAN_START` e `SCAN_FINAL_END` nel formato `GG/MM/AAAA HH:MM:SS`, quindi esegui:

```bash
python timestamp_scanner/scanner.py --dry-run
python timestamp_scanner/scanner.py
```

Comandi aggiuntivi:

```bash
python timestamp_scanner/scanner.py --initialize-state
python timestamp_scanner/scanner.py --reset-state
```

Per un invio reale servono `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

## Test

```bash
python -m unittest discover -s timestamp_scanner/tests -v
```

## GitHub Actions

Il workflow `.github/workflows/timestamp-assets.yml`:

- usa Python 3.14;
- valida sintassi e test prima della scansione;
- salva `state.json` e `found_assets.json` con retry sul push;
- avvia immediatamente un nuovo run finché l'intervallo non è completato;
- ferma la catena in caso di errore fatale;
- elimina i run completati dalla propria cronologia.

GitHub Actions non offre un selettore grafico per data e ora nei campi `workflow_dispatch`; per questo l'intervallo viene inserito come testo.

## Limiti noti

- Una scansione lunga viene suddivisa in più run.
- Limiti, blocchi o cambi di formato del CDN possono interrompere temporaneamente la catena.
- La scoperta di un file pubblico non dimostra che il contenuto sia definitivo o destinato alla pubblicazione.

---

Sottoprogetto di [LeakKit JR](../README.md).
