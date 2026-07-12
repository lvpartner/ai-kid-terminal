package com.aikid.terminal

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import java.io.File
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class DeviceApi(
    context: Context,
    private val client: OkHttpClient,
    private val tokenStore: SecureTokenStore,
) {
    private val context = context.applicationContext
    private val jsonType = "application/json".toMediaType()

    suspend fun enroll(code: String): Boolean = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("enrollment_token", code)
            .put("device_name", "${Build.MANUFACTURER}-${Build.MODEL}")
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("os_version", "Android ${Build.VERSION.RELEASE}; ${Build.DISPLAY}")
            .put("manufacturer", Build.MANUFACTURER)
            .put("device_model", Build.MODEL)
            .put("security_patch", Build.VERSION.SECURITY_PATCH)
        val request = Request.Builder()
            .url("${baseUrl()}/v1/enroll")
            .post(payload.toString().toRequestBody(jsonType))
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@withContext false
            val result = JSONObject(response.body.string())
            tokenStore.save(result.getString("device_id"), result.getString("access_token"))
            true
        }
    }

    suspend fun heartbeat(wsState: String) = post(
        "/v1/device/heartbeat",
        JSONObject()
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("os_version", "Android ${Build.VERSION.RELEASE}; ${Build.DISPLAY}")
            .put("manufacturer", Build.MANUFACTURER)
            .put("device_model", Build.MODEL)
            .put("security_patch", Build.VERSION.SECURITY_PATCH)
            .put("network_type", networkType())
            .put("battery_percent", batteryPercent())
            .put("charging", isCharging())
            .put("ws_state", wsState),
    )

    suspend fun telemetry(
        type: String,
        severity: String,
        data: JSONObject = JSONObject(),
        crashStack: String? = null,
    ) {
        val payload = JSONObject()
            .put("event_type", type)
            .put("severity", severity)
            .put("events", data.optJSONArray("events") ?: org.json.JSONArray())
        listOf(
            "session_id",
            "first_packet_ms",
            "first_audio_ms",
            "turn_total_ms",
            "reconnect_count",
        ).forEach { key -> if (data.has(key)) payload.put(key, data.get(key)) }
        if (crashStack != null) payload.put("crash_stack", crashStack)
        post("/v1/device/telemetry", payload)
    }

    suspend fun config(etag: String?): Pair<String?, JSONObject?> = withContext(Dispatchers.IO) {
        val builder = authenticated("/v1/device/config")
        if (etag != null) builder.header("If-None-Match", etag)
        client.newCall(builder.build()).execute().use { response ->
            if (response.code == 304) return@withContext etag to null
            if (!response.isSuccessful) throw IOException("config HTTP ${response.code}")
            response.header("ETag") to JSONObject(response.body.string())
        }
    }

    suspend fun latestUpdate(): JSONObject? = withContext(Dispatchers.IO) {
        val request = authenticated(
            "/v1/device/releases/latest?android_api=${Build.VERSION.SDK_INT}" +
                "&current_version_code=${BuildConfig.VERSION_CODE}",
        ).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("update HTTP ${response.code}")
            JSONObject(response.body.string()).optJSONObject("update")
        }
    }

    suspend fun downloadUpdate(context: android.content.Context, update: JSONObject): File =
        withContext(Dispatchers.IO) {
            val destination = File(context.cacheDir, "pending-update.apk")
            val request = authenticated(update.getString("download_url")).build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) throw IOException("download HTTP ${response.code}")
                destination.outputStream().use { output -> response.body.byteStream().copyTo(output) }
            }
            if (destination.length() != update.getLong("file_size")) {
                destination.delete()
                throw IOException("download size mismatch")
            }
            if (!UpdateVerifier.verify(context, destination, update.getString("sha256"))) {
                destination.delete()
                throw IOException("APK hash or signing certificate mismatch")
            }
            destination
        }

    private suspend fun post(path: String, payload: JSONObject) = withContext(Dispatchers.IO) {
        val request = authenticated(path)
            .post(payload.toString().toRequestBody(jsonType))
            .build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw IOException("telemetry HTTP ${response.code}")
        }
    }

    private fun authenticated(path: String): Request.Builder {
        val token = tokenStore.token() ?: throw IOException("device is not enrolled")
        return Request.Builder()
            .url("${baseUrl()}$path")
            .header("Authorization", "Bearer $token")
    }

    private fun baseUrl(): String = tokenStore.serverUrl()
        ?: throw IOException("family server is not configured")

    private fun batteryPercent(): Int {
        val manager = context.getSystemService(BatteryManager::class.java)
        return manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).coerceIn(0, 100)
    }

    private fun isCharging(): Boolean {
        val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        return when (battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1)) {
            BatteryManager.BATTERY_STATUS_CHARGING, BatteryManager.BATTERY_STATUS_FULL -> true
            else -> false
        }
    }

    private fun networkType(): String {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        val network = manager.activeNetwork ?: return "offline"
        val capabilities = manager.getNetworkCapabilities(network) ?: return "unknown"
        return when {
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            else -> "unknown"
        }
    }
}
