import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

@dataclass
class OwnerAction:
    """Une action faite par le propriétaire"""
    timestamp: str
    platform: str  # whatsapp, instagram, phone, etc.
    action_type: str  # reply, create_rdv, cancel_rdv, send_price, etc.
    client_id: str
    content: str  # ce qui a été dit/fait
    context: str  # la situation (message client, heure, etc.)
    duration_seconds: int  # temps de réponse

@dataclass
class LearnedPattern:
    """Un pattern appris"""
    pattern_type: str  # pricing, greeting, rdv_process, cancellation, etc.
    trigger: str  # ce qui déclenche le pattern
    response: str  # ce que le propriétaire fait d'habitude
    frequency: int  # combien de fois vu
    confidence: float  # 0-100
    autonomy_enabled: bool = False

class ObservationEngine:
    """Moteur d'observation et d'apprentissage du propriétaire"""
    
    def __init__(self, db_path: str = "./observation.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Initialise la base de données"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Actions du propriétaire
        c.execute('''
            CREATE TABLE IF NOT EXISTS owner_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                platform TEXT,
                action_type TEXT,
                client_id TEXT,
                content TEXT,
                context TEXT,
                duration_seconds INTEGER,
                day_number INTEGER,
                owner_id TEXT
            )
        ''')
        
        # Patterns appris
        c.execute('''
            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT,
                trigger TEXT,
                response TEXT,
                frequency INTEGER DEFAULT 1,
                confidence REAL DEFAULT 0,
                autonomy_enabled INTEGER DEFAULT 0,
                last_seen TEXT,
                owner_id TEXT
            )
        ''')
        
        # Résumés quotidiens
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                total_messages INTEGER,
                total_calls INTEGER,
                rdv_created INTEGER,
                rdv_cancelled INTEGER,
                avg_response_time INTEGER,
                common_questions TEXT,  -- JSON
                owner_id TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    # ========== OBSERVATION ==========
    
    def observe_action(self, action: OwnerAction, owner_id: str = "default"):
        """Enregistre une action du propriétaire"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        day_number = self.get_day_number(owner_id)
        
        c.execute('''
            INSERT INTO owner_actions 
            (timestamp, platform, action_type, client_id, content, context, duration_seconds, day_number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            action.timestamp, action.platform, action.action_type,
            action.client_id, action.content, action.context,
            action.duration_seconds, day_number
        ))
        
        conn.commit()
        conn.close()
        
        # Analyse le pattern après chaque action
        self.analyze_pattern(action, owner_id)
    
    def get_day_number(self, owner_id: str) -> int:
        """Calcule quel jour d'observation on est"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT COUNT(DISTINCT date(timestamp)) 
            FROM owner_actions 
            WHERE owner_id = ?
        ''', (owner_id,))
        
        result = c.fetchone()
        conn.close()
        
        return (result[0] or 0) + 1
    
    # ========== ANALYSE PATTERNS ==========
    
    def analyze_pattern(self, action: OwnerAction, owner_id: str):
        """Analyse un pattern et l'apprend"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Détecte le type de pattern
        pattern_type = self.detect_pattern_type(action)
        trigger = self.extract_trigger(action)
        
        # Vérifie si ce pattern existe déjà
        c.execute('''
            SELECT id, frequency, confidence FROM learned_patterns
            WHERE pattern_type = ? AND trigger = ? AND owner_id = ?
        ''', (pattern_type, trigger, owner_id))
        
        existing = c.fetchone()
        
        if existing:
            # Met à jour le pattern existant
            new_freq = existing[1] + 1
            new_conf = min(95, existing[2] + 5)  # +5% par observation, max 95
            
            c.execute('''
                UPDATE learned_patterns
                SET frequency = ?, confidence = ?, last_seen = ?
                WHERE id = ?
            ''', (new_freq, new_conf, datetime.now().isoformat(), existing[0]))
            
            # Active l'autonomie si confiance > 80
            if new_conf >= 80 and existing[2] < 80:
                c.execute('''
                    UPDATE learned_patterns
                    SET autonomy_enabled = 1
                    WHERE id = ?
                ''', (existing[0],))
        else:
            # Crée un nouveau pattern
            c.execute('''
                INSERT INTO learned_patterns
                (pattern_type, trigger, response, frequency, confidence, last_seen, owner_id)
                VALUES (?, ?, ?, 1, 10, ?, ?)
            ''', (pattern_type, trigger, action.content, datetime.now().isoformat(), owner_id))
        
        conn.commit()
        conn.close()
    
    def detect_pattern_type(self, action: OwnerAction) -> str:
        """Détecte le type de pattern"""
        content = action.content.lower()
        context = action.context.lower()
        
        if any(word in content for word in ['prix', 'tarif', 'combien', 'coûte', '$']):
            return 'pricing'
        elif any(word in content for word in ['bonjour', 'salut', 'hello', 'bonsoir']):
            return 'greeting'
        elif any(word in content for word in ['rendez-vous', 'rdv', 'appointment', 'disponible']):
            return 'rdv_creation'
        elif any(word in content for word in ['annule', 'cancel', 'annulation']):
            return 'cancellation'
        elif any(word in content for word in ['confirme', 'rappel', 'demain', 'rappelle']):
            return 'rdv_confirmation'
        elif any(word in content for word in ['merci', 'au revoir', 'belle journée']):
            return 'closing'
        elif 'en retard' in context or 'retard' in content:
            return 'late_handling'
        else:
            return 'general_response'
    
    def extract_trigger(self, action: OwnerAction) -> str:
        """Extrait le déclencheur du pattern"""
        context = action.context.lower()
        
        # Simplifie le contexte pour matcher les patterns similaires
        if 'prix' in context or 'combien' in context:
            service = self.extract_service(context)
            return f'pricing_{service}'
        elif 'rdv' in context or 'rendez-vous' in context:
            return 'rdv_request'
        elif 'annule' in context:
            return 'cancellation_request'
        elif 'salut' in context or 'bonjour' in context:
            return 'greeting'
        else:
            return context[:50]  # Premier 50 chars comme trigger
    
    def extract_service(self, text: str) -> str:
        """Extrait le service mentionné"""
        services = ['locks', 'tresses', 'twists', 'coupe', 'barbier', 'perruque', 'mise en plis']
        text_lower = text.lower()
        for service in services:
            if service in text_lower:
                return service
        return 'general'
    
    # ========== RAPPORTS ==========
    
    def generate_daily_summary(self, owner_id: str = "default") -> Dict:
        """Génère un résumé quotidien"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        today = datetime.now().date().isoformat()
        
        # Stats du jour
        c.execute('''
            SELECT 
                COUNT(*) as total_actions,
                COUNT(DISTINCT platform) as platforms_used,
                COUNT(CASE WHEN action_type = 'reply' THEN 1 END) as replies,
                COUNT(CASE WHEN action_type = 'create_rdv' THEN 1 END) as rdv_created,
                COUNT(CASE WHEN action_type = 'cancel_rdv' THEN 1 END) as rdv_cancelled,
                AVG(duration_seconds) as avg_response_time
            FROM owner_actions
            WHERE date(timestamp) = ? AND owner_id = ?
        ''', (today, owner_id))
        
        stats = c.fetchone()
        
        # Questions fréquentes
        c.execute('''
            SELECT context, COUNT(*) as freq
            FROM owner_actions
            WHERE date(timestamp) = ? AND owner_id = ?
            GROUP BY context
            ORDER BY freq DESC
            LIMIT 5
        ''', (today, owner_id))
        
        common = c.fetchall()
        
        conn.close()
        
        return {
            'date': today,
            'total_actions': stats[0] or 0,
            'platforms_used': stats[1] or 0,
            'replies': stats[2] or 0,
            'rdv_created': stats[3] or 0,
            'rdv_cancelled': stats[4] or 0,
            'avg_response_time': round(stats[5] or 0),
            'common_questions': [c[0] for c in common]
        }
    
    def get_confidence_scores(self, owner_id: str = "default") -> Dict[str, float]:
        """Récupère les scores de confiance par type de tâche"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT pattern_type, AVG(confidence) as avg_conf, MAX(autonomy_enabled) as auto
            FROM learned_patterns
            WHERE owner_id = ?
            GROUP BY pattern_type
        ''', (owner_id,))
        
        results = c.fetchall()
        conn.close()
        
        scores = {}
        for row in results:
            scores[row[0]] = {
                'confidence': round(row[1], 1),
                'autonomy': bool(row[2])
            }
        
        return scores
    
    # ========== PROPOSITIONS ==========
    
    def generate_proposals(self, owner_id: str = "default") -> List[Dict]:
        """Génère des propositions d'autonomie"""
        scores = self.get_confidence_scores(owner_id)
        proposals = []
        
        for pattern_type, data in scores.items():
            if data['confidence'] >= 70 and not data['autonomy']:
                proposals.append({
                    'pattern_type': pattern_type,
                    'confidence': data['confidence'],
                    'message': f"Je peux répondre aux questions de type '{pattern_type}' ({data['confidence']}% de confiance). Tu veux que je prenne le relais ?",
                    'action': 'enable_autonomy'
                })
            elif data['confidence'] >= 50 and data['confidence'] < 70:
                proposals.append({
                    'pattern_type': pattern_type,
                    'confidence': data['confidence'],
                    'message': f"Je commence à comprendre les questions de type '{pattern_type}' ({data['confidence']}% de confiance). Encore quelques exemples et je pourrai prendre le relais.",
                    'action': 'continue_learning'
                })
        
        return proposals
    
    # ========== TAKEOVER ==========
    
    def can_handle_autonomously(self, context: str, owner_id: str = "default") -> bool:
        """Détermine si une situation peut être gérée automatiquement"""
        pattern_type = self.detect_pattern_type_from_context(context)
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT autonomy_enabled, confidence FROM learned_patterns
            WHERE pattern_type = ? AND owner_id = ?
            ORDER BY confidence DESC
            LIMIT 1
        ''', (pattern_type, owner_id))
        
        result = c.fetchone()
        conn.close()
        
        if result and result[0] and result[1] >= 80:
            return True
        return False
    
    def detect_pattern_type_from_context(self, context: str) -> str:
        """Détecte le type de pattern depuis un contexte"""
        context_lower = context.lower()
        
        if any(word in context_lower for word in ['prix', 'tarif', 'combien', 'coûte']):
            return 'pricing'
        elif any(word in context_lower for word in ['bonjour', 'salut', 'hello']):
            return 'greeting'
        elif any(word in context_lower for word in ['rendez-vous', 'rdv', 'appointment']):
            return 'rdv_creation'
        elif any(word in context_lower for word in ['annule', 'cancel']):
            return 'cancellation'
        else:
            return 'general_response'
    
    def get_learned_response(self, context: str, owner_id: str = "default") -> Optional[str]:
        """Récupère la réponse apprise pour un contexte"""
        pattern_type = self.detect_pattern_type_from_context(context)
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT response FROM learned_patterns
            WHERE pattern_type = ? AND owner_id = ? AND autonomy_enabled = 1
            ORDER BY confidence DESC, frequency DESC
            LIMIT 1
        ''', (pattern_type, owner_id))
        
        result = c.fetchone()
        conn.close()
        
        return result[0] if result else None

