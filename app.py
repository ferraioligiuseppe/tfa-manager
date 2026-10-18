"""TFA Manager – piattaforma multi-utente per i corsisti (Gruppo C).
Streamlit + PostgreSQL OVH (schema «tfa», separato dal gestionale).

Segreti (Streamlit Cloud → Settings → Secrets):
    codice_invito  = "..."          # richiesto per registrarsi
    admin_username = "gferraioli"   # questo utente diventa amministratore alla registrazione
    [ai]                            # facoltativo: generazione testi
    api_key = "sk-ant-..."          # chiave API Anthropic ("demo" = prova senza costi)
    model = "claude-sonnet-5-5"
    limite_giornaliero = 10         # generazioni al giorno per corsista (l'amministratore non ha limiti)
    limite_mensile = 60             # generazioni al mese per corsista (modificabile per singolo utente)
    [db]
    url = "postgresql+psycopg2://utente:password@host:5432/nomedb?sslmode=require"
"""
import io, json, zipfile, hmac, re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="TFA Manager", page_icon="🎓", layout="wide")
# Database dedicato: le tabelle stanno nello schema «public» (modificabile con [db] schema = "...")
S = st.secrets.get("db", {}).get("schema", "public")
TZ = ZoneInfo("Europe/Rome")
OGGI = datetime.now(TZ).date()
GIORNI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
GRUPPI = ["A", "B", "C", "D", "E", "F"]
STATI_ASI = ["Da fare", "Da personalizzare", "Pronto", "Inviato", "Approvato"]
STATI_CAP = ["Da iniziare", "In bozza", "Rivisto", "Definitivo"]
TIPI_TIR = ["diretto", "collegiali", "documentazione", "registro25", "indiretto"]
# ---- cartelle dei documenti: (nome breve usato nel calendario, nome ufficiale dell'insegnamento)
INSEGNAMENTI_UFF = [
    ("Didattica metacognitiva", "Didattica speciale: approccio metacognitivo e cooperativo – PED/03"),
    ("Did. disabilità sensoriali", "Didattica speciale e apprendimenti per le disabilità sensoriali – PED/03"),
    ("Pedagogia relazione d’aiuto", "Pedagogia della relazione d’aiuto – PED/01"),
    ("Ped. e Did. disabilità intellettiva", "Pedagogia e didattica speciale della disabilità intellettiva e dei disturbi generalizzati dello sviluppo – PED/03"),
    ("Ped. speciale gestione gruppo classe", "Pedagogia speciale della gestione integrata del gruppo classe – PED/03"),
    ("Progettazione PDF e PEI", "Progettazione del PDF e del PEI – progetto di vita e modelli di qualità della vita: dalla programmazione alla valutazione – PED/03"),
    ("Modelli integrati", "Modelli integrati di interventi psico-educativi per la disabilità intellettiva e dei disturbi generalizzati dello sviluppo – M-PSI/04"),
    ("Psicologia dello sviluppo", "Psicologia dello sviluppo, dell’educazione e dell’istruzione: modelli di apprendimento – M-PSI/04"),
    ("Legislazione scolastica", "Legislazione primaria e secondaria riferita all’integrazione scolastica – IUS/09"),
    ("Neuropsichiatria Infantile", "Neuropsichiatria Infantile – MED/39")]
UFFICIALE = dict(INSEGNAMENTI_UFF)
LABORATORI = [
    "LAB: Didattica delle educazioni e dell’area antropologica",
    "LAB: Didattica per le disabilità sensoriali",
    "LAB: Didattica speciale: codici comunicativi dell’educazione linguistica",
    "LAB: Didattica speciale: codici del linguaggio logico e matematico",
    "LAB: Interventi psico-educativi e didattici con disturbi comportamentali",
    "LAB: Interventi psico-educativi e didattici con disturbi relazionali",
    "LAB: Linguaggi e tecniche comunicative non verbali",
    "LAB: Metodi e didattiche delle attività motorie",
    "LAB: Orientamento e progetto di vita"]
ATTIVITA = ["TIC: tecnologie dell’informazione e della comunicazione (lezione online)",
            "Tirocinio indiretto con tutor in presenza all’università",
            "Tirocinio diretto in presenza con scuola convenzionata"]
GENERALI = ["Relazione finale", "Registri e firme", "Documenti personali", "Altro"]
AREE = {"Insegnamenti": [u for _, u in INSEGNAMENTI_UFF], "Laboratori": LABORATORI,
        "Altre attività": ATTIVITA, "Generali": GENERALI}
SOTTOCARTELLE = ["Moduli didattica asincrona", "Slide e materiali di studio", "Elaborati e consegne", "Altro"]
COMUNICAZIONI = "Calendario e comunicazioni del corso"
AREE_MATERIALI = {"Insegnamenti": AREE["Insegnamenti"], "Laboratori": LABORATORI, "Altre attività": ATTIVITA,
                  "Corso": [COMUNICAZIONI]}
SOTTO_MATERIALI = ["Moduli didattica asincrona", "Slide e materiali di studio", "Altro"]
MAX_MATERIALE_MB = 50
# riconoscimento automatico dell'insegnamento dal nome del docente o del file
DOCENTI_AREA = {
    "casillo": "Neuropsichiatria Infantile", "simonetti": "Ped. e Did. disabilità intellettiva",
    "federici": "Modelli integrati", "palmieri": "Did. disabilità sensoriali", "tafuri": "Did. disabilità sensoriali",
    "moretti": "Didattica metacognitiva", "metacognitiv": "Didattica metacognitiva",
    "terrinoni": "Psicologia dello sviluppo", "di gneo": "Psicologia dello sviluppo", "digneo": "Psicologia dello sviluppo",
    "mancini": "Pedagogia relazione d’aiuto", "legislazione": "Legislazione scolastica",
    "neuropsichiatria": "Neuropsichiatria Infantile", "sensorial": "Did. disabilità sensoriali",
    "psicologia dello sviluppo": "Psicologia dello sviluppo", "disabilità intellettiva": "Ped. e Did. disabilità intellettiva",
    "gruppo classe": "Ped. speciale gestione gruppo classe", "pei": "Progettazione PDF e PEI"}
ESCLUSI = ["/ai/", "gestionale", "tfa-manager", "passaporto", "iban", "piano_carriera", "chiave", "ferraioli",
           "qrcode", "viaggi_tfa", "tfa-app", "__macosx", "/.", "lntf"]
MAX_FILE_MB, QUOTA_MB = 20, 500
CAPITOLI = [
    "1. Macrocontesto – Denominazione e tipologia istituto",
    "1. Macrocontesto – Contesto territoriale",
    "1. Macrocontesto – Documenti strategici (PTOF, PI, RAV, PdM)",
    "1. Macrocontesto – Organi collegiali",
    "2. Microcontesto – Classe e alunno",
    "2. Microcontesto – Osservazione libera",
    "2. Microcontesto – Osservazione strutturata",
    "2. Microcontesto – Riflessioni conclusive",
    "3. Progettazione UdA",
    "4. Rendicontazione dell'esperienza",
    "5. Conclusioni",
    "6. Bibliografia e sitografia",
    "7. Allegati"]
PNEV = "https://www.pnev.it/"
FIRMA_DEFAULT = "Dott. Giuseppe Ferraioli"
TITOLI_DEFAULT = "Psicologo · Neuropsicologo · Optometrista comportamentale · Istruttore INPP, RMT e MNRI"
SPOT_DEFAULT = [
    {"ordine": 1, "titolo": "PNEV Games a scuola", "pulsante": "Scopri i giochi", "link": PNEV,
     "testo": "Giochi digitali per allenare attenzione, memoria, funzioni esecutive e abilità visive. Pensati per LIM e tablet: portali nella tua classe."},
    {"ordine": 2, "titolo": "Valutazioni per l’età evolutiva", "pulsante": "Prenota una valutazione", "link": PNEV,
     "testo": "Valutazioni neuropsicologiche, degli apprendimenti e visuo-uditive allo Studio The Organism di Pagani, Sant’Agnello e Avellino."},
    {"ordine": 3, "titolo": "I video PNEV", "pulsante": "Guarda i video", "link": PNEV,
     "testo": "Riflessi primitivi, stimolazione visiva e uditiva spiegati passo passo: materiali per docenti e famiglie."},
    {"ordine": 4, "titolo": "Screening gratuito per le scuole", "pulsante": "Richiedi lo screening", "link": PNEV,
     "testo": "Screening degli apprendimenti gratuito per le scuole dell’Agro nocerino-sarnese. Contatti: WhatsApp 391 3598767."},
    {"ordine": 5, "titolo": "Giochiamo imparando", "pulsante": "Vai alle schede", "link": PNEV,
     "testo": "Schede di pregrafismo e corsivo, interattive e stampabili, da usare in classe e a casa."}]

INFORMATIVA = """**Informativa sul trattamento dei dati (sintesi)**
Titolare: Giuseppe Ferraioli, amministratore della piattaforma. Dati trattati: dati di profilo
(nome, matricola, recapiti, scuola di tirocinio) e documenti caricati, al solo scopo di organizzare
il percorso TFA. I dati sono conservati su un server PostgreSQL in UE (OVH), non sono ceduti a terzi
e sono visibili solo a te; l'amministratore vede soltanto l'elenco degli iscritti (nome, gruppo, email),
non i tuoi documenti né i tuoi registri. Puoi scaricare tutti i tuoi dati o cancellare l'account in
qualsiasi momento dalla pagina «Profilo». Non caricare dati sanitari o identificativi di alunni:
usa sempre iniziali o codici."""


# ================================================================ database
@st.cache_resource
def engine():
    return create_engine(st.secrets["db"]["url"], pool_pre_ping=True)


DDL = ("" if S == "public" else f"CREATE SCHEMA IF NOT EXISTS {S};") + f"""
CREATE TABLE IF NOT EXISTS {S}.utenti (
  id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, pwd_hash TEXT NOT NULL,
  ruolo TEXT DEFAULT 'corsista', attivo BOOLEAN DEFAULT TRUE, nome TEXT, cognome TEXT, matricola TEXT,
  gruppo TEXT DEFAULT 'C', indirizzo TEXT, telefono TEXT, email_istituzionale TEXT, scuola_tirocinio TEXT,
  tutor_scolastico TEXT, consenso_privacy TIMESTAMPTZ, creato_il TIMESTAMPTZ DEFAULT now(), ultimo_accesso TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS {S}.insegnamenti (nome TEXT PRIMARY KEY, docente TEXT, email TEXT, ore NUMERIC);
CREATE TABLE IF NOT EXISTS {S}.lezioni (id SERIAL PRIMARY KEY, gruppo TEXT DEFAULT 'C', data DATE, fascia TEXT, insegnamento TEXT, esame BOOLEAN DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS {S}.seminari (id SERIAL PRIMARY KEY, data DATE, luogo TEXT);
CREATE TABLE IF NOT EXISTS {S}.monte_ore (tipo TEXT PRIMARY KEY, voce TEXT, ore NUMERIC);
CREATE TABLE IF NOT EXISTS {S}.asincrone (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE, insegnamento TEXT, titolo TEXT, stato TEXT, data_invio DATE, note TEXT);
CREATE TABLE IF NOT EXISTS {S}.assenze (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE, data DATE, insegnamento TEXT, ore NUMERIC, recuperata BOOLEAN DEFAULT FALSE, seminario DATE, note TEXT);
CREATE TABLE IF NOT EXISTS {S}.tirocinio (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE, data DATE, tipo TEXT, attivita TEXT, ore NUMERIC, note TEXT);
CREATE TABLE IF NOT EXISTS {S}.viaggi (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE, opzione TEXT, andata TEXT, ritorno TEXT);
CREATE TABLE IF NOT EXISTS {S}.tesi (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE, capitolo TEXT, stato TEXT, note TEXT, aggiornato_il TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS {S}.documenti (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE,
  categoria TEXT, capitolo TEXT, nome_file TEXT, mime TEXT, dimensione INT, versione INT DEFAULT 1, note TEXT,
  contenuto BYTEA, caricato_il TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS documenti_utente ON {S}.documenti(utente_id);
CREATE TABLE IF NOT EXISTS {S}.impostazioni (chiave TEXT PRIMARY KEY, valore TEXT);
CREATE TABLE IF NOT EXISTS {S}.spot (id SERIAL PRIMARY KEY, ordine INT DEFAULT 0, attivo BOOLEAN DEFAULT TRUE,
  titolo TEXT, testo TEXT, pulsante TEXT, link TEXT);
CREATE TABLE IF NOT EXISTS {S}.materiali (id SERIAL PRIMARY KEY, gruppo TEXT DEFAULT 'C', area TEXT, categoria TEXT,
  nome_file TEXT, mime TEXT, dimensione INT, sha1 TEXT, note TEXT, contenuto BYTEA,
  caricato_da INT REFERENCES {S}.utenti(id) ON DELETE SET NULL, caricato_il TIMESTAMPTZ DEFAULT now());
CREATE INDEX IF NOT EXISTS materiali_gruppo ON {S}.materiali(gruppo, area);
CREATE TABLE IF NOT EXISTS {S}.soggiorni (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE,
  checkin DATE, checkout DATE, vicino_a TEXT, persone INT, budget INT, tipologie TEXT, dettaglio TEXT, creato_il TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS {S}.generazioni (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE,
  tipo TEXT, richiesta TEXT, testo TEXT, caratteri INT, token_in INT, token_out INT, creato_il TIMESTAMPTZ DEFAULT now())
"""


