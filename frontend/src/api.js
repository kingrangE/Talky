export async function api(path, options = {}) {
  let response;
  try { response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { ...(options.body && !(options.body instanceof Blob) ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  }); } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('서버에 연결하지 못했어요. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || '연결을 확인한 뒤 다시 시도해 주세요.');
    error.status = response.status;
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('talky:unauthorized'));
    throw error;
  }
  return response.json();
}

export const json = (method, body) => ({ method, body: JSON.stringify(body) });

export function mergeTurns(serverTurns, localTurns) {
  const ids = new Set(serverTurns.map(turn => turn.id));
  return [...serverTurns, ...localTurns.filter(turn => !ids.has(turn.id))];
}

export function pendingTurns(turns) {
  return turns.filter(turn => !turn.result?.user_message_id && !turn.result?.assistant_message_id);
}
