package com.aikid.terminal

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.core.content.edit
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureTokenStore(context: Context) {
    private val preferences = context.getSharedPreferences("device_identity", Context.MODE_PRIVATE)
    private val alias = "ai_kid_terminal_device_token"

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(
                KeyGenParameterSpec.Builder(
                    alias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true)
                    .build(),
            )
            generateKey()
        }
    }

    fun save(deviceId: String, token: String) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        preferences.edit {
            putString("device_id", deviceId)
            putString("token_ciphertext", cipher.doFinal(token.toByteArray()).encodeBase64())
            putString("token_iv", cipher.iv.encodeBase64())
        }
    }

    fun token(): String? {
        val ciphertext = preferences.getString("token_ciphertext", null)?.decodeBase64() ?: return null
        val iv = preferences.getString("token_iv", null)?.decodeBase64() ?: return null
        return runCatching {
            Cipher.getInstance("AES/GCM/NoPadding").run {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
                String(doFinal(ciphertext))
            }
        }.getOrNull()
    }

    fun deviceId(): String? = preferences.getString("device_id", null)

    fun saveServerUrl(value: String): Boolean {
        val normalized = ServerConfig.normalize(value) ?: return false
        preferences.edit { putString("server_url", normalized) }
        return true
    }

    fun serverUrl(): String? = preferences.getString("server_url", null)
        ?: ServerConfig.normalize(BuildConfig.API_BASE_URL)

    /** One-time bridge from older builds whose server lived only in BuildConfig. */
    fun persistBundledServerUrl() {
        if (preferences.contains("server_url")) return
        val bundled = ServerConfig.normalize(BuildConfig.API_BASE_URL)
        val legacy = BuildConfig.LEGACY_API_BASE_URL
            .takeIf { token() != null }
            ?.let(ServerConfig::normalize)
        (bundled ?: legacy)?.let { normalized ->
            preferences.edit { putString("server_url", normalized) }
        }
    }

    fun clearIdentity() = preferences.edit {
        remove("device_id")
        remove("token_ciphertext")
        remove("token_iv")
    }
}

private fun ByteArray.encodeBase64(): String =
    android.util.Base64.encodeToString(this, android.util.Base64.NO_WRAP)

private fun String.decodeBase64(): ByteArray =
    android.util.Base64.decode(this, android.util.Base64.NO_WRAP)
