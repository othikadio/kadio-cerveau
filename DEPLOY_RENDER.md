# Déploiement Rapide - Render.com (Gratuit)

## 1. Créer un compte
Aller sur https://render.com et s'inscrire (gratuit)

## 2. New Web Service
- Cliquer "New +" → "Web Service"
- Choisir "Deploy an existing image from a registry" OU connecter GitHub

## 3. Configuration
- Name: `kadio-cerveau`
- Region: `Ohio (US East)`
- Runtime: `Python 3`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port 8000`

## 4. Variables d'environnement
Copier-coller TOUTES les variables du fichier `.env`:
```
KIMI_API_KEY=sk-1bGCdte6MSN98iizcr4eKOwexpiL9q3SV4z1ge4Erb6vWClx
SQUARE_TOKEN=EAAAl621sVKBGg0JYZaOIMRv7iHe8aOPxX5Ub6-Rfnrr5J9ovhf4dRC-i1WZrgC3
SQUARE_LOCATION=LTDE9RP9PSHX7
SQUARE_APP_ID=sq0idp-DnbOmDRw7V6S8mZpmviR8g
TWILIO_SID=AC91fdfb7e070990f0d116d64a97747c1f
TWILIO_AUTH=3304ac548aaee994ab4c0e5c96ae3761
TWILIO_PHONE_NUMBER=+13022328291
TWILIO_WHATSAPP_NUMBER=+13022328291
OTHI_PHONE=+15149195970
META_APP_ID=1694870868184251
META_APP_SECRET=6bcb4a9fa3df065e748dd1eb596c69ab
META_PAGE_ID=255568957645612
META_VERIFY_TOKEN=kadio-daleba-2026
WHATSAPP_ACCESS_TOKEN=EAALUccMcrHMBRuVlg0CZAUoXZBBIwC043dzy2NpEyth87LjxsqZAsuFAFwAjOfHpkCakywK6fAZCTWqNCOo60IZCT82nu0uIQkmBgvNZAPXVxSDoMRM1g4BELDFgLT5zsG8SmoGzSGES031BA3IBQpqVLpOFHNm3hCMen8bdp6E9NpISlrqUQPwsyCNF3MOmm4u8mTAg0MD1VUxHzORNIK9o5YWmiTp6ZBh4RqCY5xFwCBDOHj5lgZDZD
STRIPE_SECRET_KEY=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
STRIPE_LINK_BASE=https://buy.stripe.com/fZu8wO78Vaq6eAe6F96wE0r
```

## 5. Déployer
Cliquer "Create Web Service"
Attendre 2-3 minutes le build

## 6. URL du webhook
Une fois déployé, copier l'URL:
```
https://kadio-cerveau.onrender.com/webhook/whatsapp
```

## 7. Configurer Twilio
Dans Twilio Console → WhatsApp sandbox:
- Mettre à jour l'URL webhook avec l'URL Render + `/webhook/whatsapp`
- Method: POST

## ✅ Fait ! Le bot est en ligne.
