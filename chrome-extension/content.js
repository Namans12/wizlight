/**
 * WizLight Video Sync - Content Script
 * Detects videos on the page and extracts dominant colors
 */

class VideoColorExtractor {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.ctx = this.canvas.getContext('2d', { willReadFrequently: true });
    this.sampleSize = 32;
    this.isEnabled = false;
    this.fps = 12;
    this.colorBoost = 1.15;
    this.minBrightness = 28;
    this.intervalId = null;
    this.currentVideo = null;
    this.lastColor = null;
    this.minColorDelta = 12;
  }

  /**
   * Find the largest visible video element on the page
   */
  findLargestVideo() {
    const videos = Array.from(document.querySelectorAll('video'));
    if (videos.length === 0) return null;

    let largest = null;
    let maxArea = 0;

    for (const video of videos) {
      // Skip hidden videos
      if (video.offsetWidth === 0 || video.offsetHeight === 0) continue;
      if (getComputedStyle(video).display === 'none') continue;
      if (video.paused && video.currentTime === 0) continue;

      const area = video.offsetWidth * video.offsetHeight;
      if (area > maxArea) {
        maxArea = area;
        largest = video;
      }
    }

    return largest;
  }

  /**
   * Extract dominant color from video frame
   */
  extractColor(video) {
    if (!video || video.readyState < 2) return null;

    const width = this.sampleSize;
    const height = this.sampleSize;

    this.canvas.width = width;
    this.canvas.height = height;

    try {
      // Draw video frame to canvas (scaled down)
      this.ctx.drawImage(video, 0, 0, width, height);
      const imageData = this.ctx.getImageData(0, 0, width, height);
      const pixels = imageData.data;

      // Weighted color extraction
      let totalR = 0, totalG = 0, totalB = 0;
      let totalWeight = 0;

      for (let i = 0; i < pixels.length; i += 4) {
        const r = pixels[i];
        const g = pixels[i + 1];
        const b = pixels[i + 2];

        // Calculate saturation (simple approximation)
        const max = Math.max(r, g, b) / 255;
        const min = Math.min(r, g, b) / 255;
        const saturation = max - min;

        // Calculate luminance
        const luma = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 255;

        // Weight by saturation and luminance
        const weight = 0.15 + saturation * 2.2 + luma * 0.5;

        totalR += r * weight;
        totalG += g * weight;
        totalB += b * weight;
        totalWeight += weight;
      }

      let avgR = Math.round(totalR / totalWeight);
      let avgG = Math.round(totalG / totalWeight);
      let avgB = Math.round(totalB / totalWeight);

      // Apply color boost
      const mean = (avgR + avgG + avgB) / 3;
      avgR = Math.round(mean + (avgR - mean) * this.colorBoost);
      avgG = Math.round(mean + (avgG - mean) * this.colorBoost);
      avgB = Math.round(mean + (avgB - mean) * this.colorBoost);

      // Enforce minimum brightness
      const peak = Math.max(avgR, avgG, avgB);
      if (peak < this.minBrightness && peak > 0) {
        const scale = this.minBrightness / peak;
        avgR = Math.round(avgR * scale);
        avgG = Math.round(avgG * scale);
        avgB = Math.round(avgB * scale);
      }

      // Clamp values
      return {
        r: Math.min(255, Math.max(0, avgR)),
        g: Math.min(255, Math.max(0, avgG)),
        b: Math.min(255, Math.max(0, avgB))
      };
    } catch (e) {
      // CORS or other error
      console.warn('WizLight: Could not extract color from video', e);
      return null;
    }
  }

  /**
   * Calculate color distance for change detection
   */
  colorDistance(a, b) {
    if (!a || !b) return 255;
    return Math.abs(a.r - b.r) + Math.abs(a.g - b.g) + Math.abs(a.b - b.b);
  }

  /**
   * Main sync loop - extract and send colors
   */
  syncLoop() {
    if (!this.isEnabled) return;

    // Find video if not already tracking one
    if (!this.currentVideo || !document.contains(this.currentVideo)) {
      this.currentVideo = this.findLargestVideo();
    }

    if (!this.currentVideo) {
      // No video found, check again next interval
      return;
    }

    const color = this.extractColor(this.currentVideo);
    if (!color) return;

    // Only send if color changed significantly
    if (this.colorDistance(color, this.lastColor) >= this.minColorDelta) {
      this.lastColor = color;
      
      // Send to background script
      chrome.runtime.sendMessage({
        type: 'color',
        color: color
      });
    }
  }

  /**
   * Start color extraction
   */
  start() {
    if (this.isEnabled) return;
    
    this.isEnabled = true;
    this.currentVideo = this.findLargestVideo();
    
    const interval = Math.round(1000 / this.fps);
    this.intervalId = setInterval(() => this.syncLoop(), interval);
    
    console.log('WizLight: Video sync started');
  }

  /**
   * Stop color extraction
   */
  stop() {
    this.isEnabled = false;
    
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
    }
    
    this.currentVideo = null;
    this.lastColor = null;
    
    console.log('WizLight: Video sync stopped');
  }

  /**
   * Update settings
   */
  updateSettings(settings) {
    if (settings.fps !== undefined) {
      this.fps = Math.max(4, Math.min(30, settings.fps));
      
      // Restart interval with new FPS if running
      if (this.isEnabled) {
        this.stop();
        this.start();
      }
    }
    
    if (settings.colorBoost !== undefined) {
      this.colorBoost = Math.max(1.0, Math.min(2.0, settings.colorBoost));
    }
    
    if (settings.minBrightness !== undefined) {
      this.minBrightness = Math.max(0, Math.min(255, settings.minBrightness));
    }
    
    if (settings.sampleSize !== undefined) {
      this.sampleSize = Math.max(16, Math.min(64, settings.sampleSize));
    }
  }

  /**
   * Get current status
   */
  getStatus() {
    return {
      enabled: this.isEnabled,
      hasVideo: this.currentVideo !== null,
      fps: this.fps,
      lastColor: this.lastColor
    };
  }
}

// Initialize extractor
const extractor = new VideoColorExtractor();

// Listen for messages from popup/background
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'start':
      extractor.start();
      sendResponse({ success: true });
      break;
      
    case 'stop':
      extractor.stop();
      sendResponse({ success: true });
      break;
      
    case 'status':
      sendResponse(extractor.getStatus());
      break;
      
    case 'settings':
      extractor.updateSettings(message.settings);
      sendResponse({ success: true });
      break;
      
    case 'ping':
      sendResponse({ pong: true });
      break;
  }
  
  return true; // Keep channel open for async response
});

// Auto-start if previously enabled
chrome.storage.local.get(['autoStart'], (result) => {
  if (result.autoStart) {
    // Wait a bit for video to load
    setTimeout(() => {
      if (extractor.findLargestVideo()) {
        extractor.start();
      }
    }, 2000);
  }
});

console.log('WizLight: Content script loaded');
