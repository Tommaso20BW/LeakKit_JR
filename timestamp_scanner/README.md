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

Per ogni secondo vengono costruiti gli URL dei target configurati. Lo scanner lavora soltanto su ore completamente concluse: alle 01:00 controlla `00:00:00 → 00:59:59`, alle 02:00 controlla `01:00:00 → 01:59:59` e così via. Non tenta mai timestamp dell'ora ancora in corso.

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

## Automazione oraria

Non occorre inserire date o orari. GitHub Actions avvia automaticamente il workflow allo scoccare di ogni ora.

Ogni run elabora al massimo una finestra oraria. Quando termina:

1. se lo scanner è in pari, termina e attende il prossimo avvio pianificato;
2. se esistono ore arretrate, avvia subito un altro run;
3. se si ferma a metà, salva il primo secondo incompleto e il run successivo riparte da quel punto.

Il pulsante **Run workflow** rimane disponibile senza campi data. `reset_state` riparte dall'inizio dell'ora corrente conservando l'archivio degli asset già inviati. `dry_run` mostra soltanto la prossima finestra disponibile.

## Stato

| File | Ruolo |
| --- | --- |
| `state.json` | Cursore, contatori, stato di allineamento, run e motivo dell'ultima interruzione |
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

Non servono variabili con date o orari. Lo scanner usa automaticamente l'ora italiana corrente:

```bash
python timestamp_scanner/scanner.py --dry-run
python timestamp_scanner/scanner.py
```

Comandi aggiuntivi:

```bash
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
- parte automaticamente allo scoccare di ogni ora;
- valida sintassi e test prima della scansione;
- salva `state.json` e `found_assets.json` con retry sul push;
- avvia immediatamente un nuovo run quando esistono arretrati o una finestra è rimasta incompleta;
- quando è in pari termina senza tenere occupato un runner in attesa;
- ferma la catena in caso di errore fatale;
- elimina i run completati dalla propria cronologia.

## Limiti noti

- Ogni run elabora al massimo un'ora; gli arretrati vengono recuperati con run concatenati.
- Se l'intero runner viene terminato prima del salvataggio finale, alcuni secondi possono essere ricontrollati, ma non vengono saltati.
- Limiti, blocchi o cambi di formato del CDN possono interrompere temporaneamente la catena.
- La scoperta di un file pubblico non dimostra che il contenuto sia definitivo o destinato alla pubblicazione.

---

Sottoprogetto di [LeakKit JR](../README.md).
