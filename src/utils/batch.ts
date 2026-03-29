/**
 * Batch processing utilities
 * Preserves the excellent batch processing logic from Python version
 */

import { Logger } from './logger.js';

const logger = Logger.create('BatchProcessor');

/**
 * Process items in batches with delay between batches
 * Mirrors the batch processing pattern from Python version
 */
export async function processBatches<T, R>(
  items: T[],
  batchSize: number,
  processor: (batch: T[], batchIndex: number) => Promise<R[]>,
  delayBetweenBatches: number = 0
): Promise<R[]> {
  const results: R[] = [];
  const totalBatches = Math.ceil(items.length / batchSize);

  logger.info(`Processing ${items.length} items in ${totalBatches} batches of size ${batchSize}`);

  for (let i = 0; i < items.length; i += batchSize) {
    const batchIndex = Math.floor(i / batchSize);
    const batch = items.slice(i, i + batchSize);

    logger.info(`Processing batch ${batchIndex + 1}/${totalBatches} (${batch.length} items)`);

    try {
      const batchResults = await processor(batch, batchIndex);
      results.push(...batchResults);

      logger.info(`Batch ${batchIndex + 1} completed: ${batchResults.length} results`);

      // Add delay between batches (except for the last batch)
      if (delayBetweenBatches > 0 && i + batchSize < items.length) {
        logger.debug(`Waiting ${delayBetweenBatches}ms before next batch`);
        await new Promise(resolve => setTimeout(resolve, delayBetweenBatches));
      }
    } catch (error) {
      logger.error(`Batch ${batchIndex + 1} failed`, error);
      throw error;
    }
  }

  logger.info(`Batch processing complete: ${results.length} total results`);
  return results;
}

/**
 * Process items in batches with custom error handling
 */
export async function processBatchesWithErrorHandling<T, R>(
  items: T[],
  batchSize: number,
  processor: (batch: T[], batchIndex: number) => Promise<R[]>,
  onBatchError?: (error: Error, batch: T[], batchIndex: number) => R[] | Promise<R[]>,
  delayBetweenBatches: number = 0
): Promise<R[]> {
  const results: R[] = [];
  const totalBatches = Math.ceil(items.length / batchSize);

  for (let i = 0; i < items.length; i += batchSize) {
    const batchIndex = Math.floor(i / batchSize);
    const batch = items.slice(i, i + batchSize);

    try {
      const batchResults = await processor(batch, batchIndex);
      results.push(...batchResults);
    } catch (error) {
      if (onBatchError) {
        logger.warn(`Batch ${batchIndex + 1} failed, using error handler`, error);
        const fallbackResults = await onBatchError(
          error instanceof Error ? error : new Error(String(error)),
          batch,
          batchIndex
        );
        results.push(...fallbackResults);
      } else {
        logger.error(`Batch ${batchIndex + 1} failed with no error handler`, error);
        throw error;
      }
    }

    // Add delay between batches
    if (delayBetweenBatches > 0 && i + batchSize < items.length) {
      await new Promise(resolve => setTimeout(resolve, delayBetweenBatches));
    }
  }

  return results;
}

/**
 * Deduplicate array based on a key function
 * Preserves the deduplication logic from Python version
 */
export function deduplicateBy<T, K>(
  items: T[],
  keyFn: (item: T) => K,
  keepFirst: boolean = true
): T[] {
  const seen = new Set<K>();
  const result: T[] = [];

  const processItems = keepFirst ? items : items.reverse();

  for (const item of processItems) {
    const key = keyFn(item);
    if (!seen.has(key)) {
      seen.add(key);
      result.push(item);
    }
  }

  return keepFirst ? result : result.reverse();
}