from fastapi import FastAPI, HTTPException, Request, WebSocket, Response
from starlette.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Ajouter le répertoire courant au path pour les imports (Railway)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()  # Charge les variables du .env

# Import des connecteurs
from connectors.whatsapp import WhatsAppConnector
from connectors.square import SquareConnector
from connectors.twilio_voice import TwilioVoiceConnector
from connectors.instagram import InstagramConnector
from connectors.email import EmailConnector
from connectors.stripe import StripeConnector
from agent_kimi import KimiAgent
from onboarding import ObservationEngine, OwnerAction, OnboardingDashboard
from checklist_page import CHECKLIST_HTML
from datetime import datetime

from database import init_db, get_db_connection, seed_echelons, seed_taches
from notifications import notification_manager
from export import (
    export_employes_csv, export_employes_pdf,
    export_pointages_csv, export_pointages_pdf,
    export_notes_csv, export_classement_pdf
)

import requests

# Initialisation de la base de données au démarrage
init_db()
seed_echelons()
seed_taches()

app = FastAPI(title="Kadio Cerveau API", version="0.1")

# CORS pour le frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En prod: remplacer par domaine Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Agent Kimi
agent = KimiAgent()

# Moteur d'observation
observation = ObservationEngine()
dashboard_onboarding = OnboardingDashboard(observation)

# Connecteurs
whatsapp = WhatsAppConnector()
square = SquareConnector()
voice = TwilioVoiceConnector()
instagram = InstagramConnector()
email = EmailConnector()
stripe = StripeConnector()

# Mémoire conversationnelle (SQLite en prod)
conversations: Dict[str, List[Dict]] = {}

# Modèles de données
class Message(BaseModel):
    platform: str  # whatsapp, instagram, voice, telegram
    sender_id: str
    sender_name: Optional[str] = None
    content: str
    timestamp: Optional[str] = None
    media_url: Optional[str] = None

class AppointmentRequest(BaseModel):
    client_name: str
    phone: str
    service: str
    date: str
    time: str
    notes: Optional[str] = None

class AlertRetardRequest(BaseModel):
    employe_nom: str
    employe_phone: str
    heure_arrivee: str
    retard_minutes: int

class AlertAbsenceRequest(BaseModel):
    employe_nom: str
    employe_phone: str

class RecapQuotidienRequest(BaseModel):
    employes_present: int = 0
    taches_completees: int = 0
    note_moyenne: float = 0
    alertes: int = 0
    top_employe: str = "N/A"

class EmployeDuMoisRequest(BaseModel):
    employe_nom: str
    employe_phone: str
    score: int

class RappelRdvRequest(BaseModel):
    client_phone: str
    client_nom: str
    date: str
    heure: str
    service: str

class ConfirmationRdvRequest(BaseModel):
    client_phone: str
    client_nom: str
    date: str
    heure: str
    service: str
    prix: float

class AlerteSystemeRequest(BaseModel):
    titre: str
    description: str

class DemandeRetrait(BaseModel):
    client_telephone: str
    client_nom: Optional[str] = None
    montant: float
    motif: str = "parrainage"  # parrainage, fidelite, autre

class ValidationRetrait(BaseModel):
    statut: str  # valide, refuse, remis
    commentaire: Optional[str] = None

class NoteEmployeCreate(BaseModel):
    employe_id: int
    client_telephone: str
    client_nom: Optional[str] = None
    note: float  # 1-5
    commentaire: Optional[str] = None
    tags: Optional[str] = None  # JSON array string
    date_rdv: Optional[str] = None

class NoteEmployeAdmin(BaseModel):
    employe_id: int
    client_telephone: str
    note: float
    commentaire: Optional[str] = None
    tags: Optional[str] = None
    date_rdv: Optional[str] = None
    niveau: str = "info"

class ConnectRequest(BaseModel):
    platform: str  # facebook, whatsapp, instagram, square, google
    auth_code: Optional[str] = None
    token: Optional[str] = None

# ========== ROUTES PRINCIPALES ==========

@app.get("/")
async def root():
    return {"status": "Kadio Cerveau v0.1 - LIVE", "online": True}

@app.get("/landing")
async def landing_page():
    """Page d'accueil publique Kadio Coiffure"""
    import os
    possible_paths = [
        "backend/landing.html",
        "landing.html",
        os.path.join(os.path.dirname(__file__), "landing.html"),
        "/app/landing.html"
    ]
    
    html = None
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                html = f.read()
            break
        except FileNotFoundError:
            continue
    
    if html is None:
        raise HTTPException(status_code=404, detail="Landing page non trouvée")
    
    return Response(content=html, media_type="text/html")

@app.get("/checklist")
async def checklist_page():
    """Page checklist service client (tablette)"""
    return Response(content=CHECKLIST_HTML, media_type="text/html")

@app.get("/admin")
async def admin_page():
    """Page tableau de bord admin"""
    import os
    possible_paths = [
        "backend/admin.html",
        "admin.html",
        os.path.join(os.path.dirname(__file__), "admin.html"),
        "/app/admin.html"
    ]
    
    html = None
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                html = f.read()
            break
        except FileNotFoundError:
            continue
    
    if html is None:
        return {"error": "Page admin not found", "checked_paths": possible_paths}
    
    return Response(content=html, media_type="text/html")

@app.get("/employe")
async def employe_page():
    """Page espace employé (tablette)"""
    import os
    possible_paths = [
        "backend/employe.html",
        "employe.html", 
        os.path.join(os.path.dirname(__file__), "employe.html"),
        "/app/employe.html"
    ]
    
    html = None
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                html = f.read()
            break
        except FileNotFoundError:
            continue
    
    if html is None:
        return {"error": "Page not found", "checked_paths": possible_paths}
    
    return Response(content=html, media_type="text/html")

@app.get("/health")
async def health():
    """Vérification santé de tous les connecteurs"""
    return {
        "kimi": agent.is_configured(),
        "whatsapp": whatsapp.is_connected(),
        "square": square.is_connected(),
        "voice": voice.is_connected(),
        "stripe": stripe.is_connected(),
        "instagram": instagram.is_connected(),
        "email": email.is_connected()
    }

# ========== CONVERSATIONS ==========

@app.post("/message/incoming")
async def incoming_message(message: Message):
    """Reçoit un message de n'importe quelle plateforme"""
    # Stocke la conversation
    if message.sender_id not in conversations:
        conversations[message.sender_id] = []
    
    conversations[message.sender_id].append({
        "role": "user",
        "content": message.content,
        "platform": message.platform,
        "timestamp": datetime.now().isoformat()
    })
    
    # Traitement par l'agent IA
    response = await process_message(message)
    
    # Envoie la réponse
    await send_response(message.platform, message.sender_id, response)
    
    return {"status": "ok", "response": response}

@app.get("/conversations/{client_id}")
async def get_conversation(client_id: str):
    """Récupère l'historique d'une conversation"""
    return conversations.get(client_id, [])

# ========== RENDEZ-VOUS ==========

@app.post("/appointments")
async def create_appointment(appointment: AppointmentRequest):
    """Crée un rendez-vous via Square et envoie le lien de dépôt si requis"""
    result = await square.create_appointment({
        "client_name": appointment.client_name,
        "phone": appointment.phone,
        "service_name": appointment.service,
        "date": appointment.date,
        "time": appointment.time,
        "notes": appointment.notes
    })
    
    # Si RDV créé et dépôt requis, envoyer le lien de paiement
    if result.get("success") and result.get("deposit"):
        deposit = result["deposit"]
        if deposit.get("required") and deposit.get("amount", 0) > 0:
            try:
                stripe_result = await stripe.send_deposit_link(
                    phone=appointment.phone,
                    service_name=result["service"],
                    amount=deposit["amount"],
                    booking_id=result.get("booking_id")
                )
                # Ajouter au résultat
                result["payment_link"] = stripe_result.get("link")
                result["payment_message"] = stripe_result.get("message")
            except Exception as e:
                result["payment_error"] = str(e)
    
    return result

@app.get("/appointments")
async def list_appointments(date: Optional[str] = None):
    """Liste les rendez-vous"""
    return await square.list_appointments(date)

@app.get("/appointments/availability")
async def check_availability(date: str, service: Optional[str] = None):
    """Vérifie les créneaux disponibles"""
    return await square.check_availability(date, service)

@app.get("/appointments/stats")
async def daily_stats(date: Optional[str] = None):
    """Stats du jour"""
    return await square.get_daily_stats(date)

@app.get("/services")
async def list_services():
    """Liste les services disponibles"""
    return await square.list_services()

# ========== DISPONIBILITÉS PUBLIQUES ==========

@app.get("/disponibilites")
async def page_disponibilites():
    """Page publique pour voir les créneaux disponibles et prendre RDV"""
    html = '''<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kadio Coiffure - Prendre Rendez-vous</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; }
        .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 2rem 1rem; text-align: center; }
        .header h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
        .header p { opacity: 0.9; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .card { background: white; border-radius: 15px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .card h2 { color: #1a1a2e; margin-bottom: 15px; font-size: 1.2rem; }
        label { display: block; margin-bottom: 5px; color: #555; font-weight: 600; font-size: 0.9rem; }
        input, select { width: 100%; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 1rem; margin-bottom: 15px; transition: border-color 0.3s; }
        input:focus, select:focus { outline: none; border-color: #e94560; }
        button { background: #e94560; color: white; border: none; padding: 14px 30px; border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; width: 100%; transition: transform 0.2s; }
        button:hover { transform: scale(1.02); }
        button:disabled { background: #ccc; cursor: not-allowed; transform: none; }
        .slots { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 15px; }
        .slot { padding: 10px; text-align: center; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: all 0.2s; }
        .slot:hover { border-color: #e94560; background: #fff0f2; }
        .slot.selected { background: #e94560; color: white; border-color: #e94560; }
        .slot.disabled { opacity: 0.3; cursor: not-allowed; text-decoration: line-through; }
        .result { padding: 15px; border-radius: 8px; margin-top: 15px; }
        .result.success { background: #d4edda; color: #155724; }
        .result.error { background: #f8d7da; color: #721c24; }
        .loading { text-align: center; padding: 20px; color: #666; }
        .service-list { display: grid; gap: 10px; }
        .service-item { padding: 15px; border: 2px solid #e0e0e0; border-radius: 10px; cursor: pointer; transition: all 0.2s; }
        .service-item:hover { border-color: #e94560; }
        .service-item.selected { border-color: #e94560; background: #fff0f2; }
        .service-item h4 { color: #1a1a2e; margin-bottom: 5px; }
        .service-item p { color: #666; font-size: 0.9rem; }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🌀 Kadio Coiffure</h1>
        <p>Prenez votre rendez-vous en ligne — Vérification des disponibilités en temps réel</p>
    </div>
    
    <div class="container">
        <!-- Étape 1: Service -->
        <div class="card" id="step1">
            <h2>1. Choisissez votre service</h2>
            <div class="service-list" id="services">
                <div class="loading">Chargement des services...</div>
            </div>
        </div>
        
        <!-- Étape 2: Date -->
        <div class="card hidden" id="step2">
            <h2>2. Choisissez une date</h2>
            <label>Date</label>
            <input type="date" id="date" min="" onchange="chargerCreneaux()">
        </div>
        
        <!-- Étape 3: Créneau -->
        <div class="card hidden" id="step3">
            <h2>3. Choisissez un créneau</h2>
            <div id="slots-loading" class="loading">Chargement des disponibilités...</div>
            <div class="slots" id="slots"></div>
        </div>
        
        <!-- Étape 4: Infos client -->
        <div class="card hidden" id="step4">
            <h2>4. Vos informations</h2>
            <label>Nom complet</label>
            <input type="text" id="nom" placeholder="Votre nom">
            <label>Téléphone</label>
            <input type="tel" id="telephone" placeholder="514-000-0000">
            <label>Notes (optionnel)</label>
            <input type="text" id="notes" placeholder="Ex: Première fois, allergies...">
            <button onclick="reserver()" id="btn-reserver">Confirmer le rendez-vous</button>
            <div id="result"></div>
        </div>
    </div>
    
    <script>
        let selectedService = null;
        let selectedDate = null;
        let selectedTime = null;
        
        // Charger les services au démarrage
        async function chargerServices() {
            try {
                const res = await fetch("/services");
                const services = await res.json();
                const container = document.getElementById("services");
                container.innerHTML = services.map(s => `
                    <div class="service-item" onclick="selectService('${s.name}', this)">
                        <h4>${s.name}</h4>
                        <p>${s.duration}min — ${s.price}$ CAD</p>
                    </div>
                `).join("");
            } catch (e) {
                document.getElementById("services").innerHTML = "<div class='loading'>Erreur de chargement. Rafraîchissez la page.</div>";
            }
        }
        
        function selectService(name, element) {
            selectedService = name;
            document.querySelectorAll(".service-item").forEach(el => el.classList.remove("selected"));
            element.classList.add("selected");
            document.getElementById("step2").classList.remove("hidden");
            
            // Date min = aujourd\'hui
            const today = new Date().toISOString().split("T")[0];
            document.getElementById("date").min = today;
            document.getElementById("date").value = today;
        }
        
        async function chargerCreneaux() {
            selectedDate = document.getElementById("date").value;
            if (!selectedDate || !selectedService) return;
            
            document.getElementById("step3").classList.remove("hidden");
            document.getElementById("slots-loading").style.display = "block";
            document.getElementById("slots").innerHTML = "";
            
            try {
                const res = await fetch(`/appointments/availability?date=${selectedDate}&service=${encodeURIComponent(selectedService)}`);
                const data = await res.json();
                
                document.getElementById("slots-loading").style.display = "none";
                
                if (data.available_slots && data.available_slots.length > 0) {
                    document.getElementById("slots").innerHTML = data.available_slots.map(time => `
                        <div class="slot" onclick="selectTime('${time}', this)">${time}</div>
                    `).join("");
                } else {
                    document.getElementById("slots").innerHTML = "<p style='grid-column:1/-1;text-align:center;color:#666;'>Aucun créneau disponible cette date. Essayez une autre date.</p>";
                }
            } catch (e) {
                document.getElementById("slots-loading").style.display = "none";
                document.getElementById("slots").innerHTML = "<p style='grid-column:1/-1;text-align:center;color:#dc3545;'>Erreur de chargement.</p>";
            }
        }
        
        function selectTime(time, element) {
            selectedTime = time;
            document.querySelectorAll(".slot").forEach(el => el.classList.remove("selected"));
            element.classList.add("selected");
            document.getElementById("step4").classList.remove("hidden");
        }
        
        async function reserver() {
            const nom = document.getElementById("nom").value.trim();
            const telephone = document.getElementById("telephone").value.trim();
            const notes = document.getElementById("notes").value.trim();
            const btn = document.getElementById("btn-reserver");
            
            if (!nom || !telephone) {
                document.getElementById("result").innerHTML = "<div class='result error'>Veuillez remplir tous les champs obligatoires.</div>";
                return;
            }
            
            btn.disabled = true;
            btn.textContent = "Confirmation en cours...";
            
            try {
                const res = await fetch("/appointments", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        client_name: nom,
                        phone: telephone,
                        service: selectedService,
                        date: selectedDate,
                        time: selectedTime,
                        notes: notes
                    })
                });
                
                const data = await res.json();
                
                if (data.success) {
                    let html = `<div class="result success">
                        <h3>✅ Rendez-vous confirmé !</h3>
                        <p><strong>Service:</strong> ${data.service}</p>
                        <p><strong>Date:</strong> ${selectedDate} à ${selectedTime}</p>
                        <p><strong>Coiffeur:</strong> ${data.coiffeur}</p>
                    </div>`;
                    
                    if (data.deposit && data.deposit.required) {
                        html += `<div class="result error" style="margin-top:10px;">
                            <p>⚠️ <strong>Dépôt requis:</strong> ${data.deposit.amount}$ CAD</p>
                            <p>Un lien de paiement vous sera envoyé par WhatsApp/SMS.</p>
                        </div>`;
                    }
                    
                    document.getElementById("result").innerHTML = html;
                    btn.textContent = "Rendez-vous confirmé ✓";
                } else {
                    let msg = data.message || data.error || "Erreur inconnue";
                    document.getElementById("result").innerHTML = `<div class="result error">❌ ${msg.replace(/\\n/g, "<br>")}</div>`;
                    btn.disabled = false;
                    btn.textContent = "Réessayer";
                }
            } catch (e) {
                document.getElementById("result").innerHTML = `<div class="result error">❌ Erreur: ${e.message}</div>`;
                btn.disabled = false;
                btn.textContent = "Réessayer";
            }
        }
        
        // Charger les services au démarrage
        chargerServices();
    </script>
</body>
</html>'''
    return HTMLResponse(content=html)

# ========== ABONNEMENTS ==========

@app.post("/subscription")
async def create_subscription(request: Request):
    """Génère un lien d'abonnement Stripe pour le client"""
    data = await request.json()
    email = data.get("email")
    phone = data.get("phone")
    
    if not email:
        raise HTTPException(status_code=400, detail="Email requis")
    
    result = await stripe.create_subscription_link(customer_email=email)
    
    if result.get("success"):
        # Envoyer le lien par WhatsApp si un numéro est fourni
        if phone:
            try:
                await stripe.send_subscription_link(phone=phone, customer_email=email)
            except:
                pass
        
        return {
            "success": True,
            "link": result.get("link"),
            "amount": result.get("amount"),
            "period": result.get("period")
        }
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Erreur Stripe"))

@app.get("/subscription/info")
async def subscription_info():
    """Retourne les infos des abonnements disponibles avec taxes Québec"""
    TAX_RATE = 0.14975
    
    def calc_ttc(ht):
        return round(ht * (1 + TAX_RATE), 2)
    
    return {
        "salon": "Kadio Coiffure",
        "taxes": {"tps": "5%", "tvq": "9.975%", "total": "14.975%"},
        "forfaits": [
            {
                "id": "mensuel",
                "name": "Abonnement Mensuel",
                "price_ht": 80.00,
                "price_ttc": calc_ttc(80),
                "currency": "CAD",
                "period": "monthly",
                "includes": ["1 prestation par mois"],
                "taxes_incluses": True
            },
            {
                "id": "trimestriel",
                "name": "Abonnement Trimestriel",
                "price_ht": 220.00,
                "price_ttc": calc_ttc(220),
                "currency": "CAD",
                "period": "3_months",
                "includes": ["3 prestations + 1 gratuite"],
                "taxes_incluses": True
            },
            {
                "id": "annuel",
                "name": "Abonnement Annuel",
                "price_ht": 800.00,
                "price_ttc": calc_ttc(800),
                "currency": "CAD",
                "period": "yearly",
                "includes": ["12 prestations + 3 gratuites"],
                "taxes_incluses": True
            }
        ],
        "code_parrainage": {
            "description": "Entrez un code KADIO-XXXXXX pour 10% de réduction sur la première mensualité",
            "discount": "10%",
            "applies_to": "first_payment_only"
        }
    }

# ========== ADMIN - ABONNÉS STRIPE ==========

@app.get("/api/admin/abonnes")
async def liste_abonnes(status: str = "all"):
    """
    Liste tous les abonnés Stripe (actifs et inactifs).
    Accessible uniquement par l'admin.
    """
    result = await stripe.list_subscriptions(status=status)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.get("/api/admin/abonne/{subscription_id}")
