/**
 * Simplified MCP Client Manager using direct MCP SDK require
 * Much cleaner approach as suggested by the user
 */

import { IMCPClient } from './interfaces.js';
import { SearchResult } from '../schemas/index.js';
import { Logger } from '../utils/logger.js';
import { withRetry } from '../utils/retry.js';

const logger = Logger.create('MCPClient');

// Direct MCP SDK usage - much simpler!
const MCP = require('@modelcontextprotocol/sdk/server/mcp.js');

export class MCPClientManager implements IMCPClient {
  private isInitialized = false;
  private mcpServer: any = null;

  constructor() {}

  async initialize(): Promise<void> {
    logger.info('🔗 Initializing MCP with SDK...');

    try {
      // Validate environment variables
      if (!process.env.NOTION_API_KEY) {
        throw new Error('NOTION_API_KEY environment variable is required');
      }
      if (!process.env.BRAVE_API_KEY) {
        throw new Error('BRAVE_API_KEY environment variable is required');
      }

      // Initialize MCP server with both Notion and Brave capabilities
      this.mcpServer = new MCP.Server({
        name: 'hackathon-agent',
        version: '2.0.0',
      });

      // Register tools for both services
      await this.registerTools();

      this.isInitialized = true;
      logger.info('✅ MCP initialized successfully');
    } catch (error) {
      logger.error('❌ Failed to initialize MCP', error);
      throw error;
    }
  }

  private async registerTools(): Promise<void> {
    // Register Brave Search tool
    this.mcpServer.setRequestHandler('tools/call', async (request: any) => {
      const { name, arguments: args } = request.params;

      switch (name) {
        case 'brave_web_search':
          return await this.handleBraveSearch(args);
        case 'create_notion_page':
          return await this.handleNotionPageCreation(args);
        default:
          throw new Error(`Unknown tool: ${name}`);
      }
    });
  }

  private async handleBraveSearch(args: any): Promise<any> {
    // Implementation would use Brave Search API directly
    // This is much cleaner than managing separate processes
    logger.info(`Executing Brave Search: ${args.query}`);

    // TODO: Implement direct Brave API call
    // const response = await fetch('https://api.search.brave.com/res/v1/web/search', {
    //   headers: { 'X-Subscription-Token': process.env.BRAVE_API_KEY },
    //   ...
    // });

    throw new Error('Direct Brave API integration needed');
  }

  private async handleNotionPageCreation(args: any): Promise<any> {
    // Implementation would use Notion API directly
    logger.info(`Creating Notion page`);

    // TODO: Implement direct Notion API call
    // const { Client } = require('@notionhq/client');
    // const notion = new Client({ auth: process.env.NOTION_API_KEY });
    // return await notion.pages.create(args);

    throw new Error('Direct Notion API integration needed');
  }

  async searchHackathons(query: string, maxResults: number = 10): Promise<SearchResult[]> {
    if (!this.isInitialized) {
      throw new Error('MCP client not initialized. Call initialize() first.');
    }

    return withRetry(async () => {
      logger.info(`🔍 Searching hackathons: "${query}" (max ${maxResults})`);

      try {
        const result = await this.mcpServer.request('tools/call', {
          name: 'brave_web_search',
          arguments: {
            query,
            count: maxResults,
            search_lang: 'en',
          },
        });

        return this.parseBraveSearchResults(result);
      } catch (error) {
        logger.error('Failed to search via MCP', error);
        throw error;
      }
    });
  }

  async createNotionPage(databaseId: string, properties: Record<string, any>): Promise<boolean> {
    if (!this.isInitialized) {
      throw new Error('MCP client not initialized. Call initialize() first.');
    }

    return withRetry(async () => {
      const pageTitle = properties.Name?.title?.[0]?.text?.content || 'Unknown';
      logger.info(`📝 Creating Notion page: "${pageTitle}"`);

      try {
        const result = await this.mcpServer.request('tools/call', {
          name: 'create_notion_page',
          arguments: {
            parent: { database_id: databaseId },
            properties,
          },
        });

        return !!result;
      } catch (error) {
        logger.error('Failed to create Notion page', error);
        throw error;
      }
    });
  }

  async disconnect(): Promise<void> {
    logger.info('🔌 Disconnecting MCP...');

    if (this.mcpServer) {
      await this.mcpServer.close();
      this.mcpServer = null;
    }

    this.isInitialized = false;
    logger.info('✅ MCP disconnected');
  }

  private parseBraveSearchResults(braveResult: any): SearchResult[] {
    // Same parsing logic as before
    let results = [];

    if (braveResult && braveResult.web && braveResult.web.results) {
      results = braveResult.web.results;
    } else if (Array.isArray(braveResult)) {
      results = braveResult;
    }

    return results
      .filter((result: any) => result && result.title && result.url)
      .map((result: any) => ({
        url: result.url,
        title: result.title,
        snippet: result.description || result.snippet || '',
        description: result.description || result.snippet || '',
        relevanceScore: this.calculateRelevanceScore(result.title, result.description || ''),
      }))
      .filter((result: SearchResult) => result.relevanceScore > 0.3);
  }

  private calculateRelevanceScore(title: string, description: string): number {
    // Same relevance calculation as before
    const content = `${title} ${description}`.toLowerCase();
    const hackathonKeywords = ['hackathon', 'hack', 'ai', 'competition', 'ml'];

    let score = 0;
    for (const keyword of hackathonKeywords) {
      if (content.includes(keyword)) {
        score += 0.2;
      }
    }

    return Math.min(score, 1.0);
  }
}