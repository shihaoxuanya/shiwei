// Aggregate-only UI. Never request or render event payloads / installation profiles.
export function metricsView(data, { el, button, daysChanged, refresh }) {
  const fragment = document.createDocumentFragment();
  const num = n => Number(n || 0).toLocaleString('zh-CN');
  const percent = n => n === null ? '—' : `${(n * 100).toFixed(1)}%`;
  const stamp = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未收到';
  const bar = el('div', undefined, 'toolbar');
  const intro = el('div'); intro.append(el('h2', '了解拾微的实际使用情况'), el('p', '仅汇总基础事件，不读取文件、笔记、提问或回答。', 'muted'));
  const controls = el('div', undefined, 'actions');
  const label = el('label', '统计时段', 'period-label'), select = el('select'); select.setAttribute('aria-label', '统计时段');
  for (const n of [7, 30]) { const option = el('option', `最近 ${n} 天`); option.value = n; option.selected = n === data.window_days; select.append(option); }
  select.addEventListener('change', () => daysChanged(Number(select.value))); label.append(select);
  controls.append(label, button('刷新数据', refresh)); bar.append(intro, controls); fragment.append(bar);
  const status = el('section', undefined, `collection-status ${data.configured ? '' : 'warning'}`);
  status.append(el('strong', data.configured ? '统计接收已启用' : '统计服务尚未配置'));
  status.append(el('span', `最近收到事件：${stamp(data.last_received_at)} · 每 60 秒刷新`, 'muted'));
  fragment.append(status);
  if (data.development) fragment.append(el('p', '隔离验收环境 · 以下为合成测试数据，不代表真实使用情况。', 'muted'));
  if (!data.last_received_at) {
    const empty = el('section', undefined, 'panel empty-metrics');
    empty.append(el('h2', '看板已就绪，等待真实使用数据'), el('p', '支持统计的桌面版本联网并开启“帮助改进拾微”后，数据会自动出现在这里。旧版或已关闭统计的客户端不会上报；不会补传历史使用记录。', 'muted'));
    fragment.append(empty);
  }
  const o = data.overview, stats = el('section', undefined, 'stats metric-stats');
  for (const [label, value, hint] of [
    ['今日活跃安装', num(o.dau), `近 7 天 ${num(o.wau)} · 近 30 天 ${num(o.mau)}`],
    ['首次观测安装', num(o.new_installations), `本时段活跃安装 ${num(o.active_installations)}`],
    ['提出问题', num(o.questions), '包含日常对话；不采集问题内容'],
    ['有证据的资料回答', num(o.successful_recalls), `已报告资料回答中占比 ${percent(o.recall_success_rate)}`],
    ['导入成功文件', num(o.import_success_files), `失败 ${num(o.import_failed_files)} · 失败占比 ${percent(o.import_failure_rate)}`],
    ['已报告应用错误', num(o.errors), `涉及 ${num(o.error_installations)} 个安装实例`],
  ]) { const card = el('article', undefined, 'stat'); card.append(el('span', label, 'muted'), el('strong', value), el('span', hint, 'muted')); stats.append(card); }
  fragment.append(stats);
  const panel = title => { const p = el('section', undefined, 'panel'); p.append(el('h2', title)); return p; };
  const table = (headers, rows, empty = '此时段暂无数据') => {
    if (!rows.length) return el('p', empty, 'muted');
    const wrap = el('div', undefined, 'table-scroll'), t = el('table'), head = el('tr'), thead = el('thead'), body = el('tbody');
    headers.forEach(h => { const cell = el('th', h); cell.scope = 'col'; head.append(cell); }); thead.append(head);
    rows.forEach(values => { const row = el('tr'); values.forEach(v => row.append(el('td', v))); body.append(row); }); t.append(thead, body); wrap.append(t); return wrap;
  };
  const grid = el('div', undefined, 'grid metrics-grid');
  const recall = panel('找回资料的表现');
  recall.append(table(['观测事件', '次数'], [['有证据的资料回答', num(o.successful_recalls)], ['未找到可靠记录', num(o.abstentions)], ['查看来源', num(o.citation_clicks)], ['创建笔记', num(o.notes_created)]]));
  recall.append(el('p', '有来源不等于回答准确；拒答也可能是正确行为。查看来源是点击次数，不是满意度或逐问转化率。', 'muted'));
  const versions = panel('活跃安装的版本分布');
  versions.append(table(['最近观测版本', '安装实例'], data.versions.map(v => [`v${v.version}`, num(v.installations)])));
  versions.append(el('p', '每个活跃安装只计最近一次观测版本，不累加其历史版本。', 'muted'));
  grid.append(recall, versions); fragment.append(grid);
  const trend = panel('每日使用趋势');
  trend.append(el('p', '按北京时间分日；今天尚未结束。可横向滚动查看完整指标。', 'muted'));
  const trendTable = table(['日期', '活跃安装', '提问', '资料回答', '未找到记录', '应用错误'], data.trend.map(d => [d.date, num(d.active), num(d.questions), num(d.recalls), num(d.abstentions), num(d.errors)]));
  trendTable.classList.add('trend-table'); trend.append(trendTable); fragment.append(trend);
  const bottom = el('div', undefined, 'grid metrics-grid');
  const retention = panel('安装实例留存');
  retention.append(table(['回访日', '可观测样本', '回访', '留存率'], data.retention.map(r => [`D${r.day}`, num(r.eligible), num(r.returned), percent(r.rate)])));
  retention.append(el('p', '从首次观测日起，是否在第 1／7／30 天再次产生使用事件。仅统计所选时段内已经结束的回访日；样本尚未成熟时显示“—”。', 'muted'));
  const updates = panel('更新事件');
  updates.append(table(['观测事件', '次数'], [['发现可用更新', num(data.updates.available)], ['开始更新', num(data.updates.started)], ['更新后重新打开', num(data.updates.completed)], ['更新失败', num(data.updates.failed)]]));
  updates.append(el('p', '这些是时段内事件次数。更新可跨时段、失败后可重试，因此不将它们相除冒充精确的更新成功率。', 'muted'));
  bottom.append(retention, updates); fragment.append(bottom);
  const errorNames = { frontend_error:'界面异常', unhandled_rejection:'未处理的异步异常', worker_exited:'本地 Worker 退出', worker_timeout:'本地 Worker 超时', worker_error:'本地 Worker 错误', rust_panic:'桌面核心异常', import_error:'导入错误', chat_error:'对话错误', update_network:'更新网络错误', update_manifest:'更新清单错误', update_signature:'更新签名错误', update_download:'更新下载错误', update_install:'更新安装错误', update_worker_busy:'更新时 Worker 忙碌', update_unknown:'未知更新错误', unknown:'未分类错误' };
  const errors = panel('错误类型与版本');
  errors.append(table(['错误类型', '版本', '次数', '涉及安装'], data.errors.map(r => [errorNames[r.error_type] || '未分类错误', `v${r.version}`, num(r.occurrences), num(r.installations)]), '此时段未收到错误报告。未上报不代表完全没有发生错误。'));
  fragment.append(errors);
  const definitions = el('details', undefined, 'panel definitions'); definitions.append(el('summary', '统计口径与数据边界'));
  definitions.append(el('p', '这里统计的是成功上报的随机安装实例，不是真实身份、注册用户或总安装量。重装、关闭统计、网络故障和版本覆盖都会影响数字。基础事件接口不用于计费或安全审计。', 'muted'));
  definitions.append(el('p', '活跃表示时段内至少一个允许的使用事件。首次观测不是实际安装日期。资料回答占比的分母只包含资料回答与拒答，不包含普通聊天。导入文件失败占比只计成功事件中的成功数及失败事件中的失败数，避免部分成功任务被重复计数。', 'muted'));
  definitions.append(el('p', `事件最多保留 ${data.storage.retention_days} 天。当前 ${num(data.storage.events)} / ${num(data.storage.capacity)} 条，达到容量上限时停止接收，不影响发布服务。汇总时间：${stamp(data.generated_at)}。`, 'muted'));
  fragment.append(definitions);
  return fragment;
}
