/**
 * WizLight Video Sync - Background Service Worker
 * Manages WebSocket connection to WizLight server
 */

class WizLightConnection {
  constructor() {
    this.socket = null;
    this.serverUrl = 'ws://localhost:38901';
    this.isConnected = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 2000;
    this.reconnectTimer = null;
  }

  /**
   * Connect to WizLight WebSocket server
   */
  connect() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      this.socket = new WebSocket(this.serverUrl);

      this.socket.onopen = () => {
        console.log('WizLight: Connected to server');
        this.isConnected = true;
        this.reconnectAttempts = 0;
        
        // Notify popup of connection
        chrome.runtime.sendMessage({ type: 'connectionStatus', connected: true });
      };

      this.socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'welcome') {
            console.log('WizLight: Server protocol:', data.protocol, data.version);
          }
        } catch (e) {
          // Ignore parse errors
        }
      };

      this.socket.onclose = () => {
        console.log('WizLight: Disconnected from server');
        this.isConnected = false;
        this.socket = null;
        
        // Notify popup of disconnection
        chrome.runtime.sendMessage({ type: 'connectionStatus', connected: false });
        
        // Attempt reconnect
        this.scheduleReconnect();
      };

      this.socket.onerror = (error) => {
        console.warn('WizLight: WebSocket error', error);
      };

    } catch (e) {
      console.error('WizLight: Failed to create WebSocket', e);
      this.scheduleReconnect();
    }
  }

  /**
   * Disconnect from server
   */
  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    
    this.reconnectAttempts = this.maxReconnectAttempts; // Prevent auto-reconnect
    
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    
    this.isConnected = false;
  }

  /**
   * Schedule a reconnection attempt
   */
  scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('WizLight: Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * this.reconnectAttempts;
    
    console.log(`WizLight: Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  /**
   * Send color to server
   */
  sendColor(r, g, b) {
    if (!this.isConnected || !this.socket) {
      return false;
    }

    try {
      this.socket.send(JSON.stringify({
        type: 'color',
        r: r,
        g: g,
        b: b
      }));
      return true;
    } catch (e) {
      console.warn('WizLight: Failed to send color', e);
      return false;
    }
  }

  /**
   * Update server URL
   */
  setServerUrl(url) {
    this.serverUrl = url;
    
    // Reconnect with new URL if currently connected
    if (this.isConnected) {
      this.disconnect();
      this.reconnectAttempts = 0;
      this.connect();
    }
  }

  /**
   * Get connection status
   */
  getStatus() {
    return {
      connected: this.isConnected,
      serverUrl: this.serverUrl,
      reconnectAttempts: this.reconnectAttempts
    };
  }
}

// Global connection instance
const connection = new WizLightConnection();

// Load settings and auto-connect
chrome.storage.local.get(['serverUrl', 'autoConnect'], (result) => {
  if (result.serverUrl) {
    connection.serverUrl = result.serverUrl;
  }
  
  if (result.autoConnect !== false) {
    connection.connect();
  }
});

// Listen for messages from content script and popup
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'color':
      // Color from content script
      const color = message.color;
      connection.sendColor(color.r, color.g, color.b);
      break;
      
    case 'connect':
      connection.reconnectAttempts = 0;
      connection.connect();
      sendResponse({ success: true });
      break;
      
    case 'disconnect':
      connection.disconnect();
      sendResponse({ success: true });
      break;
      
    case 'setServerUrl':
      connection.setServerUrl(message.url);
      chrome.storage.local.set({ serverUrl: message.url });
      sendResponse({ success: true });
      break;
      
    case 'getConnectionStatus':
      sendResponse(connection.getStatus());
      break;
      
    case 'ping':
      sendResponse({ pong: true });
      break;
  }
  
  return true;
});

console.log('WizLight: Background service worker loaded');
