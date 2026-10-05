package com.kancora.buddy_mobile

import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.KeyEvent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Android side of Buddy's gestures and floating bubble. Flutter talks to it on the
 * "buddy/native" channel (see lib/src/native.dart).
 *
 * - Hold Volume Down (in the app) to talk: after HOLD_MS the key starts recording,
 *   and releasing it sends. A short press still turns the volume down.
 * - Floating bubble: when the app goes to the background, Buddy's head stays on
 *   screen (needs "Display over other apps"). Tap opens Buddy; long-press opens it
 *   listening.
 */
class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null
    private var volumeKeyTalk = true
    private var holdingToTalk = false
    private var bubbleEnabled = false
    private var pendingTalk = false // opened from the bubble with a long-press
    private val handler = Handler(Looper.getMainLooper())
    private val bubble by lazy { BuddyBubble(applicationContext) }

    private val startTalking = Runnable {
        holdingToTalk = true
        channel?.invokeMethod("pushToTalk", true)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "buddy/native").apply {
            setMethodCallHandler { call, result ->
                when (call.method) {
                    "setVolumeKeyTalk" -> {
                        volumeKeyTalk = call.arguments as? Boolean ?: true
                        result.success(null)
                    }
                    "canDrawOverlays" -> result.success(canDrawOverlays())
                    "requestOverlayPermission" -> {
                        if (!canDrawOverlays() && Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                            startActivity(
                                Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
                            )
                        }
                        result.success(null)
                    }
                    "setBubbleEnabled" -> {
                        bubbleEnabled = call.arguments as? Boolean ?: false
                        if (!bubbleEnabled) bubble.hide()
                        result.success(null)
                    }
                    "updateBubble" -> {
                        val args = call.arguments as? Map<*, *>
                        bubble.update(args?.get("state") as? String ?: "idle", args?.get("text") as? String)
                        result.success(null)
                    }
                    "takeLaunchAction" -> {
                        // Flutter asks once it's ready: was the app opened by a bubble long-press?
                        result.success(if (pendingTalk) "talk" else null)
                        pendingTalk = false
                    }
                    else -> result.notImplemented()
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingTalk = intent?.getBooleanExtra(BuddyBubble.EXTRA_TALK, false) == true
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra(BuddyBubble.EXTRA_TALK, false)) {
            channel?.invokeMethod("startTalkingFromBubble", null)
        }
    }

    override fun onStart() {
        super.onStart()
        bubble.hide() // Buddy is on screen itself
    }

    override fun onStop() {
        super.onStop()
        if (holdingToTalk) releaseTalk()
        if (bubbleEnabled && canDrawOverlays()) bubble.show()
    }

    override fun onDestroy() {
        bubble.hide()
        super.onDestroy()
    }

    // Volume Down: hold to talk, short press to turn the volume down.
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (!volumeKeyTalk || event.keyCode != KeyEvent.KEYCODE_VOLUME_DOWN) {
            return super.dispatchKeyEvent(event)
        }
        when (event.action) {
            KeyEvent.ACTION_DOWN -> if (event.repeatCount == 0) handler.postDelayed(startTalking, HOLD_MS)
            KeyEvent.ACTION_UP -> {
                handler.removeCallbacks(startTalking)
                if (holdingToTalk) {
                    releaseTalk()
                } else {
                    val audio = getSystemService(AUDIO_SERVICE) as AudioManager
                    audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
                }
            }
        }
        return true
    }

    private fun releaseTalk() {
        holdingToTalk = false
        channel?.invokeMethod("pushToTalk", false)
    }

    private fun canDrawOverlays(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)

    companion object {
        const val HOLD_MS = 300L
    }
}
