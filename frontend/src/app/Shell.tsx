import { lazy, Suspense, useState } from 'react';
import { sessionController, type Session } from '../api/session';
import { currentRoute, routes } from './router';
const StudioPage = lazy(() => import('../features/studio/StudioPage'));
const ApprovalsPanel = lazy(() => import('../features/governance/ApprovalsPanel'));

function Icon({ name }: { name: string }) {
  const shapes: Record<string, React.ReactNode> = {
    overview: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    ontology: <><path d="M12 3v6m-7 4v8m14-8v8M5 13h14M12 9v4"/><circle cx="12" cy="3" r="2"/></>,
    objects: <><path d="m3 7 9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10"/></>,
    graph: <><path d="m6 6 12 12M6 18 18 6"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/></>,
    map: <path d="m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Zm6-2v16m6-14v16"/>,
    quality: <><path d="m12 3 8 4v6c0 5-8 8-8 8s-8-3-8-8V7l8-4Z"/><path d="m8 12 3 3 5-6"/></>,
    source: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
    governance: <><path d="M6 3h12v18H6zM9 7h6M9 11h6M9 15h4"/></>,
    access: <><circle cx="9" cy="8" r="4"/><path d="M2 21v-3a7 7 0 0 1 14 0v3M17 5a4 4 0 0 1 0 8m2 3a5 5 0 0 1 3 5"/></>,
    settings: <><circle cx="12" cy="12" r="4"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8 3 3M5 19l3-3M16 8l3-3"/></>,
  };
  return <svg viewBox="0 0 24 24" className="icon" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">{shapes[name]}</svg>;
}
export function Shell({ session }: { session: Session }): React.JSX.Element {
  const [collapsed, setCollapsed] = useState(false);
  const route = currentRoute() || (['/', '/auth/complete'].includes(window.location.pathname) ? routes[0] : undefined);
  return <div className={`workbench ${collapsed ? 'is-collapsed' : ''}`}>
    <a className="skip-link" href="#main" onClick={() => document.getElementById('main')?.focus()}>跳到主要内容</a>
    <aside className="sidebar"><a className="brand" href="/overview" aria-label="本体农场工作概览"><span className="brand-mark">田</span><span className="nav-label">本体农场<small>ONTOLOGY FARM</small></span></a>
      <div className="workspace-tag nav-label"><span className="status-dot"/> 湖域农业工作空间<small>农田 · 水塘 · 果园</small></div>
      <nav aria-label="主导航"><p className="nav-heading nav-label">工作空间</p>{routes.map((item, index) => <div key={item.path} className={index === 6 ? 'nav-divider' : undefined}>{item.delivered ? <a href={item.path} className={`nav-item ${route?.path === item.path ? 'active' : ''}`} aria-current={route?.path === item.path ? 'page' : undefined} title={item.name}><Icon name={item.glyph}/><span className="nav-label">{item.name}</span></a> : <div className="nav-item pending" aria-label={`${item.name}，待实现`} title={`${item.name} · 待实现`}><Icon name={item.glyph}/><span className="nav-label">{item.name}<small>待实现</small></span></div>}</div>)}</nav>
      <div className="sidebar-bottom"><span className="nav-label">知识相连，生长有据。</span><button className="collapse" onClick={() => setCollapsed(!collapsed)} aria-expanded={!collapsed} aria-label={collapsed ? '展开导航' : '折叠导航'}>{collapsed ? '»' : '«'}</button></div>
    </aside>
    <header className="topbar"><div className="breadcrumb">工作空间 <span>/</span> <strong>{route?.name || '页面未找到'}</strong></div><div className="account"><span className="synthetic-badge">合成数据演示</span><span className="avatar" aria-hidden="true">{session.user.display_name.slice(0, 1)}</span><span className="user-name">{session.user.display_name}</span><button className="text-button" onClick={() => void sessionController.logout()}>退出登录</button></div></header>
    <main id="main" className="main-content" tabIndex={-1}>
      <Suspense fallback={<p role="status">加载工作页面…</p>}>{route?.path === '/overview' ? <Overview session={session}/> : route?.path === '/ontology/studio' ? <StudioPage/> : route?.path === '/governance' ? <ApprovalsPanel/> : <section className="pending-page"><p className="eyebrow">{route?.en || 'NOT FOUND'}</p><h1>{route ? `${route.name} · 待实现` : '未找到此页面'}</h1><p>{route ? '此功能尚未交付，当前不提供操作入口。' : '请从主导航进入已交付的工作页面。'}</p><a href="/overview">返回工作概览 →</a></section>}</Suspense>
      <footer className="page-footer"><span>本体农场 / ONTOLOGY FARM</span><span>每一次连接，都保留来处。</span></footer>
    </main>
  </div>;
}
function Overview({ session }: { session: Session }) {
  return <><div className="page-heading"><div><p className="eyebrow">WORKSPACE OVERVIEW</p><h1>工作概览</h1><p>从一片土地，到相互连接的农业知识。</p></div><span className="section-index">01 / 工作空间</span></div>
    <section className="landscape-card"><div><span className="landscape-kicker">连接 · 理解 · 共建</span><h2>一湖流域，<br/>多种生长。</h2><p>农田、水塘与果园共享同一套知识语言，<br className="desktop-break"/>让对象、关系和每一份证据各有归处。</p><div className="domain-tags"><span>农田 / FIELD</span><span>水塘 / POND</span><span>果园 / ORCHARD</span></div></div><svg className="landscape" viewBox="0 0 520 300" aria-hidden="true"><defs><pattern id="field-lines" width="18" height="18" patternTransform="rotate(-28)" patternUnits="userSpaceOnUse"><path d="M0 0v18" stroke="#b1c3a0" strokeWidth="3"/></pattern></defs><path d="M30 120 220 30 475 118 300 225Z" fill="#adc29c"/><path d="m30 120 2 28 268 104 175-105v-29L300 225Z" fill="#6d8d72"/><path d="m44 120 175-78 100 34-176 90Z" fill="url(#field-lines)"/><path d="m230 82 95-2 125 43-119 71-96-28c-42-16-38-62-5-84Z" fill="#84b9b6"/><path d="m242 105 55-11 120 31-80 48-97-27c-24-11-24-32 2-41Z" fill="#a3ceca"/><path d="m152 172 71-33 89 59-12 15Z" fill="#d3d5b2"/>{[0,1,2,3,4].map(i=><g key={i} transform={`translate(${270+i*32},${60+i*11})`}><path d="M0 0v24" stroke="#526c52" strokeWidth="3"/><circle cy="-2" r="13" fill="#527c59"/><circle cx="-4" cy="-6" r="10" fill="#789d68"/></g>)}<path d="m17 217 278 69 205-107" fill="none" stroke="#aec0aa" strokeDasharray="3 6"/></svg></section>
    <div className="overview-grid"><section className="panel session-panel"><div className="panel-title"><h2>当前访问</h2><span className="verified"><span className="status-dot"/> 已核验</span></div><p className="panel-description">按当前身份与授权范围呈现工作内容。</p><dl><div><dt>登录成员</dt><dd>{session.user.display_name}</dd></div><div><dt>授权版本</dt><dd>v{session.authorization_version}</dd></div><div><dt>有效授权范围</dt><dd>{session.permissions.length} 项</dd></div></dl><p className="privacy-note">切换窗口时会重新核验权限。会话失效后，受保护内容将自动清除。</p><button className="secondary" onClick={() => void sessionController.reconcile()}>重新核验权限 <span aria-hidden="true">↗</span></button></section>
    <section className="panel"><div className="panel-title"><h2>工作台交付进度</h2><span className="muted-label">逐步开放</span></div><p className="panel-description">已交付功能可使用；后续模块清晰标记为待实现。</p><ol className="delivery-list"><li className="delivered"><span>01</span><div><strong>身份与工作空间</strong><p>真实登录、安全会话与中文导航</p></div><em>已交付</em></li><li className="delivered"><span>02</span><div><strong>本体建模与 Schema 审查</strong><p>完整草稿、来源、约束与审核发布</p></div><em>已交付</em></li><li><span>03</span><div><strong>对象管理、接入与空间探索</strong><p>农业对象、数据接入、图谱与湖域地图</p></div><em>待实现</em></li></ol></section></div>
    <aside className="environment-note"><span className="note-symbol" aria-hidden="true">i</span><div><strong>这里展示合成数据演示环境</strong><p>用于验证农业知识建模流程，不代表实际农场运营状况。</p></div></aside></>;
}