async def detail_abonne(subscription_id: str):
    """Détail d'un abonné Stripe"""
    result = await stripe.get_subscription(subscription_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result

@app.post("/api/admin/abonne/{subscription_id}/annuler")
async def annuler_abonnement(subscription_id: str):
    """Annule un abonnement Stripe (fin de période)"""
    result = await stripe.cancel_subscription(subscription_id)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result

@app.get("/admin/abonnes")
async def page_admin_abonnes():
    """Page HTML admin pour visualiser les abonnés"""
    html = '''<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kadio Coiffure - Gestion Abonnés</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: #f5f5f5; padding: 20px; }
        .header { background: #1a1a2e; color: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
        .header h1 { font-size: 1.5rem; }
        .filters { margin-bottom: 20px; }
        .filters button { padding: 8px 16px; margin-right: 10px; border: none; border-radius: 5px; cursor: pointer; background: #ddd; }
        .filters button.active { background: #e94560; color: white; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: white; padding: 15px; border-radius: 10px; text-align: center; }
        .stat-card h3 { font-size: 2rem; color: #e94560; }
        .stat-card p { color: #666; font-size: 0.9rem; }
        table { width: 100%; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #1a1a2e; color: white; }
        tr:hover { background: #f9f9f9; }
        .status-active { color: #28a745; font-weight: bold; }
        .status-canceled { color: #dc3545; }
        .status-past_due { color: #ffc107; }
        .btn-annuler { background: #dc3545; color: white; border: none; padding: 5px 10px; border-radius: 5px; cursor: pointer; font-size: 0.8rem; }
        .btn-annuler:hover { background: #c82333; }
        .email-link { color: #007bff; text-decoration: none; }
        .loading { text-align: center; padding: 40px; color: #666; }
        .error { background: #f8d7da; color: #721c24; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🌀 Gestion des Abonnés - Kadio Coiffure</h1>
        <p>Liste des abonnements Stripe ( visible uniquement par le propriétaire )</p>
    </div>
    
    <div class="stats" id="stats">
        <div class="stat-card"><h3 id="total">-</h3><p>Total abonnés</p></div>
        <div class="stat-card"><h3 id="actifs">-</h3><p>Actifs</p></div>
        <div class="stat-card"><h3 id="inactifs">-</h3><p>Inactifs/Annulés</p></div>
    </div>
    
    <div class="filters">
        <button class="active" onclick="filtrer('all')">Tous</button>
        <button onclick="filtrer('active')">Actifs</button>
        <button onclick="filtrer('canceled')">Annulés</button>
    </div>
    
    <div id="loading" class="loading">Chargement...</div>
    <div id="error"></div>
    <div id="table-container"></div>
    
    <script>
        let allSubs = [];
        
        async function chargerAbonnes(status = "all") {
            document.getElementById("loading").style.display = "block";
            document.getElementById("error").innerHTML = "";
            document.getElementById("table-container").innerHTML = "";
            
            try {
                const res = await fetch("/api/admin/abonnes?status=" + status);
                const data = await res.json();
                
                if (data.error) throw new Error(data.error);
                
                allSubs = data.subscriptions || [];
                afficherStats(allSubs);
                afficherTableau(allSubs);
            } catch (e) {
                document.getElementById("error").innerHTML = `<div class="error">Erreur: ${e.message}</div>`;
            } finally {
                document.getElementById("loading").style.display = "none";
            }
        }
        
        function afficherStats(subs) {
            const actifs = subs.filter(s => s.status === "active").length;
            const inactifs = subs.filter(s => s.status !== "active").length;
            document.getElementById("total").textContent = subs.length;
            document.getElementById("actifs").textContent = actifs;
            document.getElementById("inactifs").textContent = inactifs;
        }
        
        function afficherTableau(subs) {
            if (subs.length === 0) {
                document.getElementById("table-container").innerHTML = "<p style='text-align:center;padding:20px;'>Aucun abonné trouvé.</p>";
                return;
            }
            
            let html = `<table>
                <thead>
                    <tr>
                        <th>Client</th>
                        <th>Email</th>
                        <th>Statut</th>
                        <th>Montant</th>
                        <th>Début</th>
                        <th>Prochain paiement</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>`;
            
            subs.forEach(sub => {
                const statusClass = sub.status === "active" ? "status-active" : 
                                   sub.status === "canceled" ? "status-canceled" : "status-past_due";
                html += `<tr>
                    <td>${sub.customer_name}</td>
                    <td><a href="mailto:${sub.customer_email}" class="email-link">${sub.customer_email}</a></td>
                    <td class="${statusClass}">${sub.status}</td>
                    <td>${sub.amount} ${sub.currency}/${sub.interval}</td>
                    <td>${sub.created || "-"}</td>
                    <td>${sub.current_period_end || "-"}</td>
                    <td>${sub.status === "active" ? `<button class="btn-annuler" onclick="annuler('${sub.id}')">Annuler</button>` : "-"}</td>
                </tr>`;
            });
            
            html += "</tbody></table>";
            document.getElementById("table-container").innerHTML = html;
        }
        
        function filtrer(status) {
            document.querySelectorAll(".filters button").forEach(b => b.classList.remove("active"));
            event.target.classList.add("active");
            chargerAbonnes(status);
        }
        
        async function annuler(subId) {
            if (!confirm("Voulez-vous vraiment annuler cet abonnement ? Il restera actif jusqu'à la fin de la période.")) return;
            
            try {
                const res = await fetch(`/api/admin/abonne/${subId}/annuler`, { method: "POST" });
                const data = await res.json();
                if (data.success) {
                    alert("Abonnement annulé avec succès.");
                    chargerAbonnes("all");
                } else {
                    alert("Erreur: " + (data.error || "Inconnue"));
                }
            } catch (e) {
                alert("Erreur: " + e.message);
            }
        }
        
        // Charger au démarrage
        chargerAbonnes("all");
    </script>
</body>
</html>'''
    return HTMLResponse(content=html)

# ========== VOIX / TÉLÉPHONE ==========

@app.post("/voice/incoming")
async def incoming_call(request: Request):
    """Webhook Twilio pour appels entrants"""
    form = await request.form()
    from_number = form.get("From")
    call_sid = form.get("CallSid")
    
    # Génère le TwiML pour la réceptionniste
    twiml = voice.handle_incoming_call(from_number, call_sid)
    
    return Response(content=twiml, media_type="application/xml")

@app.post("/voice/response")
async def voice_response(request: Request):
    """Reçoit la réponse vocale du client (SpeechResult)"""
    form = await request.form()
    speech_result = form.get("SpeechResult")
    call_sid = form.get("CallSid")
    
    # Traite la demande vocale avec Kimi
    parsed = await agent.process_voice_command(speech_result or "")
    
    # Si c'est un RDV, on le crée dans Square
    if parsed.get("intent") == "rdv" and parsed.get("action") == "create_rdv":
        # Il manque des infos? On demande
        if not parsed.get("date") or not parsed.get("time"):
            twiml = f"""
            <?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Say voice="Polly.Lea" language="fr-FR">{parsed.get("response_speech", "Quelle date et heure souhaitez-vous?")}</Say>
                <Gather action="/voice/response" method="POST" input="speech" language="fr-FR" timeout="5">
                    <Say voice="Polly.Lea" language="fr-FR">Dites-moi la date et l'heure.</Say>
                </Gather>
            </Response>
            """
            return Response(content=twiml, media_type="application/xml")
        
        # Créer le RDV
        result = square.create_appointment({
            "client_name": parsed.get("client_name", "Client téléphone"),
            "phone": from_number or "",
            "service_name": parsed.get("service", "Locks"),
            "date": parsed.get("date"),
            "time": parsed.get("time"),
            "notes": "Réservation téléphonique via agent IA"
        })
        
        if result.get("success"):
            msg = f"Rendez-vous confirmé pour le {parsed.get('date')} à {parsed.get('time')}."
        else:
            msg = f"Je n'ai pas pu créer le rendez-vous: {result.get('error', 'erreur inconnue')}"
        
        twiml = f"""
        <?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="Polly.Lea" language="fr-FR">{msg}</Say>
            <Say voice="Polly.Lea" language="fr-FR">Merci d'avoir appelé Kadio Coiffure. À bientôt!</Say>
            <Hangup/>
        </Response>
        """
        return Response(content=twiml, media_type="application/xml")
    
    # Si transfert à Othi
    if parsed.get("intent") == "transfert":
        twiml = f"""
        <?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="Polly.Lea" language="fr-FR">Je vous transfère à Othi.</Say>
            <Dial>{os.getenv('OTHI_PHONE', '+15149195970')}</Dial>
        </Response>
        """
        return Response(content=twiml, media_type="application/xml")
    
    # Réponse par défaut
    twiml = f"""
    <?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say voice="Polly.Lea" language="fr-FR">{parsed.get("response_speech", "Je n'ai pas compris. Veuillez répéter.")}</Say>
        <Gather action="/voice/response" method="POST" input="speech" language="fr-FR" timeout="5">
            <Say voice="Polly.Lea" language="fr-FR">Puis-je vous aider autrement?</Say>
        </Gather>
    </Response>
    """
    return Response(content=twiml, media_type="application/xml")

# ========== WEBHOOK WHATSAPP ==========

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Webhook Twilio pour messages WhatsApp entrants.
    Reçoit les messages, traite avec IA, répond automatiquement.
    """
    try:
        form = await request.form()
        
        from_number = form.get("From", "").replace("whatsapp:", "")
        message_body = form.get("Body", "")
        profile_name = form.get("ProfileName", "Client")
        
        if not from_number or not message_body:
            return {"status": "ignored"}
        
        # Traiter le message avec l'IA
        response = await whatsapp.process_incoming_message(
            from_number=from_number,
            message_body=message_body,
            profile_name=profile_name
        )
        
        # Envoyer la réponse
        send_result = whatsapp.send_message(from_number, response)
        
        return {"status": "processed", "sent": send_result.get("success", False)}
    except Exception as e:
        print(f"ERREUR WEBHOOK WHATSAPP: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/webhook/whatsapp")
async def whatsapp_webhook_verify(request: Request):
    """
    Vérification Twilio du webhook (pour la configuration initiale).
    Twilio envoie une requête GET pour valider l'URL.
    """
    return {"status": "ok", "message": "Webhook WhatsApp actif"}

# ========== WEBHOOK STRIPE (REÇUS) ==========

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """
    Webhook Stripe pour recevoir les événements de paiement.
    Envoie un reçu par email et SMS à chaque abonnement réussi.
    """
    try:
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature", "")
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        
        # Vérifier la signature si configurée
        event = None
        if webhook_secret and sig_header:
            import stripe as stripe_lib
            try:
                event = stripe_lib.Webhook.construct_event(
                    payload, sig_header, webhook_secret
                )
            except Exception as e:
                print(f"Erreur signature webhook: {e}")
                # Continuer quand même pour les tests
                event = json.loads(payload)
        else:
            event = json.loads(payload)
        
        event_type = event.get("type", "")
        
        # === ABONNEMENT CRÉÉ ===
        if event_type == "checkout.session.completed":
            session = event.get("data", {}).get("object", {})
            
            # Infos client
            customer_email = session.get("customer_email", "")
            customer_phone = session.get("customer_details", {}).get("phone", "")
            client_name = session.get("customer_details", {}).get("name", "Client")
            
            # Infos abonnement
            subscription_id = session.get("subscription", "")
            amount_total = session.get("amount_total", 0) / 100  # Centimes → dollars
            currency = session.get("currency", "cad").upper()
            
            # Déterminer le type d'abonnement depuis les métadonnées
            metadata = session.get("metadata", {})
            service_name = metadata.get("service", "Abonnement Kadio Coiffure")
            
            # Mettre à jour le statut de l'abonnement dans la base de données
            try:
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("""
                    UPDATE abonnements_clients_v2 
                    SET statut = 'actif', stripe_subscription_id = ?
                    WHERE stripe_session_id = ? AND statut = 'en_attente_paiement'
                """, (subscription_id, session.get('id')))
                if c.rowcount > 0:
                    conn.commit()
                    print(f"✅ Abonnement activé pour session {session.get('id')}")
                conn.close()
            except Exception as e:
                print(f"Erreur mise à jour abonnement: {e}")
            
            # === ENVOI REÇU EMAIL ===
            email_sent = False
            try:
                resend_api_key = os.getenv("RESEND_API_KEY", "")
                if resend_api_key and customer_email:
                    import requests
                    
                    receipt_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Reçu - Kadio Coiffure</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #1a1a2e; color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
        .receipt-title {{ font-size: 24px; color: #e94560; margin-bottom: 20px; }}
        .detail {{ margin: 15px 0; padding: 10px; background: white; border-radius: 5px; }}
        .detail-label {{ font-weight: bold; color: #555; }}
        .footer {{ text-align: center; margin-top: 30px; color: #888; font-size: 12px; }}
        .total {{ font-size: 20px; color: #e94560; font-weight: bold; text-align: right; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🌀 KADIO COIFFURE</h1>
        <p>615 Antoinette-Robidoux, Longueuil QC</p>
        <p>Tél: 514-919-5970</p>
    </div>
    <div class="content">
        <h2 class="receipt-title">📄 REÇU D'ABONNEMENT</h2>
        
        <div class="detail">
            <span class="detail-label">Client:</span> {client_name}
        </div>
        <div class="detail">
            <span class="detail-label">Email:</span> {customer_email}
        </div>
        <div class="detail">
            <span class="detail-label">Service:</span> {service_name}
        </div>
        <div class="detail">
            <span class="detail-label">Date:</span> {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </div>
        <div class="detail">
            <span class="detail-label">Référence:</span> {subscription_id or session.get('id', 'N/A')}
        </div>
        
        <div class="total">
            Total: {amount_total:.2f}$ {currency}
        </div>
        
        <div style="margin-top: 30px; padding: 15px; background: #e8f5e9; border-radius: 5px;">
            <strong>✅ Paiement confirmé</strong><br>
            Merci pour votre abonnement ! Vous recevrez un rappel avant chaque paiement mensuel.
        </div>
    </div>
    <div class="footer">
        <p>Kadio Coiffure - Votre salon de confiance</p>
        <p>Ce reçu est généré automatiquement.</p>
    </div>
</body>
</html>"""
                    
                    res = requests.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {resend_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "from": "Kadio Coiffure <receipts@kadio.co>",
                            "to": [customer_email],
                            "subject": f"🌀 Reçu d'abonnement - {service_name}",
                            "html": receipt_html
                        },
                        timeout=10
                    )
                    email_sent = res.status_code == 200
                    print(f"📧 Reçu email envoyé à {customer_email}: {'OK' if email_sent else 'ÉCHEC'}")
            except Exception as e:
                print(f"Erreur envoi email reçu: {e}")
            
            # === ENVOI REÇU SMS ===
            sms_sent = False
            try:
                twilio_sid = os.getenv("TWILIO_SID", "")
                twilio_auth = os.getenv("TWILIO_AUTH", "")
                twilio_number = os.getenv("TWILIO_PHONE_NUMBER", "")
                
                if twilio_sid and twilio_auth and twilio_number and customer_phone:
                    # Formater le numéro (enlever espaces, ajouter + si manquant)
                    phone = customer_phone.replace(" ", "").replace("-", "")
                    if not phone.startswith("+"):
                        phone = "+1" + phone if len(phone) == 10 else "+" + phone
                    
                    sms_body = (
                        f"🌀 KADIO COIFFURE\n"
                        f"Reçu d'abonnement\n"
                        f"Service: {service_name}\n"
                        f"Montant: {amount_total:.2f}$ {currency}\n"
                        f"Date: {datetime.now().strftime('%d/%m/%Y')}\n"
                        f"Ref: {subscription_id or session.get('id', 'N/A')[:8]}\n\n"
                        f"Merci pour votre confiance! ❤️‍🔥"
                    )
                    
                    import requests
                    auth = (twilio_sid, twilio_auth)
                    data = {
                        "From": twilio_number,
                        "To": phone,
                        "Body": sms_body
                    }
                    
                    res = requests.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json",
                        auth=auth,
                        data=data,
                        timeout=10
                    )
                    sms_sent = res.status_code == 201
                    print(f"📱 Reçu SMS envoyé à {phone}: {'OK' if sms_sent else 'ÉCHEC'}")
            except Exception as e:
                print(f"Erreur envoi SMS reçu: {e}")
            
            # Sauvegarder dans la base de données
            try:
                conn = get_db_connection()
                conn.execute("""
                    INSERT INTO historique_paiements 
                    (client_email, client_phone, montant, devise, type, reference, service, date_paiement, email_envoye, sms_envoye)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    customer_email, customer_phone, amount_total, currency,
                    "subscription", subscription_id or session.get("id", ""),
                    service_name, datetime.now().isoformat(),
                    email_sent, sms_sent
                ))
                conn.commit()
                conn.close()
                print("💾 Paiement enregistré dans la base de données")
            except Exception as e:
                print(f"Erreur sauvegarde DB: {e}")
            
            return {
                "status": "success",
                "event": "checkout.session.completed",
                "receipt": {
                    "email_sent": email_sent,
                    "sms_sent": sms_sent,
                    "customer": customer_email or customer_phone
                }
            }
        
        # Autres événements
        return {"status": "ignored", "event": event_type}
        
    except Exception as e:
        print(f"ERREUR WEBHOOK STRIPE: {e}")
        return {"status": "error", "message": str(e)}

# ========== CONNEXION PLATEFORMES ==========

@app.post("/connect")
async def connect_platform(request: ConnectRequest):
    """Connecte une plateforme (OAuth simplifié)"""
    if request.platform == "whatsapp":
        return whatsapp.connect(request.token)
    
    elif request.platform == "square":
        return {"connected": square.is_connected(), "location_id": square.location_id}
    
    elif request.platform == "instagram":
        return instagram.connect(request.token)
    
    elif request.platform == "facebook":
        return instagram.connect_facebook(request.token)
    
    else:
        raise HTTPException(status_code=400, detail="Plateforme non supportée")

@app.get("/connect/{platform}/url")
async def get_auth_url(platform: str):
    """Génère l'URL d'authentification OAuth"""
    if platform == "square":
        return {"url": "https://connect.squareup.com/oauth2/authorize?client_id=" + os.getenv("SQUARE_APP_ID", "")}
    elif platform == "instagram":
        return {"url": instagram.get_oauth_url()}
    elif platform == "facebook":
        return {"url": instagram.get_facebook_oauth_url()}
    else:
        raise HTTPException(status_code=400, detail="Pas d'OAuth pour cette plateforme")

# ========== ONBOARDING / OBSERVATION ==========

@app.post("/onboarding/observe")
async def observe_owner_action(request: Request):
    """Enregistre une action du propriétaire pour observation"""
    data = await request.json()
    
    action = OwnerAction(
        timestamp=data.get('timestamp', datetime.now().isoformat()),
        platform=data.get('platform'),
        action_type=data.get('action_type'),
        client_id=data.get('client_id'),
        content=data.get('content'),
        context=data.get('context'),
        duration_seconds=data.get('duration_seconds', 0)
    )
    
    owner_id = data.get('owner_id', 'default')
    observation.observe_action(action, owner_id)
    
    return {"status": "observed", "action_type": action.action_type}

@app.get("/onboarding/status")
async def onboarding_status(owner_id: str = "default"):
    """Récupère le statut d'onboarding du propriétaire"""
    return dashboard_onboarding.get_status(owner_id)

@app.get("/onboarding/daily-summary")
async def daily_summary(owner_id: str = "default"):
    """Récupère le résumé quotidien d'observation"""
    return observation.generate_daily_summary(owner_id)

@app.get("/onboarding/insights")
async def conversation_insights(owner_id: str = "default"):
    """Insights sur les conversations"""
    return dashboard_onboarding.get_conversation_insights(owner_id)

@app.get("/onboarding/report")
async def learning_report(owner_id: str = "default"):
    """Rapport complet d'apprentissage"""
    return dashboard_onboarding.get_learning_report(owner_id)

@app.post("/onboarding/enable-autonomy")
async def enable_autonomy(request: Request):
    """Active l'autonomie pour un type de pattern"""
    data = await request.json()
    pattern_type = data.get('pattern_type')
    owner_id = data.get('owner_id', 'default')
    
    # Met à jour la DB pour activer l'autonomie
    # TODO: implémenter
    
    return {"status": "enabled", "pattern_type": pattern_type}

@app.get("/onboarding/takeover-check")
async def takeover_check(context: str, owner_id: str = "default"):
    """Vérifie si une situation peut être gérée automatiquement"""
    can_handle = observation.can_handle_autonomously(context, owner_id)
    learned_response = observation.get_learned_response(context, owner_id)
    
    return {
        "can_handle_autonomously": can_handle,
        "learned_response": learned_response,
        "context": context
    }

# ========== COMMANDES OTHI (via Telegram) ==========

@app.post("/command")
async def process_command(command: str, user_id: str = "othi"):
    """Reçoit les commandes de Othi via Telegram"""
    # Parse la commande
    if command.startswith("rdv"):
        return await handle_appointment_command(command)
    
    elif command.startswith("post"):
        return await handle_post_command(command)
    
    elif command.startswith("stats"):
        return await handle_stats_command()
    
    elif command.startswith("clients"):
        return await handle_clients_command()
    
    else:
        # Conversation libre avec l'agent
        return await chat_with_agent(command, user_id)

# ========== FONCTIONS INTERNES ==========

async def process_message(message: Message) -> str:
    """Traite un message avec le système d'observation + Kimi"""
    
    # Vérifie si on peut répondre automatiquement (pattern appris)
    owner_id = "default"  # TODO: récupérer l'owner_id depuis le contexte
    can_handle = observation.can_handle_autonomously(message.content, owner_id)
    learned_response = observation.get_learned_response(message.content, owner_id)
    
    if can_handle and learned_response:
        # Pattern autonome - répond directement avec le style appris
        return learned_response
    
    # Sinon, utilise Kimi pour une réponse intelligente
    # OU marque comme "à valider" si en phase d'observation
    history = conversations.get(message.sender_id, [])
    
    status = dashboard_onboarding.get_status(owner_id)
    
    if status['phase'] == 'observation':
        # Phase observation : on notifie le propriétaire, pas d'autonomie
        return f"[OBSERVATION] Message reçu de {message.sender_id}: {message.content}"
    
    elif status['phase'] == 'copilot':
        # Phase copilot : propose une réponse, attend validation
        kimi_response = await agent.process_client_message(message.content, history)
        return f"[COPILOT] {kimi_response}"
    
    else:
        # Phase autonomie : répond directement
        return await agent.process_client_message(message.content, history)

async def send_response(platform: str, sender_id: str, response: str):
    """Envoie une réponse sur la plateforme appropriée"""
    if platform == "whatsapp":
        return whatsapp.send_message(sender_id, response)
    elif platform == "instagram":
        return instagram.send_message(sender_id, response)
    elif platform == "voice":
        # La réponse vocale est gérée dans le webhook Twilio
        pass
    elif platform == "telegram":
        # TODO: implémenter
        pass
    else:
        # Log pour debug
        print(f"Plateforme {platform} non supportée pour l'envoi")
        return {"success": False, "error": "Plateforme non supportée"}

async def handle_appointment_command(command: str) -> dict:
    """Gère les commandes de rendez-vous"""
    # Format: rdv [date] [heure] [service] [nom] [téléphone]
    parts = command.split()
    if len(parts) < 5:
        return {"error": "Format: rdv JJ/MM/AAAA HH:MM Service Nom Téléphone"}
    
    date = parts[1]
    time = parts[2]
    service = parts[3]
    name = parts[4]
    phone = parts[5] if len(parts) > 5 else ""
    
    result = await square.create_appointment({
        "client_name": name,
        "phone": phone,
        "service_name": service,
        "date": date,
        "time": time
    })
    
    return result

async def handle_post_command(command: str) -> dict:
    """Gère les commandes de publication"""
    # Format: post [plateforme] [message]
    # TODO: implémenter
    return {"status": "not_implemented"}

# ========== MODÈLES GESTION EMPLOYÉS ==========

class EmployeCreate(BaseModel):
    nom: str
    telephone: str
    email: Optional[str] = None
    role: str  # coiffeur, locticien, barbier, manucure, estheticien, tisserand
    specialite: Optional[str] = None
    echelon: str = "bronze"  # bronze, argent, or, platine
    salaire_horaire: float = 0
    date_embauche: str
    square_id: Optional[str] = None

class EmployeUpdate(BaseModel):
    nom: Optional[str] = None
    telephone: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    specialite: Optional[str] = None
    echelon: Optional[str] = None
    salaire_horaire: Optional[float] = None
    statut: Optional[str] = None

class PointageCreate(BaseModel):
    employe_id: int
    date_journee: str
    heure_arrivee: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    adresse_pointage: Optional[str] = None

class PointageDepart(BaseModel):
    employe_id: int
    date_journee: str
    heure_depart: str

class TacheComplete(BaseModel):
    employe_id: int
    tache_id: int
    heure_debut: str
    heure_fin: str
    duree_minutes: int
    note: Optional[float] = None
    commentaire: Optional[str] = None

class NoteClientCreate(BaseModel):
    client_id: Optional[str] = None
    client_nom: Optional[str] = None
    employe_id: int
    rendez_vous_id: Optional[str] = None
    date_rdv: Optional[str] = None
    service: Optional[str] = None
    accueil: float = 0  # 1-5
    qualite: float = 0  # 1-5
    proprete: float = 0  # 1-5
    ambiance: float = 0  # 1-5
    commentaire: Optional[str] = None

class SanctionCreate(BaseModel):
    employe_id: int
    type: str
    raison: str
    details: Optional[str] = None
    date_sanction: Optional[str] = None
    duree_suspension_jours: Optional[int] = 0
    retrait_points: Optional[int] = 0

class AlerteCreate(BaseModel):
    employe_id: int
    type: str
    description: str
    niveau: str = "moyen"
    date_alerte: str

# ========== MODÈLES AUTHENTIFICATION EMPLOYÉ ==========

class LoginRequest(BaseModel):
    telephone: str
    prenom: Optional[str] = None  # Ajouté pour vérification
    code: str

class PointageAvecCode(BaseModel):
    employe_id: int
    code: str
    date_journee: str
    heure: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    adresse_pointage: Optional[str] = None
    raison_retard: Optional[str] = None  # OBLIGATOIRE si retard détecté

class PauseRequest(BaseModel):
    employe_id: int
    code: str
    date_journee: str
    heure: str
    type_pause: str  # "debut" ou "fin"

class RenouvelerCodeRequest(BaseModel):
    employe_id: int

# ========== FONCTION UTILITAIRE: VÉRIFICATION CODE PIN ==========

def verifier_code_pin(employe_id: int, code: str) -> bool:
    """Vérifie si le code PIN est valide (temporaire OU permanent) — PIN hashés avec bcrypt"""
    conn = get_db_connection()
    c = conn.cursor()
    
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Vérifier d'abord le code temporaire (pas hashé, usage unique)
    c.execute('''
        SELECT id FROM codes_pointage 
        WHERE employe_id = ? AND code = ? AND actif = 1 
        AND (date_expiration IS NULL OR date_expiration > ?)
    ''', (employe_id, code, now_str))
    result = c.fetchone()
    
    # Si pas trouvé, vérifier le PIN permanent (hashé avec bcrypt)
    if not result:
        import bcrypt
        c.execute('SELECT pin FROM pins_employes WHERE employe_id = ?', (employe_id,))
        row = c.fetchone()
        if row and row['pin']:
            stored_hash = row['pin'].encode('utf-8')
            try:
                if bcrypt.checkpw(code.encode('utf-8'), stored_hash):
                    result = True
            except Exception:
                pass
    
    conn.close()
    return result is not None

# ========== ROUTES GESTION EMPLOYÉS ==========

@app.get("/employes")
async def list_employes(statut: Optional[str] = None):
    """Liste tous les employés, filtrable par statut"""
    conn = get_db_connection()
    c = conn.cursor()
    if statut:
        c.execute("SELECT * FROM employes WHERE statut = ? ORDER BY nom", (statut,))
    else:
        c.execute("SELECT * FROM employes ORDER BY nom")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/employes/{employe_id}")
async def get_employe(employe_id: int):
    """Détail d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM employes WHERE id = ?", (employe_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    return dict(row)

@app.post("/employes")
async def create_employe(employe: EmployeCreate):
    """Crée un nouvel employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO employes (nom, telephone, email, role, specialite, echelon, salaire_horaire, date_embauche, square_id, statut)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'actif')
    ''', (employe.nom, employe.telephone, employe.email, employe.role, employe.specialite, employe.echelon, employe.salaire_horaire, employe.date_embauche, employe.square_id))
    conn.commit()
    employe_id = c.lastrowid
    conn.close()
    
    # Générer un code de pointage aléatoire à 4 chiffres
    import random
    code = str(random.randint(1000, 9999))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO codes_pointage (employe_id, code, date_creation)
        VALUES (?, ?, ?)
    ''', (employe_id, code, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    return {"id": employe_id, "code_pointage": code, "message": "Employé créé avec succès"}

@app.patch("/employes/{employe_id}")
async def update_employe(employe_id: int, employe: EmployeUpdate):
    """Met à jour un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    updates = []
    values = []
    for field, value in employe.dict(exclude_unset=True).items():
        updates.append(f"{field} = ?")
        values.append(value)
    
    if not updates:
        raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour")
    
    values.append(employe_id)
    c.execute(f"UPDATE employes SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", values)
    conn.commit()
    conn.close()
    return {"message": "Employé mis à jour"}

@app.delete("/employes/{employe_id}")
async def delete_employe(employe_id: int):
    """Supprime un employé (soft delete: met le statut à 'demission')"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE employes SET statut = 'demission', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (employe_id,))
    conn.commit()
    conn.close()
    return {"message": "Employé marqué comme démissionnaire"}

@app.get("/employes/{employe_id}/pointages")
async def get_pointages_employe(employe_id: int, date_debut: Optional[str] = None, date_fin: Optional[str] = None):
    """Historique des pointages d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    if date_debut and date_fin:
        c.execute('''
            SELECT * FROM pointages WHERE employe_id = ? AND date_journee BETWEEN ? AND ? ORDER BY date_journee DESC
        ''', (employe_id, date_debut, date_fin))
    else:
        c.execute('SELECT * FROM pointages WHERE employe_id = ? ORDER BY date_journee DESC', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/employes/pointer-arrivee")
async def pointer_arrivee(pointage: PointageCreate):
    """Pointage arrivée employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Calculer le retard si nécessaire (heure d'ouverture du salon: 10h ou 12h selon le jour)
    # Simplifié: on stocke juste le pointage
    c.execute('''
        INSERT INTO pointages (employe_id, date_journee, heure_arrivee, latitude, longitude, adresse_pointage, statut)
        VALUES (?, ?, ?, ?, ?, ?, 'incomplet')
    ''', (pointage.employe_id, pointage.date_journee, pointage.heure_arrivee, pointage.latitude, pointage.longitude, pointage.adresse_pointage))
    conn.commit()
    conn.close()
    return {"message": "Pointage arrivée enregistré"}

@app.post("/employes/pointer-depart")
async def pointer_depart(depart: PointageDepart):
    """Pointage départ employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Mettre à jour le pointage du jour
    c.execute('''
        UPDATE pointages SET heure_depart = ?, statut = 'complet' 
        WHERE employe_id = ? AND date_journee = ?
    ''', (depart.heure_depart, depart.employe_id, depart.date_journee))
    conn.commit()
    conn.close()
    return {"message": "Pointage départ enregistré"}

# ========== ROUTES BADGES ET RÉCOMPENSES ==========

class BadgeCreate(BaseModel):
    nom: str
    description: Optional[str] = None
    icone: Optional[str] = "🏅"
    categorie: str  # performance, ponctualite, service, technique
    condition_type: str  # notes, ponctualite, taches, anciennete
    condition_valeur: float
    condition_periode: Optional[str] = "all"  # jour, semaine, mois, all
    points_bonus: Optional[int] = 0
    recompense_montant: Optional[float] = 0

class AttributionBadgeRequest(BaseModel):
    employe_id: int
    badge_id: int
    raison: Optional[str] = None

@app.get("/badges")
async def list_badges(categorie: Optional[str] = None):
    """Liste tous les badges disponibles"""
    conn = get_db_connection()
    c = conn.cursor()
    if categorie:
        c.execute("SELECT * FROM badges WHERE categorie = ? AND actif = 1 ORDER BY nom", (categorie,))
    else:
        c.execute("SELECT * FROM badges WHERE actif = 1 ORDER BY nom")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/badges")
async def create_badge(badge: BadgeCreate):
    """Créer un nouveau badge"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO badges (nom, description, icone, categorie, condition_type, condition_valeur, condition_periode, points_bonus, recompense_montant)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (badge.nom, badge.description, badge.icone, badge.categorie, badge.condition_type, 
          badge.condition_valeur, badge.condition_periode, badge.points_bonus, badge.recompense_montant))
    conn.commit()
    badge_id = c.lastrowid
    conn.close()
    return {"id": badge_id, "message": "Badge créé"}

@app.get("/badges/employe/{employe_id}")
async def get_badges_employe(employe_id: int):
    """Récupère les badges d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT b.*, be.date_attribution, be.raison, be.vu_par_employe 
        FROM badges b
        JOIN badges_employes be ON b.id = be.badge_id
        WHERE be.employe_id = ? AND b.actif = 1
        ORDER BY be.date_attribution DESC
    ''', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/badges/attribuer")
async def attribuer_badge(req: AttributionBadgeRequest):
    """Attribue un badge à un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier si le badge existe
    c.execute("SELECT * FROM badges WHERE id = ? AND actif = 1", (req.badge_id,))
    badge = c.fetchone()
    if not badge:
        conn.close()
        raise HTTPException(status_code=404, detail="Badge non trouvé")
    
    # Vérifier si l'employé a déjà ce badge
    c.execute("SELECT * FROM badges_employes WHERE employe_id = ? AND badge_id = ?", (req.employe_id, req.badge_id))
    if c.fetchone():
        conn.close()
        return {"message": "L'employé a déjà ce badge"}
    
    # Attribuer le badge
    c.execute('''
        INSERT INTO badges_employes (employe_id, badge_id, date_attribution, raison)
        VALUES (?, ?, ?, ?)
    ''', (req.employe_id, req.badge_id, datetime.now().isoformat(), req.raison))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True, 
        "message": f"Badge '{badge['nom']}' attribué",
        "points_bonus": badge["points_bonus"]
    }

@app.post("/badges/verifier-auto/{employe_id}")
async def verifier_badges_auto(employe_id: int):
    """
    Vérifie et attribue automatiquement les badges auxquels l'employé a droit.
    À appeler périodiquement (cron) ou après chaque événement.
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer l'employé
    c.execute("SELECT * FROM employes WHERE id = ? AND statut = 'actif'", (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    badges_attribues = []
    
    # Récupérer tous les badges actifs
    c.execute("SELECT * FROM badges WHERE actif = 1")
    badges = c.fetchall()
    
    for badge in badges:
        # Vérifier si l'employé a déjà ce badge
        c.execute("SELECT * FROM badges_employes WHERE employe_id = ? AND badge_id = ?", (employe_id, badge["id"]))
        if c.fetchone():
            continue
        
        # Vérifier la condition
        condition_remplie = False
        
        if badge["condition_type"] == "notes":
            # Vérifier la moyenne des notes
            c.execute('''
                SELECT AVG(note_moyenne) as avg_note, COUNT(*) as count
                FROM notes_clients WHERE employe_id = ?
            ''', (employe_id,))
            result = c.fetchone()
            if result and result["avg_note"] and result["avg_note"] >= badge["condition_valeur"]:
                condition_remplie = True
                
        elif badge["condition_type"] == "ponctualite":
            # Vérifier le taux de ponctualité
            c.execute('''
                SELECT COUNT(*) as total, COUNT(CASE WHEN retard_minutes = 0 THEN 1 END) as ponctuel
                FROM pointages WHERE employe_id = ?
            ''', (employe_id,))
            result = c.fetchone()
            if result and result["total"] > 0:
                taux = (result["ponctuel"] / result["total"]) * 100
                if taux >= badge["condition_valeur"]:
                    condition_remplie = True
                    
        elif badge["condition_type"] == "taches":
            # Vérifier le nombre de tâches complétées
            c.execute('''
                SELECT COUNT(*) as total FROM historique_taches WHERE employe_id = ?
            ''', (employe_id,))
            result = c.fetchone()
            if result and result["total"] >= badge["condition_valeur"]:
                condition_remplie = True
        
    # Attribuer le badge si condition remplie
    if condition_remplie:
        c.execute('''
            INSERT INTO badges_employes (employe_id, badge_id, date_attribution, raison)
            VALUES (?, ?, ?, ?)
        ''', (employe_id, badge["id"], datetime.now().isoformat(), 
                  f"Attribution automatique - {badge['condition_type']} >= {badge['condition_valeur']}"))
            
        badges_attribues.append({
                "badge_id": badge["id"],
                "nom": badge["nom"],
                "icone": badge["icone"],
                "points_bonus": badge["points_bonus"]
            })
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "badges_attribues": badges_attribues,
        "total": len(badges_attribues)
    }

@app.get("/recompenses/employe/{employe_id}")
async def get_recompenses_employe(employe_id: int):
    """Récupère les récompenses d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM recompenses WHERE employe_id = ? ORDER BY date_attribution DESC
    ''', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/recompenses/attribuer")
async def attribuer_recompense(req: AttributionBadgeRequest):
    """Attribue une récompense à un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO recompenses (employe_id, type, description, raison, date_attribution)
        VALUES (?, 'bonus', ?, ?, ?)
    ''', (req.employe_id, req.raison or "Récompense", req.raison or "Récompense spéciale", datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Récompense attribuée"}

@app.post("/badges/employe/{employe_id}/vu/{badge_id}")
async def marquer_badge_vu(employe_id: int, badge_id: int):
    """Marque un badge comme vu par l'employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        UPDATE badges_employes SET vu_par_employe = 1 WHERE employe_id = ? AND badge_id = ?
    ''', (employe_id, badge_id))
    conn.commit()
    conn.close()
    return {"message": "Badge marqué comme vu"}

@app.get("/badges/stats/{employe_id}")
async def stats_badges_employe(employe_id: int):
    """Stats des badges d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT COUNT(*) as total FROM badges_employes WHERE employe_id = ?
    ''', (employe_id,))
    total = c.fetchone()["total"]
    
    c.execute('''
        SELECT COUNT(*) as non_vus FROM badges_employes WHERE employe_id = ? AND vu_par_employe = 0
    ''', (employe_id,))
    non_vus = c.fetchone()["non_vus"]
    
    c.execute('''
        SELECT SUM(b.points_bonus) as total_points
        FROM badges_employes be
        JOIN badges b ON be.badge_id = b.id
        WHERE be.employe_id = ?
    ''', (employe_id,))
    points = c.fetchone()["total_points"] or 0
    
    conn.close()
    
    return {
        "total_badges": total,
        "badges_non_vus": non_vus,
        "points_bonus_total": points
    }

# ========== ROUTES SANCTIONS ==========

@app.get("/sanctions")
async def list_sanctions(employe_id: Optional[int] = None, statut: Optional[str] = None):
    """Liste des sanctions, filtrable par employé et statut"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if employe_id:
        c.execute('''
            SELECT s.*, e.nom as employe_nom FROM sanctions s
            JOIN employes e ON s.employe_id = e.id
            WHERE s.employe_id = ? ORDER BY s.date_sanction DESC
        ''', (employe_id,))
    else:
        c.execute('''
            SELECT s.*, e.nom as employe_nom FROM sanctions s
            JOIN employes e ON s.employe_id = e.id
            ORDER BY s.date_sanction DESC
        ''')
    
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/sanctions")
async def create_sanction(sanction: SanctionCreate):
    """Crée une sanction et notifie le patron si grave"""
    conn = get_db_connection()
    c = conn.cursor()
    
    date_sanction = sanction.date_sanction or datetime.now().isoformat()
    
    # Insérer la sanction
    c.execute('''
        INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, duree_suspension_jours)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (sanction.employe_id, sanction.type, sanction.raison, sanction.details, 
          date_sanction, sanction.duree_suspension_jours))
    
    sanction_id = c.lastrowid
    
    # Si retrait de points, mettre à jour le score
    if sanction.retrait_points and sanction.retrait_points > 0:
        # On ne peut pas retirer de score_total car la colonne n'existe pas
        # On garde juste l'info dans la sanction
        pass
    
    conn.commit()
    conn.close()
    
    # Notifier le patron pour les sanctions graves
    if sanction.type in ["suspension", "retrait_bonus"]:
        try:
            notification_manager.alerte_systeme(
                f"🚨 SANCTION GRAVE - {sanction.type.upper()}\n"
                f"Employé ID: {sanction.employe_id}\n"
                f"Raison: {sanction.raison}\n"
                f"Date: {date_sanction}",
                niveau="critique"
            )
        except:
            pass
    
    return {
        "success": True,
        "id": sanction_id,
        "message": f"Sanction '{sanction.type}' créée",
        "employe_id": sanction.employe_id
    }

