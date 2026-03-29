/**
 * Storage Phase - Third phase of the 3-phase pipeline
 *
 * Handles storage of qualified hackathons in Notion with retry logic
 * Preserves exact storage logic and error handling from Python version
 */

import { IMCPClient } from '../../mcp/interfaces.js';
import { HackathonDetails, NotionPage, toNotionProperties } from '../../schemas/index.js';
import { Logger } from '../../utils/logger.js';
import { withRetry } from '../../utils/retry.js';

const logger = Logger.create('StoragePhase');

export class StoragePhase {
  constructor(
    private mcpClient: IMCPClient,
    private notionDatabaseId: string
  ) {}

  /**
   * Execute storage phase for qualified hackathons
   * Preserves exact storage logic from Python version
   */
  async execute(
    qualifiedHackathons: HackathonDetails[],
    delayBetweenStores: number = 1000
  ): Promise<number> {
    logger.info(`💾 Starting storage phase for ${qualifiedHackathons.length} qualified hackathons`);

    if (qualifiedHackathons.length === 0) {
      logger.info('ℹ️  No qualified hackathons to store');
      return 0;
    }

    let storedCount = 0;

    for (const [index, hackathon] of qualifiedHackathons.entries()) {
      try {
        logger.info(`📝 Storing hackathon ${index + 1}/${qualifiedHackathons.length}: ${hackathon.name}`);

        // Convert hackathon to Notion page format (preserves Python logic)
        const notionPage: NotionPage = {
          title: hackathon.name,
          url: hackathon.url,
          registrationDeadline: hackathon.registrationDeadline,
          endDate: hackathon.endDate,
          prizeUsd: hackathon.prizeUsd ?? undefined,
          soloFriendly: hackathon.soloFriendly ?? false,
          description: hackathon.description,
        };

        // Create page in Notion with retry logic
        const success = await withRetry(
          async () => {
            const properties = toNotionProperties(notionPage);
            return await this.mcpClient.createNotionPage(this.notionDatabaseId, properties);
          },
          {
            maxRetries: 3,
            baseDelay: 1000,
          }
        );

        if (success) {
          storedCount++;
          logger.info(`✅ Stored hackathon: ${hackathon.name}`);

          // Log key details
          if (hackathon.prizeUsd) {
            logger.debug(`   💰 Prize: $${hackathon.prizeUsd}`);
          }
          if (hackathon.registrationDeadline) {
            logger.debug(`   📅 Deadline: ${hackathon.registrationDeadline.toISOString().split('T')[0]}`);
          }
          if (hackathon.soloFriendly) {
            logger.debug(`   👤 Solo-friendly: Yes`);
          }
        } else {
          logger.error(`❌ Failed to store hackathon: ${hackathon.name}`);
        }

        // Add delay between API calls to be respectful (except for last item)
        if (delayBetweenStores > 0 && index < qualifiedHackathons.length - 1) {
          logger.debug(`⏳ Waiting ${delayBetweenStores}ms before next store`);
          await new Promise(resolve => setTimeout(resolve, delayBetweenStores));
        }
      } catch (error) {
        logger.error(`❌ Error storing hackathon ${hackathon.name}`, error);
        // Continue with next hackathon instead of failing entire storage phase
        continue;
      }
    }

    logger.info(`✅ Storage phase complete: ${storedCount}/${qualifiedHackathons.length} hackathons stored`);

    // Log summary statistics
    if (storedCount > 0) {
      const successRate = ((storedCount / qualifiedHackathons.length) * 100).toFixed(1);
      logger.info(`📊 Storage success rate: ${successRate}%`);
    }

    return storedCount;
  }

  /**
   * Store a single hackathon (useful for testing or manual storage)
   */
  async storeSingle(hackathon: HackathonDetails): Promise<boolean> {
    try {
      logger.info(`📝 Storing single hackathon: ${hackathon.name}`);

      const notionPage: NotionPage = {
        title: hackathon.name,
        url: hackathon.url,
        registrationDeadline: hackathon.registrationDeadline,
        endDate: hackathon.endDate,
        prizeUsd: hackathon.prizeUsd ?? undefined,
        soloFriendly: hackathon.soloFriendly ?? false,
        description: hackathon.description,
      };

      const success = await withRetry(async () => {
        const properties = toNotionProperties(notionPage);
        return await this.mcpClient.createNotionPage(this.notionDatabaseId, properties);
      });

      if (success) {
        logger.info(`✅ Successfully stored: ${hackathon.name}`);
      } else {
        logger.error(`❌ Failed to store: ${hackathon.name}`);
      }

      return success;
    } catch (error) {
      logger.error(`❌ Error storing single hackathon: ${hackathon.name}`, error);
      return false;
    }
  }
}