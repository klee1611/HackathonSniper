/**
 * Configuration schemas and validation
 * Mirrors the excellent configuration validation from Python version
 */

import { z } from 'zod';

/**
 * Environment validation schema
 */
const EnvironmentSchema = z.enum(['development', 'test', 'production']);

/**
 * Agent configuration schema - mirrors AgentConfigSchema from Python
 */
export const AgentConfigSchema = z.object({
  environment: EnvironmentSchema.default('development'),

  // API Configuration (required for real MCP usage)
  groqApiKey: z.string().min(1, 'GROQ_API_KEY is required'),
  notionApiKey: z.string().min(1, 'NOTION_API_KEY is required'),
  notionDatabaseId: z.string().min(1, 'NOTION_DATABASE_ID is required'),

  // MCP Configuration (optional paths)
  braveSearchPath: z.string().optional(),
  notionPath: z.string().optional(),

  // Search Configuration
  // Queries are AI-focused and time-bounded to surface open, upcoming events
  searchQueries: z.array(z.string()).default([
    "AI hackathon 2026 open registration",
    "artificial intelligence hackathon apply now 2026",
    "machine learning hackathon spring 2026",
    "generative AI hackathon open 2026",
    "LLM hackathon 2026 register",
    "AI hackathon devpost 2026",
    "AI competition lablab.ai 2026",
    "AI hackathon solo friendly 2026",
    "deep learning competition open registration 2026",
    "AI developer challenge 2026 prizes"
  ]),

  // Processing Configuration - Brave MCP 最大支持 20 個結果
  batchSize: z.number().positive().default(5),
  maxResults: z.number().positive().default(15), // Brave MCP 最大限制是 20，使用 15 更安全
  delayBetweenBatches: z.number().nonnegative().default(3000),
  delayBetweenQueries: z.number().nonnegative().default(2000),

  // Logging Configuration
  logLevel: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
});

export type AgentConfig = z.infer<typeof AgentConfigSchema>;

/**
 * Load and validate configuration from environment variables
 * Mirrors loadConfig functionality from Python version
 */
export function loadConfig(): AgentConfig {
  const config = {
    environment: process.env.NODE_ENV,
    groqApiKey: process.env.GROQ_API_KEY,
    notionApiKey: process.env.NOTION_API_KEY,
    notionDatabaseId: process.env.NOTION_DATABASE_ID,
    braveSearchPath: process.env.BRAVE_SEARCH_MCP_PATH,
    notionPath: process.env.NOTION_MCP_PATH,
    logLevel: process.env.LOG_LEVEL,
  };

  return AgentConfigSchema.parse(config);
}

/**
 * Validate configuration for production use
 */
export function validateProductionConfig(config: AgentConfig): void {
  if (config.environment === 'production') {
    const requiredFields: (keyof AgentConfig)[] = ['groqApiKey', 'notionApiKey', 'notionDatabaseId'];

    for (const field of requiredFields) {
      if (!config[field] || config[field] === 'test_key' || config[field] === 'test_db_id') {
        throw new Error(`${field} must be set for production environment`);
      }
    }
  }
}