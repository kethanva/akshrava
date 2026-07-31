package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SupportedLanguagesTest {

    @Test
    fun allContainsSixSupportedLanguages() {
        val languages = SupportedLanguages.all
        assertEquals(6, languages.size)
        val tags = languages.map { it.tag }
        assertTrue(tags.contains("en-IN"))
        assertTrue(tags.contains("hi-IN"))
        assertTrue(tags.contains("ta-IN"))
        assertTrue(tags.contains("kn-IN"))
        assertTrue(tags.contains("ml-IN"))
        assertTrue(tags.contains("te-IN"))
    }

    @Test
    fun wireCodeMapsTagToWireCode() {
        assertEquals("en", SupportedLanguages.wireCode("en-IN"))
        assertEquals("hi", SupportedLanguages.wireCode("hi-IN"))
        assertEquals("ta", SupportedLanguages.wireCode("ta-IN"))
        assertEquals("kn", SupportedLanguages.wireCode("kn-IN"))
        assertEquals("ml", SupportedLanguages.wireCode("ml-IN"))
        assertEquals("te", SupportedLanguages.wireCode("te-IN"))
    }

    @Test
    fun wireCodeHandlesCaseAndPrefixes() {
        assertEquals("en", SupportedLanguages.wireCode("EN-IN"))
        assertEquals("hi", SupportedLanguages.wireCode("hi"))
        assertEquals("ta", SupportedLanguages.wireCode("TA"))
    }

    @Test
    fun wireCodeUnknownTagDefaultsToEnglish() {
        assertEquals("en", SupportedLanguages.wireCode("fr-FR"))
        assertEquals("en", SupportedLanguages.wireCode(""))
    }
}
