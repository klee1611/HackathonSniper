/**
 * Main entry point for the Hackathon Discovery Agent
 *
 * Handles environment configuration and execution modes
 * Preserves CLI functionality from Python version
 */

import { config } from 'dotenv';
import { HackathonDiscoveryAgent } from './agent/HackathonDiscoveryAgent.js';
import { loadConfig } from './schemas/index.js';
import { Logger } from './utils/logger.js';

// Load environment variables
config();

const logger = Logger.create('Main');

/**
 * Main execution function
 */
async function main(): Promise<void> {
  try {
    // Load and validate configuration
    const agentConfig = loadConfig();
    const agent = await HackathonDiscoveryAgent.create(agentConfig);

    // Check execution mode
    const testMode = process.env.TEST_MODE?.toLowerCase() === 'true';
    const testQuery = process.env.TEST_QUERY || 'AI hackathon 2026';

    if (testMode) {
      // Run single test query (preserves Python test mode)
      logger.info('🧪 Running in test mode');
      const results = await agent.runSingleQueryTest(testQuery);
      console.log('Test Results:', JSON.stringify(results, null, 2));
    } else {
      // Run full pipeline
      logger.info('🚀 Running full discovery pipeline');
      const results = await agent.runDiscoveryPipeline();
      console.log('Pipeline Results:', JSON.stringify(results, null, 2));
    }
  } catch (error) {
    logger.error('❌ Fatal error in main', error);
    process.exit(1);
  }
}

/**
 * Handle unhandled errors and process signals
 */
process.on('unhandledRejection', (reason, promise) => {
  logger.error('Unhandled Rejection', { promise, reason });
  process.exit(1);
});

process.on('uncaughtException', (error) => {
  logger.error('Uncaught Exception:', error);
  process.exit(1);
});

process.on('SIGINT', () => {
  logger.info('👋 Received SIGINT, graceful shutdown');
  process.exit(0);
});

process.on('SIGTERM', () => {
  logger.info('👋 Received SIGTERM, graceful shutdown');
  process.exit(0);
});

// Execute main function
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}