def seed():
    return json.loads((Path(__file__).parent / "data" / "seed.json").read_text(encoding="utf-8"))


def aggiungi_colonna(c, tabella, definizione):
    """ALTER TABLE solo se la colonna manca: evita blocchi esclusivi inutili a ogni avvio."""
    nome = definizione.split()[0]
    esiste = c.execute(text("""SELECT 1 FROM information_schema.columns
                               WHERE table_schema=:s AND table_name=:t AND column_name=:c"""),
                       {"s": S, "t": tabella, "c": nome}).first()
    if not esiste:
        c.execute(text(f"ALTER TABLE {S}.{tabella} ADD COLUMN IF NOT EXISTS {definizione}"))


@st.cache_resource(show_spinner=False)
def init_db():
    """Eseguita una sola volta per avvio dell'app; il lock evita che due sessioni la eseguano insieme."""
    with engine().begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(724501)"))
        _init_db(c)
    return True


def _init_db(c):
    if True:
        for stmt in DDL.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        if not c.execute(text(f"SELECT COUNT(*) FROM {S}.spot")).scalar():
            pd.DataFrame(SPOT_DEFAULT).to_sql("spot", c, schema=S, if_exists="append", index=False)
            c.execute(text(f"INSERT INTO {S}.impostazioni VALUES ('spot_attivo','1') ON CONFLICT DO NOTHING"))
        aggiungi_colonna(c, "documenti", "area TEXT")
        for col in ("limite_mensile INT", "citta_partenza TEXT", "pwd_temporanea BOOLEAN DEFAULT FALSE", "referente BOOLEAN DEFAULT FALSE"):
            aggiungi_colonna(c, "utenti", col)
        for col in ("data DATE", "partenza TEXT", "destinazione TEXT", "mezzo TEXT", "dettaglio TEXT", "creato_il TIMESTAMPTZ DEFAULT now()"):
            aggiungi_colonna(c, "viaggi", col)
        if c.execute(text(f"SELECT COUNT(*) FROM {S}.insegnamenti")).scalar():
            c.execute(text(f"""INSERT INTO {S}.insegnamenti (nome, docente, email, ore)
                VALUES ('Legislazione scolastica', '', '', 25) ON CONFLICT (nome) DO NOTHING"""))
            return
        d = seed()
        pd.DataFrame(d["insegnamenti"] + [{"nome": "Legislazione scolastica", "docente": "", "email": "", "ore": 25}]).to_sql(
            "insegnamenti", c, schema=S, if_exists="append", index=False)
        pd.DataFrame(d["lezioni"]).assign(gruppo="C").to_sql("lezioni", c, schema=S, if_exists="append", index=False)
        pd.DataFrame(d["seminari"]).to_sql("seminari", c, schema=S, if_exists="append", index=False)
        pd.DataFrame(d["monte_ore"]).to_sql("monte_ore", c, schema=S, if_exists="append", index=False)


def q(sql, **p):
    with engine().connect() as c:
        return pd.read_sql(text(sql), c, params=p)


def run(sql, **p):
    with engine().begin() as c:
        return c.execute(text(sql), p)


def personale(tab):
    return q(f"SELECT * FROM {S}.{tab} WHERE utente_id=:u ORDER BY id", u=uid())


def salva_personale(tab, df):
    df = df.dropna(how="all").drop(columns=["id", "utente_id"], errors="ignore").assign(utente_id=uid())
    with engine().begin() as c:
        c.execute(text(f"DELETE FROM {S}.{tab} WHERE utente_id=:u"), {"u": uid()})
        if not df.empty:
            df.to_sql(tab, c, schema=S, if_exists="append", index=False)
    st.toast("Modifiche salvate.")


def salva_comune(tab, df, pk="id"):
    df = df.dropna(how="all")
    if pk == "id":
        df = df.drop(columns="id", errors="ignore")
    with engine().begin() as c:
        c.execute(text(f"DELETE FROM {S}.{tab}"))
        if not df.empty:
            df.to_sql(tab, c, schema=S, if_exists="append", index=False)
    st.toast("Modifiche salvate.")


def editor(df, key, on_save, hide=("id", "utente_id"), **cfg):
    cfg.update({h: None for h in hide if h in df.columns})
    out = st.data_editor(df, num_rows="dynamic", width="stretch", hide_index=True, key=key, column_config=cfg)
    if st.button("Salva modifiche", key=f"s_{key}", type="primary"):
        on_save(out)
        st.rerun()


# ================================================================ utenti
def uid():
    return st.session_state["user"]["id"]


def me():
    return st.session_state["user"]


def nuovo_utente(username, email, pwd, nome, cognome, matricola, gruppo):
    admin = username.lower() == str(st.secrets.get("admin_username", "")).lower()
    h = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode()
    with engine().begin() as c:
        u = c.execute(text(f"""INSERT INTO {S}.utenti (username,email,pwd_hash,ruolo,nome,cognome,matricola,gruppo,consenso_privacy)
            VALUES (:un,:em,:h,:r,:n,:c,:m,:g,now()) RETURNING id"""),
            dict(un=username, em=email.lower(), h=h, r="admin" if admin else "corsista", n=nome, c=cognome, m=matricola, g=gruppo)).scalar()
        d = seed()
        # elenco asincrone di partenza e capitoli della relazione per ogni nuovo iscritto
        stato_default = "Da fare"
        asi = pd.DataFrame(d["asincrone"]).assign(utente_id=u)
        if not admin:
            asi["stato"] = stato_default
        asi.to_sql("asincrone", c, schema=S, if_exists="append", index=False)
        pd.DataFrame({"capitolo": CAPITOLI, "stato": "Da iniziare", "utente_id": u}).to_sql("tesi", c, schema=S, if_exists="append", index=False)
        if admin:  # dati personali già raccolti per l'amministratore
            pd.DataFrame(d["assenze"]).assign(utente_id=u).to_sql("assenze", c, schema=S, if_exists="append", index=False)
            pd.DataFrame(d["viaggi"]).assign(utente_id=u).to_sql("viaggi", c, schema=S, if_exists="append", index=False)
            c.execute(text(f"UPDATE {S}.utenti SET scuola_tirocinio='Liceo «Mons. B. Mangino», Pagani' WHERE id=:u"), {"u": u})


def accesso():
    if "user" in st.session_state:
        return True
    st.title("TFA Manager")
    t_login, t_reg = st.tabs(["Accedi", "Registrati"])
    with t_login:
        with st.form("login"):
            ident = st.text_input("Nome utente o email")
            pwd = st.text_input("Password", type="password")
            if st.form_submit_button("Accedi", type="primary"):
                r = q(f"SELECT * FROM {S}.utenti WHERE lower(username)=lower(:i) OR lower(email)=lower(:i)", i=ident.strip())
                try:
                    ok_pwd = not r.empty and bcrypt.checkpw(pwd.encode(), r.iloc[0]["pwd_hash"].encode())
                except ValueError:
                    ok_pwd = False
                if not ok_pwd:
                    st.error("Nome utente o password errati.")
                elif not r.iloc[0]["attivo"]:
                    st.error("Account disattivato: contatta l'amministratore.")
                else:
                    user = r.iloc[0].drop("pwd_hash").to_dict()
                    # l'unico amministratore è l'utente indicato nei Secrets: il ruolo si ricalcola a ogni accesso
                    user["ruolo"] = "admin" if user["username"].lower() == str(st.secrets.get("admin_username", "")).lower() else "corsista"
                    run(f"UPDATE {S}.utenti SET ultimo_accesso=now(), ruolo=:r WHERE id=:u", u=int(user["id"]), r=user["ruolo"])
                    user["id"] = int(user["id"])
                    st.session_state["user"] = user
                    st.session_state["cambia_pwd"] = bool(user.get("pwd_temporanea"))
                    st.rerun()
    with t_reg:
        with st.form("reg"):
            c1, c2 = st.columns(2)
            nome, cognome = c1.text_input("Nome"), c2.text_input("Cognome")
            username, email = c1.text_input("Nome utente"), c2.text_input("Email")
            matricola = c1.text_input("Matricola")
            gruppo = c2.selectbox("Gruppo", GRUPPI, index=2)
            p1, p2 = c1.text_input("Password (almeno 8 caratteri)", type="password"), c2.text_input("Ripeti password", type="password")
            invito = st.text_input("Codice di invito", type="password", help="Te lo comunica l'amministratore.")
            st.markdown(INFORMATIVA)
            ok = st.checkbox("Ho letto l'informativa e acconsento al trattamento dei miei dati.")
            if st.form_submit_button("Crea account", type="primary"):
                err = []
                if not hmac.compare_digest(invito, str(st.secrets.get("codice_invito", ""))): err.append("Codice di invito non valido.")
                if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username or ""): err.append("Nome utente: 3-30 caratteri, solo lettere, numeri, . _ -")
                if "@" not in (email or ""): err.append("Email non valida.")
                if len(p1) < 8 or p1 != p2: err.append("Le password non coincidono o sono troppo corte.")
                if not (nome and cognome): err.append("Inserisci nome e cognome.")
                if not ok: err.append("Serve il consenso all'informativa.")
                if not err:
                    try:
                        nuovo_utente(username.strip(), email.strip(), p1, nome.strip(), cognome.strip(), matricola.strip(), gruppo)
                        st.success("Account creato. Ora accedi dalla scheda «Accedi».")
                    except Exception:
                        err.append("Nome utente o email già registrati.")
                for e in err:
                    st.error(e)
    st.divider()
    st.caption(credito())
    return False


# ================================================================ pagine
def lezioni_gruppo():
    lez = q(f"SELECT * FROM {S}.lezioni WHERE gruppo=:g ORDER BY data, fascia", g=me().get("gruppo") or "C")
    lez["data"] = pd.to_datetime(lez["data"]).dt.date
    return lez


def stato_assenze():
    ins, ass = q(f"SELECT * FROM {S}.insegnamenti"), personale("assenze")
    tot = ass.groupby("insegnamento")["ore"].sum() if not ass.empty else pd.Series(dtype=float)
    rows = []
    for _, r in ins.iterrows():
        h, lim = float(tot.get(r["nome"], 0)), float(r["ore"]) * 0.2
        if r["nome"] == "Tirocinio indiretto":
            s = "Assenza non ammessa: recupero con il tutor" if h else "Nessuna assenza"
        elif h == 0:
            s = "Nessuna assenza"
        elif h < lim:
            s = "Recupero obbligatorio (seminario + lavoro)"
        elif h == lim:
            s = "20% esaurito: nessun margine"
        else:
            s = "Oltre il 20%"
        rows.append({"Insegnamento": r["nome"], "Ore": float(r["ore"]), "Assenze (h)": h,
                     "Margine (h)": 0 if r["nome"] == "Tirocinio indiretto" else max(lim - h, 0), "Stato": s})
    return pd.DataFrame(rows)