@app.get("/sanctions/employe/{employe_id}")
async def get_sanctions_employe(employe_id: int):
    """Récupère l'historique des sanctions d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT s.*, e.nom as employe_nom FROM sanctions s
        JOIN employes e ON s.employe_id = e.id
        WHERE s.employe_id = ? ORDER BY s.date_sanction DESC
    ''', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/sanctions/stats")
async def stats_sanctions():
    """Stats globales des sanctions"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Total par type
    c.execute('''
        SELECT type, COUNT(*) as total FROM sanctions GROUP BY type
    ''')
    par_type = [dict(row) for row in c.fetchall()]
    
    # Total par employé (top 5)
    c.execute('''
        SELECT e.nom, COUNT(*) as total FROM sanctions s
        JOIN employes e ON s.employe_id = e.id
        GROUP BY s.employe_id ORDER BY total DESC LIMIT 5
    ''')
    par_employe = [dict(row) for row in c.fetchall()]
    
    # Total global
    c.execute('SELECT COUNT(*) as total FROM sanctions')
    total = c.fetchone()["total"]
    
    conn.close()
    
    return {
        "total_sanctions": total,
        "par_type": par_type,
        "top_employes": par_employe
    }

@app.patch("/sanctions/{sanction_id}")
async def update_sanction(sanction_id: int, updates: dict):
    """Met à jour une sanction (ex: levée de suspension)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Construire la requête dynamique
    fields = []
    values = []
    for key, value in updates.items():
        if key in ["statut", "levee_date", "levee_raison"]:
            fields.append(f"{key} = ?")
            values.append(value)
    
    if not fields:
        conn.close()
        raise HTTPException(status_code=400, detail="Aucun champ valide à mettre à jour")
    
    values.append(sanction_id)
    c.execute(f"UPDATE sanctions SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    
    return {"message": "Sanction mise à jour"}

# Seed badges par défaut
@app.post("/badges/seed")
async def seed_badges():
    """Crée les badges par défaut du salon"""
    badges_defaut = [
        {"nom": "⭐ 5 Étoiles", "description": "Moyenne des notes clients >= 4.5", "icone": "⭐", "categorie": "performance", "condition_type": "notes", "condition_valeur": 4.5, "points_bonus": 50, "recompense_montant": 25},
        {"nom": "⏰ Ponctuel", "description": "95% des arrivées à l'heure", "icone": "⏰", "categorie": "ponctualite", "condition_type": "ponctualite", "condition_valeur": 95, "points_bonus": 30, "recompense_montant": 15},
        {"nom": "🧹 Propre", "description": "50 tâches ménagères complétées", "icone": "🧹", "categorie": "service", "condition_type": "taches", "condition_valeur": 50, "points_bonus": 20, "recompense_montant": 10},
        {"nom": "🔒 Expert Locks", "description": "Spécialiste locks reconnu", "icone": "🔒", "categorie": "technique", "condition_type": "notes", "condition_valeur": 4.0, "points_bonus": 40, "recompense_montant": 20},
        {"nom": "💎 6 Mois", "description": "6 mois d'ancienneté", "icone": "💎", "categorie": "performance", "condition_type": "anciennete", "condition_valeur": 6, "points_bonus": 100, "recompense_montant": 50},
    ]
    
    conn = get_db_connection()
    c = conn.cursor()
    
    count = 0
    for badge in badges_defaut:
        c.execute('''
            INSERT OR IGNORE INTO badges (nom, description, icone, categorie, condition_type, condition_valeur, points_bonus, recompense_montant)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (badge["nom"], badge["description"], badge["icone"], badge["categorie"], 
              badge["condition_type"], badge["condition_valeur"], badge["points_bonus"], badge["recompense_montant"]))
        if c.rowcount > 0:
            count += 1
    
    conn.commit()
    conn.close()
    
    return {"message": f"{count} badges créés"}

# ========== ROUTES AUTHENTIFICATION EMPLOYÉ (CODE PIN) ==========

@app.post("/employe/login")
async def login_employe(login: LoginRequest):
    """
    Authentification employé avec téléphone + code PIN.
    Retourne les infos de l'employé si le code est valide.
    """
    # 1. Trouver l'employé par téléphone
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, nom, telephone, email, role, specialite, echelon, statut FROM employes WHERE telephone = ? AND statut = 'actif'", (login.telephone,))
    employe = c.fetchone()
    
    if not employe:
        conn.close()
        raise HTTPException(status_code=401, detail="Téléphone non reconnu ou employé inactif")
    
    # Si prénom fourni, vérifier qu'il correspond
    if login.prenom and login.prenom.lower() not in employe["nom"].lower():
        conn.close()
        raise HTTPException(status_code=401, detail="Prénom incorrect")
    
    employe_id = employe["id"]
    
    # Vérifier le code PIN (peut être le code temporaire de pointage OU le PIN permanent)
    # D'abord vérifier le code temporaire
    c.execute('''
        SELECT id FROM codes_pointage 
        WHERE employe_id = ? AND code = ? AND actif = 1 
        AND (date_expiration IS NULL OR date_expiration > ?)
    ''', (employe_id, login.code, datetime.now().isoformat()))
    result = c.fetchone()
    
    # Si pas trouvé, vérifier le PIN permanent
    if not result:
        c.execute('SELECT id FROM pins_employes WHERE employe_id = ? AND pin = ?', (employe_id, login.code))
        result = c.fetchone()
    
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Code PIN incorrect")
    
    # 3. Vérifier si vidéo obligatoire visionnée (première connexion)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT visionnee FROM videos_visionnees 
        WHERE employe_id = ? AND video_type = 'reglement' AND complet = 1
    ''', (employe_id,))
    video_vue = c.fetchone()
    conn.close()
    
    if not video_vue:
        return {
            "success": False,
            "bloque": True,
            "message": "Vous devez visionner la vidéo explicative avant de continuer.",
            "action_requise": "/videos/visionner",
            "employe": {
                "id": employe_id,
                "nom": employe["nom"]
            }
        }
    
    # 4. Retourner les infos employé
    return {
        "success": True,
        "employe": {
            "id": employe_id,
            "nom": employe["nom"],
            "telephone": employe["telephone"],
            "email": employe["email"],
            "role": employe["role"],
            "specialite": employe["specialite"],
            "echelon": employe["echelon"]
        },
        "message": f"Bonjour {employe['nom']} ! Connecté avec succès."
    }

@app.post("/employe/pin")
async def creer_pin_employe(employe_id: int, pin: str):
    """Crée ou met à jour le PIN permanent d'un employé (4 chiffres) — hashé avec bcrypt"""
    if not pin.isdigit() or len(pin) != 4:
        raise HTTPException(status_code=400, detail="Le PIN doit être composé de 4 chiffres")
    
    import bcrypt
    pin_hash = bcrypt.hashpw(pin.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier que l'employé existe
    c.execute('SELECT id FROM employes WHERE id = ?', (employe_id,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    # Insérer ou remplacer le PIN (stocké en hash)
    c.execute('''
        INSERT OR REPLACE INTO pins_employes (employe_id, pin, date_creation)
        VALUES (?, ?, ?)
    ''', (employe_id, pin_hash, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": f"PIN sécurisé créé pour l'employé {employe_id}"}

@app.post("/employe/pointer-arrivee-code")
async def pointer_arrivee_avec_code(pointage: PointageAvecCode):
    """
    Pointage arrivée avec vérification du code PIN.
    """
    # Vérifier le code PIN
    if not verifier_code_pin(pointage.employe_id, pointage.code):
        raise HTTPException(status_code=401, detail="Code PIN incorrect")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier s'il y a déjà un pointage aujourd'hui
    c.execute('''
        SELECT id FROM pointages WHERE employe_id = ? AND date_journee = ?
    ''', (pointage.employe_id, pointage.date_journee))
    existing = c.fetchone()
    
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Pointage arrivée déjà enregistré pour aujourd'hui")
    
    # Calculer le retard (heure d'ouverture: 10h Mer-Dim, 12h Lun)
    # Simplifié: on stocke juste le pointage
    heure_arrivee = datetime.strptime(pointage.heure, "%H:%M")
    retard = 0
    
    # Insérer le pointage
    c.execute('''
        INSERT INTO pointages (employe_id, date_journee, heure_arrivee, retard_minutes, latitude, longitude, adresse_pointage, statut, code_utilise)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'incomplet', ?)
    ''', (pointage.employe_id, pointage.date_journee, pointage.heure, retard, pointage.latitude, pointage.longitude, pointage.adresse_pointage, pointage.code))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": "Arrivée enregistrée ! Bonne journée.",
        "heure_arrivee": pointage.heure,
        "retard_minutes": retard
    }

@app.post("/employe/pointer-depart-code")
async def pointer_depart_avec_code(pointage: PointageAvecCode):
    """
    Pointage départ avec vérification du code PIN.
    """
    # Vérifier le code PIN
    if not verifier_code_pin(pointage.employe_id, pointage.code):
        raise HTTPException(status_code=401, detail="Code PIN incorrect")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier qu'il y a un pointage d'arrivée
    c.execute('''
        SELECT id, heure_arrivee FROM pointages WHERE employe_id = ? AND date_journee = ?
    ''', (pointage.employe_id, pointage.date_journee))
    pointage_row = c.fetchone()
    
    if not pointage_row:
        conn.close()
        raise HTTPException(status_code=400, detail="Aucun pointage d'arrivée trouvé pour aujourd'hui")
    
    # Calculer la durée de travail
    heure_arrivee = datetime.strptime(pointage_row["heure_arrivee"], "%H:%M")
    heure_depart = datetime.strptime(pointage.heure, "%H:%M")
    duree_minutes = int((heure_depart - heure_arrivee).total_seconds() / 60)
    
    # Détecter départ prématuré (>1h avant fin prévue)
    date_jour = datetime.strptime(pointage.date_journee, "%Y-%m-%d")
    jour_semaine = date_jour.strftime("%A")
    fermeture_heures = {"Monday": 19, "Tuesday": 19, "Wednesday": 19, "Thursday": 21, "Friday": 21, "Saturday": 21, "Sunday": 17}
    heure_fermeture = fermeture_heures.get(jour_semaine, 19)
    
    depart_minutes = heure_depart.hour * 60 + heure_depart.minute
    fermeture_minutes = heure_fermeture * 60
    
    if fermeture_minutes - depart_minutes > 60:
        # Départ prématuré > 1h
        c.execute('''
            INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
            VALUES (?, 'depart_premature', ?, 'attention', ?, 'nouveau')
        ''', (pointage.employe_id, f"{employe_nom} a pointé son départ 1h avant la fin prévue.", pointage.date_journee))
    
    # Mettre à jour le pointage
    c.execute('''
        UPDATE pointages 
        SET heure_depart = ?, duree_travail_minutes = ?, statut = 'complet' 
        WHERE employe_id = ? AND date_journee = ?
    ''', (pointage.heure, duree_minutes, pointage.employe_id, pointage.date_journee))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": "Départ enregistré ! À demain.",
        "heure_depart": pointage.heure,
        "duree_travail_minutes": duree_minutes
    }

@app.post("/employe/pause")
async def gerer_pause(pause: PauseRequest):
    """
    Début ou fin de pause avec vérification du code PIN.
    """
    # Vérifier le code PIN
    if not verifier_code_pin(pause.employe_id, pause.code):
        raise HTTPException(status_code=401, detail="Code PIN incorrect")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    if pause.type_pause == "debut":
        # Enregistrer le début de la pause
        c.execute('''
            UPDATE pointages 
            SET heure_pause_debut = ? 
            WHERE employe_id = ? AND date_journee = ?
        ''', (pause.heure, pause.employe_id, pause.date_journee))
        message = "Pause démarrée. Profite bien de ta pause !"
    elif pause.type_pause == "fin":
        # Enregistrer la fin de la pause et calculer la durée
        c.execute('''
            SELECT heure_pause_debut FROM pointages WHERE employe_id = ? AND date_journee = ?
        ''', (pause.employe_id, pause.date_journee))
        row = c.fetchone()
        
        if row and row["heure_pause_debut"]:
            debut_pause = datetime.strptime(row["heure_pause_debut"], "%H:%M")
            fin_pause = datetime.strptime(pause.heure, "%H:%M")
            duree_pause = int((fin_pause - debut_pause).total_seconds() / 60)
            
            c.execute('''
                UPDATE pointages 
                SET heure_pause_fin = ?, duree_pause_minutes = ? 
                WHERE employe_id = ? AND date_journee = ?
            ''', (pause.heure, duree_pause, pause.employe_id, pause.date_journee))
            message = f"Pause terminée. Durée: {duree_pause} minutes."
        else:
            conn.close()
            raise HTTPException(status_code=400, detail="Aucune pause en cours trouvée")
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Type de pause invalide (doit être 'debut' ou 'fin')")
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": message,
        "heure": pause.heure
    }

@app.post("/employe/renouveler-code")
async def renouveler_code(request: RenouvelerCodeRequest):
    """
    Génère un nouveau code PIN pour un employé (admin uniquement).
    """
    # TODO: Ajouter vérification admin
    import random
    nouveau_code = str(random.randint(1000, 9999))
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Désactiver l'ancien code
    c.execute('''
        UPDATE codes_pointage SET actif = 0 WHERE employe_id = ?
    ''', (request.employe_id,))
    
    # Insérer le nouveau code
    c.execute('''
        INSERT INTO codes_pointage (employe_id, code, date_creation)
        VALUES (?, ?, ?)
    ''', (request.employe_id, nouveau_code, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": "Code PIN renouvelé avec succès",
        "nouveau_code": nouveau_code
    }

@app.get("/employes/{employe_id}/taches")
async def get_taches_employe(employe_id: int, date: Optional[str] = None):
    """Tâches ménagères réalisées par un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    if date:
        c.execute('''
            SELECT ht.*, tm.nom as tache_nom, tm.zone 
            FROM historique_taches ht 
            JOIN taches_menageres tm ON ht.tache_id = tm.id 
            WHERE ht.employe_id = ? AND ht.date_realisation = ? 
            ORDER BY ht.heure_debut
        ''', (employe_id, date))
    else:
        c.execute('''
            SELECT ht.*, tm.nom as tache_nom, tm.zone 
            FROM historique_taches ht 
            JOIN taches_menageres tm ON ht.tache_id = tm.id 
            WHERE ht.employe_id = ? 
            ORDER BY ht.date_realisation DESC
        ''', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/employes/taches/complete")
async def completer_tache(tache: TacheComplete):
    """Marque une tâche ménagère comme complétée"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO historique_taches (employe_id, tache_id, date_realisation, heure_debut, heure_fin, duree_minutes, note, commentaire)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (tache.employe_id, tache.tache_id, datetime.now().date().isoformat(), tache.heure_debut, tache.heure_fin, tache.duree_minutes, tache.note, tache.commentaire))
    conn.commit()
    conn.close()
    return {"message": "Tâche complétée et enregistrée"}

@app.get("/employes/{employe_id}/notes-clients")
async def get_notes_clients(employe_id: int):
    """Notes clients d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM notes_clients WHERE employe_id = ? ORDER BY created_at DESC', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/notes-clients")
async def create_note_client(note: NoteClientCreate):
    """Ajoute une note client avec 4 critères"""
    # Validation des notes 1-5
    for critere, valeur in [("accueil", note.accueil), ("qualite", note.qualite), ("proprete", note.proprete), ("ambiance", note.ambiance)]:
        if not 1 <= valeur <= 5:
            raise HTTPException(status_code=400, detail=f"La note '{critere}' doit être entre 1 et 5")
    
    note_moyenne = round((note.accueil + note.qualite + note.proprete + note.ambiance) / 4, 2)
    
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        recompense = "badge" if note_moyenne >= 4.5 else None
        c.execute('''
            INSERT INTO notes_clients (client_id, client_nom, employe_id, rendez_vous_id, date_rdv, service, 
                                       accueil, qualite, proprete, ambiance, note_moyenne, commentaire, recompense)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (note.client_id, note.client_nom, note.employe_id, note.rendez_vous_id, 
              note.date_rdv, note.service, note.accueil, note.qualite, note.proprete, note.ambiance,
              note_moyenne, note.commentaire, recompense))
        conn.commit()
        note_id = c.lastrowid
        
        # === ALGORITHME GOOGLE AVIS (Section G) ===
        if note_moyenne >= 4:
            # 4-5/5 : SMS remerciement + lien Google Avis
            if note.client_telephone:
                try:
                    # Générer un lien unique (ou utiliser le lien Google Avis du salon)
                    google_review_link = "https://search.google.com/local/writereview?placeid=ChIJ..."  # À configurer avec le vrai place ID
                    
                    # Envoi SMS via Twilio (si configuré)
                    voice_connector.send_sms(
                        note.client_telephone,
                        f"Merci {note.client_nom} pour votre visite chez Kadio Coiffure ! Votre avis compte. Partagez votre expérience ici : {google_review_link}"
                    )
                except Exception as e:
                    logger.warning(f"SMS Google Avis non envoyé : {e}")
            
            notification_manager.alerte_systeme(
                titre="🌟 Excellente note client",
                description=f"{note.client_nom} a donné {note_moyenne}/5. Lien Google Avis envoyé.",
                niveau="info"
            )
            
        elif note_moyenne <= 3:
            # 1-3/5 : PAS de lien Google, alerte CRITIQUE au propriétaire
            notification_manager.alerte_systeme(
                titre="🚨 ALERTE CRITIQUE — Note client faible",
                description=f"{note.client_nom} : {note_moyenne}/5 (Accueil: {note.accueil}, Qualité: {note.qualite}, Propreté: {note.proprete}, Ambiance: {note.ambiance}). Commentaire: {note.commentaire or 'Aucun'}. AUCUN lien Google Avis envoyé. Gestion de crise requise.",
                niveau="critique"
            )
        
        return {"success": True, "id": note_id, "note_moyenne": note_moyenne, "message": "Note client enregistrée", "google_avis_envoye": note_moyenne >= 4}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")
    finally:
        conn.close()

@app.get("/employes/{employe_id}/sanctions")
async def get_sanctions(employe_id: int):
    """Sanctions d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM sanctions WHERE employe_id = ? ORDER BY date_sanction DESC', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/sanctions")
async def create_sanction(sanction: SanctionCreate):
    """Crée une sanction"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier si retrait_points existe dans le modèle
    retrait_points = getattr(sanction, 'retrait_points', 0) or 0
    
    c.execute('''
        INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, duree_suspension_jours, nombre_tard)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (sanction.employe_id, sanction.type, sanction.raison, sanction.details, 
          sanction.date_sanction or datetime.now().strftime("%Y-%m-%d"), 
          sanction.duree_suspension_jours or 0, retrait_points))
    conn.commit()
    sanction_id = c.lastrowid
    conn.close()
    
    # Si sanction sévère, notifier
    if sanction.type in ['blame', 'suspension']:
        notification_manager.alerte_systeme(
            titre=f"🚨 Sanction {sanction.type}",
            description=f"Employé {sanction.employe_id}: {sanction.raison}",
            niveau="danger"
        )
    
    return {"success": True, "id": sanction_id, "message": "Sanction enregistrée"}

@app.get("/alertes")
async def list_alertes(statut: Optional[str] = None):
    """Liste des alertes"""
    conn = get_db_connection()
    c = conn.cursor()
    if statut:
        c.execute('SELECT * FROM alertes WHERE statut = ? ORDER BY date_alerte DESC', (statut,))
    else:
        c.execute('SELECT * FROM alertes ORDER BY date_alerte DESC')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Alias pour le cahier des charges
@app.get("/admin/alertes")
async def list_alertes_admin(statut: Optional[str] = None):
    """Alias /admin/alertes → /alertes"""
    return await list_alertes(statut)

@app.post("/alertes")
async def create_alerte(alerte: AlerteCreate):
    """Crée une alerte"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
        VALUES (?, ?, ?, ?, ?, 'nouveau')
    ''', (alerte.employe_id, alerte.type, alerte.description, alerte.niveau, alerte.date_alerte))
    alerte_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"success": True, "message": "Alerte créée", "id": alerte_id}

@app.get("/taches-menageres")
async def list_taches(zone: Optional[str] = None, frequence: Optional[str] = None):
    """Liste des tâches ménagères"""
    conn = get_db_connection()
    c = conn.cursor()
    if zone and frequence:
        c.execute('SELECT * FROM taches_menageres WHERE zone = ? AND frequence = ? AND actif = 1', (zone, frequence))
    elif zone:
        c.execute('SELECT * FROM taches_menageres WHERE zone = ? AND actif = 1', (zone,))
    elif frequence:
        c.execute('SELECT * FROM taches_menageres WHERE frequence = ? AND actif = 1', (frequence,))
    else:
        c.execute('SELECT * FROM taches_menageres WHERE actif = 1')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/echelons")
async def list_echelons():
    """Liste des échelons"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM echelons ORDER BY salaire_min')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/employes-mois")
async def list_employes_mois(annee: Optional[int] = None, mois: Optional[str] = None):
    """Historique des employés du mois"""
    conn = get_db_connection()
    c = conn.cursor()
    if annee and mois:
        c.execute('SELECT * FROM employes_mois WHERE annee = ? AND mois = ? ORDER BY score_total DESC', (annee, mois))
    elif annee:
        c.execute('SELECT * FROM employes_mois WHERE annee = ? ORDER BY score_total DESC', (annee,))
    else:
        c.execute('SELECT * FROM employes_mois ORDER BY annee DESC, mois DESC')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Alias pour le cahier des charges
@app.get("/employe-mois/calcul")
async def employe_mois_calcul_alias(mois: Optional[str] = None):
    """Alias: /employe-mois/calcul → redirection vers /employe-du-mois"""
    return await get_employe_du_mois()

@app.get("/admin/stats")
async def admin_stats_alias():
    """Alias: /admin/stats → redirection vers /dashboard/stats"""
    return await dashboard_stats()

@app.get("/employe/{employe_id}/page")
async def page_employe_perso(employe_id: int, telephone: str = None, code: str = None):
    """
    Page employé numérique personnelle (toutes les données de l'employé).
    Nécessite téléphone + code PIN pour isolation des données (pas d'accès aux données d'un collègue).
    """
    # Vérification d'isolation : téléphone + code PIN obligatoires
    if not telephone or not code:
        raise HTTPException(status_code=403, detail="Authentification requise : téléphone + code PIN")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier que le téléphone et code correspondent à l'employé demandé
    c.execute("SELECT id FROM employes WHERE id = ? AND telephone = ?", (employe_id, telephone))
    employe_match = c.fetchone()
    
    # Vérifier le PIN
    c.execute('SELECT id FROM pins_employes WHERE employe_id = ? AND pin = ?', (employe_id, code))
    pin_ok = c.fetchone()
    
    if not employe_match or not pin_ok:
        conn.close()
        raise HTTPException(status_code=403, detail="Accès refusé : vous ne pouvez accéder qu'à vos propres données.")
    
    # Infos employé
    c.execute("SELECT * FROM employes WHERE id = ?", (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    # Pointages
    c.execute('SELECT * FROM pointages WHERE employe_id = ? ORDER BY date_journee DESC LIMIT 30', (employe_id,))
    pointages = [dict(r) for r in c.fetchall()]
    
    # Notes clients
    c.execute('SELECT * FROM notes_clients WHERE employe_id = ? ORDER BY created_at DESC LIMIT 20', (employe_id,))
    notes = [dict(r) for r in c.fetchall()]
    
    # Sanctions
    c.execute('SELECT * FROM sanctions WHERE employe_id = ? ORDER BY date_sanction DESC LIMIT 10', (employe_id,))
    sanctions = [dict(r) for r in c.fetchall()]
    
    # Badges
    c.execute('SELECT * FROM badges_employes WHERE employe_id = ? ORDER BY date_attribution DESC', (employe_id,))
    badges = [dict(r) for r in c.fetchall()]
    
    # Score du mois
    c.execute('''
        SELECT * FROM employes_mois 
        WHERE employe_id = ? ORDER BY annee DESC, mois DESC LIMIT 1
    ''', (employe_id,))
    score_mois = c.fetchone()
    
    # Calculer la position au classement
    c.execute('''
        SELECT e.id, e.nom,
               COALESCE(AVG(nc.note_moyenne), 0) * 10 as score_notes,
               COUNT(DISTINCT ht.id) * 5 as score_taches
        FROM employes e
        LEFT JOIN notes_clients nc ON e.id = nc.employe_id
        LEFT JOIN historique_taches ht ON e.id = ht.employe_id
        WHERE e.statut = 'actif'
        GROUP BY e.id
        ORDER BY (score_notes + score_taches) DESC
    ''')
    rows = c.fetchall()
    position = None
    for i, row in enumerate(rows, 1):
        if row["id"] == employe_id:
            position = i
            break
    
    # Récupérer la charte réglementaire
    regles = REGLES_SALON.get("regles", [])
    
    conn.close()
    
    return {
        "employe": dict(employe),
        "pointages": pointages,
        "notes_clients": notes,
        "sanctions": sanctions,
        "badges": badges,
        "score_mois": dict(score_mois) if score_mois else None,
        "position_classement": position,
        "total_employes": len(rows),
        "charte_reglementaire": regles,
        "acces": "page_perso"
    }

@app.get("/dashboard/stats")
async def dashboard_stats():
    """Stats globales du dashboard"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Nombre d'employés actifs
    c.execute("SELECT COUNT(*) FROM employes WHERE statut = 'actif'")
    total_employes = c.fetchone()[0]
    
    # Nombre de pointages aujourd'hui
    today = datetime.now().date().isoformat()
    c.execute("SELECT COUNT(*) FROM pointages WHERE date_journee = ?", (today,))
    total_pointages = c.fetchone()[0]
    
    # Tâches complétées aujourd'hui
    c.execute("SELECT COUNT(*) FROM historique_taches WHERE date_realisation = ?", (today,))
    total_taches = c.fetchone()[0]
    
    # Alertes actives
    c.execute("SELECT COUNT(*) FROM alertes WHERE statut = 'nouveau'")
    alertes_nouvelles = c.fetchone()[0]
    
    # Notes clients moyennes (note_moyenne, pas note)
    c.execute("SELECT AVG(note_moyenne) FROM notes_clients WHERE date(created_at) = ?", (today,))
    avg_note = c.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "total_employes": total_employes,
        "pointages_aujourd_hui": total_pointages,
        "taches_completees_aujourd_hui": total_taches,
        "alertes_nouvelles": alertes_nouvelles,
        "note_client_moyenne_jour": round(avg_note, 2),
        "date": today
    }

async def handle_stats_command() -> dict:
    """Renvoie les stats du jour"""
    return await square.get_daily_stats()

async def handle_clients_command() -> dict:
    """Liste les clients du jour"""
    # TODO: implémenter
    return {"status": "not_implemented"}

# ========== ROUTES NOTES CLIENTS + SCORING ==========

@app.post("/notes-clients")
async def add_note_client(note: NoteClientCreate):
    """Ajoute une note client"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Insérer la note
    c.execute('''
        INSERT INTO notes_clients (employe_id, client_nom, note, commentaire)
        VALUES (?, ?, ?, ?)
    ''', (note.employe_id, note.client_nom, note.note, note.commentaire))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Note ajoutée", "points": note.note * 10}

@app.get("/notes-clients/employe/{employe_id}")
async def get_notes_employe(employe_id: int):
    """Récupère les notes d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT nc.*, e.nom as employe_nom FROM notes_clients nc
        JOIN employes e ON nc.employe_id = e.id
        WHERE nc.employe_id = ? ORDER BY nc.created_at DESC
    ''', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/classement/employes")
async def classement_employes(period: str = "mois"):
    """
    Classement des employés par score.
    period: 'jour', 'semaine', 'mois', 'all'
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    # Calculer le score total pour chaque employé (score = notes * 10 + taches * 5)
    c.execute('''
        SELECT e.id, e.nom, e.role, e.specialite, e.echelon,
               COALESCE(AVG(nc.note_moyenne), 0) * 10 as score_notes,
               COUNT(DISTINCT ht.id) * 5 as score_taches,
               COUNT(DISTINCT p.id) as total_pointages,
               COUNT(DISTINCT nc.id) as total_notes,
               COALESCE(AVG(nc.note_moyenne), 0) as note_moyenne
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
    
    classement = []
    for i, row in enumerate(rows, 1):
        score_total = (row["score_notes"] or 0) + (row["score_taches"] or 0)
        classement.append({
            "rang": i,
            "id": row["id"],
            "nom": row["nom"],
            "role": row["role"],
            "specialite": row["specialite"],
            "echelon": row["echelon"],
            "score_total": round(score_total, 2),
            "total_pointages": row["total_pointages"] or 0,
            "total_notes": row["total_notes"] or 0,
            "note_moyenne": round(row["note_moyenne"] or 0, 2)
        })
    
    return classement

@app.post("/employe-du-mois/calculer")
async def calculer_employe_du_mois(annee: int = None, mois: int = None, auto: bool = False):
    """
    Calcule l'employé du mois selon Section J:
    - Notes clients: 35%
    - Ponctualité (Pointage à 8h55): 25%
    - Tâches ménagères validées: 20%
    - Checklist d'accueil client: 20%
    
    Si auto=True (appelé par cron le dernier jour à 23h59),
    l'employé est désigné mais en attente de validation propriétaire.
    """
    if annee is None:
        annee = datetime.now().year
    if mois is None:
        mois = datetime.now().month
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer tous les employés actifs
    c.execute("SELECT id, nom FROM employes WHERE statut = 'actif'")
    employes = c.fetchall()
    
    meilleur_employe = None
    meilleur_score = -1
    tous_scores = []
    
    mois_str = f"{annee}-{mois:02d}"
    
    for emp in employes:
        emp_id = emp["id"]
        emp_nom = emp["nom"]
        
        # 1. Notes clients (35%)
        c.execute('''
            SELECT AVG(note_moyenne) as avg_note, COUNT(*) as count_notes
            FROM notes_clients
            WHERE employe_id = ? AND strftime('%Y-%m', created_at) = ?
        ''', (emp_id, mois_str))
        note_data = c.fetchone()
        avg_note = note_data["avg_note"] or 0
        count_notes = note_data["count_notes"] or 0
        score_notes = (avg_note / 5) * 35 if count_notes > 0 else 0
        
        # 2. Ponctualité (25%) — Pointage à 8h55 = pas de retard
        c.execute('''
            SELECT COUNT(*) as total,
                   COUNT(CASE WHEN retard_minutes = 0 THEN 1 END) as ponctuel
            FROM pointages
            WHERE employe_id = ? AND strftime('%Y-%m', date_journee) = ?
        ''', (emp_id, mois_str))
        pointage_data = c.fetchone()
        total_pointages = pointage_data["total"] or 0
        ponctuel = pointage_data["ponctuel"] or 0
        score_ponctualite = (ponctuel / total_pointages) * 25 if total_pointages > 0 else 0
        
        # 3. Tâches ménagères (20%)
        c.execute('''
            SELECT COUNT(*) as total_taches
            FROM historique_taches
            WHERE employe_id = ? AND strftime('%Y-%m', date_realisation) = ?
        ''', (emp_id, mois_str))
        taches_data = c.fetchone()
        total_taches = taches_data["total_taches"] or 0
        score_taches = min(total_taches * 2, 20)  # Max 20 points
        
        # 4. Checklist d'accueil (20%)
        c.execute('''
            SELECT AVG(score_checklist) as avg_checklist, COUNT(*) as count_checklist
            FROM checklist_service
            WHERE employe_id = ? AND strftime('%Y-%m', date_service) = ?
        ''', (emp_id, mois_str))
        checklist_data = c.fetchone()
        avg_checklist = checklist_data["avg_checklist"] or 0
        count_checklist = checklist_data["count_checklist"] or 0
        score_checklist = (avg_checklist / 10) * 20 if count_checklist > 0 else 0
        
        # Score total sur 100
        score_total = score_notes + score_ponctualite + score_taches + score_checklist
        
        emp_result = {
            "id": emp_id,
            "nom": emp_nom,
            "score_total": round(score_total, 2),
            "breakdown": {
                "notes_clients": round(score_notes, 2),
                "ponctualite": round(score_ponctualite, 2),
                "taches_menageres": round(score_taches, 2),
                "checklist_accueil": round(score_checklist, 2)
            },
            "details": {
                "note_moyenne": round(avg_note, 2),
                "total_notes": count_notes,
                "total_pointages": total_pointages,
                "ponctuel_count": ponctuel,
                "total_taches": total_taches,
                "checklist_moyenne": round(avg_checklist, 2),
                "total_checklists": count_checklist
            }
        }
        
        tous_scores.append(emp_result)
        
        if score_total > meilleur_score:
            meilleur_score = score_total
            meilleur_employe = emp_result
    
    # Trier par score décroissant pour le classement
    tous_scores.sort(key=lambda x: x["score_total"], reverse=True)
    for i, emp in enumerate(tous_scores, 1):
        emp["rang"] = i
    
    # Enregistrer l'employé du mois en attente de validation
    if meilleur_employe:
        c.execute('''
            INSERT OR REPLACE INTO employes_mois
            (annee, mois, employe_id, score_total, raison, statut_validation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (annee, mois_str, meilleur_employe["id"],
              meilleur_employe["score_total"],
              f"Employe du mois {mois}/{annee} - Score: {meilleur_employe['score_total']}/100",
              "en_attente" if auto else "valide",
              datetime.now().isoformat()))
        conn.commit()
    
    conn.close()
    
    result = {
        "success": True,
        "annee": annee,
        "mois": mois,
        "calcul_auto": auto,
        "employe_du_mois": meilleur_employe,
        "classement_complet": tous_scores,
        "statut": "en_attente_validation_proprietaire" if auto else "valide"
    }
    
    # Si calcul auto (cron), envoyer notification au propriétaire pour validation
    if auto and meilleur_employe:
        try:
            message = f"🏆 EMPLOYE DU MOIS A VALIDER\n\n"
            message += f"{meilleur_employe['nom']} a obtenu le meilleur score ({meilleur_employe['score_total']}/100) pour {mois}/{annee}.\n"
            message += f"\nBreakdown:\n"
            message += f"• Notes clients: {meilleur_employe['breakdown']['notes_clients']}/35\n"
            message += f"• Ponctualite: {meilleur_employe['breakdown']['ponctualite']}/25\n"
            message += f"• Taches: {meilleur_employe['breakdown']['taches_menageres']}/20\n"
            message += f"• Checklist: {meilleur_employe['breakdown']['checklist_accueil']}/20\n"
            message += f"\nValidez sur /employe-du-mois/valider"

            send_sms_alert(TELEPHONE_PROPRIETAIRE, message)
            result["notification_proprietaire"] = "envoyee"
        except Exception as e:
            result["notification_proprietaire"] = f"erreur: {str(e)}"
    
    return result

class ValidationEmployeMois(BaseModel):
    annee: int
    mois: int
    valide: bool = True
    commentaire: Optional[str] = None

@app.post("/employe-du-mois/valider")
async def valider_employe_du_mois(data: ValidationEmployeMois):
    """
    Validation sécuritaire par le propriétaire de l'employé du mois.
    Après validation : notification à l'équipe + carte-cadeau 50$ + mention permanente.
    """
    if not data.valide:
        return {"success": False, "message": "Validation refusée par le propriétaire"}
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier qu'il y a un employé en attente
    c.execute('''
        SELECT em.*, e.nom, e.telephone
        FROM employes_mois em
        JOIN employes e ON em.employe_id = e.id
        WHERE em.annee = ? AND em.mois = ? AND em.statut_validation = 'en_attente'
    ''', (data.annee, f"{data.annee}-{data.mois:02d}"))
    row = c.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Aucun employé du mois en attente de validation")
    
    emp_id = row["employe_id"]
    emp_nom = row["nom"]
    emp_tel = row["telephone"]
    score = row["score_total"]
    
    # 1. Valider l'employé du mois
    c.execute('''
        UPDATE employes_mois
        SET statut_validation = 'valide', date_validation = ?, commentaire_validation = ?
        WHERE annee = ? AND mois = ?
    ''', (datetime.now().isoformat(), data.commentaire or "", data.annee, f"{data.annee}-{data.mois:02d}"))
    
    # 2. Mention permanente dans le dossier employé (badge spécial)
    c.execute('''
        INSERT INTO badges_employes (employe_id, badge_id, date_attribution, raison)
        VALUES (?, (SELECT id FROM badges WHERE nom = "Employe du Mois"), ?, ?)
    ''', (emp_id, datetime.now().strftime("%Y-%m-%d"),
           f"Employe du mois {data.mois}/{data.annee} - Score: {score}/100"))
    
    # 3. Attribuer carte-cadeau 50$
    c.execute('''
        INSERT INTO recompenses (employe_id, type, montant, description, raison, date_attribution)
        VALUES (?, 'carte_cadeau', 50, 'Carte-cadeau Employe du Mois', ?, ?)
    ''', (emp_id, f"Employe du mois {data.mois}/{data.annee}", datetime.now().strftime("%Y-%m-%d")))
    
    conn.commit()
    
    # 4. Notification à l'équipe
    message_equipe = f"🎉 FELICITATIONS {emp_nom} !\n\n"
    message_equipe += f"Tu as ete designe(e) EMPLOYE(E) DU MOIS {data.mois}/{data.annee} !\n"
    message_equipe += f"Score: {score}/100\n"
    message_equipe += f"\n🏆 Une carte-cadeau de 50$ t'attend !\n"
    message_equipe += f"Continue comme ca ! 💪"
    
    # Envoyer à tous les employés actifs
    c.execute("SELECT telephone FROM employes WHERE statut = 'actif' AND telephone IS NOT NULL")
    for emp in c.fetchall():
        if emp["telephone"]:
            try:
                send_sms_alert(emp["telephone"], message_equipe)
            except:
                pass
    
    # 5. Notification spéciale au gagnant
    if emp_tel:
        try:
            send_sms_alert(emp_tel, message_equipe)
        except:
            pass
    
    conn.close()
    
    return {
        "success": True,
        "message": f"{emp_nom} valide comme Employe du Mois {data.mois}/{data.annee}",
        "recompense": "Carte-cadeau 50$",
        "notification_equipe": "envoyee"
    }

@app.get("/employe-du-mois")
async def get_employe_du_mois(annee: int = None, mois: int = None):
    """Récupère l'employé du mois"""
    if annee is None:
        annee = datetime.now().year
    if mois is None:
        mois = datetime.now().month
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM employes_mois WHERE annee = ? AND mois = ?
    ''', (annee, mois))
    row = c.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return {"message": "Pas encore d'employé du mois pour cette période"}

async def chat_with_agent(command: str, user_id: str) -> dict:
    """Chat libre avec l'agent Kimi"""
    # TODO: implémenter
    return {"status": "not_implemented"}

# ========== ROUTES NOTIFICATIONS ==========

@app.post("/notifications/alerte-retard")
async def alerte_retard(request: AlertRetardRequest):
    """Alerte retard employé"""
    result = notification_manager.alerte_retard(
        employe_nom=request.employe_nom,
        employe_phone=request.employe_phone,
        heure_arrivee=request.heure_arrivee,
        retard_minutes=request.retard_minutes
    )
    return {"success": True, "notification": result}

@app.post("/notifications/alerte-absence")
async def alerte_absence(request: AlertAbsenceRequest):
    """Alerte absence employé"""
    result = notification_manager.alerte_absence(
        employe_nom=request.employe_nom,
        employe_phone=request.employe_phone
    )
    return {"success": True, "notification": result}

@app.post("/notifications/recap-quotidien")
async def recap_quotidien(request: RecapQuotidienRequest):
    """Envoi le récapitulatif quotidien au patron"""
    result = notification_manager.recapitulatif_quotidien(
        stats=request.dict()
    )
    return {"success": True, "notification": result}

@app.post("/notifications/employe-du-mois")
async def notif_employe_du_mois(request: EmployeDuMoisRequest):
    """Notification employé du mois"""
    result = notification_manager.alerte_employe_du_mois(
        employe_nom=request.employe_nom,
        employe_phone=request.employe_phone,
        score=request.score
    )
    return {"success": True, "notification": result}

@app.post("/notifications/rappel-rdv")
async def rappel_rdv(request: RappelRdvRequest):
    """Rappel de rendez-vous au client"""
    result = notification_manager.rappel_rdv_client(
        client_phone=request.client_phone,
        client_nom=request.client_nom,
        date=request.date,
        heure=request.heure,
        service=request.service
    )
    return {"success": True, "notification": result}

@app.post("/notifications/confirmation-rdv")
async def confirmation_rdv(request: ConfirmationRdvRequest):
    """Confirmation de rendez-vous au client"""
    result = notification_manager.confirmation_rdv(
        client_phone=request.client_phone,
        client_nom=request.client_nom,
        date=request.date,
        heure=request.heure,
        service=request.service,
        prix=request.prix
    )
    return {"success": True, "notification": result}

@app.post("/notifications/alerte-systeme")
async def alerte_systeme(request: AlerteSystemeRequest):
    """Alerte système générale"""
    result = notification_manager.alerte_systeme(
        titre=request.titre,
        description=request.description,
        niveau=request.niveau
    )
    return {"success": True, "notification": result}

# ========== ROUTES EXPORT PDF ET CSV ==========

@app.get("/export/employes/csv")
async def export_employes_csv_route():
    """Exporte la liste des employés en CSV"""
    csv_data = export_employes_csv()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=employes_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@app.get("/export/employes/pdf")
async def export_employes_pdf_route():
    """Exporte la liste des employés en PDF"""
    pdf_data = export_employes_pdf()
    return Response(
        content=bytes(pdf_data) if isinstance(pdf_data, bytearray) else pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=employes_{datetime.now().strftime('%Y%m%d')}.pdf"}
    )

@app.get("/export/pointages/csv")
async def export_pointages_csv_route(date_debut: Optional[str] = None, date_fin: Optional[str] = None):
    """Exporte les pointages en CSV (optionnellement filtré par date)"""
    csv_data = export_pointages_csv(date_debut, date_fin)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pointages_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@app.get("/export/pointages/pdf")
async def export_pointages_pdf_route(date_debut: Optional[str] = None, date_fin: Optional[str] = None):
    """Exporte les pointages en PDF (optionnellement filtré par date)"""
    pdf_data = export_pointages_pdf(date_debut, date_fin)
    return Response(
        content=bytes(pdf_data) if isinstance(pdf_data, bytearray) else pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=pointages_{datetime.now().strftime('%Y%m%d')}.pdf"}
    )

@app.get("/export/notes/csv")
async def export_notes_csv_route():
    """Exporte les notes clients en CSV"""
    csv_data = export_notes_csv()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=notes_clients_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

@app.get("/export/classement/pdf")
async def export_classement_pdf_route():
    """Exporte le classement des employés en PDF"""
    pdf_data = export_classement_pdf()
    return Response(
        content=bytes(pdf_data) if isinstance(pdf_data, bytearray) else pdf_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=classement_{datetime.now().strftime('%Y%m%d')}.pdf"}
    )

# ========== CHECKLIST SERVICE CLIENT ==========

class ChecklistServiceCreate(BaseModel):
    employe_id: int
    date_service: str
    client_nom: Optional[str] = None
    service: Optional[str] = None
    sourire: int = 0
    guider: int = 0
    offrir_boisson: int = 0
    offrir_grignotine: int = 0
    gerer_attente: int = 0
    telephone_ranger: int = 0
    commentaire: Optional[str] = None

@app.post("/checklist-service")
async def create_checklist(checklist: ChecklistServiceCreate):
    """Crée une checklist de service client"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        # Calcul du score (6 points max = 10 sur 10)
        points = sum([
            checklist.sourire, checklist.guider, checklist.offrir_boisson,
            checklist.offrir_grignotine, checklist.gerer_attente, checklist.telephone_ranger
        ])
        score = (points / 6.0) * 10  # sur 10
        
        c.execute('''
            INSERT INTO checklist_service 
            (employe_id, date_service, client_nom, service, sourire, guider, offrir_boisson, 
             offrir_grignotine, gerer_attente, telephone_ranger, score_checklist, commentaire)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (checklist.employe_id, checklist.date_service, checklist.client_nom, checklist.service,
              checklist.sourire, checklist.guider, checklist.offrir_boisson, checklist.offrir_grignotine,
              checklist.gerer_attente, checklist.telephone_ranger, round(score, 2), checklist.commentaire))
        conn.commit()
        return {"success": True, "score": round(score, 2), "points": f"{points}/6"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")
    finally:
        conn.close()

@app.get("/checklist-service/employe/{employe_id}")
async def get_checklist_employe(employe_id: int, date_debut: Optional[str] = None, date_fin: Optional[str] = None):
    """Récupère les checklists d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if date_debut and date_fin:
        c.execute('''
            SELECT * FROM checklist_service 
            WHERE employe_id = ? AND date_service BETWEEN ? AND ?
            ORDER BY date_service DESC
        ''', (employe_id, date_debut, date_fin))
    else:
        c.execute('''
            SELECT * FROM checklist_service 
            WHERE employe_id = ? ORDER BY date_service DESC
        ''', (employe_id,))
    
    rows = c.fetchall()
    conn.close()
    
    return [{**dict(row)} for row in rows]

@app.get("/checklist-service/stats/{employe_id}")
async def stats_checklist_employe(employe_id: int, mois: Optional[str] = None):
    """Stats checklist d'un employé pour un mois (format YYYY-MM)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if mois:
        c.execute('''
            SELECT COUNT(*) as total, AVG(score_checklist) as moyenne,
                   SUM(sourire) as sourires, SUM(guider) as guides,
                   SUM(offrir_boisson) as boissons, SUM(offrir_grignotine) as grignotines,
                   SUM(gerer_attente) as attentes, SUM(telephone_ranger) as telephones
            FROM checklist_service
            WHERE employe_id = ? AND strftime('%Y-%m', date_service) = ?
        ''', (employe_id, mois))
    else:
        c.execute('''
            SELECT COUNT(*) as total, AVG(score_checklist) as moyenne,
                   SUM(sourire) as sourires, SUM(guider) as guides,
                   SUM(offrir_boisson) as boissons, SUM(offrir_grignotine) as grignotines,
                   SUM(gerer_attente) as attentes, SUM(telephone_ranger) as telephones
            FROM checklist_service
            WHERE employe_id = ?
        ''', (employe_id,))
    
    row = c.fetchone()
    conn.close()
    
    return {
        "total_checklists": row["total"] or 0,
        "score_moyen": round(row["moyenne"] or 0, 2),
        "sourires": row["sourires"] or 0,
        "guides": row["guides"] or 0,
        "boissons": row["boissons"] or 0,
        "grignotines": row["grignotines"] or 0,
        "attentes": row["attentes"] or 0,
        "telephones": row["telephones"] or 0
    }

# ========== DÉTECTION RETARD AUTO ==========

@app.post("/employe/pointer-arrivee-avec-retard")
async def pointer_arrivee_avec_retard(pointage: PointageAvecCode):
    """
    Pointage arrivée avec détection automatique du retard.
    Règles: Lun-Mar ouverture 12h, Mer-Dim ouverture 10h.
    Norme = arrivée à l'ouverture ou avant. Retard = après.
    """
    if not verifier_code_pin(pointage.employe_id, pointage.code):
        raise HTTPException(status_code=401, detail="Code PIN incorrect")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer le nom de l'employé pour les messages
    c.execute('SELECT nom FROM employes WHERE id = ?', (pointage.employe_id,))
    employe_row = c.fetchone()
    employe_nom = employe_row['nom'] if employe_row else "Employé"
    
    # Vérifier s'il y a déjà un pointage aujourd'hui
    c.execute('SELECT id FROM pointages WHERE employe_id = ? AND date_journee = ?', 
                (pointage.employe_id, pointage.date_journee))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Pointage déjà enregistré")
    
    # Déterminer l'heure d'ouverture selon le jour (cahier des charges)
    date_jour = datetime.strptime(pointage.date_journee, "%Y-%m-%d")
    jour_semaine = date_jour.strftime("%A")
    
    # Lun-Mar = 12h, Mer-Dim = 10h (selon horaires salon)
    heure_ouverture = 12 if jour_semaine in ["Monday", "Tuesday"] else 10
    minute_ouverture = 0
    
    # Calculer le retard (RÈGLE : 5 minutes avant = la norme)
    heure_arrivee = datetime.strptime(pointage.heure, "%H:%M")
    heure_arrivee_minutes = heure_arrivee.hour * 60 + heure_arrivee.minute
    ouverture_minutes = heure_ouverture * 60 + minute_ouverture
    norme_minutes = ouverture_minutes - 5  # Doit arriver 5 min AVANT
    
    retard_minutes = 0
    if heure_arrivee_minutes > norme_minutes:
        retard_minutes = heure_arrivee_minutes - norme_minutes
    
    # Si retard > 0, vérifier que la raison est fournie (OBLIGATOIRE selon cahier)
    if retard_minutes > 0:
        if not pointage.raison_retard or pointage.raison_retard.strip() == "":
            conn.close()
            raise HTTPException(
                status_code=400, 
                detail=f"Vous êtes en retard de {retard_minutes} minutes. Écrivez la raison avant de valider."
            )
        
        # Compter les retards du mois (compteur remis à zéro le 1er du mois)
        debut_mois = date_jour.strftime("%Y-%m-01")
        c.execute('''
            SELECT COUNT(*) FROM pointages 
            WHERE employe_id = ? AND date_journee >= ? AND retard_minutes > 0
        ''', (pointage.employe_id, debut_mois))
        retard_numero = c.fetchone()[0] + 1
        
        # Créer alerte SMS au propriétaire (format exact cahier des charges)
        c.execute('''
            INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
            VALUES (?, 'retard', ?, 'attention', ?, 'nouveau')
        ''', (pointage.employe_id, 
              f"{employe_nom} est arrivé(e) avec {retard_minutes} min de retard. Raison : {pointage.raison_retard}. Retard #{retard_numero} ce mois.",
              pointage.date_journee))
        
        # Appliquer sanctions progressives pour retards (CAHIER DES CHARGES SECTION E)
        # 1er retard mensuel = Niveau 1 (Avertissement + descente échelon)
        # 2ème retard mensuel = Niveau 2 (1 jour sans salaire + descente échelon)
        # 3ème retard mensuel = 2 jours sans salaire
        # Récidive mois suivant = séparation définitive
        
        # Vérifier s'il y a eu des sanctions retards le mois précédent (récidive)
        mois_precedent = (date_jour.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        c.execute('''
            SELECT COUNT(*) FROM sanctions 
            WHERE employe_id = ? AND date_sanction LIKE ? AND raison LIKE '%Retard%'
        ''', (pointage.employe_id, f"{mois_precedent}%"))
        sanctions_mois_precedent = c.fetchone()[0]
        
        if retard_numero == 1:
            # Niveau 1 : Avertissement écrit + archivage + descente échelon
            c.execute('''
                INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, niveau)
                VALUES (?, 'avertissement', ?, '1er retard du mois — Niveau 1', ?, 1)
            ''', (pointage.employe_id, 
                  f"Retard de {retard_minutes} min. Raison : {pointage.raison_retard}",
                  pointage.date_journee))
        elif retard_numero == 2:
            # Niveau 2 : Suspension 1 jour + second avertissement + descente échelon
            c.execute('''
                INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, duree_suspension_jours, niveau)
                VALUES (?, 'suspension', ?, '2ème retard du mois — Niveau 2', ?, 1, 2)
            ''', (pointage.employe_id, 
                  f"Retard de {retard_minutes} min. 1 journée sans salaire. Raison : {pointage.raison_retard}",
                  pointage.date_journee))
        elif retard_numero == 3:
            # 3ème retard = 2 jours sans salaire (pas de descente additionnelle, déjà 2 sanctions)
            c.execute('''
                INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, duree_suspension_jours, niveau)
                VALUES (?, 'suspension', ?, '3ème retard du mois — 2 jours sans salaire', ?, 2, 2)
            ''', (pointage.employe_id, 
                  f"Retard de {retard_minutes} min. 2 jours sans salaire. Raison : {pointage.raison_retard}",
                  pointage.date_journee))
        
        # Vérifier récidive : sanctions retards le mois précédent ET retard ce mois
        if sanctions_mois_precedent > 0 and retard_numero >= 1:
            # Niveau 3 : Rupture de contrat, verrouillage accès, rapport, alerte propriétaire
            c.execute('''
                INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, niveau)
                VALUES (?, 'licenciement', ?, 'Récidive — Niveau 3 (Rupture contrat)', ?, 3)
            ''', (pointage.employe_id, 
                  f"Récidive retards. Mois précédent : {sanctions_mois_precedent} sanction(s). Retard #{retard_numero} ce mois. Procédure de séparation définitive.",
                  pointage.date_journee))
            
            # Verrouiller les accès de l'employé
            c.execute('UPDATE employes SET statut = ? WHERE id = ?', ('suspendu', pointage.employe_id))
            
            # Générer rapport d'infractions complet
            c.execute('''
                SELECT s.*, e.nom, e.echelon, e.date_embauche
                FROM sanctions s
                JOIN employes e ON s.employe_id = e.id
                WHERE s.employe_id = ? AND s.type IN ('avertissement', 'suspension', 'licenciement')
                ORDER BY s.date_sanction DESC
            ''', (pointage.employe_id,))
            historique_sanctions = c.fetchall()
            
            rapport = f"""
RAPPORT D'INFRACTIONS — KADIO COIFFURE
=====================================
Employé : {employe_nom}
ID : {pointage.employe_id}
Échelon : {employe.get('echelon', 'N/A')}
Date embauche : {employe.get('date_embauche', 'N/A')}
Date du rapport : {datetime.now().strftime('%Y-%m-%d %H:%M')}

MOTIF : Récidive de retards — Procédure de séparation définitive

HISTORIQUE DES SANCTIONS :
"""
            for s in historique_sanctions:
                rapport += f"- {s['date_sanction']} : {s['type'].upper()} — {s['raison']}\n"
            
            rapport += f"""

DÉCISION : Licenciement définitif
Accès verrouillés : {datetime.now().strftime('%Y-%m-%d %H:%M')}

Document généré automatiquement par le système Kadio Cerveau.
"""
            
            # Sauvegarder le rapport
            c.execute('''
                INSERT INTO documents_sanctions (employe_id, type_document, contenu, date_generation)
                VALUES (?, 'rapport_infractions', ?, ?)
            ''', (pointage.employe_id, rapport, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            # Alerte critique au propriétaire
            notification_manager.alerte_systeme(
                titre=f"🚨 LICENCIEMENT — {employe_nom}",
                description=f"Récidive retards. {sanctions_mois_precedent} sanction(s) mois précédent. Retard #{retard_numero} ce mois. Accès verrouillés. Rapport généré.",
                niveau="critique"
            )
    
    # Insérer le pointage avec retard et raison
    c.execute('''
        INSERT INTO pointages (employe_id, date_journee, heure_arrivee, retard_minutes, 
                               latitude, longitude, adresse_pointage, statut, code_utilise, raison_retard)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'incomplet', ?, ?)
    ''', (pointage.employe_id, pointage.date_journee, pointage.heure, retard_minutes,
          pointage.latitude, pointage.longitude, pointage.adresse_pointage, pointage.code, pointage.raison_retard))
    
    conn.commit()
    conn.close()
    
    if retard_minutes > 0:
        return {
            "success": True,
            "message": f"Arrivée enregistrée. Vous êtes en retard de {retard_minutes} minutes.",
            "heure_arrivee": pointage.heure,
            "retard_minutes": retard_minutes,
            "heure_ouverture": f"{heure_ouverture:02d}:{minute_ouverture:02d}",
            "norme": "Arriver 5 minutes avant l'heure officielle"
        }
    else:
        return {
            "success": True,
            "message": f"Bonjour, vous êtes arrivé(e) à l'heure. ✅",
            "heure_arrivee": pointage.heure,
            "retard_minutes": 0,
            "heure_ouverture": f"{heure_ouverture:02d}:{minute_ouverture:02d}"
        }

# ========== ÉCHELONS AUTO (CAHIER DES CHARGES SECTION D) ==========

@app.post("/echelons/verifier/{employe_id}")
async def verifier_echelon_employe(employe_id: int):
    """
    Vérifie et met à jour l'échelon selon le cahier des charges Section D :
    
    MONTEES :
    - Bronze → Argent : 3 semaines sans sanction/retard + note moyenne ≥ 4/5
    - Argent → Or : 6 semaines sans sanction/retard + note moyenne > 4/5 → prime 20$
    - Or → Platine : maintien Or 2 mois + Employé du Mois ≥ 1 fois → prime 50$ + bonus 50$/3mois
    
    DESCENTES :
    - Toute sanction = -1 échelon immédiat
    - 2 retards/mois = -1 échelon
    - 3 notes < 3/5 sur 30 jours = -1 échelon
    - 3 tâches ménagères non validées (collectif) = -1 échelon pour toute l'équipe
    - 3e sanction = retour Bronze total (destitution)
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM employes WHERE id = ? AND statut = 'actif'", (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    echelon_actuel = employe["echelon"]
    
    # Dates pour les vérifications
    date_21j = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
    date_42j = (datetime.now() - timedelta(days=42)).strftime("%Y-%m-%d")
    date_60j = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    debut_mois = datetime.now().strftime("%Y-%m-01")
    
    # === VÉRIFICATIONS COMMUNES ===
    
    # Retards sur 21 jours (3 semaines)
    c.execute("""
        SELECT COUNT(*) FROM pointages 
        WHERE employe_id = ? AND date_journee >= ? AND retard_minutes > 0
    """, (employe_id, date_21j))
    retards_21j = c.fetchone()[0]
    
    # Retards sur 42 jours (6 semaines)
    c.execute("""
        SELECT COUNT(*) FROM pointages 
        WHERE employe_id = ? AND date_journee >= ? AND retard_minutes > 0
    """, (employe_id, date_42j))
    retards_42j = c.fetchone()[0]
    
    # Retards ce mois
    c.execute("""
        SELECT COUNT(*) FROM pointages 
        WHERE employe_id = ? AND date_journee >= ? AND retard_minutes > 0
    """, (employe_id, debut_mois))
    retards_mois = c.fetchone()[0]
    
    # Note MOYENNE sur 21 jours (pas nombre de notes)
    c.execute("""
        SELECT AVG(note_moyenne) FROM notes_clients 
        WHERE employe_id = ? AND date_note >= ?
    """, (employe_id, date_21j))
    moyenne_21j = c.fetchone()[0] or 0
    
    # Note MOYENNE sur 42 jours
    c.execute("""
        SELECT AVG(note_moyenne) FROM notes_clients 
        WHERE employe_id = ? AND date_note >= ?
    """, (employe_id, date_42j))
    moyenne_42j = c.fetchone()[0] or 0
    
    # Sanctions actives
    c.execute("""
        SELECT COUNT(*) FROM sanctions 
        WHERE employe_id = ? AND statut = 'actif' AND type IN ('avertissement', 'blame', 'suspension')
    """, (employe_id,))
    sanctions_actives = c.fetchone()[0]
    
    # Mauvaises notes ce mois (< 3/5)
    c.execute("""
        SELECT COUNT(*) FROM notes_clients 
        WHERE employe_id = ? AND date_note >= ? AND note_moyenne < 3.0
    """, (employe_id, debut_mois))
    mauvaises_notes_mois = c.fetchone()[0]
    
    # Employé du Mois (pour Platine)
    c.execute('SELECT COUNT(*) FROM employes_mois WHERE employe_id = ?', (employe_id,))
    employe_mois_count = c.fetchone()[0]
    
    # === RÈGLES DE MONTEE ===
    nouveau_echelon = echelon_actuel
    changement = None
    prime = 0
    
    if echelon_actuel == "bronze":
        # Argent : 3 semaines sans retard/sanction + note moyenne >= 4/5
        if retards_21j == 0 and sanctions_actives == 0 and moyenne_21j >= 4.0:
            nouveau_echelon = "argent"
            changement = "Montée Bronze → Argent"
            
    elif echelon_actuel == "argent":
        # Or : 6 semaines sans retard/sanction + note moyenne > 4/5
        if retards_42j == 0 and sanctions_actives == 0 and moyenne_42j > 4.0:
            nouveau_echelon = "or"
            changement = "Montée Argent → Or"
            prime = 20  # Prime unique 20$
            
    elif echelon_actuel == "or":
        # Platine : maintien Or 2 mois (pas de retard/sanction) + Employé du Mois >= 1
        c.execute("""
            SELECT COUNT(*) FROM pointages 
            WHERE employe_id = ? AND date_journee >= ? AND retard_minutes > 0
        """, (employe_id, date_60j))
        retards_60j_check = c.fetchone()[0]
        
        # Vérifier s'il a été en Or pendant 60j (pas de changement d'échelon dans les 60j)
        c.execute("""
            SELECT COUNT(*) FROM historique_echelons 
            WHERE employe_id = ? AND date_changement >= ?
        """, (employe_id, date_60j))
        changements_60j = c.fetchone()[0]
        
        if retards_60j_check == 0 and sanctions_actives == 0 and employe_mois_count > 0 and changements_60j == 0:
            nouveau_echelon = "platine"
            changement = "Montée Or → Platine"
            prime = 50  # Prime d'atteinte 50$
    
    # === RÈGLES DE DESCENTE ===
    
    # 3e sanction = destitution totale (retour Bronze)
    if sanctions_actives >= 3:
        nouveau_echelon = "bronze"
        changement = "Destitution → Bronze (3e sanction)"
    
    # 1-2 sanctions = descente d'un échelon (sauf si déjà Bronze)
    elif sanctions_actives > 0 and echelon_actuel != "bronze" and nouveau_echelon == echelon_actuel:
        echelons = ["bronze", "argent", "or", "platine"]
        idx = echelons.index(echelon_actuel)
        if idx > 0:
            nouveau_echelon = echelons[idx - 1]
            changement = f"Descente {echelon_actuel} → {nouveau_echelon} (sanction)"
    
    # 2 retards dans le mois = descente d'un échelon
    if retards_mois >= 2 and echelon_actuel != "bronze" and nouveau_echelon == echelon_actuel:
        echelons = ["bronze", "argent", "or", "platine"]
        idx = echelons.index(echelon_actuel)
        if idx > 0:
            nouveau_echelon = echelons[idx - 1]
            changement = f"Descente {echelon_actuel} → {nouveau_echelon} (2 retards ce mois)"
    
    # 3 mauvaises notes clients (< 3/5) sur 30 jours = descente
    if mauvaises_notes_mois >= 3 and echelon_actuel != "bronze" and nouveau_echelon == echelon_actuel:
        echelons = ["bronze", "argent", "or", "platine"]
        idx = echelons.index(echelon_actuel)
        if idx > 0:
            nouveau_echelon = echelons[idx - 1]
            changement = f"Descente {echelon_actuel} → {nouveau_echelon} (3 notes < 3/5 ce mois)"
    
    # === APPLICATION DU CHANGEMENT ===
    if nouveau_echelon != echelon_actuel:
        c.execute('UPDATE employes SET echelon = ? WHERE id = ?', (nouveau_echelon, employe_id))
        
        # Historique du changement
        c.execute("""
            INSERT INTO historique_echelons (employe_id, echelon_avant, echelon_apres, raison, date_changement)
            VALUES (?, ?, ?, ?, ?)
        """, (employe_id, echelon_actuel, nouveau_echelon, changement, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        
        # Notification propriétaire
        notification_manager.alerte_systeme(
            titre=f"Changement d'échelon",
            description=f"{employe['nom']} : {changement}",
            niveau="info" if "Montée" in changement else "warning"
        )
        
        # Notification employé (montée = félicitations, descente = privé)
        if "Montée" in changement:
            notification_manager.alerte_systeme(
                titre=f"🎉 Félicitations {employe['nom']} !",
                description=f"Vous passez à l'échelon {nouveau_echelon.upper()}." + (f" Prime de {prime}$ versée !" if prime > 0 else ""),
                niveau="success"
            )
            
            # Enregistrer la prime si applicable
            if prime > 0:
                c.execute("""
                    INSERT INTO primes (employe_id, montant, raison, date_prime, statut)
                    VALUES (?, ?, ?, ?, 'versee')
                """, (employe_id, prime, changement, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
    
    conn.close()
    
    return {
        "employe_id": employe_id,
        "nom": employe["nom"],
        "echelon_avant": echelon_actuel,
        "echelon_apres": nouveau_echelon,
        "changement": changement,
        "prime": prime,
        "stats": {
            "retards_21j": retards_21j,
            "retards_42j": retards_42j,
            "retards_mois": retards_mois,
            "moyenne_notes_21j": round(moyenne_21j, 2),
            "moyenne_notes_42j": round(moyenne_42j, 2),
            "mauvaises_notes_mois": mauvaises_notes_mois,
            "sanctions_actives": sanctions_actives,
            "employe_du_mois": employe_mois_count
        }
    }

# ========== DESCENTE COLLECTIVE (TÂCHES MÉNAGÈRES) ==========

@app.post("/echelons/descente-collective")
async def descente_collective_echelons():
    """
    Si l'équipe omet de valider les tâches ménagères à 3 reprises dans le mois,
    l'intégralité du personnel perd un échelon (Cahier des charges Section D).
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    debut_mois = datetime.now().strftime("%Y-%m-01")
    
    # Compter les tâches NON faites ce mois (validation = false ou NULL)
    c.execute("""
        SELECT COUNT(*) FROM historique_taches 
        WHERE date_validation >= ? AND (validation = 0 OR validation IS NULL)
    """, (debut_mois,))
    taches_non_faites = c.fetchone()[0]
    
    if taches_non_faites < 3:
        conn.close()
        return {
            "descente_appliquee": False,
            "message": f"Seulement {taches_non_faites} tâche(s) non faite(s) ce mois. Seuil : 3.",
            "taches_non_faites": taches_non_faites
        }
    
    # Appliquer la descente à toute l'équipe (sauf Bronze qui ne peut pas descendre plus)
    c.execute("SELECT id, nom, echelon FROM employes WHERE statut = 'actif' AND echelon != 'bronze'")
    employes = c.fetchall()
    
    echelons = ["bronze", "argent", "or", "platine"]
    changements = []
    
    for emp in employes:
        idx = echelons.index(emp["echelon"])
        if idx > 0:
            nouveau = echelons[idx - 1]
            c.execute('UPDATE employes SET echelon = ? WHERE id = ?', (nouveau, emp["id"]))
            
            c.execute("""
                INSERT INTO historique_echelons (employe_id, echelon_avant, echelon_apres, raison, date_changement)
                VALUES (?, ?, ?, ?, ?)
            """, (emp["id"], emp["echelon"], nouveau, "Descente collective (tâches ménagères non faites 3x)", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            changements.append({
                "employe": emp["nom"],
                "avant": emp["echelon"],
                "apres": nouveau
            })
    
    conn.commit()
    conn.close()
    
    # Notification au propriétaire
    notification_manager.alerte_systeme(
        titre="⚠️ Descente collective d'échelons",
        description=f"{len(changements)} employés ont perdu un échelon (tâches ménagères non validées {taches_non_faites}x ce mois)",
        niveau="warning"
    )
    
    return {
        "descente_appliquee": True,
        "message": f"Descente collective appliquée ({taches_non_faites} tâches non faites ce mois)",
        "taches_non_faites": taches_non_faites,
        "employes_affectes": len(changements),
        "changements": changements
    }

# ========== EMPLOYÉ DU MOIS (AVEC POIDS CORRECTS) ==========

@app.post("/employe-du-mois/calculer-v2")
async def calculer_employe_du_mois_v2(annee: int = None, mois: int = None):
    """
    Calcule l'employé du mois avec les poids exacts :
    - 35% notes clients
    - 25% ponctualité
    - 20% tâches ménagères
    - 20% checklist service
    """
    if annee is None:
        annee = datetime.now().year
    if mois is None:
        mois = datetime.now().month
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer tous les employés actifs
    c.execute("SELECT id, nom FROM employes WHERE statut = 'actif'")
    employes = c.fetchall()
    
    meilleur_employe = None
    meilleur_score = 0
    
    mois_str = f"{annee}-{mois:02d}"
    
    for emp in employes:
        try:
            emp_id = emp["id"]
            emp_nom = emp["nom"]
            
            # 1. Notes clients (35% max = 35 points sur 100)
            c.execute('''
                SELECT AVG(note_moyenne) as avg_note, COUNT(*) as count
                FROM notes_clients
                WHERE employe_id = ? AND strftime('%Y-%m', date_rdv) = ?
            ''', (emp_id, mois_str))
            note_data = c.fetchone()
            avg_note = note_data["avg_note"] or 0
            count_notes = note_data["count"] or 0
            # Normaliser sur 10 → 35 points max
            score_notes = (avg_note / 10.0) * 35 if count_notes > 0 else 0
            
            # 2. Ponctualité (25% max = 25 points)
            c.execute('''
                SELECT COUNT(*) as total, COUNT(CASE WHEN retard_minutes = 0 THEN 1 END) as ponctuel
                FROM pointages
                WHERE employe_id = ? AND strftime('%Y-%m', date_journee) = ?
            ''', (emp_id, mois_str))
            pointage_data = c.fetchone()
            total_pointages = pointage_data["total"] or 0
            ponctuel = pointage_data["ponctuel"] or 0
            score_ponctualite = (ponctuel / total_pointages) * 25 if total_pointages > 0 else 0
            
            # 3. Tâches ménagères (20% max = 20 points)
            c.execute('''
                SELECT COUNT(*) as total
                FROM historique_taches
                WHERE employe_id = ? AND strftime('%Y-%m', date_realisation) = ?
            ''', (emp_id, mois_str))
            taches_data = c.fetchone()
            total_taches = taches_data["total"] or 0
            # 1 tache = 1 point, max 20
            score_taches = min(total_taches, 20)
            
            # 4. Checklist service (20% max = 20 points)
            c.execute('''
                SELECT AVG(score_checklist) as avg_score, COUNT(*) as total
                FROM checklist_service
                WHERE employe_id = ? AND strftime('%Y-%m', date_service) = ?
            ''', (emp_id, mois_str))
            checklist_data = c.fetchone()
            avg_checklist = checklist_data["avg_score"] or 0
            total_checklists = checklist_data["total"] or 0
            # Normaliser sur 10 → 20 points max
            score_checklist = (avg_checklist / 10.0) * 20 if total_checklists > 0 else 0
            
            # Score total
            score_total = score_notes + score_ponctualite + score_taches + score_checklist
            
            if score_total > meilleur_score:
                meilleur_score = score_total
                meilleur_employe = {
                    "id": emp_id,
                    "nom": emp_nom,
                    "score_total": round(score_total, 2),
                    "breakdown": {
                        "notes_clients": round(score_notes, 2),
                        "ponctualite": round(score_ponctualite, 2),
                        "taches_menageres": round(score_taches, 2),
                        "checklist_service": round(score_checklist, 2)
                    },
                    "details": {
                        "note_moyenne": round(avg_note, 2),
                        "total_notes": count_notes,
                        "total_pointages": total_pointages,
                        "total_taches": total_taches,
                        "total_checklists": total_checklists
                    }
                }
        except Exception as e:
            continue
    
    # Enregistrer l'employé du mois
    if meilleur_employe:
        try:
            c.execute('''
                INSERT OR REPLACE INTO employes_mois (annee, mois, employe_id, score_total, raison, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (annee, mois_str, meilleur_employe["id"], 
                  meilleur_employe["score_total"], 
                  f"Employé du mois (V2) - Score: {meilleur_employe['score_total']}",
                  datetime.now().isoformat()))
            conn.commit()
            
            # Notifier le propriétaire pour confirmation
            notification_manager.alerte_systeme(
                titre=f"🏆 Employé du mois {mois_str}",
                description=f"{meilleur_employe['nom']} est l'employé du mois avec {meilleur_employe['score_total']:.1f} pts ! Confirme ?",
                niveau="info"
            )
        except Exception as e:
            pass
    
    conn.close()
    
    return {
        "success": True,
        "annee": annee,
        "mois": mois,
        "employe_du_mois": meilleur_employe or "Aucun employé éligible",
        "poids": {
            "notes_clients": "35%",
            "ponctualite": "25%",
            "taches_menageres": "20%",
            "checklist_service": "20%"
        }
    }

# ========== ALERTES SMS (3 NIVEAUX) ==========

class AlerteNiveauCreate(BaseModel):
    employe_id: int
    type: str  # retard, absence, pause_longue, depart_premature, note_faible, manquement_taches
    description: str
    niveau: str  # info, attention, urgente
    date_alerte: str

@app.post("/alertes/niveau")
async def create_alerte_niveau(alerte: AlerteNiveauCreate):
    """Crée une alerte avec niveau et envoie SMS selon le niveau"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier que le niveau est valide
    if alerte.niveau not in ["info", "attention", "urgente"]:
        raise HTTPException(status_code=400, detail="Niveau doit être: info, attention, ou urgente")
    
    try:
        c.execute('''
            INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
            VALUES (?, ?, ?, ?, ?, 'nouveau')
        ''', (alerte.employe_id, alerte.type, alerte.description, alerte.niveau, alerte.date_alerte))
        
        alerte_id = c.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur DB: {str(e)}")
    
    # Récupérer l'employé
    c.execute("SELECT nom, telephone FROM employes WHERE id = ?", (alerte.employe_id,))
    employe = c.fetchone()
    conn.close()
    
    if not employe:
        return {"success": True, "alerte_id": alerte_id, "warning": "Employé non trouvé"}
    
    # Envoyer SMS selon le niveau
    if alerte.niveau == "urgente":
        # SMS immédiat au propriétaire
        notification_manager.alerte_systeme(
            titre=f"🚨 ALERTE URGENTE",
            description=f"{employe['nom']} : {alerte.description}\n\nAction requise immédiatement.",
            niveau="danger"
        )
    elif alerte.niveau == "attention":
        # SMS au propriétaire
        notification_manager.alerte_systeme(
            titre=f"⚠️ Alerte",
            description=f"{employe['nom']} : {alerte.description}\n\nÀ surveiller.",
            niveau="warning"
        )
    else:
        # Info = juste notification in-app (pas de SMS)
        pass
    
    return {
        "success": True,
        "alerte_id": alerte_id,
        "niveau": alerte.niveau,
        "message": f"Alerte {alerte.niveau} créée" + (" et SMS envoyé" if alerte.niveau in ["urgente", "attention"] else "")
    }

@app.post("/alertes/auto-verifier")
async def auto_verifier_alertes():
    """
    Vérifie automatiquement les situations qui nécessitent une alerte :
    - Retard > 5 min
    - Absence (pas de pointage à 13h)
    - Pause > 70 min
    - Départ prématuré (< 4h de travail)
    - Note client < 3/5
    - 3e manquement tâches
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    alertes_crees = []
    aujourdhui = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Retards > 5 min
    c.execute('''
        SELECT p.employe_id, e.nom, p.retard_minutes, p.raison_retard
        FROM pointages p
        JOIN employes e ON p.employe_id = e.id
        WHERE p.date_journee = ? AND p.retard_minutes > 0
    ''', (aujourdhui,))
    for row in c.fetchall():
        # Compter le retard dans le mois
        debut_mois = aujourdhui[:8] + '01'
        c.execute('''
            SELECT COUNT(*) FROM pointages 
            WHERE employe_id = ? AND date_journee >= ? AND retard_minutes > 0
        ''', (row['employe_id'], debut_mois))
        retard_numero = c.fetchone()[0]
        
        # 3e retard = URGENT, sinon ATTENTION
        if retard_numero >= 3:
            alertes_crees.append({
                "employe_id": row["employe_id"],
                "type": "retard",
                "description": f"{row['nom']} — 3e retard ce mois-ci. 2 jours sans salaire applicables.",
                "niveau": "urgente"
            })
        else:
            alertes_crees.append({
                "employe_id": row["employe_id"],
                "type": "retard",
                "description": f"{row['nom']} est arrivé(e) avec {row['retard_minutes']} min de retard. Raison : {row['raison_retard'] or 'Non spécifiée'}. Retard #{retard_numero} ce mois.",
                "niveau": "attention"
            })
    
    # 2. Absences (pas de pointage à 13h)
    heure_actuelle = datetime.now()
    heure_minutes = heure_actuelle.hour * 60 + heure_actuelle.minute
    
    # Vérifier pour chaque employé actif
    c.execute('''
        SELECT e.id, e.nom
        FROM employes e
        WHERE e.statut = 'actif'
        AND e.id NOT IN (
            SELECT employe_id FROM pointages WHERE date_journee = ?
        )
    ''', (aujourdhui,))
    for row in c.fetchall():
        # Calculer minutes depuis l'heure d'ouverture
        jour_semaine = heure_actuelle.strftime("%A")
        heure_ouverture = 12 if jour_semaine in ["Monday", "Tuesday"] else 10
        ouverture_minutes = heure_ouverture * 60
        minutes_depasse = heure_minutes - ouverture_minutes
        
        if minutes_depasse > 0:
            alertes_crees.append({
                "employe_id": row["id"],
                "type": "absence",
                "description": f"{row['nom']} n'a pas pointé depuis {minutes_depasse} min après son heure prévue.",
                "niveau": "urgente"
            })
    
    # 3. Pauses > 70 min
    c.execute('''
        SELECT p.employe_id, e.nom, p.heure_pause_debut
        FROM pointages p
        JOIN employes e ON p.employe_id = e.id
        WHERE p.date_journee = ? AND p.heure_pause_debut IS NOT NULL AND p.heure_pause_fin IS NULL
    ''', (aujourdhui,))
    for row in c.fetchall():
        debut_pause = datetime.strptime(row["heure_pause_debut"], "%H:%M")
        now = datetime.now()
        duree_pause = (now - debut_pause).total_seconds() / 60
        if duree_pause > 70:
            alertes_crees.append({
                "employe_id": row["employe_id"],
                "type": "pause_longue",
                "description": f"{row['nom']} est en pause depuis 70 minutes.",
                "niveau": "attention"
            })
    
    # 4. 3e sanction (licenciement imminent)
    c.execute('''
        SELECT e.id, e.nom, COUNT(*) as nb_sanctions
        FROM employes e
        JOIN sanctions s ON e.id = s.employe_id
        WHERE s.statut = 'actif'
        GROUP BY e.id
        HAVING nb_sanctions >= 3
    ''')
    for row in c.fetchall():
        alertes_crees.append({
            "employe_id": row["id"],
            "type": "sanction",
            "description": f"{row['nom']} — 3e sanction atteinte. Séparation à confirmer.",
            "niveau": "urgente"
        })
    
    # 5. Notes clients 1 ou 2/5
    c.execute('''
        SELECT nc.employe_id, e.nom, nc.note_moyenne, nc.commentaire
        FROM notes_clients nc
        JOIN employes e ON nc.employe_id = e.id
        WHERE nc.date_note = ? AND nc.note_moyenne <= 2.0
    ''', (aujourdhui,))
    for row in c.fetchall():
        alertes_crees.append({
            "employe_id": row["employe_id"],
            "type": "note_faible",
            "description": f"Note {row['note_moyenne']}/5 reçue pour {row['nom']}. Commentaire : {row['commentaire'] or 'Aucun'}.",
            "niveau": "urgente"
        })
    
    # Créer les alertes
    for alerte in alertes_crees:
        c.execute('''
            INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
            VALUES (?, ?, ?, ?, ?, 'nouveau')
        ''', (alerte["employe_id"], alerte["type"], alerte["description"], alerte["niveau"], aujourdhui))
    
    conn.commit()
    conn.close()
    
    # Envoyer SMS pour les alertes urgentes
    for alerte in alertes_crees:
        if alerte["niveau"] == "urgente":
            notification_manager.alerte_systeme(
                titre=f"🚨 URGENT - Kadio Coiffure",
                description=f"{alerte['description']}\n\nAction immédiate requise.",
                niveau="danger"
            )
        elif alerte["niveau"] == "attention":
            notification_manager.alerte_systeme(
                titre=f"⚠️ Kadio Coiffure",
                description=f"{alerte['description']}\n\nÀ surveiller.",
                niveau="warning"
            )
    
    return {
        "success": True,
        "alertes_crees": len(alertes_crees),
        "details": alertes_crees
    }

# ========== PARRAINAGE CLIENT (CASH) ==========

class ParrainageClientCreate(BaseModel):
    parrain_telephone: str
    filleul_telephone: str
    code_parrainage: Optional[str] = None

class DepenseClientCreate(BaseModel):
    client_telephone: str
    montant: float
    service: Optional[str] = None
    employe_id: Optional[int] = None
    source: Optional[str] = "square"

@app.post("/parrainage-client/inscrire")
async def inscrire_parrainage_client(data: ParrainageClientCreate):
    """Inscrit un filleul avec un code de parrainage"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Générer un code unique si non fourni
    code = data.code_parrainage or f"PARRAIN-{data.filleul_telephone[-4:]}"
    
    try:
        c.execute('''
            INSERT INTO parrainages_clients (parrain_telephone, filleul_telephone, code_parrainage)
            VALUES (?, ?, ?)
        ''', (data.parrain_telephone, data.filleul_telephone, code))
        conn.commit()
        parrainage_id = c.lastrowid
        conn.close()
        
        return {
            "success": True,
            "parrainage_id": parrainage_id,
            "code_parrainage": code,
            "message": "Parrainage enregistré"
        }
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Ce filleul est déjà parrainé")

@app.post("/depenses-client/enregistrer")
async def enregistrer_depense_client(data: DepenseClientCreate):
    """Enregistre une dépense client et calcule les récompenses de parrainage"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        # Enregistrer la dépense
        c.execute('''
            INSERT INTO depenses_clients (client_telephone, montant, service, employe_id, source)
            VALUES (?, ?, ?, ?, ?)
        ''', (data.client_telephone, data.montant, data.service, data.employe_id, data.source))
        conn.commit()
        
        # Vérifier si ce client est un filleul
        c.execute('''
            SELECT parrain_telephone FROM parrainages_clients 
            WHERE filleul_telephone = ? AND statut = 'actif'
        ''', (data.client_telephone,))
        parrain = c.fetchone()
        
        gains = []
        if parrain:
            parrain_tel = parrain["parrain_telephone"]
            
            # Calculer total dépenses cumulées du filleul
            c.execute('''
                SELECT SUM(montant) as total FROM depenses_clients WHERE client_telephone = ?
            ''', (data.client_telephone,))
            total_depenses = c.fetchone()["total"] or 0
            
            # Déterminer le palier atteint
            # Créer la table si elle n'existe pas (Railway)
            c.execute('''
                CREATE TABLE IF NOT EXISTS paliers_parrainage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    palier_min REAL NOT NULL,
                    palier_max REAL NOT NULL,
                    montant_recompense REAL NOT NULL,
                    description TEXT
                )
            ''')
            
            # Insérer les paliers si vide
            c.execute("SELECT COUNT(*) FROM paliers_parrainage")
            if c.fetchone()[0] == 0:
                paliers = [
                    (0, 100, 10, "0-100$"), (100, 200, 20, "100-200$"), (200, 300, 30, "200-300$"),
                    (300, 400, 40, "300-400$"), (400, 500, 50, "400-500$"), (500, 600, 60, "500-600$"),
                    (600, 700, 70, "600-700$"), (700, 800, 80, "700-800$"), (800, 900, 90, "800-900$"),
                    (900, 1000, 100, "900-1000$"), (1000, 1100, 110, "1000-1100$"), (1100, 1200, 120, "1100-1200$"),
                    (1200, 1300, 130, "1200-1300$"), (1300, 1400, 140, "1300-1400$"), (1400, 1500, 150, "1400-1500$"),
                    (1500, 1600, 160, "1500-1600$"), (1600, 1700, 170, "1600-1700$"), (1700, 1800, 180, "1700-1800$"),
                    (1800, 1900, 190, "1800-1900$"), (1900, 2000, 200, "1900-2000$")
                ]
                c.executemany('INSERT INTO paliers_parrainage (palier_min, palier_max, montant_recompense, description) VALUES (?, ?, ?, ?)', paliers)
            
            c.execute('''
                SELECT * FROM paliers_parrainage WHERE palier_min <= ? AND palier_max >= ?
            ''', (total_depenses, total_depenses))
            palier = c.fetchone()
            
            if palier:
                # Vérifier si ce palier a déjà été récompensé
                c.execute('''
                    SELECT COUNT(*) as count FROM recompenses_parrainage 
                    WHERE filleul_telephone = ? AND palier_numero = ?
                ''', (data.client_telephone, palier["id"]))
                deja_recompense = c.fetchone()["count"] > 0
                
                if not deja_recompense:
                    # Créditer le parrain
                    c.execute('''
                        INSERT INTO recompenses_parrainage (parrain_telephone, filleul_telephone, palier_numero, montant_gagne)
                        VALUES (?, ?, ?, ?)
                    ''', (parrain_tel, data.client_telephone, palier["id"], palier["montant_recompense"]))
                    conn.commit()
                    gains.append({
                        "parrain_telephone": parrain_tel,
                        "palier": palier["description"],
                        "montant_gagne": palier["montant_recompense"]
                    })
        
        conn.close()
        
        return {
            "success": True,
            "depense_id": c.lastrowid,
            "total_depenses_cumulees": total_depenses if parrain else data.montant,
            "gains_parrainage": gains,
            "message": "Dépense enregistrée" + (f" + {len(gains)} récompense(s) parrainage" if gains else "")
        }
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")

@app.get("/parrainage-client/mes-filleuls/{telephone}")
async def mes_filleuls(telephone: str):
    """Liste les filleuls d'un parrain"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT pc.*, 
               COALESCE(SUM(dc.montant), 0) as total_depenses
        FROM parrainages_clients pc
        LEFT JOIN depenses_clients dc ON pc.filleul_telephone = dc.client_telephone
        WHERE pc.parrain_telephone = ?
        GROUP BY pc.id
    ''', (telephone,))
    
    filleuls = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return {"success": True, "filleuls": filleuls}

@app.get("/parrainage-client/mes-gains/{telephone}")
async def mes_gains_parrainage(telephone: str):
    """Liste les gains d'un parrain"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT rp.*, p.description as palier_description
        FROM recompenses_parrainage rp
        LEFT JOIN paliers_parrainage p ON rp.palier_numero = p.id
        WHERE rp.parrain_telephone = ?
        ORDER BY rp.date_calcul DESC
    ''', (telephone,))
    
    gains = [dict(row) for row in c.fetchall()]
    total_gains = sum(g["montant_gagne"] for g in gains)
    conn.close()
    
    return {"success": True, "gains": gains, "total_gains": total_gains}

@app.get("/admin/parrainages-clients")
async def admin_parrainages_clients():
    """Vue admin de tous les parrainages clients"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT pc.*, 
               COALESCE(SUM(dc.montant), 0) as total_depenses,
               COALESCE(SUM(rp.montant_gagne), 0) as total_recompenses
        FROM parrainages_clients pc
        LEFT JOIN depenses_clients dc ON pc.filleul_telephone = dc.client_telephone
        LEFT JOIN recompenses_parrainage rp ON pc.filleul_telephone = rp.filleul_telephone
        GROUP BY pc.id
        ORDER BY pc.date_creation DESC
    ''')
    
    parrainages = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return {"success": True, "parrainages": parrainages}

# ========== PARRAINAGE EMPLOYÉ (COMMISSIONS) ==========

class ParrainageEmployeCreate(BaseModel):
    employe_id: int
    filleul_nom: str
    filleul_telephone: str
    type_abonnement: Optional[str] = None
    montant_recompense: Optional[float] = 15

@app.post("/parrainage-employe")
async def creer_parrainage_employe(data: ParrainageEmployeCreate):
    """Un employé enregistre un parrainage (client qui s'abonne)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        c.execute('''
            INSERT INTO parrainages_employes (employe_id, filleul_nom, filleul_telephone, type_abonnement, montant_recompense)
            VALUES (?, ?, ?, ?, ?)
        ''', (data.employe_id, data.filleul_nom, data.filleul_telephone, data.type_abonnement, data.montant_recompense))
        conn.commit()
        parrainage_id = c.lastrowid
        conn.close()
        
        return {
            "success": True,
            "parrainage_id": parrainage_id,
            "montant_recompense": data.montant_recompense,
            "message": "Parrainage employé enregistré"
        }
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")

@app.get("/parrainage-employe/{employe_id}")
async def mes_parrainages_employe(employe_id: int):
    """Liste les parrainages d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT * FROM parrainages_employes WHERE employe_id = ? ORDER BY created_at DESC
    ''', (employe_id,))
    
    parrainages = [dict(row) for row in c.fetchall()]
    total_commissions = sum(p["montant_recompense"] for p in parrainages if p["statut"] == "paye")
    conn.close()
    
    return {
        "success": True, 
        "parrainages": parrainages,
        "total_commissions": total_commissions
    }

@app.patch("/parrainage-employe/{parrainage_id}/confirmer")
async def confirmer_parrainage_employe(parrainage_id: int):
    """Confirme un parrainage (abonnement payé)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        UPDATE parrainages_employes SET statut = 'confirme' WHERE id = ?
    ''', (parrainage_id,))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Parrainage confirmé"}

@app.patch("/parrainage-employe/{parrainage_id}/payer")
async def payer_parrainage_employe(parrainage_id: int):
    """Marque un parrainage comme payé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    paye_a = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''
        UPDATE parrainages_employes SET statut = 'paye', paye_a = ? WHERE id = ?
    ''', (paye_a, parrainage_id))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Commission payée", "date_paiement": paye_a}

@app.get("/admin/parrainages-employes")
async def admin_parrainages_employes():
    """Vue admin de tous les parrainages employés"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT pe.*, e.nom as employe_nom
        FROM parrainages_employes pe
        JOIN employes e ON pe.employe_id = e.id
        ORDER BY pe.created_at DESC
    ''')
    
    parrainages = [dict(row) for row in c.fetchall()]
    total_en_attente = sum(p["montant_recompense"] for p in parrainages if p["statut"] == "en_attente")
    total_paye = sum(p["montant_recompense"] for p in parrainages if p["statut"] == "paye")
    conn.close()
    
    return {
        "success": True, 
        "parrainages": parrainages,
        "total_en_attente": total_en_attente,
        "total_paye": total_paye
    }

# ========== CODE DE POINTAGE ROTATIF (Toutes les 5 min) ==========

class LoginProprioRequest(BaseModel):
    email: str
    password: str

class ProprioAuth(BaseModel):
    email: str
    nom: str
    telephone: str

class NoteEmployeClientCreate(BaseModel):
    employe_id: int
    client_nom: str
    client_telephone: Optional[str] = None
    ponctualite: int  # 1-5
    comportement: int  # 1-5
    respect: int  # 1-5
    commentaire: Optional[str] = None
    date_service: Optional[str] = None

class GoogleAvisEnvoi(BaseModel):
    employe_id: int
    client_nom: str
    client_telephone: str
    note_client: float  # 1-5
    commentaire_client: Optional[str] = None

class EmployeDuMoisConfirm(BaseModel):
    employe_id: int
    mois: str
    annee: int
    recompense: Optional[str] = "Carte-cadeau 50$"

class TacheManquement(BaseModel):
    tache_id: int
    date: str

class AuthProprioResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    message: str

class CodePointageCreate(BaseModel):
    employe_id: int
    code: Optional[str] = None  # Si non fourni, généré automatiquement

@app.post("/codes-pointage/generer")
async def generer_code_pointage(data: CodePointageCreate):
    """Génère un nouveau code de pointage (valide 5 minutes, jamais le même dans la journée)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    now = datetime.now()
    expiration = now + timedelta(minutes=5)
    today = now.strftime("%Y-%m-%d")
    
    # Récupérer tous les codes utilisés aujourd'hui pour cet employé
    c.execute('''
        SELECT code FROM codes_pointage 
        WHERE employe_id = ? AND date_creation LIKE ?
    ''', (data.employe_id, f"{today}%"))
    codes_aujourd_hui = {row['code'] for row in c.fetchall()}
    
    # Déterminer le code à utiliser
    if data.code:
        # Code fourni manuellement — vérifier qu'il n'a pas été utilisé aujourd'hui
        if data.code in codes_aujourd_hui:
            conn.close()
            raise HTTPException(status_code=400, detail="Ce code a déjà été utilisé aujourd'hui")
        nouveau_code = data.code
    else:
        # Générer un code unique aujourd'hui
        import random
        max_essais = 50
        for _ in range(max_essais):
            nouveau_code = str(random.randint(1000, 9999))
            if nouveau_code not in codes_aujourd_hui:
                break
        else:
            conn.close()
            raise HTTPException(status_code=500, detail="Impossible de générer un code unique aujourd'hui")
    
    # Désactiver les anciens codes
    c.execute('''
        UPDATE codes_pointage SET actif = 0 WHERE employe_id = ?
    ''', (data.employe_id,))
    
    # Créer nouveau code
    c.execute('''
        INSERT INTO codes_pointage (employe_id, code, actif, date_creation, date_expiration)
        VALUES (?, ?, 1, ?, ?)
    ''', (data.employe_id, nouveau_code, now.strftime("%Y-%m-%d %H:%M:%S"), expiration.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "code": nouveau_code,
        "valide_jusqu": expiration.strftime("%H:%M:%S"),
        "message": "Code généré (valide 5 min, jamais utilisé aujourd'hui)"
    }

@app.get("/codes-pointage/{employe_id}")
async def get_code_actif(employe_id: int):
    """Retourne le code actif d'un employé, ou génère un nouveau si aucun n'est actif"""
    conn = get_db_connection()
    c = conn.cursor()
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''
        SELECT * FROM codes_pointage 
        WHERE employe_id = ? AND actif = 1 AND date_expiration > ?
        ORDER BY date_creation DESC LIMIT 1
    ''', (employe_id, now_str))
    
    code = c.fetchone()
    
    if not code:
        # Aucun code actif — générer automatiquement
        conn.close()
        result = await generer_code_pointage(CodePointageCreate(employe_id=employe_id))
        return result
    
    conn.close()
    return {"success": True, "code": dict(code)}

# ========== NOTES EMPLOYÉ → CLIENT (ancienne version — remplacée par la v2 plus bas) ==========

class GoogleAvisCreate(BaseModel):
    client_telephone: str
    client_nom: Optional[str] = None
    employe_id: int
    note_client: Optional[float] = None
    lien_google: Optional[str] = "https://g.page/r/..."

@app.post("/google-avis/envoyer")
async def envoyer_google_avis(data: GoogleAvisCreate):
    """Envoie un SMS demandant un avis Google après un service"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        # Enregistrer dans la base
        c.execute('''
            INSERT INTO google_avis (client_telephone, client_nom, employe_id, note_client, lien_google, statut)
            VALUES (?, ?, ?, ?, ?, 'en_attente')
        ''', (data.client_telephone, data.client_nom, data.employe_id, data.note_client, data.lien_google))
        conn.commit()
        
        # Ici tu peux intégrer Twilio pour envoyer le SMS réel
        # Pour l'instant on simule
        c.execute('''
            UPDATE google_avis SET sms_envoye = 1, date_sms_envoye = ?, statut = 'envoye'
            WHERE id = ?
        ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), c.lastrowid))
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "message": f"Demande d'avis Google envoyée à {data.client_nom or data.client_telephone}",
            "lien": data.lien_google
        }
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")

@app.get("/google-avis/stats")
async def stats_google_avis():
    """Stats des avis Google"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM google_avis WHERE statut = "envoye"')
    envoyes = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM google_avis WHERE statut = "repondu"')
    repondus = c.fetchone()[0]
    
    conn.close()
    
    return {"success": True, "envoyes": envoyes, "repondus": repondus, "taux_conversion": round(repondus/envoyes*100, 1) if envoyes > 0 else 0}

# ========== VIDÉO EXPLICATIVE (Onboarding) ==========

class VideoVisionnageUpdate(BaseModel):
    employe_id: int
    video_type: str = "reglement"
    visionnee: bool = True
    duree_secondes: Optional[int] = None
    complet: bool = False

@app.post("/videos/visionner")
async def marquer_video_vue(data: VideoVisionnageUpdate):
    """Marque une vidéo comme visionnée par un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        c.execute('''
            INSERT OR REPLACE INTO videos_visionnees (employe_id, video_type, visionnee, date_visionnage, duree_visionnee_secondes, complet)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data.employe_id, data.video_type, 1 if data.visionnee else 0, now, data.duree_secondes or 0, 1 if data.complet else 0))
        conn.commit()
        conn.close()
        
        return {"success": True, "message": "Visionnage enregistré"}
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")

@app.get("/videos/visionnees/{employe_id}")
async def get_videos_visionnees(employe_id: int):
    """Liste les vidéos visionnées par un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT * FROM videos_visionnees WHERE employe_id = ?
    ''', (employe_id,))
    
    videos = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return {"success": True, "videos": videos}

@app.get("/videos/verification/{employe_id}")
async def verifier_videos_obligatoires(employe_id: int):
    """Vérifie si l'employé a visionné toutes les vidéos obligatoires"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT video_type, visionnee, complet FROM videos_visionnees WHERE employe_id = ?
    ''', (employe_id,))
    
    videos = {row["video_type"]: {"visionnee": row["visionnee"], "complet": row["complet"]} for row in c.fetchall()}
    conn.close()
    
    obligatoires = ["reglement"]
    manquantes = [v for v in obligatoires if not videos.get(v, {}).get("complet")]
    
    return {
        "success": True,
        "toutes_vues": len(manquantes) == 0,
        "videos_manquantes": manquantes,
        "details": videos
    }

# Page écran salon (tablette pointage)
@app.get("/ecran-salon")
async def ecran_salon():
    """Page écran/tablette affichant le code de pointage - Cahier des charges Section C"""
    html_content = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kadio Coiffure - Pointage</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); color: #fff; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { font-size: 2.5rem; color: #c9a84c; text-transform: uppercase; letter-spacing: 4px; }
        .header p { font-size: 1rem; color: #e0e0e0; margin-top: 10px; }
        .auth-box { background: rgba(255,255,255,0.1); border: 2px solid #c9a84c; border-radius: 20px; padding: 40px; max-width: 450px; width: 100%; text-align: center; backdrop-filter: blur(10px); }
        .auth-box h2 { color: #c9a84c; font-size: 1.3rem; margin-bottom: 20px; }
        .input-field { width: 100%; padding: 15px; font-size: 1.1rem; border: 2px solid #555; border-radius: 10px; background: rgba(0,0,0,0.3); color: #fff; text-align: center; margin-bottom: 15px; }
        .input-field:focus { outline: none; border-color: #c9a84c; }
        .btn { width: 100%; padding: 15px; font-size: 1.1rem; background: #c9a84c; color: #000; border: none; border-radius: 10px; cursor: pointer; font-weight: bold; margin-top: 10px; }
        .code-display { font-size: 7rem; font-weight: bold; color: #c9a84c; letter-spacing: 15px; font-family: monospace; text-shadow: 0 0 30px rgba(201, 168, 76, 0.8); background: rgba(0,0,0,0.5); padding: 30px 50px; border-radius: 20px; margin: 20px 0; }
        .timer { font-size: 1.3rem; color: #ff6b6b; font-weight: bold; }
        .employe-info { font-size: 1.2rem; color: #c9a84c; margin-bottom: 15px; }
        .hidden { display: none; }
        .message { font-size: 0.9rem; padding: 10px; border-radius: 8px; margin-bottom: 15px; }
        .message.success { background: rgba(0,255,0,0.2); color: #90ee90; }
        .message.error { background: rgba(255,0,0,0.2); color: #ff6b6b; }
        .footer { position: fixed; bottom: 20px; text-align: center; color: #888; font-size: 0.8rem; }
        .rules { font-size: 0.8rem; color: #aaa; margin-top: 20px; text-align: left; }
        .rules li { margin-bottom: 5px; }
    </style>
</head>
<body>
    <div class="header"><h1>✂️ Kadio Coiffure</h1><p>Pointage — Écran sécurisé</p></div>
    <div id="step-auth" class="auth-box">
        <h2>🔐 Identifiez-vous</h2>
        <p style="color:#aaa; margin-bottom:15px;">Entrez votre numéro de téléphone</p>
        <input type="tel" id="telephone" class="input-field" placeholder="5141234567">
        <p style="color:#aaa; margin:15px 0 10px;">Entrez votre prénom</p>
        <input type="text" id="prenom" class="input-field" placeholder="Wilfried">
        <button class="btn" onclick="authenticate()">Obtenir mon code de pointage</button>
        <div id="auth-message"></div>
        <ul class="rules"><li>📍 Code visible UNIQUEMENT sur cet écran</li><li>⏱️ Code valide 5 minutes uniquement</li><li>🔄 Un nouveau code par jour</li></ul>
    </div>
    <div id="step-code" class="hidden" style="text-align:center;">
        <div class="employe-info" id="employe-name"></div>
        <div class="code-display" id="code-display">----</div>
        <div class="timer" id="timer">Valide 5 minutes</div>
        <p style="color:#aaa; margin-top:20px; font-size:0.9rem;">Ce code est à usage unique. Entrez-le dans le système de pointage.<br><strong style="color:#ff6b6b;">Impossible de pointer à distance.</strong></p>
        <button class="btn" onclick="logout()" style="margin-top:20px; max-width:300px;">🚪 Terminé</button>
    </div>
    <div class="footer">Kadio Coiffure • Code valide 5 min • Anti-triche activé</div>
    <script>
        const API = window.location.origin;
        let currentEmploye = null;
        async function authenticate() {
            const telephone = document.getElementById('telephone').value.trim().replace(/\D/g, '');
            const prenom = document.getElementById('prenom').value.trim().toLowerCase();
            if (!telephone || telephone.length < 10) { showMessage('auth-message', 'Entrez un numéro de téléphone valide (10 chiffres)', 'error'); return; }
            if (!prenom) { showMessage('auth-message', 'Entrez votre prénom', 'error'); return; }
            try {
                const res = await fetch(`${API}/auth/verify-employee`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({telephone: telephone, prenom: prenom}) });
                const data = await res.json();
                if (data.success) {
                    currentEmploye = data.employe;
                    showMessage('auth-message', '✅ Identité vérifiée', 'success');
                    setTimeout(() => { document.getElementById('step-auth').classList.add('hidden'); document.getElementById('step-code').classList.remove('hidden'); document.getElementById('employe-name').textContent = `👋 ${currentEmploye.nom} — Voici votre code :`; fetchCode(); }, 800);
                } else { showMessage('auth-message', data.message || 'Employé non trouvé', 'error'); }
            } catch (e) { showMessage('auth-message', 'Erreur réseau', 'error'); }
        }
        async function fetchCode() {
            if (!currentEmploye) return;
            try {
                const res = await fetch(`${API}/codes-pointage/generer`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({employe_id: currentEmploye.id}) });
                const data = await res.json();
                if (data.success && data.code) { document.getElementById('code-display').textContent = data.code; document.getElementById('timer').textContent = `Valide jusqu'à ${data.valide_jusqu}`; }
            } catch (e) {}
        }
        function logout() { currentEmploye = null; document.getElementById('step-code').classList.add('hidden'); document.getElementById('step-auth').classList.remove('hidden'); document.getElementById('telephone').value = ''; document.getElementById('prenom').value = ''; document.getElementById('auth-message').innerHTML = ''; }
        function showMessage(id, text, type) { const el = document.getElementById(id); el.className = `message ${type}`; el.textContent = text; }
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)

# ========== AUTHENTIFICATION OTP ==========

class OTPRequest(BaseModel):
    identifiant: str  # email ou téléphone

class OTPVerifyRequest(BaseModel):
    employe_id: int
    otp: str

def generer_otp() -> str:
    """Génère un code OTP à 4 chiffres"""
    import random
    return str(random.randint(1000, 9999))

def envoyer_otp_email(destinataire: str, otp: str, nom: str) -> bool:
    """Envoie l'OTP par email via Resend"""
    try:
        import requests
        api_key = os.getenv("RESEND_API_KEY", "")
        if not api_key:
            return False
        
        resp = requests.post("https://api.resend.com/emails", 
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "onboarding@resend.dev",
                "to": [destinataire],
                "subject": "🔐 Votre code de pointage — Kadio Coiffure",
                "text": f"""Bonjour {nom},

Voici votre code de confirmation : {otp}

Valide 5 minutes.

Kadio Coiffure"""
            }, timeout=15
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"Erreur envoi email OTP: {e}")
        return False

def envoyer_otp_sms(telephone: str, otp: str, nom: str) -> bool:
    """Envoie l'OTP par SMS via Twilio"""
    try:
        voice_connector = TwilioVoiceConnector()
        if not voice_connector.is_connected():
            return False
        
        message = f"Kadio Coiffure — Bonjour {nom}, votre code est : {otp}. Valide 5 min."
        result = voice_connector.send_sms(telephone, message)
        return result.get("success", False)
    except Exception as e:
        print(f"Erreur envoi SMS OTP: {e}")
        return False

@app.post("/auth/send-otp")
async def send_otp(request: OTPRequest):
    """Envoie un code OTP à l'employé par email ou SMS"""
    conn = get_db_connection()
    c = conn.cursor()
    
    identifiant = request.identifiant.strip().lower()
    
    c.execute('SELECT id, nom, email, telephone FROM employes WHERE LOWER(email) = ?', (identifiant,))
    employe = c.fetchone()
    
    if not employe:
        tel_clean = identifiant.replace('+', '').replace('-', '').replace(' ', '')
        c.execute('SELECT id, nom, email, telephone FROM employes WHERE REPLACE(REPLACE(REPLACE(telephone, "+", ""), "-", ""), " ", "") = ?', (tel_clean,))
        employe = c.fetchone()
    
    if not employe:
        conn.close()
        return {"success": False, "message": "Employé non trouvé"}
    
    employe_id = employe['id']
    nom = employe['nom']
    email = employe['email'] or ""
    telephone = employe['telephone'] or ""
    
    otp = generer_otp()
    now = datetime.now()
    expiration = now + timedelta(minutes=5)
    
    if '@' in identifiant:
        type_envoi = 'email'
        destinataire = email if email else identifiant
        envoye = envoyer_otp_email(destinataire, otp, nom)
    else:
        type_envoi = 'sms'
        tel = telephone if telephone else identifiant
        if not tel.startswith('+') and not tel.startswith('1'):
            tel = '+1' + tel
        elif not tel.startswith('+'):
            tel = '+' + tel
        destinataire = tel
        envoye = envoyer_otp_sms(destinataire, otp, nom)
    
    c.execute('INSERT INTO otp_verifications (employe_id, otp_code, type, destinaire, date_creation, date_expiration, utilise) VALUES (?, ?, ?, ?, ?, ?, 0)',
              (employe_id, otp, type_envoi, destinataire, now.isoformat(), expiration.isoformat()))
    conn.commit()
    conn.close()
    
    if envoye:
        return {"success": True, "employe": {"id": employe_id, "nom": nom}, "destinataire": destinataire, "message": f"Code envoyé par {type_envoi}"}
    else:
        return {"success": False, "message": "Erreur envoi"}

@app.post("/auth/verify-otp")
async def verify_otp(request: OTPVerifyRequest):
    """Vérifie le code OTP"""
    conn = get_db_connection()
    c = conn.cursor()
    
    now = datetime.now().isoformat()
    
    c.execute('SELECT id FROM otp_verifications WHERE employe_id = ? AND otp_code = ? AND utilise = 0 AND date_expiration > ? ORDER BY id DESC LIMIT 1',
              (request.employe_id, request.otp, now))
    
    otp_row = c.fetchone()
    
    if not otp_row:
        conn.close()
        return {"success": False, "message": "Code incorrect ou expiré"}
    
    c.execute('UPDATE otp_verifications SET utilise = 1 WHERE id = ?', (otp_row['id'],))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Authentification réussie"}

# ========== AUTHENTIFICATION EMPLOYÉ (CAHIER DES CHARGES) ==========

class EmployeeAuthRequest(BaseModel):
    telephone: str
    prenom: str

@app.post("/auth/verify-employee")
async def verify_employee(request: EmployeeAuthRequest):
    """Vérifie l'identité de l'employé par téléphone + prénom (Cahier des charges Section C)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Normaliser le téléphone
    tel_clean = request.telephone.replace('+', '').replace('-', '').replace(' ', '')
    prenom_clean = request.prenom.strip().lower()
    
    # Chercher par téléphone ET prénom
    c.execute("""
        SELECT id, nom, telephone, email, role, specialite, echelon 
        FROM employes 
        WHERE REPLACE(REPLACE(REPLACE(telephone, '+', ''), '-', ''), ' ', '') = ? 
        AND LOWER(nom) = ?
        AND statut = 'actif'
    """, (tel_clean, prenom_clean))
    
    employe = c.fetchone()
    conn.close()
    
    if not employe:
        return {"success": False, "message": "Employé non trouvé. Vérifiez votre téléphone et prénom."}
    
    return {
        "success": True,
        "employe": {
            "id": employe['id'],
            "nom": employe['nom'],
            "telephone": employe['telephone'],
            "email": employe['email'],
            "role": employe['role'],
            "specialite": employe['specialite'],
            "echelon": employe['echelon']
        }
    }

# ========== RÈGLES DU SALON ==========

REGLES_SALON = {
    "regles": [
        {
            "id": 1,
            "titre": "Arrivée 5 minutes avant",
            "contenu": "Chaque employé doit arriver 5 minutes AVANT son heure officielle. Ces 5 minutes servent à allumer la musique, s'assurer que le salon sent bon, vérifier que les postes sont propres, préparer les boissons et se préparer mentalement.",
            "consequence": "Arriver à l'heure officielle = retard."
        },
        {
            "id": 2,
            "titre": "Pause de 60 minutes",
            "contenu": "Chaque employé a droit à une pause de 60 minutes par jour. La pause se déclare dans le système. Plus de 70 minutes sans autorisation = alerte au propriétaire.",
            "consequence": "Temps non rémunéré au-delà de 60 min."
        },
        {
            "id": 3,
            "titre": "Comportement général",
            "contenu": "Téléphone personnel interdit quand un client est dans la chaise. Tenue propre et professionnelle. Aucun commentaire négatif en zone client. Le salon doit toujours sentir bon.",
            "consequence": "Avertissement puis sanction."
        },
        {
            "id": 4,
            "titre": "Esprit d'équipe",
            "contenu": "Les tâches ménagères sont collectives. Les employés s'entraident. Si un collègue est en retard, l'équipe couvre en attendant.",
            "consequence": "Sanction collective si tâches non faites."
        }
    ]
}

@app.get("/regles-salon")
async def get_regles_salon():
    """Retourne les règles du salon"""
    return {"success": True, **REGLES_SALON}

# ========== RÉCOMPENSES ÉCHELONS ==========

@app.post("/echelons/verifier-recompenses/{employe_id}")
async def verifier_recompenses_echelon(employe_id: int):
    """Vérifie et attribue les récompenses selon l'échelon actuel"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer l'employé et son échelon
    c.execute('SELECT nom, echelon FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    echelon = employe['echelon']
    recompenses = []
    
    # Argent = badge + reconnaissance
    if echelon == 'argent':
        # Vérifier si badge déjà attribué
        c.execute('SELECT id FROM badges_employes WHERE employe_id = ? AND badge_id = (SELECT id FROM badges WHERE nom = "Argent")', (employe_id,))
        if not c.fetchone():
            c.execute('INSERT INTO badges_employes (employe_id, badge_id, date_attribution, raison) VALUES (?, (SELECT id FROM badges WHERE nom = "Argent"), ?, "Atteinte niveau Argent")', (employe_id, datetime.now().strftime("%Y-%m-%d")))
            recompenses.append({"type": "badge", "nom": "Argent", "message": "Badge Argent attribué"})
        recompenses.append({"type": "reconnaissance", "message": "Reconnaissance publique pour niveau Argent"})
    
    # Or = 20$ bonus
    elif echelon == 'or':
        c.execute('SELECT id FROM recompenses WHERE employe_id = ? AND type = "bonus" AND raison LIKE "%Or%"', (employe_id,))
        if not c.fetchone():
            c.execute('INSERT INTO recompenses (employe_id, type, montant, description, raison, date_attribution) VALUES (?, "bonus", 20, "Bonus niveau Or", "Atteinte niveau Or", ?)', (employe_id, datetime.now().strftime("%Y-%m-%d")))
            recompenses.append({"type": "bonus", "montant": 20, "message": "20$ bonus pour niveau Or"})
    
    # Platine = 50$ + 50$/3 mois
    elif echelon == 'platine':
        # Prime d'atteinte unique (une seule fois)
        c.execute('SELECT id FROM recompenses WHERE employe_id = ? AND type = "bonus" AND raison LIKE "%Atteinte niveau Platine%"', (employe_id,))
        if not c.fetchone():
            c.execute('INSERT INTO recompenses (employe_id, type, montant, description, raison, date_attribution) VALUES (?, "bonus", 50, "Bonus niveau Platine", "Atteinte niveau Platine", ?)', (employe_id, datetime.now().strftime("%Y-%m-%d")))
            recompenses.append({"type": "bonus", "montant": 50, "message": "50$ bonus pour niveau Platine (prime d'atteinte)"})
        
        # Bonus récurrent tous les 3 mois si niveau maintenu
        date_3mois = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        c.execute('SELECT id FROM recompenses WHERE employe_id = ? AND type = "bonus" AND raison LIKE "%Bonus récurrent Platine%" AND date_attribution >= ?', (employe_id, date_3mois))
        if not c.fetchone():
            c.execute('INSERT INTO recompenses (employe_id, type, montant, description, raison, date_attribution) VALUES (?, "bonus", 50, "Bonus récurrent niveau Platine", "Bonus récurrent Platine - maintien 3 mois", ?)', (employe_id, datetime.now().strftime("%Y-%m-%d")))
            recompenses.append({"type": "bonus", "montant": 50, "message": "50$ bonus récurrent Platine (tous les 3 mois)"})
    
    conn.commit()
    conn.close()
    
    return {"success": True, "echelon": echelon, "recompenses": recompenses}

# ========== DESCENTE D'ÉCHELON ==========

@app.post("/echelons/verifier-descente/{employe_id}")
async def verifier_descente_echelon(employe_id: int):
    """Vérifie si l'employé doit descendre d'échelon"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer l'employé
    c.execute('SELECT nom, echelon FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    echelon_actuel = employe['echelon']
    if echelon_actuel == 'bronze':
        conn.close()
        return {"success": True, "descente": False, "message": "Déjà au niveau Bronze"}
    
    # 1. Vérifier 2 retards dans le même mois
    mois_actuel = datetime.now().strftime("%Y-%m")
    c.execute('SELECT COUNT(*) FROM pointages WHERE employe_id = ? AND date_journee LIKE ? AND retard_minutes > 0', (employe_id, f"{mois_actuel}%"))
    nb_retards = c.fetchone()[0]
    
    # 2. Vérifier 3 mauvaises notes clients sous 3/5 en un mois
    c.execute('SELECT COUNT(*) FROM notes_clients WHERE employe_id = ? AND date_rdv LIKE ? AND note_moyenne < 3', (employe_id, f"{mois_actuel}%"))
    nb_mauvaises_notes = c.fetchone()[0]
    
    # 3. Vérifier 3 tâches non faites dans le même mois (équipe)
    c.execute('SELECT COUNT(*) FROM historique_taches WHERE employe_id = ? AND date_realisation LIKE ? AND note < 5', (employe_id, f"{mois_actuel}%"))
    nb_taches_non_faites = c.fetchone()[0]
    
    # 4. Vérifier 3e sanction
    c.execute('SELECT COUNT(*) FROM sanctions WHERE employe_id = ? AND statut = "actif"', (employe_id,))
    nb_sanctions = c.fetchone()[0]
    
    raisons = []
    if nb_retards >= 2:
        raisons.append(f"{nb_retards} retards ce mois")
    if nb_mauvaises_notes >= 3:
        raisons.append(f"{nb_mauvaises_notes} mauvaises notes ce mois")
    if nb_taches_non_faites >= 3:
        raisons.append(f"{nb_taches_non_faites} tâches non faites ce mois")
    if nb_sanctions >= 3:
        raisons.append(f"{nb_sanctions} sanctions actives")
    
    if raisons:
        # Descendre d'un échelon (ou retour Bronze si 3e sanction)
        nouvel_echelon = 'bronze' if nb_sanctions >= 3 else _descendre_echelon(echelon_actuel)
        c.execute('UPDATE employes SET echelon = ? WHERE id = ?', (nouvel_echelon, employe_id))
        conn.commit()
        conn.close()
        return {"success": True, "descente": True, "ancien_echelon": echelon_actuel, "nouvel_echelon": nouvel_echelon, "raisons": raisons}
    
    conn.close()
    return {"success": True, "descente": False, "message": "Aucune condition de descente remplie"}

def _descendre_echelon(echelon: str) -> str:
    """Retourne l'échelon inférieur"""
    ordre = ['bronze', 'argent', 'or', 'platine']
    idx = ordre.index(echelon)
    return ordre[max(0, idx - 1)]

# ========== NOTIFICATIONS ÉCHELON ==========

@app.post("/echelons/notifier/{employe_id}")
async def notifier_changement_echelon(employe_id: int, type_changement: str, ancien_echelon: str, nouvel_echelon: str):
    """Envoie une notification de montée ou descente d'échelon"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT nom FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    nom = employe['nom']
    
    if type_changement == "montee":
        message = f"Félicitations ! Vous avez atteint le niveau {nouvel_echelon}."
        # Notification à l'employé (page employé)
        c.execute('INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut) VALUES (?, "montee_echelon", ?, "faible", ?, "nouveau")', (employe_id, message, datetime.now().strftime("%Y-%m-%d")))
    else:
        message = f"Notification : vous êtes redescendu au niveau {nouvel_echelon}."
        # Notification privée à l'employé
        c.execute('INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut) VALUES (?, "descente_echelon", ?, "moyen", ?, "nouveau")', (employe_id, message, datetime.now().strftime("%Y-%m-%d")))
        # Alerte au propriétaire
        c.execute('INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut) VALUES (?, "descente_echelon", ?, "moyen", ?, "nouveau")', (0, f"{nom} est redescendu au niveau {nouvel_echelon} (ancien: {ancien_echelon})", datetime.now().strftime("%Y-%m-%d")))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": message}

# ========== COMPTEUR RETARDS MENSUEL ==========

@app.get("/retards/compteur/{employe_id}")
async def compter_retards_mensuel(employe_id: int):
    """Compte les retards du mois en cours (reset le 1er de chaque mois)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    mois_actuel = datetime.now().strftime("%Y-%m")
    c.execute('SELECT COUNT(*) FROM pointages WHERE employe_id = ? AND date_journee LIKE ? AND retard_minutes > 0', (employe_id, f"{mois_actuel}%"))
    nb_retards = c.fetchone()[0]
    
    c.execute('SELECT nom FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    conn.close()
    
    return {
        "success": True,
        "employe": employe['nom'] if employe else "Inconnu",
        "mois": mois_actuel,
        "retards": nb_retards,
        "message": f"Retard #{nb_retards} ce mois" if nb_retards > 0 else "Aucun retard ce mois"
    }

# ========== SANCTIONS SPÉCIFIQUES ==========

@app.post("/sanctions/telephone-client")
async def sanction_telephone_client(employe_id: int, recidive: bool = False):
    """Téléphone devant un client : avertissement oral d'abord, puis sanction si récidive"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if not recidive:
        # 1er fois = avertissement oral (log uniquement)
        c.execute('INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut) VALUES (?, "avertissement_oral", "Téléphone personnel devant un client — avertissement oral", "faible", ?, "resolu")', (employe_id, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        return {"success": True, "type": "avertissement_oral", "message": "Avertissement oral enregistré (log)"}
    else:
        # Récidive = sanction officielle
        c.execute('INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, statut) VALUES (?, "avertissement", "Téléphone personnel devant un client (récidive)", "2e occurrence", ?, "actif")', (employe_id, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        return {"success": True, "type": "sanction", "message": "Sanction officielle pour récidive téléphone"}

@app.post("/sanctions/mauvaise-note")
async def sanction_mauvaise_note(employe_id: int, note: float):
    """1 mauvaise note isolée = avertissement écrit (PAS sanction officielle). 3/mois = 1re sanction"""
    conn = get_db_connection()
    c = conn.cursor()
    
    mois_actuel = datetime.now().strftime("%Y-%m")
    c.execute('SELECT COUNT(*) FROM notes_clients WHERE employe_id = ? AND date_rdv LIKE ? AND note_moyenne < 3', (employe_id, f"{mois_actuel}%"))
    nb_mauvaises = c.fetchone()[0] + 1  # +1 pour celle-ci
    
    if nb_mauvaises >= 3:
        # 3 mauvaises notes = 1re sanction officielle
        c.execute('INSERT INTO sanctions (employe_id, type, raison, details, date_sanction, statut) VALUES (?, "avertissement", "3 mauvaises notes clients en 1 mois", ?, ?, "actif")', (employe_id, f"Note {note}/5", datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        return {"success": True, "type": "sanction", "message": "1re sanction officielle : 3 mauvaises notes ce mois"}
    else:
        # Avertissement écrit (pas sanction officielle)
        c.execute('INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut) VALUES (?, "avertissement_ecrit", ?, "moyen", ?, "nouveau")', (employe_id, f"Mauvaise note {note}/5 — avertissement écrit", datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        return {"success": True, "type": "avertissement_ecrit", "message": f"Avertissement écrit (note {note}/5) — pas une sanction officielle"}

@app.post("/sanctions/pause-longue")
async def sanction_pause_longue(employe_id: int, duree_minutes: int):
    """Pause > 70 min = avertissement écrit"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut) VALUES (?, "avertissement_ecrit", ?, "moyen", ?, "nouveau")', (employe_id, f"Pause de {duree_minutes} minutes — avertissement écrit", datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    
    return {"success": True, "type": "avertissement_ecrit", "message": f"Avertissement écrit : pause de {duree_minutes} min"}

# ========== DOCUMENT AVERTISSEMENT AUTO ==========

@app.post("/sanctions/generer-document/{sanction_id}")
async def generer_document_avertissement(sanction_id: int):
    """Génère un document d'avertissement écrit automatique"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT s.*, e.nom as employe_nom, e.echelon
        FROM sanctions s
        JOIN employes e ON s.employe_id = e.id
        WHERE s.id = ?
    ''', (sanction_id,))
    sanction = c.fetchone()
    conn.close()
    
    if not sanction:
        raise HTTPException(status_code=404, detail="Sanction non trouvée")
    
    # Générer le document texte
    document = f"""
============================================
AVERTISSEMENT ÉCRIT — KADIO COIFFURE
============================================
Date : {sanction['date_sanction']}
Employé : {sanction['employe_nom']}
Échelon : {sanction['echelon']}

Niveau de sanction : {sanction['type']}
Motif : {sanction['raison']}
Détails : {sanction['details'] or 'Aucun'}

Ce document est archivé dans le dossier numérique de l'employé.

Signature du propriétaire : _________________
Date : {datetime.now().strftime("%Y-%m-%d")}
============================================
"""
    
    return {
        "success": True,
        "sanction_id": sanction_id,
        "document": document,
        "message": "Document d'avertissement généré"
    }

# ========== AUTHENTIFICATION PROPRIÉTAIRE (Section 12) ==========

@app.post("/auth/proprietaire/init")
async def init_proprietaire():
    """Crée le compte propriétaire initial (à appeler une seule fois au setup)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS proprietaire (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            nom TEXT NOT NULL,
            telephone TEXT NOT NULL,
            date_creation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    
    import hashlib
    password_hash = hashlib.sha256("Kadio2026!".encode()).hexdigest()
    
    c.execute('''
        INSERT OR IGNORE INTO proprietaire (id, email, password_hash, nom, telephone)
        VALUES (1, 'othi@kadio.co', ?, 'Othi Kadio', '+15149195970')
    ''', (password_hash,))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Compte propriétaire créé", "email": "othi@kadio.co"}

@app.post("/auth/proprietaire/login")
async def login_proprietaire(data: LoginProprioRequest):
    """Connexion propriétaire (email + mot de passe)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM proprietaire WHERE email = ?', (data.email,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    
    import hashlib
    password_hash = hashlib.sha256(data.password.encode()).hexdigest()
    
    if password_hash != row['password_hash']:
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    
    # Générer token simple (en prod: utiliser JWT)
    token = hashlib.sha256(f"{data.email}{datetime.now().isoformat()}".encode()).hexdigest()[:32]
    
    return {
        "success": True,
        "token": token,
        "proprietaire": {
            "id": row['id'],
            "nom": row['nom'],
            "email": row['email'],
            "telephone": row['telephone']
        }
    }

# ========== ALERTES — MARQUER COMME TRAITÉE (Section 11) ==========

@app.patch("/alertes/{alerte_id}/traiter")
async def traiter_alerte(alerte_id: int):
    """Marque une alerte comme traitée par le propriétaire"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM alertes WHERE id = ?', (alerte_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
    
    c.execute('''
        UPDATE alertes SET statut = 'traite', resolu_date = ?, resolu_par = 0, resolution = 'Marquée comme traitée par le propriétaire' WHERE id = ?
    ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alerte_id))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Alerte marquée comme traitée", "alerte_id": alerte_id}

# ========== TÂCHES MÉNAGÈRES — VÉRIFICATION 30 MIN AVANT FERMETURE (Section 6) ==========

@app.post("/taches/verifier-fermeture")
async def verifier_taches_avant_fermeture():
    """
    Vérifie 30 minutes avant la fermeture si toutes les tâches quotidiennes sont faites.
    Si une tâche manque → alerte immédiate au propriétaire.
    """
    now = datetime.now()
    jour_semaine = now.strftime("%A")  # Monday, Tuesday...
    heure_actuelle = now.hour
    
    # Horaires fermeture: Lun-Mar 19h, Mer 19h, Jeu-Sam 21h, Dim 17h
    fermeture_heures = {
        "Monday": 19, "Tuesday": 19, "Wednesday": 19,
        "Thursday": 21, "Friday": 21, "Saturday": 21, "Sunday": 17
    }
    heure_fermeture = fermeture_heures.get(jour_semaine, 19)
    
    # Vérifier si on est 30 min avant fermeture
    if heure_actuelle != heure_fermeture - 1:
        return {"success": True, "message": "Pas encore l'heure de vérification", "verifie_a": f"{heure_fermeture - 1}:30"}
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Récupérer les tâches quotidiennes non cochées aujourd'hui
    today = now.strftime("%Y-%m-%d")
    c.execute('''
        SELECT t.* FROM taches_menageres t
        LEFT JOIN historique_taches h ON t.id = h.tache_id AND h.date_realisation = ?
        WHERE t.frequence = 'quotidien' AND t.actif = 1 AND h.id IS NULL
    ''', (today,))
    taches_manquantes = c.fetchall()
    
    if not taches_manquantes:
        conn.close()
        return {"success": True, "message": "Toutes les tâches quotidiennes sont faites ✅", "taches_manquantes": 0}
    
    # Créer une alerte pour chaque tâche manquante
    alertes_crees = []
    for tache in taches_manquantes:
        message = f"🟠 ATTENTION — Tâche '{tache['nom']}' non complétée. Fermeture dans 30 min."
        c.execute('''
            INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
            VALUES (?, 'tache_manquante', ?, 'moyen', ?, 'nouveau')
        ''', (0, message, now.strftime("%Y-%m-%d %H:%M:%S")))
        alertes_crees.append({"tache_id": tache['id'], "nom": tache['nom']})
    
    # Envoyer SMS au propriétaire
    try:
        notification_manager.alerte_systeme(
            titre=f"⚠️ {len(taches_manquantes)} tâche(s) non complétée(s)",
            description=f"Fermeture dans 30 min. Tâches manquantes: {', '.join([t['nom'] for t in taches_manquantes])}",
            niveau="attention"
        )
    except:
        pass
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"{len(taches_manquantes)} tâche(s) manquante(s) détectée(s)",
        "taches_manquantes": [dict(t) for t in taches_manquantes],
        "alertes_crees": len(alertes_crees)
    }

# ========== TÂCHES MÉNAGÈRES — COMPTAGE MANQUEMENTS COLLECTIFS (Section 6) ==========

@app.post("/taches/manquement-collectif")
async def enregistrer_manquement_collectif(data: TacheManquement):
    """
    Enregistre un manquement collectif et applique les conséquences :
    1er manquement du mois = avertissement collectif
    2e manquement = 1re sanction pour chaque membre de l'équipe
    3e manquement = descente d'échelon pour toute l'équipe
    """
    conn = get_db_connection()
    c = conn.cursor()
    mois_actuel = datetime.now().strftime("%Y-%m")
    
    # Compter les manquements du mois
    c.execute('''
        SELECT COUNT(*) FROM alertes 
        WHERE type = 'manquement_collectif' AND date_alerte LIKE ?
    ''', (f"{mois_actuel}%",))
    compteur = c.fetchone()[0] + 1  # +1 pour celui qu'on vient d'ajouter
    
    # Récupérer le nom de la tâche
    c.execute('SELECT nom FROM taches_menageres WHERE id = ?', (data.tache_id,))
    tache_nom = c.fetchone()['nom']
    
    # Enregistrer le manquement
    message = f"Manquement collectif: '{tache_nom}' non complétée le {data.date}. Manquement #{compteur} du mois."
    c.execute('''
        INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
        VALUES (?, 'manquement_collectif', ?, 'moyen', ?, 'nouveau')
    ''', (0, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    # Récupérer tous les employés actifs
    c.execute('SELECT id, nom, echelon FROM employes WHERE statut = "actif"')
    employes = c.fetchall()
    
    consequences = []
    if compteur == 1:
        # 1er manquement = avertissement collectif
        for emp in employes:
            c.execute('''
                INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
                VALUES (?, 'avertissement_collectif', ?, 'faible', ?, 'nouveau')
            ''', (emp['id'], f"Avertissement collectif — tâche '{tache_nom}' non faite. Manquement #{compteur} du mois.", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        consequences.append("Avertissement collectif à toute l'équipe")
    
    elif compteur == 2:
        # 2e manquement = 1re sanction pour chaque membre
        for emp in employes:
            c.execute('''
                INSERT INTO sanctions (employe_id, type, raison, details, date_sanction)
                VALUES (?, 'avertissement', ?, ?, ?)
            ''', (emp['id'], f"Manquement collectif #{compteur}", f"Tâche '{tache_nom}' non complétée — sanction automatique", datetime.now().strftime("%Y-%m-%d")))
            # Descente d'échelon
            await verifier_descente_echelon(emp['id'])
        consequences.append("1re sanction pour chaque membre de l'équipe")
    
    elif compteur >= 3:
        # 3e manquement = descente d'échelon pour toute l'équipe
        for emp in employes:
            # Descente d'un échelon
            echelons_ordre = ['platine', 'or', 'argent', 'bronze']
            idx = echelons_ordre.index(emp['echelon']) if emp['echelon'] in echelons_ordre else 3
            if idx < 3:
                nouveau = echelons_ordre[idx + 1]
                c.execute('UPDATE employes SET echelon = ? WHERE id = ?', (nouveau, emp['id']))
                c.execute('''
                    INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
                    VALUES (?, 'descente_echelon', ?, 'moyen', ?, 'nouveau')
                ''', (emp['id'], f"Descente d'échelon collectif: {emp['echelon']} → {nouveau} (manquement #{compteur})", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        consequences.append("Descente d'échelon pour toute l'équipe")
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"Manquement collectif #{compteur} enregistré",
        "consequences": consequences,
        "employes_concernes": len(employes)
    }



# ========== VERROUILLAGE CHECKLIST -- CLOTURE DE FICHE CLIENT (Section H) ==========

@app.post("/services/cloturer")
async def cloturer_service(employe_id: int, date_service: str, client_nom: Optional[str] = None):
    """
    Verifie que la checklist est complete (6/6) avant de cloturer la fiche client.
    Si incomplet -> blocage avec message explicite.
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    # Verifier si checklist existe pour ce service
    c.execute('''
        SELECT * FROM checklist_service
        WHERE employe_id = ? AND date_service = ?
        ORDER BY created_at DESC LIMIT 1
    ''', (employe_id, date_service))
    checklist = c.fetchone()
    
    if not checklist:
        conn.close()
        raise HTTPException(
            status_code=403,
            detail="CLOTURE REFUSEE : La checklist d'accueil n'a pas ete remplie. Le coiffeur doit cocher les 5 regles d'accueil + telephone range avant de cloturer la fiche client."
        )
    
    # Verifier que tous les points sont coches (score = 10/10)
    points = sum([
        checklist['sourire'], checklist['guider'], checklist['offrir_boisson'],
        checklist['offrir_grignotine'], checklist['gerer_attente'], checklist['telephone_ranger']
    ])
    
    if points < 6:
        manquants = []
        if not checklist['sourire']: manquants.append('Regle 1: Sourire/Salutation')
        if not checklist['guider']: manquants.append('Regle 2: Guider le client')
        if not checklist['offrir_boisson']: manquants.append('Regle 3: Proposer boisson')
        if not checklist['offrir_grignotine']: manquants.append('Regle 4: Offrir grignotines')
        if not checklist['gerer_attente']: manquants.append("Regle 5: Annoncer temps d'attente")
        if not checklist['telephone_ranger']: manquants.append('Telephone range')
        
        conn.close()
        raise HTTPException(
            status_code=403,
            detail=f"CLOTURE REFUSEE : Checklist incomplete ({points}/6). Points manquants: {', '.join(manquants)}. Le coiffeur doit completer la checklist avant de cloturer la fiche client."
        )
    
    conn.close()
    return {
        "success": True,
        "message": "Fiche client cloturee avec succes -- Checklist complete (6/6)",
        "checklist_score": 10.0,
        "checklist_id": checklist["id"]
    }

# ========== COIFFEUR NOTE CLIENT (Section 7) ==========

@app.post("/notes-employes")
async def create_note_employe_client(data: NoteEmployeClientCreate):
    """Le coiffeur note un client après un service (3 critères : ponctualité, comportement, respect)"""
    # Validation des notes 1-5
    for critere, valeur in [("ponctualite", data.ponctualite), ("comportement", data.comportement), ("respect", data.respect)]:
        if not 1 <= valeur <= 5:
            raise HTTPException(status_code=400, detail=f"La note '{critere}' doit être entre 1 et 5")
    
    note_moyenne = round((data.ponctualite + data.comportement + data.respect) / 3, 2)
    date_service = data.date_service or datetime.now().strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO notes_employes (employe_id, client_nom, client_telephone, ponctualite, comportement, respect, commentaire, date_rdv)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data.employe_id, data.client_nom, data.client_telephone, data.ponctualite, data.comportement, data.respect, data.commentaire, date_service))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": "Note client enregistrée",
        "note_moyenne": float(note_moyenne),
        "visible_uniquement": "propriétaire"
    }

