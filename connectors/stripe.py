import os
import stripe
from typing import Dict, Optional

class StripeConnector:
    """Connecteur Stripe pour paiements Kadio Coiffure"""
    
    def __init__(self):
        self.api_key = os.getenv("STRIPE_SECRET_KEY", "")
        self.link_base = os.getenv("STRIPE_LINK_BASE", "https://buy.stripe.com/fZu8wO78Vaq6eAe6F96wE0r")
        stripe.api_key = self.api_key
    
    def is_connected(self) -> bool:
        key = self.api_key.strip()
        return bool(key and key.startswith("sk_") and "..." not in key and len(key) > 20)
    
    async def create_payment_link(self, amount: float, description: str, customer_email: str = None) -> Dict:
        """
        Crée un lien de paiement Stripe pour un dépôt.
        amount: montant en dollars CAD
        description: description du service
        """
        if not self.is_connected():
            return {"error": "Stripe non configuré", "link": self.link_base}
        
        try:
            # Convertir en cents
            amount_cents = int(amount * 100)
            
            # Créer une session de paiement
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "cad",
                        "product_data": {
                            "name": f"Dépôt - {description}",
                            "description": f"Dépôt de sécurité Kadio Coiffure - {description}"
                        },
                        "unit_amount": amount_cents
                    },
                    "quantity": 1
                }],
                mode="payment",
                success_url="https://kadiocoiffure.com/merci?status=success",
                cancel_url="https://kadiocoiffure.com/merci?status=cancel",
                metadata={
                    "service": description,
                    "type": "deposit",
                    "salon": "Kadio Coiffure"
                }
            )
            
            return {
                "success": True,
                "link": session.url,
                "session_id": session.id,
                "amount": amount,
                "currency": "CAD"
            }
            
        except Exception as e:
            return {"error": str(e), "link": self.link_base}
    
    async def verify_payment(self, session_id: str) -> Dict:
        """Vérifie si un paiement a été complété"""
        if not self.is_connected():
            return {"error": "Stripe non configuré"}
        
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                "paid": session.payment_status == "paid",
                "status": session.payment_status,
                "amount": session.amount_total / 100 if session.amount_total else 0,
                "customer": session.customer_details.email if session.customer_details else None
            }
        except Exception as e:
            return {"error": str(e)}
    
    async def send_deposit_link(self, phone: str, service_name: str, amount: float, booking_id: str = None) -> Dict:
        """
        Génère et envoie un lien de dépôt au client via WhatsApp.
        Retourne le lien à envoyer.
        """
        result = await self.create_payment_link(amount, service_name)
        
        if result.get("success"):
            link = result["link"]
            message = (
                f"💳 *Dépôt de sécurité requis*\n\n"
                f"Service: {service_name}\n"
                f"Montant du dépôt: {amount}$ CAD\n\n"
                f"Pour confirmer votre rendez-vous, veuillez payer le dépôt ici:\n"
                f"{link}\n\n"
                f"⚠️ Votre rendez-vous ne sera confirmé qu'après réception du paiement.\n\n"
                f"Merci de choisir Kadio Coiffure! 🖤"
            )
            return {"success": True, "message": message, "link": link, "amount": amount}
        
        # Fallback: lien de base
        return {
            "success": False,
            "message": (
                f"💳 *Dépôt de sécurité: {amount}$*\n\n"
                f"Veuillez payer ici: {self.link_base}\n\n"
                f"⚠️ Mentionnez votre nom et le service: {service_name}"
            ),
            "link": self.link_base,
            "amount": amount
        }
