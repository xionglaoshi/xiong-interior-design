(() => {
  'use strict';
  const config = JSON.parse(document.getElementById('drawing-config').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const viewport = document.getElementById('viewport');
  const paper = document.getElementById('paper');
  const status = document.getElementById('status');
  const editor = document.getElementById('text-editor');
  const textInput = editor.querySelector('textarea');
  const width = config.width, height = config.height;
  const box = { x: 0, y: 0, w: width, h: height };
  const views = {};
  let active = 'existing', tool = null, drawing = null, panning = null, pendingText = null, scale = 1, storageOk = true;

  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }
  function within(x, y) { return Number.isFinite(x) && Number.isFinite(y) && x >= 0 && y >= 0 && x <= width && y <= height; }
  function valid(item) {
    if (!item || !['rect', 'circle', 'line', 'text'].includes(item.kind)) return false;
    if (item.kind === 'text') return within(item.x, item.y) && typeof item.text === 'string' && item.text.length > 0 && item.text.length <= 200;
    if (item.kind === 'line') return within(item.x1, item.y1) && within(item.x2, item.y2) && Math.hypot(item.x2 - item.x1, item.y2 - item.y1) >= 8;
    return within(item.x, item.y) && Number.isFinite(item.w) && Number.isFinite(item.h) && item.w >= 8 && item.h >= 8 && item.x + item.w <= width && item.y + item.h <= height && (item.kind !== 'circle' || Math.abs(item.w - item.h) < 0.1);
  }
  function makeView(name, label, image, hash) {
    const sheet = document.createElement('section');
    sheet.className = 'sheet'; sheet.hidden = name !== active;
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', 'aria-label': `${label}图` });
    svg.append(svgEl('image', { href: image, x: 0, y: 0, width, height, class: 'source-image', preserveAspectRatio: 'none' }));
    const marks = svgEl('g', { 'data-mark-layer': name });
    const draft = svgEl('g', { 'data-draft-layer': name });
    svg.append(marks, draft); sheet.append(svg); paper.append(sheet);
    const key = `xiong-floorplan-markup:v1:${config.projectId}:${name}:${hash}`;
    let items = [];
    try {
      const stored = JSON.parse(localStorage.getItem(key) || '[]');
      if (Array.isArray(stored)) items = stored.filter(valid).slice(0, 1000);
    } catch (_) { storageOk = false; }
    return { name, label, hash, sheet, svg, marks, draft, key, items };
  }
  views.existing = makeView('existing', '现状', config.existingImage, config.existingHash);
  views.layout = makeView('layout', '平面布置', config.layoutImage, config.layoutHash);

  function renderShape(group, item, number, draft = false) {
    if (item.kind === 'text') {
      const text = svgEl('text', { x: item.x, y: item.y, class: 'mark-text' });
      item.text.split('\n').forEach((line, i) => { const span = svgEl('tspan', { x: item.x, dy: i ? 24 : 0 }); span.textContent = line; text.append(span); });
      group.append(text); return;
    }
    let shape;
    if (item.kind === 'line') shape = svgEl('line', { x1: item.x1, y1: item.y1, x2: item.x2, y2: item.y2, class: draft ? 'mark-line mark-draft' : 'mark-line' });
    else if (item.kind === 'circle') shape = svgEl('circle', { cx: item.x + item.w / 2, cy: item.y + item.h / 2, r: item.w / 2, class: draft ? 'mark-shape mark-draft' : 'mark-shape' });
    else shape = svgEl('rect', { x: item.x, y: item.y, width: item.w, height: item.h, class: draft ? 'mark-shape mark-draft' : 'mark-shape' });
    group.append(shape);
    if (draft) return;
    const midX = item.kind === 'line' ? (item.x1 + item.x2) / 2 : item.x + item.w;
    const midY = item.kind === 'line' ? (item.y1 + item.y2) / 2 : item.y;
    const lx = Math.min(width - 34, Math.max(0, midX)), ly = Math.min(height - 22, Math.max(0, midY - 24));
    group.append(svgEl('rect', { x: lx, y: ly, width: 32, height: 22, rx: 3, class: 'mark-label-bg' }));
    const label = svgEl('text', { x: lx + 16, y: ly + 16, 'text-anchor': 'middle', class: 'mark-label' }); label.textContent = String(number); group.append(label);
  }
  function render() {
    const view = views[active]; view.marks.replaceChildren();
    view.items.forEach((item, i) => renderShape(view.marks, item, i + 1));
    document.getElementById('undo').disabled = !view.items.length;
    document.getElementById('clear').disabled = !view.items.length;
    status.textContent = `${view.label}图 · ${view.items.length}项标注 · ${storageOk ? '自动保存在本浏览器' : '存储不可用，请导出标注'}`;
  }
  function persist() {
    try { localStorage.setItem(views[active].key, JSON.stringify(views[active].items)); storageOk = true; }
    catch (_) { storageOk = false; }
    render();
  }
  function fit() {
    scale = Math.min(viewport.clientWidth / width, viewport.clientHeight / height);
    paper.style.width = `${Math.max(1, width * scale)}px`;
    paper.style.height = `${Math.max(1, height * scale)}px`;
    viewport.scrollLeft = 0; viewport.scrollTop = 0;
  }
  function point(event, svg) {
    const matrix = svg.getScreenCTM(); if (!matrix) return null;
    const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return { x: Math.max(0, Math.min(width, p.x)), y: Math.max(0, Math.min(height, p.y)) };
  }
  function closeEditor() { editor.hidden = true; pendingText = null; textInput.value = ''; }
  function setTool(next) {
    drawing = panning = null; closeEditor(); tool = next;
    document.querySelectorAll('[data-tool]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tool === tool)));
    Object.values(views).forEach(view => tool ? view.svg.setAttribute('data-tool', tool) : view.svg.removeAttribute('data-tool'));
  }
  function switchView(name) {
    drawing = panning = null; closeEditor(); active = name;
    Object.values(views).forEach(view => { view.sheet.hidden = view.name !== name; });
    for (const key of ['layout', 'existing']) {
      const button = document.getElementById(`view-${key}`), selected = key === name;
      button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected));
    }
    render();
  }
  function draftItem(start, end) {
    if (tool === 'line') return { kind: 'line', x1: start.x, y1: start.y, x2: end.x, y2: end.y };
    if (tool === 'circle') {
      const side = Math.max(Math.abs(end.x - start.x), Math.abs(end.y - start.y));
      const x = end.x < start.x ? start.x - side : start.x;
      const y = end.y < start.y ? start.y - side : start.y;
      return { kind: 'circle', x, y, w: side, h: side };
    }
    return { kind: 'rect', x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), w: Math.abs(end.x - start.x), h: Math.abs(end.y - start.y) };
  }
  function openTextEditor(position, event) {
    pendingText = position;
    editor.style.left = `${Math.max(8, Math.min(innerWidth - 255, event.clientX))}px`;
    editor.style.top = `${Math.max(46, Math.min(innerHeight - 105, event.clientY))}px`;
    editor.hidden = false; textInput.focus();
  }
  function commitText() {
    const text = textInput.value.trim();
    if (text && pendingText) { views[active].items.push({ kind: 'text', ...pendingText, text }); persist(); }
    closeEditor();
  }

  Object.values(views).forEach(view => {
    view.svg.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary) return;
      const p = point(event, view.svg); if (!p) return;
      event.preventDefault();
      if (tool === 'text') { openTextEditor(p, event); return; }
      if (tool) drawing = { id: event.pointerId, start: p, view };
      else { panning = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop, view }; view.svg.classList.add('panning'); }
      view.svg.setPointerCapture(event.pointerId);
    });
    view.svg.addEventListener('pointermove', event => {
      if (panning?.id === event.pointerId) { viewport.scrollLeft = panning.left + panning.x - event.clientX; viewport.scrollTop = panning.top + panning.y - event.clientY; return; }
      if (drawing?.id !== event.pointerId) return;
      view.draft.replaceChildren(); renderShape(view.draft, draftItem(drawing.start, point(event, view.svg)), 0, true);
    });
    view.svg.addEventListener('pointerup', event => {
      if (drawing?.id === event.pointerId) {
        const item = draftItem(drawing.start, point(event, view.svg)); view.draft.replaceChildren(); drawing = null;
        if (valid(item) && view.items.length < 1000) { view.items.push(item); active = view.name; persist(); }
      } else if (panning?.id === event.pointerId) { panning.view.svg.classList.remove('panning'); panning = null; }
    });
    view.svg.addEventListener('pointercancel', () => { drawing = panning = null; view.draft.replaceChildren(); view.svg.classList.remove('panning'); });
    view.svg.addEventListener('dblclick', event => { if (!tool) { event.preventDefault(); fit(); } });
  });

  document.querySelectorAll('[data-tool]').forEach(button => button.addEventListener('click', () => setTool(tool === button.dataset.tool ? null : button.dataset.tool)));
  document.getElementById('view-layout').addEventListener('click', () => switchView('layout'));
  document.getElementById('view-existing').addEventListener('click', () => switchView('existing'));
  document.getElementById('undo').addEventListener('click', () => { views[active].items.pop(); persist(); });
  document.getElementById('clear').addEventListener('click', () => { if (views[active].items.length && confirm(`清空${views[active].label}图上的全部标注？`)) { views[active].items = []; persist(); } });
  const importFile = document.getElementById('import-file');
  document.getElementById('import').addEventListener('click', () => importFile.click());
  importFile.addEventListener('change', async () => {
    const file = importFile.files?.[0]; if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      if (payload.schema_version !== 1 || payload.project_id !== config.projectId ||
          !Array.isArray(payload.canvas_px) || payload.canvas_px[0] !== width || payload.canvas_px[1] !== height ||
          payload.existing_hash !== config.existingHash || payload.layout_hash !== config.layoutHash ||
          !payload.views || !Array.isArray(payload.views.existing) || !Array.isArray(payload.views.layout) ||
          payload.views.existing.length > 1000 || payload.views.layout.length > 1000 ||
          !payload.views.existing.every(valid) || !payload.views.layout.every(valid)) {
        throw new Error('项目ID、画布、底图版本或标注内容不匹配；未导入任何标注。');
      }
      if ((views.existing.items.length || views.layout.items.length) && !confirm('导入将替换两层现有标注，是否继续？')) return;
      views.existing.items = payload.views.existing;
      views.layout.items = payload.views.layout;
      try {
        localStorage.setItem(views.existing.key, JSON.stringify(views.existing.items));
        localStorage.setItem(views.layout.key, JSON.stringify(views.layout.items));
        storageOk = true;
      } catch (_) { storageOk = false; }
      switchView('layout');
    } catch (error) {
      alert(error instanceof SyntaxError ? '标注JSON无法读取。' : error.message);
    } finally { importFile.value = ''; }
  });
  document.getElementById('export').addEventListener('click', () => {
    const payload = { schema_version: 1, project_id: config.projectId, project: config.project, canvas_px: [width, height], existing_hash: config.existingHash, layout_hash: config.layoutHash, existing_state: config.existingState, exported_at: new Date().toISOString(), views: { existing: views.existing.items, layout: views.layout.items } };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const anchor = document.createElement('a'); anchor.href = URL.createObjectURL(blob); anchor.download = `${config.filename}-标注.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(anchor.href), 1000);
  });
  editor.querySelector('[data-action="save"]').addEventListener('click', commitText);
  editor.querySelector('[data-action="cancel"]').addEventListener('click', closeEditor);
  textInput.addEventListener('keydown', event => { if (event.key === 'Escape') closeEditor(); if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); commitText(); } });
  viewport.addEventListener('wheel', event => {
    if (editor.hidden === false) return;
    event.preventDefault();
    const svg = views[active].svg, p = point(event, svg); if (!p) return;
    const before = svg.getBoundingClientRect();
    scale = Math.max(0.15, Math.min(6, scale * Math.exp(-event.deltaY * 0.002)));
    paper.style.width = `${width * scale}px`; paper.style.height = `${height * scale}px`;
    viewport.scrollLeft += before.left + p.x * scale - event.clientX;
    viewport.scrollTop += before.top + p.y * scale - event.clientY;
  }, { passive: false });
  window.addEventListener('keydown', event => { if (event.key === 'Escape') { closeEditor(); setTool(null); } });
  new ResizeObserver(fit).observe(viewport);
  switchView('existing'); fit();
})();
