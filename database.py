import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import os

# Chemin de la base de données
db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kadio_gestion.db")

def get_db_connection():
    """Retourne une connexion SQLite avec timeout pour éviter les locks"""
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialise toutes les tables de la base de données"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # ========== 1. EMPLOYÉS ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS employes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            telephone TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL, -- coiffeur, locticien, barbier, manucure, esthéticien, tisserand
            specialite TEXT, -- locks, tresses, barbier, etc.
            echelon TEXT NOT NULL DEFAULT 'bronze', -- bronze, argent, or, platine
            salaire_horaire REAL NOT NULL DEFAULT 0,
            date_embauche TEXT NOT NULL,
            statut TEXT NOT NULL DEFAULT 'actif', -- actif, conge, suspendu, demission
            photo_url TEXT,
            square_id TEXT, -- ID Square du team member
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # ========== 2. ÉCHELONS (Grille salariale) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS echelons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL, -- bronze, argent, or, platine
            nom_affichage TEXT, -- 🥉 Bronze, 🥈 Argent, 🥇 Or, 💎 Platine
            description TEXT,
            salaire_min REAL NOT NULL,
            salaire_max REAL NOT NULL,
            duree_min_mois INTEGER DEFAULT 0, -- durée minimale pour cet échelon (en mois)
            conditions TEXT -- conditions pour monter (ex: "note client >= 4.5, 6 mois ancienneté")
        )
    ''')

    # ========== 3. POINTAGES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS pointages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            date_journee TEXT NOT NULL,
            heure_arrivee TEXT,
            heure_depart TEXT,
            retard_minutes INTEGER DEFAULT 0,
            duree_travail_minutes INTEGER DEFAULT 0,
            duree_pause_minutes INTEGER DEFAULT 0,
            heure_pause_debut TEXT,
            heure_pause_fin TEXT,
            code_utilise TEXT, -- code à 4 chiffres
            raison_retard TEXT, -- raison si retard (obligatoire si retard > 0)
            latitude REAL,
            longitude REAL,
            adresse_pointage TEXT,
            statut TEXT NOT NULL DEFAULT 'incomplet', -- complet, incomplet, retard
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 4. CODES DE POINTAGE ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS codes_pointage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            code TEXT NOT NULL, -- 4 chiffres
            actif INTEGER NOT NULL DEFAULT 1, -- 1 = actif, 0 = inactif
            date_creation TEXT NOT NULL,
            date_expiration TEXT,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 4b. PINS EMPLOYÉS (pour login permanent) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS pins_employes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL UNIQUE,
            pin TEXT NOT NULL, -- 4 chiffres
            date_creation TEXT NOT NULL,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 5. TÂCHES MÉNAGÈRES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS taches_menageres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            description TEXT,
            zone TEXT NOT NULL, -- salon, salle_attente, toilettes, cuisine, stockage, exterieur
            duree_estimee_minutes INTEGER DEFAULT 0,
            frequence TEXT NOT NULL, -- quotidien, hebdomadaire, mensuel
            echelon_requis TEXT, -- novice, intermediaire, etc. (NULL = tous)
            poids REAL DEFAULT 1.0, -- pondération pour le score (1-3)
            actif INTEGER NOT NULL DEFAULT 1
        )
    ''')

    # ========== 6. HISTORIQUE DES TÂCHES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS historique_taches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            tache_id INTEGER NOT NULL,
            date_realisation TEXT NOT NULL,
            heure_debut TEXT,
            heure_fin TEXT,
            duree_minutes INTEGER,
            note REAL CHECK(note >= 0 AND note <= 10), -- note 0-10
            commentaire TEXT,
            photo_url TEXT,
            verifie_par INTEGER, -- ID de l'employé qui a vérifié
            FOREIGN KEY (employe_id) REFERENCES employes(id),
            FOREIGN KEY (tache_id) REFERENCES taches_menageres(id)
        )
    ''')

    # ========== 7. NOTES CLIENTS ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS notes_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT, -- ID Square du client (optionnel)
            client_nom TEXT,
            employe_id INTEGER NOT NULL,
            rendez_vous_id TEXT, -- ID Square du RDV
            date_rdv TEXT,
            service TEXT,
            accueil REAL CHECK(accueil >= 1 AND accueil <= 5), -- critère 1
            qualite REAL CHECK(qualite >= 1 AND qualite <= 5), -- critère 2
            proprete REAL CHECK(proprete >= 1 AND proprete <= 5), -- critère 3
            ambiance REAL CHECK(ambiance >= 1 AND ambiance <= 5), -- critère 4
            note_moyenne REAL, -- calculée automatiquement
            commentaire TEXT,
            recompense TEXT, -- 4-5 étoiles = badge employé
            photo_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 8. SANCTIONS ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            type TEXT NOT NULL, -- avertissement, blame, suspension, retrait_bonus
            raison TEXT NOT NULL,
            details TEXT,
            date_sanction TEXT NOT NULL,
            duree_suspension_jours INTEGER DEFAULT 0, -- pour suspension
            nombre_tard REAL DEFAULT 0, -- nombre de retards associés
            statut TEXT NOT NULL DEFAULT 'actif', -- actif, levee, archive
            levee_par TEXT, -- qui a levé la sanction
            levee_date TEXT,
            levee_raison TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 9. ALERTES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS alertes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL, -- qui a signalé
            type TEXT NOT NULL, -- incivilite, litige, insatisfaction, accident, manque, autre
            description TEXT NOT NULL,
            niveau TEXT NOT NULL DEFAULT 'moyen', -- faible, moyen, critique
            date_alerte TEXT NOT NULL,
            statut TEXT NOT NULL DEFAULT 'nouveau', -- nouveau, en_cours, resolu, archive
            resolu_par INTEGER, -- ID de l'employé/propriétaire qui a résolu
            resolu_date TEXT,
            resolution TEXT, -- comment ça a été résolu
            photo_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 10. EMPLOYÉS DU MOIS ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS employes_mois (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            mois TEXT NOT NULL, -- YYYY-MM
            annee INTEGER NOT NULL,
            score_total REAL NOT NULL,
            notes_clients_moyenne REAL,
            taches_completes INTEGER DEFAULT 0,
            retard_total_minutes INTEGER DEFAULT 0,
            badges_recus INTEGER DEFAULT 0,
            bonus_montant REAL DEFAULT 0,
            raison TEXT, -- pourquoi il a gagné
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 11. BADGES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS badges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL, -- ex: "5 Étoiles", "Ponctuel", "Expert"
            description TEXT,
            icone TEXT, -- emoji ou URL
            categorie TEXT NOT NULL, -- performance, ponctualite, service, technique
            condition_type TEXT NOT NULL, -- notes, ponctualite, taches, anciennete
            condition_valeur REAL NOT NULL, -- seuil à atteindre
            condition_periode TEXT, -- jour, semaine, mois, all
            points_bonus INTEGER DEFAULT 0, -- points ajoutés au score
            recompense_montant REAL DEFAULT 0, -- bonus en $
            actif INTEGER NOT NULL DEFAULT 1
        )
    ''')

    # ========== 12. BADGES EMPLOYES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS badges_employes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            badge_id INTEGER NOT NULL,
            date_attribution TEXT NOT NULL,
            raison TEXT, -- pourquoi ce badge a été attribué
            vu_par_employe INTEGER DEFAULT 0, -- 1 = l'employé a vu
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id),
            FOREIGN KEY (badge_id) REFERENCES badges(id),
            UNIQUE(employe_id, badge_id) -- un badge unique par employé
        )
    ''')

    # ========== 13. RÉCOMPENSES / PRIMES ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS recompenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            type TEXT NOT NULL, -- bonus, prime, avantage
            montant REAL,
            description TEXT,
            raison TEXT, -- pourquoi cette récompense
            date_attribution TEXT NOT NULL,
            date_expiration TEXT, -- pour les avantages temporaires
            statut TEXT DEFAULT 'actif', -- actif, utilise, expire
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 14. NOTES EMPLOYÉ → CLIENT (Notation bidirectionnelle) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS notes_employes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            client_nom TEXT NOT NULL,
            client_telephone TEXT,
            date_rdv TEXT,
            service TEXT,
            ponctualite REAL CHECK(ponctualite >= 0 AND ponctualite <= 5),
            comportement REAL CHECK(comportement >= 0 AND comportement <= 5),
            respect REAL CHECK(respect >= 0 AND comportement <= 5),
            commentaire TEXT,
            visible_par_employe INTEGER DEFAULT 0, -- 0 = uniquement proprio
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 15. GOOGLE AVIS (Envoi automatique) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS google_avis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_telephone TEXT NOT NULL,
            client_nom TEXT,
            employe_id INTEGER NOT NULL,
            note_client REAL, -- note que le client a donné
            sms_envoye INTEGER DEFAULT 0, -- 1 = SMS avis Google envoyé
            date_sms_envoye TEXT,
            lien_google TEXT, -- lien vers Google Avis
            statut TEXT DEFAULT 'en_attente', -- en_attente, envoye, repondu, ignore
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 16. VIDÉOS VISIONNÉES (Onboarding) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS videos_visionnees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            video_type TEXT NOT NULL DEFAULT 'reglement', -- reglement, service, hygiene
            visionnee INTEGER DEFAULT 0, -- 1 = a visionné
            date_visionnage TEXT,
            duree_visionnee_secondes INTEGER DEFAULT 0,
            complet INTEGER DEFAULT 0, -- 1 = visionnage complet
            FOREIGN KEY (employe_id) REFERENCES employes(id),
            UNIQUE(employe_id, video_type)
        )
    ''')

    # ========== 17. CHECKLIST SERVICE CLIENT ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS checklist_service (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            date_service TEXT NOT NULL,
            client_nom TEXT,
            service TEXT,
            -- Les 6 points de la checklist
            sourire INTEGER DEFAULT 0, -- 1 = oui
            guider INTEGER DEFAULT 0,
            offrir_boisson INTEGER DEFAULT 0,
            offrir_grignotine INTEGER DEFAULT 0,
            gerer_attente INTEGER DEFAULT 0,
            telephone_ranger INTEGER DEFAULT 0,
            -- Score
            score_checklist REAL DEFAULT 0, -- sur 10 (6 points + ~4 bonus)
            commentaire TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== INDEXES POUR PERFORMANCE ==========
    c.execute('CREATE INDEX IF NOT EXISTS idx_pointages_employe_date ON pointages(employe_id, date_journee)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_taches_employe ON historique_taches(employe_id, date_realisation)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_notes_employe ON notes_clients(employe_id, date_rdv)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_notes_employes_client ON notes_employes(employe_id, client_telephone)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_google_avis_client ON google_avis(client_telephone)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sanctions_employe ON sanctions(employe_id, date_sanction)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_alertes_statut ON alertes(statut, date_alerte)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_employes_mois ON employes_mois(annee, mois)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_videos_employe ON videos_visionnees(employe_id, video_type)')

    conn.commit()
    conn.close()
    print("✅ Base de données initialisée avec succès!")


