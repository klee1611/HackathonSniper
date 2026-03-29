/**
 * Production MCP Client Manager - Real MCP SDK integration
 * Uses the official @modelcontextprotocol/sdk with StdioClientTransport
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { IMCPClient } from './interfaces.js';
import { SearchResult } from '../schemas/index.js';
import { Logger } from '../utils/logger.js';
import { withRetry } from '../utils/retry.js';

const logger = Logger.create('MCPClient');

/**
 * Production MCP Client using official MCP SDK
 */
export class MCPClientManager implements IMCPClient {
  private isInitialized = false;
  private notionClient: Client | null = null;
  private braveClient: Client | null = null;

  constructor() {}

  async initialize(): Promise<void> {
    logger.info('🔗 Initializing MCP clients with spawn...');

    try {
      // Validate environment variables
      if (!process.env.NOTION_API_KEY) {
        throw new Error('NOTION_API_KEY environment variable is required');
      }
      if (!process.env.BRAVE_API_KEY) {
        throw new Error('BRAVE_API_KEY environment variable is required');
      }

      // Initialize Notion MCP client
      await this.initializeNotionClient();

      // Initialize Brave Search MCP client
      await this.initializeBraveClient();

      this.isInitialized = true;
      logger.info('✅ MCP clients initialized successfully');
    } catch (error) {
      logger.error('❌ Failed to initialize MCP clients', error);
      await this.cleanup();
      throw error;
    }
  }

  private async initializeNotionClient(): Promise<void> {
    logger.info('🔧 Starting Notion MCP server...');

    // Create stdio transport that spawns the server
    // @notionhq/notion-mcp-server v2.x reads auth from NOTION_TOKEN (not NOTION_API_KEY)
    const transport = new StdioClientTransport({
      command: 'npx',
      args: ['@notionhq/notion-mcp-server'],
      env: {
        ...process.env,
        NOTION_API_KEY: process.env.NOTION_API_KEY!,
        NOTION_TOKEN: process.env.NOTION_TOKEN || process.env.NOTION_API_KEY!,
      },
    });

    // Create MCP client
    this.notionClient = new Client(
      {
        name: 'hackathon-agent',
        version: '2.0.0',
      },
      {
        capabilities: {},
      }
    );

    // Connect to server
    await this.notionClient.connect(transport);
    logger.info('✅ Notion MCP client connected');
  }

  private async initializeBraveClient(): Promise<void> {
    logger.info('🔧 Starting Brave Search MCP server...');

    // Create stdio transport that spawns the server
    const transport = new StdioClientTransport({
      command: 'npx',
      args: ['brave-search-mcp'],
      env: {
        ...process.env,
        BRAVE_API_KEY: process.env.BRAVE_API_KEY!,
      },
    });

    // Create MCP client
    this.braveClient = new Client(
      {
        name: 'hackathon-agent',
        version: '2.0.0',
      },
      {
        capabilities: {},
      }
    );

    // Connect to server
    await this.braveClient.connect(transport);
    logger.info('✅ Brave Search MCP client connected');
  }

  async searchHackathons(query: string, maxResults: number = 10): Promise<SearchResult[]> {
    if (!this.isInitialized || !this.braveClient) {
      throw new Error('MCP client not initialized. Call initialize() first.');
    }

    return withRetry(async () => {
      logger.info(`🔍 Searching hackathons via Brave MCP: "${query}" (max ${maxResults})`);

      try {
        // Call Brave Search via MCP
        const result = await this.braveClient!.callTool({
          name: 'brave_web_search',
          arguments: {
            query,
            count: Math.min(maxResults, 20), // Brave MCP 最大限制是 20
            search_lang: 'en',
            ui_lang: 'en-US',
            safe_search: 'moderate',
          },
        });

        if (result.isError) {
          const errorMessage = typeof result.content === 'object'
            ? JSON.stringify(result.content)
            : result.content;
          throw new Error(`Brave Search MCP error: ${errorMessage}`);
        }

        const searchResults = this.parseBraveSearchResults(result.content);
        logger.info(`✅ Found ${searchResults.length} search results`);
        return searchResults;
      } catch (error) {
        logger.error('Failed to search via Brave MCP', error);
        throw error;
      }
    });
  }

