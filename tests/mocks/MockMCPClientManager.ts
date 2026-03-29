/**
 * Mock MCP Client Manager - ZERO external dependencies for testing
 *
 * This is the cornerstone of environment isolation - provides complete
 * hackathon discovery pipeline testing without any external APIs
 */

import { IMCPClient } from '../../src/mcp/interfaces.js';
import { SearchResult } from '../../src/schemas/index.js';
import { Logger } from '../../src/utils/logger.js';

const logger = Logger.create('MockMCPClient');

/**
 * Mock MCP Client - Preserves all functionality for testing
 */
export class MockMCPClientManager implements IMCPClient {
  private isInitialized = false;

  async initialize(): Promise<void> {
    logger.info('🎭 Mock MCP Client initialized - ZERO external dependencies!');
    logger.info('✅ Complete environment isolation achieved');
    this.isInitialized = true;
  }

  async searchHackathons(query: string, maxResults: number = 10): Promise<SearchResult[]> {
    if (!this.isInitialized) {
      throw new Error('Mock MCP client not initialized');
    }

    // Import mock data from tests directory where it belongs
    const { MOCK_HACKATHON_FIXTURES } = await import('../fixtures/mockHackathons.js');

    logger.info(`🔍 Mock search: "${query}" (max ${maxResults} results)`);

    // Simulate realistic search behavior by filtering based on query terms
    const queryTerms = query.toLowerCase().split(' ');

    const filteredResults = MOCK_HACKATHON_FIXTURES.filter(hackathon => {
      const searchableText = `${hackathon.title} ${hackathon.snippet}`.toLowerCase();

      // Check if any query terms match
      return queryTerms.some(term =>
        searchableText.includes(term) ||
        // Special handling for common terms
        (term.includes('ai') && searchableText.includes('ai')) ||
        (term.includes('ml') && searchableText.includes('machine learning')) ||
        (term === '2026' && searchableText.includes('2026'))
      );
    });

    // Return subset based on maxResults
    const results = filteredResults.slice(0, maxResults);

    logger.info(`📊 Mock search returned ${results.length} results`);

    // Simulate some processing delay
    await new Promise(resolve => setTimeout(resolve, 100));

    return results;
  }

  async createNotionPage(databaseId: string, properties: Record<string, any>): Promise<boolean> {
    if (!this.isInitialized) {
      throw new Error('Mock MCP client not initialized');
    }

    // Extract page title for logging
    const pageTitle = properties.Name?.title?.[0]?.text?.content || 'Unknown';

    logger.info(`📝 Mock Notion: Creating page "${pageTitle}"`);
    logger.info(`🗄️  Database ID: ${databaseId.slice(0, 8)}...`);

    // Log some interesting details
    if (properties['Prize USD']?.number) {
      logger.info(`💰 Prize: $${properties['Prize USD'].number}`);
    }

    if (properties['Registration Deadline']?.date?.start) {
      logger.info(`⏰ Deadline: ${properties['Registration Deadline'].date.start}`);
    }

    if (properties['Solo Friendly']?.checkbox !== undefined) {
      logger.info(`👤 Solo friendly: ${properties['Solo Friendly'].checkbox ? 'Yes' : 'No'}`);
    }

    // Simulate API processing time
    await new Promise(resolve => setTimeout(resolve, 200));

    logger.info(`✅ Mock Notion page created successfully`);
    return true;
  }

  async disconnect(): Promise<void> {
    logger.info('🔌 Mock MCP client disconnected - no cleanup needed');
    this.isInitialized = false;
  }
}