/**
 * Setup script for development environment
 */

import { promises as fs } from 'fs';
import { join } from 'path';

async function setupDevelopmentEnvironment(): Promise<void> {
  console.log('🛠️  Setting up Hackathon Discovery Agent development environment...');

  try {
    // Create logs directory for production logging
    const logsDir = join(process.cwd(), 'logs');
    try {
      await fs.access(logsDir);
    } catch {
      await fs.mkdir(logsDir, { recursive: true });
      console.log('📁 Created logs directory');
    }

    // Check if .env exists, if not copy from example
    try {
      await fs.access('.env');
      console.log('✅ .env file already exists');
    } catch {
      await fs.copyFile('.env.example', '.env');
      console.log('📋 Created .env from template');
      console.log('⚠️  Remember to edit .env with your real API keys!');
    }

    // Verify test environment
    try {
      await fs.access('.env.test');
      console.log('✅ Test environment configured');
    } catch {
      console.log('⚠️  .env.test not found, mock testing may not work');
    }

    console.log('\n🎉 Development environment setup complete!');
    console.log('\nNext steps:');
    console.log('1. Edit .env with your API keys (GROQ_API_KEY, NOTION_API_KEY, NOTION_DATABASE_ID)');
    console.log('2. Run "make test-mock" to test with zero dependencies');
    console.log('3. Run "make dev" to start development mode');
  } catch (error) {
    console.error('❌ Setup failed:', error);
    process.exit(1);
  }
}

// Run setup if this file is executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  setupDevelopmentEnvironment();
}