# ========== INTERFACE POUR LE DASHBOARD ==========

class OnboardingDashboard:
    """Interface pour le dashboard de l'onboarding"""
    
    def __init__(self, engine: ObservationEngine):
        self.engine = engine
    
    def get_status(self, owner_id: str = "default") -> Dict:
        """Retourne le statut d'onboarding"""
        day = self.engine.get_day_number(owner_id)
        scores = self.engine.get_confidence_scores(owner_id)
        proposals = self.engine.generate_proposals(owner_id)
        
        # Détermine la phase
        if day <= 3:
            phase = 'observation'
            phase_name = 'Observation'
        elif day <= 5:
            phase = 'copilot'
            phase_name = 'Copilot'
        else:
            phase = 'autonomy'
            phase_name = 'Autonomie'
        
        # Compte les tâches autonomes
        total_patterns = len(scores)
        autonomous_patterns = sum(1 for s in scores.values() if s['autonomy'])
        
        return {
            'day': day,
            'phase': phase,
            'phase_name': phase_name,
            'total_patterns_learned': total_patterns,
            'autonomous_tasks': autonomous_patterns,
            'confidence_scores': scores,
            'proposals': proposals,
            'progress_pct': min(100, (day / 7) * 100)  # 7 jours = 100%
        }
    
    def get_conversation_insights(self, owner_id: str = "default") -> List[Dict]:
        """Insights sur les conversations"""
        conn = sqlite3.connect(self.engine.db_path)
        c = conn.cursor()
        
        # Top questions
        c.execute('''
            SELECT context, COUNT(*) as freq, AVG(duration_seconds) as avg_time
            FROM owner_actions
            WHERE owner_id = ?
            GROUP BY context
            ORDER BY freq DESC
            LIMIT 10
        ''', (owner_id,))
        
        questions = []
        for row in c.fetchall():
            questions.append({
                'question': row[0],
                'frequency': row[1],
                'avg_response_time': round(row[2] or 0)
            })
        
        conn.close()
        return questions
    
    def get_learning_report(self, owner_id: str = "default") -> Dict:
        """Rapport complet d'apprentissage"""
        summary = self.engine.generate_daily_summary(owner_id)
        status = self.get_status(owner_id)
        
        return {
            'summary': summary,
            'status': status,
            'insights': self.get_conversation_insights(owner_id),
            'recommendations': self.generate_recommendations(owner_id)
        }
    
    def generate_recommendations(self, owner_id: str = "default") -> List[str]:
        """Génère des recommandations basées sur l'apprentissage"""
        scores = self.engine.get_confidence_scores(owner_id)
        recommendations = []
        
        if 'pricing' in scores and scores['pricing']['confidence'] > 80:
            recommendations.append("✅ Je peux répondre aux questions de prix automatiquement")
        
        if 'greeting' in scores and scores['greeting']['confidence'] > 80:
            recommendations.append("✅ Je peux saluer les nouveaux clients automatiquement")
        
        if 'rdv_creation' not in scores or scores.get('rdv_creation', {}).get('confidence', 0) < 50:
            recommendations.append("📊 Je n'ai pas encore assez observé la prise de RDV. Encore quelques exemples...")
        
        if 'cancellation' not in scores or scores.get('cancellation', {}).get('confidence', 0) < 50:
            recommendations.append("📊 Je n'ai pas encore assez observé les annulations. Encore quelques exemples...")
        
        return recommendations
