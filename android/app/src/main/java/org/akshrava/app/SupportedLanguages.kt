package org.akshrava.app

/** Single source of truth for languages exposed by the Android client and wire protocol. */
data class SupportedLanguage(
    val tag: String,
    val wireCode: String,
    val labelRes: Int,
)

object SupportedLanguages {
    val all = listOf(
        SupportedLanguage("en-IN", "en", R.string.language_english),
        SupportedLanguage("hi-IN", "hi", R.string.language_hindi),
        SupportedLanguage("ta-IN", "ta", R.string.language_tamil),
        SupportedLanguage("kn-IN", "kn", R.string.language_kannada),
        SupportedLanguage("ml-IN", "ml", R.string.language_malayalam),
        SupportedLanguage("te-IN", "te", R.string.language_telugu),
    )

    fun wireCode(tag: String): String {
        val normalized = tag.trim().lowercase()
        return all.firstOrNull { normalized == it.tag.lowercase() || normalized.startsWith(it.wireCode) }?.wireCode
            ?: "en"
    }
}
