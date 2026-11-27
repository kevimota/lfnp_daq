from fastapi import WebSocket
from typing import List
import json


class DataBroadcaster:
    """Manages WebSocket connections and broadcasts data to all clients."""

    def __init__(self):
        self.clients: List[WebSocket] = []

    def add_client(self, websocket: WebSocket):
        """Add a new WebSocket client."""
        self.clients.append(websocket)

    def remove_client(self, websocket: WebSocket):
        """Remove a WebSocket client."""
        if websocket in self.clients:
            self.clients.remove(websocket)

    async def broadcast(self, data: dict):
        """Broadcast data to all connected clients."""
        if not self.clients:
            return

        message = json.dumps(data)
        disconnected = []

        for client in self.clients:
            try:
                await client.send_text(message)
            except Exception:
                disconnected.append(client)

        # Clean up disconnected clients
        for client in disconnected:
            self.remove_client(client)
