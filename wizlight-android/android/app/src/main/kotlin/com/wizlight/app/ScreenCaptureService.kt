package com.wizlight.app

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.WindowManager
import androidx.core.app.NotificationCompat
import io.flutter.plugin.common.MethodChannel
import org.json.JSONArray
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.nio.charset.StandardCharsets
import java.util.ArrayDeque
import kotlin.math.abs
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

class ScreenCaptureService : Service() {

    companion object {
        const val ACTION_START = "com.wizlight.app.START_CAPTURE"
        const val ACTION_STOP = "com.wizlight.app.STOP_CAPTURE"

        const val EXTRA_BULBS_JSON = "bulbsJson"
        const val EXTRA_MODE = "mode"
        const val EXTRA_MIN_FPS = "minFps"
        const val EXTRA_MAX_FPS = "maxFps"
        const val EXTRA_SMOOTHING = "smoothing"
        const val EXTRA_SAMPLE_SIZE = "sampleSize"
        const val EXTRA_COLOR_BOOST = "colorBoost"
        const val EXTRA_EDGE_WEIGHT = "edgeWeight"
        const val EXTRA_COLOR_ALGORITHM = "colorAlgorithm"
        const val EXTRA_MIN_BRIGHTNESS = "minBrightness"
        const val EXTRA_MIN_COLOR_DELTA = "minColorDelta"
        const val EXTRA_ADAPTIVE_FPS = "adaptiveFps"
        const val EXTRA_PREDICTIVE_SMOOTHING = "predictiveSmoothing"
        const val EXTRA_IGNORE_LETTERBOX = "ignoreLetterbox"

        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "wizlight_capture"
        private const val WIZ_PORT = 38899

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

        fun hasProjectionPermission(): Boolean {
            return mediaProjectionResultCode == Activity.RESULT_OK && mediaProjectionData != null
        }
    }

    private data class BulbTarget(
        val ip: String,
        val region: String,
    )

    private data class RgbColor(
        val r: Int,
        val g: Int,
        val b: Int,
    )

    private data class WizPilotColor(
        val rgb: RgbColor,
        val warmWhite: Int,
    )

    private data class SampledFrame(
        val pixels: IntArray,
        val width: Int,
        val height: Int,
    )

    private data class ImageFrame(
        val buffer: java.nio.ByteBuffer,
        val sourceWidth: Int,
        val sourceHeight: Int,
        val rowStride: Int,
        val pixelStride: Int,
        val left: Int,
        val top: Int,
        val width: Int,
        val height: Int,
    )

    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var udpSocket: DatagramSocket? = null
    private var captureThread: HandlerThread? = null
    private var captureHandler: Handler? = null

    private var screenWidth = 0
    private var screenHeight = 0
    private var screenDensity = 0

    private var bulbTargets: List<BulbTarget> = emptyList()
    private var requestedMode = "single"
    private var effectiveMode = "single"
    private var adaptiveFps = true
    private var ignoreLetterbox = true
    private var minFps = 10
    private var maxFps = 24
    private var currentFps = 24
    private var sampleSize = 56
    private var smoothing = 0.2
    private var colorBoost = 1.18
    private var colorAlgorithm = "auto"
    private var minBrightness = 20
    private var minColorDelta = 8
    private var edgeWeight = 1.5
    private var predictiveSmoothing = true
    private var predictionWeight = 0.3

    private var isRunning = false
    private var previousMotionSample: FloatArray? = null
    private var previousSingleColor: RgbColor? = null
    private var lastMotionScore = 0.0
    private var lastError: String? = null
    private var lastStatusUpdateAt = 0L
    private var lastSendAt: Long? = null
    private var cachedContentBounds: Rect? = null
    private var cachedContentBoundsAtFrame = -1
    private val sendIntervalsMs = ArrayDeque<Long>()
    private var updatesSent = 0
    private var framesProcessed = 0
    private val currentOutputColors = mutableMapOf<String, RgbColor>()
    private val lastSentColors = mutableMapOf<String, RgbColor>()
    private val lastTargetColors = mutableMapOf<String, RgbColor>()
    private val predictionTargets = mutableMapOf<String, RgbColor>()
    private val wizBasis = arrayOf(
        doubleArrayOf(cos(0.0), sin(0.0)),
        doubleArrayOf(cos((2.0 * PI) / 3.0), sin((2.0 * PI) / 3.0)),
        doubleArrayOf(cos((4.0 * PI) / 3.0), sin((4.0 * PI) / 3.0)),
    )

    private val mainHandler = Handler(Looper.getMainLooper())
    private val mediaProjectionCallback =
        object : MediaProjection.Callback() {
            override fun onStop() {
                stopCapture(stopProjection = false)
                stopSelf()
            }
        }

