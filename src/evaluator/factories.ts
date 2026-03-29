/**
 * Evaluator factory - creates appropriate evaluator based on configuration
 */

import { IEvaluator } from './interfaces.js';
import { GroqEvaluator } from './GroqEvaluator.js';
import { AgentConfig } from '../schemas/index.js';
import { Logger } from '../utils/logger.js';

const logger = Logger.create('EvaluatorFactory');

/**
 * Create evaluator based on configuration
 * Mocks are ONLY used in test environment - never in development or production
 */
export async function createEvaluator(config: AgentConfig): Promise<IEvaluator> {
  // Use mock only in test environment or with test API keys
  if (config.environment === 'test' || config.groqApiKey === 'test_key') {
    logger.info('🎭 Creating Mock Groq evaluator for test environment');
    // Dynamic import from tests directory where it belongs
    const { MockGroqEvaluator } = await import('../../tests/mocks/MockGroqEvaluator.js');
    return new MockGroqEvaluator();
  }

  logger.info('🤖 Creating production Groq evaluator');
  return new GroqEvaluator(config.groqApiKey);
}

/**
 * Factory function type for dependency injection
 */
export type CreateEvaluatorFn = (config: AgentConfig) => Promise<IEvaluator>;