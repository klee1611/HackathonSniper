/**
 * Test environment setup
 * Configures Jest for comprehensive testing
 */

import { config } from 'dotenv';

// Load test environment variables
config({ path: '.env.test' });

// Configure test environment
process.env.NODE_ENV = 'test';
process.env.USE_MOCK_MCP = 'true';

// Suppress console output during tests (except for errors)
const originalLog = console.log;
const originalInfo = console.info;
const originalWarn = console.warn;

beforeAll(() => {
  console.log = jest.fn();
  console.info = jest.fn();
  console.warn = jest.fn();
});

afterAll(() => {
  console.log = originalLog;
  console.info = originalInfo;
  console.warn = originalWarn;
});