    private val captureRunnable = object : Runnable {
        override fun run() {
            if (!isRunning) {
                return
            }

            val startedAt = SystemClock.elapsedRealtime()
            captureFrame()
            val elapsed = SystemClock.elapsedRealtime() - startedAt
            val frameDelay = max(5L, (1000L / max(currentFps, 1)) - elapsed)
            captureHandler?.postDelayed(this, frameDelay)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()

        val windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        windowManager.defaultDisplay.getRealMetrics(metrics)

        screenWidth = max(360, metrics.widthPixels / 4)
        screenHeight = max(240, metrics.heightPixels / 4)
        screenDensity = metrics.densityDpi
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                bulbTargets = parseBulbs(intent.getStringExtra(EXTRA_BULBS_JSON) ?: "[]")
                requestedMode = intent.getStringExtra(EXTRA_MODE) ?: "single"
                minFps = intent.getIntExtra(EXTRA_MIN_FPS, 10).coerceIn(4, 30)
                maxFps = intent.getIntExtra(EXTRA_MAX_FPS, 24).coerceIn(minFps, 30)
                currentFps = maxFps
                smoothing = intent.getDoubleExtra(EXTRA_SMOOTHING, 0.2).coerceIn(0.05, 0.85)
                sampleSize = intent.getIntExtra(EXTRA_SAMPLE_SIZE, 56).coerceIn(24, 96)
                colorBoost = intent.getDoubleExtra(EXTRA_COLOR_BOOST, 1.18).coerceIn(1.0, 1.8)
                edgeWeight = intent.getDoubleExtra(EXTRA_EDGE_WEIGHT, 1.5).coerceIn(1.0, 2.5)
                colorAlgorithm = normalizeAlgorithm(intent.getStringExtra(EXTRA_COLOR_ALGORITHM))
                minBrightness = intent.getIntExtra(EXTRA_MIN_BRIGHTNESS, 20).coerceIn(0, 80)
                minColorDelta = intent.getIntExtra(EXTRA_MIN_COLOR_DELTA, 8).coerceIn(2, 48)
                adaptiveFps = intent.getBooleanExtra(EXTRA_ADAPTIVE_FPS, true)
                predictiveSmoothing = intent.getBooleanExtra(EXTRA_PREDICTIVE_SMOOTHING, true)
                ignoreLetterbox = intent.getBooleanExtra(EXTRA_IGNORE_LETTERBOX, true)
                effectiveMode = resolveEffectiveMode(requestedMode, bulbTargets)
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
        if (isRunning) {
            return
        }

        val projectionData = mediaProjectionData ?: run {
            stopSelf()
            return
        }

        startForeground(NOTIFICATION_ID, createNotification())

        val projectionManager =
            getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        mediaProjection = projectionManager.getMediaProjection(
            mediaProjectionResultCode,
            projectionData,
        )
        mediaProjection?.registerCallback(mediaProjectionCallback, mainHandler)

        imageReader = ImageReader.newInstance(
            screenWidth,
            screenHeight,
            PixelFormat.RGBA_8888,
            2,
        )

        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "WizLightCapture",
            screenWidth,
            screenHeight,
            screenDensity,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface,
            null,
            mainHandler,
        )

        udpSocket = DatagramSocket()
        previousMotionSample = null
        previousSingleColor = null
        lastMotionScore = 0.0
        lastError = null
        lastStatusUpdateAt = 0L
        lastSendAt = null
        cachedContentBounds = null
        cachedContentBoundsAtFrame = -1
        sendIntervalsMs.clear()
        updatesSent = 0
        framesProcessed = 0
        currentOutputColors.clear()
        lastSentColors.clear()
        lastTargetColors.clear()
        predictionTargets.clear()

        captureThread = HandlerThread("WizLightCaptureThread").also { it.start() }
        captureHandler = Handler(captureThread!!.looper)
        isRunning = true
        captureHandler?.post(captureRunnable)
    }

    private fun captureFrame() {
        val image = imageReader?.acquireLatestImage() ?: return

        try {
            val frame = imageToFrame(image) ?: return
            val contentFrame = cropContentArea(frame, ignoreLetterbox)

            val motionScore = computeMotionScore(contentFrame)
            lastMotionScore = motionScore
            updateAdaptiveFps(motionScore)

            val targetColors = extractTargetColors(contentFrame, motionScore)
            lastTargetColors.clear()
            lastTargetColors.putAll(targetColors)

            val smoothingFactor = targetSmoothing(motionScore)
            val mappedBulbColors = buildBulbColorMap(targetColors)
            val appliedColors = mutableMapOf<String, RgbColor>()
            var sentAnyUpdate = false

            for ((ip, targetColor) in mappedBulbColors) {
                val current = currentOutputColors[ip]
                val predictedTarget = applyPredictiveTarget(ip, targetColor, motionScore)
                val smoothed = if (current == null) {
                    predictedTarget
                } else {
                    smoothColor(current, predictedTarget, smoothingFactor)
                }
                currentOutputColors[ip] = smoothed
                appliedColors[ip] = smoothed

                val lastSent = lastSentColors[ip]
                if (lastSent == null || colorDistance(smoothed, lastSent) >= minColorDelta) {
                    sendColor(ip, smoothed)
                    lastSentColors[ip] = smoothed
                    sentAnyUpdate = true
                }
            }

            val staleIps = currentOutputColors.keys - mappedBulbColors.keys
            for (ip in staleIps) {
                currentOutputColors.remove(ip)
                lastSentColors.remove(ip)
                predictionTargets.remove(ip)
            }

            framesProcessed += 1
            if (sentAnyUpdate) {
                val now = SystemClock.elapsedRealtime()
                lastSendAt?.let {
                    sendIntervalsMs.addLast(now - it)
                    while (sendIntervalsMs.size > 30) {
                        sendIntervalsMs.removeFirst()
                    }
                }
                lastSendAt = now
                updatesSent += 1
                lastError = null
            }

            maybePostStatus(appliedColors, smoothingFactor)
        } catch (error: Exception) {
            lastError = error.message ?: error.toString()
            maybePostStatus(currentOutputColors.toMap(), smoothing)
        } finally {
            image.close()
        }
    }