@app.get("/notes-employes/{employe_id}")
async def get_notes_employe_client(employe_id: int):
    """Historique des notes données par un employé (visible uniquement par le propriétaire)"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM notes_employes WHERE employe_id = ? ORDER BY date_service DESC', (employe_id,))
    rows = c.fetchall()
    conn.close()
    return {"success": True, "notes": [dict(row) for row in rows]}

# ========== GOOGLE AVIS — ENVOI CONDITIONNEL (Section 7) ==========

@app.post("/google-avis/envoyer-auto")
async def envoyer_google_avis_auto(data: GoogleAvisEnvoi):
    """
    Envoie un SMS Google Avis selon la note reçue :
    - 4-5/5 : lien Google Avis
    - 3/5 : alerte propriétaire uniquement
    - 1-2/5 : alerte URGENTE propriétaire
    """
    employe_nom = ""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT nom FROM employes WHERE id = ?', (data.employe_id,))
    row = c.fetchone()
    if row:
        employe_nom = row['nom']
    conn.close()
    
    if data.note_client >= 4:
        # Note 4-5/5 → SMS Google Avis
        message = f"Merci pour votre visite chez Kadio Coiffure ! Vous avez adoré ? Partagez votre expérience sur Google : https://g.page/r/CYvPRp7xG8o9EBM/review"
        if data.note_client == 4:
            message = f"Merci de votre visite chez Kadio Coiffure ! Votre avis nous aide à grandir : https://g.page/r/CYvPRp7xG8o9EBM/review"
        
        # Enregistrer
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            INSERT INTO google_avis (client_nom, client_telephone, note_client, employe_id, sms_envoye, date_sms_envoye)
            VALUES (?, ?, ?, ?, 1, ?)
        ''', (data.client_nom, data.client_telephone, data.note_client, data.employe_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        
        # Info alerte
        await create_alerte(AlerteCreate(
            employe_id=data.employe_id,
            type="google_avis_envoye",
            description=f"Note {data.note_client}/5 pour {employe_nom} — lien Google Avis envoyé au client.",
            niveau="faible",
            date_alerte=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        
        return {"success": True, "action": "sms_google_avis", "message": message, "note": data.note_client}
    
    elif data.note_client == 3:
        # Note 3/5 → Alerte privée propriétaire
        await create_alerte(AlerteCreate(
            employe_id=data.employe_id,
            type="note_moyenne",
            description=f"Note 3/5 reçue pour {employe_nom}. Client: {data.client_nom}. Aucun lien Google envoyé.",
            niveau="moyen",
            date_alerte=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        return {"success": True, "action": "alerte_proprietaire", "message": "Alerte envoyée au propriétaire (note 3/5)", "note": data.note_client}
    
    else:
        # Note 1-2/5 → Alerte URGENTE
        commentaire = data.commentaire_client or "Aucun commentaire"
        await create_alerte(AlerteCreate(
            employe_id=data.employe_id,
            type="note_critique",
            description=f"ATTENTION — Note {data.note_client}/5 reçue pour {employe_nom}. Commentaire : {commentaire}. Action requise.",
            niveau="critique",
            date_alerte=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        return {"success": True, "action": "alerte_urgente", "message": f"Alerte URGENTE envoyée au propriétaire (note {data.note_client}/5)", "note": data.note_client}

# ========== EMPLOYÉ DU MOIS — CONFIRMATION ET RÉCOMPENSES (Section 10) ==========

@app.post("/employe-du-mois/confirmer")
async def confirmer_employe_du_mois(data: EmployeDuMoisConfirm):
    """
    Le propriétaire confirme l'employé du mois et les récompenses sont enregistrées.
    Récompenses : photo salon + RS + carte-cadeau 50$ + mention permanente.
    """
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier l'employé
    c.execute('SELECT nom, echelon FROM employes WHERE id = ?', (data.employe_id,))
    emp = c.fetchone()
    if not emp:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    # Enregistrer la récompense
    c.execute('''
        INSERT INTO employes_mois (employe_id, mois, annee, score_total, recompense, date_selection)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (data.employe_id, data.mois, data.annee, 0, data.recompense, datetime.now().strftime("%Y-%m-%d")))
    
    # Notification à l'équipe
    c.execute('SELECT nom FROM employes WHERE statut = "actif"')
    equipe = c.fetchall()
    
    message_equipe = f"🎉 Félicitations à {emp['nom']} — Employé(e) du mois de {data.mois}/{data.annee} ! Récompense : {data.recompense}."
    
    for membre in equipe:
        c.execute('''
            INSERT INTO alertes (employe_id, type, description, niveau, date_alerte, statut)
            VALUES (?, 'annonce_equipe', ?, 'faible', ?, 'nouveau')
        ''', (data.employe_id, message_equipe, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"Employé du mois confirmé : {emp['nom']}",
        "recompenses": [
            "Photo affichée dans le salon",
            "Mis en avant sur Instagram Kadio et tous les réseaux sociaux",
            f"Carte-cadeau {data.recompense}",
            f"Mention permanente : 'Employé du mois de {data.mois}/{data.annee}'"
        ]
    }

@app.post("/employe-du-mois/calculer-auto")
async def calculer_employe_du_mois_auto():
    """
    Calcule automatiquement l'employé du mois et notifie le propriétaire pour confirmation.
    À appeler le dernier jour du mois à 23h59.
    """
    now = datetime.now()
    mois_precedent = (now - timedelta(days=15)).strftime("%B")  # Mois en cours
    annee = now.year
    
    # Calculer les scores
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT id, nom FROM employes WHERE statut = "actif" OR statut IS NULL OR statut = "" OR statut = "present"')
    employes = c.fetchall()
    
    scores = []
    for emp in employes:
        emp_id = emp['id']
        
        # Note moyenne clients (35%)
        c.execute('SELECT AVG(note_moyenne) FROM notes_clients WHERE employe_id = ?', (emp_id,))
        note_moyenne = c.fetchone()[0] or 0
        
        # Ponctualité (25%) — retard moyen en minutes
        c.execute('SELECT AVG(retard_minutes) FROM pointages WHERE employe_id = ? AND retard_minutes > 0', (emp_id,))
        retard_moyen = c.fetchone()[0] or 0
        ponctualite_score = max(0, 100 - (retard_moyen * 5))  # -5 pts par minute de retard
        
        # Tâches ménagères (20%)
        c.execute('SELECT COUNT(*) FROM historique_taches WHERE employe_id = ?', (emp_id,))
        taches_count = c.fetchone()[0] or 0
        taches_score = min(100, taches_count * 5)  # +5 pts par tâche, max 100
        
        # Checklist service (20%)
        c.execute('SELECT AVG(score_checklist) FROM checklist_service WHERE employe_id = ?', (emp_id,))
        checklist_avg = c.fetchone()[0] or 0
        checklist_score = (checklist_avg / 10) * 100 if checklist_avg else 0
        
        # Score total pondéré
        score_total = (note_moyenne * 20 * 0.35) + (ponctualite_score * 0.25) + (taches_score * 0.20) + (checklist_score * 0.20)
        
        scores.append({
            "employe_id": emp_id,
            "nom": emp['nom'],
            "score_total": round(score_total, 2),
            "details": {
                "note_clients": round(note_moyenne, 2),
                "ponctualite": round(ponctualite_score, 2),
                "taches": round(taches_score, 2),
                "checklist": round(checklist_score, 2)
            }
        })
    
    # Trier par score décroissant
    scores.sort(key=lambda x: x['score_total'], reverse=True)
    gagnant = scores[0] if scores else None
    
    conn.close()
    
    if gagnant:
        # Notifier le propriétaire pour confirmation
        await create_alerte(AlerteCreate(
            employe_id=gagnant['employe_id'],
            type="employe_du_mois",
            description=f"Employé du mois calculé : {gagnant['nom']} avec {gagnant['score_total']}/100. Confirmer sur /employe-du-mois/confirmer",
            niveau="faible",
            date_alerte=now.strftime("%Y-%m-%d %H:%M:%S")
        ))
    
    return {
        "success": True,
        "mois": mois_precedent,
        "annee": annee,
        "classement": scores,
        "gagnant": gagnant,
        "message": "Calcul effectué. Le propriétaire doit confirmer sur /employe-du-mois/confirmer"
    }

# ========== ADMIN HUB — RETRAITS CASH PARRAINAGE ==========

@app.post("/retraits/demander")
async def demander_retrait(data: DemandeRetrait):
    """Crée une demande de retrait cash (parrainage ou fidélité)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO demandes_retraits (client_telephone, client_nom, montant, motif, statut, date_demande)
        VALUES (?, ?, ?, ?, 'en_attente', ?)
    ''', (data.client_telephone, data.client_nom, data.montant, data.motif, datetime.now().isoformat()))
    
    demande_id = c.lastrowid
    conn.commit()
    conn.close()
    
    # Notifier le propriétaire
    try:
        message = f"💰 NOUVELLE DEMANDE RETRAIT #{demande_id}\n\n"
        message += f"Client: {data.client_nom or data.client_telephone}\n"
        message += f"Montant: {data.montant}$\n"
        message += f"Motif: {data.motif}\n\n"
        message += f"Validez sur: /admin/retraits"
        
        import os
        TELEPHONE_PROPRIETAIRE = os.getenv("TELEPHONE_PROPRIETAIRE", "")
        if TELEPHONE_PROPRIETAIRE:
            from notifications import notification_manager
            notification_manager.alerte_systeme(message, niveau="moyen")
    except Exception as e:
        print(f"Erreur notification retrait: {e}")
    
    return {"success": True, "demande_id": demande_id, "message": "Demande de retrait enregistrée"}

@app.get("/retraits")
async def lister_retraits(statut: Optional[str] = None):
    """Liste les demandes de retrait (filtre par statut)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if statut:
        c.execute('''
            SELECT * FROM demandes_retraits WHERE statut = ? ORDER BY date_demande DESC
        ''', (statut,))
    else:
        c.execute('''
            SELECT * FROM demandes_retraits ORDER BY date_demande DESC
        ''')
    
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/retraits/{demande_id}/valider")
async def valider_retrait(demande_id: int, data: ValidationRetrait):
    """Valide ou refuse une demande de retrait (par le propriétaire)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Vérifier que la demande existe
    c.execute('SELECT * FROM demandes_retraits WHERE id = ?', (demande_id,))
    demande = c.fetchone()
    if not demande:
        conn.close()
        raise HTTPException(status_code=404, detail="Demande de retrait non trouvée")
    
    # Mettre à jour le statut
    c.execute('''
        UPDATE demandes_retraits 
        SET statut = ?, date_validation = ?, valide_par = ?, commentaire = ?
        WHERE id = ?
    ''', (data.statut, datetime.now().isoformat(), "Propriétaire", data.commentaire, demande_id))
    
    conn.commit()
    conn.close()
    
    # Notifier le client si validé ou refusé
    try:
        if data.statut == "valide":
            message = f"✅ Votre demande de retrait de {demande['montant']}$ a été APPROUVÉE.\n\n"
            message += f"Rendez-vous au salon pour récupérer votre cash.\n"
            message += f"Commentaire: {data.commentaire or 'Aucun'}"
        elif data.statut == "refuse":
            message = f"❌ Votre demande de retrait de {demande['montant']}$ a été REFUSÉE.\n\n"
            message += f"Raison: {data.commentaire or 'Contactez le salon pour plus d\'infos.'}"
        else:
            message = None
        
        if message and demande['client_telephone']:
            from notifications import notification_manager
            notification_manager.envoyer_sms(demande['client_telephone'], message)
    except Exception as e:
        print(f"Erreur notification client: {e}")
    
    return {"success": True, "message": f"Demande {data.statut}"}

# ========== ADMIN HUB — NOTATION BIDIRECTIONNELLE ==========

@app.post("/notes-employes")
async def create_note_employe(note: NoteEmployeCreate):
    """Un employé laisse une note secrète sur un client"""
    if not 1 <= note.note <= 5:
        raise HTTPException(status_code=400, detail="La note doit être entre 1 et 5")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO notes_employes (employe_id, client_telephone, client_nom, note, commentaire, tags, date_rdv, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (note.employe_id, note.client_telephone, note.client_nom, note.note, 
          note.commentaire, note.tags, note.date_rdv, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Note secrète enregistrée"}

@app.get("/notes-employes")
async def lister_notes_employes(employe_id: Optional[int] = None, client_telephone: Optional[str] = None):
    """Liste les notes secrètes des employés sur les clients (visible admin)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if employe_id:
        c.execute('''
            SELECT ne.*, e.nom as employe_nom 
            FROM notes_employes ne
            JOIN employes e ON ne.employe_id = e.id
            WHERE ne.employe_id = ? ORDER BY ne.created_at DESC
        ''', (employe_id,))
    elif client_telephone:
        c.execute('''
            SELECT ne.*, e.nom as employe_nom 
            FROM notes_employes ne
            JOIN employes e ON ne.employe_id = e.id
            WHERE ne.client_telephone = ? ORDER BY ne.created_at DESC
        ''', (client_telephone,))
    else:
        c.execute('''
            SELECT ne.*, e.nom as employe_nom 
            FROM notes_employes ne
            JOIN employes e ON ne.employe_id = e.id
            ORDER BY ne.created_at DESC
        ''')
    
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ========== ADMIN HUB AVANCÉ : TOUR DE CONTRÔLE TOTALE ==========

class OverrideRetard(BaseModel):
    employe_id: int
    date_journee: str
    nouvelle_heure: str
    raison: str

class OverrideSolde(BaseModel):
    client_telephone: str
    montant: float
    type_solde: str = "parrainage"  # parrainage, fidelite
    raison: str

class OverrideTache(BaseModel):
    tache_id: int
    nouveau_statut: str
    raison: str

class QrCodeCreate(BaseModel):
    client_telephone: str
    type: str = "parrainage"
    date_expiration: Optional[str] = None

# --- SUPERVISION STAFF TOTALE ---

@app.get("/admin/staff/{employe_id}/historique-complet")
async def historique_complet_employe(employe_id: int):
    """Récupère l'historique complet d'un employé (pointages, retards, sanctions, checklists)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM employes WHERE id = ?", (employe_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    employe = dict(row)
    
    c.execute("SELECT * FROM pointages WHERE employe_id = ? ORDER BY date_journee DESC", (employe_id,))
    pointages = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT * FROM sanctions WHERE employe_id = ? ORDER BY date_sanction DESC", (employe_id,))
    sanctions = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT * FROM notes_clients WHERE employe_id = ? ORDER BY created_at DESC", (employe_id,))
    notes = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT * FROM historique_taches WHERE employe_id = ? ORDER BY date_realisation DESC", (employe_id,))
    taches = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT * FROM historique_staff WHERE employe_id = ? ORDER BY date_action DESC", (employe_id,))
    historique = [dict(r) for r in c.fetchall()]
    
    conn.close()
    
    return {
        "employe_id": employe_id,
        "employe": employe,
        "pointages": pointages,
        "sanctions": sanctions,
        "notes_clients": notes,
        "taches_menageres": taches,
        "historique_modifications": historique,
        "total_pointages": len(pointages),
        "total_sanctions": len(sanctions),
        "total_taches": len(taches)
    }

# --- CONTRÔLE PARRAINAGE & COFFRE-FORT VIRTUEL ---

@app.get("/admin/coffre-fort")
async def coffre_fort_global():
    """Tableau de bord du coffre-fort virtuel (tous les clients)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM coffre_fort_virtuel ORDER BY solde_parrainage DESC")
    rows = c.fetchall()
    
    result = []
    for row in rows:
        r = dict(row)
        c.execute("SELECT code_qr, type, statut FROM qr_codes_actifs WHERE client_telephone = ? AND statut = 'actif'", (r['client_telephone'],))
        r['qr_codes'] = [dict(q) for q in c.fetchall()]
        result.append(r)
    
    conn.close()
    
    total_solde = sum(r['solde_parrainage'] for r in result)
    total_clients = len(result)
    
    return {
        "clients": result,
        "total_clients": total_clients,
        "total_solde_parrainage": total_solde,
        "total_qr_actifs": sum(len(r['qr_codes']) for r in result)
    }

@app.post("/admin/qr-code/generer")
async def generer_qr_code(data: QrCodeCreate):
    """Génère un QR code pour un client"""
    import uuid
    code = f"KADIO-{uuid.uuid4().hex[:8].upper()}"
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        INSERT INTO qr_codes_actifs (client_telephone, code_qr, type, date_expiration)
        VALUES (?, ?, ?, ?)
    ''', (data.client_telephone, code, data.type, data.date_expiration))
    
    c.execute('''
        INSERT OR IGNORE INTO coffre_fort_virtuel (client_telephone, date_creation)
        VALUES (?, ?)
    ''', (data.client_telephone, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "code_qr": code, "client": data.client_telephone}

# --- INTERCEPTION FLUX NOTATION ---

@app.get("/admin/alertes-notation")
async def alertes_notation():
    """Alertes instantanées pour notes < 3/5 (à modérer avant Google)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT * FROM notes_clients 
        WHERE note_moyenne < 3 
        ORDER BY created_at DESC
    ''')
    notes_critiques = [dict(r) for r in c.fetchall()]
    
    c.execute('''
        SELECT * FROM notes_clients 
        WHERE note_moyenne >= 3 AND note_moyenne < 4
        ORDER BY created_at DESC
    ''')
    notes_moyennes = [dict(r) for r in c.fetchall()]
    
    conn.close()
    
    return {
        "critiques": notes_critiques,
        "moyennes": notes_moyennes,
        "total_a_moderer": len(notes_critiques) + len(notes_moyennes)
    }

# --- BOUTON D'OVERRIDE (CONTournement) ---

@app.post("/admin/override/retard")
async def override_retard(data: OverrideRetard):
    """Le propriétaire peut effacer ou modifier un retard manuellement"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        UPDATE pointages 
        SET heure_arrivee = ?, retard = 0
        WHERE employe_id = ? AND date_journee = ?
    ''', (data.nouvelle_heure, data.employe_id, data.date_journee))
    
    c.execute('''
        INSERT INTO historique_staff (employe_id, type_action, description, date_action, statut, details, modifie_par)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (data.employe_id, "OVERRIDE", f"Retard modifié par admin", datetime.now().isoformat(), "modifie", json.dumps({"raison": data.raison}), "ADMIN"))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Retard modifié par override"}

