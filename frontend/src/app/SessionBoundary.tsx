import { useEffect, useSyncExternalStore } from 'react';
import { sessionController } from '../api/session';
import { ErrorMessage } from '../shared/errors';
import { Shell } from './Shell';
import { completeLogin } from './router';

function LoginButton() { return <button className="primary" onClick={() => window.location.assign('/api/v1/session/login')}>登录</button>; }
export function SessionBoundary(): React.JSX.Element {
  const state = useSyncExternalStore(sessionController.subscribe, sessionController.snapshot);
  useEffect(() => sessionController.connect(Number(import.meta.env.VITE_SESSION_POLL_MS) || 30_000), []);
  useEffect(() => { if (state.status === 'ready') completeLogin(); }, [state.status]);
  if (window.location.pathname === '/auth/error') {
    const parameters = new URLSearchParams(window.location.search);
    const code = parameters.get('code');
    const allowed = ['OIDC_STATE_INVALID', 'OIDC_TOKEN_INVALID', 'OIDC_UNAVAILABLE', 'UNAUTHENTICATED'];
    const error = { code: code && allowed.includes(code) ? code : 'UNKNOWN', request_id: parameters.get('request_id') };
    return <Welcome><p className="eyebrow">身份验证 / AUTHENTICATION</p><h1>重新开始，安全连接</h1><ErrorMessage error={error} /><LoginButton /><a href="/overview">返回工作台</a></Welcome>;
  }
  return <>
    {state.session && <div
      key={`${state.session.user.id}:${state.session.authorization_version}`}
      hidden={state.status !== 'ready'} inert={state.status !== 'ready'}>
      <Shell session={state.session} />
    </div>}
    {state.status !== 'ready' && <Welcome>
    <p className="eyebrow">湖域农业 · ONTOLOGY FARM</p><h1>让农业知识<br/>生长在一起。</h1>
    <p className="welcome-description">连接农田、水塘与果园，以共同的语言理解每一片土地。</p>
    {Boolean(state.error) && <ErrorMessage error={state.error} />}
    {state.status === 'anonymous' && <LoginButton />}
    {state.status === 'unavailable' && <><h2>暂时无法核验会话</h2><button className="primary" onClick={() => void sessionController.reconcile()}>重新核验</button><LoginButton /></>}
    {['checking', 'suspended'].includes(state.status) && <p role="status">正在核验身份与当前权限…</p>}
    <p className="fine-print">合成数据演示环境 · 登录由组织身份服务提供</p>
  </Welcome>}
  </>;
}
function Welcome({ children }: { children: React.ReactNode }) { return <main className="welcome"><div className="welcome-brand"><span className="brand-mark">田</span> 本体农场</div><section className="welcome-card">{children}</section><div className="welcome-land" aria-hidden="true"><span/><span/><span/></div></main>; }
