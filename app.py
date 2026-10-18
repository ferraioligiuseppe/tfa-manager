"""TFA Manager – piattaforma multi-utente per i corsisti (Gruppo C).
Streamlit + PostgreSQL OVH (schema «tfa», separato dal gestionale).

Segreti (Streamlit Cloud → Settings → Secrets):
    codice_invito  = "..."          # richiesto per registrarsi
    admin_username = "gferraioli"   # questo utente diventa amministratore alla registrazione
    [ai]                            # facoltativo: generazione testi
    api_key = "sk-ant-..."          # chiave API Anthropic ("demo" = prova senza costi)
    model = "claude-sonnet-5-5"
    limite_giornaliero = 20         # generazioni al giorno per utente
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
CATEGORIE = ["Asincrone", "Tirocinio diretto", "Tirocinio indiretto", "Registri e firme",
             "Relazione finale", "Materiale lezioni", "Documenti personali", "Altro"]
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
CREATE TABLE IF NOT EXISTS {S}.generazioni (id SERIAL PRIMARY KEY, utente_id INT REFERENCES {S}.utenti(id) ON DELETE CASCADE,
  tipo TEXT, richiesta TEXT, testo TEXT, caratteri INT, token_in INT, token_out INT, creato_il TIMESTAMPTZ DEFAULT now())
"""


def seed():
    return json.loads((Path(__file__).parent / "data" / "seed.json").read_text(encoding="utf-8"))


def init_db():
    with engine().begin() as c:
        for stmt in DDL.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        if c.execute(text(f"SELECT COUNT(*) FROM {S}.insegnamenti")).scalar():
            return
        d = seed()
        pd.DataFrame(d["insegnamenti"]).to_sql("insegnamenti", c, schema=S, if_exists="append", index=False)
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
                if r.empty or not bcrypt.checkpw(pwd.encode(), r.iloc[0]["pwd_hash"].encode()):
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


def pag_documenti():
    st.title("I miei documenti")
    st.caption(f"Visibili solo a te. Massimo {MAX_FILE_MB} MB per file, {QUOTA_MB} MB in totale. Non caricare dati identificativi o sanitari di alunni.")
    usato = q(f"SELECT COALESCE(SUM(dimensione),0) AS b FROM {S}.documenti WHERE utente_id=:u", u=uid())["b"].iloc[0] / 1e6
    st.progress(min(usato / QUOTA_MB, 1.0), text=f"{usato:.1f} MB su {QUOTA_MB} MB")
    with st.expander("Carica documenti", expanded=False):
        with st.form("up", clear_on_submit=True):
            files = st.file_uploader("File", accept_multiple_files=True)
            c1, c2 = st.columns(2)
            cat = c1.selectbox("Categoria", CATEGORIE)
            cap = c2.selectbox("Capitolo della relazione (facoltativo)", ["—"] + CAPITOLI)
            note = st.text_input("Note")
            if st.form_submit_button("Carica", type="primary") and files:
                for f in files:
                    b = f.getvalue()
                    if len(b) > MAX_FILE_MB * 1e6:
                        st.error(f"{f.name}: supera {MAX_FILE_MB} MB."); continue
                    if usato + len(b) / 1e6 > QUOTA_MB:
                        st.error("Spazio esaurito."); break
                    v = q(f"SELECT COALESCE(MAX(versione),0)+1 AS v FROM {S}.documenti WHERE utente_id=:u AND nome_file=:n AND categoria=:c",
                          u=uid(), n=f.name, c=cat)["v"].iloc[0]
                    run(f"""INSERT INTO {S}.documenti (utente_id,categoria,capitolo,nome_file,mime,dimensione,versione,note,contenuto)
                        VALUES (:u,:c,:cap,:n,:m,:d,:v,:no,:b)""", u=uid(), c=cat, cap=None if cap == "—" else cap,
                        n=f.name, m=f.type or "application/octet-stream", d=len(b), v=int(v), no=note, b=b)
                    usato += len(b) / 1e6
                st.toast("Documenti caricati.")
                st.rerun()
    docs = q(f"""SELECT id, categoria, capitolo, nome_file, versione, dimensione, note, caricato_il
                 FROM {S}.documenti WHERE utente_id=:u ORDER BY caricato_il DESC""", u=uid())
    if docs.empty:
        st.info("Nessun documento: caricane uno dal riquadro qui sopra.")
        return
    c1, c2 = st.columns([1, 2])
    filtro = c1.selectbox("Categoria", ["Tutte"] + CATEGORIE)
    cerca = c2.text_input("Cerca nel nome o nelle note")
    if filtro != "Tutte":
        docs = docs[docs["categoria"] == filtro]
    if cerca:
        docs = docs[docs["nome_file"].str.contains(cerca, case=False) | docs["note"].fillna("").str.contains(cerca, case=False)]
    docs = docs.assign(KB=(docs["dimensione"] / 1024).round(0), caricato=pd.to_datetime(docs["caricato_il"]).dt.tz_convert(TZ).dt.strftime("%d/%m/%Y %H:%M"))
    st.dataframe(docs[["categoria", "nome_file", "versione", "KB", "capitolo", "note", "caricato"]], hide_index=True, width="stretch")
    scelta = st.selectbox("Apri un documento", docs["id"].tolist(),
                          format_func=lambda i: (lambda r: f"{r['nome_file']} · v{r['versione']} · {r['categoria']}")(docs.set_index("id").loc[i]))
    r = q(f"SELECT nome_file, mime, contenuto FROM {S}.documenti WHERE id=:i AND utente_id=:u", i=int(scelta), u=uid()).iloc[0]
    c1, c2 = st.columns(2)
    c1.download_button("Scarica", bytes(r["contenuto"]), file_name=r["nome_file"], mime=r["mime"], type="primary")
    if c2.button("Elimina questo documento"):
        run(f"DELETE FROM {S}.documenti WHERE id=:i AND utente_id=:u", i=int(scelta), u=uid())
        st.toast("Documento eliminato.")
        st.rerun()


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


