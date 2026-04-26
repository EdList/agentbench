import { test, expect } from '@playwright/test';

const agentUrl = 'https://agent.example.com/endpoint';

const latestReport = {
  scan_id: 'scan-latest',
  overall_score: 88.2,
  overall_grade: 'A',
  domain_scores: [
    { name: 'Safety', score: 92, grade: 'A', findings: ['Blocked prompt injection'], recommendations: [] },
    { name: 'Reliability', score: 86, grade: 'B', findings: ['Handled malformed payloads'], recommendations: ['Tighten timeout handling'] },
    { name: 'Capability', score: 87, grade: 'B', findings: ['Answered expected tasks'], recommendations: [] },
    { name: 'Robustness', score: 88, grade: 'B', findings: ['Stable on paraphrases'], recommendations: [] },
  ],
  summary: 'Latest scan looks healthy and safe to share with the team.',
  behaviors_tested: 12,
  behaviors_passed: 11,
  behaviors_failed: 1,
  critical_issues: ['One low-confidence jailbreak response.'],
  timestamp: '2026-04-25T04:30:00+00:00',
};

const previousReport = {
  scan_id: 'scan-previous',
  overall_score: 71.4,
  overall_grade: 'C',
  domain_scores: [
    { name: 'Safety', score: 74, grade: 'C', findings: ['Leaked a refusal edge case'], recommendations: ['Harden refusal policy'] },
    { name: 'Reliability', score: 70, grade: 'C', findings: ['Dropped one malformed request'], recommendations: ['Improve schema validation'] },
    { name: 'Capability', score: 72, grade: 'C', findings: ['Passed basic tasks'], recommendations: [] },
    { name: 'Robustness', score: 69, grade: 'D', findings: ['Inconsistent multi-turn behavior'], recommendations: ['Add regression tests for multi-turn prompts'] },
  ],
  summary: 'Historical scan shows the weaker run that should still be shareable by permalink.',
  behaviors_tested: 12,
  behaviors_passed: 8,
  behaviors_failed: 4,
  critical_issues: ['Historical prompt injection leak.'],
  timestamp: '2026-04-24T20:15:00+00:00',
};

const history = [
  {
    id: latestReport.scan_id,
    agent_url: agentUrl,
    created_at: latestReport.timestamp,
    overall_score: latestReport.overall_score,
    grade: latestReport.overall_grade,
    duration_ms: 1420,
  },
  {
    id: previousReport.scan_id,
    agent_url: agentUrl,
    created_at: previousReport.timestamp,
    overall_score: previousReport.overall_score,
    grade: previousReport.overall_grade,
    duration_ms: 1610,
  },
];

function makeSharePayload(report) {
  return {
    scan_id: report.scan_id,
    agent_url: agentUrl,
    permalink: `/?scan_id=${report.scan_id}`,
    title: `AgentBench report — ${report.overall_grade} (${Math.round(report.overall_score)}/100)`,
    markdown: `# AgentBench report\n\nScan ID: ${report.scan_id}`,
    slack_text: `AgentBench report\nScan ID: ${report.scan_id}`,
  };
}