def pag_cruscotto():
    st.title(f"Ciao {me().get('nome') or me()['username']}")
    lez = lezioni_gruppo()
    fut = lez[lez["data"] >= OGGI]
    c1, c2, c3, c4 = st.columns(4)
    if not fut.empty:
        p = fut["data"].iloc[0]
        c1.metric("Prossima lezione", f"{GIORNI[p.weekday()]} {p:%d/%m}", f"tra {(p - OGGI).days} gg")
    es = fut[fut["esame"]]
    if not es.empty:
        e = es.iloc[0]
        c2.metric("Prossimo esame", f"{e['data']:%d/%m}", e["insegnamento"], delta_color="off")
    asi = personale("asincrone")
    c3.metric("Asincrone da inviare", int((~asi["stato"].isin(["Inviato", "Approvato"])).sum()) if not asi.empty else 0)
    tesi = personale("tesi")
    c4.metric("Relazione finale", f"{int((tesi['stato'] == 'Definitivo').sum())}/{len(tesi)} capitoli")
    if not fut.empty:
        st.subheader(f"Lezioni del {p:%d/%m/%Y}")
        st.dataframe(fut[fut["data"] == p][["fascia", "insegnamento", "esame"]], hide_index=True, width="stretch")
    sa = stato_assenze()
    sa = sa[sa["Stato"] != "Nessuna assenza"]
    if not sa.empty:
        st.subheader("Avvisi assenze")
        st.dataframe(sa, hide_index=True, width="stretch")
    st.subheader("Esami in arrivo")
    st.dataframe(es[["data", "fascia", "insegnamento"]], hide_index=True, width="stretch")


def pag_calendario():
    st.title(f"Calendario Gruppo {me().get('gruppo') or 'C'}")
    st.caption("Blocchi da 2,5 ore. Il calendario è curato dall'amministratore.")
    lez = lezioni_gruppo()
    if st.toggle("Solo le prossime", value=True):
        lez = lez[lez["data"] >= OGGI]
    st.dataframe(lez[["data", "fascia", "insegnamento", "esame"]], hide_index=True, width="stretch",
                 column_config={"data": st.column_config.DateColumn("Data", format="DD/MM/YYYY")})


def pag_asincrone():
    st.title("Attività asincrone")
    st.caption("Almeno 6.000 caratteri · invio per email al docente · oggetto: «Nome Cognome – Insegnamento».")
    ins = q(f"SELECT * FROM {S}.insegnamenti")
    editor(personale("asincrone"), "asi", lambda d: salva_personale("asincrone", d),
           stato=st.column_config.SelectboxColumn("Stato", options=STATI_ASI),
           insegnamento=st.column_config.SelectboxColumn("Insegnamento", options=ins["nome"].tolist()),
           data_invio=st.column_config.DateColumn("Inviato il", format="DD/MM/YYYY"))
    st.subheader("Oggetti email")
    for _, r in ins.iterrows():
        st.code(f"{me().get('nome', '')} {me().get('cognome', '')} – {r['nome']}", language=None)
        if r["email"]:
            st.caption(f"A: {r['email']} · {r['docente']}")


def pag_assenze():
    st.title("Assenze e recuperi")
    st.caption("Tetto 20% per insegnamento; ogni assenza si recupera con un seminario «Link per l'Educazione» (uno per insegnamento) e un lavoro concordato. Sul tirocinio non sono ammesse assenze.")
    st.dataframe(stato_assenze(), hide_index=True, width="stretch")
    ins = q(f"SELECT nome FROM {S}.insegnamenti")["nome"].tolist()
    sem = q(f"SELECT data, luogo FROM {S}.seminari ORDER BY data")
    st.subheader("Le mie assenze")
    editor(personale("assenze"), "ass", lambda d: salva_personale("assenze", d),
           data=st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
           insegnamento=st.column_config.SelectboxColumn("Insegnamento", options=ins),
           seminario=st.column_config.DateColumn("Seminario scelto", format="DD/MM/YYYY"))
    st.subheader("Date dei seminari")
    st.dataframe(sem, hide_index=True, width="stretch")


def pag_tirocinio():
    st.title("Tirocinio")
    st.caption(f"Scuola: {me().get('scuola_tirocinio') or 'da indicare nel Profilo'}")
    tir, monte = personale("tirocinio"), q(f"SELECT * FROM {S}.monte_ore")
    fatte = tir.groupby("tipo")["ore"].sum() if not tir.empty else pd.Series(dtype=float)
    for col, (_, m) in zip(st.columns(len(monte)), monte.iterrows()):
        h = float(fatte.get(m["tipo"], 0))
        col.metric(m["voce"], f"{h:g}/{float(m['ore']):g} h")
        col.progress(min(h / float(m["ore"]), 1.0))
    editor(tir, "tir", lambda d: salva_personale("tirocinio", d),
           data=st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
           tipo=st.column_config.SelectboxColumn("Registro", options=TIPI_TIR),
           ore=st.column_config.NumberColumn("Ore", min_value=0.0, step=0.5))


def salva_file(f, area, sotto, capitolo=None, note=""):
    """Salva un file caricato nella cartella area/sottocartella, con numero di versione."""
    b = f.getvalue() if hasattr(f, "getvalue") else f["bytes"]
    nome = f.name if hasattr(f, "name") else f["name"]
    mime = (getattr(f, "type", None) if hasattr(f, "type") else f.get("mime")) or "application/octet-stream"
    if len(b) > MAX_FILE_MB * 1e6:
        st.error(f"{nome}: supera {MAX_FILE_MB} MB."); return False
    usato = q(f"SELECT COALESCE(SUM(dimensione),0) AS b FROM {S}.documenti WHERE utente_id=:u", u=uid())["b"].iloc[0]
    if (usato + len(b)) / 1e6 > QUOTA_MB:
        st.error("Spazio esaurito."); return False
    v = q(f"""SELECT COALESCE(MAX(versione),0)+1 AS v FROM {S}.documenti
              WHERE utente_id=:u AND nome_file=:n AND COALESCE(area,'')=:a AND categoria=:c""",
          u=uid(), n=nome, a=area, c=sotto)["v"].iloc[0]
    run(f"""INSERT INTO {S}.documenti (utente_id,area,categoria,capitolo,nome_file,mime,dimensione,versione,note,contenuto)
            VALUES (:u,:a,:c,:cap,:n,:m,:d,:v,:no,:b)""", u=uid(), a=area, c=sotto, cap=capitolo,
        n=nome, m=mime, d=len(b), v=int(v), no=note, b=b)
    return True


def anteprima(nome: str, mime: str, b: bytes, key: str):
    """Mostra il file dentro l'app: PDF, immagini, video, Word e testo."""
    n = nome.lower()
    if n.endswith(".pdf") or mime == "application/pdf":
        try:
            st.pdf(b, height=820, key=f"pdf_{key}")
            return
        except Exception:
            pass  # visualizzatore non disponibile: mostro le pagine come immagini
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(b)
            tot = len(doc)
            pag = st.number_input(f"Pagina (1–{tot})", 1, tot, 1, key=f"pg_{key}") if tot > 1 else 1
            st.image(doc[int(pag) - 1].render(scale=1.6).to_pil(), width="stretch")
        except Exception as e:
            st.warning(f"Anteprima non disponibile per questo PDF ({e}). Usa «Scarica».")
    elif n.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        st.image(b, width="stretch")
    elif n.endswith((".mp4", ".webm", ".mov")):
        st.video(b)
    elif n.endswith((".mp3", ".wav", ".m4a")):
        st.audio(b)
    elif n.endswith(".docx"):
        from docx import Document
        d = Document(io.BytesIO(b))
        with st.container(border=True, height=700):
            for p in d.paragraphs:
                if not p.text.strip():
                    continue
                stile = ((p.style.name if p.style is not None else "") or "").lower()
                if stile.startswith("heading 1") or stile == "title":
                    st.markdown(f"### {p.text}")
                elif stile.startswith("heading"):
                    st.markdown(f"**{p.text}**")
                else:
                    st.write(p.text)
            for t in d.tables:
                st.dataframe(pd.DataFrame([[c.text for c in r.cells] for r in t.rows]), hide_index=True)
    elif n.endswith((".txt", ".md", ".csv")):
        with st.container(border=True, height=600):
            st.text(b.decode("utf-8", errors="ignore"))
    else:
        st.info("Per questo tipo di file l’anteprima non è disponibile: usa «Scarica».")


def elenco_file(docs, key):
    """Tabella dei file con apertura, download ed eliminazione."""
    if docs.empty:
        st.info("Cartella vuota: carica il primo file qui sopra.")
        return
    docs = docs.assign(KB=(docs["dimensione"] / 1024).round(0),
                       caricato=pd.to_datetime(docs["caricato_il"]).dt.tz_convert(TZ).dt.strftime("%d/%m/%Y %H:%M"))
    st.dataframe(docs[["nome_file", "versione", "KB", "note", "caricato"]], hide_index=True, width="stretch",
                 column_config={"nome_file": "File", "versione": "Ver."})
    idx = docs.set_index("id")
    scelta = st.selectbox("Apri", docs["id"].tolist(), key=f"sel_{key}",
                          format_func=lambda i: f"{idx.loc[i, 'nome_file']} · v{idx.loc[i, 'versione']}")
    r = q(f"SELECT nome_file, mime, contenuto FROM {S}.documenti WHERE id=:i AND utente_id=:u", i=int(scelta), u=uid()).iloc[0]
    c1, c2 = st.columns(2)
    c1.download_button("Scarica", bytes(r["contenuto"]), file_name=r["nome_file"], mime=r["mime"], type="primary", key=f"dl_{key}")
    if st.toggle("Visualizza qui", key=f"vis_{key}", value=True):
        anteprima(r["nome_file"], r["mime"], bytes(r["contenuto"]), f"d{key}_{scelta}")
    if c2.button("Elimina", key=f"del_{key}"):
        run(f"DELETE FROM {S}.documenti WHERE id=:i AND utente_id=:u", i=int(scelta), u=uid())
        st.toast("Documento eliminato.")
        st.rerun()


