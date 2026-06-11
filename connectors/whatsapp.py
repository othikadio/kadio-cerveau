import os
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from .stripe import StripeConnector
from .square import SquareConnector

class WhatsAppConnector:
    """
    Connecteur WhatsApp via Twilio API.
    Remplace wazzap.ai - conversation IA complète + prise de RDV autonome.
    """
    
    def __init__(self):
        self.account_sid = os.getenv("TWILIO_SID", "")
        self.auth_token = os.getenv("TWILIO_AUTH", "")
        self.phone_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "+13022328291")
        self.kimi_api_key = os.getenv("KIMI_API_KEY", "")
        self.kimi_base_url = "https://api.moonshot.cn/v1"
        
        # Connecteurs
        self.square = SquareConnector()
        self.stripe = StripeConnector()
        
        # Mémoire des conversations (à remplacer par Redis/DB en prod)
        self.conversations: Dict[str, List[Dict]] = {}
        self.client_info: Dict[str, Dict] = {}
    
    def is_connected(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.account_sid.startswith("AC"))
    
    # ========== ENVOI DE MESSAGES ==========
    
    def send_message(self, to: str, message: str) -> Dict:
        """Envoie un message WhatsApp via Twilio"""
        if not self.is_connected():
            return {"error": "Twilio non configuré"}
        
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        
        payload = {
            "From": f"whatsapp:{self.phone_number}",
            "To": f"whatsapp:{to}",
            "Body": message
        }
        
        try:
            resp = requests.post(url, data=payload, auth=(self.account_sid, self.auth_token))
            data = resp.json()
            
            if resp.status_code == 201:
                return {"success": True, "sid": data.get("sid")}
            else:
                return {"error": data.get("message", "Erreur Twilio")}
        except Exception as e:
            return {"error": str(e)}
    
    def send_template(self, to: str, template_name: str, params: Dict = None) -> Dict:
        """Envoie un template Twilio (si configuré)"""
        # Templates utiles: booking_confirmation, payment_reminder, etc.
        return self.send_message(to, params.get("body", ""))
    
    # ========== IA KIMI ==========
    
    async def ask_kimi(self, messages: List[Dict], temperature: float = 0.7) -> str:
        """Envoie les messages à Kimi AI et retourne la réponse"""
        if not self.kimi_api_key:
            return "Erreur: AI non configurée"
        
        headers = {
            "Authorization": f"Bearer {self.kimi_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "kimi-latest",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 2000
        }
        
        try:
            resp = requests.post(
                f"{self.kimi_base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            data = resp.json()
            
            if "choices" in data and len(data["choices"]) > 0:
                return data["choices"][0]["message"]["content"]
            else:
                return f"Erreur Kimi: {data.get('error', 'Réponse vide')}"
        except Exception as e:
            return f"Erreur: {str(e)}"
    
    # ========== TRAITEMENT DES MESSAGES ENTRANTS ==========
    
    async def process_incoming_message(self, from_number: str, message_body: str, profile_name: str = "Client") -> str:
        """
        Point d'entrée principal: reçoit un message WhatsApp, traite avec IA, répond.
        
        Flow:
        1. Stocke le message
        2. Analyse l'intention avec Kimi
        3. Si RDV -> extrait infos -> crée RDV Square -> envoie dépôt
        4. Si infos -> répond avec connaissances salon
        5. Si prix -> liste les services
        6. Si complaint -> escale à Othi
        """
        
        # Initialiser la conversation si nouveau client
        if from_number not in self.conversations:
            self.conversations[from_number] = []
            self.client_info[from_number] = {"name": profile_name, "phone": from_number}
        
        # Ajouter le message du client
        self.conversations[from_number].append({
            "role": "user",
            "content": message_body,
            "timestamp": datetime.now().isoformat()
        })
        
        # === ÉTAPE 1: ANALYSE D'INTENTION AVEC KIMI ===
        intent_analysis = await self._analyze_intent(from_number, message_body)
        intent = intent_analysis.get("intent", "general")
        
        # === ÉTAPE 2: TRAITER SELON L'INTENTION ===
        
        if intent == "booking_request":
            # Le client veut prendre un RDV
            return await self._handle_booking_request(from_number, message_body, intent_analysis)
        
        elif intent == "price_inquiry":
            # Le client demande des prix
            return await self._handle_price_inquiry(from_number, message_body, intent_analysis)
        
        elif intent == "service_info":
            # Le client veut des infos sur un service
            return await self._handle_service_info(from_number, message_body, intent_analysis)
        
        elif intent == "cancel_request":
            # Annulation de RDV
            return await self._handle_cancel_request(from_number, message_body, intent_analysis)
        
        elif intent == "reschedule":
            # Modification de RDV
            return await self._handle_reschedule(from_number, message_body, intent_analysis)
        
        elif intent == "complaint":
            # Plainte -> escale à Othi
            return await self._handle_complaint(from_number, message_body, intent_analysis)
        
        elif intent == "greeting":
            # Salutation -> présentation salon
            return await self._handle_greeting(from_number, message_body)
        
        elif intent == "availability":
            # Demande de disponibilité
            return await self._handle_availability(from_number, message_body, intent_analysis)
        
        else:
            # Conversation générale -> réponse IA naturelle
            return await self._handle_general_conversation(from_number, message_body)
    
    # ========== HANDLERS SPÉCIFIQUES ==========
    
    async def _handle_booking_request(self, phone: str, message: str, analysis: Dict) -> str:
        """Gère une demande de rendez-vous"""
        
        # Essayer d'extraire les infos du message
        extracted = await self._extract_booking_info(phone, message)
        
        missing = extracted.get("missing", [])
        
        # Si infos manquantes, demander
        if missing:
            return self._build_missing_info_response(extracted, missing)
        
        # Si on a toutes les infos -> créer le RDV
        if extracted.get("ready"):
            booking_data = {
                "client_name": extracted.get("name", self.client_info.get(phone, {}).get("name", "Client")),
                "phone": phone,
                "service_name": extracted.get("service"),
                "date": extracted.get("date"),
                "time": extracted.get("time"),
                "notes": extracted.get("notes", "Réservation via WhatsApp AI")
            }
            
            # Créer dans Square
            result = await self.square.create_appointment(booking_data)
            
            if result.get("success"):
                # Construire la réponse de confirmation
                response = (
                    f"✅ *Rendez-vous confirmé !*\n\n"
                    f"📅 {extracted.get('date')} à {extracted.get('time')}\n"
                    f"💇‍♀️ {result.get('service')}\n"
                    f"👤 Avec {result.get('coiffeur')}\n\n"
                )
                
                # Si dépôt requis, envoyer le lien de paiement
                if result.get("deposit") and result["deposit"].get("required"):
                    deposit = result["deposit"]
                    response += (
                        f"💳 *Dépôt requis: {deposit['amount']}$* ({deposit['percent']}%)\n\n"
                        f"Pour sécuriser votre RDV, payez ici:\n"
                    )
                    
                    # Envoyer le lien de paiement
                    try:
                        stripe_result = await self.stripe.send_deposit_link(
                            phone=phone,
                            service_name=result.get("service", ""),
                            amount=deposit["amount"],
                            booking_id=result.get("booking_id")
                        )
                        response += stripe_result.get("link", "")
                        
                        # Envoyer le message de paiement séparément
                        if stripe_result.get("message"):
                            self.send_message(phone, stripe_result["message"])
                    except:
                        response += self.stripe.link_base
                
                response += f"\n\n📍 Kadio Coiffure, 615 Antoinette-Robidoux, Longueuil\n"
                response += f"📞 Questions? Répondez ici ou appelez {self.phone_number}"
                
                return response
            else:
                # Erreur création RDV
                error = result.get("error", "Erreur inconnue")
                return (
                    f"⚠️ Je n'ai pas pu créer le rendez-vous : {error}\n\n"
                    f"Essayez avec un autre créneau ou appelez-nous au {self.phone_number}"
                )
        
        return "Je n'ai pas bien compris. Pouvez-vous répéter la date et l'heure souhaitées?"
    
    async def _handle_price_inquiry(self, phone: str, message: str, analysis: Dict) -> str:
        """Répond à une demande de prix"""
        service = analysis.get("service", "")
        
        if service:
            # Chercher le prix exact
            svc = await self.square.find_service(service)
            if svc:
                price = svc.get("price", 0)
                duration = svc.get("duration", 60)
                return (
                    f"💰 *{svc['name']}*\n\n"
                    f"Prix: {price}$\n"
                    f"Durée: ~{duration} min\n\n"
                    f"Voulez-vous prendre un rendez-vous? Dites-moi quand! 📅"
                )
        
        # Liste des services populaires
        return (
            "💰 *Tarifs Kadio Coiffure*\n\n"
            "🔒 *Locks & Dreadlocks*\n"
            "• Repousses gel: 75-95$\n"
            "• Repousses crochet: 90-175$\n"
            "• Extensions locks: 185-300$\n"
            "• Dreadlocks neuves: 140-185$\n\n"
            "🎀 *Tresses*\n"
            "• Tresses simples: 35-55$\n"
            "• Knotless braids: 120-155$\n"
            "• Fulani braids: 100-145$\n\n"
            "✂️ *Barbier*\n"
            "• Coupe homme: 30-45$\n"
            "• Coupe + barbe: 45-60$\n"
            "• Coupe enfant: 25-35$\n\n"
            "📅 Pour un RDV précis avec prix exact, dites-moi le service et la date!"
        )
    
    async def _handle_service_info(self, phone: str, message: str, analysis: Dict) -> str:
        """Donne des infos détaillées sur un service"""
        service = analysis.get("service", "").lower()
        
        if any(k in service for k in ["lock", "dread"]):
            return (
                "🔒 *Locks & Dreadlocks - Spécialité Kadio*\n\n"
                "Nos locticiens (Othi, Mariel, Raquel) sont des experts.\n\n"
                "• *Repousses gel*: retwist avec gel de qualité, durée 1-2h\n"
                "• *Repousses crochet*: crochet interlock pour locks matures, 1.5-3h\n"
                "• *Repousses petit crochet*: crochet précis, 3h+, 175$\n"
                "• *Extensions*: ajout de mèches, 2-4h\n"
                "• *Dreadlocks neuves*: création complète, 2-3h\n\n"
                "💡 Conseil: Pour les repousses, prévoir un RDV toutes les 4-6 semaines.\n\n"
                "Quel type de locks avez-vous? Je peux vous recommander le meilleur soin."
            )
        
        elif any(k in service for k in ["tresse", "braid", "natte"]):
            return (
                "🎀 *Tresses - Nos Coiffeuses*\n\n"
                "Princesse, Aïcha et Ange sont nos spécialistes tresses.\n\n"
                "• *Tresses simples*: rapides, 1-1.5h\n"
                "• *Knotless braids*: sans nœuds, plus confortables, 2-4h\n"
                "• *Fulani braids*: style ethnique, 2-3h\n"
                "• *Twists*: twists deux brins, 1.5-3h\n\n"
                "💡 Durée de vie: 2-4 semaines selon l'entretien.\n\n"
                "Quel style de tresses vous intéresse?"
            )
        
        elif any(k in service for k in ["barbe", "coupe", "barbier"]):
            return (
                "✂️ *Barbier - Wilfried & Mariel*\n\n"
                "• *Coupe homme*: fade, dégradé, coupe classique\n"
                "• *Coupe + barbe*: combo complet\n"
                "• *Coupe enfant*: 25-35$\n"
                "• *Shampoing + brushing*: aussi disponible\n\n"
                "⏱️ Durée: 30-45 min\n"
                "💡 Pas de dépôt requis pour le barbier!\n\n"
                "Voulez-vous prendre un RDV?"
            )
        
        return "Quel service vous intéresse? Je peux vous donner tous les détails!"
    
    async def _handle_greeting(self, phone: str, message: str) -> str:
        """Répond à une salutation"""
        name = self.client_info.get(phone, {}).get("name", "")
        greeting = f"Bonjour {name}!" if name else "Bonjour!"
        
        return (
            f"{greeting} 🖤\n\n"
            f"Je suis l'assistante virtuelle de *Kadio Coiffure*.\n\n"
            f"Je peux vous aider à:\n"
            f"📅 Prendre un rendez-vous\n"
            f"💰 Connaître les prix\n"
            f"💇‍♀️ Découvrir nos services\n"
            f"📍 Voir nos horaires\n\n"
            f"Que souhaitez-vous faire?"
        )
    
    async def _handle_availability(self, phone: str, message: str, analysis: Dict) -> str:
        """Vérifie les disponibilités"""
        date = analysis.get("date", "")
        service = analysis.get("service", "")
        
        if not date:
            return "Pour quelle date souhaitez-vous vérifier les disponibilités? (ex: vendredi, demain, 15 juin)"
        
        # Vérifier les créneaux
        availability = await self.square.check_availability(date, service)
        slots = availability.get("available_slots", [])
        
        if slots:
            slots_text = "\n".join([f"• {s['time']}" for s in slots[:5]])
            return (
                f"📅 *Créneaux disponibles le {date}*\n\n"
                f"{slots_text}\n\n"
                f"Quel horaire vous convient?"
            )
        else:
            return (
                f"⚠️ Aucun créneau disponible le {date}.\n\n"
                f"Essayez une autre date ou appelez-nous au {self.phone_number}"
            )
    
    async def _handle_cancel_request(self, phone: str, message: str, analysis: Dict) -> str:
        """Gère une demande d'annulation"""
        # Trouver les RDV du client
        # Note: il faudrait stocker le mapping client -> booking_id
        return (
            "⚠️ Pour annuler votre rendez-vous, merci de nous appeler au "
            f"{self.phone_number} ou de préciser votre nom et la date du RDV.\n\n"
            "Les annulations doivent être faites au moins 24h à l'avance."
        )
    
    async def _handle_reschedule(self, phone: str, message: str, analysis: Dict) -> str:
        """Gère une demande de modification"""
        return (
            "🔄 Pour modifier votre rendez-vous, appelez-nous au "
            f"{self.phone_number} ou donnez-moi votre nom et la nouvelle date souhaitée.\n\n"
            "Je peux vérifier les disponibilités et vous proposer des alternatives."
        )
    
    async def _handle_complaint(self, phone: str, message: str, analysis: Dict) -> str:
        """Gère une plainte -> escale à Othi"""
        # Notifier Othi
        try:
            self.send_message(
                self.client_info.get("othi_phone", "+15149195970"),
                f"⚠️ Plainte reçue de {phone}:\n{message}\n\nRépondre ASAP."
            )
        except:
            pass
        
        return (
            "Je comprends votre préoccupation et je suis désolée pour cette expérience.\n\n"
            "Je vais immédiatement informer Othi, le propriétaire.\n"
            "Il vous contactera personnellement dans les plus brefs délais.\n\n"
            "Merci de nous donner l'opportunité de rectifier la situation. 🖤"
        )
    
    async def _handle_general_conversation(self, phone: str, message: str) -> str:
        """Réponse IA naturelle pour les conversations générales"""
        
        # Contexte du salon pour Kimi
        context = self._build_salon_context()
        
        messages = [
            {"role": "system", "content": context},
            *self.conversations[phone][-5:],  # Derniers 5 messages
        ]
        
        response = await self.ask_kimi(messages, temperature=0.8)
        
        return response
    
    # ========== EXTRACtion D'INFOS ==========
    
    async def _extract_booking_info(self, phone: str, message: str) -> Dict:
        """Extrait les informations de RDV du message avec Kimi"""
        
        extraction_prompt = f"""
Tu es un assistant de salon de coiffure. Extrais les informations de rendez-vous du message client.

Message: "{message}"

Extrais et retourne UNIQUEMENT un JSON avec cette structure:
{{
    "name": "nom du client si mentionné",
    "service": "service demandé (locks, tresses, barbier, etc.)",
    "date": "date au format YYYY-MM-DD (déduite de 'vendredi', 'demain', etc.)",
    "time": "heure au format HH:MM",
    "notes": "infos supplémentaires",
    "ready": true/false,
    "missing": ["liste des infos manquantes: name, service, date, time"]
}}

Aujourd'hui est le {datetime.now().strftime('%Y-%m-%d')}.
Si le client dit "vendredi", c'est le prochain vendredi.
Si le client dit "demain", c'est demain.
Si le client dit "cette semaine", suggère des créneaux.

Retourne UNIQUEMENT le JSON, pas d'explication.
"""
        
        messages = [{"role": "user", "content": extraction_prompt}]
        
        try:
            response = await self.ask_kimi(messages, temperature=0.1)
            # Extraire le JSON de la réponse
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                extracted = json.loads(json_match.group())
                
                # Sauvegarder le nom du client
                if extracted.get("name"):
                    self.client_info[phone]["name"] = extracted["name"]
                
                return extracted
        except:
            pass
        
        return {"ready": False, "missing": ["service", "date", "time"]}
    
    async def _analyze_intent(self, phone: str, message: str) -> Dict:
        """Analyse l'intention du message avec Kimi"""
        
        intent_prompt = f"""
Analyse l'intention du message client pour un salon de coiffure.

Message: "{message}"

Retourne UNIQUEMENT un JSON:
{{
    "intent": "booking_request|price_inquiry|service_info|cancel_request|reschedule|complaint|greeting|availability|general",
    "service": "service mentionné si identifiable",
    "date": "date mentionnée si identifiable",
    "time": "heure mentionnée si identifiable",
    "confidence": "high|medium|low"
}}

Intents:
- booking_request: veut prendre RDV
- price_inquiry: demande les prix
- service_info: veut savoir ce qu'on fait
- cancel_request: veut annuler
- reschedule: veut changer date/heure
- complaint: mécontent
- greeting: salutation simple
- availability: demande les créneaux
- general: conversation normale

Retourne UNIQUEMENT le JSON.
"""
        
        messages = [{"role": "user", "content": intent_prompt}]
        
        try:
            response = await self.ask_kimi(messages, temperature=0.1)
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        return {"intent": "general", "confidence": "low"}
    
    # ========== UTILITAIRES ==========
    
    def _build_salon_context(self) -> str:
        """Construit le contexte expert du salon pour Kimi - connaissances techniques afro"""
        return f"""
Tu es KADIO, l'assistante virtuelle experte de Kadio Coiffure, un salon de coiffure afro-caribéen à Longueuil, QC.

🎯 **TON RÔLE**: Tu es une EXPERTE en coiffure afro. Tu ne fais pas semblant. Tu connais les techniques, les produits, les textures de cheveux crépus, frisés, bouclés. Tu parles avec assurance et chaleur.

📍 **SALON**: 615 Antoinette-Robidoux, Longueuil, QC
📞 **TÉL**: {self.phone_number}

⏰ **HORAIRES**:
- Lun: 12h-19h (BARBIER UNIQUEMENT)
- Mar: FERMÉ
- Mer: 10h-19h
- Jeu: 10h-21h
- Ven: 10h-21h
- Sam: 10h-21h
- Dim: 10h-17h

---

👥 **ÉQUIPE & SPÉCIALITÉS**:

**🔒 LOCKS & DREADLOCKS** (Experts: Othi, Mariel, Raquel)
- **Othi** (propriétaire): Locticien EXPERT + Barbier. 10+ ans expérience. Maîtrise toutes techniques.
- **Mariel**: Locticien + Barbier + Brushings. Polyvalent, technique précise.
- **Raquel**: Locticienne (actuellement inactive, revient bientôt).

Techniques locks:
• **Retwist au gel**: Pour locks matures. Gel naturel sans résidu. 1-2h. 75-95$
• **Interlock/crochet**: Pour locks très matures ou racines épaisses. Crochet métal ou bois. 1.5-3h. 90-175$
• **Petit crochet**: Technique précise pour sections fines. 3h+. 175$
• **Extensions locks**: Ajout mèches synthétiques ou naturelles. 2-4h. 185-300$
• **Dreadlocks neuves**: Création complète. Twist & rip, backcomb, ou crochet. 2-3h. 140-185$
• **Réparations locks**: Locks cassées, affaiblies. Couture, re-attache. Sur devis.
• **Coloration locks**: Teinture sans ammoniaque. 50-80$

Conseils locks:
- Retwist toutes les 4-6 semaines
- Pas d'huile lourde sur racines (ça glisse)
- Dormir avec bonnet satin ou taie d'oreiller
- Shampoing clarifiant 1x/mois

---

🎀 **TRESSES AFRICAINES** (Experts: Princesse, Aïcha, Ange)
- **Princesse**: Coiffeuse spécialiste tresses. Rapide, créative.
- **Aïcha**: Coiffeuse + Semi-locticienne. Repousses locks OK, PAS extensions.
- **Ange**: Coiffeuse + Semi-locticienne. Même profil qu'Aïcha.

Styles tresses:
• **Knotless braids**: Sans nœuds aux racines. Plus confort, moins tension. 2-4h. 120-155$
• **Fulani braids**: Style peul, tresse centrale + boucles côtés. 2-3h. 100-145$
• **Box braids**: Classiques, carrées. Toutes tailles. 2-4h. 90-140$
• **Cornrows**: Tresses collées au cuir chevelu. 1-2h. 35-75$
• **Twists**: Deux brins torsadés. Havana twists, passion twists. 1.5-3h. 90-120$
• **Lemonade braids**: Tresses côté, style Beyoncé. 2-3h. 100-130$
• **Bantu knots**: Petits chignons. + tresses si demandé. 1-2h. 55-85$
• **Tresses enfants**: Plus petites sections, patience. 1-2h. 35-55$

Durée vie: 2-4 semaines. Entretien: mousse hydratante, bonnet satin.

---

✂️ **BARBIER** (Experts: Wilfried, Mariel, Othi)
- **Wilfried**: Barbier spécialisé. Fade, dégradé, design. Shampoing inclus.
- **Mariel**: Barbier + Brushings. Technique femme aussi.
- **Othi**: Toutes coupes homme/femme/enfant.

Services:
• **Coupe homme**: Fade, dégradé, afro cut, buzz. 30-45 min. 30-45$
• **Coupe + barbe**: Combo complet. Rasoir chaud. 45-60 min. 45-60$
• **Coupe enfant**: Garçon/fille. 25-35 min. 25-35$
• **Barbe seule**: Taille, rasage, soin. 20-30 min. 20-30$
• **Shampoing + brushing**: Toutes textures. 30-60 min. 40-65$
• **Coupe femme**: Dégradé, coupe courte, shape up. 30-45 min. 35-50$

⚠️ Pas de dépôt pour barbier. Paiement sur place.

---

💅 **MANUCURE** (Marianne Bérubé)
• Vernis classique, gel, semi-permanent
• Soin des mains, cuticules
• Sur réservation uniquement

---

💰 **DÉPÔT DE SÉCURITÉ**:
- Locks: 20% (ex: 175$ → dépôt 35$)
- Tresses: 20% (ex: 120$ → dépôt 24$)
- Barbier: PAS de dépôt
- Manucure: PAS de dépôt

Le dépôt est remisé sur le service final. Non-remboursable si annulation <24h.

---

🗣️ **TON STYLE DE RÉPONSE**:
- Chaleureuse, pro, avec un ton afro-caribéen authentique
- Emojis naturels, pas trop
- Tu connais les réponses aux questions techniques (quel crochet, quelle technique, combien de temps...)
- Tu recommandes le bon expert selon le service
- Si plainte: excuse immédiate, escalade à Othi
- Tu parles français, tu peux mélanger un peu d'anglais si le client le fait
- Tu n'inventes JAMAIS de prix. Tu donnes les prix réels du salon.
- Si tu ne sais pas, tu dis "Je vais vérifier avec Othi"
"""

    
    def _build_missing_info_response(self, extracted: Dict, missing: List[str]) -> str:
        """Construit une réponse demandant les infos manquantes"""
        
        name = extracted.get("name", "")
        greeting = f"{name}, " if name else ""
        
        if "service" in missing:
            return (
                f"{greeting}Quel service souhaitez-vous?\n\n"
                f"🔒 *Locks* (retwist, crochet, extensions)\n"
                f"🎀 *Tresses* (knotless, fulani, twists)\n"
                f"✂️ *Barbier* (coupe, barbe, enfant)\n\n"
                f"Dites-moi ce qui vous intéresse!"
            )
        
        if "date" in missing:
            service = extracted.get("service", "ce service")
            return (
                f"{greeting}Parfait pour *{service}*! 📅\n\n"
                f"Quelle date souhaitez-vous?\n"
                f"(ex: vendredi, demain, 15 juin)\n\n"
                f"Nos horaires:\n"
                f"• Lun: 12h-19h (barbier)\n"
                f"• Mer-Dim: 10h-21h/17h\n"
                f"• Mar: fermé"
            )
        
        if "time" in missing:
            date = extracted.get("date", "cette date")
            return (
                f"{greeting}Bien, le *{date}*! ⏰\n\n"
                f"Quelle heure vous convient?\n"
                f"(ex: 10h, 14h30, 16h)\n\n"
                f"Je vais vérifier les disponibilités."
            )
        
        return "Je vais vérifier ça pour vous. Un instant..."
    
    # ========== WEBHOOK HANDLER ==========
    
    async def handle_webhook(self, request_data: Dict) -> str:
        """
        Point d'entrée pour le webhook Twilio WhatsApp.
        Reçoit: {'From': 'whatsapp:+1234', 'Body': 'message', 'ProfileName': 'Nom'}
        """
        from_number = request_data.get("From", "").replace("whatsapp:", "")
        message_body = request_data.get("Body", "")
        profile_name = request_data.get("ProfileName", "Client")
        
        if not from_number or not message_body:
            return "Erreur: données manquantes"
        
        # Traiter le message
        response = await self.process_incoming_message(from_number, message_body, profile_name)
        
        # Envoyer la réponse
        send_result = self.send_message(from_number, response)
        
        if not send_result.get("success"):
            print(f"Erreur envoi WhatsApp: {send_result.get('error')}")
        
        return response
