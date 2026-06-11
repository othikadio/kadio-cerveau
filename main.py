from fastapi import FastAPI, HTTPException, Request, WebSocket, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import os
from datetime import datetime
from dotenv import load_dotenv

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
from datetime import datetime

import requests

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

class ConnectRequest(BaseModel):
    platform: str  # facebook, whatsapp, instagram, square, google
    auth_code: Optional[str] = None
    token: Optional[str] = None

# ========== ROUTES PRINCIPALES ==========

@app.get("/")
async def root():
    return {"status": "Kadio Cerveau v0.1", "online": True}

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
    return square.list_appointments(date)

@app.get("/appointments/availability")
async def check_availability(date: str, service: Optional[str] = None):
    """Vérifie les créneaux disponibles"""
    return square.check_availability(date, service)

@app.get("/appointments/stats")
async def daily_stats(date: Optional[str] = None):
    """Stats du jour"""
    return square.get_daily_stats(date)

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

@app.get("/webhook/whatsapp")
async def whatsapp_webhook_verify(request: Request):
    """
    Vérification Twilio du webhook (pour la configuration initiale).
    Twilio envoie une requête GET pour valider l'URL.
    """
    return {"status": "ok", "message": "Webhook WhatsApp actif"}

# ========== CONNEXION PLATEFORMES ==========

@app.post("/connect")
async def connect_platform(request: ConnectRequest):
    """Connecte une plateforme (OAuth simplifié)"""
    if request.platform == "whatsapp":
        # WhatsApp Business via Twilio
        return whatsapp.connect(request.token)
    
    elif request.platform == "square":
        # Square OAuth - on vérifie juste le token
        return {"connected": square.is_connected(), "location_id": square.location_id}
    
    elif request.platform == "instagram":
        # Meta Graph API
        return instagram.connect(request.token)
    
    elif request.platform == "facebook":
        # Meta Business
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

async def chat_with_agent(message: str, user_id: str) -> str:
    """Chat libre avec l'agent Kimi (pour Othi)"""
    system_prompt = """Tu es le cerveau d'entreprise de Kadio Coiffure. Tu aides Othi à gérer son salon.
    Tu peux:
    - Voir les stats
    - Gérer les rendez-vous
    - Publier sur les réseaux sociaux
    - Analyser les données
    - Donner des conseils
    
    Réponds de manière concise et actionnable."""
    
    messages = [{"role": "user", "content": message}]
    response = await agent.chat(messages, system_prompt)
    
    return response

async def send_response(platform: str, recipient_id: str, message: str):
    """Envoie une réponse sur la plateforme d'origine"""
    if platform == "whatsapp":
        await whatsapp.send_message(recipient_id, message)
    elif platform == "instagram":
        await instagram.send_message(recipient_id, message)
    elif platform == "telegram":
        # Via bot Telegram Othi
        pass

async def handle_appointment_command(command: str) -> Dict:
    """Commande rendez-vous : 'rdv demain 10h Marie locks'"""
    # Parse la commande
    parts = command.split()
    return {"status": "pending", "command": command, "parsed": parts}

async def handle_post_command(command: str) -> Dict:
    """Commande publication : 'post instagram avant-après'"""
    return {"status": "pending", "command": command}

async def handle_stats_command() -> Dict:
    """Statistiques salon"""
    today = datetime.now().strftime("%Y-%m-%d")
    stats = square.get_daily_stats(today)
    return stats

async def handle_clients_command() -> Dict:
    """Liste clients"""
    return {"clients": ["Marie", "Jean", "Sophie"], "total": 3}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