def pag_documenti():
    st.title("I miei documenti")
    st.caption(f"Cartelle per insegnamento, laboratorio e attività. Visibili solo a te · max {MAX_FILE_MB} MB per file, "
               f"{QUOTA_MB} MB in totale · niente dati identificativi o sanitari di alunni.")
    meta = q(f"""SELECT id, COALESCE(area,'Altro') AS area, COALESCE(categoria,'Altro') AS categoria, capitolo, nome_file,
                 versione, dimensione, note, caricato_il FROM {S}.documenti WHERE utente_id=:u ORDER BY caricato_il DESC""", u=uid())
    usato = meta["dimensione"].sum() / 1e6 if not meta.empty else 0
    st.progress(min(usato / QUOTA_MB, 1.0), text=f"{usato:.1f} MB su {QUOTA_MB} MB")
    conteggi = meta.groupby("area").size().to_dict() if not meta.empty else {}

    with st.expander("Importa molti file insieme (cartella Dropbox o zip)", expanded=meta.empty):
        st.markdown("L’app legge la cartella e propone dove mettere ogni file: insegnamento dal nome del docente o del file, "
                    "**moduli ed esercitazioni** in «Moduli didattica asincrona», **lezioni e slide** in «Slide e materiali di studio», "
                    "**i tuoi elaborati** in «Elaborati e consegne», **piano carriera, ricevute e simili** in «Documenti personali». "
                    "Controlli la tabella e confermi. Chiavi, password e file di codice vengono esclusi.")
        fonte = st.radio("Da dove prendo i file?", ["Link di una cartella Dropbox (consigliato)", "Zip dal computer"],
                         horizontal=True, key="dimp_fonte")
        if fonte.startswith("Link"):
            st.caption("Su Dropbox: tasto destro sulla cartella → Condividi → Copia link (chiunque abbia il link). "
                       "Dopo l’importazione puoi eliminare il link.")
            link = st.text_input("Link della cartella Dropbox", key="dimp_link", placeholder="https://www.dropbox.com/scl/fo/…")
            if st.button("Leggi la cartella", key="dimp_go") and link.strip():
                try:
                    with st.spinner("Scarico la cartella da Dropbox…"):
                        dati = scarica_dropbox(link.strip())
                    df, cont = leggi_zip(dati, classifica_personale, ESCLUSI_PERSONALI)
                    st.session_state.update(dimp_df=df, dimp_cont=cont)
                except Exception as e:
                    st.error(f"Download non riuscito: {e}")
        else:
            z = st.file_uploader("File .zip", type=["zip"], key="dimp_zip")
            if z is not None and st.session_state.get("dimp_nome") != z.name:
                try:
                    df, cont = leggi_zip(z.getvalue(), classifica_personale, ESCLUSI_PERSONALI)
                    st.session_state.update(dimp_nome=z.name, dimp_df=df, dimp_cont=cont)
                except zipfile.BadZipFile:
                    st.error("Il file non è uno zip valido.")
        df = st.session_state.get("dimp_df")
        if df is not None and not df.empty:
            tutte = [a for v in AREE.values() for a in v]
            out = st.data_editor(df, hide_index=True, width="stretch", key="dimp_ed", disabled=["file", "percorso", "MB"],
                                 column_config={"sha1": None, "importa": st.column_config.CheckboxColumn("Importa"),
                                                "area": st.column_config.SelectboxColumn("Cartella", options=tutte, width="large"),
                                                "sottocartella": st.column_config.SelectboxColumn("Sottocartella", options=SOTTOCARTELLE + ["Documenti"])})
            sel = out[out["importa"]]
            st.caption(f"{len(sel)} file selezionati su {len(out)} · {sel['MB'].sum():.1f} MB · spazio libero {QUOTA_MB - usato:.0f} MB")
            if st.button(f"Importa {len(sel)} file nei miei documenti", type="primary", disabled=sel.empty, key="dimp_ok"):
                cont, n = st.session_state["dimp_cont"], 0
                bar = st.progress(0.0)
                for i, (_, r) in enumerate(sel.iterrows()):
                    b, mime = cont[r["sha1"]]
                    sotto = "Documenti" if r["area"] in GENERALI else (r["sottocartella"] if r["sottocartella"] != "Documenti" else "Altro")
                    gia = q(f"SELECT 1 FROM {S}.documenti WHERE utente_id=:u AND nome_file=:n AND dimensione=:d", u=uid(), n=r["file"], d=len(b))
                    if gia.empty and salva_file({"bytes": b, "name": r["file"], "mime": mime}, r["area"], sotto, None, f"Importato da {r['percorso']}"):
                        n += 1
                    bar.progress((i + 1) / len(sel), text=r["file"])
                st.toast(f"Importati {n} file nei tuoi documenti.")
                for k in ("dimp_df", "dimp_cont", "dimp_nome"):
                    st.session_state.pop(k, None)
                st.rerun()

    nav, main = st.columns([1, 2.6], gap="large")
    with nav:
        gruppo = st.radio("Sezione", list(AREE), key="doc_gruppo")
        area = st.radio("Cartella", AREE[gruppo], key=f"doc_area_{gruppo}",
                        format_func=lambda a: f"{a}  ({conteggi.get(a, 0)})")
    with main:
        st.subheader(area)
        sotto_list = ["Documenti"] if gruppo == "Generali" else SOTTOCARTELLE
        for tab, sotto in zip(st.tabs([f"{x} ({len(meta[(meta['area'] == area) & (meta['categoria'] == x)])})" for x in sotto_list]), sotto_list):
            with tab:
                k = f"{abs(hash(area)) % 10**8}_{sotto_list.index(sotto)}"
                with st.form(f"up_{k}", clear_on_submit=True):
                    files = st.file_uploader("Aggiungi file", accept_multiple_files=True, key=f"fu_{k}")
                    cap = st.selectbox("Capitolo", ["—"] + CAPITOLI, key=f"cap_{k}") if area == "Relazione finale" else "—"
                    note = st.text_input("Note", key=f"no_{k}")
                    if st.form_submit_button("Carica", type="primary") and files:
                        n = sum(salva_file(f, area, sotto, None if cap == "—" else cap, note) for f in files)
                        if n:
                            st.toast(f"{n} file caricati in {sotto}.")
                            st.rerun()
                elenco_file(meta[(meta["area"] == area) & (meta["categoria"] == sotto)], k)

    st.divider()
    cerca = st.text_input("Cerca in tutti i miei documenti (nome o note)")
    if cerca:
        trovati = meta[meta["nome_file"].str.contains(cerca, case=False) | meta["note"].fillna("").str.contains(cerca, case=False)]
        st.dataframe(trovati[["area", "categoria", "nome_file", "versione", "note"]], hide_index=True, width="stretch")
        if not trovati.empty:
            elenco_file(trovati, "cerca")


def puo_caricare_materiali():
    return me().get("ruolo") == "admin" or bool(q(f"SELECT referente FROM {S}.utenti WHERE id=:u", u=uid())["referente"].iloc[0])


def classifica(percorso: str):
    """Da un percorso nello zip ricava: importare?, area (nome ufficiale), sottocartella."""
    p = "/" + percorso.lower().replace("\\", "/")
    nome = p.rsplit("/", 1)[-1]
    est = nome.rsplit(".", 1)[-1] if "." in nome else ""
    importa = not any(x in p for x in ESCLUSI) and est in ("pdf", "docx", "doc", "pptx", "ppt", "xlsx", "odt", "jpg", "png", "mp4")
    area = None
    if "brillantino" in p or "tirocinio indiretto" in p:
        area = ATTIVITA[1]
    elif "tirocino diretto" in p or "tirocinio diretto" in p or "registri firme" in p:
        area = ATTIVITA[2]
    elif "calendario" in nome or "nota corsisti" in nome:
        area = COMUNICAZIONI
    else:
        for chiave, breve in DOCENTI_AREA.items():
            if chiave in p:
                area = UFFICIALE[breve]; break
    if area is None:
        area, importa = COMUNICAZIONI, False
    sotto = ("Moduli didattica asincrona" if any(k in nome for k in ("modulo", "esercitazione", "prova", "asincron"))
             else "Slide e materiali di studio")
    if "registri" in nome:
        sotto = "Altro"
    return importa, area, sotto


ESCLUSI_PERSONALI = ["chiave", "gestionale", "tfa-manager", "__macosx", "/.", "api_key", "password"]


def classifica_personale(percorso: str):
    """Come classifica(), ma per l'archivio personale: tiene anche elaborati e documenti privati."""
    p = "/" + percorso.lower().replace("\\", "/")
    nome = p.rsplit("/", 1)[-1]
    est = nome.rsplit(".", 1)[-1] if "." in nome else ""
    importa = not any(x in p for x in ESCLUSI_PERSONALI) and est in (
        "pdf", "docx", "doc", "pptx", "ppt", "xlsx", "odt", "jpg", "jpeg", "png", "mp4", "mp3", "txt", "md")
    if any(k in p for k in ("piano_carriera", "iban", "esa ferraioli", "qrcode", "passaporto", "ricevuta", "bonifico")):
        return importa, "Documenti personali", "Documenti"
    if "registri" in nome:
        return importa, "Registri e firme", "Documenti"
    if "relazione finale" in nome or "relazione_finale" in nome:
        return importa, "Relazione finale", "Documenti"
    if "ptof" in nome or "tirocinio_indiretto" in nome or "tirocinio indiretto" in p:
        return importa, ATTIVITA[1], "Elaborati e consegne" if "ferraioli" in nome or "/ai/" in p else "Slide e materiali di studio"
    _, area, sotto = classifica(percorso)
    if "/ai/" in p or "ferraioli_giuseppe" in nome or "elaborat" in nome:
        sotto = "Elaborati e consegne"
    if area == COMUNICAZIONI:
        area, sotto = "Altro", "Documenti"
    return importa, area, sotto


