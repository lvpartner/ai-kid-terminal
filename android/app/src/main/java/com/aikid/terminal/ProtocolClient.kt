package com.aikid.terminal

import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.math.min
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject

class ProtocolClient(
    private val tokenStore: SecureTokenStore,
    private val listener: Listener,
) : WebSocketListener() {
    interface Listener {
        fun onConnected()
        fun onAudio(data: ByteArray)
        fun onControl(event: JSONObject)
        fun onDisconnected(retryDelayMs: Long, reason: String)
    }

    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private var socket: WebSocket? = null
    @Volatile private var connected = false
    private var reconnectAttempt = 0
    private var shuttingDown = false

    @Synchronized
    fun connect() {
        if (shuttingDown || socket != null) return
        val token = tokenStore.token() ?: return
        val baseUrl = tokenStore.serverUrl() ?: return
        val wsUrl = baseUrl
            .replaceFirst("https://", "wss://")
            .replaceFirst("http://", "ws://") + "/v1/device/ws"
        val request = Request.Builder()
            .url(wsUrl)
            .header("Authorization", "Bearer $token")
            .header("Sec-WebSocket-Protocol", "kid-terminal.v1")
            .header("X-Audio-Codecs", "g711_ulaw_8000,pcm_s16le_24000")
            .build()
        socket = http.newWebSocket(request, this)
    }

    fun startSpeech() = sendControl("speech.start")

    fun sendAudio(data: ByteArray): Boolean = socket?.send(ByteString.of(*data)) == true

    fun stopSpeech() = sendControl("speech.stop")

    fun interrupt() = sendControl("interrupt")

    fun heartbeat() = sendControl("heartbeat")

    @Synchronized
    fun close() {
        shuttingDown = true
        connected = false
        socket?.close(1000, "app shutdown")
        socket = null
    }

    private fun sendControl(type: String): Boolean {
        if (!connected) return false
        return socket?.send(
            JSONObject()
                .put("type", type)
                .put("event_id", UUID.randomUUID().toString())
                .toString(),
        ) == true
    }

    override fun onOpen(webSocket: WebSocket, response: Response) {
        synchronized(this) {
            if (socket !== webSocket || shuttingDown) {
                webSocket.close(1000, "stale connection")
                return
            }
            connected = true
        }
        reconnectAttempt = 0
        listener.onConnected()
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        runCatching { JSONObject(text) }.onSuccess(listener::onControl)
    }

    override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
        listener.onAudio(bytes.toByteArray())
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        val shouldReconnect = synchronized(this) {
            if (socket !== webSocket) return
            connected = false
            socket = null
            !shuttingDown
        }
        if (!shouldReconnect) return
        reconnectAttempt++
        val detail = "${t.javaClass.simpleName}: ${t.message.orEmpty()}".take(160)
        listener.onDisconnected(ReconnectPolicy.delayMs(reconnectAttempt), detail)
    }

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
        val shouldReconnect = synchronized(this) {
            if (socket !== webSocket) return
            connected = false
            socket = null
            !shuttingDown
        }
        if (shouldReconnect) {
            reconnectAttempt++
            listener.onDisconnected(
                ReconnectPolicy.delayMs(reconnectAttempt),
                "close code=$code reason=${reason.take(120)}",
            )
        }
    }
}

object ReconnectPolicy {
    fun delayMs(attempt: Int): Long {
        val exponent = 1L shl min(attempt.coerceAtLeast(1) - 1, 6)
        return min(60_000L, exponent * 1_000L)
    }
}
