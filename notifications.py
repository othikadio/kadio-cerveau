"""
Module de notifications pour le salon Kadio Coiffure.
Gère les alertes WhatsApp et emails pour les employés et le patron.
"""
import os
from datetime import datetime
from typing import Dict, List, Optional
from connectors.whatsapp import WhatsAppConnector
from connectors.email import EmailConnector

class NotificationManager:
    """
    Gestionnaire de notifications multi-canal.
    WhatsApp pour les alertes urgentes, Email pour les récapitulatifs.
    """
    
    def __init__(self):
        self.whatsapp = WhatsAppConnector()
        self.email = EmailConnector()
        
        # Numéro du patron (à configurer)
        self.patron_phone = os.getenv("PATRON_PHONE", "+15149195970")
        self.patron_email = os.getenv("PATRON_EMAIL", "othi@kadio.co")
    
    # ========== ALERTES EMPLOYÉS ==========
    
    def alerte_retard(self, employe_nom: str, employe_phone: str, heure_arrivee: str, retard_minutes: int) -> Dict:
        """Alerte quand un employé est en retard"""
        message = f"⚠️ *ALERTE RETARD*\n\n" \
                  f"👤 {employe_nom}\n" \
                  f"🕐 Arrivé à: {heure_arrivee}\n" \
                  f"⏱️ Retard: {retard_minutes} minutes\n\n" \
                  f"_Kadio Coiffure - Système de gestion_"
        
        # Envoi au patron
        result_patron = self.whatsapp.send_message(self.patron_phone, message)
        
        # Envoi à l'employé
        result_employe = self.whatsapp.send_message(employe_phone, 
            f"⚠️ Tu es en retard de {retard_minutes} minutes.\n"
            f"Arrivée enregistrée: {heure_arrivee}\n\n"
            f"_Kadio Coiffure_")
        
        return {
            "type": "retard",
            "patron": result_patron,
            "employe": result_employe,
            "timestamp": datetime.now().isoformat()
        }
    
    def alerte_absence(self, employe_nom: str, employe_phone: str) -> Dict:
        """Alerte quand un employé n'est pas pointé à l'heure"""
        message = f"🚨 *ABSENCE NON SIGNALÉE*\n\n" \
                  f"👤 {employe_nom}\n" \
                  f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n" \
                  f"⚠️ Pas de pointage d'arrivée enregistré."
        
        result = self.whatsapp.send_message(self.patron_phone, message)
        
        return {
            "type": "absence",
            "result": result,
            "timestamp": datetime.now().isoformat()
        }
    
    def alerte_depart_sans_pointage(self, employe_nom: str) -> Dict:
        """Alerte quand un employé oublie de pointer son départ"""
        message = f"🚪 *DÉPART NON POINTÉ*\n\n" \
                  f"👤 {employe_nom}\n" \
                  f"🕐 Heure actuelle: {datetime.now().strftime('%H:%M')}\n\n" \
                  f"⚠️ L'employé n'a pas pointé son départ."
        
        result = self.whatsapp.send_message(self.patron_phone, message)
        
        return {
            "type": "depart_non_pointe",
            "result": result,
            "timestamp": datetime.now().isoformat()
        }
    
    # ========== RÉCAPITULATIFS ==========
    
    def recapitulatif_quotidien(self, stats: Dict) -> Dict:
        """Envoi le récapitulatif quotidien au patron"""
        date_str = datetime.now().strftime('%d/%m/%Y')
        
        message = f"📊 *RÉCAPITULATIF DU JOUR*\n" \
                  f"📅 {date_str}\n\n" \
                  f"👥 Employés présents: {stats.get('employes_present', 0)}\n" \
                  f"🧹 Tâches effectuées: {stats.get('taches_completees', 0)}\n" \
                  f"⭐ Note moyenne: {stats.get('note_moyenne', 0)}/5\n" \
                  f"🔔 Alertes: {stats.get('alertes', 0)}\n\n" \
                  f"🏆 Employé du jour: {stats.get('top_employe', 'N/A')}\n\n" \
                  f"_Kadio Coiffure_"
        
        # WhatsApp
        result_whatsapp = self.whatsapp.send_message(self.patron_phone, message)
        
        # Email
        email_html = f"""
        <h2>📊 Récapitulatif du jour - {date_str}</h2>
        <table border="1" cellpadding="10" style="border-collapse:collapse;">
            <tr><td>👥 Employés présents</td><td>{stats.get('employes_present', 0)}</td></tr>
            <tr><td>🧹 Tâches effectuées</td><td>{stats.get('taches_completees', 0)}</td></tr>
            <tr><td>⭐ Note moyenne</td><td>{stats.get('note_moyenne', 0)}/5</td></tr>
            <tr><td>🔔 Alertes</td><td>{stats.get('alertes', 0)}</td></tr>
            <tr><td>🏆 Employé du jour</td><td>{stats.get('top_employe', 'N/A')}</td></tr>
        </table>
        """
        
        result_email = self.email.send_email(
            to=self.patron_email,
            subject=f"[Kadio Coiffure] Récapitulatif {date_str}",
            body=email_html
        )
        
        return {
            "type": "recap_journalier",
            "whatsapp": result_whatsapp,
            "email": result_email,
            "timestamp": datetime.now().isoformat()
        }
    
    def alerte_employe_du_mois(self, employe_nom: str, employe_phone: str, score: int) -> Dict:
        """Notification pour l'employé du mois"""
        # Message au patron
        message_patron = f"🏆 *EMPLOYÉ DU MOIS*\n\n" \
                         f"👤 {employe_nom}\n" \
                         f"📊 Score: {score} points\n\n" \
                         f"🎉 Félicitations à {employe_nom} !"
        
        result_patron = self.whatsapp.send_message(self.patron_phone, message_patron)
        
        # Message à l'employé
        message_employe = f"🎉 *FÉLICITATIONS* 🎉\n\n" \
                          f"Tu es l'employé du mois !\n" \
                          f"📊 Score: {score} points\n\n" \
                          f"🏆 Bravo pour ton excellent travail !\n\n" \
                          f"_Kadio Coiffure_"
        
        result_employe = self.whatsapp.send_message(employe_phone, message_employe)
        
        return {
            "type": "employe_du_mois",
            "patron": result_patron,
            "employe": result_employe,
            "timestamp": datetime.now().isoformat()
        }
    
    # ========== ALERTES SYSTÈME ==========
    
    def alerte_systeme(self, titre: str, description: str, niveau: str = "info") -> Dict:
        """Alerte système générale"""
        emoji = {"info": "ℹ️", "warning": "⚠️", "danger": "🚨"}.get(niveau, "ℹ️")
        
        message = f"{emoji} *{titre}*\n\n" \
                  f"{description}\n\n" \
                  f"_Kadio Coiffure_"
        
        result = self.whatsapp.send_message(self.patron_phone, message)
        
        return {
            "type": "systeme",
            "niveau": niveau,
            "result": result,
            "timestamp": datetime.now().isoformat()
        }
    
    # ========== NOTIFICATIONS CLIENTS ==========
    
    def rappel_rdv_client(self, client_phone: str, client_nom: str, date: str, heure: str, service: str) -> Dict:
        """Rappel de rendez-vous au client"""
        message = f"💇 *RAPPEL RENDEZ-VOUS*\n\n" \
                  f"Bonjour {client_nom},\n\n" \
                  f"📅 Date: {date}\n" \
                  f"🕐 Heure: {heure}\n" \
                  f"✂️ Service: {service}\n\n" \
                  f"📍 Kadio Coiffure\n" \
                  f"615 Antoinette-Robidoux, Longueuil\n\n" \
                  f"À bientôt ! 💇‍♀️"
        
        result = self.whatsapp.send_message(client_phone, message)
        
        return {
            "type": "rappel_rdv",
            "result": result,
            "timestamp": datetime.now().isoformat()
        }
    
    def confirmation_rdv(self, client_phone: str, client_nom: str, date: str, heure: str, service: str, prix: float) -> Dict:
        """Confirmation de rendez-vous"""
        message = f"✅ *CONFIRMATION RENDEZ-VOUS*\n\n" \
                  f"Bonjour {client_nom},\n\n" \
                  f"Votre rendez-vous est confirmé:\n" \
                  f"📅 {date} à {heure}\n" \
                  f"✂️ {service}\n" \
                  f"💰 {prix}$\n\n" \
                  f"📍 Kadio Coiffure\n" \
                  f"📞 514 919-5970\n\n" \
                  f"Merci de votre confiance ! 💇‍♀️"
        
        result = self.whatsapp.send_message(client_phone, message)
        
        return {
            "type": "confirmation_rdv",
            "result": result,
            "timestamp": datetime.now().isoformat()
        }

# Instance globale
notification_manager = NotificationManager()
