# TFA Manager – Gruppo C

Due interfacce sugli stessi contenuti:

- **App Streamlit** (`app.py`): piattaforma multi-utente per i corsisti del Gruppo C. Ognuno ha il proprio account (nome utente, matricola, recapiti, scuola di tirocinio) e vede solo i propri dati: asincrone, assenze, ore di tirocinio, documenti caricati e relazione finale per capitoli con le versioni. L'amministratore gestisce iscritti, calendario, insegnamenti e seminari, senza accesso ai documenti altrui. Dati e documenti su PostgreSQL OVH, schema `tfa`, separato dal gestionale.
- **Pagina HTML** (`docs/index.html`): solo calendario del gruppo, esami e viaggi, su GitHub Pages. Nessun dato personale.

## 1. Caricare su GitHub (dal browser)
1. github.com → **New repository** → nome `tfa-manager` → **Public** (GitHub Pages gratuito richiede un repository pubblico) → Create.
2. **Add file → Upload files** → trascina `app.py`, `requirements.txt`, `README.md`, `.gitignore` e le cartelle `data` e `docs` → **Commit changes**.

## 2. Pagina HTML (GitHub Pages)
1. Nel repository: **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, cartella `/docs` → Save.
2. Dopo un paio di minuti la pagina è su `https://<utente>.github.io/tfa-manager/`.
3. Password iniziale: `tfa2026`. Per cambiarla apri la pagina con `#hash` in fondo all’indirizzo, scrivi la nuova password, copia l’impronta e incollala in `PASSWORD_HASH` dentro `docs/index.html` (matita ✏️ di GitHub per modificare).
4. In `docs/index.html` sostituisci anche `STREAMLIT_URL` con l’indirizzo dell’app.

La password della pagina HTML è solo una protezione leggera: i file su GitHub Pages sono pubblici. Contengono solo calendario, insegnamenti e indirizzi istituzionali dei docenti.

## 3. App Streamlit
1. share.streamlit.io → accedi con GitHub → **Create app** → repository `tfa-manager`, branch `main`, file `app.py`.
2. **Advanced settings → Secrets** e incolla:

```toml
codice_invito  = "un-codice-da-dare-ai-colleghi"
admin_username = "gferraioli"

[ai]                                   # facoltativo: sezione «Genera testi»
api_key = "sk-ant-..."                 # chiave da console.anthropic.com; "demo" per provare senza costi
model = "claude-sonnet-5-5"
limite_giornaliero = 20                # generazioni al giorno per utente

[db]
url = "postgresql+psycopg2://UTENTE:PASSWORD@HOST:PORTA/NOMEDB?sslmode=require"
```

3. Deploy. Al primo avvio l’app crea lo schema `tfa` e carica calendario, insegnamenti e seminari.
4. Registrati per primo con il nome utente indicato in `admin_username`: diventi amministratore e ritrovi le tue asincrone, assenze e viaggi già inseriti.
5. Dai ai colleghi l’indirizzo dell’app e il codice di invito. Ogni nuovo iscritto trova l’elenco delle asincrone e i capitoli della relazione già pronti.

### Generazione testi
- Sezione «Genera testi»: elaborati asincroni da una traccia, capitoli della relazione finale dagli appunti, riscrittura delle note di osservazione in linguaggio descrittivo, email a docenti e segreteria, riassunti dei propri documenti caricati (PDF, Word, testo), testo libero.
- Il testo esce in streaming, si corregge nella pagina, si scarica in Word o si salva nei Documenti (le bozze dei capitoli finiscono nella Relazione finale, con le versioni).
- Ogni bozza è segnata come da rielaborare: gli elaborati del TFA devono essere originali.
- Costi: le chiamate all'API sono a carico della chiave inserita. Il limite giornaliero per utente e la scheda «Uso generazione testi» in Amministrazione (solo conteggi e token, non i testi) servono a tenerli sotto controllo.

### Privacy
- Al momento della registrazione ogni corsista accetta un’informativa sintetica (testo in `INFORMATIVA` dentro `app.py`, modificabile).
- Dal Profilo ciascuno può scaricare tutti i propri dati in uno zip o cancellare l’account (cancella anche i documenti).
- Documenti: massimo 20 MB per file e 500 MB per utente (costanti `MAX_FILE_MB` e `QUOTA_MB`). Nessun dato identificativo o sanitario degli alunni.

## 4. PostgreSQL su OVH
- Streamlit Cloud non ha un IP fisso: nel pannello OVH del database (Authorized IPs) va consentito l’accesso esterno. Usa SSL e una password robusta.
- Consigliato: un utente dedicato con permessi solo sullo schema `tfa`, così l’app non può toccare le tabelle del gestionale:

```sql
CREATE USER tfa_app WITH PASSWORD 'password-robusta';
CREATE SCHEMA IF NOT EXISTS tfa AUTHORIZATION tfa_app;
GRANT CONNECT ON DATABASE nomedb TO tfa_app;
```

## Aggiornare i dati
- **Dall’app Streamlit**: ogni sezione personale ha una tabella modificabile e il pulsante *Salva modifiche*; calendario, insegnamenti e seminari si modificano da *Amministrazione*.
- **Pagina HTML**: mostra i dati di `docs/data.js`. Per aggiornarla modifica quel file da GitHub, oppure chiedi a Claude di rigenerarlo con `data/build_seed.py`.