@app.post("/admin/override/solde")
async def override_solde(data: OverrideSolde):
    """Le propriétaire peut ajuster un solde de parrainage/fidélité"""
    conn = get_db_connection()
    c = conn.cursor()
    
    col = "solde_parrainage" if data.type_solde == "parrainage" else "solde_fidelite"
    
    c.execute(f'''
        UPDATE coffre_fort_virtuel 
        SET {col} = {col} + ?, derniere_maj = ?
        WHERE client_telephone = ?
    ''', (data.montant, datetime.now().isoformat(), data.client_telephone))
    
    c.execute('''
        INSERT INTO historique_staff (employe_id, type_action, description, date_action, statut, details, modifie_par)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (0, "OVERRIDE", f"Solde {data.type_solde} ajusté de {data.montant}$", datetime.now().isoformat(), "modifie", json.dumps({"client": data.client_telephone, "raison": data.raison}), "ADMIN"))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": f"Solde ajusté de {data.montant}$"}

@app.post("/admin/override/tache")
async def override_tache(data: OverrideTache):
    """Le propriétaire peut modifier le statut d'une tâche ménagère"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        UPDATE taches_menageres 
        SET statut = ?
        WHERE id = ?
    ''', (data.nouveau_statut, data.tache_id))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Tâche modifiée par override"}

