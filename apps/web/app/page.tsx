"use client";

import { useEffect, useState } from "react";

type ApiStatus = "loading" | "ok" | "unavailable";

interface DatabaseHealth {
  status: string;
  postgis_version: string;
}

async function fetchStatus(url: string): Promise<Response> {
  return fetch(url, { cache: "no-store" });
}

export default function Home() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("loading");
  const [databaseStatus, setDatabaseStatus] = useState<ApiStatus>("loading");
  const [postgisVersion, setPostgisVersion] = useState<string | null>(null);

  useEffect(() => {
    async function checkServices() {
      try {
        const apiResponse = await fetchStatus("/api/health");
        setApiStatus(apiResponse.ok ? "ok" : "unavailable");
      } catch {
        setApiStatus("unavailable");
      }

      try {
        const databaseResponse = await fetchStatus("/api/health/db");
        if (!databaseResponse.ok) {
          setDatabaseStatus("unavailable");
          return;
        }

        const payload: DatabaseHealth = await databaseResponse.json();
        setDatabaseStatus(payload.status === "ok" ? "ok" : "unavailable");
        setPostgisVersion(payload.postgis_version);
      } catch {
        setDatabaseStatus("unavailable");
      }
    }

    void checkServices();
  }, []);

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <section className="w-full max-w-lg rounded-lg border border-slate-700 bg-slate-900 p-6 shadow-sm">
        <h1 className="text-3xl font-semibold text-white">TransitPulse</h1>
        <p className="mt-1 text-slate-400">Foundation status</p>
        <dl className="mt-6 space-y-4">
          <div>
            <dt className="text-sm text-slate-400">API</dt>
            <dd className="font-medium" data-testid="api-status">
              {apiStatus}
            </dd>
          </div>
          <div>
            <dt className="text-sm text-slate-400">Database / PostGIS</dt>
            <dd className="font-medium" data-testid="database-status">
              {databaseStatus}
              {postgisVersion ? ` (PostGIS ${postgisVersion})` : ""}
            </dd>
          </div>
        </dl>
      </section>
    </main>
  );
}