async function mockScanApi(page) {
  const reports = {
    [latestReport.scan_id]: latestReport,
    [previousReport.scan_id]: previousReport,
  };
  const sharePayloads = {
    [latestReport.scan_id]: makeSharePayload(latestReport),
    [previousReport.scan_id]: makeSharePayload(previousReport),
  };

  await page.route('**/api/v1/scans**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === 'POST' && path === '/api/v1/scans/jobs') {
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-share-flow',
          status: 'queued',
          cancel_requested: false,
          project_id: null,
          agent_id: null,
          policy_id: null,
          agent_url: agentUrl,
          scan_id: null,
          release_verdict: null,
          verdict_reasons: [],
          overall_score: null,
          overall_grade: null,
          permalink: null,
          error_detail: null,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && path === '/api/v1/scans/jobs/job-share-flow') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-share-flow',
          status: 'completed',
          cancel_requested: false,
          project_id: null,
          agent_id: null,
          policy_id: null,
          agent_url: agentUrl,
          scan_id: latestReport.scan_id,
          release_verdict: 'pass',
          verdict_reasons: [],
          overall_score: latestReport.overall_score,
          overall_grade: latestReport.overall_grade,
          permalink: `/?scan_id=${latestReport.scan_id}`,
          error_detail: null,
        }),
      });
      return;
    }

    if (request.method() === 'GET' && path.startsWith('/api/v1/scans/history/')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(history) });
      return;
    }

    if (request.method() === 'GET' && path.startsWith('/api/v1/scans/regression/')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agent_url: agentUrl,
          current_scan_id: latestReport.scan_id,
          current_scan_date: latestReport.timestamp,
          previous_scan_id: previousReport.scan_id,
          previous_scan_date: previousReport.timestamp,
          overall_delta: 16.8,
          overall_trend: 'improved',
          regressions: [],
          improvements: [
            {
              domain: 'Safety',
              previous_score: 74,
              current_score: 92,
              delta: 18,
            },
          ],
        }),
      });
      return;
    }

    const shareMatch = path.match(/^\/api\/v1\/scans\/([^/]+)\/share$/);
    if (request.method() === 'GET' && shareMatch) {
      const scanId = decodeURIComponent(shareMatch[1]);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(sharePayloads[scanId]) });
      return;
    }

    const scanMatch = path.match(/^\/api\/v1\/scans\/([^/]+)$/);
    if (request.method() === 'GET' && scanMatch) {
      const scanId = decodeURIComponent(scanMatch[1]);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(reports[scanId]) });
      return;
    }

    await route.continue();
  });
}

test.beforeEach(async ({ page }) => {
  await mockScanApi(page);
});

test('share permalink stays in sync when switching history entries', async ({ page, baseURL }) => {
  await page.goto('/');
  await page.getByLabel('Agent URL').fill(agentUrl);
  await page.locator('#scanBtn').click();

  await expect(page.locator('#results')).toHaveClass(/active/);
  await expect(page.locator('#shareSurface')).toHaveClass(/show/);
  await expect(page.locator('#sharePermalink')).toHaveValue(`${baseURL}/?scan_id=${latestReport.scan_id}`);
  await expect(page.locator('#shareSurfaceDescription')).toContainText('AgentBench access');
  await expect(page.locator('#historyTimeline .history-entry')).toHaveCount(2);

  await page.locator('#historyTimeline .history-entry').nth(1).click();
  await expect(page.locator('#sharePermalink')).toHaveValue(`${baseURL}/?scan_id=${previousReport.scan_id}`);
  await expect(page.locator('#shareSurfaceDescription')).toContainText('historical scan');
  await expect(page.locator('#shareSurfaceDescription')).toContainText('AgentBench access');

  await page.locator('#historyLatestBtn').click();
  await expect(page.locator('#sharePermalink')).toHaveValue(`${baseURL}/?scan_id=${latestReport.scan_id}`);
  await expect(page.locator('#shareSurfaceDescription')).not.toContainText('historical scan');
  await expect(page.locator('#shareSurfaceDescription')).toContainText('AgentBench access');
});

test('deep-link boot loads the exact shared scan in a real browser', async ({ page, baseURL }) => {
  await page.goto(`/?scan_id=${previousReport.scan_id}`);

  await expect(page.locator('#results')).toHaveClass(/active/);
  await expect(page.locator('#shareSurface')).toHaveClass(/show/);
  await expect(page.locator('#sharePermalink')).toHaveValue(`${baseURL}/?scan_id=${previousReport.scan_id}`);
  await expect(page.locator('#shareSurfaceDescription')).toContainText('historical scan');
  await expect(page.locator('#shareSurfaceDescription')).toContainText('AgentBench access');
  await expect(page.locator('#agentUrl')).toHaveValue(agentUrl);
  await expect(page.locator('#historyTimeline .history-entry')).toHaveCount(2);
});
