/**
 * Basic Pipeline Test
 */

import { HackathonDiscoveryAgent } from '../../src/agent/HackathonDiscoveryAgent';
import { AgentConfig } from '../../src/schemas/index';

describe('Pipeline Test', () => {
  const mockConfig: AgentConfig = {
    environment: 'test',
    groqApiKey: 'test_key',
    notionApiKey: 'test_key',
    notionDatabaseId: 'test_db_id',
    logLevel: 'error',
    searchQueries: ['AI hackathon test'],
    batchSize: 2,
    maxResults: 5,
    delayBetweenBatches: 100,
    delayBetweenQueries: 100,
  };

  test('should run pipeline', async () => {
    const agent = await HackathonDiscoveryAgent.create(mockConfig);
    const results = await agent.runDiscoveryPipeline();

    expect(results).toBeDefined();
    expect(results.executionDate).toBeDefined();
  }, 10000);
});