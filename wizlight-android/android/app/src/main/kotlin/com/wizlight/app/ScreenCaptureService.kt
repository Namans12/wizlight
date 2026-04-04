package com.wizlight.app

import android.app.*
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.DisplayMetrics
import android.view.WindowManager
import androidx.core.app.NotificationCompat
import io.flutter.plugin.common.MethodChannel
import java.util.Timer
import java.util.TimerTask

class ScreenCaptureService : Service() {
    
    companion object {
        const val ACTION_START = "com.wizlight.app.START_CAPTURE"
        const val ACTION_STOP = "com.wizlight.app.STOP_CAPTURE"
        const val EXTRA_FPS = "fps"
        const val EXTRA_COLOR_BOOST = "colorBoost"
        const val EXTRA_MIN_BRIGHTNESS = "minBrightness"
        
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "wizlight_capture"
        
        private var mediaProjectionResultCode: Int = Activity.RESULT_CANCELED
        private var mediaProjectionData: Intent? = null
        private var methodChannel: MethodChannel? = null
        
        fun setMediaProjectionIntent(resultCode: Int, data: Intent) {
            mediaProjectionResultCode = resultCode
            mediaProjectionData = data
        }
        
        fun setMethodChannel(channel: MethodChannel?) {
            methodChannel = channel
        }
    }
    
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var captureTimer: Timer? = null
    
    private var fps = 12
    private var colorBoost = 1.15
    private var minBrightness = 28
    
    private var screenWidth = 0
    private var screenHeight = 0
    private var screenDensity = 0
    
    private val handler = Handler(Looper.getMainLooper())
    
    override fun onBind(intent: Intent?): IBinder? = null
    
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        
        // Get screen metrics
        val windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        windowManager.defaultDisplay.getRealMetrics(metrics)
        
        screenWidth = metrics.widthPixels / 4  // Capture at 1/4 resolution for performance
        screenHeight = metrics.heightPixels / 4
        screenDensity = metrics.densityDpi
    }
    
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                fps = intent.getIntExtra(EXTRA_FPS, 12)
                colorBoost = intent.getDoubleExtra(EXTRA_COLOR_BOOST, 1.15)
                minBrightness = intent.getIntExtra(EXTRA_MIN_BRIGHTNESS, 28)
                startCapture()
            }
            ACTION_STOP -> {
                stopCapture()
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }
    
    private fun startCapture() {
        startForeground(NOTIFICATION_ID, createNotification())
        
        if (mediaProjectionData == null) {
            stopSelf()
            return
        }
        
        val mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = mediaProjectionManager.getMediaProjection(
            mediaProjectionResultCode,
            mediaProjectionData!!
        )
        
        // Create ImageReader
        imageReader = ImageReader.newInstance(
            screenWidth,
            screenHeight,
            PixelFormat.RGBA_8888,
            2
        )
        
        // Create virtual display
        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "WizLightCapture",
            screenWidth,
            screenHeight,
            screenDensity,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface,
            null,
            handler
        )
        
        // Start capture timer
        val interval = (1000 / fps).toLong()
        captureTimer = Timer()
        captureTimer?.scheduleAtFixedRate(object : TimerTask() {
            override fun run() {
                captureFrame()
            }
        }, 0, interval)
    }
    
    private fun captureFrame() {
        val image = imageReader?.acquireLatestImage() ?: return
        
        try {
            val color = extractDominantColor(image)
            
            // Send color to Flutter
            handler.post {
                methodChannel?.invokeMethod("onColorExtracted", mapOf(
                    "r" to color[0],
                    "g" to color[1],
                    "b" to color[2]
                ))
            }
        } finally {
            image.close()
        }
    }
    
    private fun extractDominantColor(image: Image): IntArray {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        
        val width = image.width
        val height = image.height
        
        var totalR = 0.0
        var totalG = 0.0
        var totalB = 0.0
        var totalWeight = 0.0
        
        // Sample every 4th pixel
        val step = 4
        for (y in 0 until height step step) {
            for (x in 0 until width step step) {
                val offset = y * rowStride + x * pixelStride
                
                if (offset + 3 < buffer.capacity()) {
                    val r = buffer.get(offset).toInt() and 0xFF
                    val g = buffer.get(offset + 1).toInt() and 0xFF
                    val b = buffer.get(offset + 2).toInt() and 0xFF
                    
                    // Calculate saturation weight
                    val max = maxOf(r, g, b) / 255.0
                    val min = minOf(r, g, b) / 255.0
                    val saturation = max - min
                    
                    // Calculate luminance weight
                    val luma = (r * 0.2126 + g * 0.7152 + b * 0.0722) / 255.0
                    
                    val weight = 0.15 + saturation * 2.2 + luma * 0.5
                    
                    totalR += r * weight
                    totalG += g * weight
                    totalB += b * weight
                    totalWeight += weight
                }
            }
        }
        
        if (totalWeight == 0.0) return intArrayOf(128, 128, 128)
        
        var avgR = totalR / totalWeight
        var avgG = totalG / totalWeight
        var avgB = totalB / totalWeight
        
        // Apply color boost
        val mean = (avgR + avgG + avgB) / 3
        avgR = mean + (avgR - mean) * colorBoost
        avgG = mean + (avgG - mean) * colorBoost
        avgB = mean + (avgB - mean) * colorBoost
        
        // Enforce minimum brightness
        val peak = maxOf(avgR, avgG, avgB)
        if (peak < minBrightness && peak > 0) {
            val scale = minBrightness / peak
            avgR *= scale
            avgG *= scale
            avgB *= scale
        }
        
        return intArrayOf(
            avgR.coerceIn(0.0, 255.0).toInt(),
            avgG.coerceIn(0.0, 255.0).toInt(),
            avgB.coerceIn(0.0, 255.0).toInt()
        )
    }
    
    private fun stopCapture() {
        captureTimer?.cancel()
        captureTimer = null
        
        virtualDisplay?.release()
        virtualDisplay = null
        
        imageReader?.close()
        imageReader = null
        
        mediaProjection?.stop()
        mediaProjection = null
    }
    
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Screen Capture",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "WizLight screen capture service"
            }
            
            val notificationManager = getSystemService(NotificationManager::class.java)
            notificationManager.createNotificationChannel(channel)
        }
    }
    
    private fun createNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("WizLight Sync")
            .setContentText("Syncing screen colors with your lights")
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .build()
    }
    
    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }
}
