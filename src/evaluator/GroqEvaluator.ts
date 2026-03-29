/**
 * Production Groq Evaluator - Real LLM integration
 *
 * Preserves exact evaluation logic and filtering criteria from Python version
 * Uses Groq API with structured outputs via function calling
 * Includes quota protection and smart retry logic
 */

import Groq from 'groq-sdk';
import { IEvaluator } from './interfaces.js';
import {
  SearchResult,
  HackathonSearchResults,
  HackathonSearchResultsSchema,
} from '../schemas/index.js';
import { Logger } from '../utils/logger.js';
import { retryWithCondition } from '../utils/retry.js';

const logger = Logger.create('GroqEvaluator');

/**
 * Production Groq evaluator using real API
 */
export class GroqEvaluator implements IEvaluator {
  private client: Groq;
  private model: string;

  constructor(apiKey: string, model: string = 'llama-3.3-70b-versatile') {
    if (apiKey === 'test_key') {
      throw new Error('Cannot use production GroqEvaluator with test API key - use MockGroqEvaluator');
    }

    this.client = new Groq({ apiKey });
    this.model = model;
  }

  async evaluateSearchResults(
    searchResults: SearchResult[],
    query: string
  ): Promise<HackathonSearchResults> {
    const currentDate = new Date();

    logger.info(`🤖 Groq evaluation: Processing ${searchResults.length} search results with ${this.model}`);

    // Pre-validate search results to avoid wasting quota
    if (!this.validateSearchResults(searchResults)) {
      logger.warn('Invalid search results detected, returning empty result to preserve quota');
      return this.createEmptyResult(query);
    }

    // Prepare search results text for the LLM (mirrors Python version)
    const searchText = searchResults
      .map((result, i) => {
        return `Result ${i + 1}:\nTitle: ${result.title}\nURL: ${result.url}\nSnippet: ${result.snippet}`;
      })
      .join('\n\n');

    const userPrompt = `Analyze these search results for AI-related hackathons and extract those that meet the filtering criteria.

Search Query Used: ${query}
Current Date for Reference: ${currentDate.toISOString().split('T')[0]}

Search Results:
${searchText}

Please extract and evaluate each hackathon found in these results. Return a structured JSON response with the hackathons array containing detailed information for each qualifying hackathon.`;

    try {
      // Use retry logic with quota protection
      const result = await retryWithCondition(
        async () => await this.callGroqAPI(currentDate, userPrompt),
        (error) => this.shouldRetryError(error),
        {
          maxRetries: 2, // Reduce retries to preserve quota
          baseDelay: 2000, // Longer delays for API limits
          backoffMultiplier: 3
        }
      );

      return result;
    } catch (error) {
      logger.error('Groq API call failed after retries', error);
      return this.createEmptyResult(query);
    }
  }

