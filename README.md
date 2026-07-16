# Juve Font Bot 🦓

Controlla ogni 15 minuti se le immagini del font **AWAY 26-27** sono state
caricate sullo store Juventus. Appena le trova, ti avvisa su Telegram e ti
invia i PNG in qualità originale. Poi si ferma da solo (flag `.found`).

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
  -d '{"event_type":"check-font"}'
```

Puoi anche lanciarlo a mano da
**Actions → Controlla font Juventus AWAY 26-27 → Run workflow** per testarlo.

## Note
- Dopo la notifica il bot crea il file `.found` e lo committa: i run
  successivi escono subito senza rispammarti. Per "riarmarlo" (es. per la
  maglia third), cancella `.found` e cambia `AWAY-26-27` nell'URL.
- Il check verifica che la risposta sia davvero un'immagine (Content-Type
  e dimensione), così eviti falsi positivi da pagine 404 mascherate.