# ========== MODE TEST SANDBOX (Section 12) ==========

@app.post("/mode-test/activer")
async def activer_mode_test():
    """Active le mode test (sandbox) — les SMS ne sont pas envoyés réellement"""
    os.environ['MODE_TEST'] = '1'
    return {"success": True, "mode": "test", "message": "Mode test activé. Les SMS ne seront pas envoyés réellement."}

@app.post("/mode-test/desactiver")
async def desactiver_mode_test():
    """Désactive le mode test — les SMS sont envoyés réellement"""
    os.environ['MODE_TEST'] = '0'
    return {"success": True, "mode": "production", "message": "Mode production activé. Les SMS seront envoyés réellement."}

@app.get("/mode-test/status")
async def status_mode_test():
    """Vérifie si le mode test est actif"""
    mode_test = os.environ.get('MODE_TEST', '0') == '1'
    return {
        "success": True,
        "mode_test": mode_test,
        "message": "Mode test actif" if mode_test else "Mode production actif"
    }


@app.get("/employe/{employe_id}/commissions")
async def get_commissions_employe(employe_id: int):
    """Récupère les commissions, parrainages et gains d'un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    # Générer un code de parrainage personnel si non existant
    c.execute('SELECT code_parrainage FROM employes WHERE id = ?', (employe_id,))
    code_parrainage = c.fetchone()[0]
    if not code_parrainage:
        import uuid
        code_parrainage = f"EMP-{employe_id}-{str(uuid.uuid4().hex[:6]).upper()}"
        c.execute('UPDATE employes SET code_parrainage = ? WHERE id = ?', (code_parrainage, employe_id))
        conn.commit()
    
    # Récupérer l'historique des commissions
    c.execute('''
        SELECT id, type, montant, description, client_telephone, statut, date_creation
        FROM commissions_employes
        WHERE employe_id = ?
        ORDER BY date_creation DESC
    ''', (employe_id,))
    commissions = [dict(r) for r in c.fetchall()]
    
    # Calculer les totaux
    c.execute('''
        SELECT COALESCE(SUM(montant), 0) as total
        FROM commissions_employes
        WHERE employe_id = ? AND statut = 'valide'
    ''', (employe_id,))
    solde_total = c.fetchone()['total']
    
    c.execute('''
        SELECT COUNT(*) as nb FROM commissions_employes
        WHERE employe_id = ? AND type = 'parrainage' AND statut = 'valide'
    ''', (employe_id,))
    nb_parrainages = c.fetchone()['nb']
    
    c.execute('''
        SELECT COUNT(*) as nb FROM commissions_employes
        WHERE employe_id = ? AND type = 'abonnement' AND statut = 'valide'
    ''', (employe_id,))
    nb_abonnements = c.fetchone()['nb']
    
    conn.close()
    
    return {
        "employe_id": employe_id,
        "code_parrainage": code_parrainage,
        "solde_total": round(solde_total, 2),
        "nb_parrainages": nb_parrainages,
        "nb_abonnements": nb_abonnements,
        "historique": commissions
    }


@app.post("/employe/{employe_id}/retrait-gains")
async def demander_retrait_gains(employe_id: int, data: dict):
    """Demande de retrait de gains par un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    montant = data.get('montant', 0)
    if montant <= 0:
        conn.close()
        raise HTTPException(status_code=400, detail="Montant invalide")
    
    # Créer une demande de retrait
    c.execute('''
        INSERT INTO commissions_employes (employe_id, type, montant, description, statut)
        VALUES (?, 'retrait', ?, ?, 'en_attente')
    ''', (employe_id, -montant, f"Demande de retrait de {montant}$"))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"Demande de retrait de {montant}$ envoyée au propriétaire",
        "statut": "en_attente"
    }


