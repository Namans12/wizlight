package com.wizlight.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val CHANNEL = "com.wizlight.app/screen_capture"
    private val REQUEST_MEDIA_PROJECTION = 1001
    
    private var methodChannel: MethodChannel? = null
    private var pendingResult: MethodChannel.Result? = null
    
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        
        methodChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
        methodChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "requestPermission" -> {
                    requestScreenCapturePermission(result)
                }
                "startCapture" -> {
                    val fps = call.argument<Int>("fps") ?: 12
                    val colorBoost = call.argument<Double>("colorBoost") ?: 1.15
                    val minBrightness = call.argument<Int>("minBrightness") ?: 28
                    startScreenCapture(fps, colorBoost, minBrightness, result)
                }
                "stopCapture" -> {
                    stopScreenCapture(result)
                }
                else -> {
                    result.notImplemented()
                }
            }
        }
    }
    
    private fun requestScreenCapturePermission(result: MethodChannel.Result) {
        pendingResult = result
        val mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        startActivityForResult(
            mediaProjectionManager.createScreenCaptureIntent(),
            REQUEST_MEDIA_PROJECTION
        )
    }
    
    private fun startScreenCapture(fps: Int, colorBoost: Double, minBrightness: Int, result: MethodChannel.Result) {
        // Start the foreground service for screen capture
        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_START
            putExtra(ScreenCaptureService.EXTRA_FPS, fps)
            putExtra(ScreenCaptureService.EXTRA_COLOR_BOOST, colorBoost)
            putExtra(ScreenCaptureService.EXTRA_MIN_BRIGHTNESS, minBrightness)
        }
        
        startForegroundService(intent)
        ScreenCaptureService.setMethodChannel(methodChannel)
        result.success(true)
    }
    
    private fun stopScreenCapture(result: MethodChannel.Result) {
        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_STOP
        }
        stopService(intent)
        result.success(true)
    }
    
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        
        if (requestCode == REQUEST_MEDIA_PROJECTION) {
            if (resultCode == Activity.RESULT_OK && data != null) {
                // Store the permission result for later use
                ScreenCaptureService.setMediaProjectionIntent(resultCode, data)
                pendingResult?.success(true)
            } else {
                pendingResult?.success(false)
            }
            pendingResult = null
        }
    }
}
