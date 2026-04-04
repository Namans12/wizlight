"""WebSocket server for external clients (Chrome extension, Android app)."""

import asyncio
import json
import logging
from typing import Callable, Optional, Set

import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger(__name__)


class ColorSyncServer:
    """WebSocket server that receives RGB colors from external clients."""
    
    DEFAULT_PORT = 38901
    
    def __init__(
        self,
        on_color_change: Callable[[dict[str, tuple[int, int, int]]], None],
        port: int = DEFAULT_PORT,
        host: str = "0.0.0.0",
    ):
        self.on_color_change = on_color_change
        self.port = port
        self.host = host
        
        self._server: Optional[websockets.WebSocketServer] = None
        self._clients: Set[WebSocketServerProtocol] = set()
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._running:
            return
        
        self._running = True
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
        )
        logger.info(f"WebSocket server started on ws://{self.host}:{self.port}")
    
    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        
        # Close all client connections
        if self._clients:
            await asyncio.gather(
                *[client.close(1001, "Server shutting down") for client in self._clients],
                return_exceptions=True,
            )
            self._clients.clear()
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        
        logger.info("WebSocket server stopped")
    
    async def _handle_client(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a connected client."""
        self._clients.add(websocket)
        client_addr = websocket.remote_address
        logger.info(f"Client connected: {client_addr}")
        
        try:
            # Send welcome message with protocol info
            await websocket.send(json.dumps({
                "type": "welcome",
                "protocol": "wizlight-sync",
                "version": "1.0",
            }))
            
            async for message in websocket:
                await self._process_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client disconnected: {client_addr}")
        except Exception as e:
            logger.error(f"Error handling client {client_addr}: {e}")
        finally:
            self._clients.discard(websocket)
    
    async def _process_message(
        self,
        websocket: WebSocketServerProtocol,
        message: str,
    ) -> None:
        """Process incoming message from client."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "color":
                # Single color for all bulbs
                # Format: {"type": "color", "r": 255, "g": 128, "b": 64}
                r = int(data.get("r", 0))
                g = int(data.get("g", 0))
                b = int(data.get("b", 0))
                self.on_color_change({"all": (r, g, b)})
                
            elif msg_type == "colors":
                # Multiple colors by zone/bulb
                # Format: {"type": "colors", "colors": {"all": [255, 128, 64], "left": [...]}}
                colors = {}
                for key, rgb in data.get("colors", {}).items():
                    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                        colors[key] = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
                if colors:
                    self.on_color_change(colors)
                    
            elif msg_type == "ping":
                await websocket.send(json.dumps({"type": "pong"}))
                
            elif msg_type == "status":
                await websocket.send(json.dumps({
                    "type": "status",
                    "connected_clients": len(self._clients),
                    "running": self._running,
                }))
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    async def broadcast(self, message: dict) -> None:
        """Broadcast a message to all connected clients."""
        if not self._clients:
            return
        
        msg = json.dumps(message)
        await asyncio.gather(
            *[client.send(msg) for client in self._clients],
            return_exceptions=True,
        )
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def client_count(self) -> int:
        return len(self._clients)


async def run_server(
    on_color_change: Callable[[dict[str, tuple[int, int, int]]], None],
    port: int = ColorSyncServer.DEFAULT_PORT,
) -> None:
    """Run the WebSocket server until interrupted."""
    server = ColorSyncServer(on_color_change, port=port)
    await server.start()
    
    try:
        # Keep running until cancelled
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()
