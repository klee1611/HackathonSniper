/**
 * Hackathon Discovery Agent - Main Orchestrator
 *
 * Preserves the excellent 3-phase pipeline architecture from Python version
 * while leveraging Node.js/TypeScript strengths
 */

import { AgentConfig, AgentConfigSchema, PipelineResults, validateProductionConfig } from '../schemas/index.js';
import { createMCPClient, CreateMCPClientFn } from '../mcp/factories.js';
import { createEvaluator, CreateEvaluatorFn } from '../evaluator/factories.js';
import { SearchPhase } from './phases/SearchPhase.js';
import { EvaluationPhase } from './phases/EvaluationPhase.js';
import { StoragePhase } from './phases/StoragePhase.js';
import { Logger } from '../utils/logger.js';
import { IMCPClient } from '../mcp/interfaces.js';
import { IEvaluator } from '../evaluator/interfaces.js';

const logger = Logger.create('HackathonDiscoveryAgent');

/**
 * Main agent orchestrator - preserves excellent patterns from Python version
 */
export class HackathonDiscoveryAgent {
  private readonly config: AgentConfig;
  private readonly searchPhase: SearchPhase;
  private readonly evaluationPhase: EvaluationPhase;
  private readonly storagePhase: StoragePhase;
  private readonly mcpClient: IMCPClient;
  private readonly evaluator: IEvaluator;

  /**
   * Private constructor - use create() factory method instead
   */
  private constructor(
    config: AgentConfig,
    mcpClient: IMCPClient,
    evaluator: IEvaluator
  ) {
    this.config = config;
    this.mcpClient = mcpClient;
    this.evaluator = evaluator;

    // Initialize 3-phase pipeline (preserves exact architecture)
    this.searchPhase = new SearchPhase(this.mcpClient);
    this.evaluationPhase = new EvaluationPhase(this.evaluator);
    this.storagePhase = new StoragePhase(this.mcpClient, this.config.notionDatabaseId);

    logger.info('✅ Agent initialization complete');
  }

  /**
   * Async factory method to create agent with proper async dependency injection
   */
  static async create(configInput: unknown): Promise<HackathonDiscoveryAgent> {
    // Validate and parse configuration (preserves Python validation pattern)
    const config = AgentConfigSchema.parse(configInput);
    validateProductionConfig(config);

    logger.info('🤖 Initializing Hackathon Discovery Agent');
    logger.info(`📊 Environment: ${config.environment}`);
    logger.info(`🎭 Mock mode: ${config.environment === 'test'}`);

    // Initialize components with async dependency injection
    const mcpClient = await createMCPClient(config);
    const evaluator = await createEvaluator(config);

    return new HackathonDiscoveryAgent(config, mcpClient, evaluator);
  }

  /**
   * Execute the complete 3-phase hackathon discovery pipeline
   * Preserves exact pipeline flow from Python version
   */
  async runDiscoveryPipeline(): Promise<PipelineResults> {
    const executionDate = new Date().toISOString().split('T')[0];

    logger.info(`🚀 Starting Hackathon Discovery Pipeline - Date: ${executionDate}`);

    const results: PipelineResults = {
      executionDate,
      totalSearchResults: 0,
      qualifiedHackathons: 0,
      storedHackathons: 0,
      success: false,
    };

    try {
      // Initialize MCP connections
      await this.mcpClient.initialize();

      try {
        // Phase 1: Search
        logger.info('🔍 Phase 1: Search');
        const searchResults = await this.searchPhase.execute(
          this.config.searchQueries,
          this.config.maxResults,
          this.config.delayBetweenQueries
        );
        results.totalSearchResults = searchResults.length;

        if (searchResults.length === 0) {
          logger.warn('⚠️  No search results found, pipeline terminated');
          results.errorMessage = 'No search results found';
          return results;
        }

        // Phase 2: Evaluation
        logger.info('🤖 Phase 2: Evaluation');
        const qualifiedHackathons = await this.evaluationPhase.execute(
          searchResults,
          this.config.batchSize,
          this.config.delayBetweenBatches
        );
        results.qualifiedHackathons = qualifiedHackathons.length;

        if (qualifiedHackathons.length === 0) {
          logger.warn('⚠️  No qualified hackathons found');
          results.success = true; // Not an error, just no qualifying events
          return results;
        }

        // Phase 3: Storage
        logger.info('💾 Phase 3: Storage');
        const storedCount = await this.storagePhase.execute(qualifiedHackathons);
        results.storedHackathons = storedCount;

        results.success = true;
        logger.info('🎉 Hackathon Discovery Pipeline completed successfully');

        // Log final summary
        logger.info('📊 Pipeline Summary:');
        logger.info(`   🔍 Search results: ${results.totalSearchResults}`);
        logger.info(`   ✅ Qualified: ${results.qualifiedHackathons}`);
        logger.info(`   💾 Stored: ${results.storedHackathons}`);

        return results;
      } finally {
        // Always disconnect MCP clients
        await this.mcpClient.disconnect();
      }
    } catch (error) {
      logger.error('❌ Pipeline failed with error', error);
      results.errorMessage = error instanceof Error ? error.message : String(error);

      // Attempt to disconnect even after errors
      try {
        await this.mcpClient.disconnect();
      } catch (disconnectError) {
        logger.error('❌ Error during cleanup', disconnectError);
      }

      return results;
    }
  }

  /**
   * Run a single search query for testing purposes
   * Preserves test functionality from Python version
   */
  async runSingleQueryTest(query: string): Promise<any> {
    logger.info(`🧪 Running single query test: "${query}"`);

    try {
      await this.mcpClient.initialize();

      try {
        // Single search
        const searchResults = await this.mcpClient.searchHackathons(query, 5);
        logger.info(`🔍 Search returned ${searchResults.length} results`);

        // Evaluate results if we have any
        let qualifiedCount = 0;
        let hackathons: any[] = [];

        if (searchResults.length > 0) {
          const evaluationResults = await this.evaluator.evaluateSearchResults(searchResults, query);
          qualifiedCount = evaluationResults.qualifiedCount;
          hackathons = evaluationResults.hackathons
            .filter(h => h.hackathon.meetsCriteria)
            .map(h => h.hackathon);

          logger.info(`🤖 Evaluation found ${qualifiedCount} qualified hackathons`);
        }

        return {
          query,
          searchResultsCount: searchResults.length,
          qualifiedCount,
          hackathons,
          success: true,
        };
      } finally {
        await this.mcpClient.disconnect();
      }
    } catch (error) {
      logger.error('❌ Test query failed', error);
      return {
        query,
        error: error instanceof Error ? error.message : String(error),
        success: false,
      };
    }
  }

  /**
   * Get agent configuration (useful for debugging)
   */
  getConfig(): AgentConfig {
    return { ...this.config };
  }

  /**
   * Check if agent is in mock mode
   */
  isMockMode(): boolean {
    return this.config.environment === 'test';
  }
}