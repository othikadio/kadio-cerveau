"""
Module d'export PDF et CSV pour Kadio Coiffure
"""
import sqlite3
import csv
import io
import os
from datetime import datetime

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
    
    class PDF(FPDF):
        def __init__(self):
            super().__init__()
            # Ajouter une police Unicode depuis le dossier local
            font_path = os.path.join(os.path.dirname(__file__), "fonts")
            self.add_font("DejaVu", "", os.path.join(font_path, "DejaVuSans.ttf"), uni=True)
            self.add_font("DejaVu", "B", os.path.join(font_path, "DejaVuSans-Bold.ttf"), uni=True)
        
        def header(self):
            # Logo ou titre
            self.set_font('DejaVu', 'B', 16)
            self.cell(0, 10, 'Kadio Coiffure - Rapport', 0, 1, 'C')
            self.set_font('DejaVu', '', 10)
            self.cell(0, 5, f'Généré le {datetime.now().strftime("%d/%m/%Y %H:%M")}', 0, 1, 'C')
            self.ln(5)
        
        def footer(self):
            self.set_y(-15)
            self.set_font('DejaVu', '', 8)
            self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')
            
except ImportError:
    FPDF_AVAILABLE = False
    print("⚠️ fpdf non disponible, exports PDF désactivés")
    
    class PDF:
        pass

from database import get_db_connection, db_path

def export_employes_csv():
    """Exporte la liste des employés en CSV"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM employes ORDER BY nom")
    rows = c.fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(['ID', 'Nom', 'Téléphone', 'Email', 'Rôle', 'Spécialité', 
                     'Échelon', 'Salaire/H', 'Date Embauche', 'Statut'])
    
    # Data
    for row in rows:
        writer.writerow([
            row['id'], row['nom'], row['telephone'], row['email'],
            row['role'], row['specialite'], row['echelon'],
            row['salaire_horaire'], row['date_embauche'], row['statut']
        ])
    
    return output.getvalue()

def export_employes_pdf():
    """Exporte la liste des employés en PDF"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM employes ORDER BY nom")
    rows = c.fetchall()
    conn.close()
    
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('DejaVu', 'B', 12)
    pdf.cell(0, 10, 'Liste des Employés', 0, 1, 'L')
    pdf.ln(5)
    
    # Table header
    pdf.set_font('DejaVu', 'B', 10)
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(40, 7, 'Nom', 1, 0, 'C', True)
    pdf.cell(30, 7, 'Rôle', 1, 0, 'C', True)
    pdf.cell(30, 7, 'Spécialité', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Échelon', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Salaire/H', 1, 0, 'C', True)
    pdf.cell(40, 7, 'Statut', 1, 1, 'C', True)
    
    # Table data
    pdf.set_font('DejaVu', '', 9)
    for row in rows:
        pdf.cell(40, 6, str(row['nom']), 1)
        pdf.cell(30, 6, str(row['role']), 1)
        pdf.cell(30, 6, str(row['specialite'] or ''), 1)
        pdf.cell(25, 6, str(row['echelon']), 1)
        pdf.cell(25, 6, f"${row['salaire_horaire']:.2f}", 1)
        pdf.cell(40, 6, str(row['statut']), 1)
        pdf.ln()
    
    return pdf.output(dest='S')

def export_pointages_csv(date_debut=None, date_fin=None):
    """Exporte les pointages en CSV"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if date_debut and date_fin:
        c.execute('''
            SELECT p.*, e.nom as employe_nom 
            FROM pointages p
            JOIN employes e ON p.employe_id = e.id
            WHERE p.date_journee BETWEEN ? AND ?
            ORDER BY p.date_journee DESC
        ''', (date_debut, date_fin))
    else:
        c.execute('''
            SELECT p.*, e.nom as employe_nom 
            FROM pointages p
            JOIN employes e ON p.employe_id = e.id
            ORDER BY p.date_journee DESC
        ''')
    
    rows = c.fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Date', 'Employé', 'Arrivée', 'Départ', 'Retard (min)', 
                     'Durée travail (min)', 'Durée pause (min)', 'Statut'])
    
    for row in rows:
        writer.writerow([
            row['date_journee'], row['employe_nom'], row['heure_arrivee'],
            row['heure_depart'], row['retard_minutes'], row['duree_travail_minutes'],
            row['duree_pause_minutes'], row['statut']
        ])
    
    return output.getvalue()

def export_pointages_pdf(date_debut=None, date_fin=None):
    """Exporte les pointages en PDF"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if date_debut and date_fin:
        c.execute('''
            SELECT p.*, e.nom as employe_nom 
            FROM pointages p
            JOIN employes e ON p.employe_id = e.id
            WHERE p.date_journee BETWEEN ? AND ?
            ORDER BY p.date_journee DESC
        ''', (date_debut, date_fin))
    else:
        c.execute('''
            SELECT p.*, e.nom as employe_nom 
            FROM pointages p
            JOIN employes e ON p.employe_id = e.id
            ORDER BY p.date_journee DESC
        ''')
    
    rows = c.fetchall()
    conn.close()
    
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('DejaVu', 'B', 12)
    pdf.cell(0, 10, 'Rapport de Pointages', 0, 1, 'L')
    if date_debut and date_fin:
        pdf.set_font('DejaVu', '', 10)
        pdf.cell(0, 5, f'Période: {date_debut} au {date_fin}', 0, 1, 'L')
    pdf.ln(5)
    
    # Table
    pdf.set_font('DejaVu', 'B', 9)
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(25, 7, 'Date', 1, 0, 'C', True)
    pdf.cell(35, 7, 'Employé', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Arrivée', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Départ', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Retard', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Durée', 1, 0, 'C', True)
    pdf.cell(30, 7, 'Statut', 1, 1, 'C', True)
    
    pdf.set_font('DejaVu', '', 8)
    for row in rows:
        pdf.cell(25, 6, str(row['date_journee']), 1)
        pdf.cell(35, 6, str(row['employe_nom']), 1)
        pdf.cell(25, 6, str(row['heure_arrivee'] or ''), 1)
        pdf.cell(25, 6, str(row['heure_depart'] or ''), 1)
        pdf.cell(25, 6, f"{row['retard_minutes']} min", 1)
        pdf.cell(25, 6, f"{row['duree_travail_minutes']} min", 1)
        pdf.cell(30, 6, str(row['statut']), 1)
        pdf.ln()
    
    return pdf.output(dest='S')

