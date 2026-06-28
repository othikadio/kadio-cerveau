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
        """Point d'entrée principal: reçoit un message WhatsApp, traite avec IA, répond."""
        try:
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
                return await self._handle_booking_request(from_number, message_body, intent_analysis)
            elif intent == "price_inquiry":
                return await self._handle_price_inquiry(from_number, message_body, intent_analysis)
            elif intent == "subscription_inquiry":
                return await self._handle_subscription_inquiry(from_number, message_body, intent_analysis)
            elif intent == "service_info":
                return await self._handle_service_info(from_number, message_body, intent_analysis)
            elif intent == "cancel_request":
                return await self._handle_cancel_request(from_number, message_body, intent_analysis)
            elif intent == "reschedule":
                return await self._handle_reschedule(from_number, message_body, intent_analysis)
            elif intent == "complaint":
                return await self._handle_complaint(from_number, message_body, intent_analysis)
            elif intent == "greeting":
                return await self._handle_greeting(from_number, message_body)
            elif intent == "availability":
                return await self._handle_availability(from_number, message_body, intent_analysis)
            else:
                return await self._handle_general_conversation(from_number, message_body)
        
        except Exception as e:
            print(f"ERREUR process_incoming_message: {e}")
            return (
                "Bonjour! 🖤\n\n"
                "Je suis l'assistante de Kadio Coiffure.\n\n"
                "Je peux vous aider à:\n"
                "📅 Prendre un rendez-vous\n"
                "💰 Connaître les prix\n"
                "💇‍♀️ Découvrir nos services\n\n"
                "Que souhaitez-vous faire?"
            )
    
    # ========== HANDLERS SPÉCIFIQUES ==========
    
    async def _handle_booking_request(self, phone: str, message: str, analysis: Dict) -> str:
        """Gère une demande de rendez-vous"""
        try:
            extracted = await self._extract_booking_info(phone, message)
            missing = extracted.get("missing", [])
            
            if missing:
                return self._build_missing_info_response(extracted, missing)
            
            if extracted.get("ready"):
                booking_data = {
                    "client_name": extracted.get("name", self.client_info.get(phone, {}).get("name", "Client")),
                    "phone": phone,
                    "service_name": extracted.get("service"),
                    "date": extracted.get("date"),
                    "time": extracted.get("time"),
                    "notes": extracted.get("notes", "Réservation via WhatsApp AI")
                }
                
                result = await self.square.create_appointment(booking_data)
                
                if result.get("success"):
                    response = (
                        f"✅ *Rendez-vous confirmé !*\n\n"
                        f"📅 {extracted.get('date')} à {extracted.get('time')}\n"
                        f"💇‍♀️ {result.get('service')}\n"
                        f"👤 Avec {result.get('coiffeur')}\n\n"
                    )
                    
                    if result.get("deposit") and result["deposit"].get("required"):
                        deposit = result["deposit"]
                        response += f"💳 *Dépôt requis: {deposit['amount']}$* ({deposit['percent']}%)\n\n"
                        try:
                            stripe_result = await self.stripe.send_deposit_link(
                                phone=phone,
                                service_name=result.get("service", ""),
                                amount=deposit["amount"],
                                booking_id=result.get("booking_id")
                            )
                            response += stripe_result.get("link", "")
                            if stripe_result.get("message"):
                                self.send_message(phone, stripe_result["message"])
                        except:
                            pass
                    
                    response += f"\n\n📍 Kadio Coiffure, 615 Antoinette-Robidoux, Longueuil\n"
                    response += f"📞 Questions? Répondez ici ou appelez {self.phone_number}"
                    return response
                else:
                    error = result.get("error", "Erreur inconnue")
                    return (
                        f"⚠️ Je n'ai pas pu créer le rendez-vous : {error}\n\n"
                        f"Essayez avec un autre créneau ou appelez-nous au {self.phone_number}"
                    )
            
            return "Je n'ai pas bien compris. Pouvez-vous répéter la date et l'heure souhaitées?"
        except Exception as e:
            print(f"ERREUR _handle_booking_request: {e}")
            return "Je vais vérifier les disponibilités. Un instant..."
    
    async def _handle_subscription_inquiry(self, phone: str, message: str, analysis: Dict) -> str:
        """Répond à une demande d'abonnement"""
        try:
            service = analysis.get("service", "").lower()
            if any(k in service for k in ["pixie", "fer", "boucle", "abonnement", "mensuel"]):
                try:
                    stripe_result = await self.stripe.send_subscription_link(
                        phone=phone,
                        customer_email=self.client_info.get(phone, {}).get("email", "")
                    )
                    if stripe_result.get("success"):
                        return (
                            f"🌀 *Abonnement Pixie cut au fer*\n\n"
                            f"Prix: 50$/mois + taxes\n"
                            f"Inclus chaque mois:\n"
                            f"  • Coupe courte stylée\n"
                            f"  • Boucles au fer à lisser\n"
                            f"  • 2 lissages offerts par semaine\n\n"
                            f"⚠️ Sans lavage ni shampoing\n\n"
                            f"Pour activer votre abonnement:\n"
                            f"{stripe_result.get('link')}\n\n"
                            f"Merci de choisir Kadio Coiffure! 🖤"
                        )
                except:
                    pass
            
            return (
                f"🌀 *Abonnements disponibles*\n\n"
                f"• *Pixie cut au fer*: 50$/mois + taxes\n"
                f"  Coupe + boucles au fer + 2 lissages/semaine\n\n"
                f"Pour plus d'infos, demandez-moi le détail! 🖤"
            )
        except Exception as e:
            print(f"ERREUR _handle_subscription_inquiry: {e}")
            return "Je vais vérifier les abonnements disponibles."
    
    async def _handle_price_inquiry(self, phone: str, message: str, analysis: Dict) -> str:
        """Répond à une demande de prix"""
        try:
            service = analysis.get("service", "")
            if service:
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
        except Exception as e:
            print(f"ERREUR _handle_price_inquiry: {e}")
            return "Je vais vérifier les tarifs. Un instant..."
    
    async def _handle_service_info(self, phone: str, message: str, analysis: Dict) -> str:
        """Donne des infos détaillées sur un service"""
        try:
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
                    "Quel type de locks avez-vous?"
                )
            elif any(k in service for k in ["tresse", "braid", "natte"]):
                return (
                    "🎀 *Tresses - Nos Coiffeuses*\n\n"
                    "Princesse, Aïcha et Ange sont nos spécialistes.\n\n"
                    "• *Tresses simples*: rapides, 1-1.5h\n"
                    "• *Knotless braids*: sans nœuds, plus confortables, 2-4h\n"
                    "• *Fulani braids*: style ethnique, 2-3h\n"
                    "• *Twists*: twists deux brins, 1.5-3h\n\n"
                    "💡 Durée de vie: 2-4 semaines.\n\n"
                    "Quel style vous intéresse?"
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
        except Exception as e:
            print(f"ERREUR _handle_service_info: {e}")
            return "Je vais vérifier nos services. Un instant..."
    
    async def _handle_greeting(self, phone: str, message: str) -> str:
        """Répond à une salutation"""
        try:
            name = self.client_info.get(phone, {}).get("name", "")
            greeting = f"Bonjour {name}!" if name else "Bonjour!"
            return (
                f"{greeting} 🖤\n\n"
                f"Je suis l'assistante virtuelle de *Kadio Coiffure*.\n\n"
                f"Je peux vous aider à:\n"
                f"📅 Prendre un rendez-vous\n"
                f"💰 Connaître les prix\n"
                f"💇‍♀️ Découvrir nos services\n\n"
                f"Que souhaitez-vous faire?"
            )
        except Exception as e:
            print(f"ERREUR _handle_greeting: {e}")
            return "Bonjour! Je suis l'assistante de Kadio Coiffure. Comment puis-je vous aider?"
    
    async def _handle_availability(self, phone: str, message: str, analysis: Dict) -> str:
        """Vérifie les disponibilités"""
        try:
            date = analysis.get("date", "")
            service = analysis.get("service", "")
            if not date:
                return "Pour quelle date souhaitez-vous vérifier les disponibilités? (ex: vendredi, demain, 15 juin)"
            
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
        except Exception as e:
            print(f"ERREUR _handle_availability: {e}")
            return "Je vais vérifier les disponibilités. Un instant..."
    
    async def _handle_cancel_request(self, phone: str, message: str, analysis: Dict) -> str:
        """Gère une demande d'annulation"""
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
        try:
            context = self._build_salon_context()
            messages = [
                {"role": "system", "content": context},
                *self.conversations[phone][-5:],
            ]
            response = await self.ask_kimi(messages, temperature=0.8)
            return response
        except Exception as e:
            print(f"ERREUR _handle_general_conversation: {e}")
            return (
                "Bonjour! 🖤\n\n"
                "Je suis l'assistante de Kadio Coiffure.\n\n"
                "Je peux vous aider à:\n"
                "📅 Prendre un rendez-vous\n"
                "💰 Connaître les prix\n"
                "💇‍♀️ Découvrir nos services\n\n"
                "Que souhaitez-vous faire?"
            )
    
    # ========== EXTRACTION D'INFOS ==========
    
    async def _extract_booking_info(self, phone: str, message: str) -> Dict:
        """Extrait les informations de RDV du message avec Kimi"""
        try:
            extraction_prompt = f"""Tu es un assistant de salon de coiffure. Extrais les informations de rendez-vous du message client.
Message: "{message}"
Extrais et retourne UNIQUEMENT un JSON avec cette structure:
{{"name": "nom du client", "service": "service", "date": "YYYY-MM-DD", "time": "HH:MM", "notes": "", "ready": true/false, "missing": ["name","service","date","time"]}}
Aujourd'hui est le {datetime.now().strftime('%Y-%m-%d')}.
Retourne UNIQUEMENT le JSON."""
            
            messages = [{"role": "user", "content": extraction_prompt}]
            response = await self.ask_kimi(messages, temperature=0.1)
            
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                extracted = json.loads(json_match.group())
                if extracted.get("name"):
                    self.client_info[phone]["name"] = extracted["name"]
                return extracted
        except Exception as e:
            print(f"ERREUR _extract_booking_info: {e}")
        
        return {"ready": False, "missing": ["service", "date", "time"]}
    
    async def _analyze_intent(self, phone: str, message: str) -> Dict:
        """Analyse l'intention du message avec Kimi"""
        try:
            intent_prompt = f"""Analyse l'intention du message pour un salon de coiffure.
Message: "{message}"
Retourne UNIQUEMENT un JSON:
{{"intent": "booking_request|price_inquiry|service_info|cancel_request|reschedule|complaint|greeting|availability|general", "service": "", "date": "", "time": "", "confidence": "high|medium|low"}}
Retourne UNIQUEMENT le JSON."""
            
            messages = [{"role": "user", "content": intent_prompt}]
            response = await self.ask_kimi(messages, temperature=0.1)
            
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            print(f"ERREUR _analyze_intent: {e}")
        
        return {"intent": "general", "confidence": "low"}
    
    # ========== UTILITAIRES ==========
    
    def _build_salon_context(self) -> str:
        """Construit le contexte expert du salon pour Kimi"""
        return f"""Tu es KADIO, l'assistante virtuelle experte de Kadio Coiffure à Longueuil, QC.
📍 615 Antoinette-Robidoux | 📞 {self.phone_number}
⏰ Lun: 12h-19h (barbier), Mar: fermé, Mer-Dim: 10h-21h/17h
Équipe: Othi (locticien+barbier), Mariel (locticien+barbier), Raquel (locticienne), Princesse/Aïcha/Ange (tresses), Wilfried (barbier).
Services: Locks (75-300$), Tresses (35-155$), Barbier (25-60$), Manucure, Abonnement Pixie cut (50$/mois).
Réponds en français, chaleureuse, pro, connais les techniques afro."""
    
    def _build_missing_info_response(self, extracted: Dict, missing: List[str]) -> str:
        """Construit une réponse demandant les infos manquantes"""
        try:
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
        except Exception as e:
            print(f"ERREUR _build_missing_info_response: {e}")
            return "Je vais vérifier les informations. Un instant..."
    
    # ========== WEBHOOK HANDLER ==========
    
    async def handle_webhook(self, request_data: Dict) -> str:
        """Point d'entrée pour le webhook Twilio WhatsApp."""
        try:
            from_number = request_data.get("From", "").replace("whatsapp:", "")
            message_body = request_data.get("Body", "")
            profile_name = request_data.get("ProfileName", "Client")
            
            if not from_number or not message_body:
                return "Erreur: données manquantes"
            
            response = await self.process_incoming_message(from_number, message_body, profile_name)
            send_result = self.send_message(from_number, response)
            
            if not send_result.get("success"):
                print(f"Erreur envoi WhatsApp: {send_result.get('error')}")
            
            return response
        except Exception as e:
            print(f"ERREUR handle_webhook: {e}")
            return "Erreur lors du traitement du message"
