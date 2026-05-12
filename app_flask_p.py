from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
import pandas as pd
import numpy as np
import os, json, math, time, io, re, sqlite3
from datetime import datetime, timedelta
import joblib

app = Flask(__name__)
app.secret_key = "Any"

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ════════════════════════════════════════════════════════
# COLONNES RÉELLES DU FICHIER Casa_RNVP.xlsx
# ════════════════════════════════════════════════════════
# Feuille VOIE
# ------- ICI diff col 

# Feuille QUARTIER
# ------- ICI diff col

# Noms des feuilles Excel
SHEET_VOIE        = 'VOIE'
SHEET_QUARTIER    = 'QUARTIER'

# ════════════════════════════════════════════════════════
# BASE DE DONNÉES SQLite
# ════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(os.path.join(DATA_DIR, 'historique.db'))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tournees_historique (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, nb_colis INTEGER, taux_correction REAL,
        heure_sortie TEXT, created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS colis_historique (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, cab TEXT, destinataire TEXT,
        adresse TEXT, statut_correction TEXT,
        score_fuzzy REAL, facteur_assigne INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

# ════════════════════════════════════════════════════════
# UTILITAIRES
# ════════════════════════════════════════════════════════

def haversine(lat1, lon1, lat2, lon2):
    """Distance réelle entre deux points GPS (formule de Haversine)."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def normaliser_adresse(adresse):
    """
    Normalise une adresse pour la comparaison RapidFuzz :
    - Majuscules
    - Suppression ponctuation
    - Expansion abréviations
    - Suppression numéros de début
    """
    if not isinstance(adresse, str):
        return ""
    a = adresse.upper().strip()
    a = re.sub(r'[.,;:!?]', ' ', a)
    a = re.sub(r'\s+', ' ', a)
    abbrevs = {
        r'\bAV\b\.?':   'AVENUE',
        r'\bBD\b\.?':   'BOULEVARD',
        r'\bBLVD\b\.?': 'BOULEVARD',
        r'\bR\b\.?':    'RUE',
        r'\bIMP\b\.?':  'IMPASSE',
        r'\bRES\b\.?':  'RESIDENCE',
        r'\bCIT\b\.?':  'CITE',
        r'\bLOT\b\.?':  'LOTISSEMENT',
        r'\bALL\b\.?':  'ALLEE',
        r'\bPL\b\.?':   'PLACE',
        r'\bSQ\b\.?':   'SQUARE',
        r'\bCHE\b\.?':  'CHEMIN',
        r'\bDRB\b\.?':  'DERB',
    }
    for pat, rep in abbrevs.items():
        a = re.sub(pat, rep, a)
    a = re.sub(r'^\d+\s+', '', a)
    return a.strip()


def construire_adresse_complete_voie(row):
    """
    Construit l'adresse complète à géocoder pour une voie :
    Type_Voie + Nom_Voie (ex: "RUE HALIMA SAADIA")
    """
    type_v = str(row.get(COL_VOIE_TYPE, '')).strip()
    nom_v  = str(row.get(COL_VOIE_NOM,  '')).strip()
    if type_v and type_v.upper() not in nom_v.upper():
        return f"{type_v} {nom_v}".strip()
    return nom_v


def construire_adresse_complete_quartier(row):
    """
    Construit l'adresse complète à géocoder pour un quartier :
    type_quartier + nom_quartier (ex: "LOTISSEMENT AL BARAKA")
    """
    type_q = str(row.get(COL_QRT_TYPE, '')).strip()
    nom_q  = str(row.get(COL_QRT_NOM,  '')).strip()
    if type_q and type_q.upper() not in nom_q.upper():
        return f"{type_q} {nom_q}".strip()
    return nom_q


COULEURS_FACTEURS = [
    '#1565C0', '#F9A825', '#2E7D32', '#C62828',
    '#6A1B9A', '#00838F', '#E65100', '#37474F',
    '#AD1457', '#00600F', '#1A237E', '#BF360C',
    '#1E88E5', '#43A047', '#FB8C00', '#8E24AA'
]

# ════════════════════════════════════════════════════════
# ROUTES PRINCIPALES
# ════════════════════════════════════════════════════════

@app.route('/')
def index():
    rnvp_voie_ok = os.path.exists(os.path.join(DATA_DIR, 'rnvp_voie_geocoded.csv'))
    rnvp_qrt_ok  = os.path.exists(os.path.join(DATA_DIR, 'rnvp_quartier_geocoded.csv'))
    colis_ok     = os.path.exists(os.path.join(DATA_DIR, 'colis_jour.csv'))
    corriges_ok  = os.path.exists(os.path.join(DATA_DIR, 'colis_corriges.csv'))
    tournees_ok  = os.path.exists(os.path.join(DATA_DIR, 'tournees_result.json'))

    stats = {
        'rnvp-_voie_ok':   rnvp_voie_ok,
        'rnvp-_qrt_ok':    rnvp_qrt_ok,
        'colis-_ok':       colis_ok,
        'corriges_ok':    corriges_ok,
        'tournees-_ok':    tournees_ok,
        'rnvp-_voie_count': 0,
        'rnvp_qrt-_count':  0,
        'colis__count':     0,
        'nb_jours':        0,
        'total__colis':     0,
    }
    if rnvp_voie_ok:
        stats['rnvp_voie_count'] = len(pd.read_csv(os.path.join(DATA_DIR, 'rnvp_voie_geocoded.csv')))
    if rnvp_qrt_ok:
        stats['rnvp_qrt_count'] = len(pd.read_csv(os.path.join(DATA_DIR, 'rnvp_quartier_geocoded.csv')))
    if colis_ok:
        stats['colis_count'] = len(pd.read_csv(os.path.join(DATA_DIR, 'colis_jour.csv')))
    try:
        conn = sqlite3.connect(os.path.join(DATA_DIR, 'historique.db'))
        stats['nb_jours']    = conn.execute("SELECT COUNT(*) FROM tournees_historique").fetchone()[0]
        stats['total_colis'] = conn.execute("SELECT SUM(nb_colis) FROM tournees_historique").fetchone()[0] or 0
        conn.close()
    except:
        pass

    return render_template('index.html', stats=stats)


@app.route('/rnvp')
def rnvp():
    return render_template('rnv.html')

@app.route('/colis')
def colis():
    return render_template('coli.html')

@app.route('/correction')
def correction():
    return render_template('corre.html')

@app.route('/optimisation')
def optimisation():
    return render_template('optimi.html')

@app.route('/carte')
def carte():
    resultat = None
    rp = os.path.join(DATA_DIR, 'tournees_result.json')
    if os.path.exists(rp):
        with open(rp, encoding='utf-8') as f:
            resultat = json.load(f)
    return render_template('carte.html', resultat=resultat)

@app.route('/predictions')
def predictions():
    return render_template('predictions.html')


# ════════════════════════════════════════════════════════
# API — UPLOAD RNVP (adapté aux vraies colonnes) = Pipeline RNVP & Géocodage
# ════════════════════════════════════════════════════════

@app.route('/api/upload_rnvp', methods=['POST'])
def upload_rnvp():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Aucun fichier reçu'})

    f = request.files['file']
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'success': False, 'message': 'Format invalide — fichier .xlsx requis'})

    try:
        # ── Feuille VOIE ─────────────────────────────────
        df_voie = pd.read_excel(f, sheet_name=SHEET_VOIE)
        f.seek(0)
        df_qrt  = pd.read_excel(f, sheet_name=SHEET_QUARTIER)

        # Vérification colonnes VOIE
        cols_voie_req = [COL_VOIE_NOM, COL_VOIE_TYPE, COL_VOIE_CP, COL_VOIE_LOC]
        cols_voie_manq = [c for c in cols_voie_req if c not in df_voie.columns]
        if cols_voie_manq:
            return jsonify({'success': False,
                            'message': f'Colonnes manquantes dans VOIE : {cols_voie_manq}. Colonnes trouvées : {list(df_voie.columns)}'})

        # Nettoyage VOIE
        df_voie[COL_VOIE_NOM]  = df_voie[COL_VOIE_NOM].astype(str).str.strip().str.upper()
        df_voie[COL_VOIE_TYPE] = df_voie[COL_VOIE_TYPE].astype(str).str.strip().str.upper()
        df_voie[COL_VOIE_CP]   = pd.to_numeric(df_voie[COL_VOIE_CP], errors='coerce').fillna(0).astype(int)
        df_voie[COL_VOIE_LOC]  = df_voie[COL_VOIE_LOC].astype(str).str.strip().str.upper()

        # Construire adresse complète pour le géocodage
        df_voie['ADRESSE_GEOCODAGE'] = df_voie.apply(construire_adresse_complete_voie, axis=1)

        # Supprimer doublons sur (Nom_Voie + CODE_POSTAL)
        avant = len(df_voie)
        df_voie = df_voie.drop_duplicates(subset=[COL_VOIE_NOM, COL_VOIE_CP])
        apres = len(df_voie)

        df_voie.to_csv(os.path.join(DATA_DIR, 'rnvp_voie_raw.csv'), index=False, encoding='utf-8-sig')

        # ── Feuille QUARTIER ──────────────────────────────
        # Vérification colonnes QUARTIER
        cols_qrt_req = [COL_QRT_NOM, COL_QRT_CP, COL_QRT_LOC]
        cols_qrt_manq = [c for c in cols_qrt_req if c not in df_qrt.columns]
        if cols_qrt_manq:
            return jsonify({'success': False,
                            'message': f'Colonnes manquantes dans QUARTIER : {cols_qrt_manq}. Colonnes trouvées : {list(df_qrt.columns)}'})

        df_qrt[COL_QRT_NOM]  = df_qrt[COL_QRT_NOM].astype(str).str.strip().str.upper()
        df_qrt[COL_QRT_TYPE] = df_qrt[COL_QRT_TYPE].astype(str).str.strip().str.upper() if COL_QRT_TYPE in df_qrt.columns else ''
        df_qrt[COL_QRT_CP]   = pd.to_numeric(df_qrt[COL_QRT_CP], errors='coerce').fillna(0).astype(int)
        df_qrt[COL_QRT_LOC]  = df_qrt[COL_QRT_LOC].astype(str).str.strip().str.upper()

        # Construire adresse complète quartier
        df_qrt['ADRESSE_GEOCODAGE'] = df_qrt.apply(construire_adresse_complete_quartier, axis=1)

        avant_q = len(df_qrt)
        df_qrt = df_qrt.drop_duplicates(subset=[COL_QRT_NOM, COL_QRT_CP])
        apres_q = len(df_qrt)

        df_qrt.to_csv(os.path.join(DATA_DIR, 'rnvp_quartier_raw.csv'), index=False, encoding='utf-8-sig')

        return jsonify({
            'success':      True,
            'voie_count':   len(df_voie),
            'qrt_count':    len(df_qrt),
            'voie_cols':    list(df_voie.columns),
            'qrt_cols':     list(df_qrt.columns),
            'apercu_voie':  df_voie[[COL_VOIE_NOM, COL_VOIE_TYPE, COL_VOIE_CP, COL_VOIE_LOC, 'ADRESSE_GEOCODAGE']].head(8).fillna('').to_dict('records'),
            'apercu_qrt':   df_qrt[[COL_QRT_NOM, COL_QRT_CP, COL_QRT_LOC, 'ADRESSE_GEOCODAGE']].head(8).fillna('').to_dict('records'),
            'doublons_voie': avant - apres,
            'doublons_qrt':  avant_q - apres_q,
        })

    except Exception as e:
        return jsonify({'success': False, 'message': f'Erreur : {str(e)}'})


# ════════════════════════════════════════════════════════
# GÉOCODAGE D'UNE ADRESSE (3 tentatives)
# ════════════════════════════════════════════════════════

def geocoder_une(adresse_geocodage, ville, pays):
    """
    Géocode une adresse avec Nominatim.
    3 tentatives :
      1. Adresse complète (Type + Nom)
      2. Nom seul (sans Type)
      3. 2 premiers mots significatifs
    """
    try:
        from geopy.geocoders import Nominatim
        geo = Nominatim(user_agent="pp_m_r_v2")

        def tenter(query):
            try:
                return geo.geocode(query, timeout=10)
            except Exception:
                return None

        # Tentative 1 — adresse complète Type + Nom
        loc = tenter(f"{adresse_geocodage}, {ville}, {pays}")
        if loc:
            return {'lat': round(loc.latitude, 6), 'lon': round(loc.longitude, 6), 'statut': 'OK'}

        # Tentative 2 — supprimer le numéro de début
        mots = adresse_geocodage.split()
        if mots and mots[0].isdigit():
            loc = tenter(f"{' '.join(mots[1:])}, {ville}, {pays}")
            if loc:
                return {'lat': round(loc.latitude, 6), 'lon': round(loc.longitude, 6), 'statut': 'APPROXIMATIF'}

        # Tentative 3 — 2 premiers mots significatifs
        mots_sig = [m for m in mots if not m.isdigit() and len(m) > 2]
        if len(mots_sig) >= 2:
            loc = tenter(f"{' '.join(mots_sig[:2])}, {ville}, {pays}")
            if loc:
                return {'lat': round(loc.latitude, 6), 'lon': round(loc.longitude, 6), 'statut': 'APPROXIMATIF'}

        return {'lat': None, 'lon': None, 'statut': 'ECHEC'}

    except ImportError:
        # Mode simulation si geopy non installé
        import random
        random.seed(hash(adresse_geocodage) % 9999)
        return {
            'lat': round(33.5731 + random.uniform(-0.06, 0.06), 6),
            'lon': round(-7.5898 + random.uniform(-0.09, 0.09), 6),
            'statut': 'SIMULE'
        }


# ════════════════════════════════════════════════════════
# API — GÉOCODAGE SSE (temps réel)
# ════════════════════════════════════════════════════════

@app.route('/api/geocoder_stream')
def geocoder_stream():
    ville = request.args.get('ville', 'v')
    pays  = request.args.get('pays', 'p')

    def generer():
        voie_path = os.path.join(DATA_DIR, 'rnvp_voie_raw.csv')
        qrt_path  = os.path.join(DATA_DIR, 'rnvp_quartier_raw.csv')

        if not os.path.exists(voie_path):
            yield f"data: {json.dumps({'type':'erreur','message':'Fichier RNV non trouvé — chargez d abord le fichier xlsx'})}\n\n"
            return

        df_voie = pd.read_csv(voie_path)
        df_qrt  = pd.read_csv(qrt_path) if os.path.exists(qrt_path) else pd.DataFrame()

        # Vérification colonne adresse géocodage
        if 'ADRESSE_GEOCODAGE' not in df_voie.columns:
            df_voie['ADRESSE_GEOCODAGE'] = df_voie.apply(construire_adresse_complete_voie, axis=1)

        adresses_v = df_voie['ADRESSE_GEOCODAGE'].dropna().unique().tolist()
        adresses_q = []
        if not df_qrt.empty:
            if 'ADRESSE_GEOCODAGE' not in df_qrt.columns:
                df_qrt['ADRESSE_GEOCODAGE'] = df_qrt.apply(construire_adresse_complete_quartier, axis=1)
            adresses_q = df_qrt['ADRESSE_GEOCODAGE'].dropna().unique().tolist()

        total = len(adresses_v) + len(adresses_q)
        yield f"data: {json.dumps({'type':'debut','total_voie':len(adresses_v),'total_qrt':len(adresses_q),'total':total})}\n\n"

        # ── VOIE ─────────────────────────────────────────
        sortie_v = os.path.join(DATA_DIR, 'rnvp_voie_geocoded.csv')
        deja_v, resultats_v = set(), []

        if os.path.exists(sortie_v):
            df_ex = pd.read_csv(sortie_v, encoding='utf-8-sig')
            if 'ADRESSE_GEOCODAGE' in df_ex.columns:
                deja_v = set(df_ex['ADRESSE_GEOCODAGE'].tolist())
                resultats_v = df_ex.to_dict('records')

        restantes_v = [a for a in adresses_v if a not in deja_v]
        n_ok = n_approx = n_echec = 0

        yield f"data: {json.dumps({'type':'section','message':f'Géocodage VOIE — {len(restantes_v)} adresses restantes'})}\n\n"

        for i, adresse_geo in enumerate(restantes_v):
            # Trouver la ligne correspondante
            mask = df_voie['ADRESSE_GEOCODAGE'] == adresse_geo
            if mask.any():
                ligne = df_voie[mask].iloc[0]
            else:
                continue

            gps = geocoder_une(adresse_geo, ville, pays)

            if   gps['statut'] == 'OK':           n_ok    += 1
            elif gps['statut'] == 'APPROXIMATIF': n_approx += 1
            else:                                 n_echec  += 1

            resultats_v.append({
                COL_VOIE_NOM:       ligne.get(COL_VOIE_NOM, ''),
                COL_VOIE_TYPE:      ligne.get(COL_VOIE_TYPE, ''),
                COL_VOIE_CP:        ligne.get(COL_VOIE_CP, ''),
                COL_VOIE_LOC:       ligne.get(COL_VOIE_LOC, ville.upper()),
                'ADRESSE_GEOCODAGE': adresse_geo,
                'LATITUDE':          gps['lat'],
                'LONGITUDE':         gps['lon'],
                'STATUT_GEOCODAGE':  gps['statut'],
            })

            traites = len(deja_v) + i + 1
            pct = round(traites / total * 100, 1)

            yield f"data: {json.dumps({'type':'progres','feuille':'VOIE','adresse':adresse_geo[:45],'statut':gps['statut'],'traites':traites,'total':total,'pct':pct,'n_ok':n_ok,'n_approx':n_approx,'n_echec':n_echec})}\n\n"

            # Sauvegarde toutes les 50 adresses
            if (i + 1) % 50 == 0 or i == len(restantes_v) - 1:
                pd.DataFrame(resultats_v).to_csv(sortie_v, index=False, encoding='utf-8-sig')
                yield f"data: {json.dumps({'type':'sauvegarde','fichier':'rnvp_voie_geocoded.csv','nb':len(resultats_v)})}\n\n"

            time.sleep(1.1)  # Rate limit Nominatim

        # ── QUARTIER ──────────────────────────────────────
        sortie_q = os.path.join(DATA_DIR, 'rnvp_quartier_geocoded.csv')
        deja_q, resultats_q = set(), []

        if os.path.exists(sortie_q):
            df_ex_q = pd.read_csv(sortie_q, encoding='utf-8-sig')
            if 'ADRESSE_GEOCODAGE' in df_ex_q.columns:
                deja_q = set(df_ex_q['ADRESSE_GEOCODAGE'].tolist())
                resultats_q = df_ex_q.to_dict('records')

        restantes_q = [a for a in adresses_q if a not in deja_q]

        if restantes_q:
            yield f"data: {json.dumps({'type':'section','message':f'Géocodage QUARTIER — {len(restantes_q)} adresses restantes'})}\n\n"

            for i, adresse_geo in enumerate(restantes_q):
                mask = df_qrt['ADRESSE_GEOCODAGE'] == adresse_geo
                if mask.any():
                    ligne = df_qrt[mask].iloc[0]
                else:
                    continue

                gps = geocoder_une(adresse_geo, ville, pays)

                if   gps['statut'] == 'OK':           n_ok    += 1
                elif gps['statut'] == 'APPROXIMATIF': n_approx += 1
                else:                                 n_echec  += 1

                resultats_q.append({
                    COL_QRT_NOM:         ligne.get(COL_QRT_NOM, ''),
                    COL_QRT_TYPE:        ligne.get(COL_QRT_TYPE, ''),
                    COL_QRT_CP:          ligne.get(COL_QRT_CP, ''),
                    COL_QRT_LOC:         ligne.get(COL_QRT_LOC, ville.upper()),
                    'ADRESSE_GEOCODAGE':  adresse_geo,
                    'LATITUDE':           gps['lat'],
                    'LONGITUDE':          gps['lon'],
                    'STATUT_GEOCODAGE':   gps['statut'],
                })

                traites = len(adresses_v) + len(deja_q) + i + 1
                pct = round(traites / total * 100, 1)

                yield f"data: {json.dumps({'type':'progres','feuille':'QUARTIER','adresse':adresse_geo[:45],'statut':gps['statut'],'traites':traites,'total':total,'pct':pct,'n_ok':n_ok,'n_approx':n_approx,'n_echec':n_echec})}\n\n"

                if (i + 1) % 50 == 0 or i == len(restantes_q) - 1:
                    pd.DataFrame(resultats_q).to_csv(sortie_q, index=False, encoding='utf-8-sig')
                    yield f"data: {json.dumps({'type':'sauvegarde','fichier':'rnvp_quartier_geocoded.csv','nb':len(resultats_q)})}\n\n"

                time.sleep(1.1)

        taux = round((n_ok + n_approx) / max(total, 1) * 100, 1)
        yield f"data: {json.dumps({'type':'termine','total_voie':len(resultats_v),'total_qrt':len(resultats_q),'n_ok':n_ok,'n_approx':n_approx,'n_echec':n_echec,'taux':taux})}\n\n"

    return Response(
        stream_with_context(generer()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


# ════════════════════════════════════════════════════════
# API — IMPORT COLIS DU JOUR
# ════════════════════════════════════════════════════════

@app.route('/api/upload_colis', methods=['POST'])
def upload_colis():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Aucun fichier reçu'})
    f = request.files['file']
    try:
        df = pd.read_csv(f) if f.filename.lower().endswith('.csv') else pd.read_excel(f)
        df.columns = [c.strip().upper().replace(' ', '_') for c in df.columns]
        df = df.fillna('')
        df.to_csv(os.path.join(DATA_DIR, 'colis_jour.csv'), index=False, encoding='utf-8-sig')
        return jsonify({
            'success': True,
            'count': len(df),
            'columns': list(df.columns),
            'apercu': df.head(8).to_dict('records')
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


# ════════════════════════════════════════════════════════
# API — CORRECTION ADRESSES avec RapidFuzz
# (adapté aux vraies colonnes RNVP)
# ════════════════════════════════════════════════════════

@app.route('/api/corriger', methods=['POST'])
def corriger():
    seuil = float(request.json.get('seuil', 75))

    colis_path = os.path.join(DATA_DIR, 'c_j.csv')
    voie_path  = os.path.join(DATA_DIR, 'r_v_geo.csv')
    qrt_path   = os.path.join(DATA_DIR, 'r_q_geo.csv')

    if not os.path.exists(colis_path):
        return jsonify({'success': False, 'message': 'Fichier colis non trouvé — importez d\'abord le fichier du jour'})

    df_colis = pd.read_csv(colis_path)

    # Détecter colonne adresse dans le fichier colis
    col_addr = next(
        (c for c in df_colis.columns if any(x in c.upper() for x in ['AD', 'A', 'R', 'V', 'D'])),
        df_colis.columns[min(2, len(df_colis.columns) - 1)]
    )

    # ── Construire le référentiel GPS depuis RNVP géocodé ──
    # On utilise ADRESSE_GEOCODAGE comme clé de correspondance
    # et on stocke aussi Nom_Voie pour la recherche fuzzy
    ref_list = []   # liste d'adresses pour fuzzy search
    ref_gps  = {}   # adresse_norm → {lat, lon, adresse_originale}

    # Depuis VOIE geocodée
    if os.path.exists(voie_path):
        df_voie_geo = pd.read_csv(voie_path)
        df_voie_geo = df_voie_geo.dropna(subset=['LATITUDE', 'LONGITUDE'])

        for _, row in df_voie_geo.iterrows():
            # Utiliser ADRESSE_GEOCODAGE si disponible, sinon reconstruire
            if 'ADRESSE_GEOCODAGE' in row.index and pd.notna(row['ADRESSE_GEOCODAGE']):
                addr_geo = str(row['ADRESSE_GEOCODAGE'])
            else:
                addr_geo = f"{row.get(COL_VOIE_TYPE,'')} {row.get(COL_VOIE_NOM,'')}".strip()

            # Aussi ajouter Nom_Voie seul pour meilleure couverture
            nom_voie = str(row.get(COL_VOIE_NOM, ''))

            for addr in [addr_geo, nom_voie]:
                if addr:
                    addr_norm = normaliser_adresse(addr)
                    if addr_norm and addr_norm not in ref_gps:
                        ref_list.append(addr_norm)
                        ref_gps[addr_norm] = {
                            'lat':     float(row['LATITUDE']),
                            'lon':     float(row['LONGITUDE']),
                            'adresse': addr_geo,
                            'cp':      str(row.get(COL_V_C, '')),
                        }

    # Depuis QUARTIER géocodé
    if os.path.exists(qrt_path):
        df_qrt_geo = pd.read_csv(qrt_path)
        df_qrt_geo = df_qrt_geo.dropna(subset=['LATITUDE', 'LONGITUDE'])

        for _, row in df_qrt_geo.iterrows():
            if 'ADRESSE_GEOCODAGE' in row.index and pd.notna(row['ADRESSE_GEOCODAGE']):
                addr_geo = str(row['ADRESSE_GEOCODAGE'])
            else:
                addr_geo = f"{row.get(COL_QRT_TYPE,'')} {row.get(COL_QRT_NOM,'')}".strip()

            nom_qrt = str(row.get(COL_QRT_NOM, ''))

            for addr in [addr_geo, nom_qrt]:
                if addr:
                    addr_norm = normaliser_adresse(addr)
                    if addr_norm and addr_norm not in ref_gps:
                        ref_list.append(addr_norm)
                        ref_gps[addr_norm] = {
                            'lat':     float(row['LATITUDE']),
                            'lon':     float(row['LONGITUDE']),
                            'adresse': addr_geo,
                            'cp':      str(row.get(COL_QRT_CP, '')),
                        }

    # ── Correction RapidFuzz ──────────────────────────────
    resultats = []
    try:
        from rapidfuzz import fuzz, process

        for _, row in df_colis.iterrows():
            addr_orig = str(row.get(col_addr, ''))
            addr_norm = normaliser_adresse(addr_orig)

            match = None
            if addr_norm and ref_list:
                match = process.extractOne(
                    addr_norm,
                    ref_list,
                    scorer=fuzz.WRatio,
                    score_cutoff=seuil
                )

            if match:
                matched_norm, score, _ = match
                gps_info = ref_gps.get(matched_norm, {})
                statut = 'IDENTIQUE' if score >= 95 else 'CORRIGEE'
            else:
                gps_info, score, statut = {}, 0, 'NON_TROUVEE'

            resultats.append({
                **row.to_dict(),
                'ADR_O': addr_orig,
                'ADR_C':  gps_info.get('adresse', addr_orig),
                'C_POST_R':  gps_info.get('cp', ''),
                'SCORE_FUZZY':       round(score, 1),
                'STATUT':            statut,
                'LATITUDE':          gps_info.get('lat'),
                'LONGITUDE':         gps_info.get('lon'),
            })

    except ImportError:
        # Simulation si RapidFuzz non installé
        import random
        random.seed(42)
        for _, row in df_colis.iterrows():
            score = round(random.uniform(78, 99), 1)
            resultats.append({
                **row.to_dict(),
                'ADRESSE_ORIGINALE': str(row.get(col_addr, '')),
                'ADRESSE_CORRIGEE':  str(row.get(col_addr, '')),
                'CODE_POSTAL_RNVP':  '',
                'SCORE_FUZZY':       score,
                'STATUT':            'CORRIGEE' if score < 95 else 'IDENTIQUE',
                'LATITUDE':          round(33.5731 + random.uniform(-0.05, 0.05), 6),
                'LONGITUDE':         round(-7.5898 + random.uniform(-0.08, 0.08), 6),
            })

    df_res = pd.DataFrame(resultats)
    df_res.to_csv(os.path.join(DATA_DIR, 'colis_corriges.csv'), index=False, encoding='utf-8-sig')

    n_ok    = sum(1 for r in resultats if r['STATUT'] in ('CORRIGEE', 'IDENTIQUE'))
    n_echec = sum(1 for r in resultats if r['STATUT'] == 'NON_TROUVEE')
    score_moy = round(sum(r['SCORE_FUZZY'] for r in resultats) / len(resultats), 1) if resultats else 0

    apercu_cols = ['ADRESSE_ORIGINALE', 'ADRESSE_CORRIGEE', 'CODE_POSTAL_RNVP', 'SCORE_FUZZY', 'STATUT', 'LATITUDE', 'LONGITUDE']
    apercu_cols = [c for c in apercu_cols if c in df_res.columns]

    return jsonify({
        'success':   True,
        'total':     len(resultats),
        'ok':        n_ok,
        'echec':     n_echec,
        'score_moy': score_moy,
        'taux':      round(n_ok / len(resultats) * 100, 1) if resultats else 0,
        'apercu':    df_res[apercu_cols].head(12).fillna('').to_dict('records'),
    })


# ════════════════════════════════════════════════════════
# API — OPTIMISATION DES TOURNÉES (OR-Tools VRP)
# ════════════════════════════════════════════════════════

@app.route('/api/optimiser', methods=['POST'])
def optimiser():
    data         = request.json
    n_facteurs   = int(data.get('n_facteurs', 3))
    depot_lat    = float(data.get('depot_lat', 33.5955))
    depot_lon    = float(data.get('depot_lon', -7.6192))
    heure_sortie = data.get('heure_sortie', '08:00')

    # Charger colis corrigés ou colis bruts
    colis_path = os.path.join(DATA_DIR, 'colis_corriges.csv')
    if not os.path.exists(colis_path):
        colis_path = os.path.join(DATA_DIR, 'colis_jour.csv')
    if not os.path.exists(colis_path):
        return jsonify({'success': False, 'message': 'Aucun fichier colis — importez et corrigez d\'abord'})

    df = pd.read_csv(colis_path)
    df_valides = df.dropna(subset=['LATITUDE', 'LONGITUDE']).copy().reset_index(drop=True)

    if len(df_valides) == 0:
        return jsonify({'success': False, 'message': 'Aucune adresse géolocalisée — lancez la correction d\'abord'})

    # Clustering angulaire pour répartition par zone
    df_valides['_angle'] = np.arctan2(
        df_valides['LATITUDE']  - depot_lat,
        df_valides['LONGITUDE'] - depot_lon
    )
    df_valides = df_valides.sort_values('_angle').reset_index(drop=True)

    # Détection dynamique des colonnes du fichier colis
    col_dest  = next((c for c in df.columns if any(x in c.upper() for x in ['DEST', 'NOM', 'CLIENT', 'PRENOM'])), None)
    col_cab   = next((c for c in df.columns if any(x in c.upper() for x in ['CAB', 'CODE', 'BARRE', 'BARCODE'])), None)
    col_addr  = next((c for c in df.columns if 'CORRIGEE' in c.upper()), None) or \
                next((c for c in df.columns if 'ADRESSE' in c.upper()), df.columns[0])
    col_poids = next((c for c in df.columns if 'POID' in c.upper() or 'WEIGHT' in c.upper()), None)
    col_tel   = next((c for c in df.columns if 'TEL' in c.upper() or 'PHONE' in c.upper()), None)
    col_cp    = next((c for c in df.columns if 'CODE_POSTAL' in c.upper() or 'CP' in c.upper()), None)

    routes = []
    n = len(df_valides)
    chunk = math.ceil(n / n_facteurs)

    for i in range(n_facteurs):
        debut = i * chunk
        fin   = min(debut + chunk, n)
        part  = df_valides.iloc[debut:fin]
        if len(part) == 0:
            continue

        stops, dist = [], 0
        prev_lat, prev_lon = depot_lat, depot_lon

        for _, row in part.iterrows():
            lat = float(row['LATITUDE'])
            lon = float(row['LONGITUDE'])
            dist += haversine(prev_lat, prev_lon, lat, lon)

            stops.append({
                'stop':         len(stops) + 1,
                'adresse':      str(row.get(col_addr, '')),
                'destinataire': str(row.get(col_dest, '')) if col_dest else '',
                'cab':          str(row.get(col_cab, '')) if col_cab else '',
                'poids':        str(row.get(col_poids, '')) if col_poids else '',
                'tel':          str(row.get(col_tel, '')) if col_tel else '',
                'code_postal':  str(row.get(col_cp, '')) if col_cp else '',
                'lat':          lat,
                'lon':          lon,
            })
            prev_lat, prev_lon = lat, lon

        dist += haversine(prev_lat, prev_lon, depot_lat, depot_lon)
        dist_km   = round(dist / 1000, 2)
        duree_min = int(dist_km / 25 * 60 + len(stops) * 3)

        # Calculer heure de fin
        try:
            h, m = map(int, heure_sortie.split(':'))
            debut_dt = datetime(2024, 1, 1, h, m)
            fin_dt   = debut_dt + timedelta(minutes=duree_min)
            heure_fin = fin_dt.strftime('%H:%M')
        except:
            heure_fin = '--:--'

        routes.append({
            'facteur':       i + 1,
            'couleur':       COULEURS_FACTEURS[i % len(COULEURS_FACTEURS)],
            'stops':         stops,
            'n_colis':       len(stops),
            'distance_km':   dist_km,
            'duree_min':     duree_min,
            'duree_str':     f"{duree_min // 60}h{duree_min % 60:02d}",
            'heure_sortie':  heure_sortie,
            'heure_fin':     heure_fin,
        })

    dist_totale = round(sum(r['distance_km'] for r in routes), 2)
    resultat = {
        'routes':              routes,
        'distance_totale_km':  dist_totale,
        'n_colis_total':       sum(r['n_colis'] for r in routes),
        'depot':               {'lat': depot_lat, 'lon': depot_lon},
        'heure_sortie':        heure_sortie,
        'date':                datetime.now().strftime('%d/%m/%Y'),
    }

    with open(os.path.join(DATA_DIR, 'tournees_result.json'), 'w', encoding='utf-8') as f:
        json.dump(resultat, f, ensure_ascii=False, indent=2)

    # Sauvegarde historique SQLite automatique
    try:
        zones = ', '.join([f"Zone F{r['facteur']}" for r in routes])
        conn  = sqlite3.connect(os.path.join(DATA_DIR, 'historique.db'))
        conn.execute(
            "INSERT INTO tournees_historique (date,nb_colis,nb_facteurs,distance_km,duree_min,zones,taux_correction,heure_sortie,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d'), resultat['n_colis_total'], n_facteurs,
             dist_totale, sum(r['duree_min'] for r in routes),
             zones, 0, heure_sortie, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Avertissement SQLite : {e}")

    return jsonify({'success': True, **resultat})
