/**
 * Mock Groq Evaluator - Environment isolation for LLM evaluation
 *
 * Provides deterministic hackathon evaluation for testing without external API calls
 * Uses patterns from tests/fixtures instead of hardcoded data
 */

import { IEvaluator } from '../../src/evaluator/interfaces.js';
import {
  SearchResult,
  HackathonSearchResults,
  HackathonEvaluation,
  HackathonDetails,
} from '../../src/schemas/index.js';
import { Logger } from '../../src/utils/logger.js';
import { addDays, addWeeks } from 'date-fns';

const logger = Logger.create('MockEvaluator');

/**
 * Mock Groq Evaluator that simulates LLM evaluation logic
 * NO hardcoded mock data - imports from tests/fixtures
 */
export class MockGroqEvaluator implements IEvaluator {
  async evaluateSearchResults(
    searchResults: SearchResult[],
    query: string
  ): Promise<HackathonSearchResults> {
    const currentDate = new Date();

    logger.info(`🤖 Mock evaluation: Processing ${searchResults.length} search results`);
    logger.info(`📅 Current date: ${currentDate.toISOString().split('T')[0]}`);

    const evaluations: HackathonEvaluation[] = [];

    for (let i = 0; i < searchResults.length; i++) {
      const result = searchResults[i];
      const evaluation = await this.evaluateSingleHackathon(result, i, currentDate);
      evaluations.push(evaluation);

      if (evaluation.hackathon.meetsCriteria) {
        logger.info(`✅ Qualified hackathon: ${evaluation.hackathon.name}`);
      } else {
        logger.debug(`❌ Rejected hackathon: ${evaluation.hackathon.name} - ${evaluation.evaluationNotes}`);
      }
    }

    const qualifiedCount = evaluations.filter(e => e.hackathon.meetsCriteria).length;

    logger.info(`📊 Mock evaluation complete: ${evaluations.length} total, ${qualifiedCount} qualified`);

    return {
      hackathons: evaluations,
      searchQueryUsed: query,
      totalFound: evaluations.length,
      qualifiedCount,
    };
  }

  /**
   * Evaluate a single hackathon using patterns from test fixtures
   */
  private async evaluateSingleHackathon(
    result: SearchResult,
    index: number,
    currentDate: Date
  ): Promise<HackathonEvaluation> {
    // Import evaluation patterns from tests where they belong
    const { MOCK_EVALUATION_PATTERNS } = await import('../fixtures/mockEvaluationPatterns.js');

    const title = result.title;

    // Determine if this hackathon should qualify (first 2 qualify for testing)
    const shouldQualify = index < 2;

    let evaluationNotes: string;
    let hackathon: HackathonDetails;

    if (shouldQualify) {
      // Create a qualifying hackathon using patterns
      const pattern = MOCK_EVALUATION_PATTERNS.qualifying;
      const prizeAmount = pattern.basePrize + (index * pattern.prizeIncrement);
      const registrationDeadline = addDays(currentDate, pattern.daysTillRegistration + index);
      const endDate = addWeeks(currentDate, pattern.weeksToEnd + index);

      hackathon = {
        name: title,
        url: result.url,
        registrationDeadline,
        endDate,
        prizeUsd: prizeAmount,
        soloFriendly: pattern.soloFriendly,
        description: this.extractDescription(result.snippet),
        aiRelated: pattern.aiRelated,
        meetsCriteria: true,
      };

      evaluationNotes = pattern.successNote.replace('${prize}', `$${prizeAmount}`);
    } else {
      // Create a non-qualifying hackathon using failure patterns
      const failureIndex = index % MOCK_EVALUATION_PATTERNS.failures.length;
      const failurePattern = MOCK_EVALUATION_PATTERNS.failures[failureIndex];

      let registrationDeadline: Date | undefined;
      let endDate: Date | undefined;

      if (failurePattern.daysToRegistration) {
        registrationDeadline = addDays(currentDate, failurePattern.daysToRegistration);
      }

      if (failurePattern.weeksToEnd) {
        endDate = addWeeks(currentDate, failurePattern.weeksToEnd);
      } else if (failurePattern.daysToEnd) {
        endDate = addDays(currentDate, failurePattern.daysToEnd);
      }

      hackathon = {
        name: title,
        url: result.url,
        registrationDeadline,
        endDate,
        prizeUsd: failurePattern.prizeUsd,
        soloFriendly: failurePattern.soloFriendly,
        description: this.extractDescription(result.snippet),
        aiRelated: failurePattern.aiRelated,
        meetsCriteria: false,
      };

      evaluationNotes = `❌ Mock evaluation: ${failurePattern.reason}`;
    }

    return {
      hackathon,
      evaluationNotes,
      currentDateContext: currentDate.toISOString().split('T')[0],
    };
  }

  /**
   * Extract a clean description from the snippet
   */
  private extractDescription(snippet: string): string {
    return snippet
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 200) + (snippet.length > 200 ? '...' : '');
  }
}