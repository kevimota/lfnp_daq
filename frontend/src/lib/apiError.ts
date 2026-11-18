/**
 * Turn an API error into a human-readable string.
 *
 * FastAPI returns `detail` in several shapes:
 *  - a plain string (most errors),
 *  - an array of validation errors (`[{ msg, loc, ... }]`),
 *  - a structured object (e.g. 409 channel conflicts with `message`,
 *    `busy_channels` and `conflicting_run_ids`).
 *
 * Passing the object straight to `toast.error()` renders `[object Object]`,
 * so always run errors through this helper first.
 */

interface ApiErrorResponse {
  response?: {
    data?: {
      detail?: unknown;
    };
  };
}

function formatChannels(value: unknown): string {
  if (!Array.isArray(value)) return '';
  return value
    .map((entry) => {
      if (Array.isArray(entry) && entry.length >= 2) {
        return `slot ${entry[0]}/ch ${entry[1]}`;
      }
      if (entry && typeof entry === 'object') {
        const channel = entry as { slot?: unknown; channel?: unknown };
        if (channel.slot !== undefined && channel.channel !== undefined) {
          return `slot ${channel.slot}/ch ${channel.channel}`;
        }
      }
      return '';
    })
    .filter(Boolean)
    .join(', ');
}

function formatValidationErrors(detail: unknown[]): string {
  return detail
    .map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object' && 'msg' in item) {
        const msg = (item as { msg?: unknown }).msg;
        if (typeof msg === 'string') return msg;
      }
      return '';
    })
    .filter(Boolean)
    .join(', ');
}

function formatStructuredDetail(detail: Record<string, unknown>): string {
  const message = typeof detail.message === 'string' ? detail.message : '';
  const channels = formatChannels(detail.busy_channels);
  const runIds = Array.isArray(detail.conflicting_run_ids)
    ? detail.conflicting_run_ids.filter((id) => id !== null && id !== undefined)
    : [];

  const extras: string[] = [];
  if (channels) extras.push(channels);
  if (runIds.length > 0) {
    extras.push(
      `${runIds.length === 1 ? 'run' : 'runs'} ${runIds
        .map((id) => `#${id}`)
        .join(', ')}`,
    );
  }

  if (message) {
    return extras.length > 0 ? `${message} (${extras.join('; ')})` : message;
  }
  return extras.join('; ');
}

export function formatApiError(err: unknown, fallback: string): string {
  if (!err || typeof err !== 'object') return fallback;

  const detail = (err as ApiErrorResponse).response?.data?.detail;

  if (typeof detail === 'string') return detail || fallback;
  if (Array.isArray(detail)) return formatValidationErrors(detail) || fallback;
  if (detail && typeof detail === 'object') {
    return formatStructuredDetail(detail as Record<string, unknown>) || fallback;
  }
  return fallback;
}
