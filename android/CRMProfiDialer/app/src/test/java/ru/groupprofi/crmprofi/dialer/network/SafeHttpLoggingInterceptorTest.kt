package ru.groupprofi.crmprofi.dialer.network

import org.junit.Assert.*
import org.junit.Test

/**
 * Unit-тесты для SafeHttpLoggingInterceptor.
 * Проверяем корректность маскирования без порчи JSON формата.
 */
class SafeHttpLoggingInterceptorTest {
    
    private val interceptor = SafeHttpLoggingInterceptor()
    
    // Используем reflection для доступа к приватному методу maskSensitiveData
    private fun maskSensitiveData(text: String): String {
        val method = SafeHttpLoggingInterceptor::class.java.getDeclaredMethod("maskSensitiveData", String::class.java)
        method.isAccessible = true
        return method.invoke(interceptor, text) as String
    }
    
    @Test
    fun `maskSensitiveData - JSON body с device_id не портит формат`() {
        val input = """{"device_id":"9982171c26e26682","items":[]}"""
        val result = maskSensitiveData(input)
        
        // Проверяем, что JSON формат сохранен
        assertTrue("Результат должен быть валидным JSON", result.startsWith("{") && result.endsWith("}"))
        assertTrue("device_id должен быть замаскирован", result.contains("9982***6682"))
        assertFalse("НЕ должно быть формата device_id=\"...\"", result.contains("device_id=\""))
        assertTrue("Должен быть формат \"device_id\":\"...\"", result.contains("\"device_id\":\""))
    }
    
    @Test
    fun `maskSensitiveData - query параметр device_id маскируется без кавычек`() {
        val input = "GET /api/phone/calls/pull/?device_id=9982171c26e26682 HTTP/1.1"
        val result = maskSensitiveData(input)
        
        // Проверяем, что query параметр замаскирован без кавычек
        assertTrue("device_id должен быть замаскирован", result.contains("9982***6682"))
        assertFalse("НЕ должно быть кавычек вокруг значения в query", result.contains("device_id=\"9982"))
        assertTrue("Должен быть формат device_id=9982***6682", result.contains("device_id=9982***6682"))
    }
    
    @Test
    fun `maskSensitiveData - JSON body с device_id не портится при наличии query в тексте`() {
        // Симулируем лог, где есть и JSON body и query параметры
        val input = """POST /api/phone/telemetry/?device_id=9982171c26e26682 HTTP/1.1
Content-Type: application/json

{"device_id":"9982171c26e26682","items":[]}"""
        val result = maskSensitiveData(input)
        
        // Проверяем, что JSON формат сохранен
        val jsonPart = result.substringAfter("{")
        assertTrue("JSON должен содержать \"device_id\":\"9982***6682\"", jsonPart.contains("\"device_id\":\"9982***6682\""))
        assertFalse("НЕ должно быть формата device_id=\"...\" в JSON", jsonPart.contains("device_id=\""))
        
        // Проверяем, что query параметр тоже замаскирован
        assertTrue("Query параметр должен быть замаскирован", result.contains("device_id=9982***6682"))
    }
    
    @Test
    fun `maskSensitiveData - смешанный текст с device_id в разных форматах`() {
        val input = """device_id: 9982171c26e26682
{"device_id":"9982171c26e26682","other":"value"}
device_id=9982171c26e26682"""
        val result = maskSensitiveData(input)
        
        // Проверяем, что JSON формат сохранен
        assertTrue("JSON должен содержать \"device_id\":\"9982***6682\"", result.contains("\"device_id\":\"9982***6682\""))
        assertFalse("НЕ должно быть формата device_id=\"...\" в JSON", result.contains("\"device_id=\""))
        
        // Проверяем другие форматы
        assertTrue("device_id= должен быть замаскирован", result.contains("device_id=9982***6682"))
    }
    
    @Test
    fun `maskSensitiveData - JSON с экранированными кавычками не портится`() {
        val input = """{"device_id":"9982171c26e26682","message":"Say \"hello\""}"""
        val result = maskSensitiveData(input)
        
        // Проверяем, что JSON формат сохранен
        assertTrue("Результат должен быть валидным JSON", result.startsWith("{") && result.endsWith("}"))
        assertTrue("device_id должен быть замаскирован", result.contains("9982***6682"))
        assertFalse("НЕ должно быть формата device_id=\"...\"", result.contains("device_id=\""))
    }
    
    @Test
    fun `maskSensitiveData - короткий device_id маскируется корректно`() {
        val input = """{"device_id":"12345678","items":[]}"""
        val result = maskSensitiveData(input)
        
        assertTrue("Короткий device_id должен быть замаскирован", result.contains("\"device_id\":\"***\""))
    }
    
    @Test
    fun `maskSensitiveData - Bearer токен маскируется`() {
        val input = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        val result = maskSensitiveData(input)
        
        assertTrue("Bearer токен должен быть замаскирован", result.contains("Bearer ***"))
        assertFalse("Токен не должен быть виден", result.contains("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"))
    }
    
    @Test
    fun `maskSensitiveData - password в JSON маскируется`() {
        val input = """{"username":"user","password":"secret123"}"""
        val result = maskSensitiveData(input)
        
        assertTrue("Password должен быть замаскирован", result.contains("\"password\":\"masked\""))
        assertFalse("Password не должен быть виден", result.contains("secret123"))
    }
    
    @Test
    fun `maskSensitiveData - query параметр device_id с закрывающей скобкой не вызывает PatternSyntaxException`() {
        // КРИТИЧНЫЙ ТЕСТ: проверяем, что regex не падает на закрывающей } в lookahead
        // Это реальная проблема, которая ломала QR-логин на некоторых Android версиях
        val input = "GET /api/phone/calls/pull/?device_id=9982171c26e26682} HTTP/1.1"
        val result = maskSensitiveData(input)
        
        // Проверяем, что обработка прошла без исключения и device_id замаскирован
        assertTrue("device_id должен быть замаскирован", result.contains("9982***6682"))
        assertFalse("НЕ должно быть кавычек вокруг значения в query", result.contains("device_id=\"9982"))
        assertTrue("Должен быть формат device_id=9982***6682", result.contains("device_id=9982***6682"))
    }
    
    @Test
    fun `maskSensitiveData - query параметр device_id с & и закрывающей скобкой`() {
        // Проверяем edge case: device_id в середине query строки с закрывающей }
        val input = "GET /api/phone/calls/pull/?param1=value&device_id=9982171c26e26682}&param2=value HTTP/1.1"
        val result = maskSensitiveData(input)
        
        assertTrue("device_id должен быть замаскирован", result.contains("9982***6682"))
        assertTrue("Должен быть формат device_id=9982***6682", result.contains("device_id=9982***6682"))
    }
}