  async createNotionPage(databaseId: string, properties: Record<string, any>): Promise<boolean> {
    if (!this.isInitialized || !this.notionClient) {
      throw new Error('MCP client not initialized. Call initialize() first.');
    }

    return withRetry(async () => {
      const pageTitle = properties.Name?.title?.[0]?.text?.content || 'Unknown';
      logger.info(`📝 Creating Notion page: "${pageTitle}"`);

      try {
        // Call Notion Create Page via MCP
        // The @notionhq/notion-mcp-server generates tool names from OpenAPI operationIds
        // in the format "API-{operationId}". The page creation endpoint has operationId "post-page".
        const result = await this.notionClient!.callTool({
          name: 'API-post-page',
          arguments: {
            parent: {
              database_id: databaseId,
            },
            properties,
          },
        });

        if (result.isError) {
          const errorMessage = typeof result.content === 'object'
            ? JSON.stringify(result.content)
            : result.content;
          throw new Error(`Notion MCP error: ${errorMessage}`);
        }

        logger.info(`✅ Created Notion page successfully`);
        return true;
      } catch (error) {
        logger.error('Failed to create Notion page', error);
        throw error;
      }
    });
  }

  async disconnect(): Promise<void> {
    logger.info('🔌 Disconnecting MCP clients...');
    await this.cleanup();
    this.isInitialized = false;
    logger.info('✅ MCP clients disconnected');
  }

  private async cleanup(): Promise<void> {
    try {
      // Close MCP clients - transport handles process cleanup
      if (this.notionClient) {
        await this.notionClient.close();
        this.notionClient = null;
      }

      if (this.braveClient) {
        await this.braveClient.close();
        this.braveClient = null;
      }
    } catch (error) {
      logger.error('Error during cleanup', error);
    }
  }

  private parseBraveSearchResults(braveResult: any): SearchResult[] {
    logger.info(`🔍 Brave result type: ${typeof braveResult}, is array: ${Array.isArray(braveResult)}, length: ${Array.isArray(braveResult) ? braveResult.length : 'N/A'}`);

    if (braveResult && Array.isArray(braveResult) && braveResult.length > 0) {
      logger.info(`🔍 First element: ${JSON.stringify(braveResult[0], null, 2)}`);
    }

    let searchResults: any[] = [];

    // Handle different result formats from Brave MCP
    if (Array.isArray(braveResult)) {
      if (braveResult.length > 0 && braveResult[0] && braveResult[0].text) {
        // MCP format with text content - need to parse the text
        const textContent = braveResult[0].text;
        logger.info('🔍 Parsing text content for hackathon URLs and titles...');
        logger.info(`🔍 Text content sample: ${textContent.substring(0, 500)}...`);

        // Extract individual results from the text content
        const resultPattern = /(\d+):\s*Title:\s*(.+?)\nURL:\s*(.+?)\nDescription:\s*(.+?)(?=\n\d+:|\n$|$)/gs;
        const matches = Array.from(textContent.matchAll(resultPattern));

        searchResults = matches.map((match: any) => ({
          title: match[2]?.trim() || '',
          url: match[3]?.trim() || '',
          description: match[4]?.trim() || '',
          snippet: match[4]?.trim() || ''
        }));

        logger.info(`🔍 Extracted ${searchResults.length} results from text content`);
      } else {
        // Direct array format with objects
        searchResults = braveResult.filter((result: any) => result && (result.title || result.name) && result.url);
      }
    }

    const rawResults = searchResults
      .filter((result: any) => result && result.title && result.url)
      .map((result: any) => ({
        url: result.url,
        title: result.title,
        snippet: result.description || result.snippet || '',
        description: result.description || result.snippet || '',
        relevanceScore: this.calculateRelevanceScore(result.title, result.description || ''),
      }));

    logger.info(`🔍 Raw results: ${rawResults.length}, After filtering (>0.01): ${rawResults.filter(r => r.relevanceScore > 0.01).length}`);

    // Show a few examples for debugging
    rawResults.slice(0, 3).forEach((result, i) => {
      logger.info(`🔍 Example ${i+1}: "${result.title}" - Score: ${result.relevanceScore.toFixed(3)}`);
    });

    return rawResults.filter((result: SearchResult) => result.relevanceScore > 0.01);
  }

  private calculateRelevanceScore(title: string, description: string): number {
    const content = `${title} ${description}`.toLowerCase();
    // 超級寬鬆的關鍵詞 - 只要有一點相關就通過
    const hackathonKeywords = [
      'hackathon', 'hack', 'ai', 'competition', 'ml', 'contest', 'challenge',
      'artificial intelligence', 'machine learning', 'deep learning',
      'generative', 'llm', 'gpt', 'tech', 'developer', 'coding', 'programming',
      'innovation', 'startup', 'build', 'create', 'develop', 'event', 'summit',
      'conference', 'workshop', 'devpost', 'github', 'open source', 'app'
    ];

    let score = 0.1; // 基礎分數，讓大部分內容都能通過
    for (const keyword of hackathonKeywords) {
      if (content.includes(keyword)) {
        score += 0.05; // 降低每個關鍵詞的權重
      }
    }

    return Math.min(score, 1.0);
  }
}