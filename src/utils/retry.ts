/**
 * Retry utility with exponential backoff
 * Mirrors the retry logic from Python version
 */

import { Logger } from './logger.js';

const logger = Logger.create('RetryUtility');

export interface RetryOptions {
  maxRetries?: number;
  baseDelay?: number;
  maxDelay?: number;
  backoffMultiplier?: number;
  jitter?: boolean;
}

const DEFAULT_OPTIONS: Required<RetryOptions> = {
  maxRetries: 3,
  baseDelay: 1000, // 1 second
  maxDelay: 30000, // 30 seconds
  backoffMultiplier: 2,
  jitter: true,
};

/**
 * Retry a function with exponential backoff
 * Preserves the excellent retry pattern from Python version
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {}
): Promise<T> {
  const opts = { ...DEFAULT_OPTIONS, ...options };

  let lastError: Error;

  for (let attempt = 0; attempt < opts.maxRetries; attempt++) {
    try {
      const result = await fn();
      if (attempt > 0) {
        logger.info(`Operation succeeded on attempt ${attempt + 1}`);
      }
      return result;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));

      if (attempt === opts.maxRetries - 1) {
        logger.error(`All ${opts.maxRetries} retry attempts failed`, lastError);
        throw lastError;
      }

      const delay = calculateDelay(attempt, opts);
      logger.warn(
        `Attempt ${attempt + 1} failed, retrying in ${delay}ms`,
        { error: lastError.message, attempt: attempt + 1 }
      );

      await sleep(delay);
    }
  }

  throw lastError!;
}

/**
 * Calculate retry delay with exponential backoff and optional jitter
 */
function calculateDelay(attempt: number, options: Required<RetryOptions>): number {
  let delay = options.baseDelay * Math.pow(options.backoffMultiplier, attempt);

  // Apply maximum delay limit
  delay = Math.min(delay, options.maxDelay);

  // Add jitter to prevent thundering herd
  if (options.jitter) {
    delay = delay * (0.5 + Math.random() * 0.5);
  }

  return Math.floor(delay);
}

/**
 * Sleep utility
 */
function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Retry with custom condition
 */
export async function retryWithCondition<T>(
  fn: () => Promise<T>,
  shouldRetry: (error: any) => boolean,
  options: RetryOptions = {}
): Promise<T> {
  const opts = { ...DEFAULT_OPTIONS, ...options };

  let lastError: Error;

  for (let attempt = 0; attempt < opts.maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));

      if (attempt === opts.maxRetries - 1 || !shouldRetry(error)) {
        throw lastError;
      }

      const delay = calculateDelay(attempt, opts);
      logger.warn(`Retrying after ${delay}ms due to: ${lastError.message}`);
      await sleep(delay);
    }
  }

  throw lastError!;
}