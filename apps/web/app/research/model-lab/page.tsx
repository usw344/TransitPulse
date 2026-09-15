"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  type ErrorMetrics,
  fetchJson,
  formatPreciseSeconds,
  formatSeconds,
  formatSignedSeconds,
} from "../../format";

interface ModelLabSummary {
  availability: "experimental" | "unavailable";
  message: string;
  missing_artifacts?: string[];
  integrity_failure?: string;
  provenance?: {
    dataset_rows: number;
    service_days: string[];
    dataset_sha256: string;
    source_labels_sha256: string;
  };
  selected_model?: {
    name: string;
    reason: string;
    test_metrics: ErrorMetrics;
    deep_model_test_metrics: ErrorMetrics;
    deep_model_status: string;
  };
  twin_diagnostic?: {
    status: string;
    eligible_spans: number;
    eligible_labels: number;
    segment_travel_time: ErrorMetrics;
    contiguous_span_travel_time: ErrorMetrics;
    interval: { nominal_coverage: number; held_out_coverage: number };
    full_route_terminal_validation: { status: string; reason: string };
    partial_headway_diagnostic: {
      status: string;
      eligible_adjacent_pairs: number;
      headway_seconds?: ErrorMetrics;
      interpretation?: string;
      reason: string;
    };
    terminal_coverage_audit: {
      status: string;
      label_runs: number;
      complete_runs: number;
      reason: string;
    };
  };
  historical_span_replay?: {
    selected_run_id: string;
    from_stop_sequence: number;
    to_stop_sequence: number;
    validation: {
      segment_travel_time: ErrorMetrics;
      span_end_arrival_error_seconds: number;
      evaluation_note: string;
    };
  };
  constraints?: string[];
}

/**
 * Research-only view of the shelved experimental Model Lab.
 *
 * This sits deliberately outside the operations product: artifact hashes,
 * validation checks and run counts are engineering evidence, not a user feature.
 * Nothing here is a validated operational or passenger result.
 */
