package com.aikid.terminal

import java.net.URI

object ServerConfig {
    fun normalize(value: String): String? {
        val candidate = value.trim().trimEnd('/')
        val uri = runCatching { URI(candidate) }.getOrNull() ?: return null
        if (uri.userInfo != null || uri.query != null || uri.fragment != null) return null
        if (uri.path !in listOf("", "/")) return null
        val host = uri.host?.lowercase() ?: return null
        if (uri.scheme == "https") return candidate
        if (uri.scheme != "http" || !localHost(host)) return null
        return candidate
    }

    private fun localHost(host: String): Boolean {
        if (host == "localhost" || host.endsWith(".local")) return true
        val parts = host.split('.').mapNotNull(String::toIntOrNull)
        if (parts.size != 4 || parts.any { it !in 0..255 }) return false
        return parts[0] == 10 ||
            (parts[0] == 172 && parts[1] in 16..31) ||
            (parts[0] == 192 && parts[1] == 168) ||
            parts[0] == 127
    }
}

class ExitTapCounter(
    private val requiredTaps: Int = 4,
    private val windowMs: Long = 3_000,
) {
    private var firstTapMs = 0L
    private var taps = 0

    fun register(nowMs: Long): Boolean {
        if (firstTapMs == 0L || nowMs - firstTapMs > windowMs) {
            firstTapMs = nowMs
            taps = 1
            return false
        }
        taps++
        if (taps < requiredTaps) return false
        firstTapMs = 0L
        taps = 0
        return true
    }
}
