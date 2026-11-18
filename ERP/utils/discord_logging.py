import logging
import requests

class DiscordHandler(logging.Handler):
    def __init__(self, webhook_url, username=None):
        super().__init__()
        self.webhook_url = webhook_url
        self.username = username

    def emit(self, record):
        try:
            message = self.format(record)
            max_length = 1900 
            chunks = [message[i:i+max_length] for i in range(0, len(message), max_length)]
            for chunk in chunks:
                payload = {
                    "username": self.username or "Django Logger",
                    "content": f"```\n{chunk}\n```"
                }
                requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception:
            self.handleError(record)