export default function ModelLabResearchPage() {
  const [modelLab, setModelLab] = useState<ModelLabSummary | null>(null);
  const [message, setMessage] = useState("Loading versioned experimental evidence...");

  useEffect(() => {
    const abortController = new AbortController();
    void fetchJson<ModelLabSummary>("/api/model-lab/summary", abortController.signal)
      .then((summary) => {
        setModelLab(summary);
        setMessage(summary.message);
      })
      .catch((error: unknown) => {
        if (abortController.signal.aborted) return;
        setMessage(
          error instanceof Error ? error.message : "Model Lab evidence could not be loaded.",
        );
      });
    return () => abortController.abort();
  }, []);

  const twin = modelLab?.twin_diagnostic;
  const replay = modelLab?.historical_span_replay;

  return (
    <main className="research-shell">
      <header className="research-header">
        <div className="research-identity">
          <Link className="research-back" href="/">
            Back to operations
          </Link>
          <div>
            <p className="eyebrow">RESEARCH &middot; NOT A PRODUCT SURFACE</p>
            <h1>Model Lab evidence</h1>
          </div>
        </div>
        <span className={`model-lab-status ${modelLab?.availability ?? "unavailable"}`}>
          {modelLab?.availability ?? "loading"}
        </span>
      </header>

      <p className="research-warning">
        Experimental travel-time research on a single Edmonton route. The held-out twin gate is
        <strong> blocked</strong> for lack of complete terminal-to-terminal runs, the deep model was
        <strong> rejected</strong> in favour of a simple baseline, and no optimizer or service
        recommendation exists. Do not read these numbers as operational, passenger, or fleet results.
      </p>

      <p className="research-lead">{message}</p>

      {modelLab?.missing_artifacts?.length ? (
        <section className="research-block">
          <h2>Missing artifacts</h2>
          <ul>
            {modelLab.missing_artifacts.map((artifact) => (
              <li key={artifact}>{artifact}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {modelLab?.integrity_failure ? (
        <section className="research-block">
          <h2>Integrity failure</h2>
          <p>{modelLab.integrity_failure}</p>
        </section>
      ) : null}

      {modelLab?.availability === "experimental" && (
        <div className="research-grid">
          <article className="research-card">
            <span className="research-card-kind">MODEL SELECTION</span>
            <h3>{modelLab.selected_model?.name}</h3>
            <dl className="research-metrics">
              <div>
                <dt>Selected baseline MAE</dt>
                <dd>{formatPreciseSeconds(modelLab.selected_model?.test_metrics.mae)}</dd>
              </div>
              <div>
                <dt>Deep model MAE</dt>
                <dd>{formatPreciseSeconds(modelLab.selected_model?.deep_model_test_metrics.mae)}</dd>
              </div>
              <div>
                <dt>Rows / service days</dt>
                <dd>
                  {modelLab.provenance?.dataset_rows.toLocaleString()} /{" "}
                  {modelLab.provenance?.service_days.length}
                </dd>
              </div>
            </dl>
            <p>{modelLab.selected_model?.reason}</p>
            <small>{modelLab.selected_model?.deep_model_status}</small>
          </article>

          <article className="research-card">
            <span className="research-card-kind">CONTIGUOUS-SPAN DIAGNOSTIC</span>
            <h3>Twin gate is blocked</h3>
            <dl className="research-metrics">
              <div>
                <dt>Segment MAE</dt>
                <dd>{formatPreciseSeconds(twin?.segment_travel_time.mae)}</dd>
              </div>
              <div>
                <dt>Span MAE</dt>
                <dd>{formatPreciseSeconds(twin?.contiguous_span_travel_time.mae)}</dd>
              </div>
              <div>
                <dt>Anchored headway MAE</dt>
                <dd>{formatPreciseSeconds(twin?.partial_headway_diagnostic.headway_seconds?.mae)}</dd>
              </div>
              <div>
                <dt>Headway pairs</dt>
                <dd>
                  {twin?.partial_headway_diagnostic.eligible_adjacent_pairs.toLocaleString() ?? "—"}
                </dd>
              </div>
              <div>
                <dt>Strict full runs</dt>
                <dd>
                  {twin
                    ? `${twin.terminal_coverage_audit.complete_runs} / ${twin.terminal_coverage_audit.label_runs}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>80% interval coverage</dt>
                <dd>{twin ? `${Math.round(twin.interval.held_out_coverage * 100)}%` : "—"}</dd>
              </div>
            </dl>
            <p>{twin?.partial_headway_diagnostic.interpretation ?? twin?.status}</p>
            <small>{twin?.terminal_coverage_audit.reason}</small>
          </article>

          <article className="research-card">
            <span className="research-card-kind">HISTORICAL SPAN REPLAY</span>
            <h3>One contiguous observed span</h3>
            <dl className="research-metrics">
              <div>
                <dt>Run</dt>
                <dd>{replay?.selected_run_id ?? "—"}</dd>
              </div>
              <div>
                <dt>Stop sequences</dt>
                <dd>{replay ? `${replay.from_stop_sequence}-${replay.to_stop_sequence}` : "—"}</dd>
              </div>
              <div>
                <dt>Span-end error</dt>
                <dd>
                  {formatSignedSeconds(replay?.validation.span_end_arrival_error_seconds ?? null)}
                </dd>
              </div>
              <div>
                <dt>Span MAE</dt>
                <dd>{formatSeconds(twin?.contiguous_span_travel_time.mae ?? null)}</dd>
              </div>
            </dl>
            <p>{replay?.validation.evaluation_note}</p>
          </article>

          <article className="research-card">
            <span className="research-card-kind">ACTIVE CONSTRAINTS</span>
            <h3>What is withheld</h3>
            <ul className="research-constraints">
              {modelLab.constraints?.map((constraint) => (
                <li key={constraint}>{constraint}</li>
              ))}
            </ul>
            <small title={modelLab.provenance?.dataset_sha256}>
              M3 SHA-256: {modelLab.provenance?.dataset_sha256.slice(0, 16)}...
            </small>
          </article>
        </div>
      )}
    </main>
  );
}
