/**
 * Mock environment setup for Jest
 * Forces mock mode for all tests
 */

// Force mock environment
process.env.NODE_ENV = 'test';
process.env.USE_MOCK_MCP = 'true';
process.env.GROQ_API_KEY = 'test_key';
process.env.NOTION_API_KEY = 'test_key';
process.env.NOTION_DATABASE_ID = 'test_db_id';
process.env.LOG_LEVEL = 'error'; // Reduce noise during testing