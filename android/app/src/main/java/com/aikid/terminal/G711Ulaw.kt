package com.aikid.terminal

object G711Ulaw {
    fun decode(data: ByteArray): ByteArray {
        val pcm = ByteArray(data.size * 2)
        data.forEachIndexed { index, encoded ->
            val value = encoded.toInt().inv() and 0xFF
            val exponent = (value shr 4) and 0x07
            val mantissa = value and 0x0F
            var sample = (((mantissa shl 3) + 0x84) shl exponent) - 0x84
            if (value and 0x80 != 0) sample = -sample
            pcm[index * 2] = (sample and 0xFF).toByte()
            pcm[index * 2 + 1] = ((sample shr 8) and 0xFF).toByte()
        }
        return pcm
    }
}
