import { useState } from "react";
import { Activity, ChevronRight, RefreshCw } from "lucide-react";
import { date, type RecordData } from "./api";
import { Button, DebugId } from "./components";
import {
  CatalogueSearch,
  CatalogueIdentity,
  CatalogueEmpty,
} from "./catalogue";
import { SelectField, SelectOption } from "./form-select";
import { Status, statusLabel } from "./status";
import { Typography } from "./ui/typography/typography";

export function ExecutionCatalogue({
  jobs,
  refresh,
  open,
}: {
  jobs: RecordData[];
  refresh: () => void;
  open: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const states = [...new Set(jobs.map((job) => statusLabel(job.status)))];
  const filtered = jobs.filter(
    (job) =>
      (status === "all" || statusLabel(job.status) === status) &&
      `${job.prompt || ""} ${job.id}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <div className="page">
      <div className="page-title">
        <div>
          <Typography variant="H1">Executions</Typography>
          <p>Separate contexts. One place to see what happened.</p>
        </div>
      </div>
      <div className="catalogue-toolbar">
        <CatalogueSearch
          label="Search executions"
          value={query}
          onChange={setQuery}
        />
        <SelectField
          aria-label="Filter executions by status"
          value={status}
          onValueChange={setStatus}
        >
          <SelectOption value="all">All statuses</SelectOption>
          {states.map((state) => (
            <SelectOption key={state} value={state}>
              {state}
            </SelectOption>
          ))}
        </SelectField>
        <span className="catalogue-count">
          {filtered.length}/{jobs.length}
        </span>
        <Button onClick={refresh}>
          <RefreshCw size={14} />
          Refresh
        </Button>
      </div>
      <div className="catalogue-list execution-catalogue">
        {filtered.map((job) => (
          <Button
            key={job.id}
            variant="GHOST"
            className="execution-catalogue-row"
            onClick={() => open(job.session_id)}
          >
            <CatalogueIdentity
              icon={Activity}
              title={job.prompt || "Execution"}
            >
              <DebugId id={job.id} />
              <span>{date(job.created_at)}</span>
            </CatalogueIdentity>
            <Status status={job.status} />
            <ChevronRight size={15} aria-hidden="true" />
          </Button>
        ))}
        {!filtered.length && (
          <CatalogueEmpty
            icon={Activity}
            title={jobs.length ? "No matching executions" : "No executions yet"}
          >
            {jobs.length
              ? "Try another search or status."
              : "Run a routine or test a Python action to start an isolated execution."}
          </CatalogueEmpty>
        )}
      </div>
    </div>
  );
}
