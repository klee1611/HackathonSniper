/**
 * Groq evaluator interfaces
 */

import { SearchResult, HackathonSearchResults } from '../schemas/index.js';

/**
 * LLM evaluator interface - identical for production and mock
 */
export interface IEvaluator {
  /**
   * Evaluate search results and extract qualifying hackathons
   */
  evaluateSearchResults(searchResults: SearchResult[], query: string): Promise<HackathonSearchResults>;
}

/**
 * Evaluator factory function type
 */
export type EvaluatorFactory = () => IEvaluator;