def seed_echelons():
    """Insère les échelons par défaut"""
    conn = get_db_connection()
    c = conn.cursor()

    echelons = [
        ("bronze", "🥉 Bronze", "Nouveau (0-3 mois)", 0, 18, 0, "Premiers pas"),
        ("argent", "🥈 Argent", "Intermédiaire (3-6 mois)", 18, 22, 3, "Note client >= 4.0, 3 mois ancienneté"),
        ("or", "🥇 Or", "Confirmé (6-12 mois)", 22, 26, 6, "Note client >= 4.5, 6 mois ancienneté, aucune sanction grave"),
        ("platine", "💎 Platine", "Expert (12+ mois)", 26, 30, 12, "Note client >= 4.8, 12 mois ancienneté, 2x employé du mois")
    ]

    c.executemany('''
        INSERT OR IGNORE INTO echelons (nom, nom_affichage, description, salaire_min, salaire_max, duree_min_mois, conditions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', echelons)

    conn.commit()
    conn.close()
    print("✅ Échelons créés : Bronze, Argent, Or, Platine")


def seed_taches():
    """Insère les tâches ménagères par défaut"""
    conn = get_db_connection()
    c = conn.cursor()

    taches = [
        # Salon
        ("Balayer le salon", "Balayage complet du sol", "salon", 10, "quotidien", None, 1.0),
        ("Nettoyer les chaises", "Désinfection des sièges clients", "salon", 5, "quotidien", None, 1.0),
        ("Vider les poubelles", "Vider toutes les poubelles du salon", "salon", 5, "quotidien", None, 1.0),
        ("Nettoyer les miroirs", "Nettoyage des miroirs et vitres", "salon", 10, "quotidien", None, 1.0),
        ("Désinfecter les outils", "Nettoyage des ciseaux, peignes, etc.", "salon", 15, "quotidien", "novice", 2.0),
        ("Nettoyer les bacs", "Nettoyage des bacs de lavage", "salon", 10, "quotidien", "intermediaire", 1.5),
        ("Organiser le stock produits", "Rangement et inventaire des produits", "salon", 20, "hebdomadaire", "intermediaire", 2.0),
        ("Nettoyage profond des machines", "Démontage et nettoyage des outils électriques", "salon", 30, "hebdomadaire", "expert", 2.5),
        ("Dégraisser les surfaces", "Nettoyage des surfaces de travail", "salon", 15, "hebdomadaire", "intermediaire", 1.5),

        # Salle d'attente
        ("Balayer la salle d'attente", "Balayage du sol et des tapis", "salle_attente", 10, "quotidien", None, 1.0),
        ("Nettoyer les tables et canapés", "Dépoussiérage et nettoyage", "salle_attente", 5, "quotidien", None, 1.0),
        ("Ranger les magazines", "Organisation de la lecture", "salle_attente", 5, "quotidien", None, 1.0),
        ("Nettoyer le coin café", "Nettoyage de la machine à café et espace", "salle_attente", 10, "quotidien", "intermediaire", 1.5),

        # Toilettes
        ("Nettoyer les toilettes", "Nettoyage complet des sanitaires", "toilettes", 15, "quotidien", "novice", 2.0),
        ("Remplir le papier/savon", "Réapprovisionnement des consommables", "toilettes", 5, "quotidien", None, 1.0),
        ("Désinfecter les poignées", "Désinfection des surfaces de contact", "toilettes", 5, "quotidien", None, 1.0),

        # Cuisine / Backroom
        ("Nettoyer la cuisine", "Nettoyage des surfaces et évier", "cuisine", 10, "quotidien", "intermediaire", 1.5),
        ("Vider le frigo", "Vérification et nettoyage du frigo", "cuisine", 15, "hebdomadaire", "intermediaire", 1.5),
        ("Faire la vaisselle", "Vaisselle du staff", "cuisine", 10, "quotidien", "novice", 1.0),

        # Stockage
        ("Organiser le stockage", "Rangement et inventaire du stock", "stockage", 20, "hebdomadaire", "confirme", 2.0),
        ("Vérifier les dates de péremption", "Contrôle des produits", "stockage", 10, "hebdomadaire", "intermediaire", 1.5),
        ("Commander les produits manquants", "Passer commande des consommables", "stockage", 15, "hebdomadaire", "expert", 2.5),

        # Extérieur
        ("Balayer l'entrée", "Balayage du trottoir et entrée", "exterieur", 10, "quotidien", None, 1.0),
        ("Nettoyer les vitrines", "Nettoyage des vitrines extérieures", "exterieur", 15, "hebdomadaire", "intermediaire", 1.5),
        ("Vider les cendriers", "Nettoyage des cendriers extérieurs", "exterieur", 5, "quotidien", None, 1.0),
    ]

    c.executemany('''
        INSERT OR IGNORE INTO taches_menageres (nom, description, zone, duree_estimee_minutes, frequence, echelon_requis, poids)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', taches)

    # ========== 12. PARRAINAGES CLIENTS ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS parrainages_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parrain_telephone TEXT NOT NULL,
            filleul_telephone TEXT NOT NULL UNIQUE,
            code_parrainage TEXT NOT NULL UNIQUE,
            date_creation TEXT DEFAULT CURRENT_TIMESTAMP,
            statut TEXT DEFAULT 'actif'
        )
    ''')

    # ========== 13. DÉPENSES CLIENTS (pour parrainage) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS depenses_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_telephone TEXT NOT NULL,
            montant REAL NOT NULL,
            service TEXT,
            employe_id INTEGER,
            source TEXT DEFAULT 'square',
            date_depense TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    # ========== 14. RÉCOMPENSES PARRAINAGE ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS recompenses_parrainage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parrain_telephone TEXT NOT NULL,
            filleul_telephone TEXT NOT NULL,
            palier_numero INTEGER NOT NULL,
            montant_gagne REAL NOT NULL,
            statut TEXT DEFAULT 'en_attente',
            date_calcul TEXT DEFAULT CURRENT_TIMESTAMP,
            date_paiement TEXT
        )
    ''')

    # ========== 15. PARRAINAGES EMPLOYÉS (commissions) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS parrainages_employes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employe_id INTEGER NOT NULL,
            filleul_nom TEXT NOT NULL,
            filleul_telephone TEXT NOT NULL,
            type_abonnement TEXT,
            montant_recompense REAL NOT NULL DEFAULT 15,
            statut TEXT DEFAULT 'en_attente',
            paye_a TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employe_id) REFERENCES employes(id)
        )
    ''')

    conn.commit()
    conn.close()


def seed_paliers_parrainage():
    """Crée les paliers de récompense parrainage client"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS paliers_parrainage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            palier_min REAL NOT NULL,
            palier_max REAL NOT NULL,
            montant_recompense REAL NOT NULL,
            description TEXT
        )
    ''')
    
    paliers = [
        (0, 100, 10, "0-100$ dépensés"),
        (100, 200, 20, "100-200$ dépensés"),
        (200, 300, 30, "200-300$ dépensés"),
        (300, 400, 40, "300-400$ dépensés"),
        (400, 500, 50, "400-500$ dépensés"),
        (500, 600, 60, "500-600$ dépensés"),
        (600, 700, 70, "600-700$ dépensés"),
        (700, 800, 80, "700-800$ dépensés"),
        (800, 900, 90, "800-900$ dépensés"),
        (900, 1000, 100, "900-1000$ dépensés"),
        (1000, 1100, 110, "1000-1100$ dépensés"),
        (1100, 1200, 120, "1100-1200$ dépensés"),
        (1200, 1300, 130, "1200-1300$ dépensés"),
        (1300, 1400, 140, "1300-1400$ dépensés"),
        (1400, 1500, 150, "1400-1500$ dépensés"),
        (1500, 1600, 160, "1500-1600$ dépensés"),
        (1600, 1700, 170, "1600-1700$ dépensés"),
        (1700, 1800, 180, "1700-1800$ dépensés"),
        (1800, 1900, 190, "1800-1900$ dépensés"),
        (1900, 2000, 200, "1900-2000$ dépensés"),
    ]
    
    c.executemany('''
        INSERT OR IGNORE INTO paliers_parrainage (palier_min, palier_max, montant_recompense, description)
        VALUES (?, ?, ?, ?)
    ''', paliers)
    
    conn.commit()
    conn.close()
    print("✅ Paliers parrainage créés")


