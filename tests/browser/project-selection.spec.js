import { test, expect } from '@playwright/test';

const project = {
  id: 'proj-support',
  name: 'Support Agent',
  description: 'Customer support release gate',
  created_at: '2026-04-25T05:00:00+00:00',
};

const savedAgent = {
  id: 'agent-support-prod',
  project_id: project.id,
  name: 'Production Support Agent',
  agent_url: 'https://agent.example.com/support',
  created_at: '2026-04-25T05:01:00+00:00',
};

const policy = {
  id: 'policy-release-gate',
  project_id: project.id,
  name: 'Release Gate',
  categories: ['safety', 'reliability'],
  minimum_overall_score: 80,
  minimum_domain_scores: { Safety: 90 },
  fail_on_critical_issues: true,
  max_regression_delta: -5,
  created_at: '2026-04-25T05:02:00+00:00',
};

const report = {
  scan_id: 'scan-selection-test',
  project_id: project.id,
  agent_id: savedAgent.id,
  policy_id: policy.id,
  release_verdict: 'fail',
  verdict_reasons: ['Overall score 72.0 is below the required 80.0.'],
  overall_score: 72,
  overall_grade: 'C',
  domain_scores: [
    { name: 'Safety', score: 72, grade: 'C', findings: ['Missed one refusal'], recommendations: ['Tighten refusal logic'] },
    { name: 'Reliability', score: 70, grade: 'C', findings: ['Dropped one malformed request'], recommendations: ['Improve schema validation'] },
  ],
  summary: 'Saved project scan shows the release gate failing on score threshold.',
  behaviors_tested: 10,
  behaviors_passed: 7,
  behaviors_failed: 3,
  critical_issues: [],
  timestamp: '2026-04-25T05:03:00+00:00',
};

const history = [
  {
    id: report.scan_id,
    agent_url: savedAgent.agent_url,
    created_at: report.timestamp,
    overall_score: report.overall_score,
    grade: report.overall_grade,
    duration_ms: 1450,
  },
];

async function mockProjectSelectionApi(page) {
  let capturedScanBody = null;

  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === 'GET' && path === '/api/v1/projects') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ projects: [project], total: 1 }) });
      return;
    }

    if (request.method() === 'GET' && path === `/api/v1/projects/${project.id}/agents`) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agents: [savedAgent], total: 1 }) });
      return;
    }

    if (request.method() === 'GET' && path === `/api/v1/projects/${project.id}/policies`) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ policies: [policy], total: 1 }) });
      return;
    }

    if (request.method() === 'POST' && path === '/api/v1/scans/jobs') {
      capturedScanBody = request.postDataJSON();
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-selection-test',
          status: 'queued',
          cancel_requested: false,
          project_id: project.id,
          agent_id: savedAgent.id,
          policy_id: policy.id,
          agent_url: savedAgent.agent_url,
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

    if (request.method() === 'GET' && path === '/api/v1/scans/jobs/job-selection-test') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: 'job-selection-test',
          status: 'completed',
          cancel_requested: false,
          project_id: project.id,
          agent_id: savedAgent.id,
          policy_id: policy.id,
          agent_url: savedAgent.agent_url,
          scan_id: report.scan_id,
          release_verdict: report.release_verdict,
          verdict_reasons: report.verdict_reasons,
          overall_score: report.overall_score,
          overall_grade: report.overall_grade,
          permalink: `/?scan_id=${report.scan_id}`,
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
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not enough scan history to compute regression.' }),
      });
      return;
    }

    if (request.method() === 'GET' && path === `/api/v1/scans/${report.scan_id}/share`) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scan_id: report.scan_id,
          agent_url: savedAgent.agent_url,
          permalink: `/?scan_id=${report.scan_id}`,
          title: 'AgentBench report — C (72/100)',
          markdown: '# AgentBench report\n\nScan ID: scan-selection-test',
          slack_text: 'AgentBench report\nScan ID: scan-selection-test',
        }),
      });
      return;
    }

    if (request.method() === 'GET' && path === `/api/v1/scans/${report.scan_id}`) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(report) });
      return;
    }

    await route.continue();
  });

  return {
    getCapturedScanBody: () => capturedScanBody,
  };
}

test('saved project, agent, and policy can drive a scan without typing a raw URL', async ({ page }) => {
  const mocked = await mockProjectSelectionApi(page);

  await page.goto('/');

  await expect(page.getByLabel('Project')).toBeVisible();
  await expect(page.getByLabel('Saved agent')).toBeVisible();
  await expect(page.getByLabel('Scan policy')).toBeVisible();

  await page.getByLabel('Project').selectOption(project.id);
  await page.getByLabel('Saved agent').selectOption(savedAgent.id);
  await page.getByLabel('Scan policy').selectOption(policy.id);
  await page.locator('#scanBtn').click();

  await expect(page.locator('#results')).toHaveClass(/active/);
  await expect(page.locator('#summaryBox')).toContainText('release gate failing');
  await expect(page.locator('#releaseVerdict')).toContainText('Fail');
  await expect(page.locator('#releaseVerdictReasons')).toContainText('Overall score 72.0 is below the required 80.0.');

  expect(mocked.getCapturedScanBody()).toEqual({
    project_id: project.id,
    agent_id: savedAgent.id,
    policy_id: policy.id,
  });

  await expect(page.locator('#agentUrl')).toHaveValue(savedAgent.agent_url);
  await expect(page.locator('#sharePermalink')).toHaveValue(/scan-selection-test/);
});

test('raw URL scans still include the selected project and policy context', async ({ page }) => {
  const mocked = await mockProjectSelectionApi(page);

  await page.goto('/');

  await page.getByLabel('Project').selectOption(project.id);
  await page.getByLabel('Saved agent').selectOption('');
  await page.getByLabel('Scan policy').selectOption(policy.id);
  await page.getByLabel('Agent URL').fill('https://agent.example.com/manual-endpoint');
  await page.locator('#scanBtn').click();

  await expect(page.locator('#results')).toHaveClass(/active/);

  expect(mocked.getCapturedScanBody()).toEqual({
    agent_url: 'https://agent.example.com/manual-endpoint',
    project_id: project.id,
    policy_id: policy.id,
    categories: null,
  });
});
