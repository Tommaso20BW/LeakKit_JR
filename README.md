<div align="center">

# 🚨 LeakKit JR

**Bot Telegram modulare per monitorare font, immagini prodotto e aggiornamenti pubblici legati alla Juventus.**

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GitHub Actions](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/check.yml/badge.svg)](https://github.com/Tommaso20BW/LeakKit_JR/actions/workflows/check.yml)

</div>

> [!IMPORTANT]
> LeakKit JR controlla esclusivamente risorse accessibili pubblicamente. Non genera codici, non forza URL protetti e non aggira login o sistemi di controllo degli accessi.

## Funzionalità

`check.py` è il punto di ingresso unico e avvia tre monitor indipendenti:

| Monitor | File | Controllo |
| --- | --- | --- |
| Font | `font_monitor.py` | Cifre da `0` a `9` dei kit `HOME`, `AWAY`, `THIRD` e `FOURTH` della stagione corrente |
| Prodotti | `store_product_monitor.py` | Immagini principali e secondarie dei codici Juventus da `00` a `99`, varianti `A` e `B` |
| Notizie | `news_monitor.py` | Nuovi articoli e aggiornamenti nella pagina Juventus di Footy Headlines |

Se un monitor fallisce, gli altri continuano comunque. Il processo termina con codice `1` quando almeno un controllo produce un errore non recuperabile.

## Monitor dei font

Per ogni kit vengono controllate tutte le cifre da `0` a `9`:

```text
HOME-YY-YY
AWAY-YY-YY
THIRD-YY-YY
FOURTH-YY-YY
```

La notifica Telegram viene inviata soltanto quando l'intero set di dieci immagini è disponibile, evitando album incompleti.

## Monitor dei prodotti

Il monitor verifica i codici:

```text
A00 → A99
B00 → B99
```

Per ogni prodotto cerca:

- l'immagine principale, senza suffisso;
- la seconda immagine, con suffisso `_2`;
- per i prodotti da `A01` ad `A14`, la seconda immagine usa il suffisso `_d`.

Le varianti `A` e `B` vengono salvate separatamente. La notifica parte solo quando entrambe le immagini del prodotto sono disponibili.

## Monitor delle notizie

Il monitor legge la pagina pubblica Juventus di Footy Headlines e controlla i metadati `NewsArticle` dei singoli articoli.

Rileva:

- nuove pubblicazioni;
- modifiche a titolo, descrizione o data di aggiornamento;
- articoli ripubblicati tramite vecchi URL;
- aggiornamenti recenti entro una finestra di due giorni.

Ogni versione viene identificata tramite fingerprint SHA-256. Lo stato conserva al massimo 300 articoli.

## Struttura

```text
LeakKit_JR/
├── .github/workflows/check.yml
├── tests/test_check.py
├── .leakkit_state.json
├── check.py
├── common.py
├── font_monitor.py
├── news_monitor.py
├── requirements.txt
├── state_store.py
├── store_product_monitor.py
└── telegram_client.py
```

| File | Ruolo |
| --- | --- |
| `check.py` | Coordina i monitor e gestisce gli argomenti CLI |
| `common.py` | Configurazione condivisa, stagioni, header HTTP e log |
| `telegram_client.py` | Invio di messaggi, immagini e album tramite Telegram Bot API |
| `state_store.py` | Stato JSON unico, migrazioni e scritture atomiche |

## Installazione

Il workflow GitHub Actions utilizza **Python 3.14**.

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Dipendenze principali:

- `requests`
- `beautifulsoup4`
- `curl_cffi`
- `tzdata`

## Configurazione Telegram

Configura le seguenti variabili d'ambiente o i corrispondenti secret di GitHub Actions:

| Variabile | Uso |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token creato con BotFather |
| `TELEGRAM_CHAT_ID` | ID della chat, del gruppo o del canale di destinazione |

È supportata anche la variabile legacy `TELEGRAM_TOKEN`.

### Linux e macOS

```bash
export TELEGRAM_BOT_TOKEN="token_del_bot"
export TELEGRAM_CHAT_ID="id_destinazione"
python check.py
```

### PowerShell

```powershell
$env:TELEGRAM_BOT_TOKEN = "token_del_bot"
$env:TELEGRAM_CHAT_ID = "id_destinazione"
python check.py
```

## Comandi

Esegue tutti i monitor:

```bash
python check.py
```

Controlla le sorgenti senza inviare messaggi e senza modificare lo stato:

```bash
python check.py --dry-run
```

Esegue un solo monitor:

```bash
python check.py --only fonts
python check.py --only products
python check.py --only news
```

L'opzione `--only` può essere ripetuta:

```bash
python check.py --only fonts --only products
```

Importa i vecchi file nel JSON unico e termina:

```bash
python check.py --migrate-state
```

Esegue i test:

```bash
python -m unittest discover -s tests -v
```

## Stato persistente

Tutto lo stato è salvato in:

```text
.leakkit_state.json
```

La versione corrente è la `2`:

```json
{
  "version": 2,
  "fonts": {},
  "store_products": {},
  "news": {
    "initialized": false,
    "articles": {}
  }
}
```

Le scritture sono atomiche e lo stato viene salvato subito dopo ogni notifica, riducendo il rischio di file corrotti o notifiche duplicate.

La migrazione importa automaticamente:

```text
.found-font-*
.found-product-*
.seen_news.json
```

Dopo l'importazione, i vecchi file vengono eliminati. Un'eventuale sezione `adidas` proveniente da versioni precedenti viene rimossa.

## GitHub Actions

Il workflow `.github/workflows/check.yml`:

1. viene avviato manualmente tramite **Run workflow**;
2. configura Python 3.14;
3. installa le dipendenze;
4. esegue i test;
5. avvia tutti i monitor;
6. committa `.leakkit_state.json` soltanto quando cambia;
7. elimina i run completati dalla cronologia del workflow.

La configurazione `concurrency` impedisce la sovrapposizione di due esecuzioni.

> [!NOTE]
> Nel workflow corrente non è presente un trigger `schedule`. Per controlli automatici è necessario aggiungere una pianificazione cron oppure avviare il workflow tramite un sistema esterno.

## Limiti

- La struttura dello store Juventus e il markup di Footy Headlines possono cambiare.
- Un'immagine può essere caricata prima della pubblicazione ufficiale del prodotto.
- Il progetto non esegue riconoscimento visivo di loghi o maglie.
- Errori temporanei di rete possono rendere un controllo incompleto.
- Il progetto non accede ad aree riservate o protette.

---

Progetto amatoriale, non affiliato con Juventus Football Club, Telegram o Footy Headlines.
