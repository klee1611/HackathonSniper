/**
 * Mock hackathon fixtures for testing
 * Realistic test data for zero-dependency testing
 */

import { SearchResult } from '../../src/schemas/index';

export const MOCK_HACKATHON_FIXTURES: SearchResult[] = [
  {
    title: "AI Innovation Challenge 2026",
    url: "https://devpost.com/software/ai-innovation-2026",
    snippet: "Join us for a 48-hour AI hackathon with $5,000 in prizes. Registration deadline: April 5, 2026. Individual participation encouraged! Focus on machine learning and generative AI.",
    relevanceScore: 0.95,
  },
  {
    title: "Future AI Hackathon - Spring 2026",
    url: "https://dorahacks.io/hackathon/ai-future-2026",
    snippet: "Exciting AI hackathon with $10,000 prize pool. Register by April 8, 2026. Event runs April 15-17, 2026. Solo developers welcome. Build the next generation of AI applications.",
    relevanceScore: 0.92,
  },
  {
    title: "Machine Learning Challenge",
    url: "https://mlhackathon.com/spring2026",
    snippet: "30-day ML hackathon starting April 1, 2026. $3,000 in prizes. Registration open until March 31, 2026. Individual and team participation allowed.",
    relevanceScore: 0.88,
  },
  {
    title: "OpenAI Developer Challenge",
    url: "https://platform.openai.com/hackathon/2026",
    snippet: "Build innovative AI applications. $15,000 total prizes. Registration closes April 10, 2026. Event: April 20-22, 2026. Solo participation encouraged for creative solutions.",
    relevanceScore: 0.90,
  },
  {
    title: "Computer Vision Hackathon",
    url: "https://cvhack.tech/2026",
    snippet: "CV and AI hackathon with $2,500 prizes. Runs March 30 - April 2, 2026. Registration deadline: March 29, 2026 (today!). Individual developers welcome.",
    relevanceScore: 0.85,
  },
  {
    title: "Blockchain AI Fusion",
    url: "https://blockchainhacks.com/ai2026",
    snippet: "Combining blockchain and AI. $8,000 prize pool. Teams only, no individual participation. April 12-14, 2026.",
    relevanceScore: 0.75,
  },
  {
    title: "Low Prize Hackathon",
    url: "https://example.com/lowprize",
    snippet: "Small local hackathon with $100 prize. Great for beginners! Solo participation welcome.",
    relevanceScore: 0.40,
  },
  {
    title: "Past Event Example",
    url: "https://example.com/past",
    snippet: "Amazing hackathon that happened in February 2026. $5,000 in prizes. Individual participation was welcome.",
    relevanceScore: 0.25,
  },
];