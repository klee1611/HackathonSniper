/**
 * Search Phase - First phase of the 3-phase pipeline
 *
 * Handles search query execution with deduplication and error handling
 * Preserves exact search strategy from Python version
 */

import { IMCPClient } from '../../mcp/interfaces.js';
import { SearchResult } from '../../schemas/index.js';
import { Logger } from '../../utils/logger.js';
import { deduplicateBy } from '../../utils/batch.js';

const logger = Logger.create('SearchPhase');

export class SearchPhase {
  constructor(private mcpClient: IMCPClient) {}

  /**
   * Execute search phase with multiple queries and deduplication
   * Preserves exact logic from Python version
   */
  async execute(
    searchQueries: string[],
    maxResultsPerQuery: number = 15,
    delayBetweenQueries: number = 2000
  ): Promise<SearchResult[]> {
    logger.info(`🔍 Starting search phase with ${searchQueries.length} queries`);

    const allResults: SearchResult[] = [];

    for (let i = 0; i < searchQueries.length; i++) {
      const query = searchQueries[i];
      logger.info(`📡 Executing search query ${i + 1}/${searchQueries.length}: "${query}"`);

      try {
        const results = await this.mcpClient.searchHackathons(query, maxResultsPerQuery);
        allResults.push(...results);

        logger.info(`📊 Query ${i + 1} returned ${results.length} results`);

        // Add delay between queries to be respectful to APIs (except for last query)
        if (i < searchQueries.length - 1 && delayBetweenQueries > 0) {
          logger.debug(`⏳ Waiting ${delayBetweenQueries}ms before next query`);
          await new Promise(resolve => setTimeout(resolve, delayBetweenQueries));
        }
      } catch (error) {
        logger.error(`❌ Search query ${i + 1} failed: ${query}`, error);
        // Continue with other queries instead of failing the entire search phase
        continue;
      }
    }

    // Deduplicate results based on URL (preserves Python logic)
    const uniqueResults = deduplicateBy(allResults, result => result.url, true);

    logger.info(
      `✅ Search phase complete: ${allResults.length} total results, ${uniqueResults.length} unique results`
    );

    return uniqueResults;
  }
}