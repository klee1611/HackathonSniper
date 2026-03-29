/**
 * Mock evaluation response patterns for testing
 * Provides realistic evaluation scenarios without hardcoded data in src/
 */

export const MOCK_EVALUATION_PATTERNS = {
  // 放寬的合格 hackathon 模式 (更多會通過篩選)
  qualifying: {
    basePrize: 1000, // 降低最低獎金要求
    prizeIncrement: 1000,
    daysTillRegistration: 5, // 稍微放寬註冊時間
    weeksToEnd: 4, // 延長到4週內
    soloFriendly: true,
    aiRelated: true,
    successNote: "✅ 放寬條件評估: AI相關且符合時間限制 - 獎金: ${prize}, 時程合適, AI相關度高"
  },

  // Failure patterns with realistic reasons
  failures: [
    {
      reason: 'Prize pool too low (under $300)',
      prizeUsd: 100,
      soloFriendly: true,
      daysToRegistration: 5,
      weeksToEnd: 2,
      aiRelated: true
    },
    {
      reason: 'No solo participation allowed - teams only',
      prizeUsd: 5000,
      soloFriendly: false, // This causes failure
      daysToRegistration: 5,
      weeksToEnd: 2,
      aiRelated: true
    },
    {
      reason: 'Registration deadline too soon (less than 2 days)',
      prizeUsd: 5000,
      soloFriendly: true,
      daysToRegistration: 1, // This causes failure
      weeksToEnd: 2,
      aiRelated: true
    },
    {
      reason: 'Event timing outside acceptable range',
      prizeUsd: 5000,
      soloFriendly: true,
      daysToRegistration: 5,
      daysToEnd: 45, // This causes failure (too far out)
      aiRelated: true
    },
    {
      reason: 'Not AI-related or unclear focus',
      prizeUsd: 5000,
      soloFriendly: true,
      daysToRegistration: 5,
      weeksToEnd: 2,
      aiRelated: false // This causes failure
    }
  ]
};