import { metricsView } from '../../../../services/control-plane/shiwei_control/static/metrics.js';

const empty = () => ({
  configured: true, window_days: 7, timezone: 'Asia/Shanghai', generated_at: '2026-09-06T04:00:00Z',
  last_received_at: null, storage: { events: 0, capacity: 200000, retention_days: 90 },
  overview: { recall_success_rate: null, import_failure_rate: null },
  trend: [], versions: [], errors: [], retention: [{ day: 1, eligible: 0, returned: 0, rate: null }],
  updates: { available: 0, started: 0, completed: 0, failed: 0 },
});
function render(data) {
  const el = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
  const button = (text, fn) => { const b = el('button', text); b.addEventListener('click', fn); return b; };
  const daysChanged = vi.fn(), refresh = vi.fn();
  document.body.replaceChildren(metricsView(data, { el, button, daysChanged, refresh }));
  return { daysChanged, refresh };
}
afterEach(() => document.body.replaceChildren());
it('empty dashboard explains coverage and never invents percentages', () => {
  render(empty());
  expect(document.body.textContent).toContain('看板已就绪，等待真实使用数据');
  expect(document.body.textContent).not.toContain('0.0%');
  expect(document.body.textContent).toContain('尚未收到');
});
it('date selector and refresh call their real handlers', () => {
  const { daysChanged, refresh } = render(empty());
  const select = document.querySelector('select'); select.value = '30'; select.dispatchEvent(new Event('change'));
  expect(daysChanged).toHaveBeenCalledWith(30);
  document.querySelector('button').click(); expect(refresh).toHaveBeenCalledOnce();
});
it('renders aggregates, version counts and mature retention accurately', () => {
  const data = empty(); data.last_received_at = data.generated_at;
  data.overview = { dau: 8, questions: 56, successful_recalls: 42, recall_success_rate: .75, import_failure_rate: .25 };
  data.versions = [{ version: '0.3.1', installations: 9 }];
  data.retention = [{ day: 30, eligible: 12, returned: 6, rate: .5 }];
  render(data);
  for (const value of ['75.0%', '25.0%', '50.0%', 'v0.3.1', 'D30']) expect(document.body.textContent).toContain(value);
  expect(document.body.textContent).not.toContain('等待真实使用数据');
});
it('unknown text is inert, not executable HTML', () => {
  const data = empty(); data.versions = [{ version: '<img src=x onerror=alert(1)>', installations: 1 }];
  render(data);
  expect(document.querySelector('img')).toBeNull();
  expect(document.body.textContent).toContain('<img src=x onerror=alert(1)>');
});
it('synthetic environment is visibly labeled and disabled ingestion is not presented as healthy', () => {
  render({ ...empty(), development: true, configured: false });
  expect(document.body.textContent).toContain('统计服务尚未配置');
  expect(document.body.textContent).toContain('合成测试数据');
});
