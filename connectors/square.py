import os
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

class SquareConnector:
    """Connecteur Square API pour gestion des rendez-vous Kadio Coiffure"""
    
    def __init__(self):
        self.token = os.getenv("SQUARE_TOKEN", "")
        self.location_id = os.getenv("SQUARE_LOCATION", "LTDE9RP9PSHX7")
        self.app_id = os.getenv("SQUARE_APP_ID", "")
        self.base_url = "https://connect.squareup.com/v2"
        self.headers = {
            "Square-Version": "2024-04-24",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        self._services_cache = None
        self._team_members_cache = None
        
        # Services personnalisés (non encore dans Square)
        self._custom_services = {
            "repousses_locks_petit_crochet": {
                "id": "CUSTOM_REP_PIC",
                "name": "Repousses locks avec petit crochet",
                "price": 175.0,
                "duration": 180,  # 3h+
                "category": "locks",
                "deposit_required": True,
                "deposit_percent": 20
            },
            "detox_locks": {
                "id": "CUSTOM_DETOX",
                "name": "Détox locks",
                "price": 75.0,
                "duration": 60,  # 1h
                "category": "locks",
                "deposit_required": True,
                "deposit_percent": 20
            },
            "pixie_cut_boucle_fer": {
                "id": "CUSTOM_PIXIE_FER",
                "name": "Pixie cut au fer",
                "price": 50.0,
                "duration": 60,  # 1h
                "category": "coiffure",
                "deposit_required": False,
                "deposit_percent": 0,
                "subscription": True,
                "subscription_period": "monthly",
                "includes": ["coupe", "brushing", "boucles au fer", "2 lissages/semaine (sans lavage)"],
                "excludes": ["lavage", "shampoing"]
            }
        }
        
        # Règles de dépôt : 20% pour locks/tresses, 0% pour barbier
        self._deposit_rules = {
            "locks": {"required": True, "percent": 20},
            "tresses": {"required": True, "percent": 20},
            "barbier": {"required": False, "percent": 0},
            "coiffure": {"required": True, "percent": 20}
        }
    
    def is_connected(self) -> bool:
        return bool(self.token)
    
    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.base_url}/{endpoint}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=15)
        return resp.json()
    
    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}/{endpoint}"
        resp = requests.post(url, headers=self.headers, json=payload, timeout=15)
        return resp.json()
    
    # ========== SERVICES ==========
    
    async def list_services(self) -> List[Dict]:
        """Liste tous les services du catalogue Square"""
        if self._services_cache:
            return self._services_cache
        
        result = self._get("catalog/list", {"types": "ITEM"})
        items = result.get("objects", [])
        
        services = []
        for item in items:
            if item.get("type") == "ITEM":
                data = item.get("item_data", {})
                variations = data.get("variations", [])
                for var in variations:
                    var_data = var.get("item_variation_data", {})
                    services.append({
                        "id": var.get("id"),
                        "version": var.get("version", 1),
                        "name": data.get("name", "Inconnu"),
                        "variation_name": var_data.get("name", ""),
                        "duration": var_data.get("item_option_values", [{}])[0].get("duration_minutes", 60) if var_data.get("item_option_values") else 60,
                        "price": var_data.get("price_money", {}).get("amount", 0) / 100 if var_data.get("price_money") else 0,
                        "category": data.get("category_id", "")
                    })
        
        self._services_cache = services
        return services
    
    async def find_service(self, query: str) -> Optional[Dict]:
        """Trouve un service par nom (fuzzy match) - inclut services custom"""
        query_lower = query.lower()
        
        # 1. Chercher d'abord dans les services custom
        for svc_id, svc in self._custom_services.items():
            if query_lower in svc["name"].lower() or any(k in query_lower for k in svc["name"].lower().split()):
                return {**svc, "is_custom": True}
        
        # 2. Puis chercher dans Square
        services = await self.list_services()
        
        # Match exact d'abord
        for s in services:
            if query_lower in s["name"].lower() or query_lower in s["variation_name"].lower():
                return {**s, "is_custom": False}
        
        # Match partiel
        for s in services:
            name = s["name"].lower()
            if any(word in name for word in query_lower.split()):
                return {**s, "is_custom": False}
        
        return None
    
    # ========== TEAM MEMBERS ==========
    
    async def list_team_members(self) -> List[Dict]:
        """Liste les coiffeurs/staff"""
        if self._team_members_cache:
            return self._team_members_cache
        
        result = self._post("team-members/search", {})
        members = []
        for m in result.get("team_members", []):
            job_title = ""
            if m.get("wage_setting") and m["wage_setting"].get("job_assignments"):
                job_title = m["wage_setting"]["job_assignments"][0].get("job_title", "")
            
            members.append({
                "id": m.get("id"),
                "name": f"{m.get('given_name', '')} {m.get('family_name', '')}".strip() or "Staff",
                "email": m.get("email_address", ""),
                "status": m.get("status", "ACTIVE"),
                "job_title": job_title,
                "is_owner": m.get("is_owner", False)
            })
        
        self._team_members_cache = members
        return members
    
    async def get_available_member(self, service_id: str = None, service_name: str = None) -> Optional[Dict]:
        """
        Retourne le meilleur coiffeur selon le service demandé.
        Rôles réels Kadio Coiffure :
        - Othi : Locticien (expert) + Barbier (toutes coupes)
        - Wilfried : Barbier + Shampoing
        - Princesse : Coiffeuse
        - Mariel : Locticien + Barbier + Brushings
        - Aïcha : Coiffeuse + Semi-locticienne (repousses gel/crochet, PAS extensions/réparations)
        - Ange : Coiffeuse + Semi-locticienne (même que Aïcha)
        - Raquel : Locticienne (INACTIVE actuellement)
        """
        members = await self.list_team_members()
        active = [m for m in members if m.get("status") == "ACTIVE"]
        
        if not active:
            return members[0] if members else None
        
        query = (service_name or "").lower()
        
        # --- DÉTECTION DU TYPE DE SERVICE ---
        is_locks = any(k in query for k in ["lock", "dread", "retwist", "repousse"])
        is_tresses = any(k in query for k in ["tresse", "twist", "braid", "natte", "cornrow", "bantu"])
        is_barbier = any(k in query for k in ["barbe", "barbier", "coupe", "fade", "dégradé", "homme", "enfant", "shampoing", "lavage"])
        is_brushing = any(k in query for k in ["brushing", "blow-dry", "séchage"])
        is_nail = any(k in query for k in ["nail", "manucure", "ongle"])
        
        # --- RECHERCHE PAR SPÉCIALITÉ ---
        matched = []
        
        if is_locks:
            # Locks : Othi (expert) et Mariel (locticien) en priorité
            # Pour repousses simples : Aïcha et Ange (semi-locticiennes) acceptables
            is_repass_simple = any(k in query for k in ["repousse", "retwist", "gel", "crochet"]) and not any(k in query for k in ["extension", "réparation", "grosse", "grande"])
            
            # Experts locks en priorité
            for m in active:
                if "othi" in m.get("name", "").lower():
                    return m
                if "mariel" in m.get("name", "").lower():
                    matched.append(m)
            
            # Semi-locticiennes pour repousses simples
            if is_repass_simple:
                for m in active:
                    if any(n in m.get("name", "").lower() for n in ["aïcha", "aicha", "ange"]):
                        matched.append(m)
            
            # Raquel si elle redevient active
            for m in members:
                if "raquel" in m.get("name", "").lower() and m.get("status") == "ACTIVE":
                    matched.append(m)
                    
        elif is_tresses:
            # Tresses : coiffeuses (Princesse, Aïcha, Ange)
            for m in active:
                if any(n in m.get("name", "").lower() for n in ["princesse", "aïcha", "aicha", "ange"]):
                    matched.append(m)
                    
        elif is_barbier or is_brushing:
            # Barbier : Othi, Wilfried, Mariel
            for m in active:
                if any(n in m.get("name", "").lower() for n in ["othi", "wilfried", "mariel"]):
                    matched.append(m)
                    
        elif is_nail:
            for m in active:
                if "mariane" in m.get("name", "").lower() or "bérubé" in m.get("name", "").lower():
                    matched.append(m)
        
        # --- SÉLECTION FINALE ---
        if matched:
            # Priorité : Othi (propriétaire/expert) si dans la liste
            for m in matched:
                if m.get("is_owner") or "othi" in m.get("name", "").lower():
                    return m
            return matched[0]
        
        # Fallback : Othi par défaut (il fait tout)
        for m in active:
            if m.get("is_owner") or "othi" in m.get("name", "").lower():
                return m
        
        return active[0]
    
    # ========== RENDEZ-VOUS ==========
    
    async def list_appointments(self, date: str = None, limit: int = 50) -> List[Dict]:
        """Liste les rendez-vous. date=YYYY-MM-DD"""
        params = {"limit": limit}
        if date:
            start = f"{date}T00:00:00Z"
            end = f"{date}T23:59:59Z"
            params["start_at_min"] = start
            params["start_at_max"] = end
        
        result = self._get("bookings", params)
        bookings = result.get("bookings", [])
        
        # Enrichir avec noms de services
        services = {s["id"]: s["name"] for s in await self.list_services()}
        
        for b in bookings:
            for seg in b.get("appointment_segments", []):
                svc_id = seg.get("service_variation_id", "")
                seg["service_name"] = services.get(svc_id, "Service inconnu")
        
        return bookings
    
    async def get_appointment(self, booking_id: str) -> Optional[Dict]:
        """Détail d'un rendez-vous"""
        result = self._get(f"bookings/{booking_id}")
        return result.get("booking")
    
    async def create_appointment(self, client_data: dict) -> Dict:
        """
        Crée un rendez-vous dans Square avec vérification des disponibilités.
        Si le créneau est occupé, retourne les créneaux disponibles.
        """
        date_str = client_data.get("date", "")
        time_str = client_data.get("time", "")
        
        # VÉRIFICATION 1 : Vérifier les disponibilités AVANT tout
        availability = await self.check_availability(date_str, client_data.get("service_name"))
        
        if time_str not in availability.get("available_slots", []):
            # Créneau non disponible
            return {
                "error": "Créneau non disponible",
                "requested_slot": f"{date_str} {time_str}",
                "available_slots": availability.get("available_slots", []),
                "message": f"❌ Le créneau {time_str} est déjà pris.\n\nCréneaux disponibles le {date_str} :\n" + "\n".join(availability.get("available_slots", [])[:10]),
                "alternative": {
                    "date": date_str,
                    "available_slots": availability.get("available_slots", [])
                }
            }
        
        # Trouver le service
        service = await self.find_service(client_data.get("service_name", ""))
        if not service:
            return {"error": "Service non trouvé", "available_services": [s["name"] for s in await self.list_services()]}
        
        # Trouver un coiffeur adapté
        member = await self.get_available_member(service["id"], service.get("name", ""))
        if not member:
            return {"error": "Aucun coiffeur disponible"}
        
        # Construire la date/heure
        start_at = f"{date_str}T{time_str}:00-05:00"  # Timezone EST (Québec)
        
        # Créer ou trouver le client
        customer_id = await self._find_or_create_customer(
            client_data.get("client_name", ""),
            client_data.get("phone", "")
        )
        
        # Gérer service custom vs Square
        is_custom = service.get("is_custom", False)
        
        # Calcul du dépôt
        deposit_info = self._calculate_deposit(service)
        
        # Construire la note avec dépôt si applicable
        notes = client_data.get("notes", "Réservation via agent IA")
        if is_custom:
            notes = f"{service['name']} - {service['price']}$ - {service['duration']}min | {notes}"
        if deposit_info["required"]:
            notes = f"DÉPÔT REQUIS: {deposit_info['amount']}$ ({deposit_info['percent']}%) | {notes}"
        
        # Pour service custom sans ID Square, utiliser un service par défaut
        svc_id = service["id"] if not is_custom else "VSOL3FRYL3PLRFPGKJZII7ZV"
        svc_version = service.get("version", 1) if not is_custom else 1780002625436
        
        # VÉRIFICATION 2 : Double-check — s'assurer que le créneau est toujours libre
        # (éviter race condition)
        current_bookings = await self.list_appointments(date_str)
        for b in current_bookings:
            b_start = b.get("start_at", "")[:16]
            requested_start = f"{date_str}T{time_str}:00"[:16]
            if b_start == requested_start:
                return {
                    "error": "Créneau vient d'être pris",
                    "requested_slot": f"{date_str} {time_str}",
                    "available_slots": availability.get("available_slots", []),
                    "message": f"❌ Désolé, quelqu'un vient de réserver ce créneau.\n\nAutres créneaux disponibles :\n" + "\n".join(availability.get("available_slots", [])[:10])
                }
        
        payload = {
            "booking": {
                "start_at": start_at,
                "location_id": self.location_id,
                "customer_id": customer_id,
                "appointment_segments": [{
                    "duration_minutes": service.get("duration", 60),
                    "service_variation_id": svc_id,
                    "service_variation_version": svc_version,
                    "team_member_id": member["id"]
                }],
                "seller_note": notes
            }
        }
        
        result = self._post("bookings", payload)
        
        if "booking" in result:
            booking = result["booking"]
            return {
                "success": True,
                "booking_id": booking.get("id"),
                "status": booking.get("status"),
                "start_at": booking.get("start_at"),
                "service": service["name"],
                "coiffeur": member["name"],
                "client": client_data.get("client_name"),
                "deposit": deposit_info if deposit_info["required"] else None,
                "message": f"✅ Rendez-vous confirmé pour {client_data['client_name']} le {date_str} à {time_str} — {service['name']} avec {member['name']}" + (f" | Dépôt requis: {deposit_info['amount']}$" if deposit_info["required"] else "")
            }
        
        # Si erreur Square, retourner les disponibilités alternatives
        errors = result.get("errors", [])
        if errors:
            error_msg = errors[0].get("detail", "Erreur inconnue") if isinstance(errors, list) else str(errors)
            return {
                "error": error_msg,
                "available_slots": availability.get("available_slots", []),
                "message": f"❌ {error_msg}\n\nCréneaux disponibles :\n" + "\n".join(availability.get("available_slots", [])[:10])
            }
        
        return {"error": "Erreur inconnue", "available_slots": availability.get("available_slots", [])}
    
    def _calculate_deposit(self, service: dict) -> dict:
        """Calcule le dépôt requis selon le type de service"""
        name = service.get("name", "").lower()
        category = service.get("category", "").lower()
        price = service.get("price", 0)
        
        # Déterminer la catégorie
        is_locks = any(k in name for k in ["lock", "dread", "retwist", "repousse"])
        is_tresses = any(k in name for k in ["tresse", "twist", "braid", "natte", "cornrow", "bantu"])
        is_barbier = any(k in name for k in ["barbe", "barbier", "coupe homme", "coupe enfant", "shampoing", "lavage"])
        
        if is_locks or is_tresses:
            return {"required": True, "percent": 20, "amount": round(price * 0.20, 2), "reason": "Locks/Tresses"}
        elif is_barbier:
            return {"required": False, "percent": 0, "amount": 0, "reason": "Barbier - pas de dépôt"}
        else:
            # Par défaut : 20% pour services longs (>1h), 0% pour courts
            if service.get("duration", 60) > 60:
                return {"required": True, "percent": 20, "amount": round(price * 0.20, 2), "reason": "Service long"}
            return {"required": False, "percent": 0, "amount": 0, "reason": "Service court"}
    
    async def cancel_appointment(self, booking_id: str, reason: str = "") -> Dict:
        """Annule un rendez-vous"""
        payload = {
            "booking_id": booking_id,
            "cancel_reason": reason or "Annulation client"
        }
        result = self._post(f"bookings/{booking_id}/cancel", payload)
        
        if "booking" in result:
            return {"success": True, "status": result["booking"].get("status")}
        return {"error": result.get("errors", "Échec annulation")}
    
    async def check_availability(self, date: str, service_name: str = None) -> Dict:
        """
        Vérifie les créneaux disponibles pour une date.
        date: YYYY-MM-DD
        """
        # Récupérer les rendez-vous existants
        bookings = await self.list_appointments(date)
        
        # Créneaux standards du salon (10h-21h, 30-60 min par créneau)
        slots = []
        start_hour = 10
        end_hour = 21
        
        for hour in range(start_hour, end_hour):
            for minute in [0, 30]:
                time_str = f"{hour:02d}:{minute:02d}"
                full_dt = f"{date}T{time_str}:00"
                
                # Vérifier si ce créneau est occupé
                occupied = False
                for b in bookings:
                    b_start = b.get("start_at", "")[:16]
                    if b_start == full_dt[:16]:
                        occupied = True
                        break
                
                if not occupied:
                    slots.append(time_str)
        
        return {
            "date": date,
            "available_slots": slots,
            "total_slots": len(slots),
            "bookings_count": len(bookings)
        }
    
    # ========== CLIENTS ==========
    
    async def _find_or_create_customer(self, name: str, phone: str) -> str:
        """Trouve ou crée un client Square, retourne l'ID"""
        # Chercher par téléphone
        search = self._post("customers/search", {
            "query": {
                "filter": {
                    "phone_number": {"exact": phone}
                }
            }
        })
        
        customers = search.get("customers", [])
        if customers:
            return customers[0].get("id")
        
        # Créer
        create = self._post("customers", {
            "given_name": name.split()[0] if name else "Client",
            "family_name": " ".join(name.split()[1:]) if len(name.split()) > 1 else "",
            "phone_number": phone
        })
        
        if "customer" in create:
            return create["customer"].get("id")
        
        # Fallback: créer un client générique
        fallback = self._post("customers", {
            "given_name": name or "Client",
            "phone_number": phone
        })
        return fallback.get("customer", {}).get("id", "")
    
    async def get_customer(self, customer_id: str) -> Optional[Dict]:
        """Récupère un client par ID"""
        result = self._get(f"customers/{customer_id}")
        return result.get("customer")
    
    # ========== WEBHOOKS ==========
    
    async def setup_webhook(self, endpoint_url: str) -> Dict:
        """Configure un webhook Square pour recevoir les notifications de RDV"""
        payload = {
            "webhook": {
                "event_types": [
                    "booking.created",
                    "booking.updated",
                    "booking.cancelled"
                ],
                "notification_url": endpoint_url,
                "api_version": "2024-04-24"
            }
        }
        result = self._post("webhooks", payload)
        return result
    
    # ========== STATS ==========
    
    async def get_daily_stats(self, date: str = None) -> Dict:
        """Stats du jour"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        bookings = await self.list_appointments(date)
        total = len(bookings)
        
        # Calculer revenus estimés
        services = {s["id"]: s["price"] for s in await self.list_services()}
        revenue = 0
        for b in bookings:
            for seg in b.get("appointment_segments", []):
                svc_id = seg.get("service_variation_id", "")
                revenue += services.get(svc_id, 0)
        
        return {
            "date": date,
            "total_bookings": total,
            "estimated_revenue": round(revenue, 2),
            "bookings": [
                {
                    "time": b.get("start_at", "")[11:16],
                    "client_id": b.get("customer_id"),
                    "status": b.get("status"),
                    "service": seg.get("service_name", "Inconnu")
                }
                for b in bookings
                for seg in b.get("appointment_segments", [{}])
            ]
        }
