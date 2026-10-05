package com.kancora.buddy_mobile

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs

/**
 * Buddy's floating head, shown over other apps while Buddy is in the background.
 * Drag it anywhere; it snaps to the nearest side. Tap opens Buddy, long-press
 * opens Buddy listening. A ring shows Buddy's state, and new replies appear
 * next to the head for a few seconds.
 */
class BuddyBubble(private val context: Context) {
    private val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val prefs = context.getSharedPreferences("buddy_bubble", Context.MODE_PRIVATE)
    private val handler = Handler(Looper.getMainLooper())
    private val density = context.resources.displayMetrics.density
    private val headSize = (56 * density).toInt()

    private val head = HeadView(context)
    private val text = TextView(context).apply {
        setTextColor(Color.rgb(29, 29, 31))
        textSize = 14f
        maxWidth = (230 * density).toInt()
        maxLines = 4
        ellipsize = TextUtils.TruncateAt.END
        setPadding(dp(12), dp(8), dp(12), dp(8))
        background = GradientDrawable().apply {
            cornerRadius = 16 * density
            setColor(Color.argb(240, 255, 255, 255))
        }
        visibility = View.GONE
        elevation = 6 * density
    }
    private val root = LinearLayout(context).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }
    private val params = WindowManager.LayoutParams(
        WindowManager.LayoutParams.WRAP_CONTENT,
        WindowManager.LayoutParams.WRAP_CONTENT,
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        },
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        y = prefs.getInt("y", (180 * density).toInt())
    }
    private var onRight = prefs.getBoolean("right", true)
    private var attached = false
    private val hideText = Runnable { setText(null) }

    init {
        arrange()
        setUpTouch()
    }

    fun show() {
        if (attached) return
        try {
            windowManager.addView(root, params)
            attached = true
            snap()
        } catch (e: Exception) {
            // Permission revoked or the window can't be shown: stay hidden.
        }
    }

    fun hide() {
        if (!attached) return
        attached = false
        try {
            windowManager.removeView(root)
        } catch (e: IllegalArgumentException) {
            // Android already removed it (for example, the permission was revoked).
        }
    }

    /** Moves the window; if Android removed it meanwhile, just forget it. */
    private fun relayout() {
        if (!attached) return
        try {
            windowManager.updateViewLayout(root, params)
        } catch (e: IllegalArgumentException) {
            attached = false
        }
    }

    /** state: idle, listening, thinking, speaking or approval. text: a new line to show, or null. */
    fun update(state: String, newText: String?) {
        head.ringColor = when (state) {
            "listening" -> Color.rgb(229, 72, 77)
            "thinking" -> Color.rgb(245, 165, 36)
            "speaking" -> Color.rgb(48, 164, 108)
            "approval" -> Color.rgb(245, 165, 36)
            else -> Color.rgb(74, 125, 255)
        }
        head.invalidate()
        if (!newText.isNullOrBlank()) {
            setText(newText)
            handler.removeCallbacks(hideText)
            if (state != "approval") handler.postDelayed(hideText, 7000)
        }
    }

    private fun setText(value: String?) {
        text.text = value ?: ""
        text.visibility = if (value.isNullOrBlank()) View.GONE else View.VISIBLE
        root.post { snap() }
    }

    // Head on the screen edge, text toward the middle of the screen.
    private fun arrange() {
        root.removeAllViews()
        val gap = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        gap.setMargins(dp(6), 0, dp(6), 0)
        if (onRight) {
            root.addView(text, gap)
            root.addView(head, LinearLayout.LayoutParams(headSize, headSize))
        } else {
            root.addView(head, LinearLayout.LayoutParams(headSize, headSize))
            root.addView(text, gap)
        }
    }

    private fun snap() {
        if (!attached) return
        val screenWidth = context.resources.displayMetrics.widthPixels
        root.measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED)
        params.x = if (onRight) screenWidth - root.measuredWidth - dp(4) else dp(4)
        relayout()
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun setUpTouch() {
        val slop = ViewConfiguration.get(context).scaledTouchSlop
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var dragging = false
        var longPressed = false
        val longPress = Runnable {
            longPressed = true
            head.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            openBuddy(talk = true)
        }
        val listener = View.OnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    startX = params.x
                    startY = params.y
                    dragging = false
                    longPressed = false
                    handler.postDelayed(longPress, ViewConfiguration.getLongPressTimeout().toLong())
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (!dragging && (abs(dx) > slop || abs(dy) > slop)) {
                        dragging = true
                        handler.removeCallbacks(longPress)
                    }
                    if (dragging && attached) {
                        params.x = (startX + dx).toInt()
                        params.y = (startY + dy).toInt()
                        relayout()
                    }
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    handler.removeCallbacks(longPress)
                    if (dragging) {
                        val screenWidth = context.resources.displayMetrics.widthPixels
                        val right = params.x + root.width / 2 > screenWidth / 2
                        if (right != onRight) {
                            onRight = right
                            arrange()
                        }
                        prefs.edit().putInt("y", params.y).putBoolean("right", onRight).apply()
                        root.post { snap() }
                    } else if (!longPressed && event.actionMasked == MotionEvent.ACTION_UP) {
                        openBuddy(talk = false)
                    }
                }
            }
            true
        }
        head.setOnTouchListener(listener)
        text.setOnTouchListener(listener)
    }

    private fun openBuddy(talk: Boolean) {
        val intent = Intent(context, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            putExtra(EXTRA_TALK, talk)
        }
        context.startActivity(intent)
    }

    private fun dp(value: Int) = (value * density).toInt()

    /** Buddy's face: a blue circle with two eyes, and a ring in the state color. */
    private class HeadView(context: Context) : View(context) {
        var ringColor = Color.rgb(74, 125, 255)
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)

        init {
            elevation = 8 * context.resources.displayMetrics.density
        }

        override fun onDraw(canvas: Canvas) {
            val c = width / 2f
            val r = width / 2f
            paint.style = Paint.Style.FILL
            paint.color = ringColor
            canvas.drawCircle(c, c, r, paint)
            paint.color = Color.rgb(74, 125, 255)
            canvas.drawCircle(c, c, r * 0.84f, paint)
            paint.color = Color.WHITE
            canvas.drawCircle(c - r * 0.32f, c - r * 0.1f, r * 0.17f, paint)
            canvas.drawCircle(c + r * 0.32f, c - r * 0.1f, r * 0.17f, paint)
        }
    }

    companion object {
        const val EXTRA_TALK = "buddy_talk"
    }
}
