/**
 * Zod schemas for hackathon data validation - TypeScript equivalent of Pydantic schemas
 *
 * Preserves all validation logic from the Python version while adding compile-time type safety
 */

import { z } from 'zod';
import { parseISO, isValid } from 'date-fns';

// Custom date parser that handles multiple formats (mirrors Python implementation)
const dateSchema = z
  .union([z.string(), z.date(), z.null()])
  .transform((val) => {
    // Handle null values explicitly
    if (val === null || val === undefined) {
      return undefined; // Convert null to undefined for optional handling
    }

    if (val instanceof Date) {
      return val;
    }

    // Try different date formats like the Python version
    const formats = [
      // ISO format
      () => parseISO(val),
      // Common formats
      () => new Date(val),
    ];

    for (const formatFn of formats) {
      try {
        const parsed = formatFn();
        if (isValid(parsed)) {
          return parsed;
        }
      } catch {
        // Continue to next format
      }
    }

    throw new Error(`Invalid date format: ${val}`);
  })
  .optional();

/**
 * Core hackathon details schema - mirrors HackathonDetails from Python
 */
export const HackathonDetailsSchema = z.object({
  name: z.string().describe("Name of the hackathon"),
  url: z.string().url().describe("Registration or main URL for the hackathon"),
  registrationDeadline: dateSchema.describe("Registration deadline date"),
  endDate: dateSchema.describe("Event end date"),
  prizeUsd: z.number().int().positive().optional().nullable().describe("Total prize pool in USD"),
  soloFriendly: z.boolean().optional().nullable().default(false).describe("Whether solo participation is allowed/encouraged"),
  description: z.string().default("").describe("Description of the hackathon and its AI focus"),
  aiRelated: z.boolean().default(false).describe("Whether this hackathon is AI-related"),
  meetsCriteria: z.boolean().default(false).describe("Whether this hackathon meets all filtering criteria"),
});

export type HackathonDetails = z.infer<typeof HackathonDetailsSchema>;

/**
 * Hackathon evaluation schema - mirrors HackathonEvaluation from Python
 */
export const HackathonEvaluationSchema = z.object({
  hackathon: HackathonDetailsSchema,
  evaluationNotes: z.string().default("").describe("Notes on why this hackathon does/doesn't meet criteria"),
  currentDateContext: z.string().default("").describe("Current date context used for evaluation"),
});

export type HackathonEvaluation = z.infer<typeof HackathonEvaluationSchema>;

/**
 * Search results schema - mirrors HackathonSearchResults from Python
 */
export const HackathonSearchResultsSchema = z.object({
  hackathons: z.array(HackathonEvaluationSchema).default([]),
  searchQueryUsed: z.string().default("").describe("The search query that generated these results"),
  totalFound: z.number().default(0).describe("Total number of hackathons found in search"),
  qualifiedCount: z.number().default(0).describe("Number of hackathons that meet all criteria"),
});

export type HackathonSearchResults = z.infer<typeof HackathonSearchResultsSchema>;

/**
 * Notion page schema - mirrors NotionPage from Python
 */
export const NotionPageSchema = z.object({
  title: z.string(),
  url: z.string().url(),
  registrationDeadline: dateSchema,
  endDate: dateSchema,
  prizeUsd: z.number().int().positive().optional(),
  soloFriendly: z.boolean().default(false),
  description: z.string().default(""),
});

export type NotionPage = z.infer<typeof NotionPageSchema>;

/**
 * Convert NotionPage to Notion API properties format
 * Mirrors the to_notion_properties method from Python
 */
export function toNotionProperties(page: NotionPage): Record<string, any> {
  const properties: Record<string, any> = {
    "Name": { title: [{ text: { content: page.title } }] },
    "URL": { url: page.url },
    "Solo Friendly": { checkbox: page.soloFriendly },
    "Description/AI Focus": { rich_text: [{ text: { content: page.description } }] },
  };

  if (page.registrationDeadline) {
    properties["Registration Deadline"] = {
      date: { start: page.registrationDeadline.toISOString().split('T')[0] }
    };
  }

  if (page.endDate) {
    properties["End Date"] = {
      date: { start: page.endDate.toISOString().split('T')[0] }
    };
  }

  if (page.prizeUsd !== undefined && page.prizeUsd !== null) {
    properties["Prize USD"] = { number: page.prizeUsd };
  }

  return properties;
}

/**
 * Search result from MCP server
 */
export const SearchResultSchema = z.object({
  title: z.string(),
  url: z.string().url(),
  snippet: z.string().describe("Content snippet or description"),
  description: z.string().optional(),
  relevanceScore: z.number().min(0).max(1).describe("AI-calculated relevance score for hackathon content"),
});

export type SearchResult = z.infer<typeof SearchResultSchema>;

/**
 * Pipeline execution results
 */
export const PipelineResultsSchema = z.object({
  executionDate: z.string().describe("Date of pipeline execution (ISO format)"),
  totalSearchResults: z.number().describe("Total search results processed"),
  qualifiedHackathons: z.number().describe("Number of hackathons meeting criteria"),
  storedHackathons: z.number().describe("Number of hackathons stored in Notion"),
  success: z.boolean().describe("Whether pipeline completed successfully"),
  errorMessage: z.string().optional().describe("Error message if pipeline failed"),
});

export type PipelineResults = z.infer<typeof PipelineResultsSchema>;