def export_notes_csv():
    """Exporte les notes clients en CSV"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT nc.*, e.nom as employe_nom 
        FROM notes_clients nc
        JOIN employes e ON nc.employe_id = e.id
        ORDER BY nc.created_at DESC
    ''')
    rows = c.fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Date', 'Client', 'Employé', 'Note', 'Commentaire'])
    
    for row in rows:
        writer.writerow([
            row['created_at'], row['client_nom'], row['employe_nom'],
            row['note'], row['commentaire']
        ])
    
    return output.getvalue()

def export_classement_pdf():
    """Exporte le classement des employés en PDF"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT e.id, e.nom, e.role, e.specialite, e.echelon,
               COALESCE(AVG(nc.note), 0) * 10 as score_notes,
               COUNT(DISTINCT ht.id) * 5 as score_taches,
               COUNT(DISTINCT p.id) as total_pointages,
               COUNT(DISTINCT nc.id) as total_notes,
               COALESCE(AVG(nc.note), 0) as note_moyenne
        FROM employes e
        LEFT JOIN pointages p ON e.id = p.employe_id
        LEFT JOIN notes_clients nc ON e.id = nc.employe_id
        LEFT JOIN historique_taches ht ON e.id = ht.employe_id
        WHERE e.statut = 'actif'
        GROUP BY e.id
        ORDER BY (score_notes + score_taches) DESC
    ''')
    rows = c.fetchall()
    conn.close()
    
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('DejaVu', 'B', 14)
    pdf.cell(0, 10, 'Classement des Employés', 0, 1, 'C')
    pdf.ln(5)
    
    # Podium
    pdf.set_font('DejaVu', 'B', 12)
    pdf.cell(0, 10, 'Top 3', 0, 1, 'L')
    pdf.set_font('DejaVu', '', 10)
    
    for i, row in enumerate(rows[:3], 1):
        score_total = (row['score_notes'] or 0) + (row['score_taches'] or 0)
        rank = {1: '1er', 2: '2e', 3: '3e'}.get(i, f'{i}.')
        pdf.cell(0, 8, f'{rank} {row["nom"]} - Score: {score_total:.1f} pts', 0, 1, 'L')
    
    pdf.ln(5)
    
    # Full table
    pdf.set_font('DejaVu', 'B', 10)
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(15, 7, 'Rang', 1, 0, 'C', True)
    pdf.cell(40, 7, 'Employé', 1, 0, 'C', True)
    pdf.cell(30, 7, 'Rôle', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Score', 1, 0, 'C', True)
    pdf.cell(25, 7, 'Notes', 1, 0, 'C', True)
    pdf.cell(55, 7, 'Note Moy.', 1, 1, 'C', True)
    
    pdf.set_font('DejaVu', '', 9)
    for i, row in enumerate(rows, 1):
        score_total = (row['score_notes'] or 0) + (row['score_taches'] or 0)
        pdf.cell(15, 6, str(i), 1)
        pdf.cell(40, 6, str(row['nom']), 1)
        pdf.cell(30, 6, str(row['role']), 1)
        pdf.cell(25, 6, f"{score_total:.1f}", 1)
        pdf.cell(25, 6, str(row['total_notes']), 1)
        pdf.cell(55, 6, f"{row['note_moyenne'] or 0:.2f}/10", 1)
        pdf.ln()
    
    return pdf.output(dest='S')
