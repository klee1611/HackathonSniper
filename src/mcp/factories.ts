/**
 * MCP client factory - creates appropriate client based on configuration
 * Mocks are ONLY used in test environment - never in development or production
 */

import { IMCPClient } from './interfaces.js';
import { MCPClientManager } from './MCPClientManager.js';
import { AgentConfig } from '../schemas/index.js';
import { Logger } from '../utils/logger.js';

const logger = Logger.create('MCPFactory');

/**
 * Create MCP client based on configuration
 * - test environment: Use mocks for isolated testing
 * - development/production: ALWAYS use real MCP clients
 */
export async function createMCPClient(config: AgentConfig): Promise<IMCPClient> {
  // Mocks are ONLY for test environment
  if (config.environment === 'test') {
    logger.info('🎭 Creating Mock MCP client for test environment');
    // Dynamic import from tests directory
    const { MockMCPClientManager } = await import('../../tests/mocks/MockMCPClientManager.js');
    return new MockMCPClientManager();
  }

  // All other environments (development, production) use real MCP
  logger.info('🔗 Creating production MCP client');
  return new MCPClientManager();
}

/**
 * Factory function type for dependency injection
 */
export type CreateMCPClientFn = (config: AgentConfig) => Promise<IMCPClient>;