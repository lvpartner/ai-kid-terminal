package com.aikid.terminal

import android.content.Context
import java.io.PrintWriter
import java.io.StringWriter
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONObject

object CrashReporter {
    private const val PREFERENCES = "crash_reporter"
    private const val PENDING_STACK = "pending_stack"

    fun install(context: Context) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            val output = StringWriter()
            error.printStackTrace(PrintWriter(output))
            context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                .edit()
                .putString(PENDING_STACK, output.toString().take(8_000))
                .commit()
            previous?.uncaughtException(thread, error)
        }
    }

    fun flush(context: Context, tokenStore: SecureTokenStore) {
        val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
        val stack = preferences.getString(PENDING_STACK, null) ?: return
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            val data = JSONObject().put(
                "events",
                JSONArray().put(JSONObject().put("kind", "previous_process_crash")),
            )
            val client = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build()
            runCatching {
                DeviceApi(context, client, tokenStore).telemetry("crash", "error", data, stack)
            }.onSuccess { preferences.edit().remove(PENDING_STACK).apply() }
        }
    }
}