def seed_parametres():
    """Initialise les paramètres du salon"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS parametres_salon (
            id INTEGER PRIMARY KEY,
            date_debut_probation TEXT NOT NULL,
            date_fin_probation TEXT NOT NULL,
            nom_salon TEXT NOT NULL,
            adresse TEXT,
            telephone_salon TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # ========== HISTORIQUE DES PAIEMENTS (pour les reçus) ==========
    c.execute('''
        CREATE TABLE IF NOT EXISTS historique_paiements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_email TEXT,
            client_phone TEXT,
            montant REAL NOT NULL,
            devise TEXT DEFAULT 'CAD',
            type TEXT NOT NULL, -- subscription, deposit, one_time
            reference TEXT, -- ID Stripe
            service TEXT, -- nom du service
            date_paiement TEXT NOT NULL,
            email_envoye BOOLEAN DEFAULT 0,
            sms_envoye BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        INSERT OR IGNORE INTO parametres_salon 
        (id, date_debut_probation, date_fin_probation, nom_salon, adresse, telephone_salon)
        VALUES (1, '2026-06-15', '2026-08-15', 'Kadio Coiffure', '615 Antoinette-Robidoux, Local 100, Longueuil QC', '+15149195970')
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Paramètres salon créés")


if __name__ == "__main__":
    init_db()
    seed_echelons()
    seed_taches()
    seed_paliers_parrainage()
    seed_parametres()
    print("✅ Seed terminé!")
