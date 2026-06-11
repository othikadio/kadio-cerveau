# Instagram Connector placeholder
class InstagramConnector:
    def __init__(self):
        pass
    def is_connected(self):
        return False
    def connect(self, token):
        return {"connected": False}
    def send_message(self, recipient, message):
        return {"success": False}
    def get_oauth_url(self):
        return ""
    def connect_facebook(self, token):
        return {"connected": False}
    def get_facebook_oauth_url(self):
        return ""