    private fun parseBulbs(rawJson: String): List<BulbTarget> {
        val bulbs = mutableListOf<BulbTarget>()
        val array = JSONArray(rawJson)
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            val ip = item.optString("ip")
            if (ip.isBlank()) {
                continue
            }
            val region = if (item.optString("region") == "right") "right" else "left"
            bulbs.add(BulbTarget(ip = ip, region = region))
        }
        return bulbs.take(2)
    }

    private fun resolveEffectiveMode(mode: String, bulbs: List<BulbTarget>): String {
        if (mode != "zones") {
            return "single"
        }
        val regions = bulbs.map { it.region }.toSet()
        return if (regions.size >= 2 && bulbs.size >= 2) "zones" else "single"
    }

    private fun imageToFrame(image: Image): ImageFrame? {
        val plane = image.planes.firstOrNull() ?: return null
        return ImageFrame(
            buffer = plane.buffer.duplicate(),
            sourceWidth = image.width,
            sourceHeight = image.height,
            rowStride = plane.rowStride,
            pixelStride = plane.pixelStride,
            left = 0,
            top = 0,
            width = image.width,
            height = image.height,
        )
    }

    private fun cropContentArea(source: ImageFrame, shouldCrop: Boolean): ImageFrame {
        if (!shouldCrop) {
            return source
        }

        val shouldRefreshBounds =
            cachedContentBounds == null || framesProcessed - cachedContentBoundsAtFrame >= 12
        val bounds = if (shouldRefreshBounds) {
            detectContentBounds(source).also {
                cachedContentBounds = it
                cachedContentBoundsAtFrame = framesProcessed
            }
        } else {
            cachedContentBounds ?: detectContentBounds(source)
        }
        return if (
            bounds.left == source.left &&
            bounds.top == source.top &&
            bounds.width() == source.width &&
            bounds.height() == source.height
        ) {
            source
        } else {
            source.copy(
                left = bounds.left,
                top = bounds.top,
                width = bounds.width(),
                height = bounds.height(),
            )
        }
    }

    private fun detectContentBounds(source: ImageFrame): Rect {
        val sample = sampleFrame(source, 64)
        val width = sample.width
        val height = sample.height

        val rowActivity = DoubleArray(height)
        val colActivity = DoubleArray(width)
        for (y in 0 until height) {
            var rowActive = 0
            for (x in 0 until width) {
                val luma = colorLuma(sample.pixels[y * width + x])
                if (luma * 255.0 > 16.0) {
                    rowActive += 1
                    colActivity[x] += 1.0
                }
            }
            rowActivity[y] = rowActive.toDouble() / width.toDouble()
        }

        val activeRows = rowActivity.indices.filter { rowActivity[it] > 0.02 }
        val activeCols = colActivity.indices.filter { colActivity[it] / height.toDouble() > 0.02 }
        if (activeRows.isEmpty() || activeCols.isEmpty()) {
            return Rect(source.left, source.top, source.left + source.width, source.top + source.height)
        }

        val top = activeRows.first()
        val bottom = activeRows.last() + 1
        val left = activeCols.first()
        val right = activeCols.last() + 1

        if ((bottom - top) < height * 0.45 || (right - left) < width * 0.45) {
            return Rect(source.left, source.top, source.left + source.width, source.top + source.height)
        }

        val mappedLeft = source.left + (left * source.width.toDouble() / width.toDouble()).roundToInt()
        val mappedTop = source.top + (top * source.height.toDouble() / height.toDouble()).roundToInt()
        val mappedRight =
            source.left + (right * source.width.toDouble() / width.toDouble()).roundToInt()
        val mappedBottom =
            source.top + (bottom * source.height.toDouble() / height.toDouble()).roundToInt()
        return Rect(
            mappedLeft.coerceIn(source.left, source.left + source.width - 1),
            mappedTop.coerceIn(source.top, source.top + source.height - 1),
            mappedRight.coerceIn(source.left + 1, source.left + source.width),
            mappedBottom.coerceIn(source.top + 1, source.top + source.height),
        )
    }

    private fun computeMotionScore(source: ImageFrame): Double {
        val sample = sampleFrame(source, 20)
        val current = FloatArray(sample.pixels.size)
        for (index in sample.pixels.indices) {
            current[index] = colorLuma(sample.pixels[index]).toFloat()
        }

        val previous = previousMotionSample
        previousMotionSample = current
        if (previous == null || previous.size != current.size) {
            return 0.0
        }

        var total = 0.0
        for (index in current.indices) {
            total += abs(current[index] - previous[index])
        }
        return min(1.0, total / current.size.toDouble())
    }

    private fun extractTargetColors(source: ImageFrame, motionScore: Double): Map<String, RgbColor> {
        return if (effectiveMode == "single") {
            val sampled = sampleFrame(source, sampleSize)
            val extracted = if (colorAlgorithm == "auto") {
                extractCinematicSingleColor(sampled)
            } else {
                extractColor(sampled)
            }
            val enhanced = enhanceColor(extracted)
            val held = applyCinematicPaletteHold(enhanced, previousSingleColor, motionScore)
            previousSingleColor = held
            mapOf("all" to held)
        } else {
            previousSingleColor = null
            val targets = mutableMapOf<String, RgbColor>()
            for (region in bulbTargets.map { it.region }.toSet()) {
                val regionFrame = cropRelativeRegion(source, region)
                val sampled = sampleFrame(regionFrame, sampleSize)
                val color = enhanceColor(extractColor(sampled))
                targets[region] = color
            }
            targets
        }
    }

    private fun normalizeAlgorithm(raw: String?): String {
        return when (raw?.lowercase()) {
            "auto", "weighted", "kmeans", "histogram" -> raw.lowercase()
            else -> "auto"
        }
    }

    private fun extractColor(sample: SampledFrame): RgbColor {
        return when (colorAlgorithm) {
            "weighted" -> extractWeightedColor(sample)
            "histogram" -> extractHistogramColor(sample)
            "kmeans" -> extractKMeansColor(sample)
            else -> extractBalancedAutoColor(sample)
        }
    }

    private fun extractHistogramColor(sample: SampledFrame): RgbColor {
        val binSize = 32
        val bins = HashMap<Int, Int>()
        val sums = HashMap<Int, IntArray>()
        for (pixel in sample.pixels) {
            val r = Color.red(pixel)
            val g = Color.green(pixel)
            val b = Color.blue(pixel)
            val qr = (r / binSize).coerceIn(0, 7)
            val qg = (g / binSize).coerceIn(0, 7)
            val qb = (b / binSize).coerceIn(0, 7)
            val key = (qr shl 6) or (qg shl 3) or qb
            bins[key] = (bins[key] ?: 0) + 1
            val sum = sums.getOrPut(key) { intArrayOf(0, 0, 0) }
            sum[0] += r
            sum[1] += g
            sum[2] += b
        }

        val dominant = bins.maxByOrNull { it.value }?.key
        if (dominant == null) {
            return extractWeightedColor(sample)
        }
        val total = max(1, bins[dominant] ?: 1)
        val sum = sums[dominant] ?: intArrayOf(128, 128, 128)
        return RgbColor(sum[0] / total, sum[1] / total, sum[2] / total)
    }

    private fun extractKMeansColor(sample: SampledFrame): RgbColor {
        if (sample.pixels.isEmpty()) {
            return RgbColor(128, 128, 128)
        }

        val clusterCount = 3
        val centroids = Array(clusterCount) { index ->
            val pixel = sample.pixels[(index * sample.pixels.size / clusterCount).coerceIn(0, sample.pixels.size - 1)]
            floatArrayOf(Color.red(pixel).toFloat(), Color.green(pixel).toFloat(), Color.blue(pixel).toFloat())
        }

        repeat(5) {
            val sums = Array(clusterCount) { floatArrayOf(0f, 0f, 0f) }
            val counts = IntArray(clusterCount)

            for (pixel in sample.pixels) {
                val r = Color.red(pixel).toFloat()
                val g = Color.green(pixel).toFloat()
                val b = Color.blue(pixel).toFloat()

                var bestIndex = 0
                var bestDistance = Double.MAX_VALUE
                for (index in centroids.indices) {
                    val dr = r - centroids[index][0]
                    val dg = g - centroids[index][1]
                    val db = b - centroids[index][2]
                    val distance = (dr * dr + dg * dg + db * db).toDouble()
                    if (distance < bestDistance) {
                        bestDistance = distance
                        bestIndex = index
                    }
                }

                sums[bestIndex][0] += r
                sums[bestIndex][1] += g
                sums[bestIndex][2] += b
                counts[bestIndex] += 1
            }

            for (index in centroids.indices) {
                if (counts[index] == 0) {
                    continue
                }
                centroids[index][0] = sums[index][0] / counts[index]
                centroids[index][1] = sums[index][1] / counts[index]
                centroids[index][2] = sums[index][2] / counts[index]
            }
        }

        var dominantIndex = 0
        var dominantScore = -1.0
        for (index in centroids.indices) {
            val saturation = rgbSaturation(
                centroids[index][0].roundToInt(),
                centroids[index][1].roundToInt(),
                centroids[index][2].roundToInt(),
            )
            val luma = rgbLuma(
                centroids[index][0].roundToInt(),
                centroids[index][1].roundToInt(),
                centroids[index][2].roundToInt(),
            )
            val score = saturation * 0.75 + luma * 0.25
            if (score > dominantScore) {
                dominantScore = score
                dominantIndex = index
            }
        }

        val chosen = centroids[dominantIndex]
        return RgbColor(
            chosen[0].roundToInt().coerceIn(0, 255),
            chosen[1].roundToInt().coerceIn(0, 255),
            chosen[2].roundToInt().coerceIn(0, 255),
        )
    }

    private fun applyPredictiveTarget(ip: String, target: RgbColor, motionScore: Double): RgbColor {
        if (!predictiveSmoothing) {
            predictionTargets[ip] = target
            return target
        }

        val previous = predictionTargets[ip]
        predictionTargets[ip] = target
        if (previous == null) {
            return target
        }

        val normalizedMotion = min(1.0, motionScore / 0.05)
        val weight = predictionWeight * (1.0 - normalizedMotion)
        if (weight <= 0.0) {
            return target
        }

        val predicted = RgbColor(
            (target.r + (target.r - previous.r) * weight).roundToInt().coerceIn(0, 255),
            (target.g + (target.g - previous.g) * weight).roundToInt().coerceIn(0, 255),
            (target.b + (target.b - previous.b) * weight).roundToInt().coerceIn(0, 255),
        )
        return blendColors(target, predicted, weight)
    }

    private fun cropRelativeRegion(source: ImageFrame, region: String): ImageFrame {
        val rect = when (region) {
            "right" -> Rect(
                source.left + (source.width * 0.78).roundToInt(),
                source.top + (source.height * 0.08).roundToInt(),
                source.left + source.width,
                source.top + (source.height * 0.92).roundToInt(),
            )

            else -> Rect(
                source.left,
                source.top + (source.height * 0.08).roundToInt(),
                source.left + (source.width * 0.22).roundToInt().coerceAtLeast(1),
                source.top + (source.height * 0.92).roundToInt(),
            )
        }

        val left = rect.left.coerceIn(source.left, source.left + source.width - 1)
        val top = rect.top.coerceIn(source.top, source.top + source.height - 1)
        val right = rect.right.coerceIn(left + 1, source.left + source.width)
        val bottom = rect.bottom.coerceIn(top + 1, source.top + source.height)
        return source.copy(
            left = left,
            top = top,
            width = right - left,
            height = bottom - top,
        )
    }

    private fun sampleFrame(source: ImageFrame, size: Int): SampledFrame {
        val pixels = IntArray(size * size)
        val maxX = max(1, source.width - 1)
        val maxY = max(1, source.height - 1)
        for (y in 0 until size) {
            val sourceY = ((y * maxY).toDouble() / max(1, size - 1)).roundToInt()
            for (x in 0 until size) {
                val sourceX = ((x * maxX).toDouble() / max(1, size - 1)).roundToInt()
                pixels[y * size + x] = pixelAt(source, sourceX, sourceY)
            }
        }
        return SampledFrame(pixels = pixels, width = size, height = size)
    }

    private fun pixelAt(source: ImageFrame, x: Int, y: Int): Int {
        val absoluteX = (source.left + x).coerceIn(0, source.sourceWidth - 1)
        val absoluteY = (source.top + y).coerceIn(0, source.sourceHeight - 1)
        val offset = absoluteY * source.rowStride + absoluteX * source.pixelStride
        val buffer = source.buffer
        val red = buffer.get(offset).toInt() and 0xFF
        val green = buffer.get(offset + 1).toInt() and 0xFF
        val blue = buffer.get(offset + 2).toInt() and 0xFF
        val alpha =
            if (offset + 3 < buffer.limit()) buffer.get(offset + 3).toInt() and 0xFF else 0xFF
        return Color.argb(alpha, red, green, blue)
    }

    private fun extractBalancedAutoColor(sample: SampledFrame): RgbColor {
        val weighted = extractWeightedColor(sample)
        val accent = extractVibrantAccent(sample)
        val saturationMean = averageSaturation(sample)
        val vividRatio = vividRatio(sample, saturationThreshold = 0.35, lumaThreshold = 0.08)
        val sceneEnergy = min(1.0, vividRatio * 2.2 + saturationMean * 1.8)

        var accentWeight = 0.10 + sceneEnergy * 0.25
        if (sceneEnergy < 0.35) {
            accentWeight *= 0.5
        }
        return blendColors(weighted, accent, accentWeight)
    }

    private fun extractCinematicSingleColor(sample: SampledFrame): RgbColor {
        val weighted = extractWeightedColor(sample)
        val accent = extractVibrantAccent(sample)
        val palette = extractPaletteAnchor(sample)
        val saturationMean = averageSaturation(sample)
        val vividRatio = vividRatio(sample, saturationThreshold = 0.32, lumaThreshold = 0.08)
        val averageLuma = averageLuma(sample)

        if (averageLuma < 0.03 && saturationMean < 0.04) {
            return weighted
        }

        var accentWeight = 0.18 + min(0.34, saturationMean * 0.55 + vividRatio * 0.22)
        var paletteWeight =
            0.10 + min(0.26, vividRatio * 0.35 + max(0.0, saturationMean - 0.12) * 0.5)

        if (saturationMean < 0.12) {
            paletteWeight *= 0.5
        }
        if (saturationMean < 0.08 && averageLuma > 0.18) {
            accentWeight *= 0.75
        }

        return blendColors(
            blendColors(weighted, accent, accentWeight),
            palette,
            paletteWeight,
        )
    }

    private fun extractWeightedColor(sample: SampledFrame): RgbColor {
        val border = max(1, min(sample.width, sample.height) / 6)
        var totalR = 0.0
        var totalG = 0.0
        var totalB = 0.0
        var totalWeight = 0.0

        for (y in 0 until sample.height) {
            for (x in 0 until sample.width) {
                val pixel = sample.pixels[y * sample.width + x]
                val r = Color.red(pixel)
                val g = Color.green(pixel)
                val b = Color.blue(pixel)
                val saturation = rgbSaturation(r, g, b)
                val luma = rgbLuma(r, g, b)
                var weight = 0.15 + saturation * 2.2 + luma * 0.5
                if (
                    x < border || y < border ||
                    x >= sample.width - border || y >= sample.height - border
                ) {
                    weight *= edgeWeight
                }
                totalR += r * weight
                totalG += g * weight
                totalB += b * weight
                totalWeight += weight
            }
        }

        if (totalWeight <= 0.0) {
            return RgbColor(128, 128, 128)
        }
        return RgbColor(
            (totalR / totalWeight).roundToInt().coerceIn(0, 255),
            (totalG / totalWeight).roundToInt().coerceIn(0, 255),
            (totalB / totalWeight).roundToInt().coerceIn(0, 255),
        )
    }

    private fun extractVibrantAccent(sample: SampledFrame): RgbColor {
        val energies = DoubleArray(sample.pixels.size)
        for (index in sample.pixels.indices) {
            val pixel = sample.pixels[index]
            val saturation = rgbSaturation(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
            val luma = colorLuma(pixel)
            energies[index] = saturation * 0.8 + luma * 0.2
        }
        val sorted = energies.sorted()
        val thresholdIndex = max(0, (sorted.size * 0.8).toInt() - 1)
        val threshold = sorted[thresholdIndex]

        var totalR = 0.0
        var totalG = 0.0
        var totalB = 0.0
        var count = 0
        for (index in sample.pixels.indices) {
            if (energies[index] < threshold) {
                continue
            }
            val pixel = sample.pixels[index]
            totalR += Color.red(pixel)
            totalG += Color.green(pixel)
            totalB += Color.blue(pixel)
            count += 1
        }

        if (count == 0) {
            return extractWeightedColor(sample)
        }
        return RgbColor(
            (totalR / count).roundToInt().coerceIn(0, 255),
            (totalG / count).roundToInt().coerceIn(0, 255),
            (totalB / count).roundToInt().coerceIn(0, 255),
        )
    }

    private fun extractPaletteAnchor(sample: SampledFrame): RgbColor {
        val energies = DoubleArray(sample.pixels.size)
        for (index in sample.pixels.indices) {
            val pixel = sample.pixels[index]
            val saturation = rgbSaturation(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
            val luma = colorLuma(pixel)
            energies[index] = saturation * 0.9 + max(0.0, 0.65 - abs(luma - 0.42)) * 0.1
        }
        val sorted = energies.sorted()
        val thresholdIndex = max(0, (sorted.size * 0.9).toInt() - 1)
        val threshold = sorted[thresholdIndex]

        var totalR = 0.0
        var totalG = 0.0
        var totalB = 0.0
        var count = 0
        for (index in sample.pixels.indices) {
            val pixel = sample.pixels[index]
            if (energies[index] < threshold || colorLuma(pixel) <= 0.05) {
                continue
            }
            totalR += Color.red(pixel)
            totalG += Color.green(pixel)
            totalB += Color.blue(pixel)
            count += 1
        }

        if (count == 0) {
            return extractVibrantAccent(sample)
        }
        return RgbColor(
            (totalR / count).roundToInt().coerceIn(0, 255),
            (totalG / count).roundToInt().coerceIn(0, 255),
            (totalB / count).roundToInt().coerceIn(0, 255),
        )
    }

    private fun averageSaturation(sample: SampledFrame): Double {
        var total = 0.0
        for (pixel in sample.pixels) {
            total += rgbSaturation(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
        }
        return total / sample.pixels.size.toDouble()
    }

    private fun averageLuma(sample: SampledFrame): Double {
        var total = 0.0
        for (pixel in sample.pixels) {
            total += colorLuma(pixel)
        }
        return total / sample.pixels.size.toDouble()
    }

    private fun vividRatio(
        sample: SampledFrame,
        saturationThreshold: Double,
        lumaThreshold: Double,
    ): Double {
        var count = 0
        for (pixel in sample.pixels) {
            val r = Color.red(pixel)
            val g = Color.green(pixel)
            val b = Color.blue(pixel)
            if (rgbSaturation(r, g, b) > saturationThreshold && rgbLuma(r, g, b) > lumaThreshold) {
                count += 1
            }
        }
        return count.toDouble() / sample.pixels.size.toDouble()
    }

    private fun buildBulbColorMap(targetColors: Map<String, RgbColor>): Map<String, RgbColor> {
        if (bulbTargets.isEmpty() || targetColors.isEmpty()) {
            return emptyMap()
        }

        if (effectiveMode != "zones" || targetColors.containsKey("all")) {
            val single = targetColors["all"] ?: averageColors(targetColors.values.toList())
            return bulbTargets.associate { it.ip to single }
        }

        val fallback = averageColors(targetColors.values.toList())
        return bulbTargets.associate { bulb ->
            bulb.ip to (targetColors[bulb.region] ?: fallback)
        }
    }

    private fun blendColors(base: RgbColor, accent: RgbColor, weight: Double): RgbColor {
        val accentWeight = weight.coerceIn(0.0, 1.0)
        val baseWeight = 1.0 - accentWeight
        return RgbColor(
            (base.r * baseWeight + accent.r * accentWeight).roundToInt().coerceIn(0, 255),
            (base.g * baseWeight + accent.g * accentWeight).roundToInt().coerceIn(0, 255),
            (base.b * baseWeight + accent.b * accentWeight).roundToInt().coerceIn(0, 255),
        )
    }

    private fun enhanceColor(color: RgbColor): RgbColor {
        val mean = (color.r + color.g + color.b) / 3.0
        var red = mean + (color.r - mean) * colorBoost
        var green = mean + (color.g - mean) * colorBoost
        var blue = mean + (color.b - mean) * colorBoost

        val peak = max(1.0, max(red, max(green, blue)))
        if (peak < minBrightness) {
            val scale = minBrightness / peak
            red *= scale
            green *= scale
            blue *= scale
        }

        return RgbColor(
            red.roundToInt().coerceIn(0, 255),
            green.roundToInt().coerceIn(0, 255),
            blue.roundToInt().coerceIn(0, 255),
        )
    }

    private fun applyCinematicPaletteHold(
        target: RgbColor,
        previous: RgbColor?,
        motionScore: Double,
    ): RgbColor {
        previous ?: return target

        val targetSaturation = colorSaturation(target)
        val previousSaturation = colorSaturation(previous)
        val targetLuma = colorLuma(target)

        if (targetLuma < 0.035) {
            return target
        }
        if (motionScore > 0.045) {
            return target
        }
        if (previousSaturation < 0.12) {
            return target
        }
        if (targetSaturation >= previousSaturation * 0.92) {
            return target
        }

        var holdStrength = min(0.34, max(0.0, (previousSaturation - targetSaturation) * 0.85))
        holdStrength *= max(0.0, 1.0 - min(1.0, motionScore / 0.03))

        if (targetSaturation < 0.12 && targetLuma < 0.55) {
            holdStrength = min(0.4, holdStrength + 0.08)
        }

        if (holdStrength <= 0.01) {
            return target
        }
        return blendColors(target, previous, holdStrength)
    }

    private fun smoothColor(current: RgbColor, target: RgbColor, factor: Double): RgbColor {
        return RgbColor(
            (current.r + (target.r - current.r) * factor).roundToInt().coerceIn(0, 255),
            (current.g + (target.g - current.g) * factor).roundToInt().coerceIn(0, 255),
            (current.b + (target.b - current.b) * factor).roundToInt().coerceIn(0, 255),
        )
    }

    private fun averageColors(colors: List<RgbColor>): RgbColor {
        if (colors.isEmpty()) {
            return RgbColor(128, 128, 128)
        }
        var totalR = 0
        var totalG = 0
        var totalB = 0
        for (color in colors) {
            totalR += color.r
            totalG += color.g
            totalB += color.b
        }
        return RgbColor(
            totalR / colors.size,
            totalG / colors.size,
            totalB / colors.size,
        )
    }

    private fun colorDistance(first: RgbColor, second: RgbColor): Int {
        return abs(first.r - second.r) + abs(first.g - second.g) + abs(first.b - second.b)
    }

    private fun colorSaturation(color: RgbColor): Double {
        return rgbSaturation(color.r, color.g, color.b)
    }

    private fun colorLuma(color: RgbColor): Double {
        return rgbLuma(color.r, color.g, color.b)
    }

    private fun colorLuma(pixel: Int): Double {
        return rgbLuma(Color.red(pixel), Color.green(pixel), Color.blue(pixel))
    }

    private fun rgbSaturation(r: Int, g: Int, b: Int): Double {
        val maxChannel = max(r, max(g, b)) / 255.0
        val minChannel = min(r, min(g, b)) / 255.0
        return maxChannel - minChannel
    }

    private fun rgbLuma(r: Int, g: Int, b: Int): Double {
        return (r * 0.2126 + g * 0.7152 + b * 0.0722) / 255.0
    }

    private fun updateAdaptiveFps(motionScore: Double) {
        if (!adaptiveFps) {
            currentFps = maxFps
            return
        }
        val normalized = min(1.0, motionScore / 0.05)
        currentFps = (minFps + (maxFps - minFps) * normalized).roundToInt().coerceIn(minFps, maxFps)
    }

    private fun targetSmoothing(motionScore: Double): Double {
        val normalized = min(1.0, motionScore / 0.03)
        var factor = smoothing * (0.72 + normalized * 0.9)
        if (effectiveMode == "single") {
            factor *= 0.95 + normalized * 0.2
        }
        return factor.coerceIn(0.06, 0.9)
    }

    private fun mapToWizPilotColor(color: RgbColor): WizPilotColor {
        var red = color.r / 255.0
        var green = color.g / 255.0
        var blue = color.b / 255.0

        var hueX =
            wizBasis[0][0] * red +
                wizBasis[1][0] * green +
                wizBasis[2][0] * blue
        var hueY =
            wizBasis[0][1] * red +
                wizBasis[1][1] * green +
                wizBasis[2][1] * blue

        val epsilon = 1.0e-5
        val hueLength = sqrt(hueX * hueX + hueY * hueY)
        var saturation = hueLength
        if (saturation > epsilon) {
            hueX /= saturation
            hueY /= saturation
        }

        val rgb = DoubleArray(3)
        if (saturation > epsilon) {
            val maxAngle = cos((2.0 * PI / 3.0) - epsilon)
            val mask =
                IntArray(3) { index ->
                    if (hueX * wizBasis[index][0] + hueY * wizBasis[index][1] > maxAngle) 1 else 0
                }
            val activeCount = mask.sum()

            if (activeCount <= 1) {
                if (activeCount == 0) {
                    val maxChannelIndex = listOf(red, green, blue).indices.maxByOrNull { index ->
                        listOf(red, green, blue)[index]
                    } ?: 0
                    rgb[maxChannelIndex] = 1.0
                } else {
                    rgb[0] = mask[0].toDouble()
                    rgb[1] = mask[1].toDouble()
                    rgb[2] = mask[2].toDouble()
                }
            } else {
                val active = mask.indices.filter { mask[it] == 1 }
                val first = wizBasis[active.first()]
                val second = wizBasis[active.last()]
                val abX = second[1]
                val abY = -second[0]
                val coeff0 = (hueX * abX + hueY * abY) / (first[0] * abX + first[1] * abY)
                val intersectionX = hueX - first[0] * coeff0
                val intersectionY = hueY - first[1] * coeff0
                val coeff1 = intersectionX * second[0] + intersectionY * second[1]
                val maxCoeff = max(coeff0, coeff1).coerceAtLeast(epsilon)
                val scaled0 = coeff0 / maxCoeff
                val scaled1 = coeff1 / maxCoeff
                var activeIndex = 0
                for (index in 0..2) {
                    if (mask[index] == 1) {
                        rgb[index] = min(1.0, if (activeIndex == 0) scaled0 else scaled1)
                        activeIndex += 1
                    }
                }
            }
        }

        val warmWhiteFraction: Double
        if (saturation >= 0.5) {
            warmWhiteFraction = 1.0 - ((saturation - 0.5) * 2.0)
        } else {
            warmWhiteFraction = 1.0
            val scale = saturation * 2.0
            rgb[0] *= scale
            rgb[1] *= scale
            rgb[2] *= scale
        }

        return WizPilotColor(
            rgb =
                RgbColor(
                    (rgb[0] * 255.0).roundToInt().coerceIn(0, 255),
                    (rgb[1] * 255.0).roundToInt().coerceIn(0, 255),
                    (rgb[2] * 255.0).roundToInt().coerceIn(0, 255),
                ),
            warmWhite = max(0, (warmWhiteFraction * 128.0).roundToInt()),
        )
    }

    private fun sendColor(ip: String, color: RgbColor) {
        val mapped = mapToWizPilotColor(color)
        val params =
            JSONObject()
                .put("state", true)
                .put("r", mapped.rgb.r)
                .put("g", mapped.rgb.g)
                .put("b", mapped.rgb.b)
        if (mapped.warmWhite > 0) {
            params.put("w", mapped.warmWhite)
        }

        val payload = JSONObject()
            .put("method", "setPilot")
            .put("params", params)
            .toString()
            .toByteArray(StandardCharsets.UTF_8)

        val address = InetAddress.getByName(ip)
        val packet = DatagramPacket(payload, payload.size, address, WIZ_PORT)
        udpSocket?.send(packet)
    }

    private fun maybePostStatus(
        outputColors: Map<String, RgbColor>,
        smoothingFactor: Double,
    ) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastStatusUpdateAt < 350L) {
            return
        }
        lastStatusUpdateAt = now

        val intervalAverage = if (sendIntervalsMs.isEmpty()) 0.0 else sendIntervalsMs.average()
        val sendRateHz = if (intervalAverage > 0.0) 1000.0 / intervalAverage else 0.0

        val payload = mapOf(
            "mode" to effectiveMode,
            "currentFps" to currentFps,
            "sendRateHz" to sendRateHz,
            "motionScore" to lastMotionScore,
            "smoothing" to smoothingFactor,
            "colorAlgorithm" to colorAlgorithm,
            "predictiveSmoothing" to predictiveSmoothing,
            "updatesSent" to updatesSent,
            "targetColors" to lastTargetColors.mapValues { listOf(it.value.r, it.value.g, it.value.b) },
            "outputColors" to outputColors.mapValues { listOf(it.value.r, it.value.g, it.value.b) },
            "lastError" to lastError,
        )

        mainHandler.post {
            methodChannel?.invokeMethod("onSyncUpdate", payload)
        }
    }

    private fun stopCapture(stopProjection: Boolean = true) {
        isRunning = false
        captureHandler?.removeCallbacksAndMessages(null)
        captureThread?.quitSafely()
        captureThread = null
        captureHandler = null

        virtualDisplay?.release()
        virtualDisplay = null

        imageReader?.close()
        imageReader = null

        val projection = mediaProjection
        mediaProjection = null
        if (projection != null && stopProjection) {
            projection.unregisterCallback(mediaProjectionCallback)
            projection.stop()
        }

        udpSocket?.close()
        udpSocket = null
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Screen Capture",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "WizLight tablet screen sync"
            }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun createNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("WizLight Tablet Sync")
            .setContentText("Driving WiZ bulbs from tablet screen color")
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
