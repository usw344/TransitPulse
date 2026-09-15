/** Formatting and fetch helpers shared by the operations app and research pages. */

export interface ErrorMetrics {
  mae: number;
  rmse: number;
  median_absolute_error: number;
  sample_count: number;
}

/** Shown whenever the API cannot be reached, instead of a bare status code. */
export const API_NOT_RESPONDING =
  "The TransitPulse API is not responding. Start it with START_TRANSITPULSE.bat, then refresh.";

/**
 * Turn a failed response into a message a person can act on.  The API's own
 * errors carry a JSON `detail`; a 5xx without one is the web server's proxy
 * reporting that the API process is not there.
 */
export async function responseError(response: Response): Promise<Error> {
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (typeof body?.detail === "string") return new Error(body.detail);
  if (response.status >= 500) return new Error(API_NOT_RESPONDING);
  return new Error(`Request failed (${response.status})`);
}

export function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  return fetch(url, { cache: "no-store", signal }).then(
    async (response) => {
      if (!response.ok) throw await responseError(response);
      return response.json() as Promise<T>;
    },
    (error: unknown) => {
      if (signal?.aborted) throw error;
      throw new Error(API_NOT_RESPONDING);
    },
  );
}

export function formatSeconds(value: number | null): string {
  if (value === null) return "—";
  return `${Math.round(value / 60)} min`;
}

export function formatPreciseSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)} s`;
}

export function formatSignedSeconds(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  if (value !== 0 && Math.abs(value) < 60) {
    return `${sign}${Math.abs(Math.round(value))} sec`;
  }
  return `${sign}${Math.round(Math.abs(value) / 60)} min`;
}
