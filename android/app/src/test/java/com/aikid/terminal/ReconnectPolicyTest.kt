package com.aikid.terminal

import org.junit.Assert.assertEquals
import org.junit.Test

class ReconnectPolicyTest {
    @Test
    fun delayIsBoundedExponential() {
        assertEquals(1_000, ReconnectPolicy.delayMs(1))
        assertEquals(8_000, ReconnectPolicy.delayMs(4))
        assertEquals(60_000, ReconnectPolicy.delayMs(100))
    }
}

