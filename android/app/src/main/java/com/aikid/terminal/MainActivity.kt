package com.aikid.terminal

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.BatteryManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : ComponentActivity(), ProtocolClient.Listener {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var tokenStore: SecureTokenStore
    private lateinit var protocol: ProtocolClient
    private lateinit var audio: AudioEngine
    private lateinit var voiceButton: Button
    private lateinit var batteryText: TextView
    private lateinit var statusText: TextView
    private var heartbeatJob: Job? = null
    private var waitingPromptJob: Job? = null
    private var batteryReceiverRegistered = false
    private var voiceScreenActive = false
    private val exitTaps = ExitTapCounter()
    private var speechActive = false
    @Volatile private var aiResponding = false
    private var responseStartedMs = 0L
    private var firstAudioMs: Long? = null
    private var responseWireBytes = 0L
    private var responseFrames = 0
    private var responseFailed = false
    private val batteryReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent != null) updateBattery(intent)
        }
    }
    private val microphonePermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (!granted) report("microphone", "error") }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.getInsetsController(window, window.decorView).hide(
            WindowInsetsCompat.Type.statusBars() or WindowInsetsCompat.Type.navigationBars(),
        )
        tokenStore = SecureTokenStore(this)
        tokenStore.persistBundledServerUrl()
        importProvisioningConfig()
        CrashReporter.install(this)
        audio = AudioEngine(this)
        protocol = ProtocolClient(tokenStore, this)
        UpdateScheduler.schedule(this)
        if (tokenStore.token() == null || tokenStore.serverUrl() == null) {
            setContentView(createSetupScreen())
            enrollFromProvisioningIntent()
        } else {
            activateChildMode()
        }
        if (tokenStore.token() != null) CrashReporter.flush(this, tokenStore)
    }

    override fun onResume() {
        super.onResume()
        if (tokenStore.token() != null && voiceScreenActive) {
            hideSystemUi()
            Kiosk.enter(this)
            protocol.connect()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        importProvisioningConfig()
        if (tokenStore.token() == null) enrollFromProvisioningIntent()
    }

    override fun onStart() {
        super.onStart()
        registerReceiver(batteryReceiver, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        batteryReceiverRegistered = true
    }

    override fun onStop() {
        if (batteryReceiverRegistered) {
            unregisterReceiver(batteryReceiver)
            batteryReceiverRegistered = false
        }
        super.onStop()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    private fun createVoiceScreen(): View {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        batteryText = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 20f
            text = getString(R.string.battery_unknown)
            setPadding(dp(24), dp(20), dp(24), dp(20))
            setOnClickListener {
                if (exitTaps.register(android.os.SystemClock.elapsedRealtime())) {
                    Kiosk.exit(this@MainActivity)
                    finishAndRemoveTask()
                }
            }
        }
        root.addView(
            batteryText,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START,
            ),
        )
        voiceButton = Button(this).apply {
            text = ""
            contentDescription = getString(R.string.voice_button)
            background = voiceButtonBackground(pressed = false)
            setOnTouchListener { _, event ->
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        background = voiceButtonBackground(pressed = true)
                        performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
                        beginSpeech()
                    }
                    MotionEvent.ACTION_UP -> {
                        background = voiceButtonBackground(pressed = false)
                        endSpeech()
                        performClick()
                    }
                    MotionEvent.ACTION_CANCEL -> {
                        background = voiceButtonBackground(pressed = false)
                        endSpeech()
                    }
                }
                true
            }
        }
        val size = (resources.displayMetrics.density * 180).toInt()
        root.addView(
            voiceButton,
            FrameLayout.LayoutParams(size, size, Gravity.CENTER),
        )
        statusText = TextView(this).apply {
            setTextColor(Color.LTGRAY)
            textSize = 18f
            gravity = Gravity.CENTER
        }
        root.addView(
            statusText,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            ).apply { setMargins(dp(24), dp(24), dp(24), dp(48)) },
        )
        return root
    }

    private fun createSetupScreen(): View {
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(dp(32), dp(32), dp(32), dp(32))
            setBackgroundColor(Color.WHITE)
        }
        container.addView(TextView(this).apply {
            text = getString(R.string.setup_title)
            textSize = 24f
            setTextColor(Color.BLACK)
        })
        val server = EditText(this).apply {
            hint = getString(R.string.setup_server_hint)
            setText(tokenStore.serverUrl().orEmpty())
            contentDescription = getString(R.string.setup_server_hint)
        }
        val code = EditText(this).apply {
            hint = getString(R.string.setup_code_hint)
            contentDescription = getString(R.string.setup_code_hint)
        }
        val status = TextView(this).apply { setTextColor(Color.RED) }
        val submit = Button(this).apply {
            text = getString(R.string.setup_connect)
            setOnClickListener {
                val normalized = ServerConfig.normalize(server.text.toString())
                if (normalized == null || code.text.isBlank()) {
                    status.text = getString(R.string.setup_invalid)
                    return@setOnClickListener
                }
                tokenStore.saveServerUrl(normalized)
                isEnabled = false
                scope.launch {
                    val enrolled = runCatching {
                        DeviceApi(
                            this@MainActivity,
                            OkHttpClient.Builder().callTimeout(15, TimeUnit.SECONDS).build(),
                            tokenStore,
                        ).enroll(code.text.toString().trim())
                    }.getOrDefault(false)
                    if (enrolled) {
                        activateChildMode()
                    } else {
                        isEnabled = true
                        status.text = getString(R.string.setup_failed)
                    }
                }
            }
        }
        listOf(server, code, submit, status).forEach { view ->
            container.addView(
                view,
                LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(16) },
            )
        }
        return container
    }

    private fun activateChildMode() {
        voiceScreenActive = true
        setContentView(createVoiceScreen())
        hideSystemUi()
        Kiosk.enter(this)
        requestMicrophone()
        protocol.connect()
        CrashReporter.flush(this, tokenStore)
    }

    private fun importProvisioningConfig() {
        val server = intent.getStringExtra("server_url")
            ?: intent.data?.getQueryParameter("server")
        val bindingCode = intent.getStringExtra("enrollment_token")
            ?: intent.data?.getQueryParameter("token")
        if (server != null && bindingCode != null) tokenStore.clearIdentity()
        if (server != null) tokenStore.saveServerUrl(server)
    }

    private fun enrollFromProvisioningIntent() {
        if (tokenStore.token() != null) return
        val code = intent.getStringExtra("enrollment_token")
            ?: intent.data?.getQueryParameter("token")
            ?: BuildConfig.BOOTSTRAP_ENROLLMENT_TOKEN.takeIf(String::isNotBlank)
            ?: return
        if (tokenStore.serverUrl() == null) return
        scope.launch {
            val api = DeviceApi(this@MainActivity, OkHttpClient(), tokenStore)
            if (runCatching { api.enroll(code) }.getOrDefault(false)) {
                activateChildMode()
            } else {
                setContentView(createSetupScreen())
            }
            intent.removeExtra("enrollment_token")
            intent.data = null
        }
    }

    private fun beginSpeech() {
        if (tokenStore.token() == null || speechActive) return
        waitingPromptJob?.cancel()
        if (aiResponding) {
            audio.interruptPlayback()
            protocol.interrupt()
        }
        if (!protocol.startSpeech()) {
            statusText.text = getString(R.string.connection_unavailable)
            reportInputFailure("websocket_not_ready")
            protocol.connect()
            return
        }
        if (!audio.start(protocol::sendAudio)) {
            protocol.stopSpeech()
            requestMicrophone()
            reportInputFailure("microphone_start_failed")
            return
        }
        speechActive = true
    }

    private fun endSpeech() {
        if (!speechActive) return
        speechActive = false
        audio.stop()
        protocol.stopSpeech()
    }

    private fun reportInputFailure(kind: String) {
        report(
            "microphone",
            "error",
            JSONObject().put(
                "events",
                JSONArray().put(JSONObject().put("kind", kind)),
            ),
        )
    }

    private fun requestMicrophone() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
    }

    override fun onConnected() {
        heartbeatJob?.cancel()
        heartbeatJob = scope.launch {
            while (true) {
                protocol.heartbeat()
                runCatching {
                    DeviceApi(this@MainActivity, OkHttpClient(), tokenStore).heartbeat("connected")
                }
                delay(15_000)
            }
        }
        report("ai_connection", "info")
        reportMissingDeviceOwnerOnce()
        refreshConfig()
        UpdateScheduler.runNow(this)
    }

    override fun onAudio(data: ByteArray) {
        waitingPromptJob?.cancel()
        statusText.text = ""
        if (firstAudioMs == null && responseStartedMs > 0) {
            firstAudioMs = android.os.SystemClock.elapsedRealtime() - responseStartedMs
        }
        responseWireBytes += data.size
        responseFrames++
        audio.playUlaw(data)
    }

    override fun onControl(event: JSONObject) {
        when (event.optString("type")) {
            "ai.response.started" -> {
                aiResponding = true
                audio.beginResponse()
                responseStartedMs = android.os.SystemClock.elapsedRealtime()
                firstAudioMs = null
                responseWireBytes = 0
                responseFrames = 0
                responseFailed = false
                waitingPromptJob?.cancel()
                waitingPromptJob = scope.launch {
                    delay(2_200)
                    statusText.text = getString(R.string.thinking)
                }
            }
            "ai.response.done", "ai.response.interrupted" -> {
                waitingPromptJob?.cancel()
                statusText.text = ""
                aiResponding = false
                reportTurn(event.optString("session_id").takeIf(String::isNotBlank))
            }
            "config.changed" -> {
                refreshConfig()
                UpdateScheduler.runNow(this)
            }
            "error" -> {
                val code = event.optString("code")
                if (code == "upstream_error" && !responseFailed) {
                    responseFailed = true
                    waitingPromptJob?.cancel()
                    statusText.text = getString(R.string.connection_unavailable)
                    report(
                        "ai_connection",
                        "error",
                        JSONObject().put(
                            "events",
                            JSONArray().put(
                                JSONObject().put("kind", "upstream_error").put("code", code),
                            ),
                        ),
                    )
                }
            }
        }
    }

    override fun onDisconnected(retryDelayMs: Long, reason: String) {
        waitingPromptJob?.cancel()
        heartbeatJob?.cancel()
        if (speechActive) {
            speechActive = false
            audio.stop()
        }
        handler.postDelayed(protocol::connect, retryDelayMs)
        report(
            "disconnect",
            "warning",
            JSONObject().put(
                "events",
                JSONArray().put(JSONObject().put("kind", "websocket").put("reason", reason)),
            ),
        )
    }

    private fun report(type: String, severity: String, data: JSONObject = JSONObject()) {
        if (tokenStore.token() == null) return
        scope.launch {
            runCatching {
                DeviceApi(
                    this@MainActivity,
                    OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build(),
                    tokenStore,
                ).telemetry(type, severity, data)
            }
        }
    }

    private fun refreshConfig() {
        if (tokenStore.token() == null) return
        val preferences = getSharedPreferences("remote_config", MODE_PRIVATE)
        scope.launch {
            runCatching {
                val result = DeviceApi(this@MainActivity, OkHttpClient(), tokenStore).config(
                    preferences.getString("etag", null),
                )
                if (result.second != null) {
                    preferences.edit {
                        putString("etag", result.first)
                        putString("json", result.second.toString())
                    }
                }
            }
        }
    }

    private fun reportTurn(sessionId: String?) {
        if (responseStartedMs == 0L) return
        val stats = audio.playbackStats()
        val event = JSONObject()
            .put("kind", "voice_transport")
            .put("codec", "g711_ulaw_8000_mono")
            .put("wire_bytes", responseWireBytes)
            .put("frames", responseFrames)
            .put("queued_pcm_bytes", stats.queuedPcmBytes)
            .put("underruns", stats.underrunCount)
        val data = JSONObject()
            .put("first_audio_ms", firstAudioMs ?: 600_000)
            .put(
                "turn_total_ms",
                android.os.SystemClock.elapsedRealtime() - responseStartedMs,
            )
            .put("events", JSONArray().put(event))
        if (sessionId != null) data.put("session_id", sessionId)
        report("latency", "info", data)
        responseStartedMs = 0L
    }

    override fun onDestroy() {
        heartbeatJob?.cancel()
        waitingPromptJob?.cancel()
        protocol.close()
        audio.release()
        scope.cancel()
        super.onDestroy()
    }

    private fun voiceButtonBackground(pressed: Boolean) = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(if (pressed) Color.rgb(255, 193, 7) else Color.rgb(224, 50, 42))
    }

    private fun updateBattery(intent: Intent) {
        if (!::batteryText.isInitialized || !voiceScreenActive) return
        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
        if (level >= 0 && scale > 0) {
            batteryText.text = getString(R.string.battery_percent, level * 100 / scale)
        }
    }

    private fun hideSystemUi() {
        WindowCompat.getInsetsController(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.statusBars() or WindowInsetsCompat.Type.navigationBars())
            systemBarsBehavior =
                androidx.core.view.WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun reportMissingDeviceOwnerOnce() {
        if (Kiosk.isDeviceOwner(this)) return
        val preferences = getSharedPreferences("kiosk_status", MODE_PRIVATE)
        if (!preferences.getBoolean("missing_owner_reported", false)) {
            report("diagnostic", "warning")
            preferences.edit { putBoolean("missing_owner_reported", true) }
        }
    }

    private fun dp(value: Int) = (resources.displayMetrics.density * value).toInt()
}