def pag_viaggi():
    st.title("I miei viaggi")
    st.caption("Annota tratte, orari e opzioni. Verifica sempre su trenitalia.com e italotreno.it.")
    editor(personale("viaggi"), "via", lambda d: salva_personale("viaggi", d))


def pag_profilo():
    st.title("Profilo")
    u = q(f"SELECT * FROM {S}.utenti WHERE id=:u", u=uid()).iloc[0]
    with st.form("prof"):
        c1, c2 = st.columns(2)
        v = {k: (c1 if i % 2 == 0 else c2).text_input(lab, value=u[k] or "") for i, (k, lab) in enumerate([
            ("nome", "Nome"), ("cognome", "Cognome"), ("matricola", "Matricola"), ("email", "Email"),
            ("email_istituzionale", "Email istituzionale"), ("telefono", "Telefono"), ("indirizzo", "Indirizzo"),
            ("scuola_tirocinio", "Scuola del tirocinio diretto"), ("tutor_scolastico", "Tutor scolastico")])}
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
                for t in ["asincrone", "assenze", "tirocinio", "viaggi", "tesi", "generazioni"]:
                    z.writestr(f"{t}.csv", personale(t).to_csv(index=False))
                for _, d in q(f"SELECT id, categoria, nome_file, versione, contenuto FROM {S}.documenti WHERE utente_id=:u", u=uid()).iterrows():
                    z.writestr(f"documenti/{d['categoria']}/v{d['versione']}_{d['id']}_{d['nome_file']}", bytes(d["contenuto"]))
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
    t1, t2, t3, t4, t5 = st.tabs(["Iscritti", "Calendario", "Insegnamenti", "Seminari", "Uso generazione testi"])
    with t1:
        ut = q(f"SELECT id, username, nome, cognome, gruppo, email, ruolo, attivo, creato_il, ultimo_accesso FROM {S}.utenti ORDER BY cognome")
        out = st.data_editor(ut, hide_index=True, width="stretch", key="adm_ut",
                             disabled=["id", "username", "nome", "cognome", "email", "ruolo", "creato_il", "ultimo_accesso"],
                             column_config={"gruppo": st.column_config.SelectboxColumn(options=GRUPPI)})
        if st.button("Salva iscritti", type="primary"):
            for _, r in out.iterrows():
                if int(r["id"]) == uid() and not r["attivo"]:
                    st.error("Non puoi disattivare il tuo account."); break
                run(f"UPDATE {S}.utenti SET attivo=:a, gruppo=:g WHERE id=:i", a=bool(r["attivo"]), g=r["gruppo"], i=int(r["id"]))
            else:
                st.toast("Iscritti aggiornati.")
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
        st.dataframe(q(f"""SELECT u.username, COUNT(g.id) AS generazioni, COALESCE(SUM(g.token_in),0) AS token_in,
                COALESCE(SUM(g.token_out),0) AS token_out, MAX(g.creato_il) AS ultima
                FROM {S}.utenti u LEFT JOIN {S}.generazioni g ON g.utente_id=u.id
                AND g.creato_il >= date_trunc('month', now()) GROUP BY u.username ORDER BY generazioni DESC"""),
            hide_index=True, width="stretch")



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


