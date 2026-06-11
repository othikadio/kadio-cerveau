import os
import requests
from typing import Dict, List, Optional

class KimiAgent:
    """Agent IA utilisant l'API Kimi (Moonshot AI)"""
    
    def __init__(self):
        self.api_key = os.getenv("KIMI_API_KEY")
        self.base_url = "https://api.moonshot.cn/v1"
        self.model = "kimi-k2p6"  # ou kimi-latest
        
    def is_configured(self) -> bool:
        return bool(self.api_key)
    
    async def chat(self, messages: List[Dict], system_prompt: Optional[str] = None) -> str:
        """Chat avec l'agent Kimi"""
        if not self.is_configured():
            return "KIMI_API_KEY non configuré. Agent en mode démo."
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        if system_prompt:
            payload["messages"].insert(0, {
                "role": "system",
                "content": system_prompt
            })
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            
            data = response.json()
            
            if "choices" in data and len(data["choices"]) > 0:
                return data["choices"][0]["message"]["content"]
            else:
                return f"Erreur Kimi: {data.get('error', 'Réponse vide')}"
                
        except Exception as e:
            return f"Erreur connexion Kimi: {str(e)}"
    
    async def process_client_message(self, message: str, client_history: List[Dict] = None) -> str:
        """Traite un message client avec le contexte du salon"""
        
        system_prompt = """Tu es l'assistante virtuelle de Kadio Coiffure, un salon de coiffure afro à Longueuil, Québec.
        
        Adresse: 615 Antoinette-Robidoux
        Téléphone: 514-919-5970
        Horaires: Lun 12h-19h, Mer 10h-19h, Jeu-Ven 10h-21h, Sam 10h-21h, Dim 10h-17h, Mar fermé (sauf barbier 12h-19h)
        
        SERVICES ET PRIX:
        - Locks mi-tête: 70$
        - Locks toute tête: 100$
        - Tresses (box braids, etc.): 80-150$
        - Twists: 80-120$
        - Coupe homme: 25$
        - Barbier: 30-40$
        - Pose perruque: 40-60$
        - Mise en plis: 25-40$
        
        ABONNEMENTS:
        - Locks Illimité: 129,99$/mois
        - Tresses Rapides: 79,99$/mois
        - Barbier: 64,99$/mois
        - Combo Tresses + Barbier: 104,99$/mois
        
        RÈGLES:
        1. Sois chaleureuse et professionnelle
        2. Pour les RDV: demande nom, téléphone, service, date, heure
        3. Si le client demande un prix, donne-le directement
        4. Si hors horaire, indique les heures d'ouverture
        5. Pour les annulations, demande confirmation et propose de reprogrammer
        6. Tu peux parler français et anglais
        7. Ton style est professionnel mais cool, comme un salon afro de qualité
        """
        
        messages = client_history or []
        messages.append({"role": "user", "content": message})
        
        return await self.chat(messages, system_prompt)
    
    async def process_voice_command(self, speech_text: str, call_context: Dict = None) -> Dict:
        """Traite une commande vocale pour la réceptionniste"""
        
        system_prompt = """Tu es la réceptionniste téléphonique de Kadio Coiffure.
        Analyse la demande vocale et réponds avec un JSON structuré:
        {
            "intent": "rdv|info|annulation|transfert|autre",
            "service": "locks|tresses|coupe|barbier|autre",
            "date": "YYYY-MM-DD",
            "time": "HH:MM",
            "client_name": "",
            "phone": "",
            "response_speech": "Réponse à dire au client (français québécois naturel)",
            "action": "create_rdv|cancel_rdv|transfer|give_info|ask_clarification"
        }
        
        Si le client veut parler à Othi, transfère (transfer).
        Si c'est pour un RDV, demande les infos manquantes.
        Si c'est pour info, donne les infos clairement.
        """
        
        messages = [{"role": "user", "content": f"Transcription appel: {speech_text}"}]
        
        response = await self.chat(messages, system_prompt)
        
        # Essaie de parser le JSON
        try:
            import json
            return json.loads(response)
        except:
            return {
                "intent": "autre",
                "response_speech": response,
                "action": "ask_clarification"
            }
    
    async def generate_proactive_alert(self, salon_data: Dict) -> Optional[str]:
        """Génère une alerte proactive pour Othi"""
        
        system_prompt = """Tu es le cerveau d'entreprise de Kadio Coiffure.
        Analyse les données du salon et génère une alerte si nécessaire.
        Règles:
        - Si RDV annulé dernière minute → "Alerte: [Nom] a annulé. Veux-tu proposer la place à quelqu'un ?"
        - Si stock bas → "Stock: [Produit] presque vide. Réapprovisionner ?"
        - Si jour calme → "Aujourd'hui seulement X RDV. Tu veux que je fasse une promo d'urgence ?"
        - Si nouveau client récurrent → "[Nom] revient pour la 3e fois. Fidéliser avec un abonnement ?"
        - Si pas d'alerte → retourne null
        
        Réponds avec un seul message court et actionnable."""
        
        messages = [{"role": "user", "content": f"Données salon: {salon_data}"}]
        
        response = await self.chat(messages, system_prompt)
        
        if "null" in response.lower() or "pas d'alerte" in response.lower():
            return None
        
        return response
    
    async def generate_social_post(self, topic: str, style: str = "cool") -> str:
        """Génère un post pour les réseaux sociaux"""
        
        system_prompt = """Tu es le community manager de Kadio Coiffure.
        Génère un post Instagram/Facebook/TikTok.
        Style: cool, authentique, afro, sans hashtag excessif.
        Longueur: courte (1-2 phrases max).
        Langue: français québécois.
        
        Exemples:
        - "Nouvelle semaine, nouveau look. Qui vient se faire brancher cette semaine ?"
        - "Résultat du jour. Locks frais, client satisfait. C'est ça Kadio."
        """
        
        messages = [{"role": "user", "content": f"Sujet: {topic}. Style: {style}"}]
        
        return await self.chat(messages, system_prompt)