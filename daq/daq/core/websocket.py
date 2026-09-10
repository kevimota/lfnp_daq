from fastapi import WebSocket
from typing import List
import asyncio
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

    async def broadcast_raw(self, message: str):
        """Broadcast a raw string message to all clients."""
        if not self.clients:
            return

        disconnected = []

        for client in self.clients:
            try:
                await client.send_text(message)
            except Exception:
                disconnected.append(client)

        for client in disconnected:
            self.remove_client(client)

    def client_count(self) -> int:
        """Get the number of connected clients."""
        return len(self.clients)