import os
import stripe
import datetime
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
    
    async def create_subscription_link(self, customer_email: str, forfait: str = "mensuel", 
                                         code_parrainage: str = None, phone: str = None) -> Dict:
        """
        Crée un lien d'abonnement Stripe Checkout avec taxes Québec et code parrainage.
        Forfaits: mensuel (80$), trimestriel (220$), annuel (800$)
        Taxes Québec: TPS 5% + TVQ 9.975% = 14.975%
        """
        if not self.is_connected():
            return {"error": "Stripe non configuré", "link": None}
        
        # Prix HT
        prices_ht = {'mensuel': 80.00, 'trimestriel': 220.00, 'annuel': 800.00}
        prix_ht = prices_ht.get(forfait, 80.00)
        
        # Taxes Québec : TPS 5% + TVQ 9.975% = 14.975%
        TAX_RATE = 0.14975
        prix_ttc = round(prix_ht * (1 + TAX_RATE), 2)
        tax_amount = round(prix_ht * TAX_RATE, 2)
        
        # Labels
        labels = {
            'mensuel': ('Abonnement Mensuel Kadio', '80$ / mois + taxes (14.975%)', 'month'),
            'trimestriel': ('Abonnement Trimestriel Kadio', '220$ / 3 mois + taxes (14.975%)', 'month'),
            'annuel': ('Abonnement Annuel Kadio', '800$ / an + taxes (14.975%)', 'year')
        }
        nom, description, interval = labels.get(forfait, labels['mensuel'])
        
        # Prix en cents (TTC)
        amount_cents = int(prix_ttc * 100)
        
        try:
            # Préparer les discounts si code parrainage
            discounts = []
            coupon_id = None
            
            if code_parrainage and code_parrainage.startswith('KADIO-'):
                # Valider le code dans la base de données
                import sqlite3
                db_path = os.getenv("DB_PATH", "/opt/kadio-cerveau/kadio_gestion.db")
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute('SELECT telephone FROM comptes_clients WHERE code_parrainage = ?', (code_parrainage,))
                row = c.fetchone()
                conn.close()
                
                if row:
                    # Créer un coupon Stripe pour ce parrainage (réduction 10% première mensualité)
                    try:
                        coupon = stripe.Coupon.create(
                            percent_off=10,
                            duration="once",
                            name=f"Parrainage {code_parrainage}",
                            metadata={"code_parrainage": code_parrainage, "parrain_tel": row['telephone']}
                        )
                        coupon_id = coupon.id
                        discounts = [{"coupon": coupon_id}]
                    except Exception as e:
                        print(f"[Stripe] Erreur création coupon: {e}")
                        discounts = []
                else:
                    return {"error": "Code de parrainage invalide", "link": None}
            
            # Préparer les metadata
            metadata = {
                "service": f"abonnement_{forfait}",
                "type": "subscription",
                "salon": "Kadio Coiffure",
                "customer_email": customer_email,
                "forfait": forfait,
                "prix_ht": str(prix_ht),
                "taxes_qc": str(tax_amount),
                "prix_ttc": str(prix_ttc),
                "code_parrainage": code_parrainage or "none"
            }
            
            # Créer la session d'abonnement
            session_params = {
                "payment_method_types": ["card"],
                "customer_creation": "always",
                "line_items": [{
                    "price_data": {
                        "currency": "cad",
                        "product_data": {
                            "name": nom,
                            "description": f"{description} | Taxes QC (TPS+TVQ) incluses"
                        },
                        "unit_amount": amount_cents,
                        "recurring": {"interval": interval}
                    },
                    "quantity": 1,
                    "tax_rates": []  # Les taxes sont incluses dans le prix pour simplifier
                }],
                "mode": "subscription",
                "success_url": "https://kadiocoiffure.com/merci?abonnement=actif&session_id={CHECKOUT_SESSION_ID}&disconnect=1",
                "cancel_url": "https://kadiocoiffure.com/abonnement?status=cancel&disconnect=1",
                "metadata": metadata,
                "allow_promotion_codes": True,  # Permettre codes promo Stripe
            }
            
            if discounts:
                session_params["discounts"] = discounts
            
            session = stripe.checkout.Session.create(**session_params)
            
            # Mettre à jour le customer avec l'email
            if session.customer:
                stripe.Customer.modify(
                    session.customer,
                    email=customer_email,
                    metadata={"service": f"abonnement_{forfait}", "salon": "Kadio Coiffure", "forfait": forfait}
                )
            
            return {
                "success": True,
                "link": session.url,
                "session_id": session.id,
                "amount_ht": prix_ht,
                "taxes": tax_amount,
                "amount_ttc": prix_ttc,
                "currency": "CAD",
                "period": forfait,
                "forfait": forfait,
                "code_parrainage_applique": bool(code_parrainage and coupon_id),
                "message": f"{nom} — {prix_ht}$ + {tax_amount}$ taxes = {prix_ttc}$ TTC"
            }
            
        except Exception as e:
            return {"error": str(e), "link": None}
    
    async def create_payment_link_with_tax(self, amount: float, description: str, 
                                            customer_email: str = None, code_parrainage: str = None) -> Dict:
        """
        Crée un lien de paiement Stripe avec taxes Québec (14.975%) et code parrainage.
        """
        if not self.is_connected():
            return {"error": "Stripe non configuré", "link": self.link_base}
        
        # Taxes Québec
        TAX_RATE = 0.14975
        prix_ht = amount
        prix_ttc = round(prix_ht * (1 + TAX_RATE), 2)
        tax_amount = round(prix_ht * TAX_RATE, 2)
        amount_cents = int(prix_ttc * 100)
        
        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "cad",
                        "product_data": {
                            "name": description,
                            "description": f"Taxes QC (TPS+TVQ): {tax_amount}$ incluses"
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
                    "type": "payment",
                    "salon": "Kadio Coiffure",
                    "prix_ht": str(prix_ht),
                    "taxes_qc": str(tax_amount),
                    "prix_ttc": str(prix_ttc),
                    "code_parrainage": code_parrainage or "none"
                },
                allow_promotion_codes=True
            )
            
            return {
                "success": True,
                "link": session.url,
                "session_id": session.id,
                "amount_ht": prix_ht,
                "taxes": tax_amount,
                "amount_ttc": prix_ttc,
                "currency": "CAD",
                "message": f"{description} — {prix_ht}$ + {tax_amount}$ taxes = {prix_ttc}$ TTC"
            }
            
        except Exception as e:
            return {"error": str(e), "link": self.link_base}
        except Exception as e:
            return {"error": str(e), "link": None}
    
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
    
    async def send_subscription_link(self, phone: str, customer_email: str) -> Dict:
        """
        Génère et envoie le lien d'abonnement au client via WhatsApp.
        """
        result = await self.create_subscription_link(customer_email)
        
        if result.get("success"):
            link = result["link"]
            message = (
                f"🌀 *Abonnement Pixie cut au fer*\n\n"
                f"Prix: 50$/mois + taxes\n"
                f"Inclus chaque mois:\n"
                f"  • Coupe courte stylée\n"
                f"  • Boucles au fer à lisser\n"
                f"  • 2 lissages offerts par semaine\n\n"
                f"⚠️ Sans lavage ni shampoing\n\n"
                f"Pour activer votre abonnement:\n"
                f"{link}\n\n"
                f"Merci de choisir Kadio Coiffure! 🖤"
            )
            return {"success": True, "message": message, "link": link}
        
        return {
            "success": False,
            "message": "Erreur lors de la création du lien d'abonnement. Contactez Othi au 514-919-5970."
        }
    
    async def list_subscriptions(self, status: str = "all", limit: int = 100) -> Dict:
        """
        Liste tous les abonnements Stripe.
        status: 'all', 'active', 'canceled', 'incomplete', 'past_due'
        """
        if not self.is_connected():
            return {"error": "Stripe non configuré", "subscriptions": []}
        
        try:
            params = {"limit": limit, "expand": ["data.customer", "data.latest_invoice"]}
            if status != "all":
                params["status"] = status
            
            subs = stripe.Subscription.list(**params)
            
            results = []
            for sub in subs.auto_paging_iter():
                customer = sub.customer
                customer_email = customer.email if hasattr(customer, 'email') else None
                customer_name = customer.name if hasattr(customer, 'name') else None
                
                # Calculer le prochain paiement
                next_payment = None
                if sub.current_period_end:
                    import datetime
                    next_payment = datetime.datetime.fromtimestamp(sub.current_period_end).strftime("%Y-%m-%d")
                
                results.append({
                    "id": sub.id,
                    "status": sub.status,
                    "customer_id": customer.id if hasattr(customer, 'id') else sub.customer,
                    "customer_email": customer_email or "Non renseigné",
                    "customer_name": customer_name or "Non renseigné",
                    "amount": sub.plan.amount / 100 if sub.plan else 0,
                    "currency": sub.plan.currency.upper() if sub.plan else "CAD",
                    "interval": sub.plan.interval if sub.plan else "month",
                    "created": datetime.datetime.fromtimestamp(sub.created).strftime("%Y-%m-%d %H:%M") if sub.created else None,
                    "current_period_start": datetime.datetime.fromtimestamp(sub.current_period_start).strftime("%Y-%m-%d") if sub.current_period_start else None,
                    "current_period_end": next_payment,
                    "cancel_at_period_end": sub.cancel_at_period_end
                })
            
            return {
                "success": True,
                "count": len(results),
                "subscriptions": results
            }
            
        except Exception as e:
            return {"error": str(e), "subscriptions": []}
    
    async def cancel_subscription(self, subscription_id: str) -> Dict:
        """Annule un abonnement Stripe"""
        if not self.is_connected():
            return {"error": "Stripe non configuré"}
        
        try:
            sub = stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )
            return {
                "success": True,
                "status": sub.status,
                "cancel_at_period_end": sub.cancel_at_period_end,
                "message": "Abonnement annulé. Actif jusqu'à la fin de la période."
            }
        except Exception as e:
            return {"error": str(e)}
    
    async def get_subscription(self, subscription_id: str) -> Dict:
        """Récupère les détails d'un abonnement"""
        if not self.is_connected():
            return {"error": "Stripe non configuré"}
        
        try:
            sub = stripe.Subscription.retrieve(
                subscription_id,
                expand=["customer", "latest_invoice"]
            )
            return {"success": True, "subscription": sub}
        except Exception as e:
            return {"error": str(e)}
