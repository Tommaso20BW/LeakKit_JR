# 🚨 LeakKit JR

Bot Telegram modulare per monitorare font, immagini prodotto e notizie sulla Juventus, compresi i codici e gli asset Adidas già esposti pubblicamente.

`check.py` è l’unico punto di avvio e richiama, in ordine, quattro monitor indipendenti:

1. `font_monitor.py` — cifre dei font di personalizzazione 2026/27;
2. `store_product_monitor.py` — fronte e retro delle maglie nello store Juventus;
3. `news_monitor.py` — nuovi articoli e aggiornamenti di Footy Headlines;
4. `adidas_monitor.py` — codici prodotto, hash e immagini Adidas Juventus.

Un errore in un monitor non impedisce l’esecuzione degli altri. Al termine il job fallisce comunque se almeno un controllo ha avuto un errore non recuperabile, così il problema resta visibile.

## Monitor Adidas

Il controllo Adidas usa soltanto informazioni già pubbliche:

- prova le pagine Juventus ufficiali di Adidas;
- se la protezione anti-bot le blocca, usa gli indici pubblici delle immagini Bing e Yahoo con fallback automatico;
- accetta soltanto immagini ospitate sul CDN Adidas;
- estrae il codice prodotto dal link o dal nome del file;
- conserva l’asset ID, compreso l’hash presente nell’URL;
- assegna un punteggio usando titolo, URL prodotto, dominio ufficiale e CDN;
- scarta i risultati sotto la soglia Juventus.

Al primo avvio crea una baseline e invia un riepilogo unico dei codici già pubblici. In seguito notifica sia i nuovi codici sia i nuovi asset associati a codici già noti. Non prova a generare o indovinare gli hash.

## Stato unico

Tutto lo stato persistente è salvato in:

```text
.leakkit_state.json
```

Il file contiene quattro sezioni: `fonts`, `store_products`, `news` e `adidas`.

La migrazione importa automaticamente:

```text
.found-font-*
.found-product-*
.seen_news.json
```

Dopo averli inseriti nel JSON unico, elimina i vecchi file. Le scritture sono atomiche e lo stato viene salvato subito dopo ogni notifica, per ridurre il rischio di duplicati se il job si interrompe.

## GitHub Actions

Il workflow `.github/workflows/check.yml`:

- parte automaticamente ogni 10 minuti;
- può essere avviato anche con **Run workflow**;
- esegue i test prima dei monitor;
- esegue `python -u check.py`;
- committa solo `.leakkit_state.json` quando cambia;
- impedisce la sovrapposizione di due esecuzioni.

Gli avvii pianificati di GitHub Actions possono subire ritardi rispetto all’orario nominale.

## Configurazione Telegram

In **Settings → Secrets and variables → Actions** servono:

| Secret | Uso |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token creato con BotFather |
| `TELEGRAM_CHAT_ID` | Chat, gruppo o canale di destinazione |

## Opzioni Adidas

Il workflow imposta valori prudenti, modificabili tramite variabili d’ambiente:

| Variabile | Default | Descrizione |
|---|---:|---|
| `ADIDAS_NOTIFY_BASELINE` | `true` | Invia il riepilogo dei codici presenti al primo avvio |
| `ADIDAS_SEARCH_PAGES` | `1` | Pagine per ogni ricerca pubblica, massimo 2 |
| `ADIDAS_QUERY_DELAY` | `0.35` | Pausa in secondi tra le richieste |
| `ADIDAS_EXTRA_QUERIES` | vuota | Query aggiuntive separate dal carattere `\|` |

## Esecuzione locale

```bash
python -m pip install -r requirements.txt
python check.py
```

Comandi utili:

```bash
# Testa le sorgenti senza Telegram e senza modificare lo stato
python check.py --dry-run

# Testa soltanto Adidas
python check.py --only adidas --dry-run

# Importa soltanto i vecchi file nel JSON unico
python check.py --migrate-state
```

## Limiti

- Le pagine dello store, il markup di Footy Headlines e gli indici pubblici possono cambiare.
- Un asset Adidas può apparire nell’indice con ritardo rispetto al caricamento sul CDN.
- La classificazione Juventus usa il contesto testuale e i domini ufficiali; non esegue riconoscimento visivo del logo.
- Il progetto non aggira login, aree riservate o controlli di accesso.

---

Progetto amatoriale, non affiliato con Juventus FC, Adidas, Telegram, Bing o Footy Headlines.