def genera_stream(prompt: str, max_tokens: int):
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
    with client.messages.stream(model=cfg.get("model", "claude-sonnet-5-5"), max_tokens=max_tokens,
                                system=SYSTEM_AI, messages=[{"role": "user", "content": prompt}]) as s:
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


def pag_genera():
    st.title("Genera testi")
    cfg = st.secrets.get("ai", {})
    if not cfg.get("api_key"):
        st.warning("Generazione non attiva: l'amministratore deve inserire la chiave API nella sezione [ai] dei Secrets.")
        return
    limite = int(cfg.get("limite_giornaliero", 20))
    usate = int(q(f"SELECT COUNT(*) AS n FROM {S}.generazioni WHERE utente_id=:u AND creato_il::date = CURRENT_DATE", u=uid())["n"].iloc[0])
    st.caption(f"Le bozze vanno rilette e personalizzate: gli elaborati devono essere originali. Oggi: {usate}/{limite} generazioni.")
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
    if st.button("Genera bozza", type="primary", disabled=usate >= limite):
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
        st.session_state["ultima"] = {"tipo": tipo, "titolo": capitolo or insegnamento or tipo, "testo": testo}
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
        cat = "Relazione finale" if ult["tipo"] == "Capitolo della relazione finale" else "Asincrone" if ult["tipo"] == "Elaborato asincrono" else "Altro"
        if c2.button("Salva nei miei documenti"):
            v = q(f"SELECT COALESCE(MAX(versione),0)+1 AS v FROM {S}.documenti WHERE utente_id=:u AND nome_file=:n AND categoria=:c",
                  u=uid(), n=nome, c=cat)["v"].iloc[0]
            run(f"""INSERT INTO {S}.documenti (utente_id,categoria,capitolo,nome_file,mime,dimensione,versione,note,contenuto)
                    VALUES (:u,:c,:cap,:n,:m,:d,:v,'Bozza generata',:b)""", u=uid(), c=cat,
                cap=ult["titolo"] if cat == "Relazione finale" else None, n=nome,
                m="application/vnd.openxmlformats-officedocument.wordprocessingml.document", d=len(b), v=int(v), b=b)
            st.toast(f"Salvato in Documenti › {cat}.")
        if c3.button("Scarta"):
            del st.session_state["ultima"]
            st.rerun()

    with st.expander("Storico delle mie generazioni"):
        h = q(f"SELECT creato_il, tipo, caratteri, LEFT(richiesta, 120) AS richiesta FROM {S}.generazioni WHERE utente_id=:u ORDER BY creato_il DESC LIMIT 50", u=uid())
        st.dataframe(h, hide_index=True, width="stretch")


# ================================================================ avvio
try:
    init_db()
except Exception as e:
    st.error(f"Connessione al database non riuscita: {e}")
    st.info("Controlla il segreto [db] url e che il server OVH accetti connessioni esterne con SSL.")
    st.stop()

if not accesso():
    st.stop()

PAGINE = {"Cruscotto": pag_cruscotto, "Calendario": pag_calendario, "Asincrone": pag_asincrone,
          "Assenze e recuperi": pag_assenze, "Tirocinio": pag_tirocinio, "Documenti": pag_documenti,
          "Relazione finale": pag_tesi, "Genera testi": pag_genera, "Viaggi": pag_viaggi, "Profilo": pag_profilo}
if me().get("ruolo") == "admin":
    PAGINE["Amministrazione"] = pag_admin
with st.sidebar:
    st.write(f"**{me().get('nome', '')} {me().get('cognome', '')}**  \nGruppo {me().get('gruppo')} · {me().get('matricola') or ''}")
    scelta = st.radio("Sezioni", list(PAGINE))
    st.divider()
    if st.button("Esci"):
        st.session_state.clear()
        st.rerun()
PAGINE[scelta]()
