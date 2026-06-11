import os
import requests
from typing import Dict, Optional

class TwilioVoiceConnector:
    """Connecteur pour la réceptionniste téléphonique via Twilio"""
    
    def __init__(self):
        self.twilio_sid = os.getenv("TWILIO_SID", "")
        self.twilio_auth = os.getenv("TWILIO_AUTH", "")
        self.twilio_number = os.getenv("TWILIO_PHONE_NUMBER", "")
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_sid}"
    
    def is_connected(self) -> bool:
        return bool(self.twilio_sid and self.twilio_auth)
    
    def handle_incoming_call(self, from_number: str, call_sid: str) -> str:
        """Génère le TwiML pour accueillir l'appel"""
        # TwiML pour dire un message de bienvenue + demander ce que le client veut
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="Polly.Lea" language="fr-FR">
                Bonjour, Kadio Coiffure. Je suis votre assistante virtuelle. 
                Pour prendre un rendez-vous, dites rendez-vous. 
                Pour des informations, dites info. 
                Pour parler à quelqu'un, dites Othi.
            </Say>
            <Gather action="/voice/response" method="POST" input="speech" language="fr-FR" timeout="5">
                <Say voice="Polly.Lea" language="fr-FR">Comment puis-je vous aider?</Say>
            </Gather>
        </Response>"""
        return twiml
    
    def process_speech(self, call_sid: str, speech_text: str) -> str:
        """Traite la réponse vocale et génère le TwiML suivant"""
        # Ici on appellerait l'agent Kimi pour interpréter
        # Simplifié pour l'instant
        response_text = f"J'ai compris: {speech_text}. Je transfère votre demande."
        
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="Polly.Lea" language="fr-FR">{response_text}</Say>
            <Dial>{os.getenv('OTHI_PHONE', '+15149195970')}</Dial>
        </Response>"""
        return twiml
    
    def make_call(self, to_number: str, message: str) -> Dict:
        """Appelle un numéro et lit un message"""
        url = f"{self.base_url}/Calls.json"
        
        # TwiML pour lire un message
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <Say voice="Polly.Lea" language="fr-FR">{message}</Say>
        </Response>"""
        
        payload = {
            "From": self.twilio_number,
            "To": to_number,
            "Twiml": twiml
        }
        
        resp = requests.post(url, data=payload, auth=(self.twilio_sid, self.twilio_auth), timeout=15)
        data = resp.json()
        
        return {
            "success": "sid" in data,
            "sid": data.get("sid"),
            "status": data.get("status")
        }