@app.post("/employe/{employe_id}/commission/abonnement")
async def ajouter_commission_abonnement(employe_id: int, data: dict):
    """Ajoute une commission de vente d'abonnement à un employé (+15$)"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    c.execute('''
        INSERT INTO commissions_employes (employe_id, type, montant, description, client_telephone, statut)
        VALUES (?, 'abonnement', 15, ?, ?, 'valide')
    ''', (employe_id, data.get('description', 'Vente abonnement mensuel'), data.get('client_telephone')))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": "Commission de 15$ ajoutée pour la vente d'abonnement",
        "commission_id": c.lastrowid
    }


@app.post("/employe/{employe_id}/commission/parrainage")
async def ajouter_commission_parrainage(employe_id: int, data: dict):
    """Ajoute une commission de parrainage à un employé"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM employes WHERE id = ?', (employe_id,))
    employe = c.fetchone()
    if not employe:
        conn.close()
        raise HTTPException(status_code=404, detail="Employé non trouvé")
    
    montant = data.get('montant', 10)
    c.execute('''
        INSERT INTO commissions_employes (employe_id, type, montant, description, client_telephone, statut)
        VALUES (?, 'parrainage', ?, ?, ?, 'valide')
    ''', (employe_id, montant, data.get('description', 'Parrainage client'), data.get('client_telephone')))
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": f"Commission de {montant}$ ajoutée pour le parrainage",
        "commission_id": c.lastrowid
    }


