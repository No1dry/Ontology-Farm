const labels: Record<string, string> = {
  OIDC_STATE_INVALID: '登录链接已失效，请重新登录。', OIDC_TOKEN_INVALID: '身份验证未通过，请重新登录。',
  OIDC_UNAVAILABLE: '身份服务暂时不可用，请稍后重试。', UNAUTHENTICATED: '请登录后继续。',
  SESSION_EXPIRED: '会话已过期，请重新登录。', SESSION_REQUIRED: '请登录后继续。',
  STALE_SESSION: '会话或权限已变化，请重新打开内容。', AUTHORIZATION_CHANGED: '权限已变化，请重新核验。',
  FORBIDDEN: '你没有执行此操作的权限。', CSRF_INVALID: '操作验证已更新，请确认后重试。',
  LOGOUT_UNCONFIRMED: '无法确认服务器已退出。请恢复连接后重新核验。',
  PROVIDER_LOGOUT_INCOMPLETE: '已退出工作台；身份提供方的单点登录可能仍然有效。',
};
export function formatApiError(error: unknown): string {
  const value = error && typeof error === 'object' ? error as { code?: unknown; request_id?: unknown } : {};
  const message = typeof value.code === 'string' ? labels[value.code] || '操作未完成，请重试或联系管理员。' : '连接暂时不可用，请稍后重试。';
  const requestId = typeof value.request_id === 'string' && /^[A-Za-z0-9._:-]{1,128}$/.test(value.request_id) ? ` 请求编号：${value.request_id}` : '';
  return message + requestId;
}
export function ErrorMessage({ error }: { error: unknown }) { return <p role="alert" className="error-message">{formatApiError(error)}</p>; }
