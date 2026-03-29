/**
 * Evaluation Phase - Second phase of the 3-phase pipeline
 *
 * Filters search results using rule-based checks for the 4 core constraints:
 *   1. AI Focus        - must be AI/ML-related
 *   2. Registration    - must not be closed/past
 *   3. Event Timeline  - must start within the next 90 days
 *   4. Team Flexibility- prefer solo-friendly; team-only is acceptable if AI-focused
 *
 * Groq is intentionally bypassed here to avoid API quota consumption.
 * The GroqEvaluator (which enforces the same criteria via LLM) remains available
 * for callers that want deeper extraction at the cost of quota.
 */

import { IEvaluator } from '../../evaluator/interfaces.js';
import { SearchResult, HackathonDetails } from '../../schemas/index.js';
import { Logger } from '../../utils/logger.js';

const logger = Logger.create('EvaluationPhase');

export class EvaluationPhase {
  constructor(private evaluator: IEvaluator) {}

  /**
   * Execute evaluation phase — rule-based filtering, no Groq quota consumed.
   */
  async execute(
    searchResults: SearchResult[],
    _batchSize: number = 5,
    _delayBetweenBatches: number = 3000
  ): Promise<HackathonDetails[]> {
    logger.info(`🤖 Starting evaluation phase for ${searchResults.length} search results`);

    if (searchResults.length === 0) {
      logger.warn('⚠️  No search results to evaluate');
      return [];
    }

    const qualifiedHackathons: HackathonDetails[] = [];

    for (const result of searchResults) {
      const reasons: string[] = [];

      // Constraint 1: AI Focus
      if (!this.isAIRelated(result)) {
        logger.debug(`⏭️  [AI Focus] Skipping non-AI result: ${result.title}`);
        continue;
      }

      // Constraint 2: Registration must still be open
      if (this.isRegistrationClosed(result)) {
        logger.debug(`⏭️  [Registration] Skipping closed/past registration: ${result.title}`);
        continue;
      }

      // Constraint 3: Event must start within 90 days
      if (this.isClearlyOutsideWindow(result)) {
        logger.debug(`⏭️  [Timeline] Skipping event outside 90-day window: ${result.title}`);
        continue;
      }

      reasons.push('AI-related');

      const hackathon = this.convertToHackathon(result);
      qualifiedHackathons.push(hackathon);
      logger.info(`✅ Qualified [${reasons.join(', ')}]: ${hackathon.name}`);
    }

    logger.info(`✅ Evaluation phase complete: ${qualifiedHackathons.length}/${searchResults.length} hackathons passed filters`);

    if (qualifiedHackathons.length > 0) {
      logger.info('📋 Hackathons to be stored:');
      qualifiedHackathons.forEach((h, i) => logger.info(`  ${i + 1}. ${h.name} — ${h.url}`));
    } else {
      logger.warn('⚠️  No hackathons passed all filters');
    }

    return qualifiedHackathons;
  }

  // ─── Constraint 1: AI Focus ───────────────────────────────────────────────

  private isAIRelated(result: SearchResult): boolean {
    const text = `${result.title} ${result.snippet}`.toLowerCase();

    // Use word-boundary matching for short/ambiguous terms to avoid false positives
    const aiPatterns: RegExp[] = [
      /\bai\b/, /\bml\b/, /\bllm\b/, /\bgpt\b/, /\bnlp\b/,
      /artificial intelligence/, /machine learning/, /deep learning/,
      /neural network/, /generative ai/, /computer vision/,
      /reinforcement learning/, /large language model/, /diffusion model/,
      /transformer model/,
    ];
    const isAI = aiPatterns.some(re => re.test(text));

    // Must also look like a hackathon/competition (not just any AI article)
    const eventKeywords = ['hackathon', 'competition', 'contest', 'challenge', 'track', 'prize', 'submit'];
    const isEvent = eventKeywords.some(kw => text.includes(kw));

    return isAI && isEvent;
  }

  // ─── Constraint 2: Registration still open ────────────────────────────────

  private isRegistrationClosed(result: SearchResult): boolean {
    const text = `${result.title} ${result.snippet}`.toLowerCase();
    const closedSignals = [
      'registration closed', 'registration has closed',
      'applications closed', 'submissions closed',
      'deadline passed', 'deadline has passed',
      'event has ended', 'hackathon ended', 'hackathon has ended',
      'competition ended', 'contest ended',
      'no longer accepting', 'no longer open',
    ];
    return closedSignals.some(s => text.includes(s));
  }

  // ─── Constraint 3: Within 90-day window ──────────────────────────────────

