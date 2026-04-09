package com.wizlight.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channelName = "com.wizlight.app/screen_capture"
    private val requestMediaProjection = 1001

    private var methodChannel: MethodChannel? = null
    private var pendingResult: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        methodChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
        methodChannel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "requestPermission" -> requestScreenCapturePermission(result)
                "startCapture" -> startScreenCapture(
                    bulbsJson = call.argument<String>("bulbsJson") ?: "[]",
                    mode = call.argument<String>("mode") ?: "single",
                    minFps = call.argument<Int>("minFps") ?: 10,
                    maxFps = call.argument<Int>("maxFps") ?: 24,
                    smoothing = call.argument<Double>("smoothing") ?: 0.2,
                    sampleSize = call.argument<Int>("sampleSize") ?: 56,
                    colorBoost = call.argument<Double>("colorBoost") ?: 1.18,
                    minBrightness = call.argument<Int>("minBrightness") ?: 20,
                    minColorDelta = call.argument<Int>("minColorDelta") ?: 8,
                    adaptiveFps = call.argument<Boolean>("adaptiveFps") ?: true,
                    ignoreLetterbox = call.argument<Boolean>("ignoreLetterbox") ?: true,
                    result = result,
                )

                "stopCapture" -> stopScreenCapture(result)
                else -> result.notImplemented()
            }
        }
    }

    private fun requestScreenCapturePermission(result: MethodChannel.Result) {
        pendingResult = result
        val projectionManager =
            getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        startActivityForResult(
            projectionManager.createScreenCaptureIntent(),
            requestMediaProjection,
        )
    }

    private fun startScreenCapture(
        bulbsJson: String,
        mode: String,
        minFps: Int,
        maxFps: Int,
        smoothing: Double,
        sampleSize: Int,
        colorBoost: Double,
        minBrightness: Int,
        minColorDelta: Int,
        adaptiveFps: Boolean,
        ignoreLetterbox: Boolean,
        result: MethodChannel.Result,
    ) {
        if (ScreenCaptureService.hasProjectionPermission().not()) {
            result.success(false)
            return
        }

        val intent = Intent(this, ScreenCaptureService::class.java).apply {
            action = ScreenCaptureService.ACTION_START
            putExtra(ScreenCaptureService.EXTRA_BULBS_JSON, bulbsJson)
            putExtra(ScreenCaptureService.EXTRA_MODE, mode)
            putExtra(ScreenCaptureService.EXTRA_MIN_FPS, minFps)
            putExtra(ScreenCaptureService.EXTRA_MAX_FPS, maxFps)
            putExtra(ScreenCaptureService.EXTRA_SMOOTHING, smoothing)
            putExtra(ScreenCaptureService.EXTRA_SAMPLE_SIZE, sampleSize)
            putExtra(ScreenCaptureService.EXTRA_COLOR_BOOST, colorBoost)
            putExtra(ScreenCaptureService.EXTRA_MIN_BRIGHTNESS, minBrightness)
            putExtra(ScreenCaptureService.EXTRA_MIN_COLOR_DELTA, minColorDelta)
            putExtra(ScreenCaptureService.EXTRA_ADAPTIVE_FPS, adaptiveFps)
            putExtra(ScreenCaptureService.EXTRA_IGNORE_LETTERBOX, ignoreLetterbox)
        }

        ScreenCaptureService.setMethodChannel(methodChannel)
        startForegroundService(intent)
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
        if (requestCode != requestMediaProjection) {
            return
        }

        if (resultCode == Activity.RESULT_OK && data != null) {
            ScreenCaptureService.setMediaProjectionIntent(resultCode, data)
            pendingResult?.success(true)
        } else {
            pendingResult?.success(false)
        }
        pendingResult = null
    }
}
