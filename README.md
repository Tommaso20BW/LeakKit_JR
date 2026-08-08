<div align="center">

# 🚨 LeakKit JR

**Monitor Telegram per individuare nuovi asset pubblici e aggiornamenti legati alla Juventus.**

[![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Monitor](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/check.yml/badge.svg)](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/check.yml)
[![Timestamp scanner](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/timestamp-assets.yml/badge.svg)](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/timestamp-assets.yml)

</div>

> [!IMPORTANT]
> Il progetto controlla esclusivamente URL e pagine accessibili pubblicamente. Non genera credenziali, non forza aree protette e non aggira sistemi di controllo degli accessi.

## Panoramica

LeakKit JR contiene due flussi indipendenti:

| Flusso | Punto di ingresso | Scopo |
| --- | --- | --- |
| Monitor ordinari | `check.py` | Controlla font delle maglie, immagini prodotto e notizie Footy Headlines |
| Scanner timestamp | `timestamp_scanner/scanner.py` | Cerca asset pubblici dello store il cui nome corrisponde a un timestamp |

Entrambi inviano le nuove rilevazioni su Telegram e mantengono uno stato persistente per evitare notifiche duplicate.

## Monitor ordinari

`check.py` coordina tre moduli. Un errore in un modulo non impedisce l'esecuzione degli altri; il processo termina con codice `1` se almeno un monitor fallisce.

### Font delle maglie

`font_monitor.py` controlla le cifre da `0` a `9` per i kit della stagione corrente:

```text
HOME-YY-YY
AWAY-YY-YY
THIRD-YY-YY
FOURTH-YY-YY
```

La notifica parte soltanto quando tutte e dieci le cifre del kit sono disponibili, così Telegram riceve un album completo.

### Immagini prodotto

`store_product_monitor.py` verifica separatamente le varianti `A` e `B` dei codici da `00` a `99`:

```text
JUYYA00 → JUYYA99
JUYYB00 → JUYYB99
```

Per ogni variante cerca l'immagine principale e una seconda immagine. I prodotti `A01`–`A14` usano il suffisso `_d`; tutti gli altri usano `_2`. Se è disponibile una sola immagine, il bot invia comunque quella trovata e registra la variante come notificata.

### Notizie Footy Headlines

`news_monitor.py` legge la pagina Juventus di Footy Headlines e i metadati `NewsArticle` dei singoli articoli. Rileva:

- nuove pubblicazioni;
- modifiche a titolo, descrizione o data;
- vecchi URL ripubblicati con una data recente;
- aggiornamenti recenti entro una finestra di due giorni.

Ogni versione è identificata da un fingerprint SHA-256. Lo stato conserva al massimo 300 articoli e rimuove quelli tracciati che restituiscono definitivamente `404` o `410`.

## Scanner timestamp

Il sottoprogetto `timestamp_scanner/` prova, secondo per secondo, due modelli di URL configurati in `targets.json`:

- immagini di categoria;
- immagini `patch-overlay`.

Il workflow `timestamp-assets.yml` parte automaticamente allo scoccare di ogni ora e non richiede date. Ogni run:

1. riprende da `state.json`;
2. controlla al massimo un'ora completamente conclusa;
3. controlla gli URL con concorrenza e limite globale di richieste;
4. invia immediatamente ogni nuovo asset e lo registra in `found_assets.json`;
5. attende 20 secondi dopo una scoperta e prosegue nello stesso processo;
6. salva il cursore e avvia subito un altro run se esistono arretrati o un'interruzione.

Il contenuto già notificato non viene reinviato neppure quando si usa `reset_state`. La documentazione specifica è in [`timestamp_scanner/README.md`](timestamp_scanner/README.md).

## Stato persistente

| File | Contenuto |
| --- | --- |
| `.leakkit_state.json` | Stato unico dei monitor font, prodotti e notizie |
| `timestamp_scanner/state.json` | Cursore orario, contatori e ultimo esito dello scanner |
| `timestamp_scanner/found_assets.json` | Asset timestamp già inviati |
| `timestamp_scanner/targets.json` | Modelli di URL controllati dallo scanner |

Lo stato principale è alla versione `2` e viene scritto atomicamente. Al primo caricamento importa gli eventuali file legacy `.found-font-*`, `.found-product-*` e `.seen_news.json`.

## Struttura

```text
LeakKit_JR/
├── check.py
├── common.py
├── font_monitor.py
├── store_product_monitor.py
├── news_monitor.py
├── telegram_client.py
├── state_store.py
├── .leakkit_state.json
├── timestamp_scanner/
│   ├── scanner.py
│   ├── targets.json
│   ├── state.json
│   ├── found_assets.json
│   └── tests/
├── tests/
└── .github/workflows/
    ├── check.yml
    └── timestamp-assets.yml
```

## Requisiti

- Python 3.14, come nei workflow GitHub Actions;
- accesso alle sorgenti pubbliche controllate;
- un bot Telegram per gli invii reali.

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configurazione

Configura in **Settings → Secrets and variables → Actions**:

| Secret | Uso |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token del bot Telegram |
| `TELEGRAM_CHAT_ID` | Chat, gruppo o canale di destinazione |

I monitor ordinari accettano anche il nome legacy `TELEGRAM_TOKEN`. Lo scanner timestamp usa `TELEGRAM_BOT_TOKEN`.

## Avvio locale

Esegue tutti i monitor:

```bash
python check.py
```

Controlla le sorgenti senza inviare messaggi e senza salvare lo stato:

```bash
python check.py --dry-run
```

Esegue uno o più monitor specifici:

```bash
python check.py --only fonts
python check.py --only products --only news
```

Importa soltanto lo stato legacy:

```bash
python check.py --migrate-state
```

Lo scanner timestamp usa automaticamente l'ora italiana corrente e non richiede variabili con date.

## Test

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s timestamp_scanner/tests -v
```

## GitHub Actions

Entrambi i workflow usano Python 3.14. Lo scanner timestamp è anche avviabile manualmente, ma normalmente parte da solo ogni ora.

| Workflow | Comportamento |
| --- | --- |
| `check.yml` | Esegue i test, avvia i tre monitor e committa `.leakkit_state.json` quando cambia |
| `timestamp-assets.yml` | Parte ogni ora, esegue i test, salva cursore e asset e concatena i run finché torna in pari |

La configurazione `concurrency` impedisce sovrapposizioni. Al termine, ciascun workflow elimina dalla propria cronologia i run completati. `check.yml` resta manuale; `timestamp-assets.yml` usa il trigger orario `schedule`.

## Limiti noti

- Percorsi, nomi file e markup delle sorgenti esterne possono cambiare.
- La presenza di un asset sul CDN non equivale a un annuncio ufficiale.
- Gli errori temporanei di rete possono lasciare un controllo incompleto; lo scanner conserva il cursore per il tentativo successivo.
- Il progetto non interpreta visivamente le immagini e non conferma che rappresentino un prodotto specifico.

---

Progetto amatoriale, non affiliato con Juventus Football Club, Telegram, Footy Headlines o i gestori delle sorgenti citate.
