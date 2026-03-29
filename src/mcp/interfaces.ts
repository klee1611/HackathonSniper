/**
 * MCP client interfaces
 * Defines contracts for both production and mock MCP clients
 */

import { SearchResult, NotionPage } from '../schemas/index.js';

/**
 * MCP client interface - identical for production and mock implementations
 * This ensures perfect interchangeability for testing
 */
export interface IMCPClient {
  /**
   * Initialize the MCP client connections
   */
  initialize(): Promise<void>;

  /**
   * Search for hackathons using Brave Search
   */
  searchHackathons(query: string, maxResults?: number): Promise<SearchResult[]>;

  /**
   * Create a new page in Notion database
   */
  createNotionPage(databaseId: string, properties: Record<string, any>): Promise<boolean>;

  /**
   * Disconnect from MCP services
   */
  disconnect(): Promise<void>;
}

/**
 * MCP client factory function type
 */
export type MCPClientFactory = () => IMCPClient;

/**
 * Configuration for MCP clients
 */
export interface MCPClientConfig {
  useMock: boolean;
  braveSearchPath?: string;
  notionPath?: string;
  apiKeys: {
    groq: string;
    notion: string;
  };
}