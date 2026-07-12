package com.aikid.terminal

import org.junit.Assert.assertArrayEquals
import org.junit.Test

class G711UlawTest {
    @Test
    fun decodesKnownSamplesToLittleEndianPcm16() {
        assertArrayEquals(
            byteArrayOf(0, 0, 0, 0, 0x7C, 0x7D, 0x84.toByte(), 0x82.toByte()),
            G711Ulaw.decode(byteArrayOf(0xFF.toByte(), 0x7F, 0x80.toByte(), 0x00)),
        )
    }
}