@app.get("/admin/base-clients")
async def get_base_clients():
    """Base de données client et parrainage intégrale - registre complet"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Tous les clients (depuis portefeuilles_clients)
    c.execute('''
        SELECT client_telephone, client_nom, created_at as date_inscription,
               points_fidelite as solde, qr_code
        FROM portefeuilles_clients
        ORDER BY created_at DESC
    ''')
    clients_raw = c.fetchall()
    clients = [dict(r) for r in clients_raw]
    
    # Abonnés (depuis abonnements_clients et abonnements_clients_v2)
    c.execute('''
        SELECT 
            client_telephone, type_forfait, date_inscription, employe_vendeur, 
            prochain_paiement, actif,
            COALESCE(prix_ht, 0) as prix_ht,
            COALESCE(taxes_qc, 0) as taxes_qc,
            COALESCE(prix_ttc, 0) as prix_ttc,
            mode_paiement, code_parrainage, statut
        FROM abonnements_clients_v2
        ORDER BY date_inscription DESC
    ''')
    abonnes_raw = c.fetchall()
    abonnes = [dict(r) for r in abonnes_raw]
    
    # Arbre du parrainage (depuis parrainages_clients - adapté aux colonnes réelles)
    c.execute('''
        SELECT p.*, e.nom as employe_nom
        FROM parrainages_clients p
        LEFT JOIN employes e ON e.telephone = p.parrain_telephone
        ORDER BY p.date_creation DESC
    ''')
    parrainages_raw = c.fetchall()
    arbre = []
    for p in parrainages_raw:
        arbre.append({
            'parrain_telephone': p['parrain_telephone'],
            'parrain_nom': p['employe_nom'] if p['employe_nom'] else p['parrain_telephone'],
            'parrain_type': 'client' if p['parrain_telephone'] and not p['employe_nom'] else 'employe',
            'filleul_telephone': p['filleul_telephone'],
            'filleul_nom': p['filleul_telephone'],  # On n'a pas le nom, on utilise le téléphone
            'code_qr': p['code_parrainage'],
            'date_parrainage': p['date_creation'],
            'service_achete': p['statut'] or 'Inscription',
            'montant_depense': None,
            'gain_parrain': None,
            'actif': p['statut'] == 'actif'
        })
    
    # Calculer le solde global virtuel
    c.execute('SELECT COALESCE(SUM(points_fidelite), 0) as total FROM portefeuilles_clients')
    solde_global = c.fetchone()['total'] or 0
    
    # Ajouter les soldes de parrainage employés
    c.execute('''
        SELECT COALESCE(SUM(montant), 0) as total 
        FROM commissions_employes 
        WHERE statut = 'valide' AND type IN ('parrainage', 'abonnement')
    ''')
    solde_employes = c.fetchone()['total'] or 0
    
    conn.close()
    
    return {
        "total_clients": len(clients),
        "total_abonnes": len(abonnes),
        "total_parrains": len(arbre),
        "solde_global": round(solde_global + solde_employes, 2),
        "clients": clients,
        "abonnes": abonnes,
        "arbre_parrainage": arbre
    }



# ==================== CLIENT PORTAL (LIEN 3) ====================

@app.get("/client")
async def client_page():
    """Page portail client (Ambassadeur Kadio)"""
    import os
    possible_paths = [
        "backend/client.html",
        "client.html",
        os.path.join(os.path.dirname(__file__), "client.html"),
        "/app/client.html"
    ]
    
    html = None
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                html = f.read()
            break
        except FileNotFoundError:
            continue
    
    if html is None:
        return {"error": "Page client not found", "checked_paths": possible_paths}
    
    return Response(content=html, media_type="text/html")


@app.post("/api/client/register")
async def register_client(request: Request):
    """Inscription client"""
    data = await request.json()
    telephone = data.get('telephone')
    nom = data.get('nom', 'Client')
    
    if not telephone:
        return {"error": "Telephone requis"}
    
    conn = get_db_connection()
    c = conn.cursor()
    
    import random
    import string
    code_parrainage = 'KADIO-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    try:
        c.execute("INSERT INTO comptes_clients (telephone, nom, code_parrainage, solde_parrainage, points_fidelite) VALUES (?, ?, ?, 0, 0)",
                  (telephone, nom, code_parrainage))
        conn.commit()
        
        c.execute('SELECT * FROM portefeuilles_clients WHERE client_telephone = ?', (telephone,))
        if not c.fetchone():
            c.execute("INSERT INTO portefeuilles_clients (client_telephone, client_nom, points_fidelite, qr_code) VALUES (?, ?, 0, ?)",
                      (telephone, nom, code_parrainage))
            conn.commit()
        
        return {"success": True, "code_parrainage": code_parrainage}
    except sqlite3.IntegrityError:
        c.execute('SELECT code_parrainage FROM comptes_clients WHERE telephone = ?', (telephone,))
        existing = c.fetchone()
        return {"success": True, "code_parrainage": existing['code_parrainage'] if existing else None}
    finally:
        conn.close()


@app.get("/api/client/{telephone}")
async def get_client_info(telephone: str):
    """Get client info"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT * FROM comptes_clients WHERE telephone = ?', (telephone,))
    client = c.fetchone()
    
    if not client:
        c.execute('SELECT * FROM clients WHERE telephone = ?', (telephone,))
        client = c.fetchone()
        if not client:
            conn.close()
            return {"error": "Client not found"}
    
    result = dict(client)
    conn.close()
    return result


@app.get("/api/client/{telephone}/wallet")
async def get_client_wallet(telephone: str):
    """Get client wallet"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT solde_parrainage, points_fidelite FROM comptes_clients WHERE telephone = ?', (telephone,))
    row = c.fetchone()
    
    if not row:
        c.execute('SELECT points_fidelite FROM portefeuilles_clients WHERE client_telephone = ?', (telephone,))
        row = c.fetchone()
        conn.close()
        if row:
            return {"solde": 0, "points_fidelite": row['points_fidelite'] or 0}
        return {"solde": 0, "points_fidelite": 0}
    
    conn.close()
    return {"solde": row['solde_parrainage'] or 0, "points_fidelite": row['points_fidelite'] or 0}


@app.get("/api/client/{telephone}/filleuls")
async def get_client_filleuls(telephone: str):
    """Get client filleuls"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT filleul_telephone, date_creation FROM parrainages_clients WHERE parrain_telephone = ?', (telephone,))
    filleuls = [dict(r) for r in c.fetchall()]
    conn.close()
    return filleuls


@app.get("/api/client/{telephone}/rendezvous")
async def get_client_rendezvous(telephone: str):
    """Get client appointments"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM rendez_vous_clients WHERE client_telephone = ? AND statut != 'annule' ORDER BY date_rdv, heure_rdv", (telephone,))
    rdv = [dict(r) for r in c.fetchall()]
    conn.close()
    return rdv


@app.post("/api/client/rendezvous")
async def book_client_rendezvous(request: Request):
    """Book appointment"""
    data = await request.json()
    telephone = data.get('telephone')
    service = data.get('service')
    date = data.get('date')
    heure = data.get('heure')
    notes = data.get('notes', '')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("INSERT INTO rendez_vous_clients (client_telephone, service, date_rdv, heure_rdv, notes) VALUES (?, ?, ?, ?, ?)",
              (telephone, service, date, heure, notes))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Rendez-vous confirmé"}


@app.post("/api/client/abonnement")
async def subscribe_client_abonnement(request: Request):
    """Subscribe to plan — Stripe avec taxes QC et code parrainage"""
    data = await request.json()
    telephone = data.get('telephone')
    forfait = data.get('forfait')
    paiement = data.get('paiement', 'comptant')
    code_parrainage = data.get('code_parrainage')
    email = data.get('email', f"{telephone}@kadio.co")
    
    # Prix HT
    prices = {'mensuel': 80, 'trimestriel': 220, 'annuel': 800}
    prix_ht = prices.get(forfait, 80)
    
    # Taxes Québec : 14.975%
    TAX_RATE = 0.14975
    prix_ttc = round(prix_ht * (1 + TAX_RATE), 2)
    taxes = round(prix_ht * TAX_RATE, 2)
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Si paiement Stripe, créer le lien
    stripe_link = None
    stripe_session_id = None
    
    if paiement == 'stripe':
        result = await stripe.create_subscription_link(
            customer_email=email,
            forfait=forfait,
            code_parrainage=code_parrainage,
            phone=telephone
        )
        if result.get('success'):
            stripe_link = result.get('link')
            stripe_session_id = result.get('session_id')
        else:
            conn.close()
            return {"error": result.get('error', 'Erreur Stripe')}
    
    c.execute("""
        INSERT INTO abonnements_clients_v2 
        (client_telephone, type_forfait, montant_mensuel, mode_paiement, 
         prix_ht, taxes_qc, prix_ttc, code_parrainage, stripe_session_id, statut)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (telephone, forfait, prix_ht, paiement, prix_ht, taxes, prix_ttc, 
          code_parrainage, stripe_session_id, 'actif' if not stripe_link else 'en_attente_paiement'))
    conn.commit()
    conn.close()
    
    response = {
        "success": True, 
        "message": "Abonnement souscrit" if paiement != 'stripe' else "Paiement Stripe requis",
        "forfait": forfait,
        "prix_ht": prix_ht,
        "taxes_qc": taxes,
        "prix_ttc": prix_ttc,
        "mode_paiement": paiement
    }
    
    if stripe_link:
        response["stripe_link"] = stripe_link
        response["stripe_session_id"] = stripe_session_id
        response["message"] = f"Abonnement {forfait} — {prix_ht}$ + {taxes}$ taxes = {prix_ttc}$ TTC. Cliquez sur le lien Stripe pour payer."
    
    return response


@app.get("/api/client/{telephone}/codes")
async def get_client_codes(telephone: str):
    """Get QR code and digital code"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('SELECT code_parrainage, qr_code_url FROM comptes_clients WHERE telephone = ?', (telephone,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return {"code": "KADIO-DEMO", "qr_url": None}
    
    conn.close()
    return {"code": row['code_parrainage'], "qr_url": row['qr_code_url']}


@app.get("/api/client/{telephone}/historique-gains")
async def get_client_historique_gains(telephone: str):
    """Get commission history"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM commissions_parrainage WHERE parrain_telephone = ? ORDER BY date_creation DESC", (telephone,))
    gains = [dict(r) for r in c.fetchall()]
    conn.close()
    return gains


@app.post("/api/client/retrait")
async def request_client_retrait(request: Request):
    """Request cash withdrawal"""
    data = await request.json()
    telephone = data.get('telephone')
    montant = data.get('montant', 0)
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("INSERT INTO demandes_retraits_clients (client_telephone, montant, statut) VALUES (?, ?, 'en_attente')",
              (telephone, montant))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Demande de retrait envoyée"}


@app.post("/api/client/analyze-selfie")
async def analyze_selfie(request: Request):
    """Analyze selfie with Gemini"""
    return {
        "models": [
            {"name": "Box Braids", "description": "Tresses carrées tendance", "image": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400"},
            {"name": "Twist Senegalese", "description": "Twists élégants et protecteurs", "image": "https://images.unsplash.com/photo-1522337360788-8b13dee7a37e?w=400"},
            {"name": "Cornrows", "description": "Nattes collées classiques", "image": "https://images.unsplash.com/photo-1492106087820-71f1a00d2b11?w=400"}
        ]
    }


@app.post("/api/client/calculate-commission")
async def calculate_commission(request: Request):
    """Calculate commission based on official rates"""
    data = await request.json()
    montant = data.get('montant', 0)
    type_depense = data.get('type', 'prestation')
    
    if type_depense == 'prestation':
        if montant <= 100:
            gain = 10
        elif montant <= 200:
            gain = 15
        elif montant <= 300:
            gain = 20
        elif montant <= 400:
            gain = 30
        elif montant <= 500:
            gain = 40
        elif montant <= 700:
            gain = 50
        elif montant <= 1000:
            gain = 75
        elif montant <= 2000:
            gain = 100
        else:
            gain = 150
    else:  # formation
        # Barème formations Kadio:
        # - Moins de 1000$ → 100$ récompense
        # - 1000$ et plus → 200$ récompense
        if montant < 1000:
            gain = 100
        else:
            gain = 200
    
    return {"montant": montant, "type": type_depense, "gain": gain}


@app.get("/api/formations")
async def get_formations():
    """Liste des formations disponibles"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM formations ORDER BY prix')
    formations = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"formations": formations}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
