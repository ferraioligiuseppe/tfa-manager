"""Genera data/seed.json (per Streamlit) e docs/data.js (per la pagina HTML)."""
import json, os
os.chdir(os.path.dirname(os.path.abspath(__file__)) + '/..')
P = 'Psicologia dello sviluppo'; M = 'Modelli integrati'; MC = 'Didattica metacognitiva'
RA = 'Pedagogia relazione d’aiuto'; GC = 'Ped. speciale gestione gruppo classe'
DI = 'Ped. e Did. disabilità intellettiva'; S = 'Did. disabilità sensoriali'
N = 'Neuropsichiatria Infantile'; PEI = 'Progettazione PDF e PEI'; T = 'Tirocinio indiretto'; X = 'Da definire'
# * = blocco con esame
cal = {'2026-09-26': [P, P, M, M], '2026-09-27': [MC, MC, T, T], '2026-10-10': [M, M, RA, RA],
       '2026-10-11': [MC, MC, GC, GC], '2026-10-17': [DI, DI, S, S], '2026-10-18': [N, N, MC, MC],
       '2026-10-24': [S, S, DI, DI], '2026-10-25': [DI, DI, MC, MC], '2026-11-07': [PEI] * 4,
       '2026-11-08': [PEI] * 4, '2026-11-14': [M, M, MC, MC + '*'], '2026-11-15': [DI, DI, T, T],
       '2026-11-21': [N, N, M, M], '2026-11-22': [S] * 4, '2026-11-28': [N, N, P, P], '2026-11-29': [T] * 4,
       '2026-12-05': [N, N, DI, DI + '*'], '2026-12-06': [X] * 4, '2026-12-12': [P] * 4,
       '2026-12-13': [M, M + '*', GC, GC], '2026-12-19': [PEI, PEI + '*', N, N + '*'], '2026-12-20': [GC] * 4,
       '2027-01-09': [T, T, P, P + '*'], '2027-01-10': [GC, GC + '*', X, X], '2027-01-16': [X] * 4,
       '2027-01-17': [RA + '*', X, S, S + '*']}
F = ['08:30-11:00', '11:00-13:30', '14:30-17:00', '17:00-19:30']
lez = [{'data': d, 'fascia': F[i], 'insegnamento': c.rstrip('*'), 'esame': c.endswith('*')}
       for d, cells in cal.items() for i, c in enumerate(cells)]
ins = [
    {'nome': P, 'docente': 'Prof.ssa Giorgia Terrinoni', 'email': '', 'ore': 25},
    {'nome': M, 'docente': 'Prof. Simone Federici', 'email': '', 'ore': 25},
    {'nome': MC, 'docente': 'Prof.ssa Rosanna Moretti', 'email': 'r.moretti@unilink.it', 'ore': 25},
    {'nome': RA, 'docente': 'Prof. Mancini', 'email': '', 'ore': 7.5},
    {'nome': GC, 'docente': '', 'email': '', 'ore': 25},
    {'nome': DI, 'docente': 'Prof. Fabio Simonetti', 'email': '', 'ore': 25},
    {'nome': S, 'docente': 'Prof.ssa Giovanna Rosaria Palmieri', 'email': '', 'ore': 25},
    {'nome': N, 'docente': 'Prof.ssa Aurora Casillo', 'email': 'a.casillo@unilink.it', 'ore': 25},
    {'nome': PEI, 'docente': '', 'email': '', 'ore': 25},
    {'nome': T, 'docente': 'Prof.ssa Placida Brillantino', 'email': 'p.brillantino@unilink.it', 'ore': 25}]
asi = [{'insegnamento': a, 'titolo': b, 'stato': c} for a, b, c in [
    (MC, 'Attività metacognitiva – amicizia', 'Da personalizzare'), (DI, 'Prove moduli 1-4', 'Pronto'),
    (M, 'Rinforzo positivo', 'Pronto'), (N, 'Mod. 1 Autismo', 'Pronto'), (N, 'Mod. 2 ADHD', 'Pronto'),
    (N, 'Mod. 3 Disabilità intellettiva', 'Pronto'), (N, 'Mod. 4 DSA', 'Pronto'),
    (P, 'Caso D.', 'Pronto'), (S, 'Sordocecità', 'Pronto')]]
ass = [{'data': '2026-09-26', 'insegnamento': P, 'ore': 5}, {'data': '2026-09-26', 'insegnamento': M, 'ore': 5},
       {'data': '2026-09-27', 'insegnamento': MC, 'ore': 5}, {'data': '2026-09-27', 'insegnamento': T, 'ore': 5}]
sem = [{'data': d, 'luogo': l} for d, l in [('2026-11-13', 'Roma'), ('2026-12-11', 'Novedrate'),
       ('2027-01-15', 'Città di Castello'), ('2027-01-29', 'Roma'), ('2027-02-12', 'Napoli'),
       ('2027-03-12', 'Novedrate'), ('2027-04-16', 'Roma')]]
viaggi = [
    {'opzione': 'Sabato: auto + Frecciarossa da Salerno', 'andata': 'Pagani 04:45, Salerno FR 9608 alle 05:16, Roma Termini 06:50, metro A fino a Baldo degli Ubaldi', 'ritorno': 'Uscita 19:30, Termini, FR serale per Salerno (circa 20:30)'},
    {'opzione': 'Domenica: auto + treno da Afragola', 'andata': 'Il FR 9608 la domenica non circola: auto fino ad Afragola (35 min) e primo FR o Italo utile', 'ritorno': 'Rientro ad Afragola e auto'},
    {'opzione': 'Tutta in auto', 'andata': 'Partenza 05:15, A30/A1, circa 260 km, 2 h 45', 'ritorno': 'Partenza 19:40, a casa verso le 22:30'}]
monte = [{'voce': 'Diretto: osservazione e UdA', 'tipo': 'diretto', 'ore': 90},
         {'voce': 'Diretto: organi collegiali', 'tipo': 'collegiali', 'ore': 30},
         {'voce': 'Diretto: documentazione del caso', 'tipo': 'documentazione', 'ore': 30},
         {'voce': 'Registro 25 ore', 'tipo': 'registro25', 'ore': 25},
         {'voce': 'Indiretto', 'tipo': 'indiretto', 'ore': 50}]
d = {'lezioni': lez, 'insegnamenti': ins, 'asincrone': asi, 'assenze': ass, 'seminari': sem,
     'viaggi': viaggi, 'monte_ore': monte}
json.dump(d, open('data/seed.json', 'w'), ensure_ascii=False, indent=1)
pub = {k: d[k] for k in ('lezioni', 'insegnamenti', 'viaggi')}  # solo dati non personali
open('docs/data.js', 'w').write('window.TFA=' + json.dumps(pub, ensure_ascii=False) + ';')
print(len(lez), 'blocchi')
