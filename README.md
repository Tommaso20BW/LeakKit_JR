# Juve Leak Bot 🦓

Controlla se sono state caricate sullo store Juventus:

1. le immagini dei **font** (cifre di personalizzazione) per **HOME**,
   **AWAY** e **THIRD 26-27**
2. le immagini **prodotto** (fronte/retro) per replica, authentic, maniche
   lunghe e GK 26-27

Appena trova qualcosa, ti avvisa su Telegram e ti invia i PNG in qualità
originale. Ogni kit font e ogni codice prodotto ha il suo flag
(`.found-font-HOME-26-27`, `.found-product-01`, ecc.): la notifica di uno
non ferma il monitoraggio degli altri.

## Setup

### 1. Bot Telegram

1. Su Telegram scrivi a **@BotFather** → `/newbot` → segui le istruzioni
2. Copia il **token** che ti dà (tipo `123456:ABC-DEF...`)
3. Scrivi un messaggio qualsiasi al tuo nuovo bot (serve per "aprirlo")
4. Per il tuo **chat ID**: scrivi a **@userinfobot**, ti risponde con il tuo ID

### 2. Repository GitHub

1. Crea un repo (anche privato) e carica questi file
2. Vai su **Settings → Secrets and variables → Actions → New repository secret**
   - `TELEGRAM_BOT_TOKEN` → il token di BotFather
   - `TELEGRAM_CHAT_ID` → il tuo ID numerico

### 3. Trigger esterno

Il workflow non ha schedule interno: lo avvii tu dal tuo cron esterno con
una chiamata API. Ti serve un **Personal Access Token** GitHub
(Settings → Developer settings → Fine-grained token, con permesso
"Contents: read/write" e "Actions: read/write" sul repo).

Chiamata da fare nel cron:

```bash
curl -X POST \
  -H "Authorization: Bearer IL_TUO_PAT" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/TUO_USER/TUO_REPO/dispatches \
  -d '{"event_type":"check-leaks"}'
```

Puoi anche lanciarlo a mano da
**Actions → Controlla leak Juventus 26-27 → Run workflow** per testarlo.

## Note

- Dopo la notifica di un kit font, il bot crea `.found-font-NOMEKIT`; dopo
  la notifica di un prodotto crea `.found-product-CODICE`. Il workflow li
  committa: quell'elemento non viene più ricontrollato, gli altri sì. Per
  riarmare un kit/prodotto, cancella il suo flag.
- Per aggiungere kit font futuri (es. `HOME-27-28`), basta aggiungerli alla
  lista `FONT_KITS` in `check_leaks.py`.
- Per aggiungere prodotti futuri, basta aggiungerli al dizionario
  `PRODUCTS` (codice → nome) in `check_leaks.py`. Se cambia la lettera
  della stagione (attualmente `A`), aggiorna `PRODUCT_LETTER`.
- Il check verifica che la risposta sia davvero un'immagine (Content-Type
  e dimensione), così eviti falsi positivi da pagine 404 mascherate.
