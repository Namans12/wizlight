/**
 * WizLight Video Sync - Popup Script
 * Controls extension settings and displays status
 */

// DOM elements
const elements = {
  connectionIndicator: document.getElementById('connectionIndicator'),
  connectionStatus: document.getElementById('connectionStatus'),
  videoStatus: document.getElementById('videoStatus'),
  colorPreview: document.getElementById('colorPreview'),
  toggleBtn: document.getElementById('toggleBtn'),
  connectBtn: document.getElementById('connectBtn'),
  serverUrl: document.getElementById('serverUrl'),
  fpsSlider: document.getElementById('fpsSlider'),
  fpsValue: document.getElementById('fpsValue'),
  boostSlider: document.getElementById('boostSlider'),
  boostValue: document.getElementById('boostValue'),
  autoStart: document.getElementById('autoStart'),
  autoConnect: document.getElementById('autoConnect')
};

// Current state
let syncEnabled = false;
let connected = false;

/**
 * Update UI based on current state
 */
function updateUI() {
  // Connection status
  if (connected) {
    elements.connectionIndicator.className = 'status-indicator connected';
    elements.connectionStatus.textContent = 'Connected';
    elements.connectBtn.textContent = 'Disconnect';
  } else {
    elements.connectionIndicator.className = 'status-indicator disconnected';
    elements.connectionStatus.textContent = 'Disconnected';
    elements.connectBtn.textContent = 'Connect';
  }

  // Sync button
  elements.toggleBtn.textContent = syncEnabled ? 'Stop Sync' : 'Start Sync';
  elements.toggleBtn.disabled = !connected;
}

/**
 * Update color preview
 */
function updateColorPreview(color) {
  if (color) {
    elements.colorPreview.style.background = `rgb(${color.r}, ${color.g}, ${color.b})`;
  }
}

/**
 * Load saved settings
 */
async function loadSettings() {
  const settings = await chrome.storage.local.get([
    'serverUrl',
    'fps',
    'colorBoost',
    'autoStart',
    'autoConnect'
  ]);

  if (settings.serverUrl) {
    elements.serverUrl.value = settings.serverUrl;
  }
  
  if (settings.fps) {
    elements.fpsSlider.value = settings.fps;
    elements.fpsValue.textContent = settings.fps;
  }
  
  if (settings.colorBoost) {
    elements.boostSlider.value = Math.round(settings.colorBoost * 100);
    elements.boostValue.textContent = settings.colorBoost.toFixed(2);
  }
  
  elements.autoStart.checked = settings.autoStart || false;
  elements.autoConnect.checked = settings.autoConnect !== false;
}

/**
 * Save settings
 */
function saveSettings() {
  chrome.storage.local.set({
    serverUrl: elements.serverUrl.value,
    fps: parseInt(elements.fpsSlider.value),
    colorBoost: parseInt(elements.boostSlider.value) / 100,
    autoStart: elements.autoStart.checked,
    autoConnect: elements.autoConnect.checked
  });
}

/**
 * Get status from content script
 */
async function getContentStatus() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return null;

    return await chrome.tabs.sendMessage(tab.id, { type: 'status' });
  } catch (e) {
    return null;
  }
}

/**
 * Get connection status from background
 */
async function getConnectionStatus() {
  try {
    return await chrome.runtime.sendMessage({ type: 'getConnectionStatus' });
  } catch (e) {
    return { connected: false };
  }
}

/**
 * Send command to content script
 */
async function sendToContent(message) {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return null;

    return await chrome.tabs.sendMessage(tab.id, message);
  } catch (e) {
    console.warn('Could not send to content script:', e);
    return null;
  }
}

/**
 * Toggle sync on/off
 */
async function toggleSync() {
  syncEnabled = !syncEnabled;
  
  if (syncEnabled) {
    await sendToContent({ type: 'start' });
  } else {
    await sendToContent({ type: 'stop' });
  }
  
  updateUI();
}

/**
 * Toggle connection
 */
async function toggleConnection() {
  if (connected) {
    await chrome.runtime.sendMessage({ type: 'disconnect' });
  } else {
    // Update server URL first
    await chrome.runtime.sendMessage({
      type: 'setServerUrl',
      url: elements.serverUrl.value
    });
    await chrome.runtime.sendMessage({ type: 'connect' });
  }
}

/**
 * Refresh status
 */
async function refreshStatus() {
  // Connection status
  const connStatus = await getConnectionStatus();
  connected = connStatus?.connected || false;

  // Content script status
  const contentStatus = await getContentStatus();
  if (contentStatus) {
    syncEnabled = contentStatus.enabled;
    elements.videoStatus.textContent = contentStatus.hasVideo ? 'Yes' : 'No';
    
    if (contentStatus.lastColor) {
      updateColorPreview(contentStatus.lastColor);
    }
  } else {
    elements.videoStatus.textContent = 'N/A';
  }

  updateUI();
}

// Event listeners
elements.toggleBtn.addEventListener('click', toggleSync);
elements.connectBtn.addEventListener('click', toggleConnection);

elements.serverUrl.addEventListener('change', () => {
  saveSettings();
  chrome.runtime.sendMessage({
    type: 'setServerUrl',
    url: elements.serverUrl.value
  });
});

elements.fpsSlider.addEventListener('input', () => {
  const fps = elements.fpsSlider.value;
  elements.fpsValue.textContent = fps;
  saveSettings();
  sendToContent({ type: 'settings', settings: { fps: parseInt(fps) } });
});

elements.boostSlider.addEventListener('input', () => {
  const boost = parseInt(elements.boostSlider.value) / 100;
  elements.boostValue.textContent = boost.toFixed(2);
  saveSettings();
  sendToContent({ type: 'settings', settings: { colorBoost: boost } });
});

elements.autoStart.addEventListener('change', saveSettings);
elements.autoConnect.addEventListener('change', saveSettings);

// Listen for connection status updates from background
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === 'connectionStatus') {
    connected = message.connected;
    updateUI();
  }
});

// Initialize
loadSettings();
refreshStatus();

// Refresh status periodically while popup is open
setInterval(refreshStatus, 1000);
