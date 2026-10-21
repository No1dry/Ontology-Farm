export const routes = [
  { path: '/overview', name: '工作概览', en: 'Overview', glyph: 'overview', delivered: true },
  { path: '/ontology/studio', name: '本体工作室', en: 'Ontology studio', glyph: 'ontology', delivered: true },
  { path: '/objects', name: '对象与关系', en: 'Objects', glyph: 'objects', delivered: false },
  { path: '/graph', name: '知识图谱', en: 'Knowledge graph', glyph: 'graph', delivered: false },
  { path: '/geo', name: '湖域地图', en: 'Geospatial', glyph: 'map', delivered: false },
  { path: '/quality', name: '数据质量', en: 'Quality', glyph: 'quality', delivered: false },
  { path: '/sources', name: '数据接入', en: 'Sources', glyph: 'source', delivered: false },
  { path: '/governance', name: '治理与审查', en: 'Governance', glyph: 'governance', delivered: true },
  { path: '/access', name: '成员与权限', en: 'Access', glyph: 'access', delivered: false },
  { path: '/settings', name: '系统设置', en: 'Settings', glyph: 'settings', delivered: false },
] as const;
export function currentRoute() { return routes.find(route => route.path === window.location.pathname); }
export function completeLogin() {
  if (['/', '/auth/complete'].includes(window.location.pathname)) window.history.replaceState(null, '', '/overview');
}