  /**
   * Make the actual Groq API call
   */
  /**
   * Make the actual Groq API call
   */
  private async callGroqAPI(currentDate: Date, userPrompt: string): Promise<HackathonSearchResults> {
      // Use Groq function calling for structured output (mirrors Python Pydantic approach)
      const completion = await this.client.chat.completions.create({
        model: this.model,
        messages: [
          { role: 'system', content: this.buildSystemPrompt(currentDate) },
          { role: 'user', content: userPrompt },
        ],
        functions: [
          {
            name: 'extract_hackathons',
            description: 'Extract and evaluate hackathons from search results',
            parameters: {
              type: 'object',
              properties: {
                hackathons: {
                  type: 'array',
                  items: {
                    type: 'object',
                    properties: {
                      hackathon: {
                        type: 'object',
                        properties: {
                          name: { type: 'string' },
                          url: { type: 'string' },
                          registrationDeadline: { type: ['string', 'null'] },
                          endDate: { type: ['string', 'null'] },
                          prizeUsd: { type: ['number', 'null'] },
                          soloFriendly: { type: ['boolean', 'null'] },
                          description: { type: 'string' },
                          aiRelated: { type: 'boolean' },
                          meetsCriteria: { type: 'boolean' },
                        },
                        required: ['name', 'url', 'aiRelated', 'meetsCriteria', 'description'],
                      },
                      evaluationNotes: { type: 'string' },
                      currentDateContext: { type: 'string' },
                      meetsCriteria: { type: 'boolean' },
                    },
                    required: ['hackathon', 'evaluationNotes', 'currentDateContext', 'meetsCriteria'],
                  },
                },
                searchQueryUsed: { type: 'string' },
                totalFound: { type: 'number' },
                qualifiedCount: { type: 'number' },
              },
              required: ['hackathons', 'searchQueryUsed', 'totalFound', 'qualifiedCount'],
            },
          },
        ],
        function_call: { name: 'extract_hackathons' },
        temperature: 0.1,
        max_tokens: 2000,
      });

      // Extract function call result
      const functionCall = completion.choices[0]?.message?.function_call;
      if (!functionCall || functionCall.name !== 'extract_hackathons') {
        logger.error('No valid function call in Groq response');
        throw new Error('Invalid Groq response: no function call');
      }

      // Parse and validate the response
      const resultData = JSON.parse(functionCall.arguments);

      // Use Zod validation to ensure data integrity
      const validatedResult = HackathonSearchResultsSchema.parse({
        ...resultData,
        searchQueryUsed: resultData.searchQueryUsed || 'unknown',
        totalFound: resultData.hackathons?.length || 0,
        qualifiedCount: resultData.hackathons?.filter((h: any) => h.hackathon?.meetsCriteria || h.meetsCriteria).length || 0,
      });

      logger.info(
        `📊 Groq evaluation complete: ${validatedResult.totalFound} hackathons found, ${validatedResult.qualifiedCount} qualified`
      );

      return validatedResult;
  }

  /**
   * Validate search results before sending to API to prevent quota waste
   */
  private validateSearchResults(searchResults: SearchResult[]): boolean {
    if (!Array.isArray(searchResults) || searchResults.length === 0) {
      return false;
    }

    return searchResults.every(result =>
      result &&
      typeof result.title === 'string' &&
      typeof result.url === 'string' &&
      typeof result.snippet === 'string'
    );
  }

  /**
   * Determine if an error is worth retrying to preserve quota
   */
  private shouldRetryError(error: any): boolean {
    // Don't retry validation errors (400) - these will always fail
    if (error?.status === 400 || error?.message?.includes('validation failed')) {
      logger.warn('Schema validation error - not retrying to preserve quota');
      return false;
    }

    // Don't retry authentication errors
    if (error?.status === 401 || error?.status === 403) {
      logger.warn('Authentication error - not retrying');
      return false;
    }

    // Retry rate limits and server errors
    if (error?.status === 429 || error?.status >= 500) {
      logger.info('Retryable error detected', { status: error?.status });
      return true;
    }

    // Retry network errors
    if (error?.code === 'ECONNRESET' || error?.code === 'ETIMEDOUT') {
      logger.info('Network error - retrying');
      return true;
    }

    // Don't retry other errors by default
    return false;
  }

  /**
   * Build system prompt with dynamic date injection
   * Preserves exact prompt from Python version
   */
  private buildSystemPrompt(currentDate: Date): string {
    const dateStr = currentDate.toISOString().split('T')[0];
    const readableDate = currentDate.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });

    return `You are an expert hackathon analyst. Your job is to evaluate AI-related hackathons with relaxed filtering criteria to capture more opportunities.

CURRENT DATE: ${dateStr} (${readableDate})

RELAXED FILTERING CRITERIA (AI Focus is MAIN requirement):
1. **AI Focus**: Must be AI-related, have an AI track/category, or welcome AI/ML projects
2. **Registration Deadline**: Must be open for registration (not closed yet)
3. **Event Timeline**: Must start within the next 3 months (90 days) from today
4. **Team Flexibility**: Prefer solo-friendly, but team hackathons are acceptable if AI-focused

For each hackathon found in the search results:
1. Extract all available information (name, URL, dates, prizes, team requirements, description)
3. Check if registration is still open and event is within 3 months
4. Only set meetsCriteria=true if ALL criteria are satisfied
5. Provide clear evaluation notes

Parse dates carefully from various formats and convert them to YYYY-MM-DD format.`;
  }

  /**
   * Create empty result for error cases
   */
  private createEmptyResult(query: string): HackathonSearchResults {
    return {
      hackathons: [],
      searchQueryUsed: query,
      totalFound: 0,
      qualifiedCount: 0,
    };
  }
}
