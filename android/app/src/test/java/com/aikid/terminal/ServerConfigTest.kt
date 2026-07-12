package com.aikid.terminal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerConfigTest {
    @Test
    fun acceptsHttpsAndPrivateLanHttpOnly() {
        assertEquals("https://family.example", ServerConfig.normalize("https://family.example/"))
        assertEquals("http://192.168.1.20:8000", ServerConfig.normalize("http://192.168.1.20:8000"))
        assertEquals("http://kid.local:8000", ServerConfig.normalize("http://kid.local:8000"))
        assertNull(ServerConfig.normalize("http://example.com"))
        assertNull(ServerConfig.normalize("https://example.com/path"))
    }

    @Test
    fun legacyServerWinsWhenUpgradingAnEnrolledDevice() {
        assertEquals(
            "https://api.example.com",
            migratedServerUrl(null, "https://api.invalid", "https://api.example.com"),
        )
    }

    @Test
    fun legacyServerRepairsPlaceholderPersistedByPreviousUpgrade() {
        assertEquals(
            "https://api.example.com",
            migratedServerUrl(
                "https://api.invalid",
                "https://api.invalid",
                "https://api.example.com",
            ),
        )
    }

    @Test
    fun migrationPreservesExplicitServerConfiguration() {
        assertEquals(
            "https://family.example",
            migratedServerUrl(
                "https://family.example",
                "https://api.invalid",
                "https://api.example.com",
            ),
        )
    }

    @Test
    fun fourTapsWithinWindowExits() {
        val counter = ExitTapCounter()
        assertFalse(counter.register(1_000))
        assertFalse(counter.register(1_300))
        assertFalse(counter.register(1_600))
        assertTrue(counter.register(1_900))
        assertFalse(counter.register(5_000))
    }
}