def leggi_zip(dati: bytes, classificatore=None, esclusi=None):
    """Estrae i file dallo zip (anche dagli zip annidati, un livello) senza duplicati."""
    import hashlib, mimetypes
    visti, righe, contenuti = set(), [], {}

    def scorri(zb, prefisso=""):
        with zipfile.ZipFile(io.BytesIO(zb)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                nome_pieno = prefisso + info.filename
                b = z.read(info)
                if nome_pieno.lower().endswith(".zip") and not prefisso:
                    if any(x in "/" + nome_pieno.lower() for x in (esclusi if esclusi is not None else ESCLUSI)):
                        continue  # archivi personali: non si aprono nemmeno
                    try:
                        scorri(b, nome_pieno[:-4] + "/")
                    except zipfile.BadZipFile:
                        pass
                    continue
                h = hashlib.sha1(b).hexdigest()
                if h in visti:
                    continue
                visti.add(h)
                imp, area, sotto = (classificatore or classifica)(nome_pieno)
                if len(b) > MAX_MATERIALE_MB * 1e6:
                    imp = False
                righe.append({"importa": imp, "file": nome_pieno.rsplit("/", 1)[-1], "percorso": nome_pieno,
                              "area": area, "sottocartella": sotto, "MB": round(len(b) / 1e6, 2), "sha1": h})
                contenuti[h] = (b, mimetypes.guess_type(nome_pieno)[0] or "application/octet-stream")
    scorri(dati)
    return pd.DataFrame(righe), contenuti


def scarica_dropbox(link: str, limite_mb: int = 400) -> bytes:
    """Scarica come zip una cartella condivisa di Dropbox (link pubblico), lato server."""
    import re as _re, urllib.request
    if "dropbox.com" not in link:
        raise ValueError("serve un link di Dropbox (dropbox.com/…)")
    url = _re.sub(r"([?&])dl=0", r"\1dl=1", link)
    if "dl=1" not in url:
        url += ("&" if "?" in url else "?") + "dl=1"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TFA-Manager"})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=600) as r:
        while True:
            pezzo = r.read(1 << 20)
            if not pezzo:
                break
            buf.write(pezzo)
            if buf.tell() > limite_mb * 1e6:
                raise ValueError(f"la cartella supera {limite_mb} MB: dividila in più link")
    dati = buf.getvalue()
    if not dati.startswith(b"PK"):
        raise ValueError("Dropbox non ha restituito uno zip: controlla che il link sia «chiunque abbia il link» e punti a una cartella")
    return dati


def salva_materiale(gruppo, area, sotto, nome, mime, b, note=""):
    import hashlib
    h = hashlib.sha1(b).hexdigest()
    if not q(f"SELECT id FROM {S}.materiali WHERE sha1=:h AND gruppo=:g", h=h, g=gruppo).empty:
        return False  # già presente
    run(f"""INSERT INTO {S}.materiali (gruppo, area, categoria, nome_file, mime, dimensione, sha1, note, contenuto, caricato_da)
            VALUES (:g,:a,:c,:n,:m,:d,:h,:no,:b,:u)""", g=gruppo, a=area, c=sotto, n=nome, m=mime, d=len(b), h=h, no=note, b=b, u=uid())
    return True


def pag_materiali():
    st.title("Materiali del corso")
    gruppo = me().get("gruppo") or "C"
    editore = puo_caricare_materiali()
    st.caption(f"Slide, moduli e comunicazioni del Gruppo {gruppo} e del corso, condivisi tra i corsisti registrati. "
               + ("Puoi caricare ed eliminare materiali." if editore else "Caricano l’amministratore e i referenti di gruppo."))
    gruppi_visibili = list(GRUPPI) + ["Tutti"] if me().get("ruolo") == "admin" else [gruppo, "Tutti"]
    meta = q(f"""SELECT id, gruppo, area, categoria, nome_file, dimensione, note, caricato_il FROM {S}.materiali
                 WHERE gruppo = ANY(:g) ORDER BY nome_file""", g=gruppi_visibili)
    conteggi = meta.groupby("area").size().to_dict() if not meta.empty else {}

    if editore:
        with st.expander("Importa una cartella intera (zip)"):
            st.markdown("Scarica da Dropbox la cartella dei materiali come **.zip** e caricala qui: l’app riconosce "
                        "l’insegnamento dal nome del docente o del file, mette moduli ed esercitazioni in «Moduli didattica "
                        "asincrona» e il resto in «Slide e materiali di studio», salta i doppioni. Prima di importare puoi "
                        "correggere tutto nella tabella. **Escludi sempre documenti personali ed elaborati.**")
            gdest = st.selectbox("Gruppo di destinazione", ([gruppo, "Tutti"] if me().get("ruolo") != "admin" else GRUPPI + ["Tutti"]),
                                 index=0 if me().get("ruolo") != "admin" else GRUPPI.index(gruppo), key="imp_gr")
            fonte = st.radio("Da dove prendo i file?", ["Link di una cartella Dropbox (consigliato)", "Carico uno zip dal computer"],
                             horizontal=True, key="imp_fonte")
            if fonte.startswith("Link"):
                st.caption("Su Dropbox: tasto destro sulla cartella → Condividi → Copia link (chiunque abbia il link può visualizzare). "
                           "L’app scarica la cartella direttamente da Dropbox, senza passare dal tuo computer. "
                           "Finita l’importazione puoi eliminare il link da Dropbox.")
                link = st.text_input("Link della cartella Dropbox", key="imp_link", placeholder="https://www.dropbox.com/scl/fo/…")
                if st.button("Scarica da Dropbox", key="imp_scarica") and link.strip():
                    try:
                        with st.spinner("Scarico la cartella da Dropbox…"):
                            dati = scarica_dropbox(link.strip())
                        df, cont = leggi_zip(dati)
                        st.session_state.update(imp_nome=link.strip(), imp_df=df, imp_cont=cont)
                    except Exception as e:
                        st.error(f"Download non riuscito: {e}")
            else:
                z = st.file_uploader("File .zip", type=["zip"], key="imp_zip")
                if z is not None and st.session_state.get("imp_nome") != z.name:
                    try:
                        df, cont = leggi_zip(z.getvalue())
                    except zipfile.BadZipFile:
                        st.error("Il file non è uno zip valido."); df, cont = pd.DataFrame(), {}
                    st.session_state.update(imp_nome=z.name, imp_df=df, imp_cont=cont)
            if st.session_state.get("imp_nome"):
                df = st.session_state.get("imp_df", pd.DataFrame())
                if not df.empty:
                    tutte = [a for v in AREE_MATERIALI.values() for a in v]
                    out = st.data_editor(df, hide_index=True, width="stretch", key="imp_ed",
                                         disabled=["file", "percorso", "MB"],
                                         column_config={"sha1": None,
                                                        "importa": st.column_config.CheckboxColumn("Importa"),
                                                        "area": st.column_config.SelectboxColumn("Cartella", options=tutte, width="large"),
                                                        "sottocartella": st.column_config.SelectboxColumn("Sottocartella", options=SOTTO_MATERIALI)})
                    sel = out[out["importa"]]
                    st.caption(f"{len(sel)} file selezionati su {len(out)} · {sel['MB'].sum():.1f} MB")
                    if st.button(f"Importa {len(sel)} file nel Gruppo {gdest}", type="primary", disabled=sel.empty):
                        cont, nuovi = st.session_state["imp_cont"], 0
                        bar = st.progress(0.0)
                        for i, (_, r) in enumerate(sel.iterrows()):
                            b, mime = cont[r["sha1"]]
                            nuovi += salva_materiale(gdest, r["area"], r["sottocartella"], r["file"], mime, b, f"Importato da {r['percorso']}")
                            bar.progress((i + 1) / len(sel))
                        st.success(f"Importati {nuovi} file; {len(sel) - nuovi} erano già presenti.")
                        for k in ("imp_nome", "imp_df", "imp_cont"):
                            st.session_state.pop(k, None)

    nav, main = st.columns([1, 2.6], gap="large")
    with nav:
        sez = st.radio("Sezione", list(AREE_MATERIALI), key="mat_sez")
        area = st.radio("Cartella", AREE_MATERIALI[sez], key=f"mat_area_{sez}",
                        format_func=lambda a: f"{a}  ({conteggi.get(a, 0)})")
    with main:
        st.subheader(area)
        for tab, sotto in zip(st.tabs([f"{x} ({len(meta[(meta['area'] == area) & (meta['categoria'] == x)])})" for x in SOTTO_MATERIALI]), SOTTO_MATERIALI):
            with tab:
                k = f"m{abs(hash(area)) % 10**8}_{SOTTO_MATERIALI.index(sotto)}"
                if editore:
                    with st.form(f"mup_{k}", clear_on_submit=True):
                        files = st.file_uploader("Aggiungi materiali", accept_multiple_files=True, key=f"mfu_{k}")
                        g = st.selectbox("Visibile a", [gruppo, "Tutti"], key=f"mg_{k}")
                        if st.form_submit_button("Carica", type="primary") and files:
                            n = sum(salva_materiale(g, area, sotto, f.name, f.type or "application/octet-stream", f.getvalue())
                                    for f in files if len(f.getvalue()) <= MAX_MATERIALE_MB * 1e6)
                            st.toast(f"{n} file caricati."); st.rerun()
                righe = meta[(meta["area"] == area) & (meta["categoria"] == sotto)]
                if righe.empty:
                    st.info("Nessun materiale in questa cartella.")
                    continue
                st.dataframe(righe.assign(MB=(righe["dimensione"] / 1e6).round(1))[["nome_file", "gruppo", "MB"]],
                             hide_index=True, width="stretch", column_config={"nome_file": "File"})
                idx = righe.set_index("id")
                scelta = st.selectbox("Apri", righe["id"].tolist(), key=f"msel_{k}", format_func=lambda i: idx.loc[i, "nome_file"])
                r = q(f"SELECT nome_file, mime, contenuto FROM {S}.materiali WHERE id=:i AND gruppo = ANY(:g)",
                      i=int(scelta), g=gruppi_visibili).iloc[0]
                c1, c2 = st.columns(2)
                c1.download_button("Scarica", bytes(r["contenuto"]), file_name=r["nome_file"], mime=r["mime"], type="primary", key=f"mdl_{k}")
                if editore and c2.button("Elimina dal corso", key=f"mdel_{k}"):
                    run(f"DELETE FROM {S}.materiali WHERE id=:i", i=int(scelta))
                    st.rerun()
                if st.toggle("Visualizza qui", key=f"mvis_{k}", value=True):
                    anteprima(r["nome_file"], r["mime"], bytes(r["contenuto"]), f"m{k}_{scelta}")


def pag_tesi():
    st.title("Relazione finale di tirocinio")
    st.caption("Struttura con i titoli prescritti. Carica le bozze dalla pagina «Documenti» scegliendo categoria «Relazione finale» e il capitolo.")
    tesi = personale("tesi")
    fatti = int((tesi["stato"] == "Definitivo").sum())
    st.progress(fatti / max(len(tesi), 1), text=f"{fatti} capitoli definitivi su {len(tesi)}")
    editor(tesi, "tesi", lambda d: salva_personale("tesi", d.assign(aggiornato_il=datetime.now(TZ))),
           hide=("id", "utente_id", "aggiornato_il"),
           capitolo=st.column_config.TextColumn("Capitolo", disabled=True),
           stato=st.column_config.SelectboxColumn("Stato", options=STATI_CAP))
    st.subheader("Versioni caricate per capitolo")
    docs = q(f"""SELECT id, capitolo, nome_file, versione, caricato_il FROM {S}.documenti
                 WHERE utente_id=:u AND capitolo IS NOT NULL ORDER BY capitolo, caricato_il DESC""", u=uid())
    if docs.empty:
        st.info("Nessuna bozza ancora collegata a un capitolo.")
    for cap, g in docs.groupby("capitolo", sort=False):
        with st.expander(f"{cap} – {len(g)} versioni"):
            for _, d in g.iterrows():
                c1, c2 = st.columns([3, 1])
                c1.write(f"v{d['versione']} · {d['nome_file']} · {pd.Timestamp(d['caricato_il']).tz_convert(TZ):%d/%m/%Y %H:%M}")
                if c2.button("Prepara download", key=f"t{d['id']}"):
                    r = q(f"SELECT nome_file, mime, contenuto FROM {S}.documenti WHERE id=:i AND utente_id=:u", i=int(d["id"]), u=uid()).iloc[0]
                    c2.download_button("Scarica", bytes(r["contenuto"]), file_name=r["nome_file"], mime=r["mime"], key=f"d{d['id']}")


SEDE_TFA = "Link Campus University, Via Pietro De Francisci 152, Roma"
SYSTEM_VIAGGI = """Sei un assistente per i viaggi di corsisti pendolari verso le lezioni del TFA o la scuola di tirocinio.
Scrivi in italiano, in modo pratico e sintetico. Usa la ricerca web per verificare treni (Trenitalia, Italo, regionali),
collegamenti urbani (metro, bus) e tempi in auto per la data indicata. Per ogni opzione indica: orario di partenza da casa,
tratte con orari e numero del treno quando disponibili, cambi, tempo totale, arrivo a destinazione e rientro.
Distingui ciò che hai verificato da ciò che è una stima. Segnala scioperi o lavori sulla linea se li trovi.
Non inventare numeri di treno o orari: se non li trovi, scrivi che vanno verificati sul sito del vettore.
Formatta con titoli «## Opzione 1 – …» e chiudi con «## Da verificare» (biglietti, prezzi, variazioni)."""


def maps_link(origine, destinazione, modo):
    from urllib.parse import quote_plus
    return (f"https://www.google.com/maps/dir/?api=1&origin={quote_plus(origine)}"
            f"&destination={quote_plus(destinazione)}&travelmode={modo}")


def pag_viaggi():
    st.title("I miei viaggi")
    st.caption("Imposta partenza e destinazione: apri subito il percorso su Google Maps oppure genera un itinerario "
               "completo con orari verificati sul web. Controlla sempre su trenitalia.com e italotreno.it prima di partire.")
    prof = q(f"SELECT citta_partenza, scuola_tirocinio FROM {S}.utenti WHERE id=:u", u=uid()).iloc[0]
    lez = lezioni_gruppo()
    prossime = sorted(set(lez[lez["data"] >= OGGI]["data"]))
    with st.form("itin"):
        c1, c2 = st.columns(2)
        partenza = c1.text_input("Partenza (indirizzo, città o stazione)", value=prof["citta_partenza"] or "")
        dest_opt = ["Sede TFA – " + SEDE_TFA]
        if prof["scuola_tirocinio"]:
            dest_opt.append("Scuola del tirocinio – " + prof["scuola_tirocinio"])
        dest_opt.append("Altra destinazione")
        dsel = c2.selectbox("Destinazione", dest_opt)
        altra = c2.text_input("Se «Altra destinazione», scrivi l'indirizzo")
        c3, c4, c5 = st.columns(3)
        data = c3.date_input("Giorno", value=prossime[0] if prossime else OGGI, format="DD/MM/YYYY")
        arrivo = c4.time_input("Arrivare entro", value=datetime.strptime("08:15", "%H:%M").time())
        rientro = c5.time_input("Ripartire dopo", value=datetime.strptime("19:30", "%H:%M").time())
        mezzo = st.radio("Mezzo", ["Treno e mezzi pubblici", "Auto", "Auto fino alla stazione + treno", "Confronta tutto"], horizontal=True)
        ricorda = st.checkbox("Ricorda la partenza nel mio profilo", value=not prof["citta_partenza"])
        c6, c7 = st.columns(2)
        apri = c6.form_submit_button("Mostra percorsi su Google Maps")
        genera = c7.form_submit_button("Genera itinerario dettagliato", type="primary")

    destinazione = altra.strip() if dsel == "Altra destinazione" else dsel.split(" – ", 1)[1]
    if (apri or genera) and (not partenza.strip() or not destinazione):
        st.error("Indica partenza e destinazione."); return
    if (apri or genera) and ricorda:
        run(f"UPDATE {S}.utenti SET citta_partenza=:p WHERE id=:u", p=partenza.strip(), u=uid())
    if apri or genera:
        st.markdown(f"[Percorso con mezzi pubblici]({maps_link(partenza, destinazione, 'transit')}) · "
                    f"[Percorso in auto]({maps_link(partenza, destinazione, 'driving')})")
    if genera:
        cfg = st.secrets.get("ai", {})
        if not cfg.get("api_key"):
            st.warning("Generazione non attiva: manca la chiave API nei Secrets.")
        elif not controlla_limiti("L'itinerario conta come una generazione."):
            giorno = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"][data.weekday()]
            prompt = (f"Prepara l'itinerario per {giorno} {data:%d/%m/%Y}.\nPartenza: {partenza}\nDestinazione: {destinazione}\n"
                      f"Devo arrivare entro le {arrivo:%H:%M} e ripartire dopo le {rientro:%H:%M} per tornare a casa in giornata.\n"
                      f"Mezzo: {mezzo}. Proponi al massimo 3 opzioni, dalla più consigliata.")
            with st.container(border=True):
                testo = st.write_stream(genera_stream(prompt, 3000, system=SYSTEM_VIAGGI, web=True))
            registra_generazione("Itinerario", prompt, testo)
            run(f"""INSERT INTO {S}.viaggi (utente_id, opzione, andata, ritorno, data, partenza, destinazione, mezzo, dettaglio)
                    VALUES (:u, :o, :a, :r, :d, :p, :de, :m, :t)""", u=uid(), o=f"{data:%d/%m} · {mezzo}",
                a=f"arrivo entro {arrivo:%H:%M}", r=f"ripartenza dopo {rientro:%H:%M}", d=data, p=partenza, de=destinazione, m=mezzo, t=testo)
            st.toast("Itinerario salvato in «I miei itinerari».")

    st.subheader("I miei itinerari")
    v = q(f"SELECT id, data, partenza, destinazione, mezzo, dettaglio, opzione, andata, ritorno FROM {S}.viaggi WHERE utente_id=:u ORDER BY data DESC NULLS LAST, id DESC", u=uid())
    if v.empty:
        st.info("Nessun itinerario salvato.")
    for _, r in v.iterrows():
        titolo = (f"{pd.Timestamp(r['data']):%d/%m/%Y} · {r['partenza']} → {r['destinazione']} · {r['mezzo']}"
                  if pd.notna(r["data"]) else r["opzione"])
        with st.expander(titolo):
            st.markdown(r["dettaglio"] or f"Andata: {r['andata']}  \nRitorno: {r['ritorno']}")
            if st.button("Elimina", key=f"vdel{r['id']}"):
                run(f"DELETE FROM {S}.viaggi WHERE id=:i AND utente_id=:u", i=int(r["id"]), u=uid())
                st.rerun()


SYSTEM_SOGGIORNO = """Sei un assistente che aiuta corsisti pendolari a trovare dove dormire vicino alla sede delle lezioni
o alla scuola di tirocinio. Scrivi in italiano, in modo pratico. Usa la ricerca web per trovare strutture reali e aperte
(hotel, B&B, case per ferie e strutture religiose, ostelli, appartamenti) e confrontale per: distanza a piedi o in metro
dalla destinazione, prezzo indicativo per notte nelle date indicate, colazione, cancellazione gratuita, orari di check-in
compatibili con un arrivo serale dopo le lezioni. Indica per ognuna dove prenotare (sito ufficiale o portale).
Non inventare nomi, indirizzi o prezzi: se un dato non è verificabile scrivi «da verificare».
Formatta con «## Le mie proposte» (massimo 5 strutture, dalla più conveniente rispetto al budget), poi
«## Zone consigliate» e «## Consigli pratici» (convenzioni universitarie, alternative per chi viaggia in gruppo)."""


def pag_soggiorno():
    from datetime import timedelta
    from urllib.parse import quote_plus
    st.title("Soggiorno")
    st.caption("Cerca dove dormire per il weekend di lezione o i giorni di tirocinio. I link ai portali sono gratuiti; "
               "la ricerca guidata usa il web e conta come una generazione. Prezzi e disponibilità vanno sempre confermati sul portale.")
    prof = q(f"SELECT scuola_tirocinio FROM {S}.utenti WHERE id=:u", u=uid()).iloc[0]
    lez = lezioni_gruppo()
    sabati = [d for d in sorted(set(lez[lez["data"] >= OGGI]["data"])) if d.weekday() == 5]
    ci_def = sabati[0] if sabati else OGGI
    with st.form("sogg"):
        c1, c2 = st.columns(2)
        opz = ["Sede TFA – " + SEDE_TFA] + (["Scuola del tirocinio – " + prof["scuola_tirocinio"]] if prof["scuola_tirocinio"] else []) + ["Altro luogo"]
        dsel = c1.selectbox("Vicino a", opz)
        altro = c1.text_input("Se «Altro luogo», scrivi l'indirizzo")
        checkin = c2.date_input("Arrivo (check-in)", value=ci_def, format="DD/MM/YYYY")
        checkout = c2.date_input("Partenza (check-out)", value=ci_def + timedelta(days=1), format="DD/MM/YYYY")
        c3, c4, c5 = st.columns(3)
        persone = c3.number_input("Persone", 1, 6, 1)
        budget = c4.number_input("Budget massimo a notte (€)", 20, 500, 90, step=10)
        distanza = c5.selectbox("Distanza massima", ["15 min a piedi", "30 min con i mezzi", "Indifferente"])
        tipi = st.multiselect("Tipologie", ["Hotel", "B&B", "Casa per ferie / struttura religiosa", "Ostello", "Appartamento"],
                              default=["Hotel", "B&B", "Casa per ferie / struttura religiosa"])
        note = st.text_input("Esigenze particolari (facoltativo)", placeholder="es. parcheggio, camera singola, arrivo dopo le 20")
        c6, c7 = st.columns(2)
        link = c6.form_submit_button("Apri le ricerche sui portali")
        cerca = c7.form_submit_button("Trova soluzioni con le date", type="primary")

    luogo = altro.strip() if dsel == "Altro luogo" else dsel.split(" – ", 1)[1]
    if (link or cerca) and (not luogo or checkout <= checkin):
        st.error("Indica il luogo e una data di partenza successiva a quella di arrivo."); return
    if link or cerca:
        zona = luogo.split(",")[-1].strip() if "," in luogo else luogo
        st.markdown(
            f"[Booking.com](https://www.booking.com/searchresults.it.html?ss={quote_plus(luogo)}&checkin={checkin:%Y-%m-%d}"
            f"&checkout={checkout:%Y-%m-%d}&group_adults={persone}&no_rooms=1) · "
            f"[Airbnb](https://www.airbnb.it/s/{quote_plus(zona)}/homes?checkin={checkin:%Y-%m-%d}&checkout={checkout:%Y-%m-%d}&adults={persone}) · "
            f"[Strutture vicine su Google Maps](https://www.google.com/maps/search/{quote_plus('alloggi vicino ' + luogo)})")
    if cerca:
        cfg = st.secrets.get("ai", {})
        if not cfg.get("api_key"):
            st.warning("Ricerca guidata non attiva: manca la chiave API nei Secrets.")
        elif not controlla_limiti("La ricerca conta come una generazione."):
            notti = (checkout - checkin).days
            prompt = (f"Cerco dove dormire vicino a: {luogo}.\nArrivo {checkin:%d/%m/%Y}, partenza {checkout:%d/%m/%Y} ({notti} notti), "
                      f"{persone} persone.\nBudget massimo {budget} € a notte. Distanza massima: {distanza}.\n"
                      f"Tipologie gradite: {', '.join(tipi) or 'qualsiasi'}.\nEsigenze: {note or 'nessuna'}.\n"
                      f"Il giorno di arrivo ho lezione fino alle 19:30, il giorno dopo ricomincio alle 08:30.")
            with st.container(border=True):
                testo = st.write_stream(genera_stream(prompt, 3500, system=SYSTEM_SOGGIORNO, web=True))
            registra_generazione("Soggiorno", prompt, testo)
            run(f"""INSERT INTO {S}.soggiorni (utente_id, checkin, checkout, vicino_a, persone, budget, tipologie, dettaglio)
                    VALUES (:u,:ci,:co,:v,:p,:b,:t,:d)""", u=uid(), ci=checkin, co=checkout, v=luogo, p=int(persone),
                b=int(budget), t=", ".join(tipi), d=testo)
            st.toast("Ricerca salvata in «Le mie ricerche».")

    st.subheader("Le mie ricerche")
    sg = q(f"SELECT * FROM {S}.soggiorni WHERE utente_id=:u ORDER BY checkin DESC, id DESC", u=uid())
    if sg.empty:
        st.info("Nessuna ricerca salvata.")
    for _, r in sg.iterrows():
        with st.expander(f"{pd.Timestamp(r['checkin']):%d/%m} → {pd.Timestamp(r['checkout']):%d/%m/%Y} · vicino a {r['vicino_a']} · max {r['budget']} €"):
            st.markdown(r["dettaglio"])
            if st.button("Elimina", key=f"sdel{r['id']}"):
                run(f"DELETE FROM {S}.soggiorni WHERE id=:i AND utente_id=:u", i=int(r["id"]), u=uid())
                st.rerun()


def pag_profilo():
    st.title("Profilo")
    u = q(f"SELECT * FROM {S}.utenti WHERE id=:u", u=uid()).iloc[0]
    with st.form("prof"):
        c1, c2 = st.columns(2)
        v = {k: (c1 if i % 2 == 0 else c2).text_input(lab, value=u[k] or "") for i, (k, lab) in enumerate([
            ("nome", "Nome"), ("cognome", "Cognome"), ("matricola", "Matricola"), ("email", "Email"),
            ("email_istituzionale", "Email istituzionale"), ("telefono", "Telefono"), ("indirizzo", "Indirizzo"),
            ("scuola_tirocinio", "Scuola del tirocinio diretto"), ("tutor_scolastico", "Tutor scolastico"),
            ("citta_partenza", "Luogo di partenza per i viaggi")])}
        v["gruppo"] = st.selectbox("Gruppo", GRUPPI, index=GRUPPI.index(u["gruppo"]) if u["gruppo"] in GRUPPI else 2)
        if st.form_submit_button("Salva profilo", type="primary"):
            run(f"UPDATE {S}.utenti SET " + ", ".join(f"{k}=:{k}" for k in v) + " WHERE id=:u", u=uid(), **v)
            st.session_state["user"].update(v)
            st.toast("Profilo aggiornato.")
            st.rerun()
    with st.expander("Cambia password"):
        with st.form("pw"):
            old = st.text_input("Password attuale", type="password")
            n1, n2 = st.text_input("Nuova password", type="password"), st.text_input("Ripeti", type="password")
            if st.form_submit_button("Aggiorna password"):
                if not bcrypt.checkpw(old.encode(), u["pwd_hash"].encode()):
                    st.error("Password attuale errata.")
                elif len(n1) < 8 or n1 != n2:
                    st.error("Le nuove password non coincidono o sono troppo corte.")
                else:
                    run(f"UPDATE {S}.utenti SET pwd_hash=:h WHERE id=:u", h=bcrypt.hashpw(n1.encode(), bcrypt.gensalt()).decode(), u=uid())
                    st.success("Password aggiornata.")
    with st.expander("I miei dati (privacy)"):
        st.markdown(INFORMATIVA)
        if st.button("Prepara l'esportazione di tutti i miei dati"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("profilo.json", u.drop("pwd_hash").to_json(force_ascii=False, date_format="iso"))
                for t in ["asincrone", "assenze", "tirocinio", "viaggi", "soggiorni", "tesi", "generazioni"]:
                    z.writestr(f"{t}.csv", personale(t).to_csv(index=False))
                for _, d in q(f"SELECT id, COALESCE(area,'Altro') AS area, categoria, nome_file, versione, contenuto FROM {S}.documenti WHERE utente_id=:u", u=uid()).iterrows():
                    cartella = re.sub(r'[\\/:*?"<>|]', "-", d["area"])[:80]
                    z.writestr(f"documenti/{cartella}/{d['categoria']}/v{d['versione']}_{d['id']}_{d['nome_file']}", bytes(d["contenuto"]))
            st.download_button("Scarica lo zip", buf.getvalue(), file_name="tfa_miei_dati.zip", mime="application/zip")
        st.divider()
        conf = st.text_input("Per cancellare l'account scrivi ELIMINA")
        if st.button("Cancella definitivamente il mio account", disabled=conf != "ELIMINA"):
            run(f"DELETE FROM {S}.utenti WHERE id=:u", u=uid())
            st.session_state.clear()
            st.rerun()


def pag_admin():
    st.title("Amministrazione")
    st.caption("L'amministratore gestisce iscritti e dati comuni. Non ha accesso ai documenti né ai registri dei corsisti.")
    t1, t2, t3, t4, t5, t6 = st.tabs(["Iscritti", "Calendario", "Insegnamenti", "Seminari", "Uso generazione testi", "Spot pnev.it"])
    with t6:
        attivo = st.toggle("Mostra lo spot nella barra laterale", value=impostazione("spot_attivo", "1") == "1")
        if attivo != (impostazione("spot_attivo", "1") == "1"):
            run(f"INSERT INTO {S}.impostazioni VALUES ('spot_attivo', :v) ON CONFLICT (chiave) DO UPDATE SET valore=:v", v="1" if attivo else "0")
            st.rerun()
        c1, c2 = st.columns([1, 2])
        firma = c1.text_input("Firma", value=impostazione("spot_firma", FIRMA_DEFAULT))
        titoli = c2.text_input("Titoli professionali", value=impostazione("spot_titoli", TITOLI_DEFAULT))
        if st.button("Salva firma"):
            for k, v in (("spot_firma", firma), ("spot_titoli", titoli)):
                run(f"INSERT INTO {S}.impostazioni VALUES (:k, :v) ON CONFLICT (chiave) DO UPDATE SET valore=:v", k=k, v=v.strip())
            st.toast("Firma aggiornata.")
        st.caption("I messaggi ruotano ogni 8 secondi nell’ordine indicato. Ai link viene aggiunta in automatico la sorgente "
                   "«tfa-manager» per riconoscere le visite nelle statistiche di pnev.it; nessun dato dei corsisti viene raccolto.")
        editor(q(f"SELECT * FROM {S}.spot ORDER BY ordine, id"), "adm_spot", lambda d: salva_comune("spot", d),
               ordine=st.column_config.NumberColumn("Ordine", step=1), attivo=st.column_config.CheckboxColumn("Attivo"),
               titolo="Titolo", testo=st.column_config.TextColumn("Testo", width="large"), pulsante="Pulsante",
               link=st.column_config.LinkColumn("Link"))
    with t1:
        ut = q(f"SELECT id, username, nome, cognome, gruppo, email, ruolo, attivo, referente, limite_mensile, creato_il, ultimo_accesso FROM {S}.utenti ORDER BY cognome")
        out = st.data_editor(ut, hide_index=True, width="stretch", key="adm_ut",
                             disabled=["id", "username", "nome", "cognome", "email", "ruolo", "creato_il", "ultimo_accesso"],
                             column_config={"gruppo": st.column_config.SelectboxColumn(options=GRUPPI),
                                            "referente": st.column_config.CheckboxColumn("Referente", help="Può caricare i materiali del corso per il proprio gruppo"),
                                            "limite_mensile": st.column_config.NumberColumn(
                                                "Limite mensile testi", min_value=0, step=5,
                                                help="Vuoto = valore predefinito dei Secrets. 0 = generazione disattivata. Non vale per l'amministratore.")})
        if st.button("Salva iscritti", type="primary"):
            for _, r in out.iterrows():
                if int(r["id"]) == uid() and not r["attivo"]:
                    st.error("Non puoi disattivare il tuo account."); break
                lim = None if pd.isna(r["limite_mensile"]) else int(r["limite_mensile"])
                run(f"UPDATE {S}.utenti SET attivo=:a, gruppo=:g, limite_mensile=:l, referente=:rf WHERE id=:i",
                    a=bool(r["attivo"]), g=r["gruppo"], l=lim, rf=bool(r["referente"]), i=int(r["id"]))
            else:
                st.toast("Iscritti aggiornati.")
        st.markdown("**Reimposta la password di un iscritto**")
        altri = ut[ut["id"] != uid()]
        if altri.empty:
            st.caption("Nessun altro iscritto.")
        else:
            c1, c2 = st.columns([2, 1])
            chi = c1.selectbox("Iscritto", altri["id"].tolist(), key="reset_chi",
                               format_func=lambda i: (lambda r: f"{r['cognome']} {r['nome']} ({r['username']})")(altri.set_index("id").loc[i]))
            if c2.button("Reimposta password"):
                nuova = password_provvisoria()
                run(f"UPDATE {S}.utenti SET pwd_hash=:h, pwd_temporanea=TRUE WHERE id=:i",
                    h=bcrypt.hashpw(nuova.encode(), bcrypt.gensalt()).decode(), i=int(chi))
                st.session_state["ultima_reset"] = (altri.set_index("id").loc[chi, "username"], nuova)
            if st.session_state.get("ultima_reset"):
                un, pw = st.session_state["ultima_reset"]
                st.success(f"Password provvisoria per **{un}**: comunicala di persona o per messaggio. "
                           "Al primo accesso dovrà sceglierne una nuova.")
                st.code(pw, language=None)
        st.caption("Unico amministratore: l'utente indicato in admin_username nei Secrets. Il ruolo non si modifica da qui.")
        st.caption(f"Codice di invito attuale: impostato nei Secrets di Streamlit ({'presente' if st.secrets.get('codice_invito') else 'MANCANTE'}).")
    with t2:
        ins = q(f"SELECT nome FROM {S}.insegnamenti")["nome"].tolist() + ["Da definire"]
        editor(q(f"SELECT * FROM {S}.lezioni ORDER BY gruppo, data, fascia"), "adm_lez", lambda d: salva_comune("lezioni", d),
               gruppo=st.column_config.SelectboxColumn(options=GRUPPI),
               data=st.column_config.DateColumn(format="DD/MM/YYYY"),
               insegnamento=st.column_config.SelectboxColumn(options=ins),
               fascia=st.column_config.SelectboxColumn(options=["08:30-11:00", "11:00-13:30", "14:30-17:00", "17:00-19:30"]))
    with t3:
        editor(q(f"SELECT * FROM {S}.insegnamenti ORDER BY nome"), "adm_ins", lambda d: salva_comune("insegnamenti", d, pk="nome"))
    with t4:
        editor(q(f"SELECT * FROM {S}.seminari ORDER BY data"), "adm_sem", lambda d: salva_comune("seminari", d),
               data=st.column_config.DateColumn(format="DD/MM/YYYY"))
    with t5:
        st.caption("Solo conteggi e token per il controllo dei costi: i testi restano privati.")
        default = int(st.secrets.get("ai", {}).get("limite_mensile", 60))
        uso = q(f"""SELECT u.username, u.ruolo, u.limite_mensile, COUNT(g.id) AS generazioni, COALESCE(SUM(g.token_in),0) AS token_in,
                COALESCE(SUM(g.token_out),0) AS token_out, MAX(g.creato_il) AS ultima
                FROM {S}.utenti u LEFT JOIN {S}.generazioni g ON g.utente_id=u.id
                AND g.creato_il >= date_trunc('month', now()) GROUP BY u.username, u.ruolo, u.limite_mensile ORDER BY generazioni DESC""")
        uso["limite"] = uso.apply(lambda r: "nessuno" if r["ruolo"] == "admin" else
                                  str(int(r["limite_mensile"]) if pd.notna(r["limite_mensile"]) else default), axis=1)
        st.dataframe(uso.drop(columns=["ruolo", "limite_mensile"]), hide_index=True, width="stretch")
        st.caption("I limiti per singolo iscritto si modificano nella scheda «Iscritti».")



# ================================================================ generazione testi
SYSTEM_AI = """Sei un assistente per corsisti del TFA Sostegno (Corso di specializzazione per le attività di sostegno
didattico, scuola secondaria di II grado, Link Campus University). Scrivi in italiano accademico chiaro, in prima persona
quando il testo è del corsista. Usa il quadro normativo aggiornato (L. 104/1992, L. 170/2010, D.Lgs. 66/2017 e 96/2019,
D.I. 182/2020 sui modelli di PEI, D.Lgs. 62/2024) e la prospettiva ICF (barriere e facilitatori, partecipazione).
Usa «persona/studente con disabilità», mai «handicappato» o «diversamente abile».
Regole:
- Produci una BOZZA da rielaborare: il corsista deve personalizzarla perché gli elaborati devono essere originali.
- Non inventare citazioni testuali, dati, numeri di pagina o riferimenti bibliografici: cita solo autori e opere molto noti
  e verificabili; se un riferimento è incerto, ometti.
- Non inserire dati identificativi o sanitari reali di alunni: usa iniziali o nomi di fantasia.
- Quando nel testo serve un'informazione che solo il corsista conosce, inserisci un segnaposto tra parentesi quadre,
  per esempio [nome della scuola] o [esempio personale].
- Formatta con titoli preceduti da «## » e paragrafi; niente elenchi puntati salvo richiesta."""

TIPI_TESTO = {
    "Elaborato asincrono": "Scrivi lo svolgimento di un elaborato asincrono per l'insegnamento «{insegnamento}». Traccia:\n{traccia}\n",
    "Capitolo della relazione finale": "Scrivi il capitolo «{capitolo}» della relazione finale di tirocinio. Scuola: {scuola}. Appunti del corsista:\n{traccia}\n",
    "Osservazione descrittiva": "Riscrivi queste note di osservazione in linguaggio descrittivo e neutro (solo comportamenti osservabili, tempi, luoghi, parole; niente giudizi, cause ipotizzate o stati interiori attribuiti). Poi, in un paragrafo separato intitolato «## Riflessione», proponi ipotesi interpretative dichiarandole come tali. Note:\n{traccia}\n",
    "Email a docente o segreteria": "Scrivi un'email formale ma cordiale, firmata {firma}, matricola {matricola}, Gruppo {gruppo}. Oggetto e testo. Contenuto da comunicare:\n{traccia}\n",
    "Riassunto di un documento": "Fai un riassunto ragionato del documento allegato, utile per lo studio e l'esame (domanda aperta da 20 righe): concetti chiave, autori, collegamenti con la normativa. Indicazioni aggiuntive:\n{traccia}\n",
    "Testo libero": "{traccia}\n",
}


def estrai_testo(doc_id: int, limite: int = 60000) -> str:
    r = q(f"SELECT nome_file, contenuto FROM {S}.documenti WHERE id=:i AND utente_id=:u", i=doc_id, u=uid()).iloc[0]
    b, nome = bytes(r["contenuto"]), r["nome_file"].lower()
    try:
        if nome.endswith(".pdf"):
            from pypdf import PdfReader
            t = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(b)).pages)
        elif nome.endswith(".docx"):
            from docx import Document
            t = "\n".join(p.text for p in Document(io.BytesIO(b)).paragraphs)
        else:
            t = b.decode("utf-8", errors="ignore")
    except Exception as e:
        t = f"[testo non estraibile: {e}]"
    return t[:limite]


