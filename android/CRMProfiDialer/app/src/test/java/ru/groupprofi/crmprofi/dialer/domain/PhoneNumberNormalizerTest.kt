package ru.groupprofi.crmprofi.dialer.domain

import org.junit.Assert.*
import org.junit.Test

/**
 * Тесты для PhoneNumberNormalizer.
 */
class PhoneNumberNormalizerTest {
    
    @Test
    fun `normalize - убирает пробелы`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("7 999 111 22 33"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("8 999 111 22 33"))
    }
    
    @Test
    fun `normalize - убирает дефисы`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("7-999-111-22-33"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("8-999-111-22-33"))
    }
    
    @Test
    fun `normalize - убирает скобки`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("7(999)1112233"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("8(999)1112233"))
    }
    
    @Test
    fun `normalize - убирает плюс`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("+79991112233"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("+7 999 111 22 33"))
    }
    
    @Test
    fun `normalize - убирает все форматирование`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("+7 (999) 111-22-33"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("8 (999) 111 22 33"))
    }
    
    @Test
    fun `normalize - номер без форматирования остается без изменений`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("79991112233"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("89991112233"))
        assertEquals("9991112233", PhoneNumberNormalizer.normalize("9991112233"))
    }
    
    @Test
    fun `normalize - пустая строка`() {
        assertEquals("", PhoneNumberNormalizer.normalize(""))
    }
    
    @Test
    fun `normalize - только форматирование`() {
        assertEquals("", PhoneNumberNormalizer.normalize("+ ()-"))
    }

    @Test
    fun `normalize - формат с плюсом и скобками`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("+7 (999) 111-22-33"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("+7(999)111-22-33"))
    }

    @Test
    fun `normalize - номер с 8 в начале`() {
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("8 (999) 111-22-33"))
        assertEquals("79991112233", PhoneNumberNormalizer.normalize("89991112233"))
    }
}