  /**
   * Returns true only when we can CONFIRM the event is outside the window.
   * If dates are ambiguous or missing, we stay lenient and include the result.
   */
  private isClearlyOutsideWindow(result: SearchResult): boolean {
    const text = `${result.title} ${result.snippet}`;
    const now = new Date();
    const windowEnd = new Date(now.getTime() + 90 * 24 * 60 * 60 * 1000);
    const currentYear = now.getFullYear(); // 2026

    // Reject results that only mention years clearly in the past
    const yearMatches = text.match(/\b(202\d)\b/g);
    if (yearMatches) {
      const years = yearMatches.map(Number);
      const maxYear = Math.max(...years);
      if (maxYear < currentYear) {
        return true; // e.g. only mentions 2024 or 2025
      }
    }

    // Try to parse a specific date from common patterns (e.g. "April 15, 2026", "2026-04-15")
    const extractedDate = this.extractEarliestDate(text);
    if (extractedDate) {
      // Reject if start date is more than 90 days away or already in the past
      if (extractedDate < now || extractedDate > windowEnd) {
        return true;
      }
    }

    return false; // Uncertain → be lenient, include the result
  }

  /**
   * Attempt to extract the earliest plausible event/deadline date from text.
   * Returns null when no confident date can be parsed.
   */
  private extractEarliestDate(text: string): Date | null {
    const candidates: Date[] = [];

    // ISO date: 2026-04-15
    const isoPattern = /\b(\d{4})-(\d{2})-(\d{2})\b/g;
    let m: RegExpExecArray | null;
    while ((m = isoPattern.exec(text)) !== null) {
      const d = new Date(`${m[1]}-${m[2]}-${m[3]}`);
      if (!isNaN(d.getTime())) candidates.push(d);
    }

    // Verbose: "April 15, 2026" / "15 April 2026"
    const monthNames = 'January|February|March|April|May|June|July|August|September|October|November|December';
    const verbosePattern = new RegExp(
      `\\b(?:(${monthNames})\\s+(\\d{1,2}),?\\s+(\\d{4})|(\\d{1,2})\\s+(${monthNames}),?\\s+(\\d{4}))\\b`,
      'gi'
    );
    while ((m = verbosePattern.exec(text)) !== null) {
      const str = m[1]
        ? `${m[1]} ${m[2]} ${m[3]}`
        : `${m[5]} ${m[4]} ${m[6]}`;
      const d = new Date(str);
      if (!isNaN(d.getTime())) candidates.push(d);
    }

    if (candidates.length === 0) return null;
    return candidates.reduce((a, b) => (a < b ? a : b));
  }

  // ─── Constraint 4: Team flexibility (best-effort from text) ──────────────

  private detectSoloFriendly(result: SearchResult): boolean | null {
    const text = `${result.title} ${result.snippet}`.toLowerCase();

    if (/\b(solo|individual|alone|single.person|one.person|go alone)\b/.test(text)) return true;
    if (/\bteam.?(only|required|mandatory|minimum [2-9])\b/.test(text)) return false;
    if (/\bminimum (?:2|3|4|5) (members?|people|participants?)\b/.test(text)) return false;

    // If hackathon is strongly AI-focused, solo restriction is less relevant — keep null
    return null;
  }

  // ─── Conversion ──────────────────────────────────────────────────────────

  private convertToHackathon(result: SearchResult): HackathonDetails {
    const extractedDate = this.extractEarliestDate(`${result.title} ${result.snippet}`);

    return {
      name: result.title,
      url: result.url,
      description: result.snippet || 'AI-related hackathon discovered via search',
      aiRelated: true,
      meetsCriteria: true,
      // Best-effort date extraction; undefined = unknown
      registrationDeadline: extractedDate ?? undefined,
      endDate: undefined,
      prizeUsd: this.extractPrize(result.snippet),
      soloFriendly: this.detectSoloFriendly(result),
    };
  }

  /** Extract a USD prize amount from snippet text if present. */
  private extractPrize(text: string): number | null {
    // Matches: "$50k", "$50,000", "$ 10000"
    const m = text.match(/\$\s*([\d,]+)(k)?/i);
    if (!m) return null;
    let amount = parseInt(m[1].replace(/,/g, ''), 10);
    if (m[2]) amount *= 1000; // "k" suffix
    return amount > 0 ? amount : null;
  }

  /**
   * Evaluate a single result (useful for unit testing or ad-hoc use).
   */
  async evaluateSingle(searchResult: SearchResult): Promise<HackathonDetails | null> {
    const results = await this.execute([searchResult]);
    return results[0] ?? null;
  }
}