def genera_stream(prompt: str, max_tokens: int, system: str = None, web: bool = False):
    cfg = st.secrets.get("ai", {})
    if cfg.get("api_key") == "demo":
        demo = ("## Bozza dimostrativa\n\nQuesta è una risposta di prova: la modalità «demo» non chiama il modello. "
                "Imposta una chiave API reale nei Secrets per generare testi veri.\n\n" + prompt[:400])
        for w in demo.split(" "):
            yield w + " "
        st.session_state["_uso"] = (0, 0)
        return
    import anthropic
    client = anthropic.Anthropic(api_key=cfg["api_key"])
    extra = {"tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]} if web else {}
    with client.messages.stream(model=cfg.get("model", "claude-sonnet-5-5"), max_tokens=max_tokens,
                                system=system or SYSTEM_AI, messages=[{"role": "user", "content": prompt}], **extra) as s:
        for t in s.text_stream:
            yield t
        u = s.get_final_message().usage
        st.session_state["_uso"] = (u.input_tokens, u.output_tokens)


def docx_da_testo(titolo: str, testo: str) -> bytes:
    from docx import Document
    from docx.shared import Pt
    d = Document()
    d.styles["Normal"].font.name, d.styles["Normal"].font.size = "Calibri", Pt(11)
    d.add_paragraph(f"{me().get('nome', '')} {me().get('cognome', '')} – Matricola {me().get('matricola') or ''} – Gruppo {me().get('gruppo')}")
    d.add_heading(titolo, level=1)
    for blocco in testo.split("\n"):
        b = blocco.strip()
        if b.startswith("## "):
            d.add_heading(b[3:], level=2)
        elif b.startswith("# "):
            d.add_heading(b[2:], level=1)
        elif b:
            d.add_paragraph(b.replace("**", ""))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def controlla_limiti(premessa: str = "") -> bool:
    """Mostra i contatori e restituisce True se il corsista ha esaurito i limiti (l'amministratore non ne ha)."""
    cfg = st.secrets.get("ai", {})
    uso = q(f"""SELECT COUNT(*) FILTER (WHERE creato_il >= date_trunc('day', now() AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'Europe/Rome') AS oggi,
                       COUNT(*) FILTER (WHERE creato_il >= date_trunc('month', now() AT TIME ZONE 'Europe/Rome') AT TIME ZONE 'Europe/Rome') AS mese
                FROM {S}.generazioni WHERE utente_id=:u""", u=uid()).iloc[0]
    if me().get("ruolo") == "admin":
        st.caption(f"{premessa} Amministratore: nessun limite (questo mese {int(uso['mese'])}).")
        return False
    lim_g = int(cfg.get("limite_giornaliero", 10))
    lim_pers = q(f"SELECT limite_mensile FROM {S}.utenti WHERE id=:u", u=uid())["limite_mensile"].iloc[0]
    lim_m = int(lim_pers) if pd.notna(lim_pers) else int(cfg.get("limite_mensile", 60))
    bloccato = bool(uso["oggi"] >= lim_g or uso["mese"] >= lim_m)
    st.caption(f"{premessa} Oggi {int(uso['oggi'])}/{lim_g} · questo mese {int(uso['mese'])}/{lim_m} generazioni.")
    if bloccato:
        st.warning("Hai raggiunto il limite di generazioni: riprova domani o il mese prossimo, oppure chiedi all'amministratore di aumentarlo.")
    return bloccato


def registra_generazione(tipo, richiesta, testo):
    tin, tout = st.session_state.get("_uso", (0, 0))
    run(f"""INSERT INTO {S}.generazioni (utente_id,tipo,richiesta,testo,caratteri,token_in,token_out)
            VALUES (:u,:t,:r,:x,:c,:i,:o)""", u=uid(), t=tipo, r=richiesta[:4000], x=testo, c=len(testo), i=tin, o=tout)


def pag_genera():
    st.title("Genera testi")
    cfg = st.secrets.get("ai", {})
    if not cfg.get("api_key"):
        st.warning("Generazione non attiva: l'amministratore deve inserire la chiave API nella sezione [ai] dei Secrets.")
        return
    bloccato = controlla_limiti("Le bozze vanno rilette e personalizzate: gli elaborati devono essere originali.")
    ins = q(f"SELECT nome FROM {S}.insegnamenti ORDER BY nome")["nome"].tolist()
    docs = q(f"SELECT id, nome_file, versione, categoria FROM {S}.documenti WHERE utente_id=:u ORDER BY caricato_il DESC", u=uid())

    tipo = st.selectbox("Che cosa vuoi scrivere?", list(TIPI_TESTO))
    c1, c2 = st.columns(2)
    insegnamento = c1.selectbox("Insegnamento", ins) if tipo in ("Elaborato asincrono", "Riassunto di un documento") else ""
    capitolo = c1.selectbox("Capitolo", CAPITOLI) if tipo == "Capitolo della relazione finale" else ""
    caratteri = c2.number_input("Lunghezza (caratteri, spazi inclusi)", 500, 30000, 6500 if tipo == "Elaborato asincrono" else 3000, step=500)
    etichetta = {"Elaborato asincrono": "Traccia dell'esercitazione", "Capitolo della relazione finale": "I tuoi appunti per questo capitolo",
                 "Osservazione descrittiva": "Le tue note grezze", "Email a docente o segreteria": "Che cosa vuoi comunicare",
                 "Riassunto di un documento": "Su che cosa concentrarsi (facoltativo)", "Testo libero": "Richiesta"}[tipo]
    traccia = st.text_area(etichetta, height=180)
    fonti = st.multiselect("Usa come fonte i miei documenti (facoltativo)", docs["id"].tolist(),
                           format_func=lambda i: (lambda r: f"{r['nome_file']} v{r['versione']} · {r['categoria']}")(docs.set_index("id").loc[i]),
                           max_selections=3)
    if st.button("Genera bozza", type="primary", disabled=bool(bloccato)):
        if not traccia.strip() and tipo not in ("Riassunto di un documento",):
            st.error("Scrivi la traccia o le note da cui partire."); return
        if tipo == "Riassunto di un documento" and not fonti:
            st.error("Scegli almeno un documento da riassumere."); return
        prompt = TIPI_TESTO[tipo].format(insegnamento=insegnamento, traccia=traccia, capitolo=capitolo,
                                         scuola=me().get("scuola_tirocinio") or "[scuola]",
                                         firma=f"{me().get('nome', '')} {me().get('cognome', '')}",
                                         matricola=me().get("matricola") or "[matricola]", gruppo=me().get("gruppo"))
        for i in fonti:
            r = docs.set_index("id").loc[i]
            prompt += f"\n<documento nome=\"{r['nome_file']}\">\n{estrai_testo(int(i))}\n</documento>\n"
        prompt += f"\nLunghezza: circa {int(caratteri)} caratteri spazi inclusi (non meno del 95%)."
        with st.container(border=True):
            testo = st.write_stream(genera_stream(prompt, max_tokens=min(int(caratteri / 2.5) + 1500, 16000)))
        tin, tout = st.session_state.get("_uso", (0, 0))
        run(f"""INSERT INTO {S}.generazioni (utente_id,tipo,richiesta,testo,caratteri,token_in,token_out)
                VALUES (:u,:t,:r,:x,:c,:i,:o)""", u=uid(), t=tipo, r=traccia[:4000], x=testo, c=len(testo), i=tin, o=tout)
        st.session_state["ultima"] = {"tipo": tipo, "titolo": capitolo or insegnamento or tipo, "testo": testo, "insegnamento": insegnamento}
        st.rerun()

    ult = st.session_state.get("ultima")
    if ult:
        st.subheader("Ultima bozza")
        testo = st.text_area("Puoi correggerla qui prima di salvarla", ult["testo"], height=420, key=f"edit_{hash(ult['testo'])}")
        st.caption(f"{len(testo):,} caratteri spazi inclusi".replace(",", "."))
        nome = f"{ult['titolo'][:60].replace('/', '-')}.docx"
        b = docx_da_testo(ult["titolo"], testo)
        c1, c2, c3 = st.columns(3)
        c1.download_button("Scarica Word", b, file_name=nome,
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        if ult["tipo"] == "Capitolo della relazione finale":
            area, sotto, cap = "Relazione finale", "Documenti", ult["titolo"]
        elif ult.get("insegnamento") in UFFICIALE:
            area, cap = UFFICIALE[ult["insegnamento"]], None
            sotto = "Elaborati e consegne" if ult["tipo"] == "Elaborato asincrono" else "Altro"
        else:
            area, sotto, cap = "Altro", "Documenti", None
        if c2.button("Salva nei miei documenti"):
            if salva_file({"bytes": b, "name": nome, "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                          area, sotto, cap, "Bozza generata"):
                st.toast(f"Salvato in Documenti › {area} › {sotto}.")
        if c3.button("Scarta"):
            del st.session_state["ultima"]
            st.rerun()

    with st.expander("Storico delle mie generazioni"):
        h = q(f"SELECT creato_il, tipo, caratteri, LEFT(richiesta, 120) AS richiesta FROM {S}.generazioni WHERE utente_id=:u ORDER BY creato_il DESC LIMIT 50", u=uid())
        st.dataframe(h, hide_index=True, width="stretch")


# ================================================================ spot pnev.it
def impostazione(chiave, default=""):
    r = q(f"SELECT valore FROM {S}.impostazioni WHERE chiave=:k", k=chiave)
    return r["valore"].iloc[0] if not r.empty else default


def con_utm(link):
    sep = "&" if "?" in link else "?"
    return f"{link}{sep}utm_source=tfa-manager&utm_medium=app&utm_campaign=gruppo-c"


def mostra_spot():
    """Riquadro a rotazione nella barra laterale. Nessun tracciamento degli utenti: i link portano solo la sorgente (utm)."""
    if impostazione("spot_attivo", "1") != "1":
        return
    sp = q(f"SELECT titolo, testo, pulsante, link FROM {S}.spot WHERE attivo ORDER BY ordine, id")
    if sp.empty:
        return
    import html as _h
    slides = "".join(
        f"""<div class="sl{' on' if i == 0 else ''}"><b>{_h.escape(r['titolo'] or '')}</b><p>{_h.escape(r['testo'] or '')}</p>
        <a href="{_h.escape(con_utm(r['link'] if str(r['link'] or '').startswith(('https://', 'http://')) else PNEV))}" target="_blank" rel="noopener">{_h.escape(r['pulsante'] or 'Scopri di più')}</a></div>"""
        for i, (_, r) in enumerate(sp.iterrows()))
    dots = "".join(f'<i class="{"on" if i == 0 else ""}"></i>' for i in range(len(sp)))
    pagina = f"""
<style>
 body{{margin:0;font-family:"Source Sans Pro",system-ui,sans-serif}}
 .box{{background:#fff;border:1px solid #c9cdf2;border-radius:10px;padding:12px 12px 10px;color:#1e2152;position:relative}}
 .brand{{display:flex;align-items:center;gap:6px;font-size:12px;color:#4a4fa8;margin-bottom:6px}}
 .ring{{width:14px;height:14px;border-radius:50%;background:radial-gradient(circle,#fff 38%,transparent 40%),conic-gradient(#3b5bdb,#5f3dc4,#3b5bdb)}}
 .sl{{display:none}} .sl.on{{display:block;animation:f .5s}}
 @keyframes f{{from{{opacity:0}}to{{opacity:1}}}}
 b{{font-size:15px;display:block;margin-bottom:4px}}
 p{{font-size:13px;line-height:1.4;margin:0 0 9px;color:#363a6b}}
 a{{display:inline-block;background:#3b4cca;color:#fff;text-decoration:none;font-size:13px;font-weight:600;padding:6px 11px;border-radius:6px}}
 a:focus-visible{{outline:2px solid #5f3dc4;outline-offset:2px}}
 .dots{{margin-top:8px;display:flex;gap:5px}} .dots i{{width:6px;height:6px;border-radius:50%;background:#c9cdf2}} .dots i.on{{background:#3b4cca}}
 .firma{{border-top:1px solid #e3e5fa;margin-top:9px;padding-top:7px;font-size:12px;line-height:1.35;color:#363a6b}}
 .firma strong{{display:block;font-size:13px;color:#1e2152}}
 @media (prefers-reduced-motion:reduce){{.sl.on{{animation:none}}}}
</style>
<div class="box" id="b"><div class="brand"><span class="ring"></span>www.pnev.it</div>{slides}<div class="dots">{dots}</div>
<div class="firma"><strong>{_h.escape(impostazione("spot_firma", FIRMA_DEFAULT))}</strong><span>{_h.escape(impostazione("spot_titoli", TITOLI_DEFAULT))}</span></div></div>
<script>
 const s=[...document.querySelectorAll('.sl')], d=[...document.querySelectorAll('.dots i')]; let k=0, pausa=false;
 const b=document.getElementById('b'); b.onmouseenter=()=>pausa=true; b.onmouseleave=()=>pausa=false;
 const vai=n=>{{s[k].classList.remove('on');d[k].classList.remove('on');k=(n+s.length)%s.length;s[k].classList.add('on');d[k].classList.add('on');}};
 d.forEach((x,i)=>x.onclick=()=>vai(i));
 if(s.length>1) setInterval(()=>{{if(!pausa) vai(k+1)}}, 8000);
</script>"""
    if hasattr(st, "iframe"):          # Streamlit recente
        st.iframe(pagina, height=295)
    else:                              # versioni precedenti
        import streamlit.components.v1 as components
        components.html(pagina, height=295)


def credito():
    """Firma dell'autore dell'app (testi modificabili in Amministrazione → Spot pnev.it)."""
    try:
        firma, titoli = impostazione("spot_firma", FIRMA_DEFAULT), impostazione("spot_titoli", TITOLI_DEFAULT)
    except Exception:
        firma, titoli = FIRMA_DEFAULT, TITOLI_DEFAULT
    return f"TFA Manager · ideato e realizzato da **{firma}** – {titoli} · [www.pnev.it](https://www.pnev.it/)"


def cambio_obbligatorio():
    """Dopo un reset dell'amministratore l'utente deve scegliere una nuova password prima di continuare."""
    st.title("Scegli una nuova password")
    st.info("Stai usando una password provvisoria: scegline una personale per continuare.")
    with st.form("pw_obbl"):
        n1 = st.text_input("Nuova password (almeno 8 caratteri)", type="password")
        n2 = st.text_input("Ripeti la nuova password", type="password")
        if st.form_submit_button("Salva e continua", type="primary"):
            if len(n1) < 8 or n1 != n2:
                st.error("Le password non coincidono o sono troppo corte.")
            else:
                run(f"UPDATE {S}.utenti SET pwd_hash=:h, pwd_temporanea=FALSE WHERE id=:u",
                    h=bcrypt.hashpw(n1.encode(), bcrypt.gensalt()).decode(), u=uid())
                st.session_state["cambia_pwd"] = False
                st.rerun()


def password_provvisoria():
    import secrets as _s
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(_s.choice(alfabeto) for _ in range(10))


# ================================================================ avvio
try:
    init_db()
except Exception as e:
    st.error(f"Connessione al database non riuscita: {e}")
    if "deadlock" in str(e).lower() or "lock" in str(e).lower():
        st.info("Il database era occupato da un'altra sessione: ricarica la pagina tra qualche secondo.")
    else:
        st.info("Controlla il segreto [db] url e che il server OVH accetti connessioni esterne con SSL.")
    st.stop()

if not accesso():
    st.stop()
if st.session_state.get("cambia_pwd"):
    cambio_obbligatorio()
    st.stop()

PAGINE = {"Cruscotto": pag_cruscotto, "Calendario": pag_calendario, "Asincrone": pag_asincrone,
          "Assenze e recuperi": pag_assenze, "Tirocinio": pag_tirocinio, "Materiali del corso": pag_materiali, "Documenti": pag_documenti,
          "Relazione finale": pag_tesi, "Genera testi": pag_genera, "Viaggi": pag_viaggi, "Soggiorno": pag_soggiorno, "Profilo": pag_profilo}
if me().get("ruolo") == "admin":
    PAGINE["Amministrazione"] = pag_admin
with st.sidebar:
    st.write(f"**{me().get('nome', '')} {me().get('cognome', '')}**  \nGruppo {me().get('gruppo')} · {me().get('matricola') or ''}")
    scelta = st.radio("Sezioni", list(PAGINE))
    st.divider()
    if st.button("Esci"):
        st.session_state.clear()
        st.rerun()
    mostra_spot()
    st.caption(credito())
PAGINE[scelta]()
