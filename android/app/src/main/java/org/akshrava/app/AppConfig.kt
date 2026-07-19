package org.akshrava.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class AppConfig(
    val endpoint: String,
    val deviceToken: String,
    val language: String,
    val calibrationId: String
)

object AppConfigStore {
    private const val PREFS = "akshrava"
    private const val ENDPOINT = "endpoint"
    private const val TOKEN = "token" // Legacy plaintext key; migrated on first safe read.
    private const val ENCRYPTED_TOKEN = "encrypted_token"
    private const val TOKEN_IV = "token_iv"
    private const val TOKEN_KEY_ALIAS = "akshrava-device-token-v1"
    private const val LANGUAGE = "language"
    private const val CALIBRATION = "calibration"

    fun load(context: Context): AppConfig {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return AppConfig(
            endpoint = prefs.getString(ENDPOINT, "wss://example.invalid/v1/session")!!,
            deviceToken = loadToken(context),
            language = prefs.getString(LANGUAGE, "en-IN")!!,
            calibrationId = prefs.getString(CALIBRATION, "unprovisioned")!!
        )
    }

    fun save(context: Context, config: AppConfig): Boolean {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(ENDPOINT, config.endpoint.trim())
            .putString(LANGUAGE, config.language)
            .putString(CALIBRATION, config.calibrationId.trim())
            .apply()
        return saveToken(context, config.deviceToken.trim())
    }

    private fun loadToken(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val encrypted = prefs.getString(ENCRYPTED_TOKEN, null)
        val encodedIv = prefs.getString(TOKEN_IV, null)
        if (encrypted != null && encodedIv != null) {
            return runCatching {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                cipher.init(
                    Cipher.DECRYPT_MODE,
                    tokenKey(),
                    GCMParameterSpec(128, Base64.decode(encodedIv, Base64.NO_WRAP))
                )
                String(cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)), Charsets.UTF_8)
            }.getOrElse {
                // Do not fall back to a possibly copied plaintext token after keystore failure.
                prefs.edit().remove(ENCRYPTED_TOKEN).remove(TOKEN_IV).remove(TOKEN).apply()
                ""
            }
        }
        val legacy = prefs.getString(TOKEN, "") ?: ""
        if (legacy.isNotBlank()) {
            // One-shot migration into Keystore, then scrub plaintext immediately.
            if (saveToken(context, legacy)) {
                prefs.edit().remove(TOKEN).apply()
                return legacy
            }
            prefs.edit().remove(TOKEN).apply()
            return ""
        }
        return ""
    }

    /** Returns false when the Android Keystore cannot protect the bearer token. */
    private fun saveToken(context: Context, token: String): Boolean {
        val editor = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(TOKEN)
        if (token.isBlank()) {
            editor.remove(ENCRYPTED_TOKEN).remove(TOKEN_IV).apply()
            return true
        }
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, tokenKey())
            val ciphertext = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
            editor
                .putString(ENCRYPTED_TOKEN, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
                .putString(TOKEN_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .apply()
            true
        } catch (_: Exception) {
            // An unavailable Android Keystore is a provisioning failure, not a reason to retain
            // a bearer token in plaintext storage.
            editor.remove(ENCRYPTED_TOKEN).remove(TOKEN_IV).apply()
            false
        }
    }

    @Synchronized
    private fun tokenKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(TOKEN_KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                TOKEN_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setKeySize(256)